"""Pauses a run when the computer gets too hot, and resumes it once it has cooled down.

    python thermal.py now                  how hot it is right now (JSON)
    python thermal.py wait <job.json>      started by run.py after a heat pause: resumes the job once it's cool

run.py runs a Guard next to the restoration. If the computer stays too hot for about a minute it pauses the job
like the Pause button does (control.py), but leaves a THERMAL_PAUSE file instead of PAUSED and starts
`thermal.py wait`. That waits until the computer has been cool for a while and resumes the job.

A pause from the user always wins: Pause writes PAUSED, deletes THERMAL_PAUSE and stops the waiting process
(it mentions the job, so control.py finds it), so a job someone paused never resumes by itself.

How hot is "too hot":
  macOS    the thermal pressure macOS itself works with. Under a long restoration a Mac sits at Heavy (macOS
           slows it down a little to keep it cool, which is normal), so only Trapping or worse for a minute
           pauses: macOS can't keep up any more. It resumes once the Mac is back at Moderate or lower.
  Windows  the NVIDIA card's temperature and whether the card is slowing itself down because of heat
           (nvidia-smi). Slowing down for heat, or 90 °C or more, for a minute pauses; it resumes at 75 °C
           or less.
"""
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import Job  # noqa: E402

CHECK_EVERY = 15        # seconds between checks while restoring
HOT_FOR = 60            # this long too hot in a row pauses the run
COOL_CHECK_EVERY = 30   # seconds between checks while waiting to resume
COOL_FOR = 120          # this long cool in a row resumes ...
MIN_PAUSE = 300         # ... but never sooner than this after the pause, so it doesn't flap
GPU_HOT_C, GPU_COOL_C = 90, 75

# macOS thermal pressure levels (com.apple.system.thermalpressurelevel)
MAC_LEVELS = {0: 'Nominal', 1: 'Moderate', 2: 'Heavy', 3: 'Trapping', 4: 'Sleeping'}
MAC_HOT, MAC_COOL = 3, 1  # pause at Trapping or worse, resume at Moderate or lower


def mac_chip_celsius():
    """The hottest of the chip's own temperature sensors ("PMU tdie..."), in °C, or None.

    Read through IOKit's HID event system, which needs no password. Apple doesn't document it, so anything going
    wrong just means no number: the pause itself goes by macOS's thermal pressure, not by this.
    """
    import ctypes
    import ctypes.util
    try:
        cf = ctypes.CDLL(ctypes.util.find_library('CoreFoundation'))
        io = ctypes.CDLL(ctypes.util.find_library('IOKit'))
    except OSError:
        return None
    ptr, utf8, temperature = ctypes.c_void_p, 0x08000100, 15  # kCFStringEncodingUTF8, kIOHIDEventTypeTemperature
    for fn, res, args in (
            (cf.CFStringCreateWithCString, ptr, [ptr, ctypes.c_char_p, ctypes.c_uint32]),
            (cf.CFNumberCreate, ptr, [ptr, ctypes.c_int, ptr]),
            (cf.CFDictionaryCreate, ptr, [ptr, ptr, ptr, ctypes.c_long, ptr, ptr]),
            (cf.CFArrayGetCount, ctypes.c_long, [ptr]),
            (cf.CFArrayGetValueAtIndex, ptr, [ptr, ctypes.c_long]),
            (cf.CFStringGetCString, ctypes.c_bool, [ptr, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]),
            (cf.CFRelease, None, [ptr]),
            (io.IOHIDEventSystemClientCreate, ptr, [ptr]),
            (io.IOHIDEventSystemClientSetMatching, ctypes.c_int, [ptr, ptr]),
            (io.IOHIDEventSystemClientCopyServices, ptr, [ptr]),
            (io.IOHIDServiceClientCopyProperty, ptr, [ptr, ptr]),
            (io.IOHIDServiceClientCopyEvent, ptr, [ptr, ctypes.c_int64, ctypes.c_int32, ctypes.c_int64]),
            (io.IOHIDEventGetFloatValue, ctypes.c_double, [ptr, ctypes.c_int32])):
        fn.restype, fn.argtypes = res, args

    def text(t):
        return cf.CFStringCreateWithCString(None, t.encode(), utf8)

    def number(i):
        return cf.CFNumberCreate(None, 9, ctypes.byref(ctypes.c_int(i)))  # kCFNumberIntType

    # temperature sensors are usage page 0xff00, usage 5
    keys = (ptr * 2)(text('PrimaryUsagePage'), text('PrimaryUsage'))
    values = (ptr * 2)(number(0xff00), number(5))
    callbacks = [ctypes.addressof(ptr.in_dll(cf, name)) for name in
                 ('kCFTypeDictionaryKeyCallBacks', 'kCFTypeDictionaryValueCallBacks')]
    matching = cf.CFDictionaryCreate(None, keys, values, 2, *callbacks)
    client = io.IOHIDEventSystemClientCreate(None)
    if not client:
        return None
    io.IOHIDEventSystemClientSetMatching(client, matching)
    services = io.IOHIDEventSystemClientCopyServices(client)
    product, name, hottest = text('Product'), ctypes.create_string_buffer(64), None
    for i in range(cf.CFArrayGetCount(services) if services else 0):
        service = cf.CFArrayGetValueAtIndex(services, i)
        prop = io.IOHIDServiceClientCopyProperty(service, product)
        if not prop:
            continue
        ok = cf.CFStringGetCString(prop, name, len(name), utf8)
        cf.CFRelease(prop)
        if not ok or not name.value.startswith(b'PMU tdie'):
            continue
        event = io.IOHIDServiceClientCopyEvent(service, temperature, 0, 0)
        if event:
            c = io.IOHIDEventGetFloatValue(event, temperature << 16)
            cf.CFRelease(event)
            if 0 < c < 150:  # ignore sensors that aren't reporting
                hottest = max(hottest or c, c)
    for obj in (services, client, matching, product, *keys, *values):
        if obj:
            cf.CFRelease(obj)
    return round(hottest) if hottest is not None else None


