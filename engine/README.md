# tapewake engine

This is the restoration pipeline the app runs (`tapewake_engine/`). The settings were tuned on a full
two-hour VHS tape (720x480, 29.97i) on an 8 GB NVIDIA card. They're saved as the preset
**Home-video tape · Tested** in `presets/vhs-tested.json`. Jobs can override them, see `TESTED` in
`tapewake_engine/settings.py`.

Each restoration is a job file in the app's data folder
(`%LOCALAPPDATA%\com.tapewake.app\jobs\<id>.json`, on a Mac
`~/Library/Application Support/com.tapewake.app/jobs/<id>.json`). `tools.json` in the same folder says where FFmpeg,
the models and the two Pythons are installed.

## Pipeline

| Step | Script | What it does |
|---|---|---|
| Prepare | `prepare.py` (VapourSynth Python) | Cuts the source into lossless FFV1 pieces of 1000 frames on a constant timeline, then QTGMC per piece with 30-frame margins |
| Deinterlace | `deinterlace.py` | QTGMC double rate (59.94p), DFTTest sigma 6, no sharpening, Spline36 to 640x480 |
| Find faces | `faces_find.py` | RetinaFace on 2x frames every 2nd frame, tracking, visibility check with the face parser |
| Restore faces | `faces_restore.py` | DicFace (5-frame window) by default, full precision, batch 2 |
| Upscale | `upscale.py`, `trt_upscaler.py`, `ort_models.py` | Real-ESRGAN general x4v3 (dn 0.3), TensorRT if available, down to 1280x960, colour locked to the source, RAFT flicker smoothing. Without an NVIDIA or Apple GPU both models run through ONNX Runtime with DirectML (AMD, Intel) |
| Assemble | `assemble.py` | Pastes the faces back with the source's tones, fades by size/angle/visibility/focus, sharpen 1.8, grain 2, x264 CRF 14 BT.709 |
| Audio | `audio.py`, `audio_tools.py` | Hum removal, Demucs voice split, Resemble Enhance (speech) / DeepFilterNet3 (songs), EQ, -16 LUFS, sync check |
| Run | `run.py` | Goes piece by piece, checks each one, joins them and adds both audio tracks |
| Disk space | `space.py` | Deletes working files once nothing needs them; "Free up space" for finished jobs |
| Control | `control.py` | Pause (kills the job's processes, frees the GPU), resume, progress |
| Before/after | `compare.py` | Side-by-side video of the finished pieces |
| App bridge | `app_bridge.py` | Embedded in the app: jobs, progress, pause/resume, frames for the player |

`common.py` and `settings.py` hold the shared bits. `stubs/` has two fake modules (deepspeed, dlib)
so Resemble Enhance and DicFace can be imported on Windows and macOS.

## Things learned on the test tape

- **Fix the timeline first.** Old captures have holes in their timestamps. Cut everything with
  `fps=30000/1001:start_time=0`, and cut test clips with output seeking (`-i` then `-ss`). Input
  seeking with audio stream copy put the audio 6.3 s early.
- **Check sync on the loudness envelope**, not the waveform (`audio_tools.env_offset_ms`). The voice
  models rebuild the waveform, so comparing samples doesn't work.
- **Listen to each channel on its own.** On the test tape the left channel was dead (`audio_channel`).
- **Hum drifts with tape speed** (48-53 Hz here), so a fixed 50/60 Hz notch doesn't cut it.
- **Restore faces from the original pixels**, not the upscaled frame, and keep the original's broad
  tones (`--tone 0.8`) so the model only adds detail.
- **Leave faces alone that the camera never really captured.** Eyes under 12 px apart at 640x480,
  turned away, half hidden or out of focus - the model just invents things (glasses nobody wore).
  Fade the restoration in rather than switching it on.
- **Flicker:** keep coarse colour/brightness from the source and smooth the fine detail along RAFT flow.
- **Precision:** FP16 is fine for the upscaler (57.7 dB vs PyTorch) but not for the face models -
  KEEP goes black and CodeFormer lost up to 34.8 dB.
- **8 GB of GPU memory** means DicFace batch 2, parser batches of 4, TensorRT workspace 1 GB.
- **AMD and Intel cards:** torch-directml aborts on the upscaler's PReLU and inside RAFT, so it isn't used. Both
  models export cleanly to ONNX (opset 17) and run on ONNX Runtime's DirectML provider; the maths around them stays
  in PyTorch on the processor. On an RTX 3060 Ti through DirectML: 4.0 fps against 6.7 fps for PyTorch CUDA, and
  66.8 dB PSNR against the CUDA output on a 180-frame tape clip.
- **Long runs:** write `.part` files and only rename after a frame-count check, make pause actually
  kill the GPU processes, and keep the computer from sleeping (`SetThreadExecutionState` on Windows,
  `caffeinate` on a Mac).
- **Lossless intermediates add up fast.** On the test tape, per hour of tape: source cut 11 GB,
  deinterlaced 36 GB, restored pieces 17.5 GB (x264 CRF 14), finished video another 17.5 GB. Keeping
  everything until the end peaked near 90 GB per hour. Deinterlacing only a few pieces ahead and deleting
  each deinterlaced piece once its neighbours are restored brings the peak to about 50 GB per hour.

## Disk space

| Per hour of tape | Size | Deleted |
|---|---|---|
| `src/` lossless source cut | ~11 GB | by **Free up space** (the before/after player needs it) |
| `qtgmc/` lossless deinterlaced | ~36 GB in total, but only ~3 GB at a time | piece by piece during the run, the rest after the final checks |
| `work/` piece in progress | ~1.5 GB | after each piece's check, the rest after the final checks |
| `pieces/` restored pieces | ~17.5 GB | by **Free up space** (the player plays them) |
| Finished video | ~17.5 GB | never |

**Free up space** is a button in the app for finished jobs (`app_bridge.py cleanup <id>`). It refuses
while the job runs, before the finished video exists, and for jobs that run from their own scripts.
When a job finishes, the app asks once: **Finish and show the file** (`app_bridge.py finish <id>`) opens
File Explorer with the restored video selected, does the same cleanup (skipped for jobs that run from their
own scripts), deletes the job's player cache and removes its job file. The restored video, the samples and
the log are kept. **Later** leaves the job as it is.

**Modes.** `app_bridge.py probe <video>` reads the scan type, frame rate and size. Tape mode (the default
settings) deinterlaces NTSC tapes to 640x480 at 59.94. Progressive mode (`create <video> --mode progressive`,
chosen automatically for progressive videos; `settings.progressive_settings`) skips QTGMC: prepare.py gives each
piece the same DFTTest denoise and a resize to the square-pixel working size (`work_w` x `work_h`, multiples of 8
for RAFT), at the source frame rate (`out_fps`). run.py passes the working size and rate to every step
(`TAPEWAKE_W/H/FPS/MATRIX/PRIMARIES`, read in common.py; the defaults are the tape values). `create` refuses a
mode the video can't use (`mode_problem`). `--no-faces` and `--no-audio` turn face and sound restoration off;
without sound restoration the original sound is kept as the only track.

**Stop and delete project** (`app_bridge.py delete <id> check|confirm`, three clicks in the app) stops a job,
deletes its working folder's engine files (`space.project_files`: src, qtgmc, work, pieces, audio, logs) and a
half-written restored video, its player cache and its job file. The source, a finished restored video and the
samples are kept. New jobs get a work folder no other job uses; when an older job shares one, delete only removes
the job file and cache.

Set `keep_work_files` in a job's settings to keep every working file (debugging).

## Tools it needs

FFmpeg; a Python with PyTorch (CUDA), torchvision, Demucs, DeepFilterNet3, Resemble Enhance, OpenCV,
SciPy and psutil; a portable VapourSynth Python with vsjetpack 2.2.6 and its plugins; the CodeFormer,
DicFace and KEEP repos with their weights; the Real-ESRGAN weights; optionally TensorRT. See
`../THIRD_PARTY.md` for licences.
