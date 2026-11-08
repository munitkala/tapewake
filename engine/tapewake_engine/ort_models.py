"""ONNX Runtime versions of the upscale step's two models, for graphics cards that aren't NVIDIA.

On Windows they run on any DirectX 12 card (AMD, Intel, and NVIDIA too) through DirectML (pip package
onnxruntime-directml). Everything else in the step stays in PyTorch on the processor: it's light next to the models.
torch-directml was tried first, but it aborts on the upscaler's PReLU and on RAFT, so it isn't used.

Each model is exported to ONNX once per size and cached in models/onnx. Full precision: FP16 is fine for the
upscaler on NVIDIA (see the README) but hasn't been checked on other cards. upscale.py falls back to plain PyTorch if
any of this fails, so a missing package only costs speed.
"""
import copy
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import MODELS  # noqa: E402

ONNX_DIR = os.path.join(MODELS, 'onnx')
GPU_PROVIDERS = ['DmlExecutionProvider']


def gpu_provider():
    """The ONNX Runtime provider that uses the graphics card, or None (not installed, or only the CPU one)."""
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    have = ort.get_available_providers()
    return next((p for p in GPU_PROVIDERS if p in have), None)


class _OrtModel:
    """Exports `net` (once) and runs it with ONNX Runtime on the graphics card. Inputs and outputs are torch tensors."""

    def __init__(self, net, name, inputs, outputs):
        import onnxruntime as ort
        self.provider = gpu_provider()
        if self.provider is None:
            raise RuntimeError('no ONNX Runtime provider for the graphics card (pip install onnxruntime-directml)')
        self.shapes = [tuple(x.shape) for x in inputs]
        self.names = [f'x{i}' for i in range(len(inputs))]
        os.makedirs(ONNX_DIR, exist_ok=True)
        path = os.path.join(ONNX_DIR, name + '.onnx')
        if not os.path.exists(path):
            # a full-precision CPU copy, so it doesn't matter where (or in what precision) the torch model lives
            cpu_net = copy.deepcopy(net).float().cpu().eval()
            with torch.no_grad():
                torch.onnx.export(cpu_net, tuple(x.float().cpu() for x in inputs), path + '.tmp', input_names=self.names,
                                  output_names=outputs, opset_version=17)
            os.replace(path + '.tmp', path)
        opts = ort.SessionOptions()
        # DirectML needs both of these off (see the onnxruntime DirectML docs)
        opts.enable_mem_pattern = False
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.sess = ort.InferenceSession(path, opts, providers=[self.provider, 'CPUExecutionProvider'])
        if self.sess.get_providers()[0] != self.provider:
            raise RuntimeError(f'{self.provider} did not load (graphics driver too old?)')

    def run(self, *xs):
        for x, shape in zip(xs, self.shapes):
            if tuple(x.shape) != shape:
                raise ValueError(f'model exported for {shape}, got {tuple(x.shape)}')
        y = self.sess.run(None, {n: x.detach().float().cpu().numpy() for n, x in zip(self.names, xs)})[0]
        return torch.from_numpy(np.ascontiguousarray(y)).to(xs[0].device)


class OrtUpscaler(_OrtModel):
    def __init__(self, net, dn, h=480, w=640):
        super().__init__(net, f'upscaler_dn{dn:g}_{w}x{h}', [torch.zeros(1, 3, h, w)], ['y'])

    def __call__(self, x):
        return self.run(x)


class _LastFlow(torch.nn.Module):
    """RAFT returns one flow per refinement step; only the last is used, so only that one is exported."""

    def __init__(self, raft):
        super().__init__()
        self.raft = raft

    def forward(self, a, b):
        return self.raft(a, b)[-1]


class OrtFlow(_OrtModel):
    """RAFT optical flow. Called like the torchvision model: returns a list whose last entry is the flow."""

    def __init__(self, raft, size, h=480, w=640):
        x = torch.zeros(1, 3, h, w)
        super().__init__(_LastFlow(raft), f'raft_{size}_{w}x{h}', [x, x], ['flow'])

    def __call__(self, a, b):
        return [self.run(a, b)]
