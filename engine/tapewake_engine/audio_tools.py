"""Audio helpers used by audio.py.

- dehum: tracks the actual hum frequency, which drifts with tape speed (48-53 Hz isn't unusual)
- separate: Demucs splits off the voices; background = original - voices so nothing gets lost
- clean_dfn / clean_re: voice cleanup (DeepFilterNet3 = NEW, Resemble Enhance = AI / AI_LIGHT)
- clean_background: gentle hiss reduction, max 10 dB
- loudness, envelope, env_offset_ms: level and sync checks

Nothing here adds delay.
"""
import os
import pathlib
import subprocess
import sys
from functools import lru_cache

import numpy as np
import torch
import torchaudio.functional as AF
from scipy.ndimage import uniform_filter
from scipy.signal import istft, stft

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import FFMPEG, STUBS, WEIGHTS, torch_device  # noqa: E402

RATE = 48000
dev = torch_device()
EQ = ('equalizer=f=250:t=q:w=1.0:g=-2,equalizer=f=2800:t=q:w=1.0:g=2.5,highshelf=f=6000:g=1.5,'
      'acompressor=threshold=-24dB:ratio=2:attack=20:release=250:makeup=1.5')
RE_DIR = pathlib.Path(WEIGHTS) / 'resemble-enhance' / 'enhancer_stage2'
sys.path.insert(0, STUBS)  # dummy deepspeed, only imported by training code


