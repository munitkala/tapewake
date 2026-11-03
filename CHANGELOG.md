# Changelog

What changed in each update of tapewake, newest first. Updates that haven't been given a version
number yet are listed under **Unreleased**.

## Unreleased

### Disk space: working files are deleted once nothing needs them (2026-11-02)

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

**Disk space needed now**, per hour of tape:

| | Before | Now |
|---|---|---|
| Peak while restoring | about 90 GB | about 50 GB |
| Left after Free up space | (no such option) | about 17.5 GB, just the restored video |

The README, BUILDING.md, the in-app setup check and [engine/README.md](engine/README.md#disk-space)
now give these figures.

**Testing:** checked on a simulated job (deletion rules, a failed piece being redone, progress, the
button's refusals). It has not yet been run on a full tape.

## 0.1.0

First public version: the desktop app (Windows, NVIDIA), the restoration engine with the tested
home-video tape preset, live progress with pause and resume, a before/after player for finished
pieces, and first-time setup checks.
