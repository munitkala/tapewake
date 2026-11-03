"""Disk space of a job's working files, and deleting the ones nothing needs any more.

The source cut and the deinterlaced pieces are lossless FFV1, so they're big; restored pieces are
x264 CRF 14 at 1280x960 59.94. Measured on a two-hour NTSC tape, per hour of tape: source cut ~11 GB,
deinterlaced ~36 GB, restored pieces ~17.5 GB, and the finished video (the pieces joined without
re-encoding) another ~17.5 GB.

When files are deleted:
- A deinterlaced piece qt_K, as soon as pieces K-1, K and K+1 are restored (run.py borrows frames from
  both neighbours). prepare.py also stays at most `qtgmc_ahead` pieces ahead of the graphics card, so the
  deinterlaced folder only ever holds a few GB.
- Whatever is left of qtgmc/ and work/, once the finished video passes its checks (run.py).
- src/ and pieces/, only when the user asks (free_up_space). The app's before/after player needs both,
  so it stops working for that job. The restored video is never touched.

`keep_work_files` (a job setting) turns the first two off.

Stdlib only.
"""
import os
import shutil


def _size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _remove(path):
    try:
        n = os.path.getsize(path)
        os.remove(path)
        return n
    except OSError:
        return 0


def _rmtree(path):
    before = _size(path)
    shutil.rmtree(path, ignore_errors=True)
    return before - _size(path)


def gb(n):
    return f'{n / 1e9:.1f} GB'


def usage(job):
    """Bytes used by each part of the job."""
    return {'source_cut': _size(job.src), 'deinterlaced': _size(job.qtgmc), 'in_progress': _size(job.work),
            'pieces': _size(job.pieces), 'output': _size(job.output) if os.path.exists(job.output) else 0}


def finished(job, k, n_pieces):
    """True if piece k and both its neighbours (where they exist) are restored."""
    return all(os.path.exists(job.piece(j)) for j in (k - 1, k, k + 1) if 0 <= j < n_pieces)


def drop_spent_deinterlaced(job, n_pieces, around):
    """Delete each qt_K (K in `around`) that no piece still needs. Returns the bytes freed."""
    if job['keep_work_files']:
        return 0
    return sum(_remove(job.qt_piece(k)) for k in around
               if 0 <= k < n_pieces and os.path.exists(job.qt_piece(k)) and finished(job, k, n_pieces))


def clear_after_finish(job):
    """Once the finished video passed its checks: delete what is left of qtgmc/ and work/."""
    if job['keep_work_files']:
        return 0
    return _rmtree(job.qtgmc) + _rmtree(job.work)


def can_free_up(job):
    """Why free_up_space would refuse, or '' if it's allowed."""
    if job.data.get('controller'):
        return 'This restoration runs from its own scripts, so tapewake does not delete its files.'
    if not os.path.exists(job.output):
        return 'The restoration has not finished yet, so its working files are still needed.'
    return ''


def free_up_space(job):
    """Delete every working file of a finished job; only the restored video, the audio and the logs stay.

    The caller must make sure the job is not running. Returns the bytes freed.
    """
    why = can_free_up(job)
    if why:
        raise RuntimeError(why)
    freed = 0
    for d in (job.src, job.pieces):  # the .mkv files only; src/done keeps the piece count for progress
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if name.endswith('.mkv'):
                freed += _remove(os.path.join(d, name))
    freed += _rmtree(job.qtgmc) + _rmtree(job.work)
    with open(job.cleaned_flag, 'w', encoding='utf-8') as f:
        f.write(f'{gb(freed)} of working files deleted; the restored video is {job.output}\n')
    return freed
