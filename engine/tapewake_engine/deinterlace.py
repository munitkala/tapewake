"""QTGMC deinterlace (double rate) + resize to 640x480. Output is lossless FFV1 4:4:4.

Light denoise, no sharpening. Run it with the VapourSynth Python:

    python deinterlace.py IN.mkv OUT.mkv [--sigma 6] [--bff]
"""
import argparse
import os
import subprocess
import sys
import time

import vapoursynth as vs
from vsdeinterlace import QTempGaussMC
from vsdenoise import DFTTest
from vsdenoise.mvtools import mvtools as _mvt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import FFMPEG  # noqa: E402

core = vs.core

# Workaround: vsjetpack 2.2.6 always passes fields=... to mvutensils, and mvutensils 10 doesn't
# accept it anymore. None gets dropped by the wrapper, and False was the default anyway.
_mvtools_init = _mvt.MVTools.__init__


def _mvtools_init_no_fields(self, *args, **kwargs):
    _mvtools_init(self, *args, **kwargs)
    if not self.fields:
        self.fields = None


_mvt.MVTools.__init__ = _mvtools_init_no_fields


def build(src, sigma, tff=True):
    clip = core.bs.VideoSource(source=src)
    clip = core.std.SetFieldBased(clip, 2 if tff else 1)
    qt = QTempGaussMC(
        denoise_mode=QTempGaussMC.NoiseProcessMode.DENOISE,  # denoise first, otherwise ESRGAN sharpens the noise
        denoise_tr=2,
        denoise_func=DFTTest(sigma=sigma),  # about the same as old QTGMC's Sigma=1.5
        source_match_mode=QTempGaussMC.SourceMatchMode.BASIC,  # also turns off QTGMC's sharpening
    )
    clip = qt.bob(clip, tff=tff)  # 59.94p
    clip = core.resize.Spline36(clip, 640, 480, format=vs.YUV444P8)  # square pixels
    return core.std.SetFieldBased(clip, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--sigma', type=float, default=6)
    ap.add_argument('--bff', action='store_true', help='bottom field first')
    a = ap.parse_args()
    clip = build(a.src, a.sigma, not a.bff)
    print(f'{clip.num_frames} frames {clip.width}x{clip.height} @ {clip.fps}', flush=True)
    enc = subprocess.Popen([FFMPEG, '-v', 'error', '-nostdin', '-y', '-f', 'yuv4mpegpipe', '-i', '-',
                            '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'yuv444p', a.out], stdin=subprocess.PIPE)
    t0 = time.time()

    def progress(done, total):
        if done % 120 == 0 or done == total:
            print(f'  {done}/{total} frames, {done / max(time.time() - t0, 1e-6):.1f} fps', flush=True)

    clip.output(enc.stdin, y4m=True, progress_update=progress)
    enc.stdin.close()
    sys.exit(enc.wait())


if __name__ == '__main__':
    main()
