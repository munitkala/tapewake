"""Bridge between the desktop app and the restoration jobs.

The app embeds this file and runs it with the engine's Python. Every command prints JSON.

    jobs                       list the jobs
    tools                      setup checks, missing tools
    system                     this computer's processor, graphics, memory and how hot it is
    probe <video>              scan type, frame rate and size, and which mode (tape or progressive) fits it
    create <video> [--mode tape|progressive] [--no-faces] [--no-audio]
                               new job with the tested settings; refuses a mode the video can't use
    status|pause|resume <id>   state and progress of a job
    pieces <id>                frame count of each finished piece
    frame <id> K J             frame J of piece K, restored and original, as data URLs
    video <id> K               files the player needs for piece K
    open <id> samples|output|compare|log
    cleanup <id>               finished job: delete its working files (space.py), keep the restored video
    finish <id>                finished job: show the restored video in Explorer/Finder and take the job out of tapewake
    delete <id> check|confirm  stop a job and delete its working files and its entry (check says what goes)

Nothing here changes a job's settings. Finished pieces are only deleted by cleanup and finish, which
refuse unless the restored video exists and the job isn't running, and by delete, which the app only sends
after three clicks. None of them touch the source video. Pause/resume go through control.py.
"""
import base64
import glob
import json
import os
import shutil
import subprocess
import sys
import time

# run with python -c, so there's no __file__; the app tells us where the engine is
sys.path.insert(0, os.environ.get('TAPEWAKE_ENGINE') or os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(sys.path[0], 'tapewake_engine'))
from settings import (APP_DATA, CODEFORMER, FFMPEG, FFPROBE, IS_MAC, JOBS, MODELS, PYTHON, TOOLS_FILE,  # noqa: E402
                      VS_PYTHON, Job, all_jobs, job_file, progressive_settings, work_root)
import space  # noqa: E402
import thermal  # noqa: E402

NO_WINDOW = 0x08000000
LOW = NO_WINDOW | 0x00004000  # below normal priority, so we never slow the restore down
THUMBS_PER_S = 2
FPS = 60000 / 1001  # restored output is 59.94 (one frame per field)

# Player cache limits, oldest deleted first. Restored pieces are played in place, never copied,
# so this stays around 135 MB per job at most.
KEEP_VIDEOS = 10  # ~11 MB each, plus its filmstrip
KEEP_JPEGS = 250  # ~0.1 MB each
# A Mac's webview can't play Matroska, so there the player gets an MP4 copy of the restored piece (same video, no
# re-encode). Those are full size (~150 MB per piece), so only a few are kept.
KEEP_MP4_COPIES = 3


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
        'own_scripts': bool(job.data.get('controller')),
        'mode': 'progressive' if job['progressive'] else 'tape', 'fps': fps(job), 'per': 1 if job['progressive'] else 2,
        'faces': bool(job['faces']), 'restore_audio': bool(job['restore_audio']),
        'prep': None if state == 'finished' else ctl.prep(),  # [step, fraction] until piece 1 starts
        'problem': ctl.prep_problem() if state == 'running' else '',
        # paused because it got too hot ({'since', 'reason'}); it resumes by itself once it's cool
        'cooling': thermal.heat_pause(job),
        'thermal': thermal.now() if state == 'running' or os.path.exists(job.thermal_flag) else None,
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


def finish(job, ctl):
    """Finished job: show the restored video in Explorer or the Finder, delete the working files (unless the job runs
    from its own scripts) and the player cache, then remove the job file. The restored video, the samples and the log
    are kept."""
    if ctl.state() != 'finished':
        return {'done': False, 'msg': 'It has not finished yet, so it stays in tapewake.'}
    freed = 0
    if not space.can_free_up(job) and not os.path.exists(job.cleaned_flag):
        freed = space.free_up_space(job)
        ctl.log(f'Finish: deleted {space.gb(freed)} of working files (the restored video is kept)')
    shutil.rmtree(os.path.join(APP_DATA, 'cache', job.id), ignore_errors=True)
    os.remove(job.path)
    reveal(job.output)
    kept = f'Deleted {space.gb(freed)} of working files. ' if freed else ''
    return {'done': True, 'msg': f'{kept}{os.path.basename(job.output)} is ready in {os.path.dirname(job.output)}.'}


