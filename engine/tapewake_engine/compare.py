"""Side-by-side before/after video of the first N finished pieces.

    python compare.py <job.json> --pieces N [--name NAME]

Original on the left (bobbed in tape mode, then scaled up to match), restored on the right. Encoded with NVENC
HEVC so it's quick, saved to the samples folder as "<name> - Before and after.mp4".
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import FFMPEG, FFPROBE, MODELS, Job  # noqa: E402

# HEVC on the graphics hardware: NVENC on the NVIDIA card, VideoToolbox on a Mac (-q:v 70 of 100: high quality)
ENCODER = (['-c:v', 'hevc_videotoolbox', '-q:v', '70'] if sys.platform == 'darwin' else
           ['-c:v', 'hevc_nvenc', '-preset', 'p7', '-tune', 'hq', '-rc', 'vbr', '-cq', '18', '-b:v', '0',
            '-maxrate', '60M', '-bufsize', '120M'])
FONT = os.path.join(MODELS, 'fonts', 'Poppins-SemiBold.ttf').replace('\\', '/').replace(':', '\\:')


def frames(path):
    out = subprocess.run([FFPROBE, '-v', 'error', '-count_packets', '-select_streams', 'v:0', '-show_entries',
                          'stream=nb_read_packets', '-of', 'csv=p=0', path], capture_output=True, text=True).stdout
    return int(out.strip())


def label(text):
    return (f"drawtext=fontfile='{FONT}':text='{text}':fontsize=34:fontcolor=white:x=28:y=24:"
            "box=1:boxcolor=black@0.45:boxborderw=12")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('job')
    ap.add_argument('--pieces', type=int, required=True)
    ap.add_argument('--name')
    a = ap.parse_args()
    job = Job(a.job)
    os.makedirs(job.samples, exist_ok=True)
    out = os.path.join(job.samples, f'{a.name or os.path.splitext(job.name)[0]} - Before and after.mp4')
    lst = os.path.join(job.folder, 'compare_pieces.txt')
    with open(lst, 'w', encoding='utf-8') as f:
        f.writelines(f"file '{job.piece(k)}'\n" for k in range(a.pieces))
    want = sum(frames(job.piece(k)) for k in range(a.pieces))
    num, den = (int(x) for x in job['out_fps'].split('/'))
    dur = want * den / num
    print(f'{a.pieces} pieces, {want} frames, {dur / 60:.1f} min', flush=True)
    parity = job['field_order']
    bob = '' if job['progressive'] else f'bwdif=mode=send_field:parity={parity}:deint=all,'  # tape: one per field
    w, h, tb = 2 * job['work_w'], 2 * job['work_h'], f'{den}/{num}'
    # match prepare.py's timeline so the two sides stay in sync
    fc = (f"[0:v]fps={job['source_fps']}:start_time=0,{bob}"
          f"scale={w}:{h}:flags=bicubic,setsar=1,settb={tb},setpts=N,{label('ORIGINAL')}[b];"
          f"[1:v]setsar=1,settb={tb},setpts=N,{label('RESTORED')}[r];"
          f"[b][r]hstack=inputs=2,format=yuv420p[v]")
    part = out.replace('.mp4', '.part.mp4')
    # restored audio if we have it, otherwise the original
    sound = ['-i', job.audio] if os.path.exists(job.audio) else []
    subprocess.run([FFMPEG, '-v', 'error', '-stats', '-nostdin', '-y', '-i', job.source, '-f', 'concat', '-safe', '0',
                    '-i', lst, *sound, '-filter_complex', fc, '-map', '[v]', '-map', '2:a' if sound else '0:a:0?', '-frames:v',
                    str(want), '-t', f'{dur:.3f}', *ENCODER, '-tag:v', 'hvc1', '-r', job['out_fps'], '-c:a', 'aac', '-b:a', '256k', '-movflags', '+faststart', part], check=True)
    got = frames(part)
    if abs(got - want) > 2:
        print(f'CHECK FAILED: {got} of {want} frames (kept {part})')
        sys.exit(1)
    os.replace(part, out)
    print(f'OK: {out} ({os.path.getsize(out) / 1e9:.2f} GB, {got} frames)')


if __name__ == '__main__':
    main()
