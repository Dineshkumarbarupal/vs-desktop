"""FlowCode Studio: local desktop IDE built with Python and Eel.

A local-first starter editor, not a feature-for-feature distribution of Microsoft VS Code.
"""
from __future__ import annotations

import ast
import difflib
import errno
import hashlib
import keyword
from functools import lru_cache
import json
import queue
import time
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import re
import shlex
import signal
from html.parser import HTMLParser
from xml.etree import ElementTree
from urllib.parse import unquote, urlsplit
from pathlib import Path
from typing import Any

try:
    import eel
except ImportError as exc:
    raise SystemExit("Eel is missing. Run run.bat or: python -m pip install -r requirements.txt") from exc

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
SAMPLE_DIR = APP_DIR / "sample_workspace"
MAX_READ_BYTES = 5 * 1024 * 1024
MAX_EDITOR_SESSION_BYTES = 5 * 1024 * 1024
COMMAND_TIMEOUT = 45
TASK_TIMEOUT = 120
_workspace = SAMPLE_DIR.resolve()
_debug_lock = threading.RLock()
_debug_process: subprocess.Popen[str] | None = None
_debug_worker_file: Path | None = None
_debug_events: queue.Queue[dict[str, Any]] = queue.Queue()
_debug_readers_remaining: dict[int, int] = {}
_node_debug_lock = threading.RLock()
_node_debug_session: dict[str, Any] | None = None

_workspace_lock = threading.RLock()
_terminal_lock = threading.RLock()
_terminal_process: Any = None
_terminal_kind = ""
_terminal_pty_fd: int | None = None
_terminal_pid: int | None = None
_terminal_exit_code: int | None = None
_terminal_shell = ""
_terminal_output: queue.Queue[str] = queue.Queue()


def ensure_samples() -> None:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    samples = {
        "README.md": "# FlowCode Studio\n\nA local-first desktop editor built with HTML, CSS, JavaScript, Python and Eel.\n\n- Open a folder from **File → Open Folder**.\n- Click a file in Explorer to edit it.\n- **Ctrl+S** saves; **F5** runs a supported script.\n- **Ctrl+Shift+P** opens the Command Palette.\n\nThis is an original lightweight IDE foundation, not Microsoft VS Code and not a complete replacement for its extension ecosystem.\n",
        "main.py": '"""Sample Python program."""\nfrom datetime import datetime\n\ndef greet(name: str = "developer") -> str:\n    return f"Hello, {name}!"\n\nif __name__ == "__main__":\n    print(greet("FlowCode Studio"))\n    print(f"Run at {datetime.now():%H:%M:%S}")\n',
        "app.js": 'const app = { name: "FlowCode Studio", stack: ["HTML", "CSS", "JavaScript", "Python", "Eel"] };\n\nfunction boot() {\n  console.log(`Welcome to ${app.name}`);\n  console.table(app.stack);\n}\n\nboot();\n',
        "index.html": '<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>Hello, world</title>\n  <link rel="stylesheet" href="styles.css">\n</head>\n<body>\n  <main><h1>Hello, developer 👋</h1><p>Your next idea starts here.</p></main>\n  <script src="app.js"></script>\n</body>\n</html>\n',
        "styles.css": 'body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #111827; color: #e5e7eb; font-family: system-ui, sans-serif; }\nmain { max-width: 42rem; padding: 3rem; }\nh1 { color: #7dd3fc; }\n',
        ".gitignore": "__pycache__/\n.venv/\nnode_modules/\n.env\n*.py[cod]\n",
    }
    for name, content in samples.items():
        path = SAMPLE_DIR / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def workspace_root() -> Path:
    with _workspace_lock:
        return _workspace.resolve()


def safe_path(relative: str | None = None, *, allow_root: bool = False) -> Path:
    base = workspace_root()
    rel = (relative or "").strip()
    lexical = Path(os.path.abspath(base / rel)) if rel else base
    try:
        parts = lexical.relative_to(base).parts
    except ValueError as exc:
        raise ValueError("The requested path is outside the opened workspace.") from exc
    cursor = base
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Symbolic links are not followed by workspace operations.")
    candidate = lexical.resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError("The requested path is outside the opened workspace.") from exc
    if not allow_root and candidate == base:
        raise ValueError("Choose a file or a subfolder inside the workspace.")
    return candidate


def _tree_entry(path: Path, base: Path, depth: int = 0) -> dict[str, Any]:
    stat = path.stat()
    result: dict[str, Any] = {
        "name": path.name or str(path),
        "path": "" if path == base else path.relative_to(base).as_posix(),
        "is_dir": path.is_dir(),
        "size": stat.st_size if path.is_file() else 0,
        "modified": stat.st_mtime,
    }
    if path.is_dir() and depth < 24:
        children = []
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
        except OSError:
            entries = []
        for child in entries:
            if child.name in {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", "dist", "build"}:
                continue
            if child.is_symlink():
                continue
            try:
                children.append(_tree_entry(child, base, depth + 1))
            except OSError:
                continue
        result["children"] = children
    return result


REPLACE_TEXT_EXTENSIONS = {".py", ".js", ".cjs", ".mjs", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css", ".scss", ".json", ".md", ".markdown", ".txt", ".yml", ".yaml", ".toml", ".sh", ".bash", ".bat", ".cmd", ".ps1", ".sql", ".xml", ".svg", ".ini", ".cfg", ".conf", ".log", ".csv", ".gitignore", ".editorconfig"}


def _apply_workspace_text_changes(changes: list[dict[str, Any]], allowed_extensions: set[str] | None = None, max_files: int = 100) -> dict[str, Any]:
    """Atomically stage and apply a bounded UTF-8 text batch, rolling back if a replace fails."""
    if not isinstance(changes, list) or not changes or len(changes) > max_files:
        return {"error": f"The change batch is empty or exceeds the {max_files}-file limit."}
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_bytes = 0
    try:
        for item in changes:
            if not isinstance(item, dict):
                return {"error": "Malformed workspace change batch."}
            relative = str(item.get("path", ""))
            if relative in seen:
                return {"error": f"Duplicate file in change batch: {relative}"}
            seen.add(relative)
            target = safe_path(relative)
            is_known_dotfile = target.name in {".gitignore", ".editorconfig"}
            if not target.is_file() or (allowed_extensions and target.suffix.lower() not in allowed_extensions and not is_known_dotfile):
                return {"error": f"Unsupported or missing text file: {relative}"}
            if target.stat().st_size > MAX_READ_BYTES:
                return {"error": f"{relative} exceeds the {MAX_READ_BYTES // (1024 * 1024)} MB edit limit."}
            original = target.read_bytes()
            expected = str(item.get("original_sha256", ""))
            actual = hashlib.sha256(original).hexdigest()
            if not expected or actual != expected:
                return {"error": f"{relative} changed on disk after the preview. Refresh the preview before applying."}
            new_content = item.get("new_content")
            if not isinstance(new_content, str):
                return {"error": f"Malformed replacement output for {relative}."}
            new_bytes = new_content.encode("utf-8")
            if len(new_bytes) > MAX_READ_BYTES:
                return {"error": f"Replacement output for {relative} exceeds the file-size limit."}
            total_bytes += len(new_bytes)
            if total_bytes > 12 * 1024 * 1024:
                return {"error": "The replacement batch exceeds the 12 MB safety limit."}
            plan.append({"path": target, "relative": relative, "old_bytes": original,
                         "new_bytes": new_bytes, "mode": target.stat().st_mode & 0o777,
                         "new_temp": None, "backup_temp": None, "committed": False,
                         "original_sha256": expected})
        for item in plan:
            for field, data, prefix in (("new_temp", item["new_bytes"], ".flowcode-new-"),
                                        ("backup_temp", item["old_bytes"], ".flowcode-backup-")):
                with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(item["path"].parent), prefix=prefix) as fh:
                    fh.write(data); fh.flush(); os.fsync(fh.fileno())
                    item[field] = fh.name
                os.chmod(item[field], item["mode"])
        try:
            for item in plan:
                current_hash = hashlib.sha256(item["path"].read_bytes()).hexdigest()
                if current_hash != item["original_sha256"]:
                    raise RuntimeError(f"{item['relative']} changed while applying the batch.")
                os.replace(item["new_temp"], item["path"])
                item["new_temp"] = None
                item["committed"] = True
        except Exception:
            for item in reversed(plan):
                if item["committed"] and item["backup_temp"]:
                    try:
                        os.replace(item["backup_temp"], item["path"])
                        item["backup_temp"] = None
                    except OSError:
                        pass
            raise
        return {"ok": True, "updated": [{"path": item["relative"], "size": len(item["new_bytes"]), "sha256": hashlib.sha256(item["new_bytes"]).hexdigest()} for item in plan]}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        for item in plan:
            for field in ("new_temp", "backup_temp"):
                temp_path = item.get(field)
                if temp_path:
                    try: Path(temp_path).unlink(missing_ok=True)
                    except OSError: pass


@eel.expose
def preview_workspace_replace(query: str, replacement: str, case_sensitive: bool = False) -> dict[str, Any]:
    """Preview a literal workspace-wide replacement without writing any files."""
    term = query if isinstance(query, str) else str(query or "")
    new_text = replacement if isinstance(replacement, str) else str(replacement or "")
    if not term:
        return {"error": "Enter text to find before previewing replacements."}
    if len(term) > 500 or len(new_text) > 5000:
        return {"error": "Find text is limited to 500 characters and replacement text to 5,000 characters."}
    base = workspace_root()
    changes: list[dict[str, Any]] = []
    total_matches = 0
    total_bytes = 0
    ignored = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", "dist", "build"}
    matcher = re.compile(re.escape(term), 0 if case_sensitive else re.IGNORECASE)
    try:
        for folder, dirs, names in os.walk(base):
            dirs[:] = [name for name in dirs if name not in ignored and not name.startswith(".")]
            for name in names:
                target = Path(folder) / name
                if target.is_symlink() or (target.suffix.lower() not in REPLACE_TEXT_EXTENSIONS and name not in {".gitignore", ".editorconfig"}):
                    continue
                relative = target.relative_to(base).as_posix()
                try:
                    safe = safe_path(relative)
                    if not safe.is_file() or safe.stat().st_size > MAX_READ_BYTES:
                        continue
                    original_bytes = safe.read_bytes()
                    original = original_bytes.decode("utf-8")
                except (OSError, UnicodeDecodeError, ValueError):
                    continue
                new_content, count = matcher.subn(lambda _m: new_text, original)
                if not count:
                    continue
                total_matches += count
                encoded = new_content.encode("utf-8")
                total_bytes += len(encoded)
                if len(changes) >= 100 or total_bytes > 12 * 1024 * 1024:
                    return {"error": "Replacement preview exceeds the limit (100 files / 12 MB). Narrow your search first."}
                diff = "".join(difflib.unified_diff(original.splitlines(keepends=True), new_content.splitlines(keepends=True),
                    fromfile=relative, tofile=relative, n=2))
                changes.append({"path": relative, "new_content": new_content, "diff": diff,
                    "matches": count, "original_sha256": hashlib.sha256(original_bytes).hexdigest()})
    except Exception as exc:
        return {"error": str(exc), "changes": []}
    return {"changes": changes, "files_changed": len(changes), "matches": total_matches,
            "query": term, "replacement": new_text, "case_sensitive": bool(case_sensitive), "preview_only": True}


@eel.expose
def apply_workspace_replace(changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply a previously reviewed workspace replacement batch after hash checks."""
    return _apply_workspace_text_changes(changes, REPLACE_TEXT_EXTENSIONS, max_files=100)


def _execute(args: list[str], *, cwd: Path | None = None, timeout: int = COMMAND_TIMEOUT,
             extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        cp = subprocess.run(args, cwd=str(cwd or workspace_root()), capture_output=True,
                            text=True, errors="replace", timeout=timeout, env=env)
        return {"stdout": cp.stdout[-50000:], "stderr": cp.stderr[-50000:],
                "returncode": cp.returncode, "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return {"stdout": stdout[-50000:],
                "stderr": (stderr + f"\nStopped after {timeout} seconds.")[-50000:],
                "returncode": -1, "timed_out": True}
    except FileNotFoundError as exc:
        return {"error": f"Required command not found: {exc.filename}"}
    except Exception as exc:
        return {"error": str(exc)}


def _user_state_dir() -> Path:
    """Return the per-user, cross-launch state directory (not tied to Eel's random port)."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return root / "flowcode-studio"


def _user_settings_file() -> Path:
    return _user_state_dir() / "settings.json"


def _write_private_json(target: Path, payload: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    temporary: str | None = None
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > max_bytes:
            return {"error": f"Saved settings exceed the {max_bytes // (1024 * 1024)} MB size limit."}
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.parent.chmod(0o700)
        except OSError:
            pass
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(target.parent), prefix=".flowcode-", suffix=".tmp") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = handle.name
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, target)
        temporary = None
        return {"ok": True, "bytes": len(encoded)}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


@eel.expose
def load_user_settings() -> dict[str, Any]:
    """Load small UI preferences and last workspace across app launches."""
    try:
        path = _user_settings_file()
        if not path.is_file():
            return {"settings": {}}
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            return {"settings": {}, "warning": "Saved settings exceeded their size limit and were ignored."}
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            return {"settings": {}}
        safe: dict[str, Any] = {}
        theme = value.get("theme")
        if isinstance(theme, str) and theme in {"light", "dark"}:
            safe["theme"] = theme
        if isinstance(value.get("fontSize"), (int, float)) and 8 <= float(value["fontSize"]) <= 32:
            safe["fontSize"] = float(value["fontSize"])
        for key in ("wordWrap", "autoSave", "minimap"):
            if isinstance(value.get(key), bool):
                safe[key] = value[key]
        if isinstance(value.get("autoSaveDelay"), int) and 250 <= value["autoSaveDelay"] <= 10000:
            safe["autoSaveDelay"] = value["autoSaveDelay"]
        last = value.get("lastWorkspacePath")
        if isinstance(last, str) and last:
            candidate = Path(last).expanduser()
            try:
                if candidate.is_dir():
                    safe["lastWorkspacePath"] = str(candidate.resolve())
            except OSError:
                pass
        return {"settings": safe}
    except Exception as exc:
        return {"settings": {}, "warning": f"Saved settings could not be loaded: {exc}"}


@eel.expose
def save_user_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Validate and atomically persist supported preferences for the current user."""
    if not isinstance(settings, dict):
        return {"error": "Settings must be an object."}
    safe: dict[str, Any] = {}
    if "theme" in settings:
        if not isinstance(settings["theme"], str) or settings["theme"] not in {"light", "dark"}:
            return {"error": "Theme must be light or dark."}
        safe["theme"] = settings["theme"]
    if "fontSize" in settings:
        size = settings["fontSize"]
        if isinstance(size, bool) or not isinstance(size, (int, float)) or not 8 <= float(size) <= 32:
            return {"error": "Font size must be between 8 and 32 pixels."}
        safe["fontSize"] = float(size)
    for key in ("wordWrap", "autoSave", "minimap"):
        if key in settings:
            if not isinstance(settings[key], bool):
                return {"error": f"{key} must be true or false."}
            safe[key] = settings[key]
    if "autoSaveDelay" in settings:
        delay = settings["autoSaveDelay"]
        if isinstance(delay, bool) or not isinstance(delay, int) or not 250 <= delay <= 10000:
            return {"error": "Auto-save delay must be from 250 to 10000 milliseconds."}
        safe["autoSaveDelay"] = delay
    if "lastWorkspacePath" in settings:
        last = settings["lastWorkspacePath"]
        if not isinstance(last, str):
            return {"error": "Last workspace path must be a directory path."}
        if not last:
            safe["lastWorkspacePath"] = ""
        else:
            candidate = Path(last).expanduser()
            if not candidate.is_dir():
                return {"error": "Last workspace directory no longer exists."}
            safe["lastWorkspacePath"] = str(candidate.resolve())
    return _write_private_json(_user_settings_file(), safe, 1024 * 1024)



# A small, deliberately bounded subset of VS Code-compatible workspace settings.
_WORKSPACE_SETTING_KEYS = {
    "editor.fontSize", "editor.wordWrap", "editor.minimap.enabled",
    "files.autoSave", "files.autoSaveDelay", "editor.insertSpaces", "editor.tabSize",
}
_WORKSPACE_SETTINGS_MAX_BYTES = 256 * 1024


def _workspace_settings_file() -> Path:
    return safe_path(".vscode/settings.json")


def _read_workspace_settings_raw() -> tuple[Path, dict[str, Any]]:
    path = _workspace_settings_file()
    if not path.exists():
        return path, {}
    if not path.is_file():
        raise ValueError(".vscode/settings.json is not a regular file.")
    raw = path.read_bytes()
    if len(raw) > _WORKSPACE_SETTINGS_MAX_BYTES:
        raise ValueError("Workspace settings exceed the 256 KB size limit.")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Workspace settings JSON is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Workspace settings JSON must contain an object.")
    return path, value


def _validated_workspace_setting(key: str, value: Any) -> Any:
    if key == "editor.fontSize":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 8 <= float(value) <= 32:
            raise ValueError("editor.fontSize must be a number from 8 to 32.")
        return int(value) if float(value).is_integer() else float(value)
    if key == "editor.wordWrap":
        if not isinstance(value, str) or value not in {"on", "off"}:
            raise ValueError("editor.wordWrap must be 'on' or 'off'.")
        return value
    if key == "editor.minimap.enabled":
        if not isinstance(value, bool):
            raise ValueError("editor.minimap.enabled must be true or false.")
        return value
    if key == "files.autoSave":
        if not isinstance(value, str) or value not in {"off", "afterDelay"}:
            raise ValueError("files.autoSave must be 'off' or 'afterDelay'.")
        return value
    if key == "files.autoSaveDelay":
        if isinstance(value, bool) or not isinstance(value, int) or not 250 <= value <= 10000:
            raise ValueError("files.autoSaveDelay must be an integer from 250 to 10000 milliseconds.")
        return value
    if key == "editor.insertSpaces":
        if not isinstance(value, bool):
            raise ValueError("editor.insertSpaces must be true or false.")
        return value
    if key == "editor.tabSize":
        if isinstance(value, bool) or not isinstance(value, int) or value not in {2, 4, 8}:
            raise ValueError("editor.tabSize must be 2, 4 or 8.")
        return value
    raise ValueError(f"Unsupported workspace setting: {key}")


@eel.expose
def load_workspace_settings() -> dict[str, Any]:
    """Read recognized per-workspace settings from .vscode/settings.json safely."""
    try:
        with _workspace_lock:
            path, raw = _read_workspace_settings_raw()
            safe: dict[str, Any] = {}
            warnings: list[str] = []
            for key in _WORKSPACE_SETTING_KEYS:
                if key not in raw:
                    continue
                try:
                    safe[key] = _validated_workspace_setting(key, raw[key])
                except ValueError as exc:
                    warnings.append(str(exc))
            return {"settings": safe, "file_exists": path.is_file(), "settings_path": ".vscode/settings.json",
                    "warnings": warnings}
    except Exception as exc:
        return {"settings": {}, "file_exists": False, "settings_path": ".vscode/settings.json", "error": str(exc)}


@eel.expose
def save_workspace_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Atomically save known overrides while preserving unrelated workspace settings."""
    if not isinstance(settings, dict):
        return {"error": "Workspace settings must be an object."}
    if any(not isinstance(key, str) or key not in _WORKSPACE_SETTING_KEYS for key in settings):
        return {"error": "Workspace settings contain an unsupported key."}
    safe: dict[str, Any] = {}
    try:
        for key, value in settings.items():
            if value is not None:
                safe[key] = _validated_workspace_setting(key, value)
        with _workspace_lock:
            path, raw = _read_workspace_settings_raw()
            # Only keys managed by this UI are replaced; third-party settings survive.
            for key in _WORKSPACE_SETTING_KEYS:
                raw.pop(key, None)
            raw.update(safe)
            encoded = (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if len(encoded) > _WORKSPACE_SETTINGS_MAX_BYTES:
                return {"error": "Workspace settings exceed the 256 KB size limit."}
            # Re-check after directory creation to reject a symlinked .vscode folder.
            parent = path.parent
            parent.mkdir(parents=True, exist_ok=True)
            path = _workspace_settings_file()
            if path.exists() and (path.is_symlink() or not path.is_file()):
                return {"error": "Workspace settings path must be a regular file, not a symbolic link."}
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(parent), prefix=".settings-", suffix=".tmp") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                    temporary = handle.name
                try:
                    os.chmod(temporary, 0o644)
                except OSError:
                    pass
                os.replace(temporary, path)
                temporary = None
            finally:
                if temporary:
                    try:
                        Path(temporary).unlink(missing_ok=True)
                    except OSError:
                        pass
            return {"ok": True, "settings": safe, "settings_path": ".vscode/settings.json", "bytes": len(encoded)}
    except Exception as exc:
        return {"error": str(exc)}

def _editor_session_file() -> Path:
    workspace_id = hashlib.sha256(str(workspace_root()).encode("utf-8")).hexdigest()
    return _user_state_dir() / "sessions" / f"{workspace_id}.json"


def _valid_session_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 1024:
        return False
    if value.startswith("/") or "\\" in value or "\0" in value or (len(value) > 1 and value[1] == ":"):
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _clean_session_breakpoint_conditions(raw: Any, breakpoints: dict[str, list[int]]) -> dict[str, dict[str, str]]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or len(raw) > 200:
        raise ValueError("Editor session contains invalid conditional breakpoint data.")
    clean: dict[str, dict[str, str]] = {}
    for file_name, rows in raw.items():
        if not _valid_session_relative_path(file_name) or not isinstance(rows, dict) or len(rows) > 2000:
            raise ValueError("Editor session contains invalid conditional breakpoint data.")
        if file_name not in breakpoints:
            raise ValueError("Conditional breakpoints must reference a file with saved breakpoints.")
        file_conditions: dict[str, str] = {}
        for raw_line, expression in rows.items():
            if not isinstance(raw_line, str) or not raw_line.isdigit():
                raise ValueError("Conditional breakpoint lines must be positive integers.")
            line = int(raw_line)
            if line not in breakpoints[file_name]:
                raise ValueError("A conditional breakpoint must reference an enabled breakpoint line.")
            if not isinstance(expression, str) or not expression.strip() or len(expression) > 300 or any(ch in expression for ch in "\r\n\0"):
                raise ValueError("Conditional breakpoint expressions must be 1–300 characters on one line.")
            try:
                compile(expression, "<breakpoint condition>", "eval")
            except (SyntaxError, ValueError) as exc:
                raise ValueError(f"Invalid conditional breakpoint expression on line {line}: {exc}") from exc
            file_conditions[str(line)] = expression.strip()
        if file_conditions:
            clean[file_name] = file_conditions
    return clean


@eel.expose
def load_editor_session() -> dict[str, Any]:
    """Load the last open tabs and unsaved drafts for the currently active workspace."""
    path = _editor_session_file()
    try:
        if not path.is_file():
            return {"session": None}
        raw = path.read_bytes()
        if len(raw) > MAX_EDITOR_SESSION_BYTES:
            return {"session": None, "warning": "The saved editor session exceeded its size limit and was ignored."}
        state = json.loads(raw.decode("utf-8"))
        if not isinstance(state, dict) or state.get("version") != 1 or state.get("workspacePath") != str(workspace_root()):
            return {"session": None}
        open_paths = state.get("openPaths")
        drafts = state.get("drafts", {})
        if not isinstance(open_paths, list) or not isinstance(drafts, dict):
            return {"session": None}
        if len(open_paths) > 40 or any(not _valid_session_relative_path(item) for item in open_paths):
            return {"session": None}
        safe_drafts: dict[str, Any] = {}
        for name, draft in drafts.items():
            if name not in open_paths or not _valid_session_relative_path(name) or not isinstance(draft, dict):
                continue
            content, saved = draft.get("content"), draft.get("saved")
            saved_hash = draft.get("savedHash")
            if (isinstance(content, str) and isinstance(saved, str)
                    and len(content.encode("utf-8")) <= MAX_READ_BYTES
                    and len(saved.encode("utf-8")) <= MAX_READ_BYTES
                    and (saved_hash is None or isinstance(saved_hash, str) and len(saved_hash) <= 128)):
                safe_drafts[name] = {"content": content, "saved": saved, "savedHash": saved_hash,
                                     "openedAt": draft.get("openedAt", 0)}
        raw_breakpoints = state.get("breakpoints", {})
        clean_breakpoints: dict[str, list[int]] = {}
        if isinstance(raw_breakpoints, dict):
            for file_name, lines in list(raw_breakpoints.items())[:200]:
                if not _valid_session_relative_path(file_name) or not isinstance(lines, list):
                    continue
                clean_lines = sorted({line for line in lines[:2000] if isinstance(line, int) and not isinstance(line, bool) and 0 < line <= 10000000})
                if clean_lines:
                    clean_breakpoints[file_name] = clean_lines
        try:
            clean_conditions = _clean_session_breakpoint_conditions(state.get("breakpointConditions", {}), clean_breakpoints)
        except ValueError as exc:
            return {"session": None, "warning": str(exc)}
        raw_watches = state.get("debugWatches", [])
        clean_watches = [watch.strip() for watch in raw_watches[:30] if isinstance(watch, str) and watch.strip() and len(watch) <= 500] if isinstance(raw_watches, list) else []
        clean = {"version": 1, "workspacePath": str(workspace_root()),
                 "openPaths": open_paths, "activePath": state.get("activePath") if state.get("activePath") in open_paths else None,
                 "drafts": safe_drafts, "breakpoints": clean_breakpoints, "breakpointConditions": clean_conditions, "debugWatches": clean_watches,
                 "updatedAt": state.get("updatedAt", 0)}
        return {"session": clean}
    except Exception as exc:
        return {"session": None, "warning": f"Saved editor session could not be loaded: {exc}"}


@eel.expose
def save_editor_session(state: dict[str, Any]) -> dict[str, Any]:
    """Atomically store bounded editor-recovery state outside the source workspace."""
    temporary: str | None = None
    try:
        if not isinstance(state, dict) or state.get("version") != 1:
            return {"error": "Invalid editor-session payload."}
        if state.get("workspacePath") != str(workspace_root()):
            return {"error": "Editor-session workspace did not match the active workspace."}
        open_paths, drafts = state.get("openPaths"), state.get("drafts", {})
        if not isinstance(open_paths, list) or len(open_paths) > 40 or any(not _valid_session_relative_path(x) for x in open_paths):
            return {"error": "Editor session contains an invalid list of open files."}
        if len(set(open_paths)) != len(open_paths) or not isinstance(drafts, dict):
            return {"error": "Editor session contains duplicate paths or invalid draft data."}
        clean_drafts: dict[str, Any] = {}
        for name, draft in drafts.items():
            if name not in open_paths or not _valid_session_relative_path(name) or not isinstance(draft, dict):
                return {"error": "Editor session contains a draft outside the open-file list."}
            content, saved = draft.get("content"), draft.get("saved")
            saved_hash = draft.get("savedHash")
            if not isinstance(content, str) or not isinstance(saved, str):
                return {"error": "Editor session draft content must be text."}
            if len(content.encode("utf-8")) > MAX_READ_BYTES or len(saved.encode("utf-8")) > MAX_READ_BYTES:
                return {"error": "A recovery draft exceeds the 5 MB file limit."}
            if saved_hash is not None and (not isinstance(saved_hash, str) or len(saved_hash) > 128):
                return {"error": "Editor session contains an invalid disk hash."}
            clean_drafts[name] = {"content": content, "saved": saved, "savedHash": saved_hash,
                                  "openedAt": draft.get("openedAt", 0)}
        raw_breakpoints = state.get("breakpoints", {})
        if not isinstance(raw_breakpoints, dict) or len(raw_breakpoints) > 200:
            return {"error": "Editor session contains invalid breakpoint data."}
        clean_breakpoints: dict[str, list[int]] = {}
        for file_name, lines in raw_breakpoints.items():
            if not _valid_session_relative_path(file_name) or not isinstance(lines, list) or len(lines) > 2000:
                return {"error": "Editor session contains invalid breakpoint data."}
            if any(isinstance(line, bool) or not isinstance(line, int) or not 0 < line <= 10000000 for line in lines):
                return {"error": "Editor session contains invalid breakpoint line numbers."}
            clean_breakpoints[file_name] = sorted(set(lines))
        try:
            clean_conditions = _clean_session_breakpoint_conditions(state.get("breakpointConditions", {}), clean_breakpoints)
        except ValueError as exc:
            return {"error": str(exc)}
        raw_watches = state.get("debugWatches", [])
        if not isinstance(raw_watches, list) or len(raw_watches) > 30 or any(not isinstance(watch, str) or not watch.strip() or len(watch) > 500 for watch in raw_watches):
            return {"error": "Editor session contains invalid debug watch expressions."}
        clean_watches = [watch.strip() for watch in raw_watches]
        clean = {"version": 1, "workspacePath": str(workspace_root()), "openPaths": open_paths,
                 "activePath": state.get("activePath") if state.get("activePath") in open_paths else None,
                 "drafts": clean_drafts, "breakpoints": clean_breakpoints, "breakpointConditions": clean_conditions, "debugWatches": clean_watches,
                 "updatedAt": state.get("updatedAt", 0)}
        encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_EDITOR_SESSION_BYTES:
            return {"error": "Editor session exceeds the 5 MB recovery-storage limit."}
        target = _editor_session_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.parent.chmod(0o700)
        except OSError:
            pass
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(target.parent), prefix=".session-", suffix=".tmp") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = handle.name
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, target)
        temporary = None
        return {"ok": True, "bytes": len(encoded)}
    except Exception as exc:
        return {"error": f"Could not save editor recovery state: {exc}"}
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