def sharing_folder(job):
    """Another job using the same work folder (projects made before 0.1.3 could), or None."""
    same = os.path.normcase(os.path.abspath(job.folder))
    return next((o for o in all_jobs() if o.id != job.id and os.path.normcase(os.path.abspath(o.folder)) == same), None)


def delete_check(job):
    """What Stop and delete would do, for the confirmation window."""
    if job.data.get('controller'):
        return {'refused': 'This restoration runs from its own scripts, so tapewake does not delete it.'}
    other = sharing_folder(job)
    return {'refused': '', 'folder': job.folder, 'shared_with': other.name if other else None,
            'gb': 0.0 if other else round(sum(space._size(p) for p in space.project_files(job)) / 1e9, 1),
            'output_exists': os.path.exists(job.output)}


def delete_project(job, ctl):
    """Stop the job, delete its working files and player cache, and remove the job file. The source video, a
    finished restored video and saved samples are kept. When another job shares the work folder, only this job's
    entry and cache go: its files and processes belong to both."""
    check = delete_check(job)
    if check['refused']:
        return {'done': False, 'msg': check['refused']}
    freed = 0
    if not check['shared_with']:
        if ctl.state() == 'running':
            ctl.pause()
        freed = space.delete_project_files(job)
    shutil.rmtree(os.path.join(APP_DATA, 'cache', job.id), ignore_errors=True)
    os.remove(job.path)
    if check['shared_with']:
        return {'done': True, 'msg': f"Removed {job.name} from tapewake. Its working folder is shared with another "
                                     f"project of the same video, so its files were kept."}
    return {'done': True, 'msg': f'Deleted {job.name}: {space.gb(freed)} of working files. '
                                 f'Your original video was not touched.'}


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
            known[str(k)] = [size, round(float(dur) * fps(job))]
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


def fps(job):
    """Restored frames a second: 59.94 in tape mode (one per field), the video's own rate in progressive mode."""
    num, den = (int(x) for x in job['out_fps'].split('/'))
    return num / den


def to_original(job):
    """ffmpeg filters that turn the source cut into frames that line up with the restored ones."""
    if job['progressive']:
        return f"scale={job['work_w']}:{job['work_h']},setsar=1"
    return f'bwdif=mode=send_field:parity={parity(job)}:deint=all,scale=640:480,setsar=1'


