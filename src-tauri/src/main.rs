// no console window in release builds
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// Just one window with ui/index.html. Everything job related goes through engine/app_bridge.py, which is
// embedded below and run with the engine's Python.
// TODO: tray icon, keep running in the background, first-run setup

use std::path::PathBuf;
use std::process::Command;

use tauri::Manager;

const BRIDGE: &str = include_str!("../../engine/app_bridge.py");

/// %LOCALAPPDATA%\com.tapewake.app (tools.json, jobs, cache)
fn app_data() -> PathBuf {
    let local = std::env::var_os("LOCALAPPDATA").unwrap_or_default();
    PathBuf::from(local).join("com.tapewake.app")
}

/// Python to run the engine with: tools.json "python", or the default install location.
fn engine_python() -> PathBuf {
    let from_tools = std::fs::read_to_string(app_data().join("tools.json"))
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(s.trim_start_matches('\u{feff}')).ok())  // strip BOM (Notepad)
        .and_then(|v| v.get("python").and_then(|p| p.as_str()).map(PathBuf::from));
    from_tools.unwrap_or_else(|| app_data().join("python").join("python.exe"))
}

/// Bundled engine folder, or the one in the repo for dev builds.
fn engine_dir(app: &tauri::AppHandle) -> PathBuf {
    let bundled = app.path().resource_dir().map(|d| d.join("engine")).ok();
    match bundled {
        Some(d) if d.join("tapewake_engine").exists() => d,
        _ => PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("engine"),
    }
}

/// Runs a bridge action and returns its JSON. Only the actions below are allowed, with checked arguments:
/// jobs, tools, status/pause/resume/pieces/cleanup/finish <job>, frame <job> <k> <j>, video <job> <k>, open <job> <what>,
/// delete <job> check|confirm
#[tauri::command]
async fn job(app: tauri::AppHandle, action: String, job: Option<String>, args: Option<Vec<String>>) -> Result<String, String> {
    let args = args.unwrap_or_default();
    let numbers = |n: usize| args.len() == n && args.iter().all(|a| !a.is_empty() && a.len() <= 6 && a.bytes().all(|b| b.is_ascii_digit()));
    let id_ok = job.as_deref().is_some_and(|j| !j.is_empty() && j.len() <= 64 && j.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-'));
    let ok = match action.as_str() {
        "jobs" | "tools" => job.is_none() && args.is_empty(),
        "status" | "pause" | "resume" | "pieces" | "cleanup" | "finish" => id_ok && args.is_empty(),
        "frame" => id_ok && numbers(2),
        "video" => id_ok && numbers(1),
        "open" => id_ok && args.len() == 1 && matches!(args[0].as_str(), "samples" | "output" | "compare" | "log"),
        "delete" => id_ok && args.len() == 1 && matches!(args[0].as_str(), "check" | "confirm"),
        _ => false,
    };
    if !ok {
        return Err(format!("unknown action {action} {job:?} {args:?}"));
    }
    let is_video = action == "video";
    let mut all = vec![action];
    all.extend(job);
    all.extend(args);
    let out = bridge(&app, all).await?;
    // allow the player to load just these three files
    if is_video {
        let v: serde_json::Value = serde_json::from_str(&out).map_err(|e| e.to_string())?;
        let scope = app.asset_protocol_scope();
        for key in ["rest", "orig", "strip"] {
            if let Some(p) = v.get(key).and_then(|p| p.as_str()) {
                scope.allow_file(p).map_err(|e| e.to_string())?;
            }
        }
    }
    Ok(out)
}

/// Run app_bridge.py with `args`, return stdout or the error.
async fn bridge(app: &tauri::AppHandle, args: Vec<String>) -> Result<String, String> {
    let py = engine_python();
    if !py.exists() {
        return Err(format!("tapewake's engine Python was not found ({}). First-run setup installs it; until then set \"python\" in {}.",
                           py.display(), app_data().join("tools.json").display()));
    }
    let engine = engine_dir(app);
    tauri::async_runtime::spawn_blocking(move || {
        let mut cmd = Command::new(py);
        cmd.arg("-c").arg(BRIDGE).args(&args).env("TAPEWAKE_ENGINE", engine);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        }
        let out = cmd.output().map_err(|e| e.to_string())?;
        if out.status.success() {
            Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
        } else {
            let err = String::from_utf8_lossy(&out.stderr);
            // last line of a traceback is the actual message
            Err(err.trim().lines().last().unwrap_or("The engine stopped with an error.").to_string())
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Native open dialog. None if cancelled.
#[tauri::command]
async fn pick_video() -> Option<String> {
    tauri::async_runtime::spawn_blocking(|| {
        rfd::FileDialog::new()
            .set_title("Choose a video to restore")
            .add_filter("Video", &["mkv", "avi", "mp4", "mov", "mpg", "mpeg", "vob", "m2ts", "ts", "dv", "wmv", "m4v"])
            .add_filter("All files", &["*"])
            .pick_file()
            .map(|p| p.display().to_string())
    })
    .await
    .ok()
    .flatten()
}

/// Scan type, frame rate and size of `path`, and which modes can restore it (app_bridge.probe_video).
#[tauri::command]
async fn video_probe(app: tauri::AppHandle, path: String) -> Result<String, String> {
    if !std::path::Path::new(&path).is_file() {
        return Err("That video was not found.".into());
    }
    bridge(&app, vec!["probe".into(), path]).await
}

/// Create a job for `path` with the tested settings. Returns {"id", "name"}.
/// mode: "tape" or "progressive" (None: the one that fits); faces / audio false turn those steps off.
#[tauri::command]
async fn job_create(app: tauri::AppHandle, path: String, faces: Option<bool>, audio: Option<bool>,
                    mode: Option<String>) -> Result<String, String> {
    if !std::path::Path::new(&path).is_file() {
        return Err("That video was not found.".into());
    }
    let mut args = vec!["create".into(), path];
    if faces == Some(false) {
        args.push("--no-faces".into());
    }
    if audio == Some(false) {
        args.push("--no-audio".into());
    }
    match mode.as_deref() {
        None => {}
        Some(m @ ("tape" | "progressive")) => args.extend(["--mode".into(), m.to_string()]),
        Some(m) => return Err(format!("unknown mode {m}")),
    }
    bridge(&app, args).await
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![job, pick_video, job_create, video_probe])
        .run(tauri::generate_context!())
        .expect("error while running tapewake");
}
