"""Find and track faces in the deinterlaced frames, and save aligned 512x512 crops per track.

False positives get thrown out: tracks that are too short, low detector scores, and anything the
face parser doesn't think is a face (fabric, leaves, someone half hidden). The face positions are
smoothed over time so the crops don't jitter.
"""
import argparse
import os
import pickle
import sys
import time

import cv2
import numpy as np
import torch
from scipy.signal import savgol_filter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CODEFORMER, Progress, frame_total, qtgmc_reader, track_detail  # noqa: E402
from settings import torch_device  # noqa: E402

sys.path.insert(0, CODEFORMER)
from basicsr.utils import img2tensor  # noqa: E402
from facelib.utils.face_restoration_helper import FaceRestoreHelper  # noqa: E402
from torchvision.transforms.functional import normalize  # noqa: E402

DET_SCALE = 2  # upscale 2x before detecting, otherwise small faces get missed
PARSE_BATCH = 4
FACE_CLASSES = list(range(1, 14))  # parser labels that count as face
yy, xx = np.mgrid[0:512, 0:512]
FACE_OVAL = ((xx - 256) / 150.0) ** 2 + ((yy - 285) / 185.0) ** 2 <= 1  # where the face is in the aligned crop


def eye_dist(lm):
    return float(np.linalg.norm(lm[0] - lm[1]))


def yaw_ratio(lm):
    """How turned the face is: 0 = frontal, 0.5 or more = profile."""
    return float(abs(lm[2, 0] - (lm[0, 0] + lm[1, 0]) / 2) / max(eye_dist(lm), 1e-6))


