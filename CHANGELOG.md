# Changelog

What changed in each update of tapewake, newest first. Updates that haven't been given a version
number yet are listed under **Unreleased**.

Windows and Mac are released separately, each with its own tag (`windows-v…`, `macos-v…`). Each version says which
platforms it was released for. Versions up to 0.1.5 were Windows only.

## Unreleased

Nothing yet.

## 0.2.0 (2026-11-03)

**Released for:** Mac (`macos-v0.2.0`). Not released for Windows yet: the changes to shared code below (the heat
pause and the computer details in Settings) haven't been tried on a Windows PC. The latest Windows release is
still 0.1.5 (`windows-v0.1.5`).

### A Mac version

**Why:** to restore tapes on a Mac with Apple Silicon, without an NVIDIA card.

**What changed**

- tapewake builds and runs on Macs with Apple Silicon (M1 or later), macOS 15 or later and 16 GB of memory or
  more. The AI models run on the Mac's graphics processor (Metal). See [README-macOS.md](README-macOS.md) and
  [BUILDING-macOS.md](BUILDING-macOS.md).
- On a Mac, the setup check shows the chip, its graphics cores and memory, and **Restore video** refuses to
  start on a Mac that can't run tapewake, or when the disk doesn't have room for that particular video (worked
  out from its length, size and frame rate).
- `scripts/setup-macos.sh` installs everything the engine needs on a Mac in one go. It installs Homebrew's
  `ffmpeg-full`: the plain `ffmpeg` formula is built without the text filter the before/after video uses for its
  labels.
- On a Mac the player plays an MP4 copy of each restored piece (the same video, not re-encoded), because the Mac's
  webview can't play MKV files. The last three copies are kept.
- On a Mac the before/after video is encoded with Apple's own hardware encoder (VideoToolbox) instead of NVIDIA's.
- The progress panel no longer cuts off long lines at narrow window sizes; they wrap instead.
- The flicker smoothing in the upscaler reads the picture edge a different way on a Mac, because Metal doesn't
  have the mode it uses on Windows. The result is the same.
- `THIRD_PARTY.md` lists what the Mac version uses on top (the Mac denoiser build, uv), and four packages the face
  models import that weren't listed: ffmpeg-python, PyAV, diffusers 0.11 and gmflow.


### Pausing when the computer gets too hot

**Why:** a restoration keeps the computer at full load for hours or days. If it overheats, it's better to stop and
wait than to carry on.

**What changed**

- While it restores, tapewake checks how hot the computer is every 15 seconds (`thermal.py`). If it stays too hot
  for a minute, it pauses the restoration like the Pause button does and waits. Once the computer has cooled down
  (and at least 5 minutes have passed) it carries on by itself from the last finished part.
- A pause from the user always wins: pressing Pause or **Keep paused** while it waits stops it from resuming by
  itself, and a restoration the user paused never resumes by itself.
- "Too hot" on a Mac is macOS's own thermal pressure at Trapping or worse (Heavy is normal under load). On Windows
  it's the NVIDIA card slowing itself down because of heat, or 90 °C; it resumes at 75 °C. The Windows side hasn't
  been tried on a Windows PC yet.
- **Settings → Performance** shows this computer: processor, graphics, memory, operating system, what the AI models
  run on, free space for the working files and how hot it is. The progress card shows the temperature too: on a
  Mac the thermal pressure and the chip's temperature in °C, on Windows the graphics card's temperature.

### README for each platform

- The README covers what's the same everywhere; [README-windows.md](README-windows.md) and
  [README-macOS.md](README-macOS.md) cover each platform: what it needs, how long it takes, installing, and what's
  different.

Apart from the heat pause and the computer details in Settings, nothing changed on Windows: the restoration works
exactly as in 0.1.5.

## 0.1.5 (2026-11-03)

### Clean-up after a full check of the repository and the installer

**Why:** a file-by-file check of everything in the repository and the installer, its dependencies and their
licences.

**What changed**