def _mac():
    try:
        out = subprocess.run(['/usr/bin/notifyutil', '-g', 'com.apple.system.thermalpressurelevel'],
                             capture_output=True, text=True, timeout=10).stdout.split()
        level = int(out[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return {'known': False, 'hot': False, 'cool': True, 'label': 'Unknown',
                'detail': "macOS didn't say how hot it is"}
    name = MAC_LEVELS.get(level, f'level {level}')
    note = (' (too hot: macOS can no longer keep it cool)' if level >= MAC_HOT
            else ' (slowed down a little to stay cool, normal under load)' if level == 2 else '')
    c = mac_chip_celsius()
    chip = f' · chip {c} °C' if c is not None else ''
    return {'known': True, 'hot': level >= MAC_HOT, 'cool': level <= MAC_COOL, 'label': name + chip,
            'celsius': c, 'detail': f'Thermal pressure {name}{note}{chip}'}


def _nvidia():
    # the throttle field was renamed in newer drivers, so ask for either
    for field in ('clocks_event_reasons.hw_thermal_slowdown', 'clocks_throttle_reasons.hw_thermal_slowdown'):
        try:
            out = subprocess.run(['nvidia-smi', f'--query-gpu=temperature.gpu,{field}',
                                  '--format=csv,noheader,nounits'],
                                 capture_output=True, text=True, timeout=10, creationflags=0x08000000)
        except (OSError, subprocess.TimeoutExpired):
            break
        if out.returncode == 0 and out.stdout.strip():
            temp, slowdown = (x.strip() for x in out.stdout.splitlines()[0].split(','))
            try:
                c = int(temp)
            except ValueError:
                break
            slowing = slowdown.lower() == 'active'
            return {'known': True, 'hot': slowing or c >= GPU_HOT_C, 'cool': not slowing and c <= GPU_COOL_C,
                    'label': f'{c} °C', 'celsius': c,
                    'detail': f'Graphics card {c} °C' + (', slowing down because of heat' if slowing else '')}
    return {'known': False, 'hot': False, 'cool': True, 'label': 'Unknown', 'detail': "Couldn't read the graphics card"}


def now():
    """How hot the computer is: known, hot (pause), cool (ok to resume), label and detail for the app."""
    return _mac() if sys.platform == 'darwin' else _nvidia()


def heat_pause(job):
    """The heat pause in progress for this job ({'since', 'reason'}), or None."""
    try:
        with open(job.thermal_flag, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Guard(threading.Thread):
    """Runs beside run.py. Calls on_hot(reason) once the computer has been too hot for HOT_FOR seconds."""

    def __init__(self, on_hot):
        super().__init__(daemon=True)
        self.on_hot = on_hot

    def run(self):
        hot_since = None
        while True:
            t = now()
            if t['hot']:
                hot_since = hot_since or time.time()
                if time.time() - hot_since >= HOT_FOR:
                    self.on_hot(t['detail'])
                    return
            else:
                hot_since = None
            time.sleep(CHECK_EVERY)


def wait_and_resume(job):
    """After a heat pause: wait until it's cool, then resume. Gives up quietly if the user paused or resumed."""
    from control import Control, other_job_running
    ctl = Control(job)
    cool_since = None
    while True:
        time.sleep(COOL_CHECK_EVERY)
        pause = heat_pause(job)
        if pause is None or os.path.exists(job.pause_flag) or ctl.runner_running():
            return  # the user paused it, resumed it or deleted it meanwhile
        t = now()
        cool_since = (cool_since or time.time()) if t['cool'] else None
        if cool_since is None or time.time() - cool_since < COOL_FOR or time.time() - pause['since'] < MIN_PAUSE:
            continue
        if other_job_running(job):
            continue  # one restoration at a time; this one waits its turn
        ctl.log(f"cooled down ({t['detail']}): resuming")
        ctl.resume()
        return


if __name__ == '__main__':
    if sys.argv[1] == 'now':
        print(json.dumps(now()))
    elif sys.argv[1] == 'wait':
        wait_and_resume(Job(sys.argv[2]))