@eel.expose
def get_app_info() -> dict[str, Any]:
    return {"name": "FlowCode Studio", "version": "0.34.0", "python": platform.python_version(),
            "platform": platform.platform(), "workspace": str(workspace_root())}


@eel.expose
def get_workspace() -> dict[str, Any]:
    base = workspace_root()
    return {"path": str(base), "name": base.name or str(base), "tree": _tree_entry(base, base),
            "sample": base == SAMPLE_DIR.resolve()}


@eel.expose
def set_workspace(path: str) -> dict[str, Any]:
    global _workspace
    try:
        chosen = Path(path).expanduser().resolve()
        if not chosen.is_dir():
            return {"error": "The selected folder does not exist."}
        with _workspace_lock:
            _workspace = chosen
        return {"cancelled": False, **get_workspace()}
    except Exception as exc:
        return {"error": str(exc)}


@eel.expose
def pick_workspace() -> dict[str, Any]:
    try:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk()
        window.withdraw()
        try:
            window.attributes("-topmost", True)
        except Exception:
            pass
        chosen = filedialog.askdirectory(title="Open workspace folder", initialdir=str(workspace_root()))
        window.destroy()
        if not chosen:
            return {"cancelled": True}
        return set_workspace(chosen)
    except Exception as exc:
        return {"error": f"Folder picker failed: {exc}"}


@eel.expose
def pick_file() -> dict[str, Any]:
    """Choose a file that belongs to the currently opened workspace."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk(); window.withdraw()
        try: window.attributes("-topmost", True)
        except Exception: pass
        chosen = filedialog.askopenfilename(title="Open File", initialdir=str(workspace_root()))
        window.destroy()
        if not chosen: return {"cancelled": True}
        path = Path(os.path.abspath(Path(chosen).expanduser()))
        relative = path.relative_to(workspace_root()).as_posix()
        safe_path(relative)
        if not path.is_file(): return {"error": "Choose an existing file."}
        return {"cancelled": False, "path": relative}
    except ValueError:
        return {"error": "Choose a file inside the opened workspace."}
    except Exception as exc:
        return {"error": f"Open file dialog failed: {exc}"}


@eel.expose
def pick_save_path(suggested_path: str = "") -> dict[str, Any]:
    """Choose a Save As destination constrained to the current workspace."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        initial = Path(suggested_path or "new-file.txt")
        initialdir = safe_path(initial.parent.as_posix(), allow_root=True) if initial.parent.as_posix() not in {".", ""} else workspace_root()
        window = tk.Tk(); window.withdraw()
        try: window.attributes("-topmost", True)
        except Exception: pass
        chosen = filedialog.asksaveasfilename(title="Save As", initialdir=str(initialdir), initialfile=initial.name,
            defaultextension=initial.suffix, filetypes=[("All files", "*.*"), ("Python", "*.py"), ("JavaScript", "*.js"), ("HTML", "*.html"), ("Text", "*.txt")])
        window.destroy()
        if not chosen: return {"cancelled": True}
        target = Path(os.path.abspath(Path(chosen).expanduser()))
        relative = target.relative_to(workspace_root()).as_posix()
        # safe_path also rejects symbolic links anywhere in the destination path.
        safe_path(relative)
        return {"cancelled": False, "path": relative}
    except ValueError:
        return {"error": "Save As destination must stay inside the opened workspace."}
    except Exception as exc:
        return {"error": f"Save As dialog failed: {exc}"}


@eel.expose
def read_file(relative_path: str) -> dict[str, Any]:
    try:
        path = safe_path(relative_path)
        if not path.is_file():
            return {"error": "The selected path is not a file."}
        raw = path.read_bytes()
        if len(raw) > MAX_READ_BYTES:
            return {"error": f"File exceeds the {MAX_READ_BYTES // (1024 * 1024)} MB editor limit."}
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {"error": "This appears to be a binary file; text editing is unavailable."}
        stat = path.stat()
        return {"path": path.relative_to(workspace_root()).as_posix(), "content": content,
                "size": len(raw), "modified": stat.st_mtime, "sha256": hashlib.sha256(raw).hexdigest()}
    except Exception as exc:
        return {"error": str(exc)}


@eel.expose
def save_file(relative_path: str, content: str, expected_sha256: str | None = None, force: bool = False) -> dict[str, Any]:
    """Atomically save UTF-8 editor text, optionally refusing to overwrite external edits."""
    temporary: str | None = None
    try:
        path = safe_path(relative_path)
        source = content if isinstance(content, str) else str(content)
        encoded = source.encode("utf-8")
        if len(encoded) > MAX_READ_BYTES:
            return {"error": f"Files saved by the editor are limited to {MAX_READ_BYTES // (1024 * 1024)} MB."}
        if path.exists() and path.is_dir():
            return {"error": "Cannot write text content to a directory."}
        existed = path.is_file()
        if expected_sha256 is not None and not force:
            if not existed:
                return {"error": "The file was removed or replaced outside FlowCode. Compare, reload, or explicitly overwrite it."}
            current = hashlib.sha256(path.read_bytes()).hexdigest()
            if current != expected_sha256:
                return {"error": "The file changed on disk since it was opened. Your editor buffer was not saved; compare, reload or explicitly overwrite it."}
        path.parent.mkdir(parents=True, exist_ok=True)
        # Resolve and revalidate after creating parents to reject newly introduced symlinks.
        path = safe_path(relative_path)
        old_mode = path.stat().st_mode & 0o777 if path.is_file() else None
        if old_mode is None:
            current_umask = os.umask(0)
            os.umask(current_umask)
            old_mode = 0o666 & ~current_umask
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(path.parent), prefix=".flowcode-save-") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
            temporary = fh.name
        os.chmod(temporary, old_mode)
        if expected_sha256 is not None and not force:
            latest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
            if latest != expected_sha256:
                return {"error": "The file changed while saving. The new buffer was not written; compare, reload or overwrite explicitly."}
        os.replace(temporary, path)
        temporary = None
        stat = path.stat()
        return {"ok": True, "path": path.relative_to(workspace_root()).as_posix(),
                "size": stat.st_size, "modified": stat.st_mtime, "sha256": hashlib.sha256(encoded).hexdigest()}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if temporary:
            try: Path(temporary).unlink(missing_ok=True)
            except OSError: pass


@eel.expose
def create_entry(parent_path: str, name: str, is_dir: bool = False) -> dict[str, Any]:
    try:
        name = (name or "").strip()
        if not name or name in {".", ".."} or any(c in name for c in ("/", "\\", "\0")):
            return {"error": "Enter a valid name without path separators."}
        parent = safe_path(parent_path, allow_root=True)
        if not parent.is_dir():
            return {"error": "The parent directory does not exist."}
        target = (parent / name).resolve(strict=False)
        target.relative_to(workspace_root())
        if target.exists():
            return {"error": f"'{name}' already exists."}
        if is_dir:
            target.mkdir()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
        return {"ok": True, "entry": _tree_entry(target, workspace_root())}
    except Exception as exc:
        return {"error": str(exc)}


@eel.expose
def rename_entry(relative_path: str, new_name: str) -> dict[str, Any]:
    try:
        name = (new_name or "").strip()
        if not name or name in {".", ".."} or any(c in name for c in ("/", "\\", "\0")):
            return {"error": "Enter a valid file or folder name."}
        old = safe_path(relative_path)
        new = (old.parent / name).resolve(strict=False)
        new.relative_to(workspace_root())
        if new.exists():
            return {"error": "An item with that name already exists."}
        old.rename(new)
        return {"ok": True, "old_path": relative_path, "path": new.relative_to(workspace_root()).as_posix(), "name": new.name}
    except Exception as exc:
        return {"error": str(exc)}


@eel.expose
def delete_entry(relative_path: str) -> dict[str, Any]:
    try:
        path = safe_path(relative_path)
        if path.name in {".git", "node_modules", ".venv", "venv"}:
            return {"error": "Deleting repository/dependency folders is blocked."}
        if path.is_dir():
            import shutil
            shutil.rmtree(path)
        else:
            path.unlink()
        return {"ok": True, "path": relative_path}
    except Exception as exc:
        return {"error": str(exc)}


@eel.expose
def search_workspace(query: str, include_content: bool = True) -> dict[str, Any]:
    term = (query or "").strip()
    if not term:
        return {"results": []}
    base = workspace_root()
    results: list[dict[str, Any]] = []
    ignored = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
    text_exts = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json", ".md", ".txt", ".yml", ".yaml", ".toml", ".sh", ".bat", ".sql", ".xml", ".ps1"}
    try:
        for folder, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ignored and not d.startswith(".")]
            for name in files:
                path = Path(folder) / name
                if path.is_symlink():
                    continue
                relative = path.relative_to(base).as_posix()
                if term.casefold() in name.casefold():
                    results.append({"path": relative, "name": name, "kind": "file"})
                if include_content and path.suffix.lower() in text_exts and len(results) < 300:
                    try:
                        if path.stat().st_size <= 750_000:
                            for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                                if term.casefold() in line.casefold():
                                    results.append({"path": relative, "name": name, "kind": "match", "line": line_no, "preview": line.strip()[:220]})
                                    if len(results) >= 300:
                                        break
                    except OSError:
                        pass
                if len(results) >= 300:
                    break
            if len(results) >= 300:
                break
    except Exception as exc:
        return {"error": str(exc), "results": []}
    return {"results": results[:300]}

_SYMBOL_EXTENSIONS = {".py", ".js", ".cjs", ".mjs", ".jsx", ".ts", ".tsx"}
_SYMBOL_IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", "coverage", ".next", ".pytest_cache", ".mypy_cache"}


def _symbols_from_python(source: str, relative: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source, filename=relative)
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return found

    def walk(body: list[ast.stmt], parents: list[str]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                found.append({"name": name, "kind": "class" if isinstance(node, ast.ClassDef) else ("async function" if isinstance(node, ast.AsyncFunctionDef) else "function"),
                              "path": relative, "line": int(getattr(node, "lineno", 1)),
                              "container": ".".join(parents), "qualified_name": ".".join([*parents, name])})
                nested = getattr(node, "body", None)
                if isinstance(nested, list):
                    walk(nested, [*parents, name])
            elif not parents and isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id.isidentifier() and not target.id.startswith("_"):
                        found.append({"name": target.id, "kind": "variable", "path": relative,
                                      "line": int(getattr(node, "lineno", 1)), "container": "", "qualified_name": target.id})
            elif isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try, ast.Match)):
                # Visit nested function/class declarations inside conditionals without inventing scope names.
                for field_name in ("body", "orelse", "finalbody"):
                    value = getattr(node, field_name, None)
                    if isinstance(value, list):
                        walk(value, parents)
                for handler in getattr(node, "handlers", []) or []:
                    walk(getattr(handler, "body", []), parents)
    walk(tree.body, [])
    return found