def frame(job, k, j):
    """Restored frame j of piece k, plus the original at the same moment.

    The original is bobbed with bwdif (one picture per field), so restored frame j == source field j.
    """
    if os.path.exists(job.cleaned_flag):
        raise SystemExit(CLEANED_MSG)
    c = cache_dir(job)
    if job['progressive']:  # one restored frame per source frame
        t = f'{(j - 0.5) / fps(job):.6f}' if j else '0'
        return {'rest': grab(job, ['-ss', t, '-i', job.piece(k)], os.path.join(c, f'{k:03d}_{j:05d}_rest.jpg')),
                'orig': grab(job, ['-ss', t, '-i', job.src_piece(k), '-vf', to_original(job)],
                             os.path.join(c, f'{k:03d}_{j:05d}_orig.jpg'))}
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

    The restored piece plays as is (on a Mac from an MP4 copy, see KEEP_MP4_COPIES). The source cut is lossless and the webview can't play that, so we make
    a small 640x480 H.264 copy once (bobbed like frame(), so the timelines match). The filmstrip comes out
    of the same ffmpeg pass. Takes ~4 s on two CPU threads, no GPU.
    """
    if os.path.exists(job.cleaned_flag):
        raise SystemExit(CLEANED_MSG)
    c = cache_dir(job)
    if not os.path.exists(job.piece(k)):
        raise SystemExit(f'Piece {k + 1} is not finished yet.')
    frames = pieces(job)[k]
    cols = int(frames * THUMBS_PER_S / fps(job)) + 2
    orig = os.path.join(c, f'{k:03d}_orig.mp4')
    strip = os.path.join(c, f'{k:03d}_strip.jpg')
    if not (os.path.exists(orig) and os.path.exists(strip)):
        subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', '-threads', '2', '-i', job.src_piece(k),
                        '-filter_complex',
                        f'{to_original(job)},split[a][b];'
                        f'[b]fps={THUMBS_PER_S},scale=-2:96,tile={cols}x1[t]',
                        '-map', '[a]', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '2',
                        '-movflags', '+faststart', '-an', orig + '.tmp.mp4',
                        '-map', '[t]', '-frames:v', '1', '-q:v', '4', strip + '.tmp.jpg'],
                       check=True, creationflags=LOW)
        os.replace(orig + '.tmp.mp4', orig)
        os.replace(strip + '.tmp.jpg', strip)
        prune(c, '*_orig.mp4', KEEP_VIDEOS, ('_orig.mp4', '_strip.jpg'))
    else:
        os.utime(orig)  # bump it so prune() keeps it
    rest = job.piece(k)
    if IS_MAC:
        rest = os.path.join(c, f'{k:03d}_rest.mp4')
        if not os.path.exists(rest):
            subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', '-i', job.piece(k), '-map', '0:v', '-c', 'copy',
                            '-movflags', '+faststart', rest + '.tmp.mp4'], check=True, creationflags=LOW)
            os.replace(rest + '.tmp.mp4', rest)
            prune(c, '*_rest.mp4', KEEP_MP4_COPIES)
        else:
            os.utime(rest)
    return {'k': k, 'frames': frames, 'rest': rest, 'orig': orig, 'strip': strip, 'cols': cols,
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
    if IS_MAC:
        subprocess.Popen(['open', target])
    else:
        os.startfile(target)
    return f'Opened {os.path.basename(target) or target}.'


def reveal(path):
    """Open the folder with `path` selected."""
    if IS_MAC:
        subprocess.Popen(['open', '-R', path])
    else:
        subprocess.Popen(f'explorer /select,"{path}"')


def missing_tools():
    """Tools the engine needs but can't find."""
    need = {'FFmpeg': FFMPEG, 'the engine Python': PYTHON, 'the VapourSynth Python': VS_PYTHON,
            'the models folder': MODELS, 'CodeFormer': CODEFORMER}
    return [name for name, path in need.items() if not (os.path.isabs(path) and os.path.exists(path))]


# What a Mac needs. The VapourSynth plugins are only built for macOS 15 and later. The models share the Mac's
# memory with everything else, and 16 GB leaves them about what the 8 GB graphics card had on the test PC.
MAC_MIN_OS = (15, 0)
MAC_MIN_MEMORY_GB = 16
# Working files at the peak, per hour of video, as measured (see engine/README.md): tape mode 50 GB at
# 640x480 59.94, progressive mode about 40 GB for a 720x464 29.97 DVD clip. Other sizes and rates scale with
# the number of pixels a second. 10% on top, so a run doesn't stop near the end because the disk is full.
GB_PER_HOUR = {'tape': (50, 640 * 480 * 60000 / 1001), 'progressive': (40, 720 * 464 * 30000 / 1001)}
DISK_MARGIN = 1.1


def sysctl(name):
    try:
        out = subprocess.run(['/usr/sbin/sysctl', '-n', name], capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ''


def system_info():
    """This computer for the app: [label, value] rows that don't change (cached), plus free space and heat now."""
    path = os.path.join(APP_DATA, 'cache', 'system.json')
    try:
        with open(path, encoding='utf-8') as f:
            rows = json.load(f)
    except (OSError, ValueError):
        rows = mac_specs() if IS_MAC else windows_specs()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(rows, f)
    root = work_root(os.path.join(os.path.expanduser('~'), 'x'))
    try:
        rows = rows + [['Free space for working files', f'{free_gb(root):.0f} GB in {shown(root)}']]
    except OSError:
        pass
    return {'specs': rows, 'thermal': thermal.now()}


def models_run_on():
    try:
        import torch
    except ImportError:
        return 'PyTorch is not installed yet'
    if torch.cuda.is_available():
        return f'{torch.cuda.get_device_name(0)} (CUDA)'
    if torch.backends.mps.is_available():
        return 'Apple GPU (Metal)'
    if not IS_MAC and directml_ready():
        cards = windows_gpus()
        card = cards[0] if cards else 'the graphics card'
        return f'{card} (DirectML) for upscaling; face restoration on the processor'
    return 'Processor only (no supported graphics processor found)'


def windows_gpus():
    """Names of the graphics cards Windows knows about, any make. Leaves out the stand-in display drivers."""
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_VideoController).Name'],
                             capture_output=True, text=True, creationflags=NO_WINDOW, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    skip = ('basic display', 'basic render', 'remote display', 'virtual display')
    return [n.strip() for n in out.splitlines() if n.strip() and not any(k in n.lower() for k in skip)]


