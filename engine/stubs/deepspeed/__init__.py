"""Fake deepspeed so resemble_enhance can be imported on Windows.

Only its training code uses it. If anything actually calls it, it raises.
"""


def _unsupported(*args, **kwargs):
    raise RuntimeError('deepspeed stub: training is not supported in this environment')


class DeepSpeedConfig:
    def __init__(self, *args, **kwargs):
        _unsupported()


init_distributed = _unsupported