def _symbols_from_javascript(source: str, relative: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    # This is intentionally a lightweight declaration scanner, not a JavaScript parser.
    patterns = [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)", re.M), "class"),
        (re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)", re.M), "function"),
        (re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?interface\s+([A-Za-z_$][\w$]*)", re.M), "interface"),
        (re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?type\s+([A-Za-z_$][\w$]*)\s*=", re.M), "type"),
        (re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?enum\s+([A-Za-z_$][\w$]*)", re.M), "enum"),
        (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*(?:async\s*)?(?:function\b|(?:\([^\n]*?\)|[A-Za-z_$][\w$]*)\s*=>)", re.M), "function"),
        (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\b", re.M), "variable"),
    ]
    source_lines = source.splitlines()
    occupied: set[tuple[int, str]] = set()
    for regex, kind in patterns:
        for match in regex.finditer(source):
            name = match.group(1)
            line = source.count("\n", 0, match.start()) + 1
            key = (line, name)
            if key in occupied or name in {"default", "constructor"}:
                continue
            occupied.add(key)
            found.append({"name": name, "kind": kind, "path": relative, "line": line,
                          "container": "", "qualified_name": name})
    found.sort(key=lambda item: (item["line"], item["name"].casefold(), item["kind"]))
    return found


@eel.expose
def workspace_symbols(unsaved_buffers: dict[str, str] | None = None) -> dict[str, Any]:
    """Find navigable Python and JS/TS declarations in this workspace with bounded, safe I/O."""
    base = workspace_root()
    buffers = unsaved_buffers if isinstance(unsaved_buffers, dict) else {}
    if len(buffers) > 40:
        return {"error": "Go to Symbol accepts at most 40 open editor buffers.", "symbols": []}
    drafts: dict[str, str] = {}
    draft_bytes = 0
    for raw_path, content in buffers.items():
        if not isinstance(raw_path, str) or not isinstance(content, str):
            return {"error": "Invalid unsaved editor buffer payload.", "symbols": []}
        if Path(raw_path).suffix.lower() not in _SYMBOL_EXTENSIONS:
            continue
        encoded_len = len(content.encode("utf-8", errors="replace"))
        draft_bytes += encoded_len
        if encoded_len > 1_000_000 or draft_bytes > 5 * 1024 * 1024:
            return {"error": "Unsaved symbol-search buffers exceed the 5 MB limit.", "symbols": []}
        try:
            normalized = raw_path.replace("\\", "/")
            if (normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized)
                    or any(part == ".." for part in normalized.split("/"))):
                return {"error": "Unsaved buffer paths must stay inside the workspace.", "symbols": []}
            # Validates traversal and symlinks, while allowing a newly created editor buffer.
            safe_path(normalized)
        except (ValueError, OSError):
            return {"error": f"Unsafe symbol-search buffer path: {raw_path}", "symbols": []}
        drafts[normalized] = content

    scanned_entries = 0
    scanned_files = 0
    total_bytes = 0
    symbols: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        for folder, dirs, names in os.walk(base, followlinks=False):
            folder_path = Path(folder)
            depth = len(folder_path.relative_to(base).parts)
            dirs[:] = [name for name in dirs
                       if name not in _SYMBOL_IGNORED_DIRS and not name.startswith(".")
                       and not (folder_path / name).is_symlink()]
            if depth >= 14:
                dirs[:] = []
            scanned_entries += len(names) + len(dirs)
            if scanned_entries > 10000:
                warnings.append("Workspace symbol scan stopped at 10,000 filesystem entries.")
                break
            for name in names:
                path = folder_path / name
                if path.is_symlink() or path.suffix.lower() not in _SYMBOL_EXTENSIONS:
                    continue
                relative = path.relative_to(base).as_posix()
                try:
                    safe = safe_path(relative)
                    if not safe.is_file():
                        continue
                    content = drafts.get(relative)
                    if content is None:
                        size = safe.stat().st_size
                        if size > 512 * 1024:
                            continue
                        total_bytes += size
                        if total_bytes > 20 * 1024 * 1024:
                            warnings.append("Workspace symbol scan stopped at 20 MB of source files.")
                            break
                        content = safe.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError, ValueError):
                    continue
                scanned_files += 1
                if safe_path(relative).suffix.lower() == ".py":
                    symbols.extend(_symbols_from_python(content, relative))
                else:
                    symbols.extend(_symbols_from_javascript(content, relative))
                if scanned_files >= 2000:
                    warnings.append("Workspace symbol scan reached the 2,000 source-file limit.")
                    break
                if len(symbols) >= 2500:
                    warnings.append("Workspace symbol scan reached the 2,500 declaration limit.")
                    break
            if scanned_entries > 10000 or scanned_files >= 2000 or len(symbols) >= 2500 or any("20 MB" in x for x in warnings):
                break
    except OSError as exc:
        return {"error": f"Workspace symbol scan failed: {exc}", "symbols": []}

    # Drafts can belong to not-yet-saved files or were already covered on disk; insert any omitted drafts.
    present = {item["path"] for item in symbols}
    for relative, content in drafts.items():
        if relative in present:
            continue
        if Path(relative).suffix.lower() == ".py":
            symbols.extend(_symbols_from_python(content, relative))
        else:
            symbols.extend(_symbols_from_javascript(content, relative))
    symbols.sort(key=lambda item: (item["path"].casefold(), item["line"], item["name"].casefold()))
    return {"symbols": symbols[:2500], "count": len(symbols), "scanned_files": scanned_files,
            "scanned_entries": scanned_entries, "truncated": len(symbols) > 2500 or bool(warnings),
            "warnings": warnings, "workspace": str(base)}


@eel.expose
def run_command(command: str) -> dict[str, Any]:
    """Execute one shell command. Each command is a fresh non-interactive subprocess."""
    command = (command or "").strip()
    if not command:
        return {"error": "Type a command first."}
    try:
        cp = subprocess.run(command, cwd=str(workspace_root()), shell=True, capture_output=True,
                            text=True, errors="replace", timeout=COMMAND_TIMEOUT, env=os.environ.copy())
        return {"command": command, "stdout": cp.stdout[-50000:], "stderr": cp.stderr[-50000:],
                "returncode": cp.returncode, "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or "", exc.stderr or ""
        if isinstance(stdout, bytes): stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes): stderr = stderr.decode(errors="replace")
        return {"command": command, "stdout": stdout[-50000:],
                "stderr": (stderr + f"\nStopped after {COMMAND_TIMEOUT} seconds.")[-50000:],
                "returncode": -1, "timed_out": True}
    except Exception as exc:
        return {"error": str(exc)}


@eel.expose
def run_file(relative_path: str) -> dict[str, Any]:
    """Run common local script types using installed runtimes, with a timeout."""
    try:
        path = safe_path(relative_path)
        if not path.is_file():
            return {"error": "Select a file to run."}
        suffix = path.suffix.lower()
        if suffix == ".py":
            args = [sys.executable, str(path)]
            current = os.environ.get("PYTHONPATH", "")
            extra_env = {"PYTHONPATH": str(workspace_root()) + (os.pathsep + current if current else "")}
        elif suffix in {".js", ".cjs", ".mjs"}:
            runtime = shutil.which("node")
            if not runtime: return {"error": "Node.js was not found on PATH."}
            args, extra_env = [runtime, str(path)], None
        elif suffix == ".sh":
            runtime = shutil.which("bash") or shutil.which("sh")
            if not runtime: return {"error": "No bash/sh runtime was found."}
            args, extra_env = [runtime, str(path)], None
        elif suffix in {".bat", ".cmd"}:
            if not sys.platform.startswith("win"): return {"error": "Batch files can only run on Windows."}
            args, extra_env = [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(path)], None
        elif suffix == ".ps1":
            if not sys.platform.startswith("win"): return {"error": "PowerShell script execution is configured for Windows only."}
            runtime = shutil.which("pwsh") or shutil.which("powershell")
            if not runtime: return {"error": "PowerShell was not found on PATH."}
            args, extra_env = [runtime, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)], None
        else:
            return {"error": "No run adapter is configured for this extension. Supported: .py, .js, .cjs, .mjs, .sh, .bat, .cmd and .ps1."}
        result = _execute(args, extra_env=extra_env)
        return {"command": " ".join(f'"{arg}"' if " " in arg else arg for arg in args), **result}
    except Exception as exc:
        return {"error": str(exc)}


def _terminal_status() -> tuple[bool, int | None, int | None]:
    """Serialize terminal state inspection across browser polling and shutdown."""
    with _terminal_lock:
        return _terminal_status_unlocked()


def _terminal_status_unlocked() -> tuple[bool, int | None, int | None]:
    """Return running, return code and PID for the current terminal adapter."""
    global _terminal_exit_code
    process = _terminal_process
    if process is None:
        return False, _terminal_exit_code, None
    if _terminal_kind == "posix-pty":
        if _terminal_exit_code is None and _terminal_pid is not None:
            try:
                waited_pid, status = os.waitpid(_terminal_pid, os.WNOHANG)
                if waited_pid:
                    _terminal_exit_code = os.waitstatus_to_exitcode(status)
            except ChildProcessError:
                _terminal_exit_code = _terminal_exit_code if _terminal_exit_code is not None else 0
        return _terminal_exit_code is None, _terminal_exit_code, _terminal_pid
    if _terminal_kind == "winpty":
        try:
            if process.isalive():
                return True, None, getattr(process, "pid", None)
            code = getattr(process, "exitstatus", None)
            if code is not None:
                _terminal_exit_code = int(code)
            return False, _terminal_exit_code, getattr(process, "pid", None)
        except Exception:
            return False, _terminal_exit_code, getattr(process, "pid", None)
    code = process.poll()
    return code is None, code, process.pid


def _terminal_reader(process: Any, kind: str, pty_fd: int | None = None) -> None:
    """Read shell output in chunks so prompts and progress output appear promptly."""
    if kind == "posix-pty" and pty_fd is not None:
        try:
            while True:
                try:
                    chunk = os.read(pty_fd, 4096)
                except OSError as exc:
                    # Linux returns EIO once the PTY slave closes.
                    if exc.errno in {errno.EIO, errno.EBADF}:
                        break
                    raise
                if not chunk:
                    break
                _terminal_output.put(chunk.decode("utf-8", errors="replace"))
        except (OSError, ValueError):
            pass
        return
    if kind == "winpty":
        try:
            while True:
                chunk = process.read(4096)
                if not chunk:
                    break
                _terminal_output.put(chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace"))
        except (EOFError, OSError, ValueError):
            pass
        return
    stream = process.stdout
    if stream is None:
        return
    try:
        for line in iter(stream.readline, ""):
            _terminal_output.put(line)
    except (OSError, ValueError):
        pass


@eel.expose
def terminal_start() -> dict[str, Any]:
    """Start a persistent PTY shell where supported, with a portable pipe fallback."""
    global _terminal_process, _terminal_kind, _terminal_pty_fd, _terminal_pid, _terminal_exit_code, _terminal_shell
    with _terminal_lock:
        running, _, active_pid = _terminal_status()
        if _terminal_process is not None and running:
            shell_name = Path(_terminal_shell or "shell").name
            return {"ok": True, "pid": active_pid, "shell": shell_name, "pty": _terminal_kind != "pipe"}
        if _terminal_pty_fd is not None:
            try: os.close(_terminal_pty_fd)
            except OSError: pass
            _terminal_pty_fd = None
        _terminal_process = None
        _terminal_kind = ""
        _terminal_pid = None
        _terminal_exit_code = None
        _terminal_shell = ""
        while True:
            try: _terminal_output.get_nowait()
            except queue.Empty: break

        if sys.platform.startswith("win"):
            shell = os.environ.get("COMSPEC", "cmd.exe")
            args = [shell, "/Q"]
        else:
            shell = shutil.which("bash") or shutil.which("sh")
            if not shell:
                return {"error": "No bash/sh shell was found on PATH."}
            args = [shell, "--noprofile", "--norc"] if Path(shell).name == "bash" else [shell]
        try:
            if not sys.platform.startswith("win"):
                # Resolve data before fork: workspace locks could be inherited in a
                # locked state from an Eel worker thread in the child process.
                terminal_cwd = str(workspace_root())
                terminal_env = os.environ.copy()
                terminal_env.setdefault("TERM", "xterm-256color")
                # forkpty gives the child a controlling terminal, so isatty(), prompts,
                # line editing and signal handling behave as they do in a real terminal.
                import pty
                pid, master_fd = pty.fork()
                if pid == 0:  # child: replace the forked process immediately with the shell
                    try:
                        os.chdir(terminal_cwd)
                        os.execve(args[0], args, terminal_env)
                    except BaseException as exc:
                        try: os.write(2, f"FlowCode terminal could not start shell: {exc}\n".encode("utf-8", errors="replace"))
                        finally: os._exit(127)
                _terminal_process = True
                _terminal_kind = "posix-pty"
                _terminal_pty_fd = master_fd
                _terminal_pid = pid
                _terminal_shell = shell
                threading.Thread(target=_terminal_reader, args=(None, _terminal_kind, master_fd), daemon=True, name="flowcode-terminal-reader").start()
                return {"ok": True, "pid": pid, "shell": Path(shell).name, "pty": True}

            # WinPTY supplies a real Windows console for interactive programs. It is
            # optional, so environments where wheel installation fails retain pipe mode.
            try:
                from winpty import PtyProcess  # type: ignore[import-not-found]
                win_process = PtyProcess.spawn(args, cwd=str(workspace_root()), env=os.environ.copy())
                _terminal_process = win_process
                _terminal_kind = "winpty"
                _terminal_pid = getattr(win_process, "pid", None)
                _terminal_exit_code = None
                _terminal_shell = shell
                threading.Thread(target=_terminal_reader, args=(win_process, "winpty", None), daemon=True, name="flowcode-terminal-reader").start()
                return {"ok": True, "pid": _terminal_pid, "shell": Path(shell).name, "pty": True}
            except Exception:
                # Optional WinPTY can fail to initialize even when importable; keep a
                # reliable basic shell rather than preventing terminal startup.
                kwargs: dict[str, Any] = {}
                proc = subprocess.Popen(args, cwd=str(workspace_root()), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace", bufsize=1, env=os.environ.copy(), **kwargs)
                _terminal_process = proc
                _terminal_kind = "pipe"
                _terminal_pid = proc.pid
                _terminal_shell = shell
                threading.Thread(target=_terminal_reader, args=(proc, "pipe", None), daemon=True, name="flowcode-terminal-reader").start()
                return {"ok": True, "pid": proc.pid, "shell": Path(shell).name, "pty": False,
                        "notice": "WinPTY is unavailable; using basic pipe terminal mode."}
        except Exception as exc:
            _terminal_process = None
            _terminal_kind = ""
            _terminal_pid = None
            _terminal_shell = ""
            if _terminal_pty_fd is not None:
                try: os.close(_terminal_pty_fd)
                except OSError: pass
                _terminal_pty_fd = None
            return {"error": f"Could not start shell session: {exc}"}


@eel.expose
def terminal_write(line: str) -> dict[str, Any]:
    running, _, _ = _terminal_status()
    if _terminal_process is None or not running:
        return {"error": "The terminal session is not running. Restart the terminal and try again."}
    try:
        if _terminal_kind == "posix-pty":
            if _terminal_pty_fd is None: raise OSError("PTY file descriptor is closed")
            os.write(_terminal_pty_fd, ((line or "") + "\n").encode("utf-8", errors="replace"))
        elif _terminal_kind == "winpty":
            _terminal_process.write((line or "") + "\r\n")
        else:
            if _terminal_process.stdin is None: raise OSError("Shell stdin is closed")
            _terminal_process.stdin.write((line or "") + "\n")
            _terminal_process.stdin.flush()
        return {"ok": True}
    except Exception as exc:
        return {"error": f"Could not send input to the terminal: {exc}"}


@eel.expose
def terminal_poll() -> dict[str, Any]:
    running, returncode, pid = _terminal_status()
    output: list[str] = []
    budget = 0
    while len(output) < 300 and budget < 10000:
        budget += 1
        try: output.append(_terminal_output.get_nowait())
        except queue.Empty: break
    return {"output": "".join(output)[-50000:], "running": running,
            "returncode": returncode, "pid": pid, "pty": _terminal_kind != "pipe" and _terminal_process is not None}


@eel.expose
def terminal_stop() -> dict[str, Any]:
    global _terminal_process, _terminal_kind, _terminal_pty_fd, _terminal_pid, _terminal_exit_code, _terminal_shell
    with _terminal_lock:
        process, kind, pty_fd, pid = _terminal_process, _terminal_kind, _terminal_pty_fd, _terminal_pid
        if process is None:
            return {"ok": True}
        try:
            running, _, _ = _terminal_status()
            if kind == "posix-pty":
                if running and pid is not None:
                    try: os.killpg(pid, signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        try: os.kill(pid, signal.SIGTERM)
                        except OSError: pass
                    deadline = time.monotonic() + 1.2
                    while time.monotonic() < deadline:
                        still_running, _, _ = _terminal_status()
                        if not still_running: break
                        time.sleep(0.03)
                    still_running, _, _ = _terminal_status()
                    if still_running and pid is not None:
                        try: os.killpg(pid, signal.SIGKILL)
                        except OSError:
                            try: os.kill(pid, signal.SIGKILL)
                            except OSError: pass
                        time.sleep(0.05)
                        _terminal_status()
                if pty_fd is not None:
                    try: os.close(pty_fd)
                    except OSError: pass
            elif kind == "winpty":
                try: process.close(force=True)
                except Exception: pass
            else:
                if process.poll() is None:
                    if process.stdin:
                        try: process.stdin.write("exit\n"); process.stdin.flush()
                        except Exception: pass
                    try: process.wait(timeout=1.2)
                    except subprocess.TimeoutExpired: process.terminate()
                    try: process.wait(timeout=1.2)
                    except subprocess.TimeoutExpired: process.kill()
                for stream in (process.stdin, process.stdout):
                    if stream:
                        try: stream.close()
                        except OSError: pass
        finally:
            _terminal_process = None
            _terminal_kind = ""
            _terminal_pty_fd = None
            _terminal_pid = None
            _terminal_exit_code = None
            _terminal_shell = ""
        return {"ok": True}


_DEBUG_WORKER = r'''\
from __future__ import annotations
import bdb, io, json, os, sys, traceback

script_path = os.path.abspath(sys.argv[1])
workspace_path = os.path.abspath(sys.argv[2])
breakpoints = json.loads(sys.argv[3])
program_args = json.loads(sys.argv[4]) if len(sys.argv) > 4 else []
breakpoint_conditions = json.loads(sys.argv[5]) if len(sys.argv) > 5 else {}
protocol = sys.stdout
command_stream = sys.stdin

def send(event):
    try:
        protocol.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        protocol.flush()
    except Exception:
        pass

def shorten(value, limit=240):
    try:
        rendered = repr(value)
    except Exception as exc:
        rendered = "<repr failed: " + type(exc).__name__ + ">"
    return rendered if len(rendered) <= limit else rendered[:limit - 1] + "…"

def snapshot(frame):
    result = {}
    for key, value in list(frame.f_locals.items()):
        if key.startswith("__"):
            continue
        result[key] = shorten(value)
        if len(result) >= 80:
            result["…"] = "<locals truncated>"
            break
    return result

class FlowCodeDebugger(bdb.Bdb):
    def stop_here(self, frame):
        filename = os.path.abspath(frame.f_code.co_filename)
        try:
            relative = os.path.relpath(filename, workspace_path)
        except Exception:
            return False
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            return False
        return super().stop_here(frame)

    def user_line(self, frame):
        filename = os.path.abspath(frame.f_code.co_filename)
        try:
            relative = os.path.relpath(filename, workspace_path)
        except Exception:
            relative = filename
        is_breakpoint = bool(self.get_breaks(filename, frame.f_lineno))
        send({"type": "stopped", "reason": "breakpoint" if is_breakpoint else "step",
              "file": relative.replace(os.sep, "/"), "line": frame.f_lineno,
              "function": frame.f_code.co_name, "locals": snapshot(frame)})
        while True:
            raw = command_stream.readline()
            if not raw:
                self.set_quit()
                return
            raw_command = raw.strip()
            command = raw_command.lower()
            if command in {"continue", "c"}:
                self.set_continue(); return
            if command in {"step", "s"}:
                self.set_step(); return
            if command in {"next", "n"}:
                self.set_next(frame); return
            if command in {"stop", "quit", "q"}:
                self.set_quit()
                send({"type": "terminated", "returncode": -2, "message": "Debug session stopped."})
                return
            if command == "locals":
                send({"type": "locals", "file": relative.replace(os.sep, "/"),
                      "line": frame.f_lineno, "locals": snapshot(frame)})
                continue
            verb, _, argument = raw_command.partition(" ")
            verb = verb.lower()
            if verb == "evaluate":
                expression = raw_command.partition(" ")[2].strip()
                if not expression or len(expression) > 500 or "\n" in expression or "\r" in expression:
                    send({"type": "watch", "expression": expression[:500], "value": "", "error": "Watch expression must be 1–500 characters on one line."})
                    continue
                try:
                    result = eval(compile(expression, "<FlowCode watch expression>", "eval"), frame.f_globals, frame.f_locals)
                    send({"type": "watch", "expression": expression, "value": shorten(result), "error": None})
                except Exception as exc:
                    send({"type": "watch", "expression": expression, "value": "", "error": type(exc).__name__ + ": " + str(exc)})
                continue
            if verb in {"breakpoint", "clear"}:
                line_token, separator, condition = argument.strip().partition(" ")
                if line_token.isdigit() and int(line_token) > 0:
                    line_number = int(line_token)
                    try:
                        if verb == "breakpoint":
                            condition = condition.strip() if separator else None
                            if condition is not None and (len(condition) > 300 or any(ch in condition for ch in "\r\n\0")):
                                raise ValueError("Condition must be 1–300 characters on one line.")
                            if condition:
                                compile(condition, "<breakpoint condition>", "eval")
                            try:
                                self.clear_break(script_path, line_number)
                            except Exception:
                                pass
                            self.set_break(script_path, line_number, cond=condition or None)
                            message = "Conditional breakpoint set at line " + str(line_number) if condition else "Breakpoint set at line " + str(line_number)
                        else:
                            self.clear_break(script_path, line_number)
                            message = "Breakpoint removed at line " + str(line_number)
                        send({"type": "console", "text": message + "\n"})
                    except Exception as exc:
                        send({"type": "console", "text": "Breakpoint update failed: " + str(exc) + "\n"})
                    continue
            send({"type": "console", "text": "Unknown debugger command: " + command + "\n"})

try:
    source = open(script_path, "r", encoding="utf-8").read()
    compiled = compile(source, script_path, "exec")
    debugger = FlowCodeDebugger()
    for line in breakpoints:
        try:
            condition = breakpoint_conditions.get(str(int(line)))
            debugger.set_break(script_path, int(line), cond=condition or None)
        except Exception as exc:
            send({"type": "console", "text": "Could not set the breakpoint at line " + str(line) + ": " + str(exc) + "\n"})
    namespace = {"__name__": "__main__", "__file__": script_path, "__package__": None, "__cached__": None}
    # Reserve stdout for structured debugger messages. Program output is relayed as console text on stderr.
    sys.stdout = sys.stderr
    sys.stdin = io.StringIO("")
    sys.argv = [script_path, *[str(arg) for arg in program_args]]
    if sys.path:
        sys.path[0] = os.path.dirname(script_path)
    else:
        sys.path.insert(0, os.path.dirname(script_path))
    # Always provide an initial pause even on runtimes where line stepping does not
    # fire for the initial module frame. An explicit user breakpoint takes precedence.
    if not breakpoints:
        for line_number, line_text in enumerate(source.splitlines(), 1):
            stripped = line_text.strip()
            if stripped and not stripped.startswith("#"):
                try:
                    debugger.set_break(script_path, line_number)
                except Exception as exc:
                    send({"type": "console", "text": "Could not set the initial debugger stop: " + str(exc) + "\n"})
                break
    debugger.set_step()
    debugger.runctx(compiled, namespace, namespace)
    send({"type": "terminated", "returncode": 0, "message": "Debug session finished."})
except bdb.BdbQuit:
    send({"type": "terminated", "returncode": -2, "message": "Debug session stopped."})
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    send({"type": "terminated", "returncode": code, "message": "Program exited."})
except BaseException as exc:
    send({"type": "exception", "message": type(exc).__name__ + ": " + str(exc),
          "traceback": traceback.format_exc(limit=12)})
    send({"type": "terminated", "returncode": 1, "message": "Program failed."})
finally:
    try: protocol.flush()
    except Exception: pass
'''


def _debug_reader_finished(process: subprocess.Popen[str]) -> None:
    with _debug_lock:
        remaining = _debug_readers_remaining.get(process.pid, 0)
        if remaining <= 1:
            if _debug_process is None or _debug_process.pid != process.pid:
                _debug_readers_remaining.pop(process.pid, None)
            else:
                _debug_readers_remaining[process.pid] = 0
        else:
            _debug_readers_remaining[process.pid] = remaining - 1


def _debug_read_protocol(process: subprocess.Popen[str]) -> None:
    try:
        if process.stdout is None:
            return
        for line in iter(process.stdout.readline, ""):
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    _debug_events.put(event)
            except json.JSONDecodeError:
                _debug_events.put({"type": "console", "text": line})
    except (OSError, ValueError):
        pass
    finally:
        _debug_reader_finished(process)


def _debug_read_stderr(process: subprocess.Popen[str]) -> None:
    try:
        if process.stderr is None:
            return
        for line in iter(process.stderr.readline, ""):
            if line:
                _debug_events.put({"type": "console", "text": line})
    except (OSError, ValueError):
        pass
    finally:
        _debug_reader_finished(process)

def _clear_debug_events() -> None:
    while True:
        try:
            _debug_events.get_nowait()
        except queue.Empty:
            return


def _expand_launch_value(value: str, active_path: str) -> str:
    root = workspace_root()
    active = safe_path(active_path)
    mapping = {
        "${workspaceFolder}": str(root),
        "${workspaceFolderBasename}": root.name,
        "${file}": str(active),
        "${fileDirname}": str(active.parent),
        "${fileBasename}": active.name,
        "${fileBasenameNoExtension}": active.stem,
    }
    output = value
    for token, replacement in mapping.items():
        output = output.replace(token, replacement)
    output = re.sub(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), output)
    return output


def _path_inside_workspace(value: str, *, allow_root: bool = False) -> Path:
    root = workspace_root()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    lexical = Path(os.path.abspath(candidate))
    try:
        relative = lexical.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("Launch configuration paths must stay inside the opened workspace.") from exc
    return safe_path(relative, allow_root=allow_root)


def _normalize_debug_configuration(active_path: str, config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("Launch configuration must be an object.")
    request = str(config.get("request", "launch")).lower()
    if request != "launch":
        raise ValueError("Only launch configurations are supported; attach configurations are not implemented.")
    raw_type = str(config.get("type", "")).lower()
    if raw_type in {"python", "debugpy"}:
        kind = "python"
    elif raw_type in {"node", "pwa-node", "node-terminal"}:
        kind = "node"
    else:
        raise ValueError(f"Unsupported launch type: {raw_type or '(missing)'}. Supported types are python and node.")
    program_value = config.get("program") or config.get("file") or "${file}"
    if not isinstance(program_value, str) or not program_value.strip():
        raise ValueError("Launch configuration requires a program or file path.")
    expanded_program = _expand_launch_value(program_value.strip(), active_path)
    target = _path_inside_workspace(expanded_program)
    if not target.is_file():
        raise ValueError(f"Launch program does not exist: {target.name}")
    allowed_suffixes = {".py"} if kind == "python" else {".js", ".cjs", ".mjs"}
    if target.suffix.lower() not in allowed_suffixes:
        raise ValueError(f"{kind} launch requires one of: {', '.join(sorted(allowed_suffixes))}.")
    raw_args = config.get("args", [])
    if not isinstance(raw_args, list) or len(raw_args) > 100 or any(not isinstance(item, (str, int, float)) for item in raw_args):
        raise ValueError("Launch args must be an array of at most 100 scalar values.")
    args = [_expand_launch_value(str(item), active_path) for item in raw_args]
    if any(len(item) > 2000 or "\x00" in item for item in args):
        raise ValueError("A launch argument exceeds the supported length or contains a NUL character.")
    cwd_value = config.get("cwd", "${workspaceFolder}")
    if not isinstance(cwd_value, str):
        raise ValueError("Launch cwd must be a workspace path string.")
    cwd_expanded = _expand_launch_value(cwd_value, active_path)
    cwd = _path_inside_workspace(cwd_expanded, allow_root=True)
    if not cwd.is_dir():
        raise ValueError("Launch cwd must be an existing directory inside the workspace.")
    raw_env = config.get("env", {})
    if not isinstance(raw_env, dict) or len(raw_env) > 100:
        raise ValueError("Launch env must be an object with at most 100 variables.")
    env: dict[str, str] = {}
    for key, value in raw_env.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid environment variable name: {key}")
        if not isinstance(value, (str, int, float)):
            raise ValueError(f"Environment variable {key} must have a scalar value.")
        rendered = _expand_launch_value(str(value), active_path)
        if len(rendered) > 8000 or "\x00" in rendered:
            raise ValueError(f"Environment value {key} exceeds the supported size.")
        env[key] = rendered
    raw_runtime_args = config.get("runtimeArgs", []) if kind == "node" else []
    if not isinstance(raw_runtime_args, list) or len(raw_runtime_args) > 50 or any(not isinstance(item, str) for item in raw_runtime_args):
        raise ValueError("Node runtimeArgs must be an array of at most 50 strings.")
    runtime_args = [_expand_launch_value(item, active_path) for item in raw_runtime_args]
    if any(arg.startswith("--inspect") or "\x00" in arg or len(arg) > 1000 for arg in runtime_args):
        raise ValueError("Node runtimeArgs may not override Inspector binding and must be shorter than 1,000 characters.")
    return {"name": str(config.get("name") or "Current file"), "type": kind,
            "program": target.relative_to(workspace_root()).as_posix(), "args": args,
            "cwd": cwd.relative_to(workspace_root()).as_posix(), "env": env,
            "runtimeArgs": runtime_args, "request": "launch"}


@eel.expose
def list_debug_configurations(active_path: str = "") -> dict[str, Any]:
    """Read valid Python/Node launch configurations from .vscode/launch.json."""
    try:
        current = active_path.strip() or next((p.relative_to(workspace_root()).as_posix() for p in workspace_root().rglob("*.py") if p.is_file() and not any(x in {".venv", "venv", "node_modules", ".git", "__pycache__"} for x in p.parts)), "")
        if not current:
            return {"configurations": [], "errors": []}
        safe_path(current)
        config_path = safe_path(".vscode/launch.json")
        if not config_path.exists():
            return {"configurations": [], "errors": []}
        if not config_path.is_file() or config_path.stat().st_size > 1024 * 1024:
            return {"configurations": [], "errors": [".vscode/launch.json must be a file smaller than 1 MB."]}
        data = json.loads(config_path.read_text(encoding="utf-8"))
        rows = data.get("configurations", []) if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return {"configurations": [], "errors": ["launch.json must contain a configurations array."]}
        configs: list[dict[str, Any]] = []
        errors: list[str] = []
        current_suffix = Path(current).suffix.lower()
        for index, raw in enumerate(rows[:100]):
            try:
                if not isinstance(raw, dict):
                    raise ValueError("configuration must be an object")
                program_template = str(raw.get("program") or raw.get("file") or "${file}")
                if "${file}" in program_template:
                    raw_type = str(raw.get("type", "")).lower()
                    if raw_type in {"python", "debugpy"} and current_suffix != ".py":
                        continue
                    if raw_type in {"node", "pwa-node", "node-terminal"} and current_suffix not in {".js", ".cjs", ".mjs"}:
                        continue
                normalized = _normalize_debug_configuration(current, raw)
                normalized["id"] = f"launch-{index}"
                configs.append(normalized)
            except Exception as exc:
                errors.append(f"Configuration {index + 1}: {exc}")
        return {"configurations": configs, "errors": errors}
    except json.JSONDecodeError as exc:
        return {"configurations": [], "errors": [f"Invalid launch.json: {exc.msg} at line {exc.lineno}, column {exc.colno}."]}
    except Exception as exc:
        return {"configurations": [], "errors": [str(exc)]}


@eel.expose
def save_debug_configuration(operation: str, configuration: dict[str, Any] | None,
                             active_path: str, config_id: str = "") -> dict[str, Any]:
    """Create/update/delete a supported launch.json entry using bounded atomic writes."""
    choice = (operation or "").strip().lower()
    if choice not in {"create", "update", "delete"}:
        return {"error": "Choose create, update or delete for launch configurations."}
    try:
        active = safe_path(active_path)
        if not active.is_file():
            return {"error": "Open a source file before editing launch configurations."}
        vscode_dir = safe_path(".vscode", allow_root=True)
        if vscode_dir.exists() and not vscode_dir.is_dir():
            return {"error": ".vscode must be a directory."}
        vscode_dir.mkdir(parents=True, exist_ok=True)
        # Recheck after creation so a symlink at either path is never followed.
        vscode_dir = safe_path(".vscode", allow_root=True)
        target = safe_path(".vscode/launch.json", allow_root=True)
        if target.exists():
            if not target.is_file() or target.stat().st_size > 1024 * 1024:
                return {"error": ".vscode/launch.json must be a regular file smaller than 1 MB."}
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                return {"error": f"Invalid launch.json: {exc.msg} at line {exc.lineno}, column {exc.colno}."}
            if not isinstance(data, dict) or not isinstance(data.get("configurations", []), list):
                return {"error": "launch.json must be an object with a configurations array."}
            rows = data.setdefault("configurations", [])
            if len(rows) > 100:
                return {"error": "launch.json supports at most 100 configurations."}
        else:
            data = {"version": "0.2.0", "configurations": []}
            rows = data["configurations"]

        index: int | None = None
        if choice in {"update", "delete"}:
            match = re.fullmatch(r"launch-(\d+)", str(config_id or ""))
            if not match:
                return {"error": "Select a valid launch configuration first."}
            index = int(match.group(1))
            if index >= len(rows) or not isinstance(rows[index], dict):
                return {"error": "The selected launch configuration no longer exists. Refresh the list."}

        if choice == "delete":
            removed_name = str(rows[index].get("name") or "Launch configuration")
            rows.pop(index)
        else:
            if not isinstance(configuration, dict):
                return {"error": "Launch configuration must be an object."}
            config = dict(configuration)
            allowed = {"name", "type", "request", "program", "cwd", "args", "env", "runtimeArgs"}
            if set(config) - allowed:
                return {"error": "Launch configuration contains unsupported fields."}
            name = str(config.get("name") or "").strip()
            if not name or len(name) > 100 or any(ord(ch) < 32 for ch in name):
                return {"error": "Configuration name must contain 1–100 printable characters."}
            config["name"] = name
            config["request"] = str(config.get("request") or "launch").lower()
            try:
                normalized = _normalize_debug_configuration(active_path, config)
            except Exception as exc:
                return {"error": f"Invalid launch configuration: {exc}"}
            # Keep user-entered VS Code path variables intact in the JSON file; normalized
            # values above are used only for validation and match current runtime support.
            clean = {
                "name": name,
                "type": normalized["type"],
                "request": "launch",
                "program": config.get("program") or config.get("file") or "${file}",
                "cwd": config.get("cwd", "${workspaceFolder}"),
                "args": config.get("args", []),
                "env": config.get("env", {}),
            }
            if normalized["type"] == "node":
                clean["runtimeArgs"] = config.get("runtimeArgs", [])
            # Duplicate names make the Run & Debug selector ambiguous.
            duplicate = any(isinstance(row, dict) and str(row.get("name") or "").casefold() == name.casefold()
                            and i != index for i, row in enumerate(rows))
            if duplicate:
                return {"error": f'A launch configuration named "{name}" already exists.'}
            if choice == "create":
                if len(rows) >= 100:
                    return {"error": "launch.json supports at most 100 configurations."}
                rows.append(clean)
                index = len(rows) - 1
            else:
                old = rows[index]
                merged = dict(old)
                merged.update(clean)
                rows[index] = merged

        encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(encoded) > 1024 * 1024:
            return {"error": "Updated launch.json would exceed the 1 MB limit."}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(vscode_dir), prefix=".launch-", suffix=".tmp") as fh:
                temporary = fh.name
                fh.write(encoded)
                fh.flush()
                os.fsync(fh.fileno())
            # Revalidate the target immediately before replace (including symbolic-link rejection).
            safe_target = safe_path(".vscode/launch.json", allow_root=True)
            if safe_target != target:
                raise ValueError("launch.json path changed while saving.")
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary:
                try: Path(temporary).unlink(missing_ok=True)
                except OSError: pass
        return {"ok": True, "operation": choice, "id": f"launch-{index}" if index is not None else None,
                "name": removed_name if choice == "delete" else rows[index].get("name"),
                "settings_path": ".vscode/launch.json", "configuration_count": len(rows)}
    except Exception as exc:
        return {"error": str(exc)}


@eel.expose
def debug_start(relative_path: str, breakpoints: list[int] | None = None, launch_configuration: dict[str, Any] | None = None, breakpoint_conditions: dict[str, str] | None = None) -> dict[str, Any]:
    """Start a real, subprocess-isolated Python source debugger using bdb."""
    global _debug_process, _debug_worker_file
    try:
        launch = _normalize_debug_configuration(relative_path, launch_configuration) if launch_configuration else None
        target = safe_path(launch["program"] if launch else relative_path)
        if launch and launch["type"] != "python":
            return {"error": "The selected launch configuration is not for Python."}
        if not target.is_file() or target.suffix.lower() != ".py":
            return {"error": "The built-in debugger currently supports Python (.py) files only."}
        if target.stat().st_size > MAX_READ_BYTES:
            return {"error": "The selected script exceeds the 5 MB debugger limit."}
        source = target.read_text(encoding="utf-8")
        valid_lines = sorted({int(n) for n in (breakpoints or []) if str(n).isdigit() and 1 <= int(n) <= max(1, len(source.splitlines()))})
        if breakpoint_conditions is None:
            breakpoint_conditions = {}
        if not isinstance(breakpoint_conditions, dict) or len(breakpoint_conditions) > 200:
            return {"error": "Conditional breakpoint data must be an object with at most 200 conditions."}
        clean_conditions: dict[str, str] = {}
        for raw_line, raw_condition in breakpoint_conditions.items():
            if not isinstance(raw_line, str) or not raw_line.isdigit() or int(raw_line) not in valid_lines:
                return {"error": "Each conditional expression must reference an enabled breakpoint line."}
            if not isinstance(raw_condition, str) or not raw_condition.strip() or len(raw_condition) > 300 or any(ch in raw_condition for ch in "\r\n\0"):
                return {"error": "Conditional breakpoint expressions must be 1–300 characters on one line."}
            try:
                compile(raw_condition, "<breakpoint condition>", "eval")
            except (SyntaxError, ValueError) as exc:
                return {"error": f"Invalid condition on line {raw_line}: {exc}"}
            clean_conditions[str(int(raw_line))] = raw_condition.strip()
        debug_stop()
        _clear_debug_events()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".py", prefix="flowcode_debug_", delete=False) as worker:
            worker.write(_DEBUG_WORKER)
            worker_path = Path(worker.name)
        env = os.environ.copy()
        current = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(workspace_root()) + (os.pathsep + current if current else "")
        if launch:
            env.update(launch["env"])
        program_args = launch["args"] if launch else []
        run_cwd = safe_path(launch["cwd"], allow_root=True) if launch else workspace_root()
        with _debug_lock:
            process = subprocess.Popen(
                [sys.executable, str(worker_path), str(target), str(workspace_root()), json.dumps(valid_lines), json.dumps(program_args), json.dumps(clean_conditions)],
                cwd=str(run_cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
            )
            _debug_process, _debug_worker_file = process, worker_path
            _debug_readers_remaining[process.pid] = 2
        threading.Thread(target=_debug_read_protocol, args=(process,), daemon=True, name="flowcode-debug-protocol").start()
        threading.Thread(target=_debug_read_stderr, args=(process,), daemon=True, name="flowcode-debug-console").start()
        return {"ok": True, "pid": process.pid, "file": target.relative_to(workspace_root()).as_posix(),
                "breakpoints": valid_lines, "breakpoint_conditions": clean_conditions, "engine": "Python bdb (subprocess)"}
    except Exception as exc:
        return {"error": str(exc)}


@eel.expose
def debug_command(command: str) -> dict[str, Any]:
    """Send a constrained stepping command to the paused debugger."""
    global _debug_process
    raw_value = (command or "").strip()
    lowered = raw_value.lower()
    aliases = {"c": "continue", "s": "step", "n": "next", "q": "stop", "quit": "stop"}
    value = raw_value if lowered.startswith("evaluate ") else aliases.get(lowered, lowered)
    if lowered.startswith("evaluate "):
        expression = raw_value.partition(" ")[2].strip()
        if not expression or len(expression) > 500 or "\n" in expression or "\r" in expression:
            return {"error": "Watch expression must be 1–500 characters on one line."}
    elif lowered.startswith("breakpoint "):
        match = re.fullmatch(r"breakpoint\s+(\d+)(?:\s+(.+))?", raw_value, re.IGNORECASE)
        if not match or int(match.group(1)) <= 0:
            return {"error": "Breakpoint command must be 'breakpoint LINE [condition]' with a positive line number."}
        condition = match.group(2)
        if condition is not None:
            if len(condition) > 300 or any(ch in condition for ch in "\r\n\0"):
                return {"error": "Conditional breakpoint expressions must be at most 300 characters on one line."}
            try:
                compile(condition, "<breakpoint condition>", "eval")
            except (SyntaxError, ValueError) as exc:
                return {"error": f"Invalid conditional breakpoint expression: {exc}"}
        value = raw_value
    elif value not in {"continue", "step", "next", "stop", "locals"} and not re.fullmatch(r"clear\s+\d+", value):
        return {"error": "Unsupported debugger command."}
    with _debug_lock:
        process = _debug_process
        if process is None or process.poll() is not None:
            return {"error": "No active debug session."}
        if value == "stop":
            try:
                process.terminate()
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                process.kill()
            except Exception:
                pass
            return {"ok": True, "stopping": True}
        if process.stdin is None:
            return {"error": "Debugger command pipe is unavailable."}
        try:
            process.stdin.write(value + "\n")
            process.stdin.flush()
            return {"ok": True, "command": value}
        except Exception as exc:
            return {"error": str(exc)}


@eel.expose
def debug_poll() -> dict[str, Any]:
    """Drain structured debugger events and report session status."""
    global _debug_process, _debug_worker_file
    events: list[dict[str, Any]] = []
    while len(events) < 250:
        try:
            events.append(_debug_events.get_nowait())
        except queue.Empty:
            break
    with _debug_lock:
        process = _debug_process
        process_returncode = process.poll() if process is not None else None
        readers_left = _debug_readers_remaining.get(process.pid, 0) if process is not None else 0
        queued_events = not _debug_events.empty()
        finished = process is not None and process_returncode is not None and readers_left == 0 and not queued_events
        running = process is not None and not finished
        returncode = process_returncode if finished else None
        if finished:
            _debug_process = None
            _debug_readers_remaining.pop(process.pid, None)
            worker = _debug_worker_file
            _debug_worker_file = None
            if worker:
                try: worker.unlink(missing_ok=True)
                except OSError: pass
    return {"events": events, "running": running, "returncode": returncode}

@eel.expose
def debug_stop() -> dict[str, Any]:
    """Terminate the debugger process and remove its temporary worker."""
    global _debug_process, _debug_worker_file
    with _debug_lock:
        process = _debug_process
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=1.5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=1.5)
            except Exception:
                pass
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    try: stream.close()
                    except OSError: pass
            _debug_process = None
            if _debug_readers_remaining.get(process.pid) == 0:
                _debug_readers_remaining.pop(process.pid, None)
        worker = _debug_worker_file
        _debug_worker_file = None
        if worker:
            try: worker.unlink(missing_ok=True)
            except OSError: pass
    return {"ok": True}


@eel.expose
def format_python_document(relative_path: str, content: str) -> dict[str, Any]:
    """Format the current Python buffer with Black without writing the file."""
    try:
        import black
    except ImportError:
        return {"error": "Python formatting requires Black. Restart run.bat/launch.ps1 after dependency installation, or install black in this environment."}
    try:
        target = safe_path(relative_path)
        if not target.is_file() or target.suffix.lower() != ".py":
            return {"error": "Open a Python (.py) file before formatting."}
        source = content if isinstance(content, str) else str(content)
        if len(source.encode("utf-8", errors="replace")) > MAX_READ_BYTES:
            return {"error": "Python formatting is limited to 5 MB of source text."}
        try:
            formatted = black.format_file_contents(source, fast=False, mode=black.FileMode(line_length=88))
            changed = True
        except black.NothingChanged:
            formatted = source
            changed = False
        return {"formatted": formatted, "changed": changed, "engine": "Black", "line_length": 88}
    except black.InvalidInput as exc:
        return {"error": f"Black could not format this file: {exc}"}
    except Exception as exc:
        return {"error": str(exc)}



# Node.js debugging via the local Chrome DevTools Protocol/Inspector WebSocket.
def _node_debug_emit(session: dict[str, Any], event: dict[str, Any]) -> None:
    try:
        session["events"].put(event)
    except Exception:
        pass


def _node_debug_reader_finished(session: dict[str, Any], key: str) -> None:
    session[key] = True


def _node_debug_stdout_reader(session: dict[str, Any]) -> None:
    process = session["process"]
    try:
        if process.stdout is not None:
            for line in iter(process.stdout.readline, ""):
                if line:
                    _node_debug_emit(session, {"type": "console", "text": line})
    except (OSError, ValueError):
        pass
    finally:
        _node_debug_reader_finished(session, "stdout_done")


def _node_debug_stderr_reader(session: dict[str, Any]) -> None:
    process = session["process"]
    try:
        if process.stderr is None:
            return
        for line in iter(process.stderr.readline, ""):
            if not line:
                continue
            match = re.search(r"Debugger listening on (ws://\S+)", line)
            if match:
                session["ws_url"] = match.group(1).strip()
                session["ws_ready"].set()
            elif "For help, see:" in line:
                continue
            elif "Waiting for the debugger to disconnect" in line:
                _node_debug_emit(session, {"type": "console", "text": line})
                # Node has completed the script but keeps the process alive while the
                # Inspector client remains attached. Closing the local socket lets it exit.
                session["stop_event"].set()
                try:
                    if session.get("socket") is not None:
                        session["socket"].close()
                except Exception:
                    pass
            else:
                _node_debug_emit(session, {"type": "console", "text": line})
    except (OSError, ValueError):
        pass
    finally:
        _node_debug_reader_finished(session, "stderr_done")
        session["ws_ready"].set()


def _node_debug_local_file(session: dict[str, Any], url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "file":
            return None
        target = Path(unquote(parsed.path))
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", str(target)):
            target = Path(str(target)[1:])
        target = target.resolve()
        return target.relative_to(session["root"]).as_posix()
    except Exception:
        return None


def _node_debug_handle_message(session: dict[str, Any], message: dict[str, Any]) -> None:
    if "id" in message:
        pending = session["pending"].get(message["id"])
        if pending is not None:
            pending["response"] = message
            pending["event"].set()
        return
    method = message.get("method")
    params = message.get("params") or {}
    if method == "Debugger.scriptParsed":
        session["scripts"][str(params.get("scriptId"))] = params.get("url", "")
        return
    if method == "Debugger.paused":
        frames = params.get("callFrames") or []
        frame = frames[0] if frames else {}
        location = frame.get("location") or {}
        url = session["scripts"].get(str(location.get("scriptId")), "")
        file_path = _node_debug_local_file(session, url)
        hit = bool(params.get("hitBreakpoints"))
        reason = "breakpoint" if hit else "step"
        event = {"type": "stopped", "reason": reason, "file": file_path or url,
                 "line": int(location.get("lineNumber", 0)) + 1,
                 "column": int(location.get("columnNumber", 0)) + 1,
                 "function": frame.get("functionName") or "<module>",
                 "locals": {}, "_scopeObjectId": None, "_callFrameId": frame.get("callFrameId"),
                 "_external": file_path is None}
        for scope in frame.get("scopeChain") or []:
            if scope.get("type") in {"local", "closure", "script"}:
                obj = scope.get("object") or {}
                if obj.get("objectId"):
                    event["_scopeObjectId"] = obj["objectId"]
                    break
        # Queue enrichment mutates its event; retain an independent copy for locals/step commands.
        session["paused"] = dict(event)
        _node_debug_emit(session, event)
        return
    if method == "Runtime.consoleAPICalled":
        # Program output is captured from the child stdout/stderr pipes to avoid duplicates.
        return
    if method == "Runtime.exceptionThrown":
        details = params.get("exceptionDetails") or {}
        exc = details.get("exception") or {}
        text = details.get("text") or exc.get("description") or "JavaScript exception"
        _node_debug_emit(session, {"type": "exception", "message": str(text), "traceback": str(exc.get("description", ""))})
        return
    if method == "Debugger.resumed":
        session["paused"] = None


def _node_debug_socket_reader(session: dict[str, Any]) -> None:
    try:
        ws = session["socket"]
        while not session["stop_event"].is_set():
            try:
                raw = ws.recv()
            except Exception as exc:
                try:
                    import websocket
                    if isinstance(exc, websocket.WebSocketTimeoutException):
                        if session["process"].poll() is not None:
                            break
                        continue
                except Exception:
                    pass
                break
            if not raw:
                break
            try:
                message = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(message, dict):
                _node_debug_handle_message(session, message)
    finally:
        _node_debug_reader_finished(session, "socket_done")


def _node_debug_request(session: dict[str, Any], method: str, params: dict[str, Any] | None = None, timeout: float = 4.0) -> dict[str, Any]:
    ws = session.get("socket")
    if ws is None:
        return {"error": "Node Inspector WebSocket is not connected."}
    with session["request_lock"]:
        request_id = session["next_id"]
        session["next_id"] += 1
        waiter = {"event": threading.Event(), "response": None}
        session["pending"][request_id] = waiter
        try:
            ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}, separators=(",", ":")))
        except Exception as exc:
            session["pending"].pop(request_id, None)
            return {"error": str(exc)}
    if not waiter["event"].wait(timeout):
        session["pending"].pop(request_id, None)
        return {"error": f"Node Inspector request timed out: {method}"}
    response = waiter.get("response") or {}
    if response.get("error"):
        return {"error": response["error"].get("message", "Inspector request failed"), "details": response["error"]}
    return response.get("result", {})


def _node_debug_read_locals(session: dict[str, Any], stopped: dict[str, Any]) -> dict[str, str]:
    object_id = stopped.get("_scopeObjectId")
    if not object_id:
        return {}
    response = _node_debug_request(session, "Runtime.getProperties", {"objectId": object_id, "ownProperties": True, "accessorPropertiesOnly": False, "generatePreview": True})
    variables: dict[str, str] = {}
    for item in response.get("result", [])[:80]:
        if item.get("isOwn") is False or item.get("kind") == "accessor":
            continue
        name = str(item.get("name", ""))
        if not name or name.startswith("__"):
            continue
        value = item.get("value") or {}
        rendered = value.get("unserializableValue", value.get("value", value.get("description", value.get("type", "undefined"))))
        variables[name] = str(rendered)[:240]
    return variables


def _node_debug_set_breakpoint(session: dict[str, Any], line: int) -> dict[str, Any]:
    if line < 1 or line > 10000000:
        return {"error": "Invalid breakpoint line."}
    result = _node_debug_request(session, "Debugger.setBreakpointByUrl", {
        "url": session["file_url"], "lineNumber": line - 1, "columnNumber": 0})
    if result.get("error"):
        return result
    session["breakpoints"][line] = result.get("breakpointId")
    return {"ok": True, "line": line, "breakpointId": result.get("breakpointId")}


@eel.expose
def node_debug_start(relative_path: str, breakpoints: list[int] | None = None,
                     launch_configuration: dict[str, Any] | None = None) -> dict[str, Any]:
    """Start a Node.js source debugger through the local Inspector protocol on loopback only."""
    global _node_debug_session
    try:
        import websocket
    except ImportError:
        return {"error": "JavaScript debugging requires websocket-client. Restart run.bat/launch.ps1 after dependency installation."}
    try:
        launch = _normalize_debug_configuration(relative_path, launch_configuration) if launch_configuration else None
        if launch and launch["type"] != "node":
            return {"error": "The selected launch configuration is not for Node.js."}
        target = safe_path(launch["program"] if launch else relative_path)
    except Exception as exc:
        return {"error": str(exc)}
    if not target.is_file() or target.suffix.lower() not in {".js", ".cjs", ".mjs"}:
        return {"error": "The Node.js debugger supports .js, .cjs and .mjs files."}
    if target.stat().st_size > MAX_READ_BYTES:
        return {"error": "The selected JavaScript file exceeds the 5 MB debugger limit."}
    node = shutil.which("node")
    if not node:
        return {"error": "Node.js was not found on PATH."}
    node_debug_stop()
    for event_queue in (_debug_events,):
        while True:
            try: event_queue.get_nowait()
            except queue.Empty: break
    process = None
    session: dict[str, Any] = {
        "file": target.relative_to(workspace_root()).as_posix(), "target": target,
        "root": workspace_root(), "file_url": target.as_uri(), "events": queue.Queue(),
        "ws_ready": threading.Event(), "ws_url": None, "socket": None,
        "process": None, "stop_event": threading.Event(), "pending": {}, "next_id": 1,
        "request_lock": threading.RLock(), "scripts": {}, "breakpoints": {}, "paused": None,
        "stdout_done": False, "stderr_done": False, "socket_done": False, "terminated_sent": False,
        "threads": [],
    }
    try:
        env = os.environ.copy()
        if launch:
            env.update(launch["env"])
        node_args = [node, "--inspect-brk=127.0.0.1:0", *(launch["runtimeArgs"] if launch else []), str(target), *(launch["args"] if launch else [])]
        run_cwd = safe_path(launch["cwd"], allow_root=True) if launch else workspace_root()
        process = subprocess.Popen(node_args, cwd=str(run_cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env)
        session["process"] = process
        with _node_debug_lock:
            _node_debug_session = session
        for target_fn, key in ((_node_debug_stdout_reader, "stdout"), (_node_debug_stderr_reader, "stderr")):
            thread = threading.Thread(target=target_fn, args=(session,), daemon=True, name=f"flowcode-node-debug-{key}")
            session["threads"].append(thread); thread.start()
        if not session["ws_ready"].wait(8) or not session.get("ws_url"):
            raise RuntimeError("Node Inspector did not publish its loopback WebSocket endpoint within 8 seconds.")
        ws = websocket.create_connection(session["ws_url"], timeout=5, suppress_origin=True, enable_multithread=True)
        ws.settimeout(0.25)
        session["socket"] = ws
        thread = threading.Thread(target=_node_debug_socket_reader, args=(session,), daemon=True, name="flowcode-node-inspector-reader")
        session["threads"].append(thread); thread.start()
        init = _node_debug_request(session, "Runtime.enable")
        if init.get("error"): raise RuntimeError(init["error"])
        init = _node_debug_request(session, "Debugger.enable")
        if init.get("error"): raise RuntimeError(init["error"])
        requested = sorted({int(n) for n in (breakpoints or []) if str(n).isdigit() and 1 <= int(n) <= max(1, len(target.read_text(encoding="utf-8").splitlines()))})
        for line in requested:
            result = _node_debug_set_breakpoint(session, line)
            if result.get("error"):
                _node_debug_emit(session, {"type": "console", "text": f"Could not set breakpoint at line {line}: {result['error']}\n"})
        # Node's --inspect-brk target waits for an explicit Runtime command before
        # emitting the initial paused event and executing its entry script.
        resumed = _node_debug_request(session, "Runtime.runIfWaitingForDebugger", timeout=5.0)
        if resumed.get("error"):
            raise RuntimeError(f"Could not release Node Inspector startup wait: {resumed['error']}")
        return {"ok": True, "pid": process.pid, "file": session["file"], "breakpoints": requested,
                "engine": "Node.js Inspector Protocol"}
    except Exception as exc:
        if process is not None:
            try: process.terminate()
            except Exception: pass
        node_debug_stop()
        return {"error": str(exc)}


@eel.expose
def node_debug_command(command: str) -> dict[str, Any]:
    global _node_debug_session
    session = _node_debug_session
    if not session or session["process"].poll() is not None:
        return {"error": "No active Node.js debug session."}
    raw_value = (command or "").strip()
    lowered = raw_value.lower()
    aliases = {"c": "continue", "s": "step", "n": "next", "q": "stop", "quit": "stop"}
    value = aliases.get(lowered, lowered)
    if value == "stop":
        with _node_debug_lock:
            if _node_debug_session is not session:
                return {"ok": True, "stopping": True}
            session["stop_requested"] = True
            session["stop_event"].set()
        try:
            if session.get("socket") is not None:
                session["socket"].close()
        except Exception:
            pass
        process = session.get("process")
        try:
            if process is not None and process.poll() is None:
                process.terminate()
        except Exception as exc:
            return {"error": str(exc)}
        return {"ok": True, "stopping": True}
    if value in {"continue", "step", "next"}:
        method = {"continue": "Debugger.resume", "step": "Debugger.stepInto", "next": "Debugger.stepOver"}[value]
        result = _node_debug_request(session, method)
        return {"ok": True, "command": value} if not result.get("error") else result
    if value == "locals":
        stopped = session.get("paused")
        if not stopped:
            return {"error": "The JavaScript program is not paused."}
        variables = _node_debug_read_locals(session, stopped)
        _node_debug_emit(session, {"type": "locals", "file": stopped.get("file"), "line": stopped.get("line"),
                                  "function": stopped.get("function"), "locals": variables})
        return {"ok": True}
    if raw_value.lower().startswith("evaluate "):
        expression = raw_value.partition(" ")[2].strip()
        if not expression or len(expression) > 500 or "\n" in expression or "\r" in expression:
            return {"error": "Watch expression must be 1–500 characters on one line."}
        stopped = session.get("paused")
        frame_id = stopped.get("_callFrameId") if stopped else None
        if not frame_id:
            return {"error": "Pause the JavaScript program before evaluating watch expressions."}
        result = _node_debug_request(session, "Debugger.evaluateOnCallFrame", {
            "callFrameId": frame_id, "expression": expression, "returnByValue": True, "silent": True}, timeout=5.0)
        if result.get("error"):
            return result
        exception = result.get("exceptionDetails")
        if exception:
            details = exception.get("exception") or {}
            return {"error": str(details.get("description") or exception.get("text") or "Expression evaluation failed."), "expression": expression}
        remote = result.get("result") or {}
        if "value" in remote:
            evaluated = remote["value"]
            if isinstance(evaluated, (dict, list)):
                rendered = json.dumps(evaluated, ensure_ascii=False, default=str)
            else:
                rendered = str(evaluated)
        else:
            rendered = str(remote.get("unserializableValue", remote.get("description", remote.get("type", "undefined"))))
        return {"ok": True, "expression": expression, "value": rendered[:1000]}
    match = re.fullmatch(r"(breakpoint|clear)\s+(\d+)", value)
    if match:
        verb, number_text = match.groups(); line = int(number_text)
        if verb == "breakpoint":
            return _node_debug_set_breakpoint(session, line)
        breakpoint_id = session["breakpoints"].pop(line, None)
        if breakpoint_id:
            result = _node_debug_request(session, "Debugger.removeBreakpoint", {"breakpointId": breakpoint_id})
            if result.get("error"): return result
        return {"ok": True, "line": line, "removed": bool(breakpoint_id)}
    return {"error": "Unsupported Node.js debugger command."}


@eel.expose
def node_debug_poll() -> dict[str, Any]:
    global _node_debug_session
    session = _node_debug_session
    if not session:
        return {"events": [], "running": False, "returncode": None}
    events: list[dict[str, Any]] = []
    while len(events) < 250:
        try: events.append(session["events"].get_nowait())
        except queue.Empty: break
    enriched: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") == "stopped":
            event["locals"] = _node_debug_read_locals(session, event)
            event.pop("_scopeObjectId", None); event.pop("_callFrameId", None); event.pop("_external", None)
        elif event.get("type") == "locals":
            event.pop("_scopeObjectId", None); event.pop("_callFrameId", None); event.pop("_external", None)
        enriched.append(event)
    process = session["process"]
    code = process.poll()
    done = code is not None and session.get("stdout_done") and session.get("stderr_done") and session.get("socket_done")
    if done and not session.get("terminated_sent"):
        stopped_by_user = bool(session.get("stop_requested"))
        enriched.append({"type": "terminated", "returncode": code,
                         "message": "Node.js debug session stopped." if stopped_by_user else "Node.js debug session finished."})
        session["terminated_sent"] = True
    running = not done
    result = {"events": enriched, "running": running, "returncode": code if done else None}
    if done:
        try:
            if session.get("socket"): session["socket"].close()
        except Exception: pass
        for thread in session.get("threads", []):
            if thread is not threading.current_thread():
                try: thread.join(timeout=0.25)
                except RuntimeError: pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                try: stream.close()
                except OSError: pass
        with _node_debug_lock:
            if _node_debug_session is session: _node_debug_session = None
    return result


@eel.expose
def node_debug_stop() -> dict[str, Any]:
    global _node_debug_session
    with _node_debug_lock:
        session = _node_debug_session
        _node_debug_session = None
    if not session:
        return {"ok": True}
    process = session.get("process")
    session["stop_event"].set()
    try:
        if session.get("socket"): session["socket"].close()
    except Exception: pass
    if process is not None:
        try:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=1.5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=1.5)
        except Exception: pass
        # Let the dedicated reader threads drain EOF before closing their pipes.
        for thread in session.get("threads", []):
            if thread is not threading.current_thread():
                try: thread.join(timeout=0.75)
                except RuntimeError: pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                try: stream.close()
                except OSError: pass
    return {"ok": True}


class _HTMLStructureChecker(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.stack: list[tuple[str, int, int]] = []
        self.diagnostics: list[dict[str, Any]] = []

    def _diag(self, message: str, line: int, column: int) -> None:
        self.diagnostics.append({"severity": "warning", "message": message, "line": line, "column": column + 1, "source": "HTML"})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() not in self.VOID_TAGS:
            line, column = self.getpos()
            self.stack.append((tag.lower(), line, column))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in self.VOID_TAGS:
            return
        matches = [i for i, entry in enumerate(self.stack) if entry[0] == name]
        line, column = self.getpos()
        if not matches:
            self._diag(f"Unexpected closing tag </{name}>.", line, column)
            return
        index = matches[-1]
        while len(self.stack) - 1 > index:
            unclosed, open_line, open_column = self.stack.pop()
            self._diag(f"Tag <{unclosed}> is not closed before </{name}>.", open_line, open_column)
        self.stack.pop()

    def finish(self) -> list[dict[str, Any]]:
        try:
            self.feed(self.source)
            self.close()
        except Exception as exc:
            self.diagnostics.append({"severity": "warning", "message": f"Could not fully analyze HTML: {exc}", "line": 1, "column": 1, "source": "HTML"})
        for tag, line, column in self.stack:
            self._diag(f"Opening tag <{tag}> is not closed.", line, column)
        return self.diagnostics[:100]


def _validate_delimiters(source: str, *, language_name: str, comments: str) -> list[dict[str, Any]]:
    """Basic delimiter checker for CSS/SCSS that ignores quoted strings and comments."""
    diagnostics: list[dict[str, Any]] = []
    stack: list[tuple[str, int, int]] = []
    pairs = {"}": "{", ")": "(", "]": "["}
    i = 0; line = 1; col = 1; quote: str | None = None; escaped = False
    while i < len(source):
        ch = source[i]; nxt = source[i + 1] if i + 1 < len(source) else ""
        if quote:
            if escaped: escaped = False
            elif ch == "\\": escaped = True
            elif ch == quote: quote = None
        elif comments == "css" and ch == "/" and nxt == "*":
            i += 2; col += 2
            while i < len(source) and source[i:i+2] != "*/":
                if source[i] == "\n": line += 1; col = 1
                else: col += 1
                i += 1
            if i < len(source): i += 2; col += 2
            continue
        elif ch in {"'", '"'}:
            quote = ch
        elif ch in "{([":
            stack.append((ch, line, col))
        elif ch in "})]":
            if not stack or stack[-1][0] != pairs[ch]:
                diagnostics.append({"severity": "warning", "message": f"Unmatched closing delimiter '{ch}'.", "line": line, "column": col, "source": language_name})
            else:
                stack.pop()
        if ch == "\n": line += 1; col = 1
        else: col += 1
        i += 1
    if quote:
        diagnostics.append({"severity": "warning", "message": "String literal is not closed.", "line": line, "column": max(1, col - 1), "source": language_name})
    for opener, open_line, open_column in stack:
        expected = {"{": "}", "(": ")", "[": "]"}[opener]
        diagnostics.append({"severity": "warning", "message": f"Opening delimiter '{opener}' is not closed (expected '{expected}').", "line": open_line, "column": open_column, "source": language_name})
    return diagnostics[:100]


@eel.expose
def validate_code(relative_path: str, content: str) -> dict[str, Any]:
    """Non-executing source checks for Python, JSON, Node.js JS, HTML, CSS/SCSS and XML."""
    suffix = Path(relative_path or "").suffix.lower()
    source = content if isinstance(content, str) else str(content)
    diagnostics: list[dict[str, Any]] = []
    checked = suffix in {".py", ".json", ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".scss", ".xml", ".svg"}
    if suffix == ".py":
        try:
            ast.parse(source, filename=relative_path or "<editor>")
        except SyntaxError as exc:
            diagnostics.append({"severity": "error", "message": exc.msg, "line": exc.lineno or 1, "column": exc.offset or 1, "source": "Python"})
    elif suffix == ".json":
        try:
            json.loads(source)
        except json.JSONDecodeError as exc:
            diagnostics.append({"severity": "error", "message": exc.msg, "line": exc.lineno, "column": exc.colno, "source": "JSON"})
    elif suffix in {".ts", ".tsx", ".jsx"}:
        result = typescript_intelligence(relative_path, source, 1, 0, "diagnostics")
        if result.get("error"):
            checked = False
        else:
            diagnostics.extend(result.get("diagnostics", []))
    elif suffix in {".js", ".cjs", ".mjs"}:
        node = shutil.which("node")
        if node:
            temp_name = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=suffix, encoding="utf-8", delete=False) as fh:
                    fh.write(source); temp_name = fh.name
                cp = subprocess.run([node, "--check", temp_name], capture_output=True, text=True, errors="replace", timeout=8)
                if cp.returncode:
                    msg = (cp.stderr or cp.stdout).strip(); line = 1; column = 1
                    for part in msg.splitlines():
                        if part.startswith((temp_name + ":", "SyntaxError")):
                            match = re.search(re.escape(temp_name) + r":(\d+):(\d+)", part)
                            if match: line, column = int(match.group(1)), int(match.group(2)); break
                            match = re.search(r":(\d+)(?::(\d+))?", part)
                            if match: line, column = int(match.group(1)), int(match.group(2) or 1); break
                    diagnostics.append({"severity": "error", "message": msg[-1200:], "line": line, "column": column, "source": "Node.js"})
            except subprocess.TimeoutExpired:
                diagnostics.append({"severity": "warning", "message": "Syntax check timed out.", "line": 1, "column": 1, "source": "Node.js"})
            except Exception as exc:
                diagnostics.append({"severity": "warning", "message": str(exc), "line": 1, "column": 1, "source": "Node.js"})
            finally:
                if temp_name:
                    try: os.unlink(temp_name)
                    except OSError: pass

            # JavaScript remains syntax-checked by Node without adding a runtime
            # dependency. TypeScript's semantic checker is invoked only when the
            # file opts in with // @ts-check or the workspace explicitly enables
            # checkJs, avoiding a compiler process for ordinary JS files.
            ts_check = bool(re.search(r"^\s*//\s*@ts-check\b", source, flags=re.MULTILINE))
            try:
                tsconfig = safe_path("tsconfig.json")
                if tsconfig.is_file() and not tsconfig.is_symlink() and tsconfig.stat().st_size <= 256 * 1024:
                    project_config = json.loads(tsconfig.read_text(encoding="utf-8"))
                    compiler_options = project_config.get("compilerOptions", {}) if isinstance(project_config, dict) else {}
                    ts_check = ts_check or (isinstance(compiler_options, dict) and compiler_options.get("checkJs") is True)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                pass
            if ts_check:
                typed = typescript_intelligence(relative_path, source, 1, 0, "diagnostics")
                if typed.get("error"):
                    diagnostics.append({"severity": "warning", "message": typed["error"], "line": 1, "column": 1, "source": "TypeScript"})
                else:
                    syntax_error_lines = {item["line"] for item in diagnostics if item.get("source") == "Node.js" and item.get("severity") == "error"}
                    for item in typed.get("diagnostics", []):
                        if item.get("source") == "TypeScript" and item.get("line") in syntax_error_lines:
                            continue
                        diagnostics.append(item)
        else:
            checked = False
    elif suffix in {".html", ".htm"}:
        diagnostics.extend(_HTMLStructureChecker(source).finish())
    elif suffix in {".css", ".scss"}:
        diagnostics.extend(_validate_delimiters(source, language_name="CSS", comments="css"))
    elif suffix in {".xml", ".svg"}:
        try:
            ElementTree.fromstring(source)
        except ElementTree.ParseError as exc:
            line, column = getattr(exc, "position", (1, 0))
            diagnostics.append({"severity": "error", "message": str(exc), "line": line, "column": column + 1, "source": "XML"})
    return {"checked": checked, "diagnostics": diagnostics}


WORKSPACE_DIAGNOSTIC_EXTENSIONS = frozenset({
    ".py", ".json", ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx",
    ".html", ".htm", ".css", ".scss", ".xml", ".svg",
})
WORKSPACE_DIAGNOSTIC_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".idea",
    "dist", "build", "coverage", ".next", ".pytest_cache", ".mypy_cache",
})
MAX_WORKSPACE_DIAGNOSTIC_FILES = 100
MAX_WORKSPACE_DIAGNOSTIC_BYTES = 12 * 1024 * 1024
WORKSPACE_DIAGNOSTIC_TIMEOUT = 12.0


