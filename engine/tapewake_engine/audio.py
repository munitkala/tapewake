"""Restores the whole audio track of a job:  python audio.py <job.json>

Speech and music can be cleaned differently (audio_talk / audio_song = NEW, AI or AI_LIGHT).
The tested combo is AI_LIGHT for talking and NEW for songs, since NEW touches music the least.
When they differ we crossfade between versions based on how much voice there is at that moment.

Works in 60 s blocks (3 s overlap), then EQ, loudness to -16 LUFS and a true-peak limit.
Output is audio/audio_restored.flac, after checking length, sync and levels.
"""
import json
import os
import subprocess
import sys
import time

import numpy as np
from scipy.ndimage import uniform_filter1d

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio_tools as at  # noqa: E402
from settings import FFMPEG, Job  # noqa: E402

BLOCK, CTX, XF = 60.0, 3.0, 1.0
METHODS = ('NEW', 'AI', 'AI_LIGHT')


def voice_versions(voc, need):
    out = {'NEW': at.clean_dfn(voc)}  # always needed, it's the loudness reference
    for m, lam in (('AI', 0.9), ('AI_LIGHT', 0.5)):
        if m in need:
            v = at.clean_re(voc, lam)
            out[m] = v * (np.sqrt(np.mean(out['NEW'] ** 2)) / (np.sqrt(np.mean(v ** 2)) + 1e-12))
    return out


def main():
    job = Job(sys.argv[1])
    folder = os.path.dirname(job.audio)
    os.makedirs(folder, exist_ok=True)

    def log(msg):
        line = f'[{time.strftime("%H:%M:%S")}] {msg}'
        print(line, flush=True)
        with open(os.path.join(folder, 'audio.log'), 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    talk, song = job['audio_talk'], job['audio_song']
    assert talk in METHODS and song in METHODS, (talk, song)
    log(f'=== audio restoration: talking -> {talk}, songs -> {song}')
    t_start = time.time()

    tape = os.path.join(folder, 'source_f32.raw')
    if not os.path.exists(tape):  # decode once (+ rumble filter and declick)
        subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', '-i', job.source, '-vn', '-af',
                        f'{job.channel_filter()},highpass=f=40,adeclick', '-f', 'f32le', '-ar', str(at.RATE), tape],
                       check=True)
    x_all = np.memmap(tape, np.float32, 'r')
    total = len(x_all)
    acc = np.zeros(total, np.float32)  # float32, 2h at 48k is ~350M samples
    wsum = np.zeros(total, np.float32)
    blocks = int(np.ceil(total / (BLOCK * at.RATE)))
    for b in range(blocks):
        s0 = int(b * BLOCK * at.RATE)
        s1 = min(total, int((b + 1) * BLOCK * at.RATE))
        c0, c1 = max(0, s0 - int(CTX * at.RATE)), min(total, s1 + int(CTX * at.RATE))
        x = at.dehum(np.asarray(x_all[c0:c1], np.float64))
        voc, bg = at.separate(x)
        bg_clean = at.clean_background(bg)
        vv = voice_versions(voc, {talk, song})
        if talk == song:
            voice = vv[talk]
        else:  # how much of this is speech, smoothed over 2 s
            win = int(2 * at.RATE)  # cumsum trick - np.convolve with a 96k kernel took ~15 min per block
            pv = uniform_filter1d(voc ** 2, win, mode='nearest')
            px = uniform_filter1d(x ** 2, win, mode='nearest') + 1e-12
            w_talk = np.clip((pv / px - 0.35) / 0.4, 0, 1)
            voice = w_talk * vv[talk] + (1 - w_talk) * vv[song]
        y = voice * 10 ** (1 / 20) + bg_clean
        # overlap-add with the neighbouring blocks
        k0, k1 = max(c0, s0 - int(XF / 2 * at.RATE)), min(c1, s1 + int(XF / 2 * at.RATE))
        w = np.ones(k1 - k0)
        ramp = int(XF * at.RATE)
        if k0 > 0:
            w[:ramp] = np.linspace(0, 1, ramp)
        if k1 < total:
            w[-ramp:] = np.minimum(w[-ramp:], np.linspace(1, 0, ramp))
        acc[k0:k1] += y[k0 - c0:k1 - c0] * w
        wsum[k0:k1] += w
        el = time.time() - t_start  # every block: the app's progress screen reads these lines
        log(f'block {b + 1}/{blocks} done; {el / 60:.0f} min so far, about {el / (b + 1) * (blocks - b - 1) / 60:.0f} min left')
    y = acc / np.maximum(wsum, 1e-9)
    del acc, wsum

    # EQ/compression, then 2-pass loudnorm to -16 LUFS, -1.5 dBTP
    eq = at.ffmpeg_f32(y, at.EQ)
    meas = subprocess.run([FFMPEG, '-v', 'info', '-nostdin', '-f', 'f32le', '-ar', str(at.RATE), '-ac', '1', '-i', '-',
                           '-af', 'pan=stereo|c0=c0|c1=c0,loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json',
                           '-f', 'null', '-'], input=eq.astype(np.float32).tobytes(), capture_output=True).stderr
    js = meas.decode(errors='ignore')
    m = json.loads(js[js.rindex('{'):js.rindex('}') + 1])
    ln = (f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    part = job.audio.replace('.flac', '.part.flac')
    subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-y', '-f', 'f32le', '-ar', str(at.RATE), '-ac', '1', '-i', '-',
                    '-af', f'pan=stereo|c0=c0|c1=c0,{ln},aresample=48000', '-c:a', 'flac', part],
                   input=eq.astype(np.float32).tobytes(), check=True)

    # sanity checks before we keep it
    del y, eq
    raw = subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-i', part, '-af', 'pan=mono|c0=c0', '-f', 'f32le',
                          '-ar', str(at.RATE), '-'], capture_output=True, check=True).stdout
    z = np.frombuffer(raw, np.float32)
    problems = []
    if abs(len(z) - total) > at.RATE // 100:
        problems.append(f'length {len(z) / at.RATE:.2f}s vs source {total / at.RATE:.2f}s')
    span = total / at.RATE
    for t in np.linspace(min(120, span * 0.1), max(0, span - min(120, span * 0.1) - 20), 10):
        s0, s1 = int(t * at.RATE), int((t + 20) * at.RATE)
        ms, r = at.env_offset_ms(at.envelope(z[s0:s1].astype(np.float64)),
                                 at.envelope(np.asarray(x_all[s0:s1], np.float64)))
        if abs(ms) > 10:
            problems.append(f'sync {ms:+.0f} ms at {t / 60:.1f} min (match {r:.2f})')
        log(f'sync check at {t / 60:6.1f} min: {ms:+.0f} ms (match {r:.2f})')
    lufs = at.loudness(part)
    peak = float(np.max(np.abs(z)))
    if abs(lufs + 16) > 0.7:
        problems.append(f'loudness {lufs:.1f} LUFS')
    if peak > 0.95:
        problems.append(f'peak {peak:.2f}')
    if problems:
        log('CHECK FAILED: ' + '; '.join(problems) + f' (kept as {part})')
        sys.exit(1)
    os.replace(part, job.audio)
    log(f'CHECK OK: {len(z) / at.RATE / 60:.1f} min, {lufs:.1f} LUFS, peak {20 * np.log10(peak):.1f} dBFS, in sync. '
        f'Saved {job.audio} ({(time.time() - t_start) / 60:.0f} min)')


if __name__ == '__main__':
    main()
