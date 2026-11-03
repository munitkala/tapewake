"""Paste the restored faces onto the upscaled frames, add grain, trim and encode.

Notes on the face blending:
  - colour/brightness come from the original face, the model only adds detail (no flicker)
  - small faces are left alone, then it fades in with size (--min-ed / --full-ed)
  - turned or partly hidden faces get less, and it's all smoothed over time
  - faces get blurred a bit before shrinking so they aren't sharper than the rest of the frame
"""
import argparse
import os
import pickle
import subprocess
import sys
import time

import cv2
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CODEFORMER, FFMPEG, FPS, H, W, Progress, delivery_args, reader, track_detail  # noqa: E402

sys.path.insert(0, CODEFORMER)
from basicsr.utils import img2tensor  # noqa: E402
from facelib.utils.face_restoration_helper import FaceRestoreHelper  # noqa: E402
from torchvision.transforms.functional import normalize  # noqa: E402

TEMPLATE_EYE_DIST = 125.92  # eye distance in the aligned crop
UP = 2  # working size (640x480 for tape) -> twice that
FACE_PARTS = list(range(1, 14))  # same labels as faces_find
MASK_SIGMA = 16  # mask feathering, in crop px
MASK_BORDER = 24  # mask is 0 this close to the crop edge


def ramp(x, lo, hi):
    t = np.clip((x - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def face_weights(tr, n_frames, args):
    a = ramp(tr['ed'], args.min_ed, args.full_ed) * (1 - ramp(tr['yaw'], 0.25, 0.5)) * ramp(tr['vis'], 0.35, 0.6)
    if args.detail_hi > 0:
        a = a * ramp(track_detail(tr['src'], tr['ed']), args.detail_lo, args.detail_hi)
    f0, f1 = tr['frames'][0], tr['frames'][-1]
    fade = np.ones(len(a))
    k = np.arange(len(a))
    if f0 > 0:
        fade = np.minimum(fade, (k + 1) / (args.fade + 1))
    if f1 < n_frames - 1:
        fade = np.minimum(fade, (len(a) - k) / (args.fade + 1))
    a = a * fade
    if len(a) >= 9:
        a = np.convolve(np.pad(a, 4, mode='edge'), np.ones(9) / 9, mode='valid')
    return a


def parse_mask(helper, face, dev):
    x = img2tensor(face.astype('float32') / 255., bgr2rgb=True, float32=True)
    normalize(x, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=True)
    with torch.no_grad():
        cls = helper.face_parse(x.unsqueeze(0).to(dev))[0].argmax(dim=1).squeeze().cpu().numpy()
    m = cv2.GaussianBlur(np.isin(cls, FACE_PARTS).astype(np.float32), (0, 0), MASK_SIGMA)
    # fade out towards the edges so there's never a hard line
    edge = np.minimum(np.arange(512), np.arange(512)[::-1]).astype(np.float32)
    edge = np.clip(edge / MASK_BORDER, 0, 1)
    return m * np.minimum(edge[:, None], edge[None, :])


def paste(img, face, affine, alpha, pmask):
    """Blend a restored face into img (float32, in place) using its mask * alpha."""
    h_up, w_up = img.shape[:2]
    # crop -> 640x480 -> 1280x960 (scaling around pixel centres)
    to_out = np.array([[UP, 0, (UP - 1) / 2], [0, UP, (UP - 1) / 2], [0, 0, 1]], np.float64)
    inv = (to_out @ np.vstack([cv2.invertAffineTransform(affine), [0, 0, 1]]))[:2]
    corners = inv @ np.array([[0, 0, 1], [511, 0, 1], [0, 511, 1], [511, 511, 1]], np.float64).T
    x0, y0 = max(0, int(np.floor(corners[0].min())) - 2), max(0, int(np.floor(corners[1].min())) - 2)
    x1, y1 = min(w_up, int(np.ceil(corners[0].max())) + 3), min(h_up, int(np.ceil(corners[1].max())) + 3)
    if x1 <= x0 or y1 <= y0:
        return
    inv[:, 2] -= (x0, y0)
    size = (x1 - x0, y1 - y0)
    warped = cv2.warpAffine(face, inv, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    mask = cv2.warpAffine(pmask, inv, size, flags=cv2.INTER_LINEAR, borderValue=0) * alpha
    roi = img[y0:y1, x0:x1]
    roi += mask[:, :, None] * (warped - roi)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('clip_dir')
    ap.add_argument('model', help='face model whose results to paste (codeformer/keep/dicface) or none')
    ap.add_argument('out')
    ap.add_argument('--bg', default='03_bg.mkv')
    ap.add_argument('--grain', type=float, default=2.0, help='grain strength in 8-bit levels (0 = none)')
    ap.add_argument('--min-ed', type=float, default=6.0)
    ap.add_argument('--full-ed', type=float, default=14.0)
    ap.add_argument('--fade', type=int, default=8)
    ap.add_argument('--audio', default='04_audio.flac', help="'none' for video only")
    ap.add_argument('--first', type=int, required=True, help='first frame to keep (after the lead-in)')
    ap.add_argument('--last', type=int, required=True, help='frame after the last one to keep')
    ap.add_argument('--sharpen', type=float, default=0.0, help='luma unsharp amount (tested: 1.8)')
    # blurry faces get less restoration (detail score, see common.face_detail). On the test tape an
    # out of focus face that got invented glasses scored 0.14-0.18, sharp ones 0.20-0.44
    ap.add_argument('--detail-lo', type=float, default=0.15)
    ap.add_argument('--detail-hi', type=float, default=0.23)
    ap.add_argument('--tone', type=float, default=0.5,
                    help='structure coarser than this many source pixels comes from the source face, not the model '
                         '(higher = more faithful, fewer invented details)')
    a = ap.parse_args()
    dev = torch.device('cuda')
    faces_dir = os.path.join(a.clip_dir, 'faces')
    with open(os.path.join(faces_dir, 'tracks.pkl'), 'rb') as fh:
        data = pickle.load(fh)
    n_frames, tracks = data['n_frames'], data['tracks'] if a.model != 'none' else []
    helper = None
    if tracks:
        os.chdir(CODEFORMER)
        helper = FaceRestoreHelper(UP, face_size=512, crop_ratio=(1, 1), det_model='retinaface_resnet50',
                                   save_ext='png', use_parse=True, device=dev)
    active = {}
    for tr in tracks:
        tr['restored'] = np.load(os.path.join(faces_dir, f'restored_{a.model}', f'{tr["id"]:03d}.npy'), mmap_mode='r')
        tr['src'] = np.load(os.path.join(faces_dir, 'crops', f'{tr["id"]:03d}.npy'), mmap_mode='r')
        tr['alpha'] = face_weights(tr, n_frames, a)
        for i, f in enumerate(tr['frames']):
            active.setdefault(int(f), []).append((tr, i))

    first, last = a.first, a.last
    if a.audio == 'none':
        audio_args = ['-map', '0:v']
    else:
        audio_args = ['-i', os.path.join(a.clip_dir, a.audio), '-map', '0:v', '-map', '1:a', '-c:a', 'aac',
                      '-b:a', '256k', '-shortest']
    enc = subprocess.Popen([FFMPEG, '-v', 'error', '-nostdin', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                            '-s', f'{UP * W}x{UP * H}', '-r', FPS, '-i', '-', *audio_args, *delivery_args(), a.out],
                           stdin=subprocess.PIPE)
    rng = np.random.default_rng(1995)
    pasted, t0 = 0, time.time()
    prog = Progress(a.clip_dir, 'assembling', last - first)
    for f, bg in enumerate(reader(os.path.join(a.clip_dir, a.bg), UP * W, UP * H)):
        if f >= last:
            break
        if f < first:
            continue
        img = bg.astype(np.float32)
        for tr, i in active.get(f, []):
            alpha = float(tr['alpha'][i])
            if alpha < 0.02:
                continue
            face = tr['restored'][i].astype(np.float32)
            src = tr['src'][i].astype(np.float32)
            scale = TEMPLATE_EYE_DIST / max(tr['ed'][i], 1e-3)  # crop px per source px
            sig = float(np.clip(a.tone * scale, 3, 12))
            face = face - cv2.GaussianBlur(face, (0, 0), sig) + cv2.GaussianBlur(src, (0, 0), sig)  # keep the original's tones
            shrink = scale / UP
            if shrink > 1:
                face = cv2.GaussianBlur(face, (0, 0), 0.45 * shrink)  # anti-alias
            face = np.clip(face, 0, 255)
            pmask = parse_mask(helper, tr['restored'][i], dev)
            paste(img, face, tr['affine'][i], alpha, pmask)
            pasted += 1
        if a.sharpen > 0:  # sharpen luma before adding grain
            y = img @ np.float32([0.114, 0.587, 0.299])
            img += (a.sharpen * (y - cv2.GaussianBlur(y, (5, 5), 0)))[:, :, None]
        if a.grain > 0:
            g = cv2.GaussianBlur(rng.standard_normal((UP * H, UP * W)).astype(np.float32), (0, 0), 0.7)
            img += (g * (a.grain / 0.4))[:, :, None]  # the blur leaves ~0.4 of the std
        enc.stdin.write(np.clip(img + 0.5, 0, 255).astype(np.uint8).tobytes())
        prog.update(f - first + 1)
    enc.stdin.close()
    rc = enc.wait()
    prog.finish()
    print(f'{os.path.basename(a.out)}: {last - first} frames, {pasted} faces pasted, {time.time() - t0:.0f}s, '
          f'encoder exit {rc}', flush=True)


if __name__ == '__main__':
    main()
