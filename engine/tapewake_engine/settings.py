"""Paths to the tools, plus the Job class.

tools.json (in the app data folder) says where things are installed:

    ffmpeg, models, codeformer, python, vs_python   and optionally work_root

Anything missing falls back to the defaults below.

A job is jobs/<id>.json with id, name, source, folder, output, samples (optional) and
settings (overrides for TESTED). Inside the work folder:

    src/src_NNN.mkv      lossless interlaced pieces (+ src/done: how many)
    qtgmc/qt_NNN.mkv     deinterlaced; each is deleted once it and its neighbours are restored
    work/pNNN/           piece in progress
    pieces/piece_NNN.mkv finished
    audio/, run.log, PAUSED, THERMAL_PAUSE (too hot, see thermal.py), CLEANED (working files deleted after the
    finish, see space.py)

src/ and qtgmc/ are lossless, so they're big: see space.py and the README for how much.

Stdlib only - the VapourSynth Python imports this too.
"""
import glob
import json
import os
import shutil
import subprocess
import sys

if os.name == 'nt':
    # ffmpeg, ffprobe and python are all console programs. A run started from the app has no console,
    # so anything launched without CREATE_NO_WINDOW pops up its own window for a moment. Every engine
    # script imports this module, so patching Popen here covers all of them (and the libraries too).
    class _NoWindowPopen(subprocess.Popen):
        def __init__(self, *args, creationflags=0, **kwargs):
            if not creationflags & 0x00000008:  # DETACHED_PROCESS has no window anyway
                creationflags |= 0x08000000  # CREATE_NO_WINDOW
            super().__init__(*args, creationflags=creationflags, **kwargs)

    subprocess.Popen = _NoWindowPopen
else:
    # On a Mac Popen refuses creationflags (it's Windows only), so drop it here and every call above can stay as it
    # is. The two flags that matter are carried over: DETACHED_PROCESS becomes a new session (the run keeps going
    # when the app quits), and the below-normal / idle priority classes become nice 10, so the player's ffmpeg
    # calls never slow the restoration down.
    class _PosixPopen(subprocess.Popen):
        def __init__(self, *args, creationflags=0, **kwargs):
            if creationflags & 0x00000008:  # DETACHED_PROCESS
                kwargs.setdefault('start_new_session', True)
            if creationflags & (0x00004000 | 0x00000040) and 'preexec_fn' not in kwargs:  # below normal | idle
                kwargs['preexec_fn'] = lambda: os.nice(10)
            super().__init__(*args, **kwargs)

    subprocess.Popen = _PosixPopen

IS_MAC = sys.platform == 'darwin'
# PyTorch on Apple GPUs (MPS): ops it hasn't got for Metal yet run on the CPU instead of stopping the step.
# Set here, before any step imports torch, and inherited by every process the run starts.
if IS_MAC:
    os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')

if os.name == 'nt':
    APP_DATA = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'com.tapewake.app')
else:  # where Tauri keeps app data on a Mac
    APP_DATA = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'com.tapewake.app')
JOBS = os.path.join(APP_DATA, 'jobs')
STUBS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'stubs')  # engine/stubs


def _load(path):
    try:
        with open(path, encoding='utf-8-sig') as f:  # -sig: Notepad likes to add a BOM
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _find_ffmpeg():
    # on a Mac, Homebrew's ffmpeg-full first: the plain ffmpeg formula has no drawtext, which the before/after video
    # needs, and it would usually be first on the PATH
    if os.name != 'nt' and os.path.exists('/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg'):
        return '/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg'
    found = shutil.which('ffmpeg')
    if found:
        return found
    if os.name == 'nt':
        winget = glob.glob(os.path.join(os.environ.get('LOCALAPPDATA', ''), r'Microsoft\WinGet\Packages\*FFmpeg*\*\bin\ffmpeg.exe'))
        return sorted(winget)[-1] if winget else 'ffmpeg'
    # an app opened from the Finder doesn't get the shell's PATH, so look where Homebrew puts it
    for p in ('/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg'):
        if os.path.exists(p):
            return p
    return 'ffmpeg'


def _venv_python(folder):
    """The python inside a venv or portable install: python.exe on Windows, bin/python3 on a Mac."""
    return os.path.join(folder, 'python.exe') if os.name == 'nt' else os.path.join(folder, 'bin', 'python3')


TOOLS_FILE = os.path.join(APP_DATA, 'tools.json')
_T = _load(TOOLS_FILE)
FFMPEG = _T.get('ffmpeg') or _find_ffmpeg()
_EXE = '.exe' if os.name == 'nt' else ''
FFPROBE = os.path.join(os.path.dirname(FFMPEG), 'ffprobe' + _EXE) if os.path.dirname(FFMPEG) else 'ffprobe'
MODELS = _T.get('models') or os.path.join(APP_DATA, 'models')
WEIGHTS = os.path.join(MODELS, 'weights')
CODEFORMER = _T.get('codeformer') or os.path.join(MODELS, 'CodeFormer')
PYTHON = _T.get('python') or _venv_python(os.path.join(APP_DATA, 'python'))
VS_PYTHON = _T.get('vs_python') or _venv_python(os.path.join(MODELS, 'vs-python'))


def torch_device():
    """Where the AI models run: the NVIDIA card (cuda), the Apple GPU (mps), or the CPU as a last resort.

    TAPEWAKE_DEVICE (or "device" in tools.json) forces one, for testing. Imports torch, so only the engine
    Python calls this.
    """
    import torch
    want = os.environ.get('TAPEWAKE_DEVICE') or _T.get('device')
    if want:
        return torch.device(want)
    if torch.cuda.is_available():
        return torch.device('cuda')
    if getattr(torch.backends, 'mps', None) and torch.backends.mps.is_available():
        return torch.device('mps')
    return torch.device('cpu')


