"""Paths to the tools, plus the Job class.

tools.json (in the app data folder) says where things are installed:

    ffmpeg, models, codeformer, python, vs_python   and optionally work_root

Anything missing falls back to the defaults below.

A job is jobs/<id>.json with id, name, source, folder, output, samples (optional) and
settings (overrides for TESTED). Inside the work folder:

    src/src_NNN.mkv      lossless interlaced pieces
    qtgmc/qt_NNN.mkv     deinterlaced
    work/pNNN/           piece in progress
    pieces/piece_NNN.mkv finished
    audio/, run.log, PAUSED

Stdlib only - the VapourSynth Python imports this too.
"""
import glob
import json
import os
import shutil
import subprocess

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

APP_DATA = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'com.tapewake.app')
JOBS = os.path.join(APP_DATA, 'jobs')
STUBS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'stubs')  # engine/stubs


def _load(path):
    try:
        with open(path, encoding='utf-8-sig') as f:  # -sig: Notepad likes to add a BOM
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _find_ffmpeg():
    found = shutil.which('ffmpeg')
    if found:
        return found
    winget = glob.glob(os.path.join(os.environ.get('LOCALAPPDATA', ''), r'Microsoft\WinGet\Packages\*FFmpeg*\*\bin\ffmpeg.exe'))
    return sorted(winget)[-1] if winget else 'ffmpeg'


TOOLS_FILE = os.path.join(APP_DATA, 'tools.json')
_T = _load(TOOLS_FILE)
FFMPEG = _T.get('ffmpeg') or _find_ffmpeg()
FFPROBE = os.path.join(os.path.dirname(FFMPEG), 'ffprobe.exe') if os.path.dirname(FFMPEG) else 'ffprobe'
MODELS = _T.get('models') or os.path.join(APP_DATA, 'models')
WEIGHTS = os.path.join(MODELS, 'weights')
CODEFORMER = _T.get('codeformer') or os.path.join(MODELS, 'CodeFormer')
PYTHON = _T.get('python') or os.path.join(APP_DATA, 'python', 'python.exe')
VS_PYTHON = _T.get('vs_python') or os.path.join(MODELS, 'vs-python', 'python.exe')

def work_root(source):
    # a 2h tape needs ~150 GB of temp files, so people may want this on a big drive
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
}


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

    def __getitem__(self, key):
        return self.settings[key]

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