def directml_ready():
    """Can the upscale step run on a non-NVIDIA card here (onnxruntime-directml in the engine Python)?"""
    try:
        from ort_models import gpu_provider
        return gpu_provider() is not None
    except ImportError:
        return False


def mac_specs():
    hw = mac_hardware()
    perf, eff = sysctl('hw.perflevel0.physicalcpu'), sysctl('hw.perflevel1.physicalcpu')
    cores = f'{perf} performance + {eff} efficiency' if perf and eff else sysctl('hw.physicalcpu')
    model = sysctl('hw.model')
    return [['Mac', model or 'Unknown'],
            ['Chip', hw['chip']],
            ['Processor cores', cores],
            ['Graphics', f"{hw['gpu_cores']}-core GPU, built into the chip" if hw['gpu_cores'] else 'Built into the chip'],
            ['Memory', f"{hw['memory_gb']:.0f} GB, shared by the processor and graphics"],
            ['macOS', hw['version']],
            ['AI models run on', models_run_on()]]


def windows_specs():
    import platform
    import psutil
    rows = [['Processor', platform.processor() or 'Unknown'],
            ['Processor cores', f'{psutil.cpu_count(logical=False)} ({psutil.cpu_count()} threads)'],
            ['Memory', f'{psutil.virtual_memory().total / 2 ** 30:.0f} GB'],
            ['Windows', platform.version()]]
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader'],
                             capture_output=True, text=True, creationflags=NO_WINDOW, timeout=10).stdout.strip()
        name, mem, driver = (x.strip() for x in out.splitlines()[0].split(','))
        rows.insert(2, ['Graphics', f'{name}, {mem}, driver {driver}'])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        cards = windows_gpus()
        rows.insert(2, ['Graphics', ', '.join(cards) if cards else 'No graphics card found'])
    return rows + [['AI models run on', models_run_on()]]


def mac_hardware():
    """macOS version, chip, memory and GPU cores of this Mac."""
    import platform
    ver = platform.mac_ver()[0] or '0'
    gpu_cores = ''
    try:
        out = subprocess.run(['/usr/sbin/system_profiler', 'SPDisplaysDataType'], capture_output=True, text=True,
                             timeout=20).stdout
        gpu_cores = next((ln.split(':')[1].strip() for ln in out.splitlines() if 'Total Number of Cores' in ln), '')
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {'version': ver, 'os': tuple(int(x) for x in (ver.split('.') + ['0'])[:2]),
            # hw.optional.arm64 is 1 on Apple Silicon even when this Python runs under Rosetta
            'apple_silicon': sysctl('hw.optional.arm64') == '1',
            'chip': sysctl('machdep.cpu.brand_string') or 'Unknown chip',
            'memory_gb': int(sysctl('hw.memsize') or 0) / 2 ** 30, 'gpu_cores': gpu_cores}


def mac_problem(hw=None):
    """Why this Mac can't run tapewake, or ''."""
    hw = hw or mac_hardware()
    if not hw['apple_silicon']:
        return ('tapewake needs a Mac with Apple Silicon (M1 or later): the AI models run on its graphics '
                'processor. Intel Macs are not supported.')
    if hw['os'] < MAC_MIN_OS:
        return f"tapewake needs macOS 15 or later. This Mac has macOS {hw['version']}."
    if hw['memory_gb'] < MAC_MIN_MEMORY_GB - 0.5:
        return (f"tapewake needs at least {MAC_MIN_MEMORY_GB} GB of memory for its AI models. This Mac has "
                f"{hw['memory_gb']:.0f} GB.")
    return ''


