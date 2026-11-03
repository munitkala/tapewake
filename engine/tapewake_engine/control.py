"""Pause, resume and progress for a job.

    python control.py pause|resume|status <job.json>

pause kills the runner and its children (frees the GPU) and leaves a PAUSED file. resume deletes
PAUSED and starts run.py again, detached. Finished pieces are never redone.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import PYTHON, Job  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STAGES = ('finding faces', 'restoring faces', 'upscaling', 'assembling')
# rough share of a piece's time per step, measured on the test tape
# (faces ~690 s, restore ~1020 s, upscale ~320 s w/ TensorRT; assemble overlaps the next piece)
STEP_SHARE = (0.32, 0.48, 0.15, 0.05)
# without face restoration (setting faces: false) only the last two run; upscale ~320 s, assemble ~110 s
STAGES_NO_FACES, STEP_SHARE_NO_FACES = STAGES[2:], (0.75, 0.25)
# what runs before piece 1 can start (run.py waits for the sound and the first two deinterlaced pieces),
# with its weight inside the preparation, and the preparation's share of the whole bar in percent
PREP = (('preparing the sound', 0.5), ('cutting the video', 0.2), ('deinterlacing the first pieces', 0.3))
PREP_PCT = 2.0


class Control:
    def __init__(self, job):
        self.job = job
        # the steps this job really runs, for the progress screen
        self.STAGES, self.STEP_SHARE = (STAGES, STEP_SHARE) if job['faces'] else (STAGES_NO_FACES, STEP_SHARE_NO_FACES)

    def processes(self):
        """PIDs of run.py and its children (python/ffmpeg processes mentioning this job). Skips app_bridge."""
        import psutil
        me, out = os.getpid(), []
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            cmd = ' '.join(p.info['cmdline'] or [])
            if p.info['pid'] != me and (self.job.path in cmd or self.job.folder in cmd) and 'app_bridge' not in cmd \
                    and 'control.py' not in cmd and \
                    (p.info['name'] or '').lower() in ('python.exe', 'pythonw.exe', 'ffmpeg.exe'):
                out.append(p)
        return out

    def runner_running(self):
        return any('run.py' in ' '.join(p.info['cmdline'] or []) for p in self.processes())

    def last_lines(self, n=8):
        try:
            with open(self.job.log, encoding='utf-8') as f:
                return f.read().splitlines()[-n:]
        except OSError:
            return []

    def log(self, msg):
        os.makedirs(self.job.folder, exist_ok=True)
        with open(self.job.log, 'a', encoding='utf-8') as f:
            f.write(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {msg}\n')

    def pause(self):
        import psutil
        os.makedirs(self.job.folder, exist_ok=True)
        open(self.job.pause_flag, 'w').close()
        procs = self.processes()
        for p in procs:
            try:
                p.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(procs, timeout=10)
        self.log(f'PAUSED by user ({len(procs)} processes stopped)')

    def resume(self):
        if os.path.exists(self.job.pause_flag):
            os.remove(self.job.pause_flag)
        if self.runner_running():
            return
        pythonw = os.path.join(os.path.dirname(PYTHON), 'pythonw.exe')
        cmd = [pythonw if os.path.exists(pythonw) else PYTHON, os.path.join(HERE, 'run.py'), self.job.path]
        flags = 0x00000008 | 0x00000200 | 0x01000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB
        try:
            subprocess.Popen(cmd, creationflags=flags, close_fds=True)
        except OSError:
            subprocess.Popen(cmd, creationflags=flags & ~0x01000000, close_fds=True)

    def state(self):
        if os.path.exists(self.job.pause_flag):
            return 'paused'
        if self.runner_running():
            return 'running'
        if os.path.exists(self.job.output):
            return 'finished'
        return 'stopped' if self.last_lines(1) else 'not started'

    def _run_start(self):
        started = 0.0  # anything older than the last start is left over from before a pause
        try:
            with open(self.job.log, encoding='utf-8') as f:
                for ln in f:
                    if '=== run started' in ln:
                        started = time.mktime(time.strptime(ln[1:20], '%Y-%m-%d %H:%M:%S'))
        except (OSError, ValueError):
            pass
        return started

    def live(self):
        """(piece, step, stage, done, total) for each piece in progress, read from progress.txt."""
        out, work, started = [], self.job.work, self._run_start()
        for d in sorted(os.listdir(work)) if os.path.isdir(work) else []:
            wd = os.path.join(work, d)
            if not (d.startswith('p') and d[1:].isdigit() and os.path.isdir(wd)):
                continue
            k = int(d[1:])
            if max((e.stat().st_mtime for e in os.scandir(wd) if e.is_file()), default=0) < started:
                continue
            if os.path.exists(self.job.piece(k)):
                continue  # done already, stale progress.txt
            p = os.path.join(wd, 'progress.txt')
            try:
                if os.path.getmtime(p) < started:
                    raise OSError('left over from before the last start')
                with open(p) as f:
                    done, total, stage = f.read().split(' ', 2)
                out.append((k + 1, self.STAGES.index(stage) + 1, stage, int(done), int(total)))
            except (OSError, ValueError):
                out.append((k + 1, 1, self.STAGES[0], 0, 0))  # just started
        return out

    @staticmethod
    def _tail_from(path, marker):
        """The lines of a log file after the last line containing marker (the latest run)."""
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                lines = f.read().splitlines()
        except OSError:
            return []
        starts = [i for i, ln in enumerate(lines) if marker in ln]
        return lines[starts[-1]:] if starts else []

    def _sound_done(self):
        job = self.job
        try:
            if open(os.path.join(os.path.dirname(job.audio), 'source_sound.txt')).read().strip() != 'ok':
                return 1.0  # no sound, or only hiss: kept as it is
        except OSError:
            pass
        if os.path.exists(job.audio):
            return 1.0
        frac = 0.0
        for ln in self._tail_from(os.path.join(os.path.dirname(job.audio), 'audio.log'), '=== audio restoration'):
            if 'block ' in ln and ' done' in ln:
                try:
                    b, n = ln.split('block ')[1].split(' ')[0].split('/')
                    frac = 0.95 * int(b) / int(n)  # then the sync and loudness checks
                except (IndexError, ValueError):
                    pass
        return frac

    def _cut_done(self):
        if self.job.pieces_total():
            return 1.0
        for ln in reversed(self._tail_from(os.path.join(self.job.folder, 'prepare.log'), 'cutting the source into')):
            if 'cutting the source into' in ln:
                try:
                    n = int(ln.split('cutting the source into ')[1].split(' ')[0])
                    made = sum(1 for f in os.listdir(self.job.src) if f.endswith('.mkv'))
                    return min(max(made - 1, 0) / n, 0.99)  # the newest file is still being written
                except (OSError, IndexError, ValueError):
                    return 0.0
        return 0.0

    def prep(self):
        """[(step, fraction done)] for the work before piece 1 starts, or None once the pieces have started."""
        total = self.job.pieces_total()
        need = min(2, total)
        if os.path.exists(self.job.output):
            return None
        if total and (self.live() or any(os.path.exists(self.job.piece(k)) for k in range(total))):
            return None
        deint = (sum(1 for k in range(need) if os.path.exists(self.job.qt_piece(k))) / need) if need else 0.0
        return [(PREP[0][0], self._sound_done()), (PREP[1][0], self._cut_done()), (PREP[2][0], deint)]

    def prep_problem(self):
        """Why the preparation stopped, or ''. Cutting and deinterlacing run in prepare.py beside the runner, which
        only notices it died once the sound is done, so the progress screen would look stuck until then."""
        prep = self.prep()
        if not prep or prep[2][1] >= 1 or not self.runner_running():
            return ''
        if any('prepare.py' in ' '.join(p.info['cmdline'] or []) for p in self.processes()):
            return ''
        try:
            with open(os.path.join(self.job.folder, 'prepare.out.log'), encoding='utf-8', errors='replace') as f:
                last = next((ln.strip() for ln in reversed(f.read().splitlines()) if ln.strip()), '')
        except OSError:
            last = ''
        step = 'Cutting the video' if prep[1][1] < 1 else 'Deinterlacing'
        return f'{step} stopped with an error, so this restoration can not continue. {last}'.strip()

    def percent(self):
        """Returns (percent, done, total, hours_left, start_time). The last two can be None."""
        total = self.job.pieces_total()
        if os.path.exists(self.job.output):  # only there once every check passed (its pieces may be deleted)
            return 100.0, total, total, 0.0, None
        prep = self.prep()
        if prep is not None:  # the first PREP_PCT % of the bar
            return PREP_PCT * sum(w * f for (_, w), (_, f) in zip(PREP, prep)), 0, total, None, None
        checked = set()
        try:
            with open(self.job.log, encoding='utf-8') as f:
                for ln in f:
                    if 'CHECK OK (' in ln:
                        checked.add(int(ln.split('piece ')[1].split('/')[0]))
        except (OSError, IndexError, ValueError):
            pass
        finished = {k for k in checked if os.path.exists(self.job.piece(k - 1))}
        done = len(finished)
        # count partial pieces too, otherwise the % only moves once per piece
        partial = 0.0
        for k, n, _, d, t in self.live():
            if k not in finished:
                share = self.STEP_SHARE
                partial += sum(share[:n - 1]) + share[n - 1] * (min(d / t, 1.0) if t else 0.0)
        # ETA from the average piece time (ignoring gaps > 6 h, i.e. pauses)
        hours, start, stamps = None, None, []
        try:
            with open(self.job.log, encoding='utf-8') as f:
                for ln in f:
                    if 'GPU stages done' in ln:
                        stamps.append(time.mktime(time.strptime(ln[1:20], '%Y-%m-%d %H:%M:%S')))
        except (OSError, ValueError):
            pass
        if len(stamps) >= 2:
            gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b - a < 6 * 3600]
            if gaps:
                avg = sum(gaps) / len(gaps)
                hours = avg * (total - done) / 3600
                start = stamps[0] - avg  # approx
        pieces_pct = 100 * (done + partial) / total if total else 0.0
        return PREP_PCT + (100 - PREP_PCT) * pieces_pct / 100, done, total, hours, start


if __name__ == '__main__':
    c = Control(Job(sys.argv[2]))
    action = sys.argv[1]
    if action == 'pause':
        c.pause()
    elif action == 'resume':
        c.resume()
    pct, done, total, hours, _ = c.percent()
    print(json.dumps({'state': c.state(), 'pct': pct, 'done': done, 'total': total, 'hours_left': hours}))
