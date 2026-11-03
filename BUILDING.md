# Building and setting up tapewake

This takes you from an empty PC to a working restoration. There's no installer that sets everything up for you
yet, so it's a fair amount of steps. Budget an hour or two, plus download time (the tools and models come to roughly
15 GB).

All commands are for **PowerShell**. The versions given are the ones tapewake was tested with; newer ones will
probably work but haven't been tried.

Before you start, read the "Before you use it" section in the README and [THIRD_PARTY.md](THIRD_PARTY.md). Several
of the parts you'll download have their own licences. The face models in particular are **non-commercial only**.

## What you need

- Windows 10 (2004 or later) or Windows 11, 64-bit
- An NVIDIA graphics card with at least 8 GB, and a recent driver (the CUDA 12.1 builds of PyTorch need driver
  530 or newer)
- About 20 GB free for the tools, plus about 50 GB per hour of tape while it's being restored (about 100 GB
  for a two-hour tape; see [engine/README.md](engine/README.md#disk-space))
- An internet connection the first time you restore something (a few models download themselves then)

In the steps below everything goes into one folder, `C:\tapewake-tools`. You can use any folder you like, just
use the same paths in `tools.json` at the end.

```powershell
mkdir C:\tapewake-tools
mkdir C:\tapewake-tools\models
```

## 1. Get the code

Install [Git for Windows](https://git-scm.com/download/win) if you don't have it, then:

```powershell
cd C:\
git clone https://github.com/munitkala/tapewake.git
cd tapewake
```

## 2. Build the app

You need three things to build it:

- **Rust**: install with [rustup](https://rustup.rs) and accept the default (stable, MSVC)
- **Visual Studio 2022 Build Tools** with the **Desktop development with C++** workload
  ([download](https://visualstudio.microsoft.com/visual-cpp-build-tools/))
- **Node.js LTS** ([nodejs.org](https://nodejs.org))

Open a new PowerShell window after installing them (so they're on your PATH), then in the `tapewake` folder:

```powershell
npm install
npm run tauri build -- --bundles nsis
```

The first build takes a while (Rust compiles everything once). The installer ends up in
`src-tauri\target\release\bundle\nsis\`. Run it to install tapewake for your user.

If you just want to try it while developing, `npm run dev` starts the app straight from the source with live reload.

## 3. FFmpeg

Easiest is winget, which tapewake finds by itself:

```powershell
winget install Gyan.FFmpeg
```

Any recent "full" build works. Note that full builds are GPL (see THIRD_PARTY.md).

## 4. The engine's Python

Install **Python 3.10** from [python.org](https://www.python.org/downloads/release/python-31011/) (3.10.11, the
Windows 64-bit installer). Then make a separate environment for tapewake:

```powershell
cd C:\tapewake-tools
py -3.10 -m venv engine-venv
.\engine-venv\Scripts\python -m pip install --upgrade pip

# PyTorch with CUDA 12.1
.\engine-venv\Scripts\python -m pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121

# the rest of the engine
.\engine-venv\Scripts\python -m pip install "numpy<2" scipy opencv-python psutil demucs==4.1.0 deepfilternet==0.5.6

# what the face model repos need
.\engine-venv\Scripts\python -m pip install addict future lmdb pyyaml requests scikit-image tqdm yapf lpips einops

# Resemble Enhance: --no-deps because it asks for deepspeed, which doesn't install on Windows
# (tapewake ships a small stand-in for it in engine\stubs)
.\engine-venv\Scripts\python -m pip install resemble-enhance==0.0.1 --no-deps
.\engine-venv\Scripts\python -m pip install celluloid librosa matplotlib omegaconf pandas ptflops resampy rich soundfile tabulate
```

Quick check that the graphics card is visible:

```powershell
.\engine-venv\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

It should print `True` and your card's name.

## 5. VapourSynth (for deinterlacing)

The deinterlacing step runs in its own Python, because VapourSynth wants a newer one. Install **Python 3.12** from
python.org, then:

```powershell
cd C:\tapewake-tools
py -3.12 -m venv vs-venv
.\vs-venv\Scripts\python -m pip install --upgrade pip
.\vs-venv\Scripts\python -m pip install vapoursynth==81 vsjetpack==2.2.6
.\vs-venv\Scripts\python -m pip install vapoursynth-bestsource vapoursynth-mvutensils vapoursynth-znedi3 vapoursynth-dfttest2-cpu dfttest2 vapoursynth-akarin vapoursynth-resize2 vapoursynth-zsmooth vapoursynth-vszip vapoursynth-vivtc vapoursynth-dmetrics
```

Check it:

```powershell
.\vs-venv\Scripts\python -c "import vapoursynth as vs; print(vs.core.version())"
```

## 6. The face models

These are three separate research projects. Read their licences before downloading: they're for
**non-commercial use only**.

```powershell
cd C:\tapewake-tools
git clone https://github.com/sczhou/CodeFormer.git
cd models
git clone https://github.com/fudan-generative-vision/DicFace.git
git clone https://github.com/jnjaby/KEEP.git
mkdir weights\DicFace, weights\KEEP
```

Then download the weights by hand and put them here:

| File | Where to get it | Put it in |
|---|---|---|
| `bfr_100k.pth` (DicFace, the default) | [DicFace_model on Hugging Face](https://huggingface.co/fudan-generative-ai/DicFace_model), in the `CodeFormer` folder | `models\weights\DicFace\` |
| `KEEP-b76feb75.pth` (optional) | [KEEP releases, v1.0.0](https://github.com/jnjaby/KEEP/releases/tag/v1.0.0) | `models\weights\KEEP\` |
| `codeformer.pth` (optional) | [CodeFormer releases, v0.1.0](https://github.com/sczhou/CodeFormer/releases/tag/v0.1.0) | `CodeFormer\weights\CodeFormer\` |

The face finder and face parser weights (`detection_Resnet50_Final.pth`, `parsing_parsenet.pth`) download
themselves into `CodeFormer\weights\facelib` the first time faces are processed.

## 7. Upscaling weights

From the [Real-ESRGAN v0.2.5.0 release](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0), download
`realesr-general-x4v3.pth` and `realesr-general-wdn-x4v3.pth` into `models\esrgan-weights\`.

## 8. Voice model (Resemble Enhance)

The model lives on Hugging Face and needs Git LFS:

```powershell
cd C:\tapewake-tools
git lfs install
git clone https://huggingface.co/ResembleAI/resemble-enhance re-model
mkdir models\weights\resemble-enhance
Move-Item re-model\enhancer_stage2 models\weights\resemble-enhance\
```

You can delete `re-model` afterwards. Demucs and DeepFilterNet download their own models the first time the sound
is restored, and the RAFT model used for flicker smoothing downloads itself the first time something is upscaled.

## 9. Font for the before/after video

Download **Poppins** from [Google Fonts](https://fonts.google.com/specimen/Poppins) and copy
`Poppins-SemiBold.ttf` into `models\fonts\`. (Only needed for the before/after video.)

## 10. TensorRT (optional, about 2x faster upscaling)

Skip this if you like, tapewake falls back to plain PyTorch. TensorRT is NVIDIA's own licence.

```powershell
cd C:\tapewake-tools
.\engine-venv\Scripts\python -m pip install --target models\trt-pkgs tensorrt-cu12==10.9.0.34 onnx==1.17.0
```

The first upscale then spends a few minutes building an engine for your card (saved in `models\trt-engines`).

## 11. Tell tapewake where everything is

By now `C:\tapewake-tools` should look like this:

```
C:\tapewake-tools
├─ engine-venv\
├─ vs-venv\
├─ CodeFormer\
└─ models\
   ├─ DicFace\
   ├─ KEEP\
   ├─ esrgan-weights\   realesr-general-x4v3.pth, realesr-general-wdn-x4v3.pth
   ├─ fonts\            Poppins-SemiBold.ttf
   ├─ weights\
   │  ├─ DicFace\       bfr_100k.pth
   │  ├─ KEEP\          KEEP-b76feb75.pth
   │  └─ resemble-enhance\enhancer_stage2\
   └─ trt-pkgs\         (optional)
```

Create `%LOCALAPPDATA%\com.tapewake.app\tools.json` (paste that path into Explorer's address bar to get there; make
the folder if it doesn't exist yet):

```json
{
  "python": "C:\\tapewake-tools\\engine-venv\\Scripts\\python.exe",
  "vs_python": "C:\\tapewake-tools\\vs-venv\\Scripts\\python.exe",
  "models": "C:\\tapewake-tools\\models",
  "codeformer": "C:\\tapewake-tools\\CodeFormer",
  "work_root": "D:\\tapewake-work"
}
```

- Use double backslashes, it's JSON.
- `work_root` is where the big temporary files go. Point it at your largest drive. Leave it out to use the
  video's own folder.
- Add `"ffmpeg": "C:\\path\\to\\ffmpeg.exe"` only if you didn't install FFmpeg with winget.

## 12. First restoration

1. Open tapewake and go to **Settings**. Run the setup check. Every line should be green.
2. Try a short clip first (a minute or two) to make sure everything works before you start a whole tape.
3. Pick the video (or drop it on the window) and press **Restore video**.

The result is saved next to the original as `<name> - Restored.mkv`. You can pause at any time and resume later;
finished pieces are kept.

## If something goes wrong

- **The setup check says something is missing:** the path in `tools.json` is wrong or the file isn't there. Check
  for single backslashes.
- **`torch.cuda.is_available()` is False:** update the NVIDIA driver, and make sure you installed the `cu121`
  PyTorch (the plain `pip install torch` gives you a CPU-only build).
- **It stops during "Finding faces" or "Restoring faces" with an out-of-memory error:** close other programs that
  use the graphics card (games, browsers with video), then pause and resume.
- **Anything else:** Troubleshoot > **Open this job's log** shows the last thing that happened. If you open an
  issue, include those lines (remove anything private first).
