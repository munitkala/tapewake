"""Optional TensorRT version of the upscaler (about 2x faster).

The engine gets built once and cached in models/trt-engines. TensorRT itself is loaded from
models/trt-pkgs so it doesn't have to be installed into the main env. upscale.py falls back to
plain torch if any of this fails.
"""
import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import MODELS  # noqa: E402

sys.path.insert(0, os.path.join(MODELS, 'trt-pkgs'))
ENGINES = os.path.join(MODELS, 'trt-engines')


class TRTUpscaler:
    def __init__(self, net, dn, h=480, w=640):
        import tensorrt as trt
        self.trt, self.shape = trt, (1, 3, h, w)
        os.makedirs(ENGINES, exist_ok=True)
        path = os.path.join(ENGINES, f'upscaler_dn{dn:g}_{w}x{h}_trt{trt.__version__}.engine')
        logger = trt.Logger(trt.Logger.WARNING)
        if not os.path.exists(path):
            self._build(net, path, logger)
        with open(path, 'rb') as f:
            self.engine = trt.Runtime(logger).deserialize_cuda_engine(f.read())
        self.ctx = self.engine.create_execution_context()
        self.out = torch.empty((1, 3, 4 * h, 4 * w), dtype=torch.float16, device='cuda')
        self.names = [self.engine.get_tensor_name(i) for i in range(self.engine.num_io_tensors)]

    def _build(self, net, path, logger):
        trt = self.trt
        onnx_path = path[:-7] + '.onnx'
        torch.onnx.export(net, torch.zeros(self.shape, dtype=torch.float16, device='cuda'), onnx_path,
                          input_names=['x'], output_names=['y'], opset_version=17)
        builder = trt.Builder(logger)
        network = builder.create_network(0)
        parser = trt.OnnxParser(network, logger)
        with open(onnx_path, 'rb') as f:
            if not parser.parse(f.read()):
                raise RuntimeError(parser.get_error(0))
        cfg = builder.create_builder_config()
        cfg.set_flag(trt.BuilderFlag.FP16)
        cfg.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 1 << 30)  # 1 GB, leave room for everything else
        blob = builder.build_serialized_network(network, cfg)
        if blob is None:
            raise RuntimeError('TensorRT build failed')
        with open(path + '.tmp', 'wb') as f:
            f.write(blob)
        os.replace(path + '.tmp', path)

    def __call__(self, x):
        if tuple(x.shape) != self.shape:
            raise ValueError(f'engine built for {self.shape}, got {tuple(x.shape)}')
        x = x.contiguous()
        self.ctx.set_tensor_address(self.names[0], x.data_ptr())
        self.ctx.set_tensor_address(self.names[1], self.out.data_ptr())
        self.ctx.execute_async_v3(torch.cuda.current_stream().cuda_stream)
        return self.out