def track(dets, n_frames, max_gap):
    tracks = []
    for f in range(n_frames):
        active = [t for t in tracks if f - t['last'] <= max_gap]
        pairs = []
        for ti, t in enumerate(active):
            ls, lb = t['size'][-1], t['lms'][-1].mean(0)
            for di, (lm, box, score) in enumerate(dets[f]):
                size = max(box[2] - box[0], box[3] - box[1])
                dist = np.linalg.norm(lm.mean(0) - lb)
                gap = f - t['last']
                if dist < (0.5 + 0.15 * (gap - 1)) * max(ls, size) + 3 and 0.6 < size / ls < 1.6:
                    pairs.append((dist / max(ls, size), ti, di))
        used_t, used_d = set(), set()
        for _, ti, di in sorted(pairs):
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            lm, box, score = dets[f][di]
            t = active[ti]
            t['frames'].append(f)
            t['lms'].append(lm)
            t['size'].append(max(box[2] - box[0], box[3] - box[1]))
            t['score'].append(score)
            t['last'] = f
        for di, (lm, box, score) in enumerate(dets[f]):
            if di not in used_d:
                tracks.append({'frames': [f], 'lms': [lm], 'size': [max(box[2] - box[0], box[3] - box[1])],
                               'score': [score], 'last': f})
    return tracks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src', help='02_qtgmc.mkv')
    ap.add_argument('out_dir')
    ap.add_argument('--min-len', type=int, default=15, help='detected frames needed (0.25 s at 59.94)')
    ap.add_argument('--max-gap', type=int, default=8, help='frames a face may go undetected inside a track')
    ap.add_argument('--min-score', type=float, default=0.85)
    ap.add_argument('--min-vis', type=float, default=0.45, help='share of the face oval the parser sees as face')
    ap.add_argument('--min-ed', type=float, default=5.0, help='eye distance in 640x480 pixels')
    ap.add_argument('--smooth', type=int, default=11, help='Savitzky-Golay window (frames) for the face position')
    ap.add_argument('--detect-step', type=int, default=1, help='run the detector on every Nth frame (gaps filled)')
    # Faces that aren't close up (eyes < --clear-ed px apart) need to be clearly visible, otherwise the
    # model doesn't have enough to work with and starts inventing stuff (glasses etc)
    ap.add_argument('--clear-ed', type=float, default=0.0)
    ap.add_argument('--clear-score', type=float, default=0.95)
    ap.add_argument('--clear-vis', type=float, default=0.6)
    ap.add_argument('--clear-yaw', type=float, default=0.25)
    # blurry faces, see common.face_detail (assemble.py fades in between 0.15 and 0.23)
    ap.add_argument('--min-detail', type=float, default=0.14)
    a = ap.parse_args()
    a.min_len = max(5, a.min_len // a.detect_step)
    os.makedirs(os.path.join(a.out_dir, 'crops'), exist_ok=True)
    os.chdir(CODEFORMER)
    dev = torch_device()
    helper = FaceRestoreHelper(DET_SCALE, face_size=512, crop_ratio=(1, 1), det_model='retinaface_resnet50',
                               save_ext='png', use_parse=True, device=dev)
    # retinaface rebuilds its anchors in python on every frame (~60 s per piece!) so cache them
    from facelib.detection.retinaface import retinaface_utils as ru
    cache, slow = {}, ru.PriorBox.forward
    ru.PriorBox.forward = lambda self: cache.setdefault(tuple(self.image_size), slow(self)).clone()

    t0 = time.time()
    frames, dets = [], []
    prog = Progress(os.path.dirname(os.path.abspath(a.out_dir)), 'finding faces', 4 * frame_total(a.src))  # progress: detection is ~1/4 of the work
    for frame in qtgmc_reader(a.src):
        frames.append(frame.copy())
        prog.update(len(frames))
        if (len(frames) - 1) % a.detect_step:
            dets.append([])
            continue
        big = cv2.resize(frame, None, fx=DET_SCALE, fy=DET_SCALE, interpolation=cv2.INTER_LINEAR)
        with torch.no_grad():
            b = helper.face_detector.detect_faces(big, conf_threshold=0.6)
        cur = []
        for row in (b if b is not None else []):
            lm = np.array(row[5:15], np.float64).reshape(5, 2) / DET_SCALE
            if eye_dist(lm) >= 3:
                cur.append((lm, np.array(row[0:4], np.float64) / DET_SCALE, float(row[4])))
        dets.append(cur)
    n = len(frames)
    prog.update(prog.total // 4, force=True)
    print(f'detection: {n} frames, {sum(map(len, dets))} detections, {time.time() - t0:.0f}s', flush=True)

    raw = track(dets, n, a.max_gap)
    kept, dropped = [], {}
    for ti, t in enumerate(raw):
        prog.update(prog.total // 4 + 3 * prog.total // 4 * ti // max(len(raw), 1))
        if len(t['frames']) < a.min_len:
            dropped['too short'] = dropped.get('too short', 0) + 1
            continue
        f0, f1 = t['frames'][0], t['frames'][-1]
        span = np.arange(f0, f1 + 1)
        lms = np.full((len(span), 10), np.nan)
        score = np.full(len(span), np.nan)
        for f, lm, s in zip(t['frames'], t['lms'], t['score']):
            lms[f - f0] = lm.reshape(10)
            score[f - f0] = s
        for c in range(10):  # fill short gaps
            ok = ~np.isnan(lms[:, c])
            lms[:, c] = np.interp(span, span[ok], lms[ok, c])
        win = min(a.smooth, len(span) - (1 - len(span) % 2))
        if win >= 5:
            lms = savgol_filter(lms, win, 2, axis=0)
        lms = lms.reshape(-1, 5, 2)
        # these are dropped anyway, don't bother cropping/parsing them
        if float(np.nanmedian(score)) < a.min_score or float(np.median([eye_dist(m) for m in lms])) < a.min_ed:
            r = 'low confidence' if float(np.nanmedian(score)) < a.min_score else 'too small'
            dropped[r] = dropped.get(r, 0) + 1
            continue
        crops, affines = [], []
        for i, f in enumerate(span):
            helper.clean_all()
            helper.input_img = frames[f]
            helper.all_landmarks_5 = [lms[i]]
            helper.align_warp_face()
            crops.append(helper.cropped_faces[0])
            affines.append(helper.affine_matrices[0])
        # how much of each face is actually visible - parse every 3rd crop, batches of 4
        # (16 ran out of memory on an 8 GB card)
        vis = np.full(len(span), np.nan)
        idx = list(range(0, len(span), 3))
        for b in range(0, len(idx), PARSE_BATCH):
            xs = []
            for i in idx[b:b + PARSE_BATCH]:
                x = img2tensor(crops[i].astype('float32') / 255., bgr2rgb=True, float32=True)
                normalize(x, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=True)
                xs.append(x)
            with torch.no_grad():
                cls = helper.face_parse(torch.stack(xs).to(dev))[0].argmax(dim=1).cpu().numpy()
            for i, c in zip(idx[b:b + PARSE_BATCH], cls):
                vis[i] = float(np.isin(c[FACE_OVAL], FACE_CLASSES).mean())
        ok = ~np.isnan(vis)
        vis = np.interp(np.arange(len(vis)), np.arange(len(vis))[ok], vis[ok])
        ed = np.array([eye_dist(m) for m in lms])
        yaw = np.array([yaw_ratio(m) for m in lms])
        med = {'score': float(np.nanmedian(score)), 'vis': float(np.median(vis)), 'ed': float(np.median(ed)),
               'yaw': float(np.median(yaw)), 'detail': float(np.median(track_detail(crops, ed)))}
        unclear = med['ed'] < a.clear_ed and (med['score'] < a.clear_score or med['vis'] < a.clear_vis or
                                              med['yaw'] > a.clear_yaw)
        reason = ('low confidence' if med['score'] < a.min_score else 'not a visible face' if med['vis'] < a.min_vis
                  else 'too small' if med['ed'] < a.min_ed else 'medium distance, not clear enough' if unclear
                  else 'out of focus' if med['detail'] < a.min_detail else None)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            print(f'  dropped track frames {f0}-{f1}: {reason} {med}', flush=True)
            continue
        tid = len(kept)
        np.save(os.path.join(a.out_dir, 'crops', f'{tid:03d}.npy'), np.stack(crops))
        kept.append({'id': tid, 'frames': span, 'lms': lms, 'affine': np.stack(affines), 'ed': ed, 'yaw': yaw,
                     'vis': vis, 'score': score, **{f'median_{k}': v for k, v in med.items()}})
        print(f'  track {tid:03d}: frames {f0}-{f1} ({len(span)}), eye dist {med["ed"]:.1f}px, '
              f'score {med["score"]:.2f}, visible {med["vis"]:.2f}, yaw {np.median(yaw):.2f}', flush=True)
    with open(os.path.join(a.out_dir, 'tracks.pkl'), 'wb') as fh:
        pickle.dump({'n_frames': n, 'tracks': kept}, fh)
    print(f'kept {len(kept)} face tracks; dropped {dropped}; total {time.time() - t0:.0f}s', flush=True)
    prog.finish()


if __name__ == '__main__':
    main()
