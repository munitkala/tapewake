#!/bin/bash
# Sets up everything tapewake's engine needs on a Mac, the same layout BUILDING.md describes for Windows:
# FFmpeg, the two Pythons, the VapourSynth plugins, the models, and tools.json.
#
#   bash scripts/setup-macos.sh [folder]      (default folder: ~/tapewake-tools)
#
# Safe to run again: whatever is already there is skipped. Needs Homebrew (https://brew.sh) and about
# 20 GB free. Several of the models are for non-commercial use only, see THIRD_PARTY.md.
set -euo pipefail

TOOLS="${1:-$HOME/tapewake-tools}"
MODELS="$TOOLS/models"
APP_DATA="$HOME/Library/Application Support/com.tapewake.app"

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\ntapewake setup stopped: %s\n' "$*" >&2; exit 1; }

# fetch URL FILE: download once, to a .part file first so a broken download isn't mistaken for a finished one.
# The big model files drop out now and then, so it carries on from where it stopped rather than starting over.
fetch() {
    [ -s "$2" ] && return
    mkdir -p "$(dirname "$2")"
    local tries=0
    until curl -fL --speed-limit 50000 --speed-time 30 -C - -o "$2.part" "$1"; do
        tries=$((tries + 1))
        [ "$tries" -lt 20 ] || fail "could not download $1"
        echo "download interrupted, carrying on ($tries)"
        sleep 5
    done
    mv "$2.part" "$2"
}

say "Checking this Mac"
[ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ] || fail "tapewake needs a Mac with Apple Silicon (M1 or later)."
major=$(sw_vers -productVersion | cut -d. -f1)
[ "$major" -ge 15 ] || fail "tapewake needs macOS 15 or later (this Mac has $(sw_vers -productVersion))."
mem_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
[ "$mem_gb" -ge 16 ] || fail "tapewake needs at least 16 GB of memory (this Mac has $mem_gb GB)."
command -v brew >/dev/null || fail "Homebrew is missing. Install it from https://brew.sh and run this again."
free_gb=$(df -g "$HOME" | awk 'NR==2 {print $4}')
[ "$free_gb" -ge 20 ] || fail "the tools need about 20 GB and there is only $free_gb GB free."
echo "Apple Silicon, macOS $(sw_vers -productVersion), $mem_gb GB memory, $free_gb GB free"

say "Homebrew tools (FFmpeg, uv, Git LFS)"
# ffmpeg-full, not ffmpeg: the plain formula is built without drawtext, which labels the before/after video
brew install ffmpeg-full uv git-lfs
git lfs install >/dev/null

mkdir -p "$TOOLS" "$MODELS"
cd "$TOOLS"

say "Engine Python (3.10, PyTorch with Apple GPU support)"
PY="$TOOLS/engine-venv/bin/python"
[ -x "$PY" ] || uv venv --python 3.10 engine-venv
uv pip install -p "$PY" torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 \
    "numpy<2" scipy opencv-python psutil demucs==4.1.0 deepfilternet==0.5.6 \
    addict future lmdb pyyaml requests scikit-image tqdm yapf lpips einops ffmpeg-python av
# KEEP is written for diffusers 0.11, which in turn needs a huggingface_hub from before 0.26
uv pip install -p "$PY" diffusers==0.11.0 "huggingface_hub<0.26"
# --no-deps: Resemble Enhance asks for deepspeed, which doesn't install here (engine/stubs stands in for it)
uv pip install -p "$PY" resemble-enhance==0.0.1 --no-deps
uv pip install -p "$PY" celluloid librosa matplotlib omegaconf pandas ptflops resampy rich soundfile tabulate
"$PY" -c "import torch; assert torch.backends.mps.is_available(), 'no Metal'; print('PyTorch', torch.__version__, 'on the Apple GPU')"

say "VapourSynth Python (3.12) and plugins"
VS="$TOOLS/vs-venv/bin/python"
[ -x "$VS" ] || uv venv --python 3.12 vs-venv
uv pip install -p "$VS" vapoursynth==81 vsjetpack==2.2.6 vapoursynth-bestsource vapoursynth-mvutensils \
    vapoursynth-znedi3 vapoursynth-akarin vapoursynth-resize2 vapoursynth-zsmooth vapoursynth-vszip \
    vapoursynth-vivtc vapoursynth-dmetrics