@eel.expose
def validate_workspace(overrides: dict[str, str] | None = None) -> dict[str, Any]:
    """Run bounded, non-executing syntax checks over supported workspace source files.

    `overrides` lets the UI validate open unsaved buffers without writing them to disk.
    Dependencies, build output, caches and symlinks are excluded from traversal.
    """
    try:
        with _workspace_lock:
            started = time.monotonic()
            if overrides is None:
                overrides = {}
            if not isinstance(overrides, dict) or len(overrides) > MAX_WORKSPACE_DIAGNOSTIC_FILES:
                return {"error": "Workspace diagnostic overrides must be an object containing at most 100 files."}
            clean_overrides: dict[str, str] = {}
            for raw_path, content in overrides.items():
                if not isinstance(raw_path, str) or not isinstance(content, str) or not _valid_session_relative_path(raw_path):
                    return {"error": "Workspace diagnostic overrides must map safe relative paths to text content."}
                target = safe_path(raw_path)
                relative = target.relative_to(workspace_root()).as_posix()
                if target.suffix.lower() not in WORKSPACE_DIAGNOSTIC_EXTENSIONS:
                    continue
                if len(content.encode("utf-8", errors="replace")) > MAX_READ_BYTES:
                    return {"error": f"Unsaved buffer {relative} exceeds the 5 MB file limit."}
                clean_overrides[relative] = content
    
            root = workspace_root()
            candidates: list[str] = []
            partial = False
            for folder, dirs, filenames in os.walk(root, topdown=True, followlinks=False):
                base = Path(folder)
                dirs[:] = sorted(
                    [name for name in dirs if name not in WORKSPACE_DIAGNOSTIC_SKIP_DIRS
                     and not (base / name).is_symlink()],
                    key=str.casefold,
                )
                for filename in sorted(filenames, key=str.casefold):
                    file_path = base / filename
                    if file_path.is_symlink() or file_path.suffix.lower() not in WORKSPACE_DIAGNOSTIC_EXTENSIONS:
                        continue
                    try:
                        rel = file_path.relative_to(root).as_posix()
                    except ValueError:
                        continue
                    candidates.append(rel)
                    if len(candidates) > MAX_WORKSPACE_DIAGNOSTIC_FILES:
                        partial = True
                        break
                if len(candidates) > MAX_WORKSPACE_DIAGNOSTIC_FILES:
                    break
    
            if partial:
                candidates = candidates[:MAX_WORKSPACE_DIAGNOSTIC_FILES]
            diagnostics: list[dict[str, Any]] = []
            checked_files = 0
            skipped_files = 0
            total_bytes = 0
            truncated = partial
            for relative in sorted(candidates, key=str.casefold):
                if time.monotonic() - started >= WORKSPACE_DIAGNOSTIC_TIMEOUT:
                    truncated = True
                    break
                try:
                    target = safe_path(relative)
                    if relative in clean_overrides:
                        content = clean_overrides[relative]
                        size = len(content.encode("utf-8", errors="replace"))
                    else:
                        if not target.is_file():
                            skipped_files += 1
                            continue
                        size = target.stat().st_size
                        if size > MAX_READ_BYTES:
                            skipped_files += 1
                            continue
                        content = target.read_text(encoding="utf-8")
                        size = len(content.encode("utf-8", errors="replace"))
                except (OSError, UnicodeError, ValueError):
                    skipped_files += 1
                    continue
                if size > MAX_READ_BYTES:
                    skipped_files += 1
                    continue
                if total_bytes + size > MAX_WORKSPACE_DIAGNOSTIC_BYTES:
                    truncated = True
                    break
                total_bytes += size
                result = validate_code(relative, content)
                if result.get("checked"):
                    checked_files += 1
                for item in result.get("diagnostics", []):
                    diagnostic = dict(item)
                    diagnostic["path"] = relative
                    diagnostics.append(diagnostic)
                    if len(diagnostics) >= 1000:
                        truncated = True
                        break
                if truncated and len(diagnostics) >= 1000:
                    break
            diagnostics.sort(key=lambda item: (str(item.get("path", "")).casefold(),
                                               int(item.get("line", 1) or 1),
                                               int(item.get("column", 1) or 1)))
            return {
                "diagnostics": diagnostics,
                "checked_files": checked_files,
                "candidate_files": min(len(candidates), MAX_WORKSPACE_DIAGNOSTIC_FILES),
                "skipped_files": skipped_files,
                "partial": truncated,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
    except Exception as exc:
        return {"error": f"Workspace diagnostics failed: {exc}", "diagnostics": []}

PYTHON_INTELLIGENCE_MAX_BYTES = 1 * 1024 * 1024

def _python_script(relative_path: str, content: str):
    """Create a Jedi script over editor text without executing user code."""
    try:
        import jedi
    except ImportError as exc:
        raise RuntimeError("Python code intelligence requires Jedi. Install dependencies with run.bat or pip install -r requirements.txt.") from exc
    path = safe_path(relative_path)
    if path.suffix.lower() != ".py":
        raise ValueError("Python code intelligence is available for .py files only.")
    source = content if isinstance(content, str) else str(content)
    if len(source.encode("utf-8", errors="replace")) > PYTHON_INTELLIGENCE_MAX_BYTES:
        raise ValueError("Python code intelligence is limited to 1 MB of editor text.")
    return jedi.Script(code=source, path=str(path)), source, path


TYPESCRIPT_INTELLIGENCE_MAX_BYTES = 1 * 1024 * 1024
TYPESCRIPT_PROJECT_FILE_LIMIT = 350

@lru_cache(maxsize=16)
def _resolve_typescript_module(workspace: str, node_path: str) -> str | None:
    """Find a project-local or globally installed TypeScript compiler without requiring it."""
    root = Path(workspace).resolve()
    candidates: list[Path] = []
    candidates.extend([
        root / "node_modules/typescript/lib/typescript.js",
        APP_DIR / "node_modules/typescript/lib/typescript.js",
    ])
    for parent in [root, *root.parents]:
        candidates.append(parent / "node_modules/typescript/lib/typescript.js")
    node_exe = Path(node_path).resolve()
    if node_exe.parent.name.lower() in {"bin"}:
        candidates.append(node_exe.parent.parent / "lib/node_modules/typescript/lib/typescript.js")
    for entry in os.environ.get("NODE_PATH", "").split(os.pathsep):
        if entry.strip():
            candidates.append(Path(entry.strip()) / "typescript/lib/typescript.js")
            candidates.append(Path(entry.strip()) / "typescript.js")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    # npm's global root can differ from Node's installation directory on Windows.
    npm = shutil.which("npm")
    if npm:
        try:
            result = subprocess.run([npm, "root", "-g"], capture_output=True, text=True,
                                    errors="replace", timeout=3, cwd=str(root))
            if result.returncode == 0 and result.stdout.strip():
                candidate = Path(result.stdout.strip()) / "typescript/lib/typescript.js"
                if candidate.is_file():
                    return str(candidate.resolve())
        except Exception:
            pass
    try:
        result = subprocess.run([node_path, "-p", "require.resolve('typescript')"], cwd=str(root),
                                capture_output=True, text=True, errors="replace", timeout=3)
        if result.returncode == 0 and result.stdout.strip() and Path(result.stdout.strip()).is_file():
            return str(Path(result.stdout.strip()).resolve())
    except Exception:
        pass
    return None


_TYPESCRIPT_WORKER = r'''
const fs = require('fs');
const path = require('path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ts = require(process.argv[1]);
const root = path.resolve(input.workspace);
const active = path.resolve(root, input.path);
const activeSource = String(input.content ?? '');
const action = String(input.action || 'completions');
const maxFileBytes = 1024 * 1024;
const ignored = new Set(['.git', '.venv', 'venv', 'node_modules', '__pycache__', 'dist', 'build', '.idea']);
const extensions = new Set(['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.json']);
const isInside = filename => {
  const rel = path.relative(root, path.resolve(filename));
  return rel === '' || (!rel.startsWith('..' + path.sep) && rel !== '..' && !path.isAbsolute(rel));
};
if (!isInside(active) || !fs.existsSync(active) || !fs.statSync(active).isFile()) {
  process.stdout.write(JSON.stringify({error:'Active file is missing or outside the workspace.'})); process.exit(0);
}
if (!extensions.has(path.extname(active).toLowerCase())) {
  process.stdout.write(JSON.stringify({error:'TypeScript intelligence supports JavaScript/TypeScript files only.'})); process.exit(0);
}
if (Buffer.byteLength(activeSource, 'utf8') > maxFileBytes) {
  process.stdout.write(JSON.stringify({error:'TypeScript intelligence is limited to 1 MB of active editor text.'})); process.exit(0);
}
const files = new Set([active]);
const settings = {
  allowJs:true, checkJs:false, noEmit:true, target:ts.ScriptTarget.ES2022 || ts.ScriptTarget.Latest,
  module:ts.ModuleKind.ESNext, moduleResolution:ts.ModuleResolutionKind.Node10 || ts.ModuleResolutionKind.NodeJs,
  jsx:ts.JsxEmit.ReactJSX, resolveJsonModule:true, allowSyntheticDefaultImports:true,
  allowImportingTsExtensions:true, skipLibCheck:true, strict:false
};
let configDiagnostics = [];
let configuredProject = false;
const configPath = ts.findConfigFile(root, ts.sys.fileExists, 'tsconfig.json');
if (configPath && isInside(configPath)) {
  try {
    const read = ts.readConfigFile(configPath, ts.sys.readFile);
    if (read.error) configDiagnostics.push(read.error);
    else {
      const parsed = ts.parseJsonConfigFileContent(read.config, ts.sys, path.dirname(configPath), undefined, configPath);
      configDiagnostics.push(...(parsed.errors || []));
      Object.assign(settings, parsed.options || {});
      settings.noEmit = true;
      settings.skipLibCheck = true;
      settings.allowJs = parsed.options.allowJs !== undefined ? parsed.options.allowJs : true;
      const limit = Math.max(1, Number(input.fileLimit || 350));
      for (const filename of (parsed.fileNames || []).slice(0, Math.max(0, limit - files.size))) {
        const resolved = path.resolve(filename);
        if (!isInside(resolved) || !extensions.has(path.extname(resolved).toLowerCase())) continue;
        try { if (fs.statSync(resolved).isFile() && fs.statSync(resolved).size <= maxFileBytes) files.add(resolved); } catch (_) {}
      }
      configuredProject = files.size > 1 || (parsed.fileNames || []).length === 0;
    }
  } catch (error) {
    configDiagnostics.push({messageText:'Could not parse tsconfig.json: ' + String(error)});
  }
}
function walk(dir) {
  if (files.size >= Number(input.fileLimit || 350)) return;
  let items;
  try { items = fs.readdirSync(dir, {withFileTypes:true}); } catch (_) { return; }
  for (const item of items) {
    if (files.size >= Number(input.fileLimit || 350)) break;
    if (item.isSymbolicLink()) continue;
    const full = path.join(dir, item.name);
    if (item.isDirectory()) { if (!ignored.has(item.name)) walk(full); }
    else if (item.isFile() && extensions.has(path.extname(item.name).toLowerCase())) {
      try { if (fs.statSync(full).size <= maxFileBytes) files.add(path.resolve(full)); } catch (_) {}
    }
  }
}
if (!configuredProject) walk(root);
const versions = new Map();
const sourceCache = new Map();
function fileText(filename) {
  filename = path.resolve(filename);
  if (filename === active) return activeSource;
  if (sourceCache.has(filename)) return sourceCache.get(filename);
  if (!isInside(filename)) return undefined;
  try {
    const stat = fs.statSync(filename);
    if (!stat.isFile() || stat.size > maxFileBytes) return undefined;
    const text = fs.readFileSync(filename, 'utf8');
    sourceCache.set(filename, text);
    versions.set(filename, String(stat.mtimeMs));
    return text;
  } catch (_) { return undefined; }
}
const host = {
  getCompilationSettings:()=>settings,
  getScriptFileNames:()=>Array.from(files),
  getScriptVersion:filename=>versions.get(path.resolve(filename)) || '1',
  getScriptSnapshot:filename=>{ const text=fileText(filename); return text===undefined?undefined:ts.ScriptSnapshot.fromString(text); },
  getCurrentDirectory:()=>root,
  getDefaultLibFileName:options=>ts.getDefaultLibFilePath(options),
  fileExists:ts.sys.fileExists,
  readFile:ts.sys.readFile,
  readDirectory:ts.sys.readDirectory,
  directoryExists:ts.sys.directoryExists,
  getDirectories:ts.sys.getDirectories,
  realpath:ts.sys.realpath,
  useCaseSensitiveFileNames:()=>ts.sys.useCaseSensitiveFileNames,
  getNewLine:()=>ts.sys.newLine
};
const service = ts.createLanguageService(host, ts.createDocumentRegistry());
const sourceFile = ts.createSourceFile(active, activeSource, ts.ScriptTarget.Latest, true);
const lines = activeSource.split(/\r?\n/);
const line = Math.max(1, Math.min(Number(input.line || 1), Math.max(1, lines.length)));
const column = Math.max(0, Math.min(Number(input.column || 0), (lines[line - 1] || '').length));
const offset = sourceFile.getPositionOfLineAndCharacter(line - 1, column);
const relative = filename => {
  const resolved = path.resolve(filename);
  if (!isInside(resolved)) return null;
  return path.relative(root, resolved).split(path.sep).join('/');
};
const loc = (filename, pos, name, description) => {
  const rel = relative(filename); if (rel === null) return null;
  const source = filename === active ? sourceFile : ts.createSourceFile(filename, fileText(filename) || '', ts.ScriptTarget.Latest, true);
  const lc = source.getLineAndCharacterOfPosition(Math.max(0, pos || 0));
  return {path:rel, line:lc.line + 1, column:lc.character, name:name || '', description:description || '', external:false};
};
try {
  if (action === 'completions') {
    const result = service.getCompletionsAtPosition(active, offset, {includeCompletionsWithInsertText:true, includeCompletionsWithSnippetText:true, includeCompletionsForModuleExports:true});
    const items = [];
    for (const entry of (result?.entries || []).slice(0, 60)) {
      let description = entry.kind || 'suggestion';
      try {
        const detail = service.getCompletionEntryDetails(active, offset, entry.name, {}, entry.source, {includeCompletionsWithInsertText:true}, entry.data);
        if (detail) description = ts.displayPartsToString(detail.displayParts) || description;
      } catch (_) {}
      items.push({name:entry.name, type:entry.kind || 'suggestion', description:description.slice(0,180), insertText:entry.insertText || entry.name, sortText:entry.sortText || ''});
    }
    process.stdout.write(JSON.stringify({items,engine:'TypeScript',line,column}));
  } else if (action === 'definition') {
    const defs = service.getDefinitionAtPosition(active, offset) || service.getTypeDefinitionAtPosition(active, offset) || [];
    const definitions = defs.slice(0,30).map(d=>loc(d.fileName,d.textSpan.start,d.name,`${d.kind || 'symbol'} ${d.name || ''}`)).filter(Boolean);
    process.stdout.write(JSON.stringify({definitions,engine:'TypeScript'}));
  } else if (action === 'references') {
    const groups = service.findReferences(active, offset) || [];
    const references = [], seen = new Set();
    for (const group of groups) for (const ref of group.references || []) {
      if (!relative(ref.fileName)) continue;
      const item = loc(ref.fileName,ref.textSpan.start,group.definition?.name || '',group.definition?.displayParts ? ts.displayPartsToString(group.definition.displayParts) : 'reference');
      if (!item) continue;
      const key = `${item.path}:${item.line}:${item.column}`; if (seen.has(key)) continue; seen.add(key);
      item.isWriteAccess = !!ref.isWriteAccess; references.push(item);
      if (references.length >= 200) break;
    }
    process.stdout.write(JSON.stringify({references:references.slice(0,150),count:references.length,engine:'TypeScript'}));
  } else if (action === 'rename') {
    const newName = String(input.newName || '');
    const info = service.getRenameInfo(active, offset, {allowRenameOfImportPath:false, providePrefixAndSuffixTextForRename:true});
    if (!info.canRename) { process.stdout.write(JSON.stringify({error:info.localizedErrorMessage || 'This TypeScript symbol cannot be renamed.',canRename:false,locations:[]})); }
    else {
      const changes = service.findRenameLocations(active, offset, false, false, true) || [];
      const locations = changes.slice(0,1000).map(c=>({fileName:c.fileName,start:c.textSpan.start,length:c.textSpan.length,prefixText:c.prefixText || '',suffixText:c.suffixText || ''}));
      process.stdout.write(JSON.stringify({canRename:true,displayName:info.displayName,kind:info.kind,locations,engine:'TypeScript'}));
    }
  } else if (action === 'format') {
    const formatOptions = {indentSize:4, tabSize:4, convertTabsToSpaces:true, newLineCharacter:'\n', insertSpaceAfterCommaDelimiter:true, insertSpaceAfterSemicolonInForStatements:true, insertSpaceBeforeAndAfterBinaryOperators:true, insertSpaceAfterKeywordsInControlFlowStatements:true, insertSpaceAfterFunctionKeywordForAnonymousFunctions:true, insertSpaceBeforeFunctionParenthesis:false, placeOpenBraceOnNewLineForFunctions:false, placeOpenBraceOnNewLineForControlBlocks:false, semicolons:ts.SemicolonPreference.Insert};
    const edits = service.getFormattingEditsForDocument(active, formatOptions) || [];
    let formatted = activeSource;
    for (const edit of [...edits].sort((a,b)=>b.span.start-a.span.start)) {
      formatted = formatted.slice(0,edit.span.start) + edit.newText + formatted.slice(edit.span.start+edit.span.length);
    }
    process.stdout.write(JSON.stringify({formatted,changed:formatted!==activeSource,engine:'TypeScript',editCount:edits.length}));
  } else if (action === 'codefixes') {
    const formatOptions = {indentSize:4, tabSize:4, convertTabsToSpaces:true, newLineCharacter:'\n', insertSpaceAfterCommaDelimiter:true};
    const allDiagnostics = [...service.getSyntacticDiagnostics(active), ...service.getSemanticDiagnostics(active)];
    const lineDiagnostics = allDiagnostics.filter(d => {
      if (d.start == null) return false;
      return sourceFile.getLineAndCharacterOfPosition(Math.max(0, Math.min(d.start, activeSource.length))).line + 1 === line;
    }).slice(0, 12);
    const actions = [], seenActions = new Set();
    const allowedCodeExt = new Set(['.js','.jsx','.mjs','.cjs','.ts','.tsx']);
    const crypto = require('crypto');
    for (const diagnostic of lineDiagnostics) {
      const start = Math.max(0, Math.min(Number(diagnostic.start || 0), activeSource.length));
      const end = Math.max(start + 1, Math.min(activeSource.length, start + Math.max(1, Number(diagnostic.length || 1))));
      let fixes = [];
      try { fixes = service.getCodeFixesAtPosition(active, start, end, [diagnostic.code], formatOptions, {}) || []; } catch (_) {}
      for (const fix of fixes) {
        if (actions.length >= 12) break;
        if (!Array.isArray(fix.changes) || !fix.changes.length || fix.changes.length > 20 || (fix.commands && fix.commands.length)) continue;
        const actionKey = `${diagnostic.code}:${fix.fixName || fix.description}`;
        if (seenActions.has(actionKey)) continue;
        seenActions.add(actionKey);
        const changes = [];
        let outputSize = 0, valid = true;
        for (const fileChange of fix.changes.slice(0, 20)) {
          const filename = path.resolve(fileChange.fileName);
          const rel = relative(filename);
          if (!rel || !allowedCodeExt.has(path.extname(filename).toLowerCase()) || fileChange.isNewFile) { valid = false; break; }
          const original = fileText(filename);
          if (typeof original !== 'string' || !fs.existsSync(filename)) { valid = false; break; }
          let next = original;
          const edits = (fileChange.textChanges || []).map(edit => ({
            start: Math.max(0, Math.min(Number(edit.span?.start || 0), original.length)),
            end: Math.max(0, Math.min(original.length, Number(edit.span?.start || 0) + Number(edit.span?.length || 0))),
            text: String(edit.newText ?? '')
          })).sort((a,b) => b.start-a.start || b.end-a.end);
          let lastStart = original.length + 1;
          for (const edit of edits) {
            if (edit.end > lastStart) { valid = false; break; }
            next = next.slice(0, edit.start) + edit.text + next.slice(edit.end);
            lastStart = edit.start;
          }
          if (!valid || next === original) { if (!valid) break; else continue; }
          let diskBytes;
          try { diskBytes = fs.readFileSync(filename); } catch (_) { valid = false; break; }
          if (diskBytes.length > 5 * 1024 * 1024) { valid = false; break; }
          outputSize += Buffer.byteLength(next, 'utf8');
          if (outputSize > 8 * 1024 * 1024) { valid = false; break; }
          changes.push({path:rel, new_content:next, original_sha256:crypto.createHash('sha256').update(diskBytes).digest('hex')});
        }
        if (!valid || !changes.length) continue;
        const firstLine = sourceFile.getLineAndCharacterOfPosition(start);
        actions.push({id:actionKey, description:String(fix.description || 'Apply quick fix').slice(0,240),
          fix_name:String(fix.fixName || '').slice(0,120), diagnostic:{code:diagnostic.code,
          message:ts.flattenDiagnosticMessageText(diagnostic.messageText,' ').slice(0,400), line:firstLine.line+1,
          column:firstLine.character+1}, changes, count:changes.length});
      }
      if (actions.length >= 12) break;
    }
    process.stdout.write(JSON.stringify({actions, count:actions.length, engine:'TypeScript', line}));
  } else if (action === 'diagnostics') {
    const diagnostics = configDiagnostics.map(d => {
      const start = d.start == null ? 0 : d.start;
      const source = d.file || ts.createSourceFile(configPath || active, '', ts.ScriptTarget.Latest, true);
      const lc = source.getLineAndCharacterOfPosition(Math.max(0, Math.min(start, source.text.length)));
      return {severity:d.category === ts.DiagnosticCategory.Warning ? 'warning' : 'error', message:ts.flattenDiagnosticMessageText(d.messageText || d.message || 'Invalid tsconfig.json', '\n').slice(0,800), line:lc.line+1, column:lc.character+1, source:'tsconfig.json'};
    });
    const sourceDiagnostics = [
      ...service.getSyntacticDiagnostics(active),
      ...service.getSemanticDiagnostics(active)
    ];
    const seenDiagnostics = new Set();
    for (const d of sourceDiagnostics) {
      const start = d.start == null ? 0 : d.start;
      const lc = sourceFile.getLineAndCharacterOfPosition(Math.max(0, Math.min(start, sourceFile.text.length)));
      const message = ts.flattenDiagnosticMessageText(d.messageText, '\n').slice(0,800);
      const key = `${d.code}:${start}:${message}`;
      if (seenDiagnostics.has(key)) continue;
      seenDiagnostics.add(key);
      diagnostics.push({severity:d.category === ts.DiagnosticCategory.Error ? 'error' : 'warning', message, line:lc.line+1, column:lc.character+1, source:'TypeScript'});
    }
    process.stdout.write(JSON.stringify({diagnostics:diagnostics.slice(0,150),checked:true,engine:'TypeScript',projectConfig:configPath ? 'tsconfig.json' : null}));
  } else if (action === 'hover') {
    const info = service.getQuickInfoAtPosition(active, offset);
    if (!info) { process.stdout.write(JSON.stringify({items:[],engine:'TypeScript'})); }
    else {
      const signature = ts.displayPartsToString(info.displayParts || []);
      const docstring = ts.displayPartsToString(info.documentation || []);
      const tags = (info.tags || []).map(t=>`@${t.name} ${ts.displayPartsToString(t.text || [])}`).join('\n');
      process.stdout.write(JSON.stringify({items:[{name:signature.split(/[ (]/).filter(Boolean).pop() || '',type:info.kind || 'symbol',signature:signature.slice(0,500),docstring:(docstring+'\n'+tags).slice(0,2000)}],engine:'TypeScript'}));
    }
  } else {
    process.stdout.write(JSON.stringify({error:'Unsupported TypeScript intelligence action.'}));
  }
} catch (error) {
  process.stdout.write(JSON.stringify({error:String(error?.stack || error).slice(0,2500)}));
}
'''


@eel.expose
def typescript_intelligence(relative_path: str, content: str, line: int, column: int, action: str = "completions", new_name: str = "") -> dict[str, Any]:
    """Optional TypeScript compiler language-service features for JS/TS; never executes workspace source."""
    allowed_extensions = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
    try:
        target = safe_path(relative_path)
        if not target.is_file() or target.suffix.lower() not in allowed_extensions:
            return {"error": "TypeScript intelligence supports .js/.jsx/.mjs/.cjs/.ts/.tsx files only."}
        source = content if isinstance(content, str) else str(content)
        if len(source.encode("utf-8", errors="replace")) > TYPESCRIPT_INTELLIGENCE_MAX_BYTES:
            return {"error": "TypeScript intelligence is limited to 1 MB of active editor text."}
        node = shutil.which("node")
        if not node:
            return {"error": "Node.js is required for JavaScript/TypeScript code intelligence."}
        compiler = _resolve_typescript_module(str(workspace_root()), node)
        if not compiler:
            return {"error": "TypeScript compiler was not found. Run npm install in the FlowCode Studio folder or install TypeScript globally; basic word suggestions remain available."}
        if action not in {"completions", "definition", "references", "hover", "diagnostics", "rename", "format", "codefixes"}:
            return {"error": "Unsupported TypeScript intelligence action."}
        payload = {"workspace": str(workspace_root()), "path": target.relative_to(workspace_root()).as_posix(),
                   "content": source, "line": int(line or 1), "column": int(column or 0),
                   "action": action, "newName": new_name, "fileLimit": TYPESCRIPT_PROJECT_FILE_LIMIT}
        try:
            cp = subprocess.run([node, "-e", _TYPESCRIPT_WORKER, compiler], input=json.dumps(payload),
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(workspace_root()), timeout=10)
        except subprocess.TimeoutExpired:
            return {"error": "TypeScript analysis exceeded the 10-second limit."}
        if cp.returncode != 0:
            return {"error": (cp.stderr or cp.stdout or "TypeScript analysis failed.")[-2500:]}
        try:
            response = json.loads(cp.stdout)
        except json.JSONDecodeError:
            return {"error": "TypeScript analysis returned an invalid response.", "detail": cp.stdout[-1200:]}
        if isinstance(response, dict):
            response.setdefault("engine", "TypeScript")
            return response
        return {"error": "TypeScript analysis returned an invalid result."}
    except Exception as exc:
        return {"error": str(exc)}


@eel.expose
def typescript_code_fixes(relative_path: str, content: str, line: int, column: int) -> dict[str, Any]:
    """Preview TypeScript compiler code fixes for active-line diagnostics; never writes files."""
    try:
        target = safe_path(relative_path)
        if not target.is_file() or target.suffix.lower() not in TYPESCRIPT_RENAME_EXTENSIONS:
            return {"error": "Quick Fix supports .js/.jsx/.mjs/.cjs/.ts/.tsx files only.", "actions": []}
        source = content if isinstance(content, str) else str(content)
        if len(source.encode("utf-8", errors="replace")) > TYPESCRIPT_INTELLIGENCE_MAX_BYTES:
            return {"error": "Quick Fix is limited to 1 MB of editor text.", "actions": []}
        # Preview hashes correspond to on-disk content. Require a saved active file so
        # fixes cannot silently discard an unsaved draft when applying cross-file edits.
        disk = target.read_bytes()
        if len(disk) > MAX_READ_BYTES:
            return {"error": "Active file exceeds the Quick Fix size limit.", "actions": []}
        try:
            disk_text = disk.decode("utf-8")
        except UnicodeDecodeError:
            return {"error": "Quick Fix requires UTF-8 source files.", "actions": []}
        if disk_text != source:
            return {"error": "Save the active file before previewing Quick Fix changes.", "actions": []}
        result = typescript_intelligence(relative_path, source, line, column, "codefixes")
        if result.get("error"):
            return {"error": result["error"], "actions": []}
        actions = result.get("actions") or []
        normalized: list[dict[str, Any]] = []
        total_bytes = 0
        import difflib
        for action in actions[:12]:
            if not isinstance(action, dict) or not isinstance(action.get("changes"), list):
                continue
            changes: list[dict[str, Any]] = []
            seen: set[str] = set()
            valid = True
            for change in action["changes"][:20]:
                relative = str(change.get("path", ""))
                if relative in seen:
                    valid = False; break
                seen.add(relative)
                try:
                    changed_path = safe_path(relative)
                    if (not changed_path.is_file() or changed_path.suffix.lower() not in TYPESCRIPT_RENAME_EXTENSIONS
                            or changed_path.stat().st_size > MAX_READ_BYTES):
                        valid = False; break
                    original_bytes = changed_path.read_bytes()
                    original_sha = hashlib.sha256(original_bytes).hexdigest()
                    if original_sha != change.get("original_sha256"):
                        valid = False; break
                    new_content = change.get("new_content")
                    if not isinstance(new_content, str):
                        valid = False; break
                    new_bytes = new_content.encode("utf-8")
                    total_bytes += len(new_bytes)
                    if len(new_bytes) > MAX_READ_BYTES or total_bytes > 8 * 1024 * 1024:
                        valid = False; break
                    old_text = original_bytes.decode("utf-8")
                    diff = "".join(difflib.unified_diff(old_text.splitlines(keepends=True),
                        new_content.splitlines(keepends=True), fromfile=relative, tofile=relative, n=2))
                    changes.append({"path": relative, "new_content": new_content, "diff": diff,
                                    "original_sha256": original_sha, "is_current_file": changed_path == target})
                except Exception:
                    valid = False; break
            if valid and changes:
                normalized.append({"id": str(action.get("id", ""))[:200],
                    "description": str(action.get("description", "Quick Fix"))[:240],
                    "diagnostic": action.get("diagnostic") if isinstance(action.get("diagnostic"), dict) else {},
                    "changes": changes, "count": len(changes)})
        return {"actions": normalized, "count": len(normalized), "engine": "TypeScript", "preview_only": True}
    except Exception as exc:
        return {"error": str(exc), "actions": []}


@eel.expose
def apply_typescript_code_fix(changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Atomically apply a previously previewed Quick Fix if all source files are unchanged."""
    return _apply_workspace_text_changes(changes, TYPESCRIPT_RENAME_EXTENSIONS, max_files=20)


@eel.expose
def format_javascript_document(relative_path: str, content: str) -> dict[str, Any]:
    """Format JS/TS source using the installed TypeScript language service without writing files."""
    try:
        target = safe_path(relative_path)
        if not target.is_file() or target.suffix.lower() not in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
            return {"error": "JavaScript/TypeScript formatting supports .js/.jsx/.mjs/.cjs/.ts/.tsx files only."}
        source = content if isinstance(content, str) else str(content)
        if len(source.encode("utf-8", errors="replace")) > TYPESCRIPT_INTELLIGENCE_MAX_BYTES:
            return {"error": "JavaScript/TypeScript formatting is limited to 1 MB of source text."}
        result = typescript_intelligence(relative_path, source, 1, 0, "format")
        if result.get("error"):
            return result
        formatted = result.get("formatted")
        if not isinstance(formatted, str):
            return {"error": "TypeScript formatter returned an invalid response."}
        return {"formatted": formatted, "changed": formatted != source,
                "engine": "TypeScript", "edit_count": int(result.get("editCount", 0))}
    except Exception as exc:
        return {"error": str(exc)}


TYPESCRIPT_RENAME_EXTENSIONS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
TYPESCRIPT_RESERVED_WORDS = {"await", "break", "case", "catch", "class", "const", "continue", "debugger", "default", "delete", "do", "else", "enum", "export", "extends", "false", "finally", "for", "function", "if", "import", "in", "instanceof", "new", "null", "return", "super", "switch", "this", "throw", "true", "try", "typeof", "var", "void", "while", "with", "yield", "let", "static", "implements", "interface", "package", "private", "protected", "public", "arguments", "eval"}


def _utf16_offset_to_python_index(text: str, offset: int) -> int:
    """Convert a TypeScript UTF-16 offset into a Python Unicode code-point index."""
    remaining = max(0, int(offset))
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xFFFF else 1
        if remaining < width:
            return index
        remaining -= width
    return len(text)


@eel.expose
def javascript_rename_symbol(relative_path: str, content: str, line: int, column: int, new_name: str) -> dict[str, Any]:
    """Preview TypeScript language-service symbol rename across JS/TS workspace files."""
    replacement = (new_name or "").strip()
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]{0,127}", replacement) or replacement in TYPESCRIPT_RESERVED_WORDS:
        return {"error": "Enter a valid JavaScript/TypeScript identifier (not a reserved word).", "changes": []}
    try:
        target = safe_path(relative_path)
        if not target.is_file() or target.suffix.lower() not in TYPESCRIPT_RENAME_EXTENSIONS:
            return {"error": "Rename Symbol supports .js/.jsx/.mjs/.cjs/.ts/.tsx files only.", "changes": []}
        result = typescript_intelligence(relative_path, content, line, column, "rename", replacement)
        if result.get("error"):
            return {"error": result["error"], "changes": []}
        locations = result.get("locations") or []
        if not locations:
            return {"error": "No rename locations were found for the symbol at the cursor.", "changes": []}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for location in locations:
            filename = Path(str(location.get("fileName", ""))).resolve()
            try:
                relative = filename.relative_to(workspace_root()).as_posix()
                safe = safe_path(relative)
            except Exception:
                continue
            if safe.suffix.lower() not in TYPESCRIPT_RENAME_EXTENSIONS or not safe.is_file():
                continue
            grouped.setdefault(relative, []).append(location)
        if not grouped:
            return {"error": "No workspace source files can be safely changed for this rename.", "changes": []}
        if len(grouped) > 100:
            return {"error": "Rename would touch more than 100 files. Narrow the refactor first.", "changes": []}
        import difflib
        changes: list[dict[str, Any]] = []
        total_bytes = 0
        for relative, entries in grouped.items():
            path = safe_path(relative)
            original_bytes = path.read_bytes()
            if len(original_bytes) > MAX_READ_BYTES:
                return {"error": f"{relative} exceeds the 5 MB rename limit.", "changes": []}
            try:
                original = original_bytes.decode("utf-8")
            except UnicodeDecodeError:
                continue
            new_content = original
            edits: list[tuple[int, int, str]] = []
            for item in entries:
                start = _utf16_offset_to_python_index(original, int(item.get("start", 0)))
                end = _utf16_offset_to_python_index(original, int(item.get("start", 0)) + int(item.get("length", 0)))
                prefix = str(item.get("prefixText") or "")
                suffix = str(item.get("suffixText") or "")
                edits.append((start, end, prefix + replacement + suffix))
            edits.sort(key=lambda edit: (edit[0], edit[1]), reverse=True)
            last_start = len(original) + 1
            for start, end, insert_text in edits:
                if end > last_start:
                    continue
                new_content = new_content[:start] + insert_text + new_content[end:]
                last_start = start
            if new_content == original:
                continue
            encoded = new_content.encode("utf-8")
            total_bytes += len(encoded)
            if len(encoded) > MAX_READ_BYTES or total_bytes > 12 * 1024 * 1024:
                return {"error": "Rename output exceeds the file or batch size limit.", "changes": []}
            diff = "".join(difflib.unified_diff(original.splitlines(keepends=True), new_content.splitlines(keepends=True), fromfile=relative, tofile=relative, n=2))
            changes.append({"path": relative, "new_content": new_content, "diff": diff,
                            "matches": len(edits), "original_sha256": hashlib.sha256(original_bytes).hexdigest(),
                            "is_current_file": path == target})
        changes.sort(key=lambda item: (item["path"] != relative_path, item["path"].casefold()))
        return {"changes": changes, "count": len(changes), "new_name": replacement,
                "engine": "TypeScript", "preview_only": True}
    except Exception as exc:
        return {"error": str(exc), "changes": []}


@eel.expose
def apply_javascript_rename(changes: list[dict[str, Any]]) -> dict[str, Any]:
    return _apply_workspace_text_changes(changes, TYPESCRIPT_RENAME_EXTENSIONS, max_files=100)


def _safe_jedi_location(module_path: Any, line: Any, column: Any, name: str, description: str) -> dict[str, Any]:
    root = workspace_root()
    result: dict[str, Any] = {"name": name, "description": description,
                              "line": int(line or 1), "column": int(column or 0)}
    try:
        candidate = Path(module_path).resolve() if module_path else None
        if candidate is None:
            result["path"] = None
            result["external"] = True
        else:
            rel = candidate.relative_to(root).as_posix()
            result["path"] = rel
            result["external"] = False
    except (ValueError, OSError, TypeError):
        result["path"] = None
        result["external"] = True
    return result


@eel.expose
def python_completions(relative_path: str, content: str, line: int, column: int) -> dict[str, Any]:
    """Return Python-aware completions (imports, attributes and local symbols) via Jedi."""
    try:
        script, source, _path = _python_script(relative_path, content)
        rows = source.splitlines() or [""]
        line_number = max(1, min(int(line or 1), len(rows)))
        col = max(0, min(int(column or 0), len(rows[line_number - 1])))
        completions = script.complete(line=line_number, column=col, fuzzy=False)
        items: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for completion in completions[:80]:
            name = str(completion.name)
            typ = str(getattr(completion, "type", "name"))
            key = (name, typ)
            if key in seen:
                continue
            seen.add(key)
            try: description = str(completion.description)
            except Exception: description = typ
            items.append({"name": name, "type": typ, "description": description[:180]})
        return {"items": items, "line": line_number, "column": col, "engine": "Jedi"}
    except Exception as exc:
        return {"error": str(exc), "items": []}


@eel.expose
def python_goto_definition(relative_path: str, content: str, line: int, column: int) -> dict[str, Any]:
    """Resolve the symbol at the caret without running the file."""
    try:
        script, source, _path = _python_script(relative_path, content)
        rows = source.splitlines() or [""]
        line_number = max(1, min(int(line or 1), len(rows)))
        col = max(0, min(int(column or 0), len(rows[line_number - 1])))
        found = script.goto(line=line_number, column=col, follow_imports=True, only_stubs=False)
        definitions = []
        for item in found[:10]:
            location = _safe_jedi_location(getattr(item, "module_path", None),
                getattr(item, "line", None), getattr(item, "column", None),
                str(getattr(item, "name", "")), str(getattr(item, "description", "")))
            try: location["docstring"] = str(item.docstring(raw=False) or "")[:1200]
            except Exception: location["docstring"] = ""
            definitions.append(location)
        return {"definitions": definitions, "engine": "Jedi"}
    except Exception as exc:
        return {"error": str(exc), "definitions": []}


@eel.expose
def python_hover(relative_path: str, content: str, line: int, column: int) -> dict[str, Any]:
    """Return a short inferred symbol signature and documentation for the caret position."""
    try:
        script, source, _path = _python_script(relative_path, content)
        rows = source.splitlines() or [""]
        line_number = max(1, min(int(line or 1), len(rows)))
        col = max(0, min(int(column or 0), len(rows[line_number - 1])))
        inferred = script.infer(line=line_number, column=col, only_stubs=False, prefer_stubs=False)
        items = []
        for item in inferred[:5]:
            try: doc = str(item.docstring(raw=False) or "")
            except Exception: doc = ""
            items.append({"name": str(getattr(item, "name", "")),
                          "type": str(getattr(item, "type", "name")),
                          "signature": str(getattr(item, "description", ""))[:240],
                          "docstring": doc[:1800]})
        return {"items": items, "engine": "Jedi"}
    except Exception as exc:
        return {"error": str(exc), "items": []}


@eel.expose
def python_references(relative_path: str, content: str, line: int, column: int) -> dict[str, Any]:
    """Find symbol references known to Jedi, restricting navigable results to this workspace."""
    try:
        script, source, _path = _python_script(relative_path, content)
        rows = source.splitlines() or [""]
        line_number = max(1, min(int(line or 1), len(rows)))
        col = max(0, min(int(column or 0), len(rows[line_number - 1])))
        refs = script.get_references(line=line_number, column=col, include_builtins=False, scope="project")
        results = []
        seen: set[tuple[str, int, int]] = set()
        for item in refs[:200]:
            loc = _safe_jedi_location(getattr(item, "module_path", None), getattr(item, "line", None),
                                      getattr(item, "column", None), str(getattr(item, "name", "")),
                                      str(getattr(item, "description", "")))
            if not loc.get("path"):
                continue
            key = (loc["path"], loc["line"], loc["column"])
            if key in seen:
                continue
            seen.add(key)
            results.append(loc)
        return {"references": results[:100], "count": len(results), "engine": "Jedi"}
    except Exception as exc:
        return {"error": str(exc), "references": []}


@eel.expose
def python_rename_symbol(relative_path: str, content: str, line: int, column: int, new_name: str) -> dict[str, Any]:
    """Preview Jedi symbol rename across workspace files. Does not write anything."""
    try:
        replacement = (new_name or "").strip()
        if not replacement.isidentifier() or keyword.iskeyword(replacement) or len(replacement) > 128:
            return {"error": "Enter a valid Python identifier (not a keyword)."}
        script, source, current_path = _python_script(relative_path, content)
        rows = source.splitlines() or [""]
        line_number = max(1, min(int(line or 1), len(rows)))
        col = max(0, min(int(column or 0), len(rows[line_number - 1])))
        refactor = script.rename(line=line_number, column=col, new_name=replacement)
        changed_files = refactor.get_changed_files()
        if not changed_files:
            return {"error": "No references were renamed. Place the cursor on a Python symbol and try again."}
        if len(changed_files) > 50:
            return {"error": "Rename would touch more than 50 files. Narrow the refactor before applying it."}
        base = workspace_root()
        changes: list[dict[str, Any]] = []
        total_bytes = 0
        for candidate, change in changed_files.items():
            try:
                candidate_path = Path(candidate).absolute()
                relative = candidate_path.relative_to(base).as_posix()
                target = safe_path(relative)
            except Exception:
                return {"error": "Rename includes a file outside the opened workspace; no changes were applied."}
            if target.suffix.lower() != ".py" or not target.is_file():
                return {"error": f"Rename includes an unsupported or missing file: {relative}"}
            original = target.read_bytes()
            new_content = change.get_new_code()
            encoded = new_content.encode("utf-8")
            if len(encoded) > MAX_READ_BYTES:
                return {"error": f"Rename output for {relative} exceeds the editor file-size limit."}
            total_bytes += len(encoded)
            if total_bytes > 12 * 1024 * 1024:
                return {"error": "Rename preview exceeds the 12 MB safety limit."}
            changes.append({"path": relative, "new_content": new_content,
                            "diff": change.get_diff(),
                            "original_sha256": hashlib.sha256(original).hexdigest(),
                            "is_current_file": target == current_path})
        changes.sort(key=lambda item: (item["path"] != relative_path, item["path"].casefold()))
        return {"changes": changes, "count": len(changes), "new_name": replacement,
                "engine": "Jedi", "preview_only": True}
    except Exception as exc:
        return {"error": str(exc), "changes": []}


@eel.expose
def apply_python_rename(changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply a previously reviewed multi-file rename with optimistic concurrency and rollback."""
    if not isinstance(changes, list) or not changes or len(changes) > 50:
        return {"error": "The rename batch is empty or exceeds the 50-file limit."}
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_bytes = 0
    try:
        for item in changes:
            if not isinstance(item, dict):
                return {"error": "Malformed rename batch."}
            relative = str(item.get("path", ""))
            if relative in seen:
                return {"error": f"Duplicate file in rename batch: {relative}"}
            seen.add(relative)
            target = safe_path(relative)
            if target.suffix.lower() != ".py" or not target.is_file():
                return {"error": f"Unsupported or missing target: {relative}"}
            original = target.read_bytes()
            expected = str(item.get("original_sha256", ""))
            actual = hashlib.sha256(original).hexdigest()
            if not expected or actual != expected:
                return {"error": f"{relative} changed on disk after the rename preview. Refresh the preview before applying."}
            content = item.get("new_content")
            if not isinstance(content, str):
                return {"error": f"Malformed output for {relative}."}
            new_bytes = content.encode("utf-8")
            if len(new_bytes) > MAX_READ_BYTES:
                return {"error": f"Rename output for {relative} exceeds the editor file-size limit."}
            total_bytes += len(new_bytes)
            if total_bytes > 12 * 1024 * 1024:
                return {"error": "Rename batch exceeds the 12 MB safety limit."}
            plan.append({"path": target, "relative": relative, "old_bytes": original,
                         "new_bytes": new_bytes, "mode": target.stat().st_mode & 0o777,
                         "new_temp": None, "backup_temp": None, "committed": False})
        # Stage both replacements and rollback copies before changing any real file.
        for item in plan:
            for field, data, prefix in (("new_temp", item["new_bytes"], ".flowcode-new-"),
                                        ("backup_temp", item["old_bytes"], ".flowcode-backup-")):
                with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(item["path"].parent), prefix=prefix) as fh:
                    fh.write(data); fh.flush(); os.fsync(fh.fileno())
                    item[field] = fh.name
                os.chmod(item[field], item["mode"])
        try:
            for item in plan:
                # Recheck just before each replace to avoid silently overwriting concurrent edits.
                now = hashlib.sha256(item["path"].read_bytes()).hexdigest()
                expected = next(str(x.get("original_sha256", "")) for x in changes if x.get("path") == item["relative"])
                if now != expected:
                    raise RuntimeError(f"{item['relative']} changed while applying the rename.")
                os.replace(item["new_temp"], item["path"])
                item["new_temp"] = None
                item["committed"] = True
        except Exception:
            for item in reversed(plan):
                if item["committed"] and item["backup_temp"]:
                    try:
                        os.replace(item["backup_temp"], item["path"])
                        item["backup_temp"] = None
                    except OSError:
                        pass
            raise
        return {"ok": True, "updated": [{"path": x["relative"], "size": len(x["new_bytes"]), "sha256": hashlib.sha256(x["new_bytes"]).hexdigest()} for x in plan]}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        for item in plan:
            for field in ("new_temp", "backup_temp"):
                temp_path = item.get(field)
                if temp_path:
                    try: Path(temp_path).unlink(missing_ok=True)
                    except OSError: pass


def _render_markdown_inline(source: str) -> str:
    """Render a restrained Markdown inline subset after escaping raw HTML."""
    import html as html_module
    stored: list[str] = []
    def stash(markup: str) -> str:
        token = f"\ue000FLOW{len(stored)}TOKEN\ue001"
        stored.append(markup)
        return token
    text = re.sub(r'`([^`]+)`', lambda m: stash('<code>' + html_module.escape(m.group(1), quote=False) + '</code>'), source)
    def link_replace(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2).strip()
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"https", "http", "mailto"}:
            return html_module.escape(label, quote=False)
        safe_url = html_module.escape(url, quote=True)
        safe_label = html_module.escape(label, quote=False)
        return stash(f'<a href="{safe_url}" rel="noreferrer">{safe_label}</a>')
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link_replace, text)
    escaped = html_module.escape(text, quote=False)
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
    escaped = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', escaped)
    escaped = re.sub(r'~~(.+?)~~', r'<del>\1</del>', escaped)
    for index, markup in enumerate(stored):
        escaped = escaped.replace(f"\ue000FLOW{index}TOKEN\ue001", markup)
    return escaped


