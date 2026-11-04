# Building and setting up tapewake on a Mac

Start with [README-macOS.md](README-macOS.md): which Macs can run tapewake, how long it takes and what to watch
out for. This page is the step-by-step setup.

The Mac version runs the same engine as the Windows one. The AI models run on the Mac's own graphics processor
(Metal) instead of an NVIDIA card. A setup script does most of the work. Budget about an hour, mostly downloads
(the tools and models come to roughly 15 GB).

Read the "Before you use it" section in the README and [THIRD_PARTY.md](THIRD_PARTY.md) first. The face models in
particular are **non-commercial only**.

## What you need

- A Mac with **Apple Silicon** (M1 or later). Intel Macs aren't supported.
- **macOS 15** (Sequoia) or later. Some of the VapourSynth plugins are only built for 15 and up.
- **16 GB of memory** or more. On a Mac the models share memory with everything else, and 16 GB leaves them
  about what the 8 GB graphics card had on the Windows test PC.
- About 20 GB free for the tools, plus about **50 GB per hour of tape** while it's being restored (about 100 GB
  for a two-hour tape; see [engine/README.md](engine/README.md#disk-space)). tapewake checks the space before it
  starts and tells you if there isn't enough.
- [Homebrew](https://brew.sh)
- An internet connection the first time you restore something (a few models download themselves then)

tapewake checks all of this in **Settings → Setup check**, and won't start a restoration on a Mac that can't do it.

## 1. Get the code

```bash
git clone https://github.com/munitkala/tapewake.git
cd tapewake
git checkout macos-v0.2.0
```

## 2. Build the app

You need Rust ([rustup](https://rustup.rs), default options), Node.js (`brew install node`) and Apple's
command-line tools (`xcode-select --install`). Then:

```bash
npm install
npm run tauri build -- --bundles app,dmg
```

The app ends up in `src-tauri/target/release/bundle/macos/tapewake.app`, and a disk image next to it in
`bundle/dmg/`. Drag the app into Applications. An app you built yourself opens normally; one downloaded from
someone else needs a right-click → **Open** the first time, because it isn't signed.

## 3. Everything else

```bash
bash scripts/setup-macos.sh
```

It checks the Mac first, then installs FFmpeg, the two Pythons (3.10 for the engine with PyTorch, 3.12 for
VapourSynth and its plugins), fetches the models and writes `tools.json`. Everything goes into `~/tapewake-tools`; give it another folder as the first argument if you
prefer. It can be run again; whatever is already there is skipped.

`tools.json` lives in `~/Library/Application Support/com.tapewake.app/`. It's the same file as on Windows, with Mac
paths. To put the big working files on another drive, add `"work_root": "/Volumes/YourDrive/tapewake-work"`.

## 4. First restoration

1. Open tapewake and go to **Settings**. Run the setup check. Every line should be green.
2. Try a short clip first (a minute or less) before a whole tape.
3. Pick the video (or drop it on the window) and press **Restore video**.

The result is saved next to the original as `<name> - Restored.mkv`. tapewake keeps the Mac awake while it
restores, but a Mac laptop still sleeps when you close the lid, so leave it open (the screen can turn off) or
connect it to a display.

## How the Mac version differs

- **Graphics processor:** PyTorch's Metal backend (MPS) instead of CUDA. Anything Metal can't do yet falls back
  to the processor for that one step.
- **No TensorRT.** It's NVIDIA only, so the upscaler always runs in plain PyTorch (FP16).
- **DFTTest:** dfttest2's portable "gcc" backend instead of its x86 one (`dfttest2_cpu`), which has no Apple
  Silicon build. Same plugin, same settings.
- **Speed:** slower than an NVIDIA card; the times measured so far are in [README-macOS.md](README-macOS.md#how-long-it-takes).

## If something goes wrong

- **The setup check says something is missing:** run `bash scripts/setup-macos.sh` again; it fills in what isn't
  there.
- **"PyTorch can not use the Apple GPU":** the engine Python has the wrong PyTorch. Delete
  `~/tapewake-tools/engine-venv` and run the script again.
- **It stops with a memory error:** close other big programs (browsers with many tabs, video editors), then
  pause and resume.
- **Anything else:** Troubleshoot → **Open this job's log** shows the last thing that happened. If you open an
  issue, include those lines (remove anything private first).