- The installer carried 24 Python cache files (`__pycache__`) left in the engine folder by test runs on the
  build computer. Nothing needed them. Every build now deletes them first (`scripts/clean-engine.mjs`).
- `THIRD_PARTY.md` lists the Rust crates' licences accurately (not only MIT / Apache-2.0: also Unicode-3.0,
  MPL-2.0, Zlib and BSD-3-Clause, all permissive, none GPL) and the helper libraries the face and sound models
  need (all permissive). Shipping the crates' licence texts with an installer is on the release checklist.
- Two screenshots that nothing used were removed.

Nothing else changed: the app and the restoration work exactly as in 0.1.4.

## 0.1.4 (2026-11-03)

### Progressive video mode

**Why:** every restoration used the home-video tape settings, which deinterlace the picture. A video that
is already progressive (each frame a whole picture, like a DVD rip of a film) has nothing to deinterlace, so
it failed: a 720×462 rip stopped with "SeparateFields: clip height must be mod 2", and its frame rate would
have been changed to the tape's 29.97 fps.

**What changed**

- New **Progressive video · Tested** preset. When you choose a video, tapewake reads its scan type, frame
  rate and size, picks the preset that fits and says so under the preset menu.
- Progressive mode skips deinterlacing and keeps the video's own frame rate (23.976 stays 23.976). It works
  at a square-pixel size of its own (a widescreen 720×462 DVD frame becomes 856×464) and restores at twice
  that. Everything after deinterlacing is the same as for tapes: the same gentle denoise (DFTTest), the
  Real-ESRGAN upscaler with flicker smoothing, sharpening and grain.
- Face restoration and sound restoration start off in this mode. Switching Audio off keeps the original
  sound as it is (one track).
- The home-video tape mode is unchanged: on a tape clip, the cut and deinterlaced pieces are identical,
  frame for frame, to 0.1.3.

### Settings that can't work are explained before anything starts

**Why:** the app let you choose settings and presets that the restoration doesn't use yet, and nothing
said so.

**What changed**

- Under the preset menu, a note says what the video is, which mode it gets and what the result will be,
  and lists any setting that can't work, in red.
- Pressing Restore with such a setting opens a window that explains each one and offers **Use the tested
  settings for this video**. Nothing starts until the settings can work. It catches: Deinterlace on for a
  progressive video or off for an interlaced one, interlaced videos that aren't 29.97 fps, presets that
  aren't available yet, Enhance with AI off, and any other step switched or changed from the tested values.
- The restoration engine refuses the same mistakes, with the same explanation.

### Fixes

- The step switches only changed how they looked: switching Face restoration off didn't reach the
  restoration in 0.1.3. They now do.
- A running restoration with face restoration off showed an empty preset menu.

## 0.1.3 (2026-11-03)

### Stop and delete a project

**Why:** there was no way to stop a restoration for good or remove a project, for example one started
with the wrong settings. Pause was the only option, so the project stayed in tapewake.

**What changed**

- New red **Stop and delete project** button (**Delete project** once it has stopped) under each
  restoration's progress. Deleting takes three clicks: the red button opens a red confirmation window
  that says how many GB of working files go, you tick "I understand ... can't be undone", and only then
  does the red **Delete project** button work.
- It stops the restoration if it is running (freeing the graphics card), deletes its working files and
  any half-written restored video, deletes the player cache and removes the project from tapewake.
- Kept: the original video, a finished restored video and saved samples. Not offered for restorations
  that run from their own scripts.

### Every project gets its own working folder

**Why:** a second project for the same video used the first one's working folder, so the two would
have overwritten each other's files.

**What changed**

- New projects get a folder no other project uses: `<name> - tapewake work (2)`, `(3)` and so on.
- For projects made before this update that share a folder, Delete only removes that project from
  tapewake and keeps the files, because the other project still needs them.

### The progress screen shows the steps a project really runs

**Why:** it always listed finding faces, restoring faces, upscaling and assembling, whatever was chosen.