def dehum(x, harmonics=8, thresh_db=12.0, persist=5):
    """Remove mains hum even when it drifts.

    Every 0.5 s, look for narrow peaks in the hum bands (k * 44-57 Hz) that stand thresh_db above
    their surroundings and stay there for `persist` of 5 steps, and pull them down. Speech and music
    don't sit on one frequency like that so they're left alone.
    """
    n = RATE // 2
    f, _, z = stft(x, fs=RATE, nperseg=n, noverlap=n * 3 // 4, boundary='even')
    mag = np.abs(z)
    in_band = np.zeros(len(f), bool)
    for k in range(1, harmonics + 1):
        in_band |= (f >= 44 * k) & (f <= 57 * k)
    df = f[1] - f[0]
    near = int(round(16 / df))
    excl = int(round(4 / df))
    floor = np.empty_like(mag)
    for b in np.nonzero(in_band)[0]:
        idx = np.r_[max(0, b - near):max(0, b - excl), b + excl + 1:min(len(f), b + near + 1)]
        floor[b] = np.median(mag[idx], axis=0)
    cand = np.zeros_like(mag, bool)
    cand[in_band] = mag[in_band] > floor[in_band] * 10 ** (thresh_db / 20)
    # include neighbouring bins, the tone drifts
    spread = cand | np.roll(cand, 1, axis=0) | np.roll(cand, -1, axis=0)
    steady = uniform_filter(spread.astype(np.float32), size=(1, 5)) * 5 >= persist
    hit = cand & steady
    z[hit] *= floor[hit] / (mag[hit] + 1e-12)
    return istft(z, fs=RATE, nperseg=n, noverlap=n * 3 // 4, boundary=True)[1][:len(x)]


@lru_cache(None)
def demucs_model():
    from demucs.pretrained import get_model
    return get_model('htdemucs').eval().to(dev)


@lru_cache(None)
def dfn():
    import df.logger
    from df.enhance import init_df
    # DeepFilterNet shells out to git just to log a commit hash - breaks if git isn't installed
    df.logger.get_commit_hash = df.logger.get_branch_name = lambda: None
    return init_df(log_level='ERROR')[:2]


def separate(x):
    """Split mono audio into (voices, rest)."""
    from demucs.apply import apply_model
    m = demucs_model()
    w = AF.resample(torch.tensor(x, dtype=torch.float32), RATE, m.samplerate)
    with torch.no_grad():
        s = apply_model(m, w[None, None].repeat(1, 2, 1).to(dev), shifts=1, split=True, overlap=0.25,
                        progress=False)[0]
    v = AF.resample(s[m.sources.index('vocals')].mean(0).cpu(), m.samplerate, RATE).numpy().astype(np.float64)
    v = np.pad(v, (0, max(0, len(x) - len(v))))[:len(x)]
    return v, x - v


def clean_dfn(v):
    """DeepFilterNet3 ("NEW"). Capped at 18 dB so voices don't sound processed."""
    from df.enhance import enhance
    model, state = dfn()
    return enhance(model, state, torch.tensor(v, dtype=torch.float32)[None], pad=True,
                   atten_lim_db=18)[0].numpy().astype(np.float64)[:len(v)]


def clean_re(v, lambd):
    """Resemble Enhance. lambd=0.9 is "AI", 0.5 is "AI_LIGHT"."""
    from resemble_enhance.enhancer.inference import enhance
    # hparams.yaml contains PosixPaths which can't be created on Windows
    posix, pathlib.PosixPath = pathlib.PosixPath, pathlib.WindowsPath
    try:
        out, sr = enhance(torch.tensor(v, dtype=torch.float32), RATE, dev, nfe=64, solver='midpoint',
                          lambd=lambd, tau=0.5, run_dir=RE_DIR)
    finally:
        pathlib.PosixPath = posix
    out = AF.resample(out.float().cpu(), sr, RATE).numpy().astype(np.float64)
    return np.pad(out, (0, max(0, len(v) - len(out))))[:len(v)]


def clean_background(b, max_reduction_db=10, over=1.5):
    f, _, z = stft(b, fs=RATE, nperseg=2048, noverlap=1536)
    p = np.abs(z) ** 2
    noise = np.percentile(p, 10, axis=1, keepdims=True)  # noise floor per bin
    gain = np.sqrt(np.clip(1 - over * noise / (p + 1e-20), 0, 1))
    gain = np.maximum(uniform_filter(gain, size=(3, 5)), 10 ** (-max_reduction_db / 20))
    return istft(z * gain, fs=RATE, nperseg=2048, noverlap=1536)[1][:len(b)]


def ffmpeg_f32(x, af):
    raw = subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-f', 'f32le', '-ar', str(RATE), '-ac', '1', '-i', '-',
                          '-af', af, '-f', 'f32le', '-ac', '1', '-ar', str(RATE), '-'],
                         input=x.astype(np.float32).tobytes(), capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def loudness(path_or_array):
    """Integrated loudness in LUFS (ffmpeg ebur128)."""
    if isinstance(path_or_array, str):
        cmd, inp = [FFMPEG, '-v', 'info', '-nostdin', '-i', path_or_array], None
    else:  # measure as dual mono since that's what we deliver (+3 dB vs mono)
        cmd = [FFMPEG, '-v', 'info', '-nostdin', '-f', 'f32le', '-ar', str(RATE), '-ac', '1', '-i', '-']
        inp = path_or_array.astype(np.float32).tobytes()
    af = 'ebur128' if inp is None else 'pan=stereo|c0=c0|c1=c0,ebur128'
    err = subprocess.run(cmd + ['-af', af, '-f', 'null', '-'], input=inp, capture_output=True).stderr
    lines = [ln for ln in err.decode(errors='ignore').splitlines() if ln.strip().startswith('I:')]
    return float(lines[-1].split()[1])


def envelope(y):
    """1 ms loudness envelope with slow changes removed. Used for checking sync."""
    x = ffmpeg_f32(y, 'highpass=f=300,lowpass=f=3000')
    x16 = AF.resample(torch.tensor(x, dtype=torch.float32), RATE, 16000).numpy().astype(np.float64)
    e = np.sqrt(np.convolve(x16 ** 2, np.ones(160) / 160, mode='same'))[::16]
    e = np.log(e + 1e-4)
    return e - np.convolve(e, np.ones(301) / 301, mode='same')


def env_offset_ms(a, b, max_ms=150):
    """Offset of a vs b in ms, and how well they match (0-1)."""
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    lags = np.arange(-max_ms, max_ms + 1)
    vals = [np.dot(a[max(0, k):n + min(0, k)], b[max(0, -k):n - max(0, k)]) for k in lags]
    k = int(np.argmax(vals))
    return float(lags[k]), vals[k] / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12)