def disk_needed_gb(probe, mode):
    """Rough peak disk space for restoring this video, in GB."""
    per_hour, ref = GB_PER_HOUR[mode]
    if mode == 'tape':
        rate = ref
    else:
        num, den = (int(x) for x in probe['fps'].split('/'))
        rate = probe['work_w'] * probe['work_h'] * num / den
    return DISK_MARGIN * per_hour * probe['duration'] / 3600 * rate / ref


def shown(path):
    """A path for the app to show, with the home folder as ~ (so screenshots don't carry the user's name)."""
    home = os.path.expanduser('~')
    return '~' + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def existing_parent(path):
    """`path`, or the nearest folder above it that exists (the work folder isn't made until the run starts)."""
    while path and not os.path.exists(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return path or os.sep


def free_gb(path):
    """Free space on the drive that holds `path`, in GB."""
    return shutil.disk_usage(existing_parent(path)).free / 1e9


def disk_problem(source, probe, mode):
    """Why there isn't enough disk space to restore this video, or ''. The working files go in work_root,
    the restored video (about a third of the peak) next to the source."""
    need = disk_needed_gb(probe, mode)
    work, out = work_root(source), os.path.dirname(os.path.abspath(source))
    try:
        same = os.stat(existing_parent(work)).st_dev == os.stat(out).st_dev
        free_work, free_out = free_gb(work), free_gb(out)
    except OSError:
        return ''  # can't tell; the run itself stops cleanly if the disk fills up
    if same:
        short = [(need, free_work, work)] if free_work < need else []
    else:
        short = [(n, f, p) for n, f, p in ((need * 0.65, free_work, work), (need * 0.35, free_out, out)) if f < n]
    if not short:
        return ''
    n, f, p = short[0]
    hours = probe['duration'] / 3600
    return (f'Restoring this video ({hours:.1f} h) needs about {n:.0f} GB free in {p}, but there is only {f:.0f} GB. '
            f'Free up some space, or point "work_root" in {TOOLS_FILE} at a bigger drive.')


def checks():
    """Setup checks for this computer, as [name, ok, detail] rows."""
    out = mac_rows() if IS_MAC else windows_rows()
    root = work_root(os.path.join(os.path.expanduser('~'), 'x'))
    try:
        free = free_gb(root)
        out.append(['Free disk space', free > 110, f'{free:.0f} GB free in {shown(root)} (a two-hour tape needs about '
                    '100 GB at the peak: about 50 GB per hour of tape)'])
    except OSError:
        out.append(['Free disk space', False, f'Could not read {shown(root)}'])
    missing = missing_tools()
    out.append(['FFmpeg, Python, VapourSynth and models', not missing,
                'All found' if not missing else 'Missing: ' + ', '.join(missing) + f' (set in {TOOLS_FILE})'])
    return out


def windows_rows():
    import platform
    build = int((platform.version().split('.') + ['0', '0', '0'])[2] or 0)
    out = [['Windows version', platform.system() == 'Windows' and build >= 19041,
            f"Windows {'11' if build >= 22000 else '10'} (build {build})"]]
    try:
        gpu = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'],
                             capture_output=True, text=True, creationflags=NO_WINDOW, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        gpu = ''
    if gpu:
        out.append(['Graphics processor and memory', True, gpu.splitlines()[0]])
    else:
        out.append(['Graphics processor and memory', *other_gpu_row()])
    return out


def other_gpu_row():
    """[ok, detail] for a PC without an NVIDIA card: AMD and Intel cards work through DirectML."""
    cards = windows_gpus()
    if not cards:
        return [False, 'No graphics card found. tapewake needs an NVIDIA card, or an AMD or Intel card with DirectX 12.']
    names = ', '.join(cards)
    if directml_ready():
        return [True, f'{names} (DirectML). Upscaling runs on the card. Face restoration runs on the processor and is '
                      'very slow, so turn it off for long videos.']
    return [False, f"{names}: tapewake can't use this card yet. NVIDIA cards need the NVIDIA driver; AMD and Intel "
                   'cards need onnxruntime-directml in the engine Python (see BUILDING.md, "AMD and Intel graphics").']


def mac_rows():
    hw = mac_hardware()
    out = [['macOS version', hw['os'] >= MAC_MIN_OS,
            f"macOS {hw['version']}" + ('' if hw['os'] >= MAC_MIN_OS else ' (tapewake needs macOS 15 or later)')]]
    cores = f", {hw['gpu_cores']}-core GPU" if hw['gpu_cores'] else ''
    chip = f"{hw['chip']}{cores}, {hw['memory_gb']:.0f} GB memory"
    problem = mac_problem(hw) if hw['os'] >= MAC_MIN_OS else ''  # the macOS row already says that one
    if not problem:
        try:
            import torch
            if not torch.backends.mps.is_available():
                problem = 'PyTorch can not use the Apple GPU (Metal). Reinstall the engine Python (BUILDING-macOS.md).'
        except ImportError:
            problem = 'PyTorch is not installed in the engine Python yet.'
    out.append(['Graphics processor and memory', not problem, chip + (f'. {problem}' if problem else '')])
    return out


TAPE_FPS = '30000/1001'  # tape mode is tested on NTSC tapes only


def probe_video(path):
    """What kind of video this is, and which mode can restore it.

    scan is 'interlaced' (each frame is two fields: tape mode deinterlaces it), 'progressive' (whole pictures:
    progressive mode) or 'unknown' (the file doesn't say). work_w x work_h is the square-pixel working size for
    progressive mode, rounded to multiples of 8 for the flicker smoothing.
    """
    d = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-show_entries',
                                   'stream=codec_type,width,height,r_frame_rate,field_order,sample_aspect_ratio:'
                                   'format=duration', '-of', 'json', path],
                                  capture_output=True, text=True, creationflags=NO_WINDOW).stdout or '{}')
    v = next((s for s in d.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not v:
        raise SystemExit('This file has no video tapewake can read.')
    w, h, rate = int(v['width']), int(v['height']), v.get('r_frame_rate', '0/1')
    fo = v.get('field_order', 'unknown')
    scan = 'interlaced' if fo in ('tt', 'bb', 'tb', 'bt') else 'progressive' if fo == 'progressive' else 'unknown'
    try:
        sn, sd = (int(x) for x in v.get('sample_aspect_ratio', '1:1').split(':'))
    except ValueError:
        sn = sd = 1
    sar = sn / sd if sn > 0 and sd > 0 else 1.0
    num, den = (int(x) for x in rate.split('/'))
    per8 = lambda x: max(8, int(round(x / 8)) * 8)  # noqa: E731
    modes = []
    if scan != 'progressive' and rate == TAPE_FPS:
        modes.append('tape')
    if scan != 'interlaced':
        modes.append('progressive')
    mode = 'progressive' if scan == 'progressive' else 'tape' if 'tape' in modes else modes[0] if modes else None
    return {'scan': scan, 'fps': rate, 'fps_label': f'{num / den:.3f}'.rstrip('0').rstrip('.') if den else '?',
            'width': w, 'height': h, 'work_w': per8(w * sar), 'work_h': per8(h),
            'audio': any(s.get('codec_type') == 'audio' for s in d.get('streams', [])),
            'duration': float(d.get('format', {}).get('duration') or 0), 'modes': modes, 'mode': mode}


def mode_problem(probe, mode):
    """Why `mode` can't restore this video, or ''. The app shows the same reasons before starting."""
    rate = f"{probe['fps_label']} fps"
    if mode == 'tape':
        if probe['scan'] == 'progressive':
            return ('This video is already progressive: every frame is a whole picture, so there is nothing to '
                    'deinterlace. Turn Deinterlace off (Progressive video preset).')
        if probe['fps'] != TAPE_FPS:
            return (f'Deinterlacing is only tested on NTSC tapes (29.97 fps). This video is {rate}, so tapewake '
                    "can't restore it yet.")
    elif mode == 'progressive':
        if probe['scan'] == 'interlaced':
            return ('This video is interlaced: each frame holds two half-pictures taken at different moments. '
                    'It needs Deinterlace on (Home-video tape preset).')
    else:
        return f'Unknown mode {mode!r}.'
    return ''


def create(source, faces=True, audio=True, mode=None):
    """Make a job file for `source` with the tested settings.

    mode is 'tape' (deinterlace, the tested home-video settings) or 'progressive' (no deinterlacing, the video's own
    frame rate); None picks the one that fits. faces=False turns face restoration off, audio=False keeps the
    original sound as it is. Work files go in tools.json's work_root (or next to the video), in a folder no other
    job uses; the result goes next to the video.
    """
    source = os.path.abspath(source)
    if not os.path.isfile(source):
        raise SystemExit(f'Video not found: {source}')
    probe = probe_video(source)
    mode = mode or probe['mode']
    why = mode_problem(probe, mode) if mode else "tapewake can't tell how to restore this video."
    if not why and IS_MAC:  # check the Mac and the disk up front instead of failing hours in
        why = mac_problem() or disk_problem(source, probe, mode)
    if why:
        raise SystemExit(why)
    base = os.path.splitext(os.path.basename(source))[0]
    slug = '-'.join(filter(None, ''.join(c if c.isascii() and c.isalnum() else '-' for c in base.lower()).split('-')))
    slug = slug[:48].strip('-') or 'video'
    job_id, n = slug, 2
    while os.path.exists(job_file(job_id)):
        job_id, n = f'{slug}-{n}', n + 1
    os.makedirs(JOBS, exist_ok=True)
    used = {os.path.normcase(os.path.abspath(j.folder)) for j in all_jobs()}
    folder, n = os.path.join(work_root(source), f'{base} - tapewake work'), 2
    while os.path.exists(folder) or os.path.normcase(os.path.abspath(folder)) in used:
        folder, n = os.path.join(work_root(source), f'{base} - tapewake work ({n})'), n + 1
    settings = progressive_settings(probe) if mode == 'progressive' else {}
    if not faces:
        settings['faces'] = False
    if not audio:
        settings['restore_audio'] = False
    preset = 'Progressive video · Tested' if mode == 'progressive' else 'Home-video tape · Tested'
    off = [n for n, on in (('face restoration', faces), ('sound restoration', audio)) if not on]
    data = {'id': job_id, 'name': os.path.basename(source), 'source': source, 'folder': folder,
            'output': os.path.join(os.path.dirname(source), f'{base} - Restored.mkv'),
            'preset': preset + (', no ' + ' or '.join(off) if off else ''),
            'settings': settings, 'created': time.strftime('%Y-%m-%d %H:%M:%S')}
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
    if cmd == 'system':
        print(json.dumps(system_info()))
        return
    if cmd == 'tools':
        print(json.dumps({'missing': missing_tools(), 'tools_file': TOOLS_FILE, 'checks': checks()}))
        return
    if cmd == 'probe':
        print(json.dumps(probe_video(sys.argv[2])))
        return
    if cmd == 'create':
        missing = missing_tools()
        if missing:
            raise SystemExit('tapewake needs ' + ', '.join(missing) + f' before it can restore. Their locations go in {TOOLS_FILE}.')
        rest = sys.argv[3:]
        mode = rest[rest.index('--mode') + 1] if '--mode' in rest else None
        d = create(sys.argv[2], faces='--no-faces' not in rest, audio='--no-audio' not in rest, mode=mode)
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
    if cmd == 'finish':
        print(json.dumps(finish(job, ctl)))
        return
    if cmd == 'delete':
        print(json.dumps(delete_check(job) if args == ['check'] else delete_project(job, ctl) if args == ['confirm']
                         else {'done': False, 'msg': 'delete needs check or confirm'}))
        return
    msg = ''
    if cmd == 'cleanup':
        msg = cleanup(job, ctl)
    elif cmd == 'pause':
        if os.path.exists(job.thermal_flag):  # cooling down: pausing now means it stays paused
            ctl.pause()
            msg = 'Kept paused. It will not resume by itself; press Resume when you want it to carry on.'
        elif ctl.state() != 'running':
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