def _render_markdown(source: str, title: str) -> str:
    """Convert common Markdown blocks into self-contained, script-free HTML."""
    import html as html_module
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    in_code = False
    code_lines: list[str] = []
    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append("<p>" + " ".join(_render_markdown_inline(x.strip()) for x in paragraph) + "</p>")
            paragraph = []
    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = None
    for line in lines:
        if line.strip().startswith("```"):
            flush_paragraph(); close_list()
            if in_code:
                out.append("<pre><code>" + html_module.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []; in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph(); close_list(); continue
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            flush_paragraph(); close_list(); level = len(heading.group(1))
            out.append(f"<h{level}>" + _render_markdown_inline(heading.group(2)) + f"</h{level}>")
            continue
        if re.match(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$", line):
            flush_paragraph(); close_list(); out.append("<hr>"); continue
        ul = re.match(r"^\s*[-+*]\s+(.+)$", line)
        ol = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        if ul or ol:
            flush_paragraph(); target = "ul" if ul else "ol"
            if list_kind != target:
                close_list(); out.append(f"<{target}>"); list_kind = target
            out.append("<li>" + _render_markdown_inline((ul or ol).group(1)) + "</li>")
            continue
        quote = re.match(r"^\s{0,3}>\s?(.*)$", line)
        if quote:
            flush_paragraph(); close_list(); out.append("<blockquote>" + _render_markdown_inline(quote.group(1)) + "</blockquote>"); continue
        close_list(); paragraph.append(line)
    flush_paragraph(); close_list()
    if in_code:
        out.append("<pre><code>" + html_module.escape("\n".join(code_lines)) + "</code></pre>")
    safe_title = html_module.escape(title, quote=False)
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>""" + safe_title + """</title><style>
    :root{color-scheme:light}*{box-sizing:border-box}body{margin:0;padding:36px max(24px,calc((100vw - 850px)/2));font:15px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#242833;background:#fff;overflow-wrap:anywhere}h1,h2,h3,h4,h5,h6{line-height:1.3;color:#181b23;margin:1.4em 0 .55em}h1{font-size:2.1rem;border-bottom:1px solid #e4e7ed;padding-bottom:.3em}h2{font-size:1.55rem;border-bottom:1px solid #e9ebef;padding-bottom:.25em}p{margin:.7em 0}a{color:#445dcc}code{font: .9em ui-monospace,SFMono-Regular,Consolas,monospace;background:#f0f2f6;padding:.18em .35em;border-radius:4px}pre{overflow:auto;background:#f4f5f8;border:1px solid #e3e6ec;border-radius:7px;padding:15px}pre code{background:transparent;padding:0;white-space:pre}blockquote{margin:1em 0;padding:.25em 1em;border-left:3px solid #bcc6f5;color:#596174;background:#fafbfe}li{margin:.25em 0}hr{border:0;border-top:1px solid #e0e3e9;margin:1.8em 0}img{max-width:100%}</style></head><body>""" + "\n".join(out) + "</body></html>"


@eel.expose
def preview_html(relative_path: str) -> dict[str, Any]:
    """Build a sandboxed preview for HTML pages or Markdown documents."""
    try:
        html_path = safe_path(relative_path)
        if not html_path.is_file() or html_path.suffix.lower() not in {".html", ".htm", ".md", ".markdown"}:
            return {"error": "Select an HTML or Markdown file to preview."}
        if html_path.stat().st_size > 2 * 1024 * 1024:
            return {"error": "Document preview is limited to 2 MB source files."}
        source = html_path.read_text(encoding="utf-8")
        base = html_path.parent
        local_assets: list[str] = []
        if html_path.suffix.lower() in {".md", ".markdown"}:
            rendered = _render_markdown(source, html_path.name)
            return {"html": rendered, "path": html_path.relative_to(workspace_root()).as_posix(),
                    "assets": [], "isolated": True, "kind": "markdown"}

        def local_asset(url: str) -> str | None:
            parsed = urlsplit(url.strip())
            if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("/"):
                return None
            decoded = unquote(parsed.path)
            candidate = Path(os.path.abspath(base / decoded))
            try:
                # Keep the lexical path so safe_path can detect symlink components.
                relative = candidate.relative_to(workspace_root()).as_posix()
                return safe_path(relative)
            except (ValueError, OSError):
                return None

        link_re = re.compile(r'<link\b(?=[^>]*\brel\s*=\s*["\']?stylesheet\b)[^>]*>', re.I)
        href_re = re.compile(r'\bhref\s*=\s*(["\'])(.*?)\1', re.I | re.S)
        for match in reversed(list(link_re.finditer(source))):
            tag = match.group(0)
            href = href_re.search(tag)
            if not href:
                continue
            asset = local_asset(href.group(2))
            if not asset or not asset.is_file() or asset.stat().st_size > 1_000_000 or asset.suffix.lower() != ".css":
                continue
            css = asset.read_text(encoding="utf-8", errors="replace").replace("</style", "<\\/style")
            replacement = f"<style>\n{css}\n</style>"
            source = source[:match.start()] + replacement + source[match.end():]
            local_assets.append(asset.name)

        script_re = re.compile(r'<script\b(?=[^>]*\bsrc\s*=\s*["\'])([^>]*)>\s*</script\s*>', re.I | re.S)
        src_re = re.compile(r'\bsrc\s*=\s*(["\'])(.*?)\1', re.I | re.S)
        for match in reversed(list(script_re.finditer(source))):
            tag_attrs = match.group(1)
            src = src_re.search(tag_attrs)
            if not src:
                continue
            asset = local_asset(src.group(2))
            if not asset or not asset.is_file() or asset.stat().st_size > 1_000_000 or asset.suffix.lower() not in {".js", ".mjs", ".cjs"}:
                continue
            script = asset.read_text(encoding="utf-8", errors="replace").replace("</script", "<\\/script")
            attrs = re.sub(r'\bsrc\s*=\s*(["\']).*?\1', '', tag_attrs, flags=re.I | re.S)
            replacement = f"<script{attrs}>\n{script}\n</script>"
            source = source[:match.start()] + replacement + source[match.end():]
            local_assets.append(asset.name)

        return {"html": source, "path": html_path.relative_to(workspace_root()).as_posix(),
                "assets": local_assets, "isolated": True}
    except Exception as exc:
        return {"error": str(exc)}


def _git_status_snapshot() -> dict[str, Any]:
    """Return branch status and a bounded list of changed paths using NUL-delimited porcelain."""
    try:
        cp = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--branch", "--untracked-files=all"],
            cwd=str(workspace_root()), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=COMMAND_TIMEOUT,
        )
        if cp.returncode != 0:
            return {"stdout": cp.stdout[-50000:], "stderr": cp.stderr[-50000:],
                    "returncode": cp.returncode, "timed_out": False, "files": []}
        parts = cp.stdout.split("\0")
        branch = ""
        files: list[dict[str, Any]] = []
        display_lines: list[str] = []
        index = 0
        while index < len(parts):
            token = parts[index]
            index += 1
            if not token:
                continue
            if token.startswith("## "):
                branch = token[3:].strip()
                display_lines.append(token)
                continue
            if len(token) < 3 or token[2] != " ":
                continue
            status = token[:2]
            path = token[3:]
            item: dict[str, Any] = {
                "status": status,
                "path": path,
                "staged": status != "??" and status[0] != " ",
                "unstaged": status == "??" or status[1] != " ",
                "untracked": status == "??",
                "deleted": "D" in status,
                "renamed": "R" in status or "C" in status,
            }
            if item["renamed"] and index < len(parts):
                item["original_path"] = parts[index]
                index += 1
            files.append(item)
            display_lines.append(f"{status} {path}")
            if len(files) >= 301:
                break
        truncated = len(files) > 300
        files = files[:300]
        if truncated:
            display_lines.append("… additional changes omitted (showing first 300 files)")
        stdout = "\n".join(display_lines)
        return {"stdout": stdout[-50000:], "stderr": "", "returncode": 0,
                "timed_out": False, "branch": branch, "files": files,
                "files_truncated": truncated}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Git status timed out after {COMMAND_TIMEOUT} seconds.",
                "returncode": -1, "timed_out": True, "files": []}
    except FileNotFoundError:
        return {"error": "Required command not found: git", "files": []}
    except Exception as exc:
        return {"error": str(exc), "files": []}


def _git_hunk_snapshot(relative: str, *, staged: bool = False) -> dict[str, Any]:
    """Return bounded, index-addressed text hunks for one tracked workspace file."""
    try:
        path = safe_path(relative, allow_root=False)
        if path.exists() and not path.is_file():
            return {"error": "Hunk operations require a single regular file."}
        checked = _execute(["git", "ls-files", "--error-unmatch", "--", relative])
        if checked.get("returncode") != 0:
            return {"error": "Hunk staging is available for tracked text files only. Stage a new file normally first."}
        command = ["git", "diff", "--no-ext-diff", "--no-color", "--unified=3"]
        if staged:
            command.append("--cached")
        command.extend(["--", relative])
        try:
            cp = subprocess.run(command, cwd=str(workspace_root()), capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=COMMAND_TIMEOUT,
                                env={**os.environ, "GIT_EXTERNAL_DIFF": ""})
        except subprocess.TimeoutExpired:
            return {"error": f"Git hunk scan timed out after {COMMAND_TIMEOUT} seconds."}
        if cp.returncode != 0:
            return {"error": (cp.stderr or "Could not read Git diff.")[-4000:]}
        if len(cp.stdout.encode("utf-8", errors="replace")) > 1_000_000:
            return {"error": "This file's diff exceeds the 1 MB hunk limit. Use normal file staging instead."}
        lines = cp.stdout.splitlines(keepends=True)
        starts = [i for i, line in enumerate(lines) if line.startswith("@@ ")]
        if not starts:
            return {"hunks": [], "path": relative, "staged": staged, "truncated": False,
                    "message": "No text hunks available. The file may be unchanged, binary, or untracked."}
        if len([line for line in lines if line.startswith("diff --git ")]) != 1:
            return {"error": "Hunk operations are restricted to a single-file diff."}
        prefix = lines[:starts[0]]
        hunks = []
        for index, start in enumerate(starts[:200]):
            end = starts[index + 1] if index + 1 < len(starts) else len(lines)
            patch_text = "".join(prefix + lines[start:end])
            header = lines[start].rstrip("\r\n")
            body = "".join(lines[start:end])
            hunks.append({"index": index, "header": header,
                          "additions": sum(1 for line in lines[start:end] if line.startswith("+") and not line.startswith("+++")),
                          "deletions": sum(1 for line in lines[start:end] if line.startswith("-") and not line.startswith("---")),
                          "preview": body[-16000:],
                          "signature": hashlib.sha256(patch_text.encode("utf-8", errors="replace")).hexdigest(),
                          "_patch": patch_text})
        return {"hunks": hunks, "path": relative, "staged": staged,
                "truncated": len(starts) > 200}
    except Exception as exc:
        return {"error": str(exc)}


@eel.expose
def git_action(action: str, arg: str = "") -> dict[str, Any]:
    """Allowlisted Git workflow with workspace-scoped paths and validated refs/remotes."""
    action = str(action or "").strip().lower()
    arg = str(arg or "")
    simple_actions = {
        "init": ["git", "init"],
        "diff": ["git", "diff", "--"],
        "log": ["git", "log", "-8", "--oneline", "--decorate"],
        "diff-cached": ["git", "diff", "--cached", "--"],
        "branch-list": ["git", "branch", "--no-color", "--format=%(HEAD) %(refname:short)"],
        "remote-list": ["git", "remote"],
        "remote-branch-list": ["git", "branch", "-r", "--no-color", "--format=%(refname:short)"],
    }

    def checked_relative_path(raw: str, *, allow_root: bool = True) -> str | dict[str, Any]:
        path = raw.strip()
        if not path:
            return {"error": "Specify a relative file path (or . for the whole workspace)."}
        try:
            safe_path(path, allow_root=allow_root)
        except Exception as exc:
            return {"error": str(exc)}
        return path

    def validated_branch(raw: str) -> str | dict[str, Any]:
        branch = raw.strip()
        if not branch or len(branch) > 200 or "\x00" in branch:
            return {"error": "Enter a valid Git branch name (1–200 characters)."}
        checked = _execute(["git", "check-ref-format", "--branch", branch])
        if checked.get("error"):
            return checked
        if checked.get("returncode") != 0:
            return {"error": (checked.get("stderr") or "Invalid Git branch name.").strip()}
        return branch

    def remote_names() -> list[str] | dict[str, Any]:
        result = _execute(["git", "remote"])
        if result.get("error"):
            return result
        if result.get("returncode") != 0:
            return {"error": (result.get("stderr") or "Could not list Git remotes.").strip()}
        return [line for line in result.get("stdout", "").splitlines() if line]

    def parse_remote_branch(raw: str) -> tuple[str, str] | dict[str, Any]:
        # The UI sends JSON so remote names and branch names stay separate argv values.
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            remote = str(parsed.get("remote", "")).strip()
            branch_raw = str(parsed.get("branch", "")).strip()
        else:
            bits = raw.strip().split(maxsplit=1)
            if len(bits) != 2:
                return {"error": "Choose a configured remote and a branch."}
            remote, branch_raw = bits
        available = remote_names()
        if isinstance(available, dict):
            return available
        if not remote or remote not in available:
            return {"error": "Choose an existing Git remote. Use Refresh Remotes to reload the list."}
        branch = validated_branch(branch_raw)
        if isinstance(branch, dict):
            return branch
        return remote, branch

    if action == "status":
        return _git_status_snapshot()
    if action == "hunks":
        requested = arg.strip()
        if requested.startswith("{"):
            try:
                parsed = json.loads(requested)
                requested = str(parsed.get("path", "")) if isinstance(parsed, dict) else ""
            except json.JSONDecodeError:
                return {"error": "Invalid hunk request."}
        path = checked_relative_path(requested, allow_root=False)
        if isinstance(path, dict):
            return path
        unstaged = _git_hunk_snapshot(path, staged=False)
        staged_hunks = _git_hunk_snapshot(path, staged=True)
        if unstaged.get("error"):
            return unstaged
        if staged_hunks.get("error"):
            return staged_hunks
        def public_hunks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
            return [{key: value for key, value in hunk.items() if not key.startswith("_")}
                    for hunk in snapshot.get("hunks", [])]
        return {"returncode": 0, "path": path, "unstaged_hunks": public_hunks(unstaged),
                "staged_hunks": public_hunks(staged_hunks),
                "unstaged_truncated": unstaged.get("truncated", False),
                "staged_truncated": staged_hunks.get("truncated", False)}
    if action in {"stage-hunk", "unstage-hunk", "discard-hunk"}:
        try:
            payload = json.loads(arg)
        except (TypeError, json.JSONDecodeError):
            return {"error": "Invalid hunk action request."}
        if not isinstance(payload, dict):
            return {"error": "Invalid hunk action request."}
        path = checked_relative_path(str(payload.get("path", "")), allow_root=False)
        if isinstance(path, dict):
            return path
        raw_index = payload.get("index")
        signature = payload.get("signature")
        if isinstance(raw_index, bool) or not isinstance(raw_index, int) or raw_index < 0 or raw_index >= 200:
            return {"error": "Choose a valid hunk from the refreshed diff."}
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            return {"error": "The selected hunk is missing its freshness signature. Refresh the diff."}
        reverse = action in {"unstage-hunk", "discard-hunk"}
        snapshot = _git_hunk_snapshot(path, staged=(action == "unstage-hunk"))
        if snapshot.get("error"):
            return snapshot
        hunks = snapshot.get("hunks", [])
        if raw_index >= len(hunks):
            return {"error": "The diff changed. Refresh hunks and select the desired change again."}
        selected = hunks[raw_index]
        if selected.get("signature") != signature:
            return {"error": "The diff changed since it was displayed. Refresh hunks before applying a change."}
        patch_text = selected.get("_patch", "")
        if not patch_text or len(patch_text.encode("utf-8", errors="replace")) > 1_000_000:
            return {"error": "Selected hunk is empty or exceeds the 1 MB safety limit."}
        command = ["git", "apply", "--recount"]
        if action != "discard-hunk":
            command.insert(2, "--cached")
        if reverse:
            command.append("-R")
        command.append("-")
        try:
            cp = subprocess.run(command, cwd=str(workspace_root()), input=patch_text,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=COMMAND_TIMEOUT, env=os.environ.copy())
            return {"stdout": cp.stdout[-50000:], "stderr": cp.stderr[-50000:],
                    "returncode": cp.returncode, "timed_out": False, "path": path,
                    "hunk_index": raw_index}
        except subprocess.TimeoutExpired:
            return {"error": f"Git hunk operation timed out after {COMMAND_TIMEOUT} seconds."}
        except Exception as exc:
            return {"error": str(exc)}
    if action in {"conflicts", "merge-abort"}:
        if action == "conflicts":
            try:
                cp = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U", "-z"],
                    cwd=str(workspace_root()), capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=COMMAND_TIMEOUT)
                if cp.returncode != 0:
                    return {"stdout": cp.stdout[-50000:], "stderr": cp.stderr[-50000:],
                            "returncode": cp.returncode, "conflicts": []}
                paths = [p for p in cp.stdout.split("\0") if p]
                # Never allow resolving a path outside the workspace, even if the index is malformed.
                safe_paths: list[str] = []
                for path in paths[:300]:
                    checked = checked_relative_path(path, allow_root=False)
                    if not isinstance(checked, dict):
                        safe_paths.append(checked)
                return {"stdout": "\n".join(safe_paths), "stderr": "", "returncode": 0,
                        "conflicts": [{"path": path} for path in safe_paths],
                        "files_truncated": len(paths) > 300}
            except subprocess.TimeoutExpired:
                return {"error": f"Conflict scan timed out after {COMMAND_TIMEOUT} seconds.", "conflicts": []}
            except Exception as exc:
                return {"error": str(exc), "conflicts": []}
        merge_head = _execute(["git", "rev-parse", "--verify", "-q", "MERGE_HEAD"])
        if merge_head.get("returncode") != 0:
            return {"error": "There is no merge in progress to abort."}
        return _execute(["git", "merge", "--abort"])
    if action in {"add", "unstage"}:
        path = checked_relative_path(arg)
        if isinstance(path, dict):
            return path
        if action == "add":
            args = ["git", "add", "--", path]
        else:
            # `git restore --staged` needs HEAD on some Git versions for the first commit.
            head = _execute(["git", "rev-parse", "--verify", "HEAD"])
            if head.get("returncode") == 0:
                args = ["git", "restore", "--staged", "--", path]
            else:
                # Unstage the initial index but keep files on disk (including when HEAD is unborn).
                args = ["git", "rm", "--cached", "-r", "--ignore-unmatch", "--", path]
    elif action == "compare":
        try:
            requested = json.loads(arg) if arg.lstrip().startswith("{") else {"path": arg}
        except json.JSONDecodeError:
            return {"error": "Invalid compare request."}
        if not isinstance(requested, dict):
            return {"error": "Invalid compare request."}
        path = checked_relative_path(str(requested.get("path", "")), allow_root=False)
        old_path = checked_relative_path(str(requested.get("old_path") or requested.get("path") or ""), allow_root=False)
        if isinstance(path, dict):
            return path
        if isinstance(old_path, dict):
            return old_path

        def read_git_blob(spec: str) -> bytes | None:
            size_result = _execute(["git", "cat-file", "-s", spec])
            if size_result.get("returncode") != 0:
                return None
            try:
                size = int(size_result.get("stdout", "").strip())
            except ValueError:
                return None
            if size > 1024 * 1024:
                raise ValueError("A compared file exceeds the 1 MB diff-view limit.")
            try:
                cp = subprocess.run(["git", "cat-file", "blob", spec], cwd=str(workspace_root()),
                    capture_output=True, timeout=COMMAND_TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                raise ValueError(f"Git compare timed out after {COMMAND_TIMEOUT} seconds.") from exc
            if cp.returncode != 0:
                return None
            return cp.stdout

        try:
            has_head = _execute(["git", "rev-parse", "--verify", "HEAD"]).get("returncode") == 0
            left_bytes = read_git_blob(f"HEAD:{old_path}") if has_head else None
            disk_path = safe_path(path, allow_root=False)
            if disk_path.is_file():
                if disk_path.stat().st_size > 1024 * 1024:
                    return {"error": "A compared file exceeds the 1 MB diff-view limit."}
                right_bytes = disk_path.read_bytes()
            else:
                right_bytes = b""
            if (left_bytes and b"\0" in left_bytes) or b"\0" in right_bytes:
                return {"error": "Binary files are not supported in the text diff viewer; use the file manager or terminal."}
            left_text = (left_bytes or b"").decode("utf-8", errors="replace")
            right_text = right_bytes.decode("utf-8", errors="replace")
            left_lines = left_text.splitlines()
            right_lines = right_text.splitlines()
            truncated = False
            if len(left_lines) > 2500:
                left_lines = left_lines[:2500]
                truncated = True
            if len(right_lines) > 2500:
                right_lines = right_lines[:2500]
                truncated = True
            rows: list[dict[str, Any]] = []
            matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=True)
            for op, i1, i2, j1, j2 in matcher.get_opcodes():
                if op == "equal":
                    for offset in range(i2 - i1):
                        rows.append({"left_no": i1 + offset + 1, "left": left_lines[i1 + offset],
                                     "right_no": j1 + offset + 1, "right": right_lines[j1 + offset], "kind": "same"})
                elif op == "replace":
                    width = max(i2 - i1, j2 - j1)
                    for offset in range(width):
                        li, ri = i1 + offset, j1 + offset
                        rows.append({"left_no": li + 1 if li < i2 else None,
                                     "left": left_lines[li] if li < i2 else "",
                                     "right_no": ri + 1 if ri < j2 else None,
                                     "right": right_lines[ri] if ri < j2 else "", "kind": "replace"})
                elif op == "delete":
                    for li in range(i1, i2):
                        rows.append({"left_no": li + 1, "left": left_lines[li],
                                     "right_no": None, "right": "", "kind": "delete"})
                else:
                    for ri in range(j1, j2):
                        rows.append({"left_no": None, "left": "", "right_no": ri + 1,
                                     "right": right_lines[ri], "kind": "insert"})
                if len(rows) > 1500:
                    rows = rows[:1500]
                    truncated = True
                    break
            for row in rows:
                for key in ("left", "right"):
                    value = row[key]
                    if len(value) > 4000:
                        row[key] = value[:4000] + " … [line truncated]"
                        truncated = True
            return {"returncode": 0, "path": path, "left_label": f"HEAD · {old_path}",
                    "right_label": f"Working tree · {path}", "rows": rows, "truncated": truncated,
                    "left_lines": len(left_lines), "right_lines": len(right_lines)}
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
    elif action in {"diff", "diff-cached", "log"} and arg.strip():
        path = checked_relative_path(arg)
        if isinstance(path, dict):
            return path
        if action == "diff":
            args = ["git", "diff", "--", path]
        elif action == "diff-cached":
            args = ["git", "diff", "--cached", "--", path]
        else:
            args = ["git", "log", "-8", "--oneline", "--decorate", "--", path]
    elif action == "commit":
        message = arg.strip()
        if not message:
            return {"error": "A commit message is required."}
        if len(message) > 5000:
            return {"error": "Commit message exceeds the 5,000 character limit."}
        args = ["git", "commit", "-m", message]
    elif action in {"branch-create", "branch-switch", "merge"}:
        branch = validated_branch(arg)
        if isinstance(branch, dict):
            return branch
        if action == "branch-create":
            args = ["git", "switch", "--create", branch]
        elif action == "branch-switch":
            args = ["git", "switch", branch]
        else:
            args = ["git", "merge", "--no-edit", branch]
    elif action in {"fetch", "pull", "push"}:
        if action == "fetch":
            available = remote_names()
            if isinstance(available, dict):
                return available
            remote = arg.strip()
            if not remote or remote not in available:
                return {"error": "Choose an existing Git remote. Use Refresh Remotes to reload the list."}
            # Fetch updates refs only; it does not modify checked-out files.
            args = ["git", "fetch", "--prune", remote]
        elif action == "pull":
            selected = parse_remote_branch(arg)
            if isinstance(selected, dict):
                return selected
            remote, branch = selected
            # Explicit fast-forward-only policy avoids surprise merge commits/conflicts.
            args = ["git", "pull", "--ff-only", remote, branch]
        else:
            selected = parse_remote_branch(arg)
            if isinstance(selected, dict):
                return selected
            remote, branch = selected
            # Never force-push from this UI.
            args = ["git", "push", remote, branch]
    elif action in simple_actions:
        args = simple_actions[action]
    else:
        return {"error": "Unsupported Git action."}
    remote_action = action in {"fetch", "pull", "push"}
    result = _execute(args, extra_env={"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"} if remote_action else None)
    if action in {"merge", "pull"} and result.get("returncode") != 0:
        conflicts = git_action("conflicts")
        result["conflicts"] = conflicts.get("conflicts", [])
    return result



