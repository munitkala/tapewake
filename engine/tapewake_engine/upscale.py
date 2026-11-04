"""Upscale the deinterlaced frames to 1280x960 (Real-ESRGAN general x4v3, then down to 2x).

Two things on top of the plain upscale:
  - only the fine detail comes from the model; the low frequencies come from the source. That
    stops the brightness/colour flicker you otherwise get.
  - the detail is blended with the previous frame (aligned with RAFT flow) to calm it down,
    except where the source itself changed.

Writes lossless FFV1.
"""
import argparse
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.models.optical_flow import Raft_Large_Weights, Raft_Small_Weights, raft_large, raft_small

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import H, MODELS, W, Progress, frame_total, lossless_writer, qtgmc_reader  # noqa: E402
from settings import torch_device  # noqa: E402

GEN = os.path.join(MODELS, 'esrgan-weights', 'realesr-general-x4v3.pth')
WDN = os.path.join(MODELS, 'esrgan-weights', 'realesr-general-wdn-x4v3.pth')
dev = torch_device()


class WeightsNet(nn.Module):
    """Builds the network straight from the state dict.

    body.N is a conv if its weight is 4-D, a PReLU if it's 1-D. The output goes through pixel_shuffle
    and gets a nearest-neighbour upscale of the input added on top.
    """

    def __init__(self, params):
        super().__init__()
        layers = []
        for i in range(len({k.split('.')[1] for k in params if k.startswith('body.')})):
            w = params[f'body.{i}.weight']
            if w.dim() == 4:
                layers.append(nn.Conv2d(w.shape[1], w.shape[0], w.shape[2], padding=w.shape[2] // 2))
            else:
                layers.append(nn.PReLU(w.shape[0]))
        self.body = nn.Sequential(*layers)
        self.scale = int(round((layers[-1].out_channels / 3) ** 0.5))
        self.load_state_dict(params)

    def forward(self, x):
        y = F.pixel_shuffle(self.body(x), self.scale)
        return y + F.interpolate(x, scale_factor=self.scale, mode='nearest')


def load_upscaler(dn):
    """Mix the normal and the weak-denoise weights. dn=1 is the normal model, dn=0 the wdn one."""
    a = torch.load(GEN, map_location='cpu', weights_only=True)['params']
    b = torch.load(WDN, map_location='cpu', weights_only=True)['params']
    net = WeightsNet({k: dn * a[k] + (1 - dn) * b[k] for k in a}).eval().to(dev)
    return net.half() if HALF else net  # FP16 is fine for this model (57.7 dB vs FP32), see the README


# FP16 on the NVIDIA card and the Apple GPU; the CPU fallback stays at full precision (FP16 there is slow)
HALF = dev.type in ('cuda', 'mps')


def gauss(x, sigma):
    if sigma <= 0:
        return x
    r = max(1, int(3 * sigma + 0.5))
    k = torch.exp(-0.5 * (torch.arange(-r, r + 1, device=x.device, dtype=x.dtype) / sigma) ** 2)
    k = (k / k.sum()).view(1, 1, 1, -1)
    c = x.shape[1]
    x = F.conv2d(F.pad(x, (r, r, 0, 0), mode='reflect'), k.expand(c, 1, 1, -1), groups=c)
    return F.conv2d(F.pad(x, (0, 0, r, r), mode='reflect'), k.view(1, 1, -1, 1).expand(c, 1, -1, 1), groups=c)


def warp(x, flow):
    """Backward-warp x by flow (in pixels). Also returns which pixels landed inside the image."""
    _, _, h, w = x.shape
    gy, gx = torch.meshgrid(torch.arange(h, device=x.device, dtype=x.dtype),
                            torch.arange(w, device=x.device, dtype=x.dtype), indexing='ij')
    px, py = gx + flow[:, 0], gy + flow[:, 1]
    grid = torch.stack([2 * px / (w - 1) - 1, 2 * py / (h - 1) - 1], dim=-1)
    valid = ((px >= 0) & (px <= w - 1) & (py >= 0) & (py <= h - 1)).unsqueeze(1)
    if x.device.type == 'mps':
        # Metal has no 'border' padding. With align_corners=True, clamping the grid to the image edge gives the
        # same result (anything outside reads the nearest edge pixel).
        return F.grid_sample(x, grid.clamp(-1, 1), mode='bilinear', padding_mode='zeros', align_corners=True), valid
    return F.grid_sample(x, grid, mode='bilinear', padding_mode='border', align_corners=True), valid


def luma(x):
    return (0.299 * x[:, 0:1] + 0.587 * x[:, 1:2] + 0.114 * x[:, 2:3]) * 255


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src', help='02_qtgmc.mkv')
    ap.add_argument('out')
    ap.add_argument('--dn', type=float, default=0.3, help='AI denoise strength (0 = keep texture, 1 = smooth)')
    ap.add_argument('--anchor-sigma', type=float, default=3.0, help='px @1280: tones coarser than this come from source')
    ap.add_argument('--stab-alpha', type=float, default=0.4, help='share of the new frame where motion is tracked well')
    ap.add_argument('--stab-k', type=float, default=10.0, help='source change (8-bit levels) that fully resets to new')
    ap.add_argument('--no-stab', action='store_true')
    ap.add_argument('--no-anchor', action='store_true')
    ap.add_argument('--raft', choices=['small', 'large'], default='large')
    ap.add_argument('--no-trt', action='store_true', help='plain PyTorch upscaler instead of the TensorRT engine')
    a = ap.parse_args()

    torch.backends.cudnn.benchmark = True
    net = load_upscaler(a.dn)
    # TensorRT (NVIDIA only) is ~2x faster, practically the same output (57.7 dB PSNR vs torch)
    if not a.no_trt and dev.type == 'cuda':
        try:
            from trt_upscaler import TRTUpscaler
            net = TRTUpscaler(net, a.dn, h=H, w=W)
            print('upscaler: TensorRT', flush=True)
        except Exception as e:  # noqa: BLE001 - any TensorRT problem: carry on with PyTorch
            print(f'upscaler: PyTorch (TensorRT unavailable: {e!r})', flush=True)
    if a.raft == 'large':
        flow_net = raft_large(weights=Raft_Large_Weights.DEFAULT).eval().to(dev)
    else:
        flow_net = raft_small(weights=Raft_Small_Weights.DEFAULT).eval().to(dev)
    enc = lossless_writer(a.out, 2 * W, 2 * H)
    prev_src = prev_detail = None
    n = cuts = 0
    t0 = time.time()
    prog = Progress(os.path.dirname(os.path.abspath(a.out)), 'upscaling', frame_total(a.src))
    with torch.no_grad():
        for frame in qtgmc_reader(a.src):
            s = torch.from_numpy(frame[:, :, ::-1].copy()).to(dev).permute(2, 0, 1).unsqueeze(0).float() / 255
            up4 = net(s.half() if HALF else s).float().clamp(0, 1)
            ai = F.interpolate(up4, size=(2 * H, 2 * W), mode='bicubic', antialias=True, align_corners=False)
            src2 = F.interpolate(s, size=(2 * H, 2 * W), mode='bicubic', align_corners=False).clamp(0, 1)
            if a.no_anchor:
                base, detail = torch.zeros_like(ai), ai
            else:
                base = gauss(src2, a.anchor_sigma)
                detail = ai - gauss(ai, a.anchor_sigma)
            if not a.no_stab and prev_src is not None:
                flow = flow_net(s * 2 - 1, prev_src * 2 - 1)[-1]  # flow from current to previous
                prev_w, valid = warp(prev_src, flow)
                err = gauss((luma(s) - luma(prev_w)).abs(), 1.0)
                if err.mean() > 20:  # scene cut
                    cuts += 1
                else:
                    alpha = (a.stab_alpha + err / a.stab_k).clamp(0, 1)
                    alpha = torch.where(valid, alpha, torch.ones_like(alpha))
                    alpha2 = F.interpolate(alpha, size=(2 * H, 2 * W), mode='bilinear', align_corners=False)
                    flow2 = F.interpolate(flow, size=(2 * H, 2 * W), mode='bilinear', align_corners=False) * 2
                    prev_detail_w, _ = warp(prev_detail, flow2)
                    detail = alpha2 * detail + (1 - alpha2) * prev_detail_w
            prev_src, prev_detail = s, detail
            out = (base + detail).clamp(0, 1)
            enc.stdin.write((out[0].permute(1, 2, 0).flip(-1) * 255 + 0.5).byte().cpu().numpy().tobytes())
            n += 1
            prog.update(n)
            if n % 120 == 0:
                print(f'  {n} frames, {n / (time.time() - t0):.2f} fps', flush=True)
    enc.stdin.close()
    rc = enc.wait()
    prog.finish()
    print(f'done: {n} frames in {time.time() - t0:.0f}s ({n / (time.time() - t0):.2f} fps), scene cuts {cuts}, '
          f'encoder exit {rc}', flush=True)


if __name__ == '__main__':
    main()
