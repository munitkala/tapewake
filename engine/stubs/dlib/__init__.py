"""Fake dlib. DicFace imports it at the top of its face helper but we never hit that code path."""


def __getattr__(name):
    raise RuntimeError(f'dlib stub: dlib.{name} is not available in this environment')