**What changed**

- A project started with **Face restoration** switched off now restores without faces, and its progress
  shows only upscaling and assembling. Face restoration is the first setting the app passes on to the
  restoration; the others still use the tested values (see the Status section of the README).

### Progress no longer looks stuck at the start

**Why:** before the first piece starts, tapewake prepares the sound, cuts the video into pieces and
deinterlaces the first ones. That can take a while (the sound alone took about 8 minutes for a two-hour
film), and the progress screen showed 0% and no steps the whole time.

**What changed**

- A **Preparing** stage shows these three steps, each with its own percentage, and they fill the first
  2% of the overall bar. The sound step now reports after every block of audio.
- If cutting or deinterlacing fails during the preparation, the progress screen says so in red straight
  away (with the error), instead of looking stuck until the sound is finished.

## 0.1.2 (2026-11-03)

### A "Restoration finished" prompt

**Why:** when a restoration finished, nothing told you. The job just sat in its tab, and you had to
find the file and tidy up by hand.

**What changed**

- When a job finishes, tapewake asks once: **Finish and show the file** or **Later**.
- Finish opens File Explorer with the restored video selected, deletes the working files (as Free up
  space does) and the player cache, and removes the job from tapewake. The restored video, the saved
  samples and the log are kept.
- Jobs that run from their own scripts keep their working files; only the job is removed from tapewake.

## 0.1.1 (2026-11-02)

### Disk space: working files are deleted once nothing needs them

**Why:** restoring a tape makes large working files at every stage, and the lossless ones are huge.
Before this update every one of them was kept until the very end, and none were deleted after the
finish. A two-hour tape used about **180 GB** and kept all of it until you deleted it by hand. The
README said about 150 GB.

**What changed**

- Deinterlacing now stays at most 8 pieces ahead of the graphics card instead of doing the whole tape
  first (setting `qtgmc_ahead`).
- Each deinterlaced piece is deleted as soon as it and both of its neighbours are restored.
- Once the finished video passes its checks, whatever is left of the deinterlaced and in-progress
  files is deleted automatically.
- New **Free up space** button for finished restorations. It deletes the remaining working files (the
  cut source and the restored pieces) and keeps the restored video. You click it twice to confirm. It
  refuses while a restoration is running and before the finished video exists. Afterwards the
  before/after player for that video explains that its files were deleted.
- Resuming a finished restoration no longer rebuilds its finished video.
- New setting `keep_work_files` keeps everything, for debugging.
- `qtgmc_ahead` can't go below 2. Restoring a piece needs the next one deinterlaced too, so a smaller
  value would have stalled the restoration (found while testing this update).

**Disk space needed now**, per hour of tape:

| | Before | Now |
|---|---|---|
| Peak while restoring | about 90 GB | about 50 GB |
| Left after Free up space | (no such option) | about 17.5 GB, just the restored video |

The README, BUILDING.md, the in-app setup check and [engine/README.md](engine/README.md#disk-space)
now give these figures.

**Testing**

- Simulated jobs: the deletion rules, a failed piece being redone, a hand-deleted piece being rebuilt,
  progress, the button's refusals, and no stalls at any `qtgmc_ahead` value.
- A real restoration of a 12-second NTSC clip, cut into 4 pieces with `qtgmc_ahead` set to 2:
  - Deinterlacing waited for the graphics card as intended, with at most 3 deinterlaced pieces at once.
  - The deinterlaced and in-progress folders were empty after the finish.
  - Free up space left only the restored video and the logs, and Resume then refused to start.
  - The output had the same 722 frames as a restore of the same clip with 0.1.0, and averaged 42.9 dB
    PSNR against it (lowest frame 41.4 dB, joins between pieces no worse), so the picture is the same.
- Not yet run on a full-length tape.

## 0.1.0

First public version: the desktop app (Windows, NVIDIA), the restoration engine with the tested
home-video tape preset, live progress with pause and resume, a before/after player for finished
pieces, and first-time setup checks.
