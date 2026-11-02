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

## Licence

MIT (see `LICENSE`).
