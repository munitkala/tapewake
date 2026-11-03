// Runs before every app build (tauri.conf.json beforeBuildCommand): deletes Python's __pycache__ folders from
// engine/, so the installer only carries the engine's source files and none of this computer's cache files.
import { readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';

function clean(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (!e.isDirectory()) continue;
    const p = join(dir, e.name);
    if (e.name === '__pycache__') rmSync(p, { recursive: true, force: true });
    else clean(p);
  }
}

clean(new URL('../engine', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'));
