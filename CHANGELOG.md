# Changelog

What changed in each update of tapewake, newest first. Updates that haven't been given a version
number yet are listed under **Unreleased**.

## Unreleased

Nothing yet.

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
