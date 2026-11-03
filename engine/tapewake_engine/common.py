"""Helpers shared by the pipeline steps."""
import os
import subprocess
import time

import sys

import numpy as np


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import CODEFORMER, FFMPEG, FFPROBE, MODELS, WEIGHTS  # noqa: E402,F401 - re-exported for the steps

# The working picture every step reads. Tape mode: deinterlaced to 640x480 at 59.94 (one frame per field).
# Progressive mode keeps the video's own frame rate and its own size, which run.py passes on in these variables.
FPS = os.environ.get('TAPEWAKE_FPS', '60000/1001')
W, H = int(os.environ.get('TAPEWAKE_W', 640)), int(os.environ.get('TAPEWAKE_H', 480))
MATRIX = os.environ.get('TAPEWAKE_MATRIX', 'bt601')
PRIMARIES = os.environ.get('TAPEWAKE_PRIMARIES', 'smpte170m')
_num, _den = (int(x) for x in FPS.split('/'))
FPS_F = _num / _den


def reader(path, w, h, vf=None):
    """Yield frames from ffmpeg as BGR uint8 arrays."""
    vf_args = ['-vf', vf] if vf else []
    proc = subprocess.Popen([FFMPEG, '-v', 'error', '-nostdin', '-i', path, '-map', '0:v:0', *vf_args,
                             '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
                            stdout=subprocess.PIPE, bufsize=w * h * 3 * 4)
    size = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def qtgmc_reader(path):
    return reader(path, W, H, f'scale=in_color_matrix={MATRIX}:in_range=tv,format=bgr24')


def lossless_writer(path, w, h):
    """Lossless FFV1 writer for the intermediate files."""
    return subprocess.Popen([FFMPEG, '-v', 'error', '-nostdin', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                             '-s', f'{w}x{h}', '-r', FPS, '-i', '-', '-c:v', 'ffv1', '-level', '3',
                             '-pix_fmt', 'bgr0', path], stdin=subprocess.PIPE)


TEMPLATE_EYE_DIST = 125.92  # eye distance in the 512x512 aligned face
_yy, _xx = np.mgrid[0:512, 0:512]
INNER_FACE = ((_xx - 256) / 120.0) ** 2 + ((_yy - 290) / 140.0) ** 2 <= 1  # roughly eyes/nose/mouth


def face_detail(crop, ed):
    """Rough sharpness score for a source face (crop = aligned 512px crop, ed = eye distance in source px).

    Compares fine detail (1-3 source px) to the overall contrast. Blurry faces come out around 0.15 or
    below - those are the ones where the face model starts making things up, like glasses.
    """
    import cv2
    s = TEMPLATE_EYE_DIST / max(ed, 1e-3)  # crop px per source px
    g = cv2.cvtColor(np.asarray(crop), cv2.COLOR_BGR2GRAY).astype(np.float32)
    b1 = cv2.GaussianBlur(g, (0, 0), 0.7 * s)
    b2 = cv2.GaussianBlur(g, (0, 0), 2.0 * s)
    return float((b1 - b2)[INNER_FACE].std() / (b2[INNER_FACE].std() + 8))


def track_detail(crops, ed, step=3, smooth=31):
    """face_detail() over a whole track, sampled every `step` frames and smoothed (~0.5 s) to avoid flicker."""
    idx = np.arange(0, len(crops), step)
    d = np.interp(np.arange(len(crops)), idx, [face_detail(crops[i], ed[i]) for i in idx])
    if len(d) >= 3:
        k = min(smooth, len(d) - (1 - len(d) % 2))
        d = np.convolve(np.pad(d, k // 2, mode='edge'), np.ones(k) / k, mode='valid')
    return d


def frame_total(path):
    """Approximate frame count at the working frame rate, from the duration. Only used for progress."""
    out = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                         capture_output=True, text=True, creationflags=0x08000000).stdout
    try:
        return round(float(out) * FPS_F)
    except ValueError:
        return 0


class Progress:
    """Writes 'done total stage' to progress.txt (max every 2 s) for the app to show.

    Swallows errors on purpose - progress reporting should never kill a run.
    """

    def __init__(self, folder, stage, total):
        self.path, self.stage, self.total, self.t = os.path.join(folder, 'progress.txt'), stage, total, 0
        self.update(0, force=True)

    def update(self, done, force=False):
        if not force and time.time() - self.t < 2:
            return
        self.t = time.time()
        try:
            with open(self.path + '.tmp', 'w') as f:
                f.write(f'{done} {self.total} {self.stage}')
            os.replace(self.path + '.tmp', self.path)
        except OSError:
            pass

    def finish(self):
        self.update(self.total, force=True)


def delivery_args(crf=14):
    """ffmpeg args for the final H.264 encode (converted + tagged BT.709 for HD players)."""
    return ['-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p', '-c:v', 'libx264',
            '-crf', str(crf), '-preset', 'slow', '-colorspace', 'bt709', '-color_primaries', PRIMARIES,
            '-color_trc', 'bt709', '-color_range', 'tv']
