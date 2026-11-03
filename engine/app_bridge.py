"""Bridge between the desktop app and the restoration jobs.

The app embeds this file and runs it with the engine's Python. Every command prints JSON.

    jobs                       list the jobs
    tools                      setup checks, missing tools
    create <video>             new job with the tested settings
    status|pause|resume <id>   state and progress of a job
    pieces <id>                frame count of each finished piece
    frame <id> K J             frame J of piece K, restored and original, as data URLs
    video <id> K               files the player needs for piece K
    open <id> samples|output|compare|log
    cleanup <id>               finished job: delete its working files (space.py), keep the restored video

Nothing here changes a job's settings. Finished pieces are only deleted by cleanup, which refuses
unless the restored video exists and the job isn't running. Pause/resume go through control.py.
"""
import base64
import glob
import json
import os
import subprocess
import sys
import time

# run with python -c, so there's no __file__; the app tells us where the engine is
sys.path.insert(0, os.environ.get('TAPEWAKE_ENGINE') or os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(sys.path[0], 'tapewake_engine'))
from settings import (APP_DATA, CODEFORMER, FFMPEG, FFPROBE, JOBS, MODELS, PYTHON, TOOLS_FILE, VS_PYTHON,  # noqa: E402
                      Job, all_jobs, job_file, work_root)
import space  # noqa: E402

NO_WINDOW = 0x08000000
LOW = NO_WINDOW | 0x00004000  # below normal priority, so we never slow the restore down
THUMBS_PER_S = 2
FPS = 60000 / 1001  # restored output is 59.94 (one frame per field)

# Player cache limits, oldest deleted first. Restored pieces are played in place, never copied,
# so this stays around 135 MB per job at most.
KEEP_VIDEOS = 10  # ~11 MB each, plus its filmstrip
KEEP_JPEGS = 250  # ~0.1 MB each


def controller(job):
    from control import Control
    return Control(job)


def cache_dir(job):
    d = os.path.join(APP_DATA, 'cache', job.id)
    os.makedirs(d, exist_ok=True)
    return d


