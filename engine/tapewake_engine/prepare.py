"""CPU side of a job: cut the source into lossless pieces, then QTGMC each one.

Needs the VapourSynth Python:  python prepare.py <job.json>

The cut forces a constant frame rate starting at 0 - old captures often have gaps in their
timestamps. Each piece borrows piece_margin frames from its neighbours when deinterlacing so the
joins don't show. Can be restarted, finished pieces are skipped.

Deinterlaced pieces are big (~0.35 GB each), so this stays at most `qtgmc_ahead` pieces ahead of the
last restored piece; run.py deletes each one once nothing needs it (see space.py).
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deinterlace as q  # noqa: E402
import space  # noqa: E402
from settings import FFMPEG, FFPROBE, Job  # noqa: E402

core = q.core


def log(job, msg):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    print(line, flush=True)
    with open(os.path.join(job.folder, 'prepare.log'), 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def source_frames(job):
    """Frame count after the constant-rate cut (duration * fps)."""
    dur = subprocess.run([FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'format=duration',
                          '-of', 'csv=p=0', job.source], capture_output=True, text=True).stdout
    num, den = (int(x) for x in job['source_fps'].split('/'))
    return int(float(dur) * num / den)


def cut(job):
    if job.pieces_total():
        return job.pieces_total()
    size = job['piece_frames']
    pieces = (source_frames(job) + size - 1) // size
    log(job, f'cutting the source into {pieces} lossless pieces ...')
    points = ','.join(str(k * size) for k in range(1, pieces))
    order = job['field_order']
    # short video = single piece; the segment muxer wants at least one cut point so skip it
    split = ['-f', 'segment', '-segment_frames', points, '-reset_timestamps', '1',
             os.path.join(job.src, 'src_%03d.mkv')] if points else [job.src_piece(0)]
    subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', '-i', job.source, '-map', '0:v:0',
                    '-vf', f"fps={job['source_fps']}:start_time=0,setfield={order}", '-c:v', 'ffv1', '-level', '3',
                    '-field_order', 'tt' if order == 'tff' else 'bb', *split], check=True)
    made = sorted(f for f in os.listdir(job.src) if f.endswith('.mkv'))
    open(os.path.join(job.src, 'done'), 'w').write(str(len(made)))
    log(job, f'cut done: {len(made)} pieces')
    return len(made)


def main():
    job = Job(sys.argv[1])
    tff, margin = job['field_order'] == 'tff', job['piece_margin']
    os.makedirs(job.src, exist_ok=True)
    os.makedirs(job.qtgmc, exist_ok=True)
    n = cut(job)

    def piece(k):
        return core.std.SetFieldBased(core.bs.VideoSource(source=job.src_piece(k)), 2 if tff else 1)
    def front():
        """Index of the last restored piece (-1 if none yet)."""
        return max((j for j in range(n) if os.path.exists(job.piece(j))), default=-1)

    # restoring piece K needs K+1 deinterlaced too, so anything under 2 would wait forever
    ahead = max(2, int(job['qtgmc_ahead']))
    t0, done_now = time.time(), 0
    for k in range(n):
        out = job.qt_piece(k)
        if os.path.exists(out) or space.finished(job, k, n):  # nothing needs it any more
            continue
        told = False
        while k > front() + ahead:  # keep the deinterlaced folder small
            if not told:
                log(job, f'piece {k + 1}/{n}: waiting, {ahead} deinterlaced pieces are ready ahead')
                told = True
            time.sleep(20)
        if told:
            t0, done_now = time.time(), 0  # the wait isn't deinterlacing time
        cur = piece(k)
        before = piece(k - 1)[-margin:] if k > 0 else None
        after = piece(k + 1)[:margin] if k + 1 < n else None
        clip = cur
        if before is not None:
            clip = before + clip
        if after is not None:
            clip = clip + after
        qt = q.QTempGaussMC(denoise_mode=q.QTempGaussMC.NoiseProcessMode.DENOISE, denoise_tr=2,
                            denoise_func=q.DFTTest(sigma=job['qtgmc_sigma']),
                            source_match_mode=q.QTempGaussMC.SourceMatchMode.BASIC).bob(clip, tff=tff)
        lead = 2 * (margin if before is not None else 0)
        qt = qt[lead:lead + 2 * cur.num_frames]
        qt = core.std.SetFieldBased(core.resize.Spline36(qt, 640, 480, format=q.vs.YUV444P8), 0)
        part = out.replace('.mkv', '.part.mkv')
        enc = subprocess.Popen([FFMPEG, '-v', 'error', '-nostdin', '-y', '-f', 'yuv4mpegpipe', '-i', '-',
                                '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'yuv444p', part], stdin=subprocess.PIPE)
        qt.output(enc.stdin, y4m=True)
        enc.stdin.close()
        if enc.wait() == 0:
            os.replace(part, out)
        done_now += 1
        el = time.time() - t0
        log(job, f'piece {k + 1}/{n} deinterlaced ({2 * cur.num_frames} frames); about '
                 f'{el / done_now * (n - k - 1) / 3600:.1f} h left')
    log(job, '=== deinterlacing done for the whole video')


if __name__ == '__main__':
    main()