def _load_workspace_tasks() -> tuple[list[dict[str, Any]], str | None]:
    """Load user-authored VS Code-style tasks from .vscode/tasks.json."""
    try:
        config = safe_path(".vscode/tasks.json")
        if not config.exists():
            return [], None
        if not config.is_file():
            return [], "The .vscode/tasks.json path is not a file."
        if config.stat().st_size > 1024 * 1024:
            return [], "The task configuration exceeds the 1 MB limit."
        data = json.loads(config.read_text(encoding="utf-8"))
        raw_tasks = data.get("tasks", []) if isinstance(data, dict) else None
        if not isinstance(raw_tasks, list):
            return [], "The task configuration must contain a tasks array."
        result: list[dict[str, Any]] = []
        for item in raw_tasks[:200]:
            if not isinstance(item, dict):
                continue
            label = item.get("label") or item.get("taskName")
            command = item.get("command")
            task_type = item.get("type", "shell")
            args = item.get("args", [])
            if not isinstance(label, str) or not label.strip() or not isinstance(command, str) or not command.strip():
                continue
            if task_type not in {"shell", "process"}:
                continue
            if not isinstance(args, list) or any(not isinstance(arg, (str, int, float)) for arg in args):
                continue
            options = item.get("options", {}) if isinstance(item.get("options"), dict) else {}
            result.append({"label": label.strip(), "type": task_type, "command": command,
                           "args": [str(arg) for arg in args], "group": item.get("group", ""),
                           "cwd": str(options.get("cwd", "")),
                           "env": options.get("env", {}) if isinstance(options.get("env", {}), dict) else {}})
        return result, None
    except json.JSONDecodeError as exc:
        return [], f"Invalid .vscode/tasks.json: {exc.msg} at line {exc.lineno}, column {exc.colno}."
    except Exception as exc:
        return [], str(exc)


