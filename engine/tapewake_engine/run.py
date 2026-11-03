"""Runs a whole job:  python run.py <job.json>

prepare.py (cutting + QTGMC) runs on the CPU in the background. On the GPU we do the audio first,
then each piece in turn:

    deinterlaced piece (+ a few frames from each neighbour, so tracking doesn't break at the joins)
    -> find faces -> restore faces -> upscale -> assemble -> check

Assembling is mostly CPU so it overlaps with the next piece. At the end the pieces are joined and
the audio tracks muxed in. Safe to stop at any point - finished pieces are skipped next time.

Each deinterlaced piece is deleted once it and its neighbours are restored, and what's left of the
deinterlaced and work folders goes once the finished video passes its checks (space.py).
"""
import ctypes
import json
import os
import pickle
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import space  # noqa: E402
from settings import FFMPEG, FFPROBE, PYTHON, VS_PYTHON, Job  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = 0x08000000


class Runner:
    def __init__(self, job):
        self.job = job

    def log(self, msg):
        line = f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {msg}'
        print(line, flush=True)
        with open(self.job.log, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    def wait_for(self, cond, what):
        told = False
        while not cond():
            if os.path.exists(self.job.pause_flag):
                self.log('paused - stopping (Resume continues)')
                sys.exit(0)
            if not told:
                self.log(f'waiting for {what} ...')
                told = True
            time.sleep(30)

    def pieces_total(self):
        return self.job.pieces_total()

    def build_work(self, k, n_pieces, wd):
        """Piece k with some frames of its neighbours on each side. Returns (lead-in frames, total frames)."""
        job, edge = self.job, self.job['edge_frames']
        n = frames_of(job.qt_piece(k))
        inputs, parts, lead = [], [], 0
        if k > 0:
            inputs += ['-i', job.qt_piece(k - 1)]
            prev_n = frames_of(job.qt_piece(k - 1))
            parts.append(f'[{len(parts)}:v]trim=start_frame={prev_n - edge},setpts=PTS-STARTPTS[v{len(parts)}]')
            lead = edge
        inputs += ['-i', job.qt_piece(k)]
        parts.append(f'[{len(parts)}:v]setpts=PTS-STARTPTS[v{len(parts)}]')
        if k + 1 < n_pieces:
            inputs += ['-i', job.qt_piece(k + 1)]
            parts.append(f'[{len(parts)}:v]trim=end_frame={edge},setpts=PTS-STARTPTS[v{len(parts)}]')
        fc = ';'.join(parts) + ';' + ''.join(f'[v{i}]' for i in range(len(parts))) + f'concat=n={len(parts)}:v=1[out]'
        subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', *inputs, '-filter_complex', fc, '-map', '[out]',
                        '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'yuv444p', os.path.join(wd, '02_qtgmc.mkv')],
                       check=True, creationflags=NO_WINDOW)
        return lead, n

    def assemble(self, k, n_pieces, wd, lead, n, problems):
        job = self.job
        out = job.piece(k)
        part = out.replace('.mkv', '.part.mkv')
        model = job['face_model'] if job['faces'] else 'none'
        run_step('assemble', [PYTHON, os.path.join(HERE, 'assemble.py'), wd, model, part, '--audio', 'none',
                              '--first', str(lead), '--last', str(lead + n), '--sharpen', str(job['sharpen']),
                              '--grain', str(job['grain']),
                              # small faces get less (or no) restoration, see assemble.py
                              '--min-ed', str(job['face_min_ed']), '--full-ed', str(job['face_full_ed']),
                              '--tone', str(job['face_tone'])], wd)
        got = frames_of(part)
        if got == n:
            os.replace(part, out)
            shutil.rmtree(wd, ignore_errors=True)
            self.log(f'piece {k + 1}/{n_pieces} CHECK OK ({n} frames)')
            space.drop_spent_deinterlaced(job, n_pieces, (k - 1, k, k + 1))
        else:
            problems.append(k)
            self.log(f'piece {k + 1}/{n_pieces} CHECK FAILED: {got}/{n} frames (kept {wd} for a retry)')

    def finalize(self, n_pieces, sound):
        job = self.job
        lst = os.path.join(job.folder, 'pieces.txt')
        with open(lst, 'w', encoding='utf-8') as f:
            f.writelines(f"file '{job.piece(k)}'\n" for k in range(n_pieces))
        os.makedirs(os.path.dirname(job.output), exist_ok=True)
        part = job.output.replace('.mkv', '.part.mkv')
        self.log('joining pieces and adding audio tracks ...')
        if sound == 'ok':  # track 1 restored, track 2 original
            tracks, want_audio = ['-i', job.audio, '-i', job.source, '-map', '0:v', '-map', '1:a', '-map', '2:a:0',
                                  '-c:a:0', 'flac', '-c:a:1', 'copy', '-metadata:s:a:0', 'title=Restored audio',
                                  '-metadata:s:a:1', 'title=Original audio', '-disposition:a:0', 'default',
                                  '-disposition:a:1', '0'], 2
        elif sound == 'silent':  # nothing worth restoring, keep the original
            tracks, want_audio = ['-i', job.source, '-map', '0:v', '-map', '1:a:0', '-c:a', 'copy',
                                  '-metadata:s:a:0', 'title=Original audio'], 1
        else:
            tracks, want_audio = ['-map', '0:v'], 0
        subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', '-f', 'concat', '-safe', '0', '-i', lst, *tracks,
                        '-c:v', 'copy', '-metadata:s:v:0', 'title=Restored video', part],
                       check=True, creationflags=NO_WINDOW)
        info = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-show_entries',
                                          'stream=codec_type:format=duration', '-of', 'json', part],
                                         capture_output=True, text=True).stdout)
        frames = frames_of(part)
        want = sum(frames_of(job.piece(k)) for k in range(n_pieces))
        audio = sum(1 for s in info['streams'] if s['codec_type'] == 'audio')
        dur = float(info['format']['duration'])
        if frames == want and audio == want_audio and abs(dur - frames / (60000 / 1001)) < 1.0:
            os.replace(part, job.output)
            self.log(f'=== FINISHED: {job.output} ({frames} frames, {dur / 60:.1f} min, {audio} audio tracks) - all checks OK')
            freed = space.clear_after_finish(job)
            if freed:
                self.log(f'deleted {space.gb(freed)} of deinterlaced and in-progress files')
            left = space.usage(job)
            self.log(f"kept {space.gb(left['source_cut'] + left['pieces'])} of working files for the before/after "
                     'player; Free up space in the app deletes them')
        else:
            self.log(f'FINAL CHECK FAILED: {frames}/{want} frames, {audio} audio tracks, {dur:.1f}s (kept {part})')

    def main(self):
        job = self.job
        for d in (job.work, job.pieces):
            os.makedirs(d, exist_ok=True)
        if os.path.exists(job.pause_flag):
            self.log('not starting: paused (Resume clears it)')
            return
        if os.path.exists(job.output):  # only there once every check passed; its pieces may be deleted
            self.log(f'not starting: already finished ({job.output}). Delete or move it to restore again.')
            return
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)  # don't let Windows sleep mid-run
        self.log('=== run started (finished pieces are skipped)')
        # cutting/deinterlacing runs in the background the whole time
        if not running('prepare.py', job.path):
            subprocess.Popen([VS_PYTHON, os.path.join(HERE, 'prepare.py'), job.path], creationflags=NO_WINDOW,
                             stdout=open(os.path.join(job.folder, 'prepare.out.log'), 'a'), stderr=subprocess.STDOUT)
            self.log('cutting and deinterlacing (re)started in the background')
        # audio first, while the first pieces are still being prepared
        sound = source_sound(job)
        if sound != 'ok':
            self.log(f"the source has {'no sound' if sound == 'none' else 'only silence or hiss'}: "
                     "its sound is kept as it is, not restored")
        elif not os.path.exists(job.audio):
            if not running('audio.py', job.path):
                self.log('audio restoration ...')
                rc = run_step('audio', [PYTHON, os.path.join(HERE, 'audio.py'), job.path], job.folder)
                self.log(f'audio restoration finished (exit {rc})')
                if rc != 0 and not os.path.exists(job.audio):
                    self.log(f"AUDIO FAILED - see {os.path.join(os.path.dirname(job.audio), 'audio.log')}; stopping (Resume tries again)")
                    return
            self.wait_for(lambda: os.path.exists(job.audio) and not running('audio.py', job.path), 'the audio')
        prep_failed = lambda: not running('prepare.py', job.path)  # noqa: E731
        self.wait_for(lambda: self.pieces_total() > 0 or prep_failed(), 'the source to be cut into pieces')
        if self.pieces_total() == 0:
            self.log(f"CUTTING FAILED - see {os.path.join(job.folder, 'prepare.out.log')}; stopping (Resume tries again)")
            return
        n_pieces = self.pieces_total()
        space.drop_spent_deinterlaced(job, n_pieces, range(n_pieces))  # e.g. left by an older version
        problems, pending = [], None
        t0, done_now = time.time(), 0
        for k in range(n_pieces):
            if os.path.exists(job.piece(k)):
                continue
            need = [job.qt_piece(j) for j in (k - 1, k, k + 1) if 0 <= j < n_pieces]  # build_work reads all three
            self.wait_for(lambda: all(os.path.exists(p) for p in need) or prep_failed(), f'deinterlaced piece {k + 1}')
            if not all(os.path.exists(p) for p in need):
                self.log(f"DEINTERLACING FAILED at piece {k + 1} - see {os.path.join(job.folder, 'prepare.out.log')}; "
                         "stopping (Resume tries again)")
                return
            wd = os.path.join(job.work, f'p{k:03d}')
            os.makedirs(wd, exist_ok=True)
            lead, n = self.build_work(k, n_pieces, wd)
            clip, faces = os.path.join(wd, '02_qtgmc.mkv'), os.path.join(wd, 'faces')
            if job['faces']:
                run_step('faces_find', [PYTHON, os.path.join(HERE, 'faces_find.py'), clip, faces,
                                        '--detect-step', str(job['face_detect_step']), '--min-ed', str(job['face_min_ed']),
                                        '--min-vis', str(job['face_min_vis']), '--clear-ed', str(job['face_clear_ed'])], wd)
            if job['faces'] and os.path.exists(os.path.join(faces, 'tracks.pkl')):
                run_step('faces_restore', [PYTHON, os.path.join(HERE, 'faces_restore.py'), faces, job['face_model'],
                                           '--half-rate', '--batch', str(job['face_batch'])], wd)
            else:  # no faces (or faces turned off)
                os.makedirs(os.path.join(faces, 'crops'), exist_ok=True)
                with open(os.path.join(faces, 'tracks.pkl'), 'wb') as fh:
                    pickle.dump({'n_frames': 0, 'tracks': []}, fh)
            run_step('upscale', [PYTHON, os.path.join(HERE, 'upscale.py'), clip, os.path.join(wd, '03_bg.mkv'),
                                 '--dn', str(job['upscale_dn'])], wd)
            if pending:
                pending.join()
            pending = threading.Thread(target=self.assemble, args=(k, n_pieces, wd, lead, n, problems))
            pending.start()
            done_now += 1
            left = sum(1 for j in range(k + 1, n_pieces) if not os.path.exists(job.piece(j)))
            self.log(f'piece {k + 1}/{n_pieces} GPU stages done; about {(time.time() - t0) / done_now * left / 3600:.1f} h left')
        if pending:
            pending.join()
        if problems:
            self.log(f'{len(problems)} piece(s) failed checks: {problems} - not joining until they are redone')
            return
        self.finalize(n_pieces, sound)