# QTGMC's denoiser: dfttest2_cpu is x86 only, so on Apple Silicon use the same plugin's portable "gcc" backend.
# The wheel ships it as a .so, but VapourSynth only autoloads .dylib files on a Mac, hence the link.
uv pip install -p "$VS" vapoursynth-dfttest2-gcc==11.1.0
PLUGINS=$("$VS" -c "import vapoursynth, os; print(os.path.join(os.path.dirname(vapoursynth.__file__), 'plugins'))")
ln -sf libdfttest2_gcc.so "$PLUGINS/libdfttest2_gcc.dylib"
"$VS" -c "import vapoursynth as vs; assert hasattr(vs.core, 'dfttest2_gcc'); print('VapourSynth', vs.core.core_version, 'with DFTTest')"

say "Face models (CodeFormer, DicFace, KEEP)"
[ -d CodeFormer ] || git clone --depth 1 https://github.com/sczhou/CodeFormer.git
# basicsr imports a version.py that CodeFormer's own install step normally writes; tapewake only needs it to exist
if [ ! -f CodeFormer/basicsr/version.py ]; then
    ver=$(cat CodeFormer/basicsr/VERSION)
    sha=$(git -C CodeFormer rev-parse --short HEAD)
    printf "__version__ = '%s'\n__gitsha__ = '%s'\nversion_info = (%s)\n" "$ver" "$sha" "${ver//./, }" \
        > CodeFormer/basicsr/version.py
fi
[ -d "$MODELS/DicFace" ] || git clone --depth 1 https://github.com/fudan-generative-vision/DicFace.git "$MODELS/DicFace"
# KEEP keeps its flow model (gmflow) in a submodule
[ -d "$MODELS/KEEP" ] || git clone --depth 1 --recursive --shallow-submodules https://github.com/jnjaby/KEEP.git "$MODELS/KEEP"
fetch https://huggingface.co/fudan-generative-ai/DicFace_model/resolve/main/CodeFormer/bfr_100k.pth \
    "$MODELS/weights/DicFace/bfr_100k.pth"
fetch https://github.com/jnjaby/KEEP/releases/download/v1.0.0/KEEP-b76feb75.pth "$MODELS/weights/KEEP/KEEP-b76feb75.pth"
fetch https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth \
    "$TOOLS/CodeFormer/weights/CodeFormer/codeformer.pth"

say "Upscaler weights (Real-ESRGAN)"
for w in realesr-general-x4v3 realesr-general-wdn-x4v3; do
    fetch "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/$w.pth" "$MODELS/esrgan-weights/$w.pth"
done

say "Voice model (Resemble Enhance)"
if [ ! -d "$MODELS/weights/resemble-enhance/enhancer_stage2" ]; then
    rm -rf "$TOOLS/build/re-model"
    git clone https://huggingface.co/ResembleAI/resemble-enhance "$TOOLS/build/re-model"
    mkdir -p "$MODELS/weights/resemble-enhance"
    mv "$TOOLS/build/re-model/enhancer_stage2" "$MODELS/weights/resemble-enhance/"
    rm -rf "$TOOLS/build/re-model"
fi

say "Font for the before/after video (Poppins)"
fetch https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-SemiBold.ttf "$MODELS/fonts/Poppins-SemiBold.ttf"

say "Telling tapewake where everything is"
mkdir -p "$APP_DATA"
if [ -f "$APP_DATA/tools.json" ]; then
    echo "Keeping the tools.json that's already there: $APP_DATA/tools.json"
else
    "$PY" - "$APP_DATA/tools.json" "$TOOLS" "$(brew --prefix ffmpeg-full)/bin/ffmpeg" <<'EOF'
import json, os, sys
out, tools, ffmpeg = sys.argv[1:]
json.dump({'python': os.path.join(tools, 'engine-venv', 'bin', 'python3'),
           'vs_python': os.path.join(tools, 'vs-venv', 'bin', 'python3'),
           'models': os.path.join(tools, 'models'),
           'codeformer': os.path.join(tools, 'CodeFormer'),
           'ffmpeg': ffmpeg}, open(out, 'w'), indent=2)
print('Wrote', out)
EOF
fi

say "Done"
echo "Open tapewake, go to Settings and run the setup check. Try a short clip first."
echo "To put the big working files on another drive, add \"work_root\" to $APP_DATA/tools.json."