@eel.expose
def list_tasks() -> dict[str, Any]:
    tasks, error = _load_workspace_tasks()
    return {"tasks": tasks, "error": error}


@eel.expose
def save_workspace_task(operation: str, task: dict[str, Any] | None, original_label: str = "") -> dict[str, Any]:
    """Create/update/delete a bounded VS Code-style task while preserving unknown JSON fields."""
    choice = (operation or "").strip().lower()
    if choice not in {"create", "update", "delete"}:
        return {"error": "Choose create, update or delete for workspace tasks."}
    try:
        vscode_dir = safe_path(".vscode", allow_root=True)
        if vscode_dir.exists() and not vscode_dir.is_dir():
            return {"error": ".vscode must be a directory."}
        vscode_dir.mkdir(parents=True, exist_ok=True)
        # Re-check both paths after mkdir so links are never followed.
        safe_path(".vscode", allow_root=True)
        target = safe_path(".vscode/tasks.json", allow_root=True)
        if target.exists():
            if not target.is_file() or target.stat().st_size > 1024 * 1024:
                return {"error": ".vscode/tasks.json must be a regular file smaller than 1 MB."}
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                return {"error": f"Invalid tasks.json: {exc.msg} at line {exc.lineno}, column {exc.colno}."}
            if not isinstance(data, dict) or not isinstance(data.get("tasks", []), list):
                return {"error": "tasks.json must be an object with a tasks array."}
            rows = data.setdefault("tasks", [])
            if len(rows) > 200:
                return {"error": "tasks.json supports at most 200 tasks."}
        else:
            data = {"version": "2.0.0", "tasks": []}
            rows = data["tasks"]

        label_old = (original_label or "").strip()
        index: int | None = None
        if choice in {"update", "delete"}:
            if not label_old:
                return {"error": "Select a workspace task first."}
            matches = [i for i, row in enumerate(rows) if isinstance(row, dict) and (row.get("label") or row.get("taskName")) == label_old]
            if len(matches) != 1:
                return {"error": "The selected task no longer exists or its label is ambiguous. Refresh the task list."}
            index = matches[0]

        if choice == "delete":
            del rows[index]
        else:
            if not isinstance(task, dict):
                return {"error": "Task configuration must be an object."}
            label = task.get("label")
            command = task.get("command")
            task_type = task.get("type", "shell")
            args = task.get("args", [])
            group = task.get("group", "")
            cwd_value = task.get("cwd", "")
            env_values = task.get("env", {})
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120 or any(ord(c) < 32 for c in label):
                return {"error": "Task label must be 1–120 printable characters."}
            label = label.strip()
            if not isinstance(command, str) or not command.strip() or len(command) > 2000 or "\x00" in command:
                return {"error": "Task command must be 1–2,000 characters and contain no NUL character."}
            if task_type not in {"shell", "process"}:
                return {"error": "Supported task types are shell and process."}
            if not isinstance(args, list) or len(args) > 100 or any(not isinstance(value, (str, int, float)) for value in args):
                return {"error": "Task args must be an array of at most 100 scalar values."}
            normalized_args = [str(value) for value in args]
            if any(len(value) > 2000 or "\x00" in value for value in normalized_args):
                return {"error": "A task argument exceeds the supported length or contains a NUL character."}
            if not isinstance(group, str) or len(group) > 80:
                return {"error": "Task group must be a short text value."}
            if not isinstance(cwd_value, str) or len(cwd_value) > 1000 or "\x00" in cwd_value:
                return {"error": "Task cwd must be a workspace-relative path."}
            cwd = cwd_value.strip()
            if cwd:
                expanded = cwd.replace("${workspaceFolder}", str(workspace_root())).replace("${workspaceFolderBasename}", workspace_root().name)
                lexical = Path(expanded).expanduser()
                if not lexical.is_absolute():
                    lexical = workspace_root() / lexical
                try:
                    rel = Path(os.path.abspath(lexical)).relative_to(workspace_root()).as_posix()
                    cwd_path = safe_path(rel, allow_root=True)
                except Exception:
                    return {"error": "Task working directory must remain inside the opened workspace and cannot traverse a symlink."}
                if not cwd_path.is_dir():
                    return {"error": "Task working directory must be an existing directory inside the workspace."}
            if not isinstance(env_values, dict) or len(env_values) > 100:
                return {"error": "Task environment must be an object with at most 100 variables."}
            normalized_env: dict[str, str] = {}
            for key, value in env_values.items():
                if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or not isinstance(value, (str, int, float)):
                    return {"error": "Task environment values must use valid variable names and scalar values."}
                rendered = str(value)
                if len(rendered) > 8000 or "\x00" in rendered:
                    return {"error": f"Environment value {key} exceeds the supported size."}
                normalized_env[key] = rendered
            duplicate = any(i != index and isinstance(row, dict) and (row.get("label") or row.get("taskName")) == label for i, row in enumerate(rows))
            if duplicate:
                return {"error": f'A task named "{label}" already exists.'}
            if choice == "create" and len(rows) >= 200:
                return {"error": "tasks.json supports at most 200 tasks."}

            row = dict(rows[index]) if choice == "update" and isinstance(rows[index], dict) else {}
            row.update({"label": label, "type": task_type, "command": command.strip(), "args": normalized_args})
            if group:
                row["group"] = group
            else:
                row.pop("group", None)
            options = dict(row.get("options", {})) if isinstance(row.get("options"), dict) else {}
            if cwd:
                options["cwd"] = cwd
            else:
                options.pop("cwd", None)
            if normalized_env:
                options["env"] = normalized_env
            else:
                options.pop("env", None)
            if options:
                row["options"] = options
            else:
                row.pop("options", None)
            if choice == "create":
                rows.append(row)
                index = len(rows) - 1
            else:
                rows[index] = row

        # Serialize before replacing; malformed/oversized data is never partially written.
        encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(encoded) > 1024 * 1024:
            return {"error": "Saved tasks.json exceeds the 1 MB size limit."}
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(vscode_dir), prefix=".tasks-", suffix=".tmp") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = handle.name
            # Revalidate destination immediately before replace.
            safe_path(".vscode/tasks.json", allow_root=True)
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
        return {"ok": True, "operation": choice, "label": label_old if choice == "delete" else rows[index].get("label", ""),
                "task_count": len(rows), "settings_path": ".vscode/tasks.json"}
    except Exception as exc:
        return {"error": str(exc)}