def source_info(job):
    """Details for the source card. ffprobe runs once, then it's cached."""
    path = os.path.join(cache_dir(job), 'source.json')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    d = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-show_entries',
                                   'stream=codec_type,codec_name,width,height,r_frame_rate,field_order,sample_rate,'
                                   'display_aspect_ratio:format=duration', '-of', 'json', job.source],
                                  capture_output=True, text=True, creationflags=NO_WINDOW).stdout or '{}')
    v = next((s for s in d.get('streams', []) if s['codec_type'] == 'video'), {})
    a = next((s for s in d.get('streams', []) if s['codec_type'] == 'audio'), {})
    num, den = (int(x) for x in v.get('r_frame_rate', '30000/1001').split('/'))
    fo = v.get('field_order', 'progressive')
    codec = {'mpeg2video': 'MPEG-2', 'h264': 'H.264', 'hevc': 'HEVC', 'dvvideo': 'DV', 'ffv1': 'FFV1'}.get(
        v.get('codec_name'), (v.get('codec_name') or '?').upper())
    acodec = {'ac3': 'AC-3', 'pcm_s16le': 'PCM', 'aac': 'AAC', 'mp2': 'MP2'}.get(a.get('codec_name'),
                                                                                 (a.get('codec_name') or '').upper())
    info = {
        'picture': f"{v.get('width')}×{v.get('height')} · {v.get('display_aspect_ratio', '4:3')} · {codec}",
        'rate': f"{num / den:.2f}{'i · ' + ('TFF' if fo in ('tt', 'tb') else 'BFF') if fo not in ('progressive', 'unknown') else 'p'}",
        'interlaced': f"{v.get('width')}×{v.get('height')} {num / den:.2f}{'i' if fo not in ('progressive', 'unknown') else 'p'}",
        'frames': int(float(d.get('format', {}).get('duration', 0)) * num / den),
        'audio': (f"{acodec} {int(a['sample_rate']) // 1000} kHz" if a else 'No audio') +
                 (f" · {job.data['audio_note']}" if job.data.get('audio_note') else ''),
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(info, f)
    return info


def space_info(job, state):
    """What Free up space would delete, for finished jobs only (the folder sizes are read each time)."""
    if state != 'finished':
        return None
    u = space.usage(job)
    work = u['source_cut'] + u['deinterlaced'] + u['in_progress'] + u['pieces']
    return {'working_gb': round(work / 1e9, 1), 'output_gb': round(u['output'] / 1e9, 1),
            'cleaned': os.path.exists(job.cleaned_flag), 'refused': space.can_free_up(job)}


def status(job, ctl, msg=''):
    pct, done, total, hours, start = ctl.percent()
    state = ctl.state()
    return {
        'id': job.id, 'name': job.name, 'preset': job.data.get('preset', 'Home-video tape · Tested'),
        'source': source_info(job), 'state': state, 'pct': pct, 'done': done, 'total': total,
        'hours_left': hours, 'eta': time.time() + hours * 3600 if hours is not None else None,
        'started': start, 'pieces': ctl.live(), 'stages': list(ctl.STAGES), 'shares': list(ctl.STEP_SHARE),
        'log': [ln.split('] ', 1)[-1] for ln in ctl.last_lines(4)],
        'output': job.output, 'msg': msg, 'space': space_info(job, state),
    }


CLEANED_MSG = ('The working files of this restoration were deleted to free up space, so the before/after '
               'player has nothing to show. The restored video is not affected.')


def cleanup(job, ctl):
    """Delete a finished job's working files and its player cache. Returns the message for the app."""
    if ctl.runner_running():
        return 'It is still running, so its working files are in use.'
    why = space.can_free_up(job)
    if why:
        return why
    freed = space.free_up_space(job)
    c = cache_dir(job)
    for name in os.listdir(c):
        if name != 'source.json':
            try:
                os.remove(os.path.join(c, name))
            except OSError:
                pass
    ctl.log(f'Free up space: deleted {space.gb(freed)} of working files (the restored video is kept)')
    return f'Deleted {space.gb(freed)} of working files. The restored video is kept.'


def prune(folder, pattern, keep, partner=None):
    for f in sorted(glob.glob(os.path.join(folder, pattern)), key=os.path.getmtime)[:-keep]:
        for g in (f, f.replace(*partner)) if partner else (f,):
            try:
                os.remove(g)
            except OSError:
                pass


def pieces(job):
    """Frame count of each finished piece. Only reads the duration so it's quick; cached by file size."""
    cache_file = os.path.join(cache_dir(job), 'pieces.json')
    try:
        with open(cache_file, encoding='utf-8') as f:
            known = json.load(f)
    except (OSError, ValueError):
        known = {}
    out = []
    for k in range(10000):
        p = job.piece(k)
        if not os.path.exists(p):
            break
        size = str(os.path.getsize(p))
        if known.get(str(k), [None])[0] != size:
            dur = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', p],
                                 capture_output=True, text=True, creationflags=NO_WINDOW).stdout
            known[str(k)] = [size, round(float(dur) * FPS)]
        out.append(known[str(k)][1])
    with open(cache_file + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(known, f)
    os.replace(cache_file + '.tmp', cache_file)
    return out


def grab(job, args, path):
    """Write one JPEG with ffmpeg (or reuse the cached one) and return it as a data URL."""
    if not os.path.exists(path):
        subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', *args, '-frames:v', '1', '-q:v', '3',
                        path + '.tmp.jpg'], check=True, creationflags=LOW)
        os.replace(path + '.tmp.jpg', path)
        prune(cache_dir(job), '*_rest.jpg', KEEP_JPEGS // 2)
        prune(cache_dir(job), '*_orig.jpg', KEEP_JPEGS // 2)
    else:
        os.utime(path)
    with open(path, 'rb') as f:
        return 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode()


def parity(job):
    return 'tff' if job.settings['field_order'] == 'tff' else 'bff'


def frame(job, k, j):
    """Restored frame j of piece k, plus the original at the same moment.

    The original is bobbed with bwdif (one picture per field), so restored frame j == source field j.
    """
    if os.path.exists(job.cleaned_flag):
        raise SystemExit(CLEANED_MSG)
    c = cache_dir(job)
    s0 = max(0, j // 2 - 2)  # start a little early so bwdif has neighbours
    pick = 2 * (j // 2 - s0) + j % 2
    return {
        'rest': grab(job, ['-ss', f'{(j * 1001 - 500) / 60000:.6f}', '-i', job.piece(k)],
                     os.path.join(c, f'{k:03d}_{j:05d}_rest.jpg')),
        'orig': grab(job, ['-ss', f'{(s0 * 1001 - 500) / 30000:.6f}' if s0 else '0', '-i', job.src_piece(k), '-vf',
                           f'bwdif=mode=send_field:parity={parity(job)}:deint=all,select=eq(n\\,{pick}),'
                           'scale=640:480,setsar=1'],
                     os.path.join(c, f'{k:03d}_{j:05d}_orig.jpg')),
    }


def video(job, k):
    """Files the player needs for piece k.

    The restored piece plays as is. The source cut is lossless and the webview can't play that, so we make
    a small 640x480 H.264 copy once (bobbed like frame(), so the timelines match). The filmstrip comes out
    of the same ffmpeg pass. Takes ~4 s on two CPU threads, no GPU.
    """
    if os.path.exists(job.cleaned_flag):
        raise SystemExit(CLEANED_MSG)
    c = cache_dir(job)
    if not os.path.exists(job.piece(k)):
        raise SystemExit(f'Piece {k + 1} is not finished yet.')
    frames = pieces(job)[k]
    cols = int(frames * THUMBS_PER_S / FPS) + 2
    orig = os.path.join(c, f'{k:03d}_orig.mp4')
    strip = os.path.join(c, f'{k:03d}_strip.jpg')
    if not (os.path.exists(orig) and os.path.exists(strip)):
        subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', '-threads', '2', '-i', job.src_piece(k),
                        '-filter_complex',
                        f'bwdif=mode=send_field:parity={parity(job)}:deint=all,scale=640:480,setsar=1,split[a][b];'
                        f'[b]fps={THUMBS_PER_S},scale=128:96,tile={cols}x1[t]',
                        '-map', '[a]', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '2',
                        '-movflags', '+faststart', '-an', orig + '.tmp.mp4',
                        '-map', '[t]', '-frames:v', '1', '-q:v', '4', strip + '.tmp.jpg'],
                       check=True, creationflags=LOW)
        os.replace(orig + '.tmp.mp4', orig)
        os.replace(strip + '.tmp.jpg', strip)
        prune(c, '*_orig.mp4', KEEP_VIDEOS, ('_orig.mp4', '_strip.jpg'))
    else:
        os.utime(orig)  # bump it so prune() keeps it
    return {'k': k, 'frames': frames, 'rest': job.piece(k), 'orig': orig, 'strip': strip, 'cols': cols,
            'per_s': THUMBS_PER_S}


def open_item(job, which):
    if which == 'samples':
        target = job.samples
        os.makedirs(target, exist_ok=True)
    elif which == 'output':
        target = job.output if os.path.exists(job.output) else os.path.dirname(job.output)
        os.makedirs(target, exist_ok=True)
    elif which == 'log':
        target = job.log if os.path.exists(job.log) else job.folder
        if not os.path.exists(target):
            return 'This job has no log yet: it is written once the restoration starts.'
    else:  # newest before/after video
        found = sorted(glob.glob(os.path.join(job.samples, '* - Before and after.mp4')), key=os.path.getmtime)
        if not found:
            return 'No before/after video has been made yet.'
        target = found[-1]
    os.startfile(target)
    return f'Opened {os.path.basename(target) or target}.'


def missing_tools():
    """Tools the engine needs but can't find."""
    need = {'FFmpeg': FFMPEG, 'the engine Python': PYTHON, 'the VapourSynth Python': VS_PYTHON,
            'the models folder': MODELS, 'CodeFormer': CODEFORMER}
    return [name for name, path in need.items() if not (os.path.isabs(path) and os.path.exists(path))]


def checks():
    """Setup checks for this PC, as [name, ok, detail] rows."""
    import platform
    import shutil
    build = int((platform.version().split('.') + ['0', '0', '0'])[2] or 0)
    out = [['Windows version', platform.system() == 'Windows' and build >= 19041,
            f"Windows {'11' if build >= 22000 else '10'} (build {build})"]]
    try:
        gpu = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'],
                             capture_output=True, text=True, creationflags=NO_WINDOW, timeout=10).stdout.strip()
        out.append(['Graphics processor and memory', bool(gpu), gpu.splitlines()[0] if gpu else 'No NVIDIA graphics card found'])
    except (OSError, subprocess.TimeoutExpired):
        out.append(['Graphics processor and memory', False, 'No NVIDIA graphics card found (the engine needs one for now)'])
    root = work_root(os.path.expanduser('~\\x'))
    try:
        free = shutil.disk_usage(root if os.path.exists(root) else os.path.splitdrive(root)[0] + os.sep).free / 1e9
        out.append(['Free disk space', free > 110, f'{free:.0f} GB free in {root} (a two-hour tape needs about '
                    '100 GB at the peak: about 50 GB per hour of tape)'])
    except OSError:
        out.append(['Free disk space', False, f'Could not read {root}'])
    missing = missing_tools()
    out.append(['FFmpeg, Python, VapourSynth and models', not missing,
                'All found' if not missing else 'Missing: ' + ', '.join(missing) + f' (set in {TOOLS_FILE})'])
    return out


def create(source):
    """Make a job file for `source` with the tested settings.

    Work files go in tools.json's work_root (or next to the video); the result goes next to the video.
    """
    source = os.path.abspath(source)
    if not os.path.isfile(source):
        raise SystemExit(f'Video not found: {source}')
    base = os.path.splitext(os.path.basename(source))[0]
    slug = '-'.join(filter(None, ''.join(c if c.isascii() and c.isalnum() else '-' for c in base.lower()).split('-')))
    slug = slug[:48].strip('-') or 'video'
    job_id, n = slug, 2
    while os.path.exists(job_file(job_id)):
        job_id, n = f'{slug}-{n}', n + 1
    os.makedirs(JOBS, exist_ok=True)
    data = {'id': job_id, 'name': os.path.basename(source), 'source': source,
            'folder': os.path.join(work_root(source), f'{base} - tapewake work'),
            'output': os.path.join(os.path.dirname(source), f'{base} - Restored.mkv'),
            'preset': 'Home-video tape · Tested', 'settings': {}, 'created': time.strftime('%Y-%m-%d %H:%M:%S')}
    with open(job_file(job_id), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def busy_elsewhere(job):
    """Name of another running job, if there is one. Only one job gets the GPU at a time."""
    for other in all_jobs():
        if other.id != job.id:
            try:
                if controller(other).state() == 'running':
                    return other.name
            except Exception:  # noqa: BLE001 - don't let a broken job file block this one
                pass
    return None


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'jobs'
    if cmd == 'jobs':
        print(json.dumps({'jobs': [{'id': j.id, 'name': j.name} for j in all_jobs()]}))
        return
    if cmd == 'tools':
        print(json.dumps({'missing': missing_tools(), 'tools_file': TOOLS_FILE, 'checks': checks()}))
        return
    if cmd == 'create':
        missing = missing_tools()
        if missing:
            raise SystemExit('tapewake needs ' + ', '.join(missing) + f' before it can restore. Their locations go in {TOOLS_FILE}.')
        d = create(sys.argv[2])
        print(json.dumps({'id': d['id'], 'name': d['name']}))
        return
    job = Job(job_file(sys.argv[2]))
    args = sys.argv[3:]
    if cmd == 'pieces':
        print(json.dumps({'frames': pieces(job)}))
        return
    if cmd == 'frame':
        print(json.dumps(frame(job, int(args[0]), int(args[1]))))
        return
    if cmd == 'video':
        print(json.dumps(video(job, int(args[0]))))
        return
    if cmd == 'open':
        print(json.dumps({'msg': open_item(job, args[0])}))
        return
    ctl = controller(job)
    msg = ''
    if cmd == 'cleanup':
        msg = cleanup(job, ctl)
    elif cmd == 'pause':
        if ctl.state() != 'running':
            msg = 'It is not running.'
        else:
            ctl.pause()
            msg = 'Paused. The graphics card is free; finished pieces are kept.'
    elif cmd == 'resume':
        other = busy_elsewhere(job)
        if ctl.runner_running():
            msg = 'It is already running.'
        elif other:
            msg = f'{other} is restoring now. One restoration runs at a time: start this one when that one finishes or is paused.'
        else:
            ctl.resume()  # detached, so it keeps going if the app is closed
            time.sleep(3)
            msg = 'Resumed. Finished pieces are skipped, so it carries on where it stopped.'
    print(json.dumps(status(job, ctl, msg)))


if __name__ == '__main__':
    main()