def peak_gpu_gb(dev):
    """GPU memory for the step logs: the peak on an NVIDIA card, what's allocated now on a Mac (Metal keeps no peak)."""
    import torch
    if dev.type == 'cuda':
        return torch.cuda.max_memory_allocated() / 1e9
    if dev.type == 'mps' and hasattr(torch, 'mps') and hasattr(torch.mps, 'driver_allocated_memory'):
        return torch.mps.driver_allocated_memory() / 1e9
    return 0.0

def work_root(source):
    # working files need about 50 GB per hour of tape at the peak, so people may want this on a big drive
    return _T.get('work_root') or os.path.dirname(os.path.abspath(source))


# Defaults = the tested VHS preset (see presets/vhs-tested.json). Jobs can override any of these.
TESTED = {
    'source_fps': '30000/1001', 'field_order': 'tff',  # NTSC
    'audio_channel': 'both',  # or 'left'/'right' if one channel is dead
    'piece_frames': 1000, 'piece_margin': 30, 'edge_frames': 60,
    'qtgmc_sigma': 6.0,
    'faces': True, 'face_detect_step': 2, 'face_min_ed': 12, 'face_full_ed': 22, 'face_min_vis': 0.5,
    'face_clear_ed': 20, 'face_tone': 0.8, 'face_model': 'dicface', 'face_batch': 2,
    'upscale_dn': 0.3, 'sharpen': 1.8, 'grain': 2.0, 'crf': 14,
    'audio_talk': 'AI_LIGHT', 'audio_song': 'NEW',
    # disk space: deinterlace at most this many pieces ahead of the graphics card (about 0.35 GB each)
    'qtgmc_ahead': 8,
    # True keeps every deinterlaced piece and the work folder after the finish (for debugging)
    'keep_work_files': False,
    # False keeps the original sound as it is (one audio track) instead of restoring it
    'restore_audio': True,
    # Tape mode: QTGMC to one frame per field (59.94) at 640x480. Progressive mode (progressive: True) is for
    # videos whose frames are already whole pictures: no deinterlacing, the video keeps its own frame rate
    # (out_fps = source_fps) and gets a square-pixel working size of its own (work_w x work_h, even numbers).
    # Everything after that (faces, upscale 2x, sharpen, grain) is the same in both modes.
    'progressive': False, 'out_fps': '60000/1001', 'work_w': 640, 'work_h': 480,
    'color_matrix': 'bt601', 'color_primaries': 'smpte170m',  # how to read the source's colours
}


def progressive_settings(probe):
    """The settings that switch a job to progressive mode, from probe_video()."""
    return {'progressive': True, 'source_fps': probe['fps'], 'out_fps': probe['fps'],
            # about the same work per piece as tape mode (1000 tape frames become 2000 at 640x480)
            'work_w': probe['work_w'], 'work_h': probe['work_h'], 'piece_frames': 1500,
            'color_matrix': 'bt709' if probe['height'] >= 720 else 'bt601',
            'color_primaries': 'bt709' if probe['height'] >= 720 else 'smpte170m'}


class Job:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        d = _load(self.path)
        if not d:
            raise SystemExit(f'Job file not found or unreadable: {path}')
        self.data = d
        self.id, self.name, self.source = d['id'], d.get('name') or os.path.basename(d['source']), d['source']
        self.folder = d['folder']
        self.output = d.get('output') or os.path.join(self.folder, os.path.splitext(self.name)[0] + ' - Restored.mkv')
        self.samples = d.get('samples') or os.path.join(self.folder, 'samples')
        self.settings = {**TESTED, **d.get('settings', {})}
        f = self.folder
        self.src, self.qtgmc, self.work, self.pieces = (os.path.join(f, n) for n in ('src', 'qtgmc', 'work', 'pieces'))
        self.audio = os.path.join(f, 'audio', 'audio_restored.flac')
        self.log, self.pause_flag = os.path.join(f, 'run.log'), os.path.join(f, 'PAUSED')
        self.thermal_flag = os.path.join(f, 'THERMAL_PAUSE')  # paused because it got too hot, see thermal.py
        self.cleaned_flag = os.path.join(f, 'CLEANED')

    def __getitem__(self, key):
        return self.settings[key]

    def pieces_total(self):
        """How many pieces the source was cut into (0 until the cut is done). Survives the clean-up."""
        try:
            return int(open(os.path.join(self.src, 'done')).read())
        except (OSError, ValueError):
            return 0

    def piece(self, k):
        return os.path.join(self.pieces, f'piece_{k:03d}.mkv')

    def src_piece(self, k):
        return os.path.join(self.src, f'src_{k:03d}.mkv')

    def qt_piece(self, k):
        return os.path.join(self.qtgmc, f'qt_{k:03d}.mkv')

    def channel_filter(self):
        """ffmpeg pan filter for the mono track we restore."""
        return {'left': 'pan=mono|c0=c0', 'right': 'pan=mono|c0=c1'}.get(self['audio_channel'], 'pan=mono|c0=0.5*c0+0.5*c1')


def job_file(job_id):
    return os.path.join(JOBS, f'{job_id}.json')


def all_jobs():
    return [Job(p) for p in sorted(glob.glob(os.path.join(JOBS, '*.json')))]
