# Third-party components

Everything tapewake uses that it didn't write itself, what it's used for, and its licence. Checked on
2 November 2026 against the package metadata and licence files of the installed copies. Not legal advice.

None of this is in the repository. The repository only holds tapewake's own code (MIT, see `LICENSE`).
Everything below gets installed separately, from its own official source.

## Part of the desktop app

| Component | Used for | Licence |
|---|---|---|
| Tauri 2 + its Rust crates, serde_json, rfd | The app window, the file picker | MIT / Apache-2.0 |
| Microsoft Edge WebView2 | Draws the interface (already part of Windows 10/11) | Microsoft |
| Big Shoulders Display, IBM Plex Sans, IBM Plex Mono | Interface fonts (currently loaded from Google Fonts) | SIL OFL 1.1 |

## Installed separately (first-run setup)

### Video

| Component | Used in | Licence |
|---|---|---|
| FFmpeg (a GPL build: libx264, NVENC, FFV1, bwdif, drawtext, loudnorm, ebur128 ...) | every step: cutting, decoding, encoding, audio filters, player previews | GPL-3.0 |
| VapourSynth R81 | `prepare.py`, `deinterlace.py` (runs QTGMC) | LGPL-2.1+ |
| vsjetpack 2.2.6 (QTempGaussMC, DFTTest wrapper, NNEDI3 wrapper) | deinterlacing | MIT |
| bestsource | reading the source pieces in VapourSynth | MIT |
| mvutensils (mvtools) | motion search inside QTGMC | GPL-2.0 |
| znedi3 (+ nnedi3_weights.bin) | interpolation inside QTGMC | GPL-2.0 |
| dfttest2 / dfttest2_cpu | QTGMC's denoiser | GPL-3.0+ / GPL-2.0+ |
| akarin, resize2 | expressions and resizing inside vsjetpack | LGPL-3.0 / LGPL-2.1 |
| zsmooth, vszip | smoothing / helpers inside vsjetpack | MIT |
| Real-ESRGAN weights `realesr-general-x4v3` + `-wdn-x4v3` | `upscale.py` (only the weight files; the network is loaded by tapewake's own code) | BSD-3-Clause |
| RAFT optical flow (torchvision weights) | `upscale.py` flicker smoothing | BSD-3-Clause |
| NVIDIA TensorRT 10.9 + ONNX (optional) | `trt_upscaler.py`, faster upscaling; falls back to PyTorch without it | NVIDIA proprietary / Apache-2.0 |

### Faces (non-commercial)

| Component | Used in | Licence |
|---|---|---|
| **CodeFormer** repository: facelib (RetinaFace detector, face alignment, ParseNet face parser) + basicsr | `faces_find.py` (finding and aligning faces, checking they're visible), `assemble.py` (face masks) | **S-Lab License 1.0, non-commercial** |
| CodeFormer weights: `detection_Resnet50_Final.pth`, `parsing_parsenet.pth` (+ `codeformer.pth` if that model is picked) | same | S-Lab License 1.0 |
| **DicFace** repository + `bfr_100k.pth` | `faces_restore.py`, the default face model | **S-Lab License 1.0, non-commercial** |
| **KEEP** repository + `KEEP-b76feb75.pth` | `faces_restore.py`, optional face model | **S-Lab License 1.0, non-commercial** |

These three are the reason tapewake has to stay free. They're only used, never copied into the repo.

### Sound

| Component | Used in | Licence |
|---|---|---|
| Demucs 4.1 + htdemucs weights | `audio_tools.py`: separating voices from music/background | MIT |
| DeepFilterNet 0.5.6 (DeepFilterNet3) | `audio_tools.py`: voice cleanup ("NEW") | MIT / Apache-2.0 |
| Resemble Enhance + its weights | `audio_tools.py`: voice cleanup ("AI", "AI_LIGHT") | MIT (per its GitHub repo; the package has no licence metadata) |

### Python and libraries

| Component | Used in | Licence |
|---|---|---|
| Python 3.10 | engine | PSF |
| PyTorch 2.5, torchvision, torchaudio (incl. the NVIDIA CUDA runtime in the wheels) | all GPU steps | BSD-3-Clause (CUDA: NVIDIA redistributable) |
| NumPy, SciPy | everywhere | BSD |
| OpenCV | face steps, assembling | Apache-2.0 |
| psutil | `control.py`, `run.py`: finding and stopping the job's processes | BSD-3-Clause |
| Poppins SemiBold | label on the before/after video (`compare.py`) | SIL OFL 1.1 |

## tapewake's own helper files

`engine/stubs/` has two tiny fake modules (deepspeed, dlib). Resemble Enhance and DicFace import those
at the top of their training code, and neither installs on Windows. tapewake never calls them. They're
tapewake's own code (MIT).

## Licence notes

- tapewake doesn't ship any of the GPL parts (FFmpeg, mvtools, znedi3, dfttest2). Users install them
  separately, and MIT is compatible with the GPL anyway. If the installer ever *bundles* them, it has
  to include their licences and offer their source.
- The S-Lab face models are non-commercial. tapewake's MIT licence covers tapewake's own files only,
  not these. That's what the README says too.

## Before releasing an installer

1. Bundle the interface fonts instead of loading them from Google (no request to Google at launch).
2. Make first-run setup download everything above from the official sources and show each licence,
   with the face models clearly marked non-commercial.
3. Optional: code-signing certificate so SmartScreen doesn't warn people.
