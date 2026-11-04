"""Run a face model over every track's crops.

Each model repo comes with its own copy of basicsr, so only one model can be loaded per process.

    codeformer  per frame, w=1.0
    keep        recurrent (Kalman + flow); run in overlapping chunks that get crossfaded
    dicface     5-frame window around each frame (default)
"""
import argparse
import glob
import os
import pickle
import sys
import time

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CODEFORMER, MODELS, WEIGHTS, Progress  # noqa: E402
from settings import STUBS, peak_gpu_gb, torch_device  # noqa: E402

REPOS = {'codeformer': CODEFORMER, 'keep': os.path.join(MODELS, 'KEEP'), 'dicface': os.path.join(MODELS, 'DicFace')}
dev = torch_device()


def to_tensor(crops):
    """(T,512,512,3) BGR uint8 -> (T,3,512,512) RGB float in [-1, 1]."""
    x = torch.from_numpy(crops[..., ::-1].copy()).permute(0, 3, 1, 2).float() / 255
    return x * 2 - 1


def to_uint8(y):
    """(T,3,512,512) RGB in [-1, 1] -> (T,512,512,3) BGR uint8."""
    y = ((y.clamp(-1, 1) + 1) / 2 * 255 + 0.5).byte().permute(0, 2, 3, 1).cpu().numpy()
    return y[..., ::-1].copy()


def load(model):
    sys.path.insert(0, REPOS[model])
    sys.path.insert(1, STUBS)  # dummy dlib/deepspeed, training-only imports
    from basicsr.utils.registry import ARCH_REGISTRY
    if model == 'codeformer':
        net = ARCH_REGISTRY.get('CodeFormer')(dim_embd=512, codebook_size=1024, n_head=8, n_layers=9,
                                              connect_list=['32', '64', '128', '256'])
        path = os.path.join(CODEFORMER, 'weights', 'CodeFormer', 'codeformer.pth')
    elif model == 'keep':
        net = ARCH_REGISTRY.get('KEEP')(img_size=512, emb_dim=256, dim_embd=512, n_head=8, n_layers=9,
                                        codebook_size=1024, cft_list=['16', '32', '64'], kalman_attn_head_dim=48,
                                        num_uncertainty_layers=3, cfa_list=['16', '32'], cfa_nhead=4, cfa_dim=256,
                                        cond=1)
        path = os.path.join(WEIGHTS, 'KEEP', 'KEEP-b76feb75.pth')
    else:
        net = ARCH_REGISTRY.get('TemporalCodeFormerDirDistMultiScale')(
            dim_embed=512, n_head=8, n_layers=9, codebook_size=1024, connect_list=['32', '64', '128', '256'],
            frame_length=5)
        path = os.path.join(WEIGHTS, 'DicFace', 'bfr_100k.pth')
    net.load_state_dict(torch.load(path, map_location='cpu', weights_only=True)['params_ema'])
    return net.eval().to(dev)


def run_codeformer(net, x, batch=4):
    out = []
    for i in range(0, len(x), batch):
        out.append(net(x[i:i + batch].to(dev), w=1.0, adain=True)[0].cpu())
    return torch.cat(out)


def run_keep(net, x, run=20, overlap=6):
    """KEEP over chunks of `run` frames. Chunks overlap and get crossfaded so the resets don't show."""
    t = len(x)
    if t == 1:
        return net(x[[0, 0]].unsqueeze(0).to(dev), need_upscale=False)[0, :1].cpu()
    acc = torch.zeros_like(x)
    wsum = torch.zeros(t)
    start = 0
    while True:
        end = min(start + run, t)
        s = max(0, end - run)
        y = net(x[s:end].unsqueeze(0).to(dev), need_upscale=False)[0].cpu()
        w = torch.ones(end - s)
        if s > 0:
            ramp = min(overlap, end - s)
            w[:ramp] = torch.linspace(1 / (ramp + 1), ramp / (ramp + 1), ramp)
        acc[s:end] += y * w.view(-1, 1, 1, 1)
        wsum[s:end] += w
        if end == t:
            break
        start = end - overlap
    return acc / wsum.view(-1, 1, 1, 1)


