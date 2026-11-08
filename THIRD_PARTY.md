# Third-party components

Everything tapewake uses that it didn't write itself, what it's used for, and its licence. Checked on
3 November 2026 against the package metadata and licence files of the installed copies. Not legal advice.

None of this is in the repository. The repository only holds tapewake's own code (MIT, see `LICENSE`).
Everything below gets installed separately, from its own official source.

## Part of the desktop app

| Component | Used for | Licence |
|---|---|---|
| Tauri 2 + its Rust crates, serde_json, rfd | The app window, the file picker | Mostly MIT / Apache-2.0 (see below) |
| Microsoft Edge WebView2 | Draws the interface (already part of Windows 10/11) | Microsoft |
| WebKit (WKWebView) | Draws the interface on a Mac (part of macOS) | Apple / LGPL-2.1 and BSD |
| IOKit, CoreFoundation, `notifyutil`, `caffeinate` (part of macOS) | `thermal.py`: the chip's temperature and macOS's thermal pressure; `run.py`: keeping the Mac awake. Called, nothing copied | Apple (system software) |
| Big Shoulders Display, IBM Plex Sans, IBM Plex Mono | Interface fonts (currently loaded from Google Fonts) | SIL OFL 1.1 |

The Rust crates are compiled into `tapewake.exe`: 233 of them on Windows (`cargo metadata`, 3 November 2026).
All are permissive and none is GPL. Most are MIT and/or Apache-2.0. The others: 18 Unicode-3.0 (the `icu_*`,
`zerovec`, `yoke`, `tinystr`, `litemap`, `writeable` and `potential_utf` text-handling crates), 5 MPL-2.0
(`cssparser`, `cssparser-macros`, `selectors`, `dtoa-short`, `option-ext`; used unmodified), 2 Zlib (`foldhash`,
`zlib-rs`), 2 BSD-3-Clause (`alloc-stdlib`, `alloc-no-stdlib`), plus a few that offer Unlicense, 0BSD or CC0 as
alternatives. The npm side is only the Tauri command-line tool (MIT / Apache-2.0), used to build and not shipped.

## Installed separately (first-run setup)

### Video

| Component | Used in | Licence |
|---|---|---|
| FFmpeg (a GPL build: libx264, NVENC / VideoToolbox, FFV1, bwdif, drawtext, loudnorm, ebur128 ...; on a Mac Homebrew's `ffmpeg-full`) | every step: cutting, decoding, encoding, audio filters, player previews | GPL-3.0 |
| VapourSynth R81 | `prepare.py`, `deinterlace.py` (runs QTGMC) | LGPL-2.1+ |
| vsjetpack 2.2.6 (QTempGaussMC, DFTTest wrapper, NNEDI3 wrapper) | deinterlacing | MIT |
| bestsource | reading the source pieces in VapourSynth | MIT |
| mvutensils (mvtools) | motion search inside QTGMC | GPL-2.0 |
| znedi3 (+ nnedi3_weights.bin) | interpolation inside QTGMC | GPL-2.0 |
| dfttest2 / dfttest2_cpu (Windows), dfttest2_gcc (Mac) | QTGMC's denoiser | GPL-3.0+ / GPL-2.0+ (the Mac wheel: GPL-2.0+) |
| akarin, resize2 | expressions and resizing inside vsjetpack | LGPL-3.0 / LGPL-2.1 |
| zsmooth, vszip | smoothing / helpers inside vsjetpack | MIT |
| Real-ESRGAN weights `realesr-general-x4v3` + `-wdn-x4v3` | `upscale.py` (only the weight files; the network is loaded by tapewake's own code) | BSD-3-Clause |
| RAFT optical flow (torchvision weights) | `upscale.py` flicker smoothing | BSD-3-Clause |
| NVIDIA TensorRT 10.9 + ONNX (optional) | `trt_upscaler.py`, faster upscaling; falls back to PyTorch without it | NVIDIA proprietary / Apache-2.0 |
| onnxruntime-directml 1.23.0 + onnx 1.17.0 (optional, Windows) | `ort_models.py`: the upscaler and RAFT on AMD and Intel cards; falls back to PyTorch on the processor without it | MIT (the wheel's licence file); the wheel also carries Microsoft's `DirectML.dll` (DirectML redistributable, Microsoft's licence terms). tapewake doesn't ship either: it's installed from PyPI |

### Faces (non-commercial)

| Component | Used in | Licence |
|---|---|---|
| **CodeFormer** repository: facelib (RetinaFace detector, face alignment, ParseNet face parser) + basicsr | `faces_find.py` (finding and aligning faces, checking they're visible), `assemble.py` (face masks) | **S-Lab License 1.0, non-commercial** |
| CodeFormer weights: `detection_Resnet50_Final.pth`, `parsing_parsenet.pth` (+ `codeformer.pth` if that model is picked) | same | S-Lab License 1.0 |
| **DicFace** repository + `bfr_100k.pth` | `faces_restore.py`, the default face model | **S-Lab License 1.0, non-commercial** |
| **KEEP** repository + `KEEP-b76feb75.pth` | `faces_restore.py`, optional face model | **S-Lab License 1.0, non-commercial** |

These three are the reason tapewake has to stay free. They're only used, never copied into the repo.

The face model repositories import a few more packages when they load. These were found while setting up the Mac
version (`scripts/setup-macos.sh` installs them); a Windows setup needs them too.

| Component | Used in | Licence |
|---|---|---|
| ffmpeg-python 0.2 | imported by DicFace's data loaders | Apache-2.0 |
| PyAV 17 (its wheel includes its own FFmpeg libraries) | imported by DicFace's data loaders | BSD-3-Clause (FFmpeg libraries: LGPL-2.1+) |
| diffusers 0.11.0 + huggingface_hub 0.25 | KEEP's attention layers (KEEP only works with this old diffusers) | Apache-2.0 |
| gmflow (KEEP's git submodule) | KEEP's flow model | Apache-2.0 |

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
| uv (Mac setup only) | `scripts/setup-macos.sh`: installs the two Pythons and their packages | MIT / Apache-2.0 |
| NumPy, SciPy | everywhere | BSD |
| OpenCV | face steps, assembling | Apache-2.0 |
| psutil | `control.py`, `run.py`: finding and stopping the job's processes | BSD-3-Clause |
| Helper libraries the face and sound models need (BUILDING.md, steps 4 and 5): addict, celluloid, einops, future, ptflops, PyYAML, rich, tabulate | imported by CodeFormer/basicsr, DicFace and Resemble Enhance | MIT |
| … continued: lpips, omegaconf, pandas, scikit-image, soundfile | same | BSD |
| … continued: librosa, resampy | same | ISC |
| … continued: requests, yapf | same | Apache-2.0 |
| … continued: matplotlib | same | PSF-based (matplotlib licence) |
| … continued: tqdm | same | MPL-2.0 and MIT |
| … continued: lmdb | same | OpenLDAP Public License 2.8 |
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
2. Ship the licence texts of the Rust crates with the installer (they're compiled into `tapewake.exe`, and their
   licences ask for the notices to come with binaries), for example generated with `cargo about`.
3. Make first-run setup download everything above from the official sources and show each licence,
   with the face models clearly marked non-commercial.
4. Optional: code-signing certificate so SmartScreen doesn't warn people.
