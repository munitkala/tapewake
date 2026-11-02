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


class Control:
    STAGES, STEP_SHARE = STAGES, STEP_SHARE

    def __init__(self, job):
        self.job = job

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
                out.append((k + 1, STAGES.index(stage) + 1, stage, int(done), int(total)))
            except (OSError, ValueError):
                out.append((k + 1, 1, STAGES[0], 0, 0))  # just started
        return out

    def percent(self):
        """Returns (percent, done, total, hours_left, start_time). The last two can be None."""
        total = len([f for f in os.listdir(self.job.src) if f.endswith('.mkv')]) if os.path.isdir(self.job.src) else 0
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
                partial += sum(STEP_SHARE[:n - 1]) + STEP_SHARE[n - 1] * (min(d / t, 1.0) if t else 0.0)
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
        return (100 * (done + partial) / total if total else 0.0), done, total, hours, start


if __name__ == '__main__':
    c = Control(Job(sys.argv[2]))
    action = sys.argv[1]
    if action == 'pause':
        c.pause()
    elif action == 'resume':
        c.resume()
    pct, done, total, hours, _ = c.percent()
    print(json.dumps({'state': c.state(), 'pct': pct, 'done': done, 'total': total, 'hours_left': hours}))