def run_dicface(net, x, batch=2, on_step=None):
    t = len(x)
    idx = np.clip(np.arange(t)[:, None] + np.arange(-2, 3)[None, :], 0, t - 1)  # indices of the 5-frame windows
    out = []
    for i in range(0, t, batch):
        win = x[idx[i:i + batch].reshape(-1)].to(dev)
        res = net(win, w=1.0)[0]
        out.append(res.view(-1, 5, *res.shape[1:])[:, 2].cpu())
        if on_step:
            on_step(min(i + batch, t) / t)
    return torch.cat(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('faces_dir')
    ap.add_argument('model', choices=list(REPOS))
    # fp16 makes KEEP output pure black (no NaNs, so it's easy to miss) - keep it fp32
    ap.add_argument('--precision', choices=['half', 'full'], default='full')
    ap.add_argument('--half-rate', action='store_true',
                    help='restore every other frame (59.94 -> 29.97 rate); in-between frames = average of neighbours')
    ap.add_argument('--batch', type=int, default=2)
    a = ap.parse_args()
    out_dir = os.path.join(a.faces_dir, f'restored_{a.model}')
    os.makedirs(out_dir, exist_ok=True)
    torch.backends.cudnn.benchmark = True
    net = load(a.model)
    paths = sorted(glob.glob(os.path.join(a.faces_dir, 'crops', '*.npy')))
    prog = Progress(os.path.dirname(os.path.abspath(a.faces_dir)), 'restoring faces',
                    sum(len(np.load(p, mmap_mode='r')) for p in paths))
    cur = {'base': 0, 'len': 0}  # for progress

    def step(frac):
        prog.update(cur['base'] + int(frac * cur['len']))
    base = {'codeformer': run_codeformer, 'keep': run_keep, 'dicface': run_dicface}[a.model]
    if a.model == 'dicface':
        run_one = lambda n, x: base(n, x, batch=a.batch, on_step=step)  # noqa: E731
    elif a.model == 'codeformer':
        run_one = lambda n, x: base(n, x, batch=a.batch)  # noqa: E731
    else:
        run_one = base

    def run(n, x):
        if not a.half_rate or len(x) < 3:
            return run_one(n, x)
        even = run_one(n, x[::2])
        y = torch.empty_like(x)
        y[::2] = even
        odd = y[1::2]
        nxt = even[1:len(odd) + 1]
        odd[:len(nxt)] = (even[:len(nxt)] + nxt) / 2
        odd[len(nxt):] = even[len(nxt):len(odd)]
        return y
    total, t0, done_frames = 0, time.time(), 0
    with torch.no_grad():
        for path in paths:
            out_path = os.path.join(out_dir, os.path.basename(path))
            crops = np.load(path, mmap_mode='r')
            if os.path.exists(out_path) and np.load(out_path, mmap_mode='r').shape == crops.shape:
                print(f'  {os.path.basename(path)}: already restored, skipped', flush=True)
                done_frames += len(crops)
                continue
            ts = time.time()
            # do long tracks in chunks (with some context on each side) into a memmap -
            # one big track all at once used ~14 GB of RAM
            tmp = out_path + '.tmp.npy'
            res = np.lib.format.open_memmap(tmp, mode='w+', dtype=np.uint8, shape=crops.shape)
            n_all = len(crops)
            ch, pad = (len(crops) if a.model == 'keep' else 240), 8
            for s in range(0, n_all, ch):
                e = min(n_all, s + ch)
                c0, c1 = max(0, s - pad), min(n_all, e + pad)
                cur['base'], cur['len'] = done_frames + s, e - s
                y = run(net, to_tensor(np.asarray(crops[c0:c1])))
                res[s:e] = to_uint8(y[s - c0:s - c0 + (e - s)])
            res.flush()
            del res
            os.replace(tmp, out_path)  # write to .tmp first so a killed run can't leave half a file
            total += len(crops)
            done_frames += len(crops)
            prog.update(done_frames, force=True)
            print(f'  {os.path.basename(path)}: {len(crops)} frames, {len(crops) / (time.time() - ts):.2f} faces/s',
                  flush=True)
    prog.finish()
    print(f'{a.model}: {total} face-frames in {time.time() - t0:.0f}s ({total / (time.time() - t0):.2f} faces/s), '
          f'GPU memory {peak_gpu_gb(dev):.1f} GB', flush=True)


if __name__ == '__main__':
    main()
