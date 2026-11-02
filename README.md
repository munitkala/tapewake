# tapewake

**Local video restoration.** Free and open source (MIT licence).

tapewake restores old home videos on your own computer: it deinterlaces the tape, upscales it with AI, restores
faces carefully, cleans up the sound, and gives you the restored film plus a before-and-after comparison. Nothing is
uploaded anywhere.

![Before and after](screenshots/before-after.gif)

## What it does

- Restores home-video tapes (interlaced NTSC, 4:3) with settings proven on a full two-hour VHS tape: QTGMC
  deinterlacing, Real-ESRGAN upscaling to 1280×960 at 59.94 fps, face restoration that keeps likeness, gentle grain,
  and sound restoration.
- Works piece by piece and can be paused and resumed at any time; finished parts are kept and checked.
- Shows each restoration live, with a player that plays the finished parts next to the original
  (side by side, wipe, or hold-to-compare) and a filmstrip.

| | |
|---|---|
| ![Before and after, side by side](screenshots/before-after-side-by-side.png) | ![Wipe comparison](screenshots/before-after-wipe.png) |
| ![Progress](screenshots/progress.png) | ![Finished](screenshots/finished.png) |
| ![Guided tour](screenshots/guided-tour.png) | ![Engine commands](screenshots/lab-commands.png) |

## Status

Version 0.1 runs on Windows with an NVIDIA graphics card. The source code will be published here later.

## Example footage

The example clip is from *Amateur films: Ivan Besse collection, Britton, South Dakota 1938–39 (Part X)*, Prelinger
Archives, via the Internet Archive (archive.org/details/sIvanBes1938_10), public domain.

## Credits

tapewake builds on these open-source projects. Thank you to their authors. Each keeps its own licence. None of them
is part of this repository, and their authors are not involved with tapewake.

| Project | Used for | Licence |
|---|---|---|
| [FFmpeg](https://ffmpeg.org) | Reading, cutting and encoding video and sound | GPL-3.0 (build used) |
| [VapourSynth](https://www.vapoursynth.com) and [vs-jetpack](https://github.com/Jaded-Encoding-Thaumaturgy/vs-jetpack) (QTGMC) | Deinterlacing | LGPL-2.1 / MIT |
| [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) | Upscaling | BSD-3-Clause |
| [CodeFormer](https://github.com/sczhou/CodeFormer) | Finding and aligning faces | S-Lab License 1.0 (non-commercial) |
| [DicFace](https://github.com/fudan-generative-vision/DicFace) | Face restoration | S-Lab License 1.0 (non-commercial) |
| [KEEP](https://github.com/jnjaby/KEEP) | Face restoration (alternative) | S-Lab License 1.0 (non-commercial) |
| [Demucs](https://github.com/facebookresearch/demucs) | Separating voices from noise | MIT |
| [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) | Cleaning up voices | MIT / Apache-2.0 |
| [Resemble Enhance](https://github.com/resemble-ai/resemble-enhance) | Enhancing voices | MIT |
| [PyTorch](https://pytorch.org) and torchvision (RAFT) | Running the AI models | BSD-3-Clause |
| [OpenCV](https://opencv.org), NumPy, SciPy, Pillow | Image and signal processing | Apache-2.0 / BSD / HPND |
| [Tauri](https://tauri.app) | The desktop app | MIT / Apache-2.0 |
| IBM Plex, Big Shoulders Display, Poppins | Fonts | SIL Open Font License |

The face models (CodeFormer, DicFace, KEEP) may only be used for non-commercial purposes, so tapewake will stay free
while it uses them.

## Licence

MIT (see `LICENSE`). This covers tapewake's own files only, not the projects listed under Credits.