@eel.expose
def run_task(label: str) -> dict[str, Any]:
    """Run one task declared in .vscode/tasks.json with a bounded timeout."""
    tasks, error = _load_workspace_tasks()
    if error:
        return {"error": error}
    name = (label or "").strip()
    matches = [task for task in tasks if task["label"] == name]
    if not matches:
        return {"error": f"Task not found: {name or '(empty label)'}. Refresh the Run and Debug task list."}
    if len(matches) != 1:
        return {"error": f'Task label "{name}" is duplicated in tasks.json; give each task a unique label before running it.'}
    task = matches[0]
    base = workspace_root()
    def expand(value: str) -> str:
        return value.replace("${workspaceFolder}", str(base)).replace("${workspaceFolderBasename}", base.name)
    command = expand(task["command"])
    args = [expand(arg) for arg in task["args"]]
    options = {}
    try:
        raw_config = json.loads(safe_path(".vscode/tasks.json").read_text(encoding="utf-8"))
        for item in raw_config.get("tasks", []):
            if isinstance(item, dict) and (item.get("label") or item.get("taskName")) == name:
                options = item.get("options") if isinstance(item.get("options"), dict) else {}
                break
    except Exception as exc:
        return {"error": f"Could not read task options: {exc}"}
    cwd_value = expand(str(options.get("cwd", "") or "")).strip()
    try:
        if cwd_value:
            cwd_lexical = Path(cwd_value).expanduser()
            if not cwd_lexical.is_absolute():
                cwd_lexical = base / cwd_lexical
            cwd_rel = cwd_lexical.relative_to(base).as_posix()
            cwd = safe_path(cwd_rel, allow_root=True)
        else:
            cwd = base
    except Exception:
        return {"error": "Task working directory must remain inside the opened workspace."}
    env = os.environ.copy()
    env_values = options.get("env", {})
    if isinstance(env_values, dict):
        for key, value in env_values.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or not isinstance(value, (str, int, float)):
                return {"error": "Task environment values must use valid variable names and scalar values."}
            env[key] = expand(str(value))
    display_command = " ".join([command, *args]).strip()
    try:
        if task["type"] == "process":
            result = _execute([command, *args], cwd=cwd, timeout=TASK_TIMEOUT, extra_env=env)
        else:
            if os.name == "nt":
                command_line = command + (" " + subprocess.list2cmdline(args) if args else "")
            else:
                command_line = command + (" " + shlex.join(args) if args else "")
            cp = subprocess.run(command_line, cwd=str(cwd), capture_output=True, text=True,
                                errors="replace", timeout=TASK_TIMEOUT, env=env, shell=True)
            result = {"stdout": cp.stdout[-50000:], "stderr": cp.stderr[-50000:],
                      "returncode": cp.returncode, "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or "", exc.stderr or ""
        if isinstance(stdout, bytes): stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes): stderr = stderr.decode(errors="replace")
        result = {"stdout": stdout[-50000:], "stderr": (stderr + f"\nStopped after {TASK_TIMEOUT} seconds.")[-50000:],
                  "returncode": -1, "timed_out": True}
    except Exception as exc:
        return {"error": str(exc), "task": name}
    return {"task": name, "command": display_command, **result}


_TEST_DISCOVERY_IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".vscode",
    "dist", "build", "coverage", ".next", ".pytest_cache", ".mypy_cache", ".tox",
}
_TEST_FILE_CAP = 300
_TEST_SCAN_ENTRY_CAP = 20000
_TEST_RUN_TIMEOUT = 120


def _discover_workspace_test_snapshot() -> dict[str, Any]:
    """Discover conventional Python/Node tests without following symlinks or scanning dependency trees."""
    base = workspace_root()
    python_files: list[str] = []
    node_files: list[str] = []
    visited = 0
    truncated = False
    for current, dirs, names in os.walk(base, topdown=True, followlinks=False):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(base).parts)
        except ValueError:
            dirs[:] = []
            continue
        dirs[:] = [name for name in dirs if name not in _TEST_DISCOVERY_IGNORED_DIRS
                   and not (current_path / name).is_symlink()]
        if depth >= 12:
            dirs[:] = []
        visited += len(dirs) + len(names)
        if visited > _TEST_SCAN_ENTRY_CAP:
            truncated = True
            break
        for name in names:
            if len(python_files) + len(node_files) >= _TEST_FILE_CAP:
                truncated = True
                break
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                continue
            suffix = path.suffix.lower()
            lower = name.lower()
            rel = path.relative_to(base).as_posix()
            if suffix == ".py" and (lower.startswith("test_") or lower.endswith("_test.py")):
                python_files.append(rel)
            if suffix in {".js", ".cjs", ".mjs"} and (
                    ".test." in lower or ".spec." in lower or lower.startswith("test-")
                    or lower.endswith(("-test.js", "-test.cjs", "-test.mjs", "-spec.js", "-spec.cjs", "-spec.mjs"))):
                node_files.append(rel)
        if truncated:
            break
    python_files.sort(key=str.casefold)
    node_files.sort(key=str.casefold)
    try:
        import importlib.util
        python_runner = "pytest" if importlib.util.find_spec("pytest") is not None else "unittest"
    except (ImportError, ValueError):
        python_runner = "unittest"
    npm_test = False
    npm_script = ""
    warnings: list[str] = []
    try:
        package_path = safe_path("package.json")
        if package_path.is_file() and package_path.stat().st_size <= 256 * 1024:
            package = json.loads(package_path.read_text(encoding="utf-8-sig"))
            scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
            if isinstance(scripts, dict) and isinstance(scripts.get("test"), str) and scripts["test"].strip():
                npm_test = True
                npm_script = scripts["test"].strip()[:240]
        elif package_path.is_file():
            warnings.append("package.json exceeds the 256 KB discovery limit; npm test script was not read.")
    except Exception as exc:
        warnings.append(f"package.json could not be inspected: {exc}")
    return {
        "python_files": python_files[:_TEST_FILE_CAP], "python_count": len(python_files),
        "python_runner": python_runner, "node_files": node_files[:_TEST_FILE_CAP],
        "node_count": len(node_files), "node_available": bool(shutil.which("node")),
        "npm_test": npm_test, "npm_test_script": npm_script, "npm_available": bool(shutil.which("npm")),
        "truncated": truncated, "visited_entries": min(visited, _TEST_SCAN_ENTRY_CAP), "warnings": warnings,
    }


@eel.expose
def discover_workspace_tests() -> dict[str, Any]:
    try:
        return _discover_workspace_test_snapshot()
    except Exception as exc:
        return {"error": str(exc), "python_files": [], "node_files": [], "python_count": 0,
                "node_count": 0, "node_available": bool(shutil.which("node")), "npm_test": False,
                "npm_available": bool(shutil.which("npm")), "truncated": False, "warnings": []}


@eel.expose
def run_workspace_tests(kind: str) -> dict[str, Any]:
    """Run a discovered project test runner using fixed argv and bounded output/time."""
    choice = (kind or "").strip().lower()
    if choice not in {"python", "node", "npm"}:
        return {"error": "Choose a supported test runner: python, node or npm."}
    snapshot = _discover_workspace_test_snapshot()
    if choice == "python":
        files = snapshot["python_files"]
        if not files:
            return {"error": "No conventional Python test files were found in this workspace."}
        if snapshot["python_runner"] == "pytest":
            args = [sys.executable, "-m", "pytest", "-q"]
            runner = "pytest"
        else:
            base = workspace_root()
            parents = [str((base / item).parent) for item in files]
            start_dir = Path(os.path.commonpath(parents))
            try:
                start_rel = start_dir.resolve().relative_to(base).as_posix() or "."
            except ValueError:
                return {"error": "Python test discovery escaped the opened workspace."}
            args = [sys.executable, "-m", "unittest", "discover", "-v", "-s", start_rel]
            runner = "unittest"
    elif choice == "node":
        if not snapshot["node_files"]:
            return {"error": "No conventional Node.js test files were found in this workspace."}
        node = shutil.which("node")
        if not node:
            return {"error": "Node.js is not installed or not on PATH."}
        args = [node, "--test"]
        runner = "node --test"
    else:
        if not snapshot["npm_test"]:
            return {"error": "No non-empty scripts.test entry was found in package.json."}
        npm = shutil.which("npm")
        if not npm:
            return {"error": "npm is not installed or not on PATH."}
        args = [npm, "test"]
        runner = "npm test"
    result = _execute(args, cwd=workspace_root(), timeout=_TEST_RUN_TIMEOUT)
    return {"kind": choice, "runner": runner, "test_files": snapshot["python_files"] if choice == "python"
            else snapshot["node_files"] if choice == "node" else [], **result}


@eel.expose
def reveal_workspace() -> dict[str, Any]:
    try:
        path = str(workspace_root())
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return {"ok": True}
    except Exception as exc:
        return {"error": str(exc)}


if __name__ == "__main__":
    ensure_samples()
    eel.init(str(WEB_DIR))
    print(f"FlowCode Studio 0.34.0 | Workspace: {_workspace}")
    browser_modes = ["edge", "chrome"] if sys.platform.startswith("win") else ["chrome"]
    launch_errors: list[str] = []
    for browser_mode in browser_modes:
        try:
            eel.start("index.html", size=(1440, 920), min_size=(1000, 650), position=(60, 35), mode=browser_mode, port=0)
            break
        except EnvironmentError as exc:
            launch_errors.append(f"{browser_mode}: {exc}")
    else:
        details = " | ".join(launch_errors)
        raise SystemExit(f"Could not launch the desktop window. Install Microsoft Edge or Google Chrome. Details: {details}")
