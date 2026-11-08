## Roadmap

- [x] **Mid-November 2026**: GPU accessibility improvements (DirectML / ONNX fallback for non-NVIDIA cards, expanded GPU compatibility)
- [ ] **Late November 2026**: Port missing updates from current macOS version back to Windows (thermal throttling check via `nvidia-smi`, performance display sync)
- [ ] **December 2026**: Automated Windows installer (one-click binary setup script for FFmpeg, PyTorch, models, and tools)
- [ ] **Late December 2026**: Automated macOS installer (`.dmg` installer package and auto-config script)

### Future / Unscheduled (When I get to it)
- [ ] Progressive video mode auto-bypass improvements (skipping QTGMC pass on digital/DVD source clips)
- [ ] Configurable output frame rate controls (keep 29.97p/30p option without forcing 59.94p upscaling)
- [ ] Face restoration threshold slider (skip tiny/blurry faces under minimum distance threshold)
- [ ] Per-face restoration toggle (enable/disable AI face reconstruction on specific subjects)
- [ ] Granular audio toggles (independent control for 50/60Hz hum removal, voice isolation, and waveform synthesis)
- [ ] Dynamic temp-file cleanup during active jobs (reduce peak disk usage below 50 GB/hour)
- [ ] Default PAL format presets (50i / 25p color matrix and deinterlacing presets)
- [ ] Linux distribution support (native CLI toolchain & headless runner)