def run_step(name, cmd, logdir):
    with open(os.path.join(logdir, f'{name}.log'), 'w', encoding='utf-8') as fh:
        return subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)


def source_sound(job, silent_db=-40.0):
    """'ok', 'silent' or 'none' (no audio stream). Silent = never louder than silent_db. Cached."""
    cache = os.path.join(job.folder, 'audio', 'source_sound.txt')
    try:
        return open(cache).read().strip()
    except OSError:
        pass
    has = subprocess.run([FFPROBE, '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index',
                          '-of', 'csv=p=0', job.source], capture_output=True, text=True).stdout.strip()
    result = 'none'
    if has:
        err = subprocess.run([FFMPEG, '-v', 'info', '-nostdin', '-i', job.source, '-map', '0:a:0', '-af', 'volumedetect',
                              '-f', 'null', '-'], capture_output=True, text=True, creationflags=NO_WINDOW).stderr
        peak = [float(ln.split('max_volume:')[1].split()[0]) for ln in err.splitlines() if 'max_volume:' in ln]
        result = 'ok' if peak and peak[-1] > silent_db else 'silent'
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    open(cache, 'w').write(result)
    return result


def frames_of(path):
    if not os.path.exists(path):
        return 0
    out = subprocess.run([FFPROBE, '-v', 'error', '-count_packets', '-select_streams', 'v:0', '-show_entries',
                          'stream=nb_read_packets', '-of', 'csv=p=0', path], capture_output=True, text=True).stdout
    return int(out.strip() or 0)


def running(script, job_path):
    """True if `script` is already running for this job."""
    import psutil
    for p in psutil.process_iter(['cmdline']):
        cmd = ' '.join(p.info['cmdline'] or [])
        if script in cmd and job_path in cmd:
            return True
    return False


if __name__ == '__main__':
    Runner(Job(sys.argv[1])).main()
