#!/usr/bin/env python3
"""Loopback Agent API for SPP Audio Studio.

The API is opt-in, binds only to 127.0.0.1, and uses a Bearer token.
Long-running audio jobs can be submitted asynchronously through /v1/tasks.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

API_VERSION = "1.1"
APP_NAME = "SPP Audio Studio"
LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
DATA_DIR = LOCAL_APP_DATA / APP_NAME
TOKEN_FILE = DATA_DIR / "agent-api-token.txt"
DISCOVERY_FILE = DATA_DIR / "agent-api.json"

SCRIPT_DIR = Path(__file__).resolve().parent
WORKER_SCRIPT = SCRIPT_DIR / "spp_worker.py"
WORKER_EXE = SCRIPT_DIR / "SPPWorker" / "SPPWorker.exe"
PYTHON = Path(os.environ.get("SPP_PYTHON_CORE", sys.executable))

TASKS: dict[str, dict] = {}
FUTURES = {}
TASK_LOCK = threading.Lock()
TASK_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spp-agent")
MAX_TASKS = 100


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def worker_command(args: list[str]) -> list[str]:
    if WORKER_EXE.is_file():
        return [str(WORKER_EXE), *args]
    if WORKER_SCRIPT.is_file() and PYTHON.is_file():
        return [str(PYTHON), str(WORKER_SCRIPT), *args]
    raise RuntimeError("SPP Worker is not available")


def run_worker(args: list[str], timeout: int = 3600) -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        worker_command(args), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=timeout,
    )
    result = {}
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                result = value
                break
        except json.JSONDecodeError:
            continue

    if proc.returncode != 0 and not result.get("error"):
        result = {
            "ok": False,
            "error": (proc.stderr or proc.stdout or f"worker exit {proc.returncode}")[-1200:],
            "exit_code": proc.returncode,
        }
    if not result:
        result = {"ok": proc.returncode == 0}
    return result


def required_string(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(key + " is required")
    return value.strip()


def build_worker_args(action: str, body: dict) -> list[str]:
    action = {"convert": "convert_audio", "separate": "separate_audio", "clone": "clone_voice"}.get(action, action)
    output_dir = body.get("output_dir")

    if action == "convert_audio":
        args = ["convert", required_string(body, "input")]
    elif action == "separate_audio":
        keep = str(body.get("keep", "instrumental")).lower()
        fmt = str(body.get("format", "MP3")).upper()
        if keep not in {"instrumental", "vocals", "both"}:
            raise ValueError("keep must be instrumental, vocals, or both")
        if fmt not in {"MP3", "WAV", "FLAC"}:
            raise ValueError("format must be MP3, WAV, or FLAC")
        args = ["separate", required_string(body, "input"), "--keep", keep, "--format", fmt]
        if body.get("bitrate"):
            args += ["--bitrate", str(body["bitrate"])]

    elif action == "clone_voice":
        text = required_string(body, "text")
        template_id = body.get("template_id")
        if isinstance(template_id, str) and template_id.strip():
            args = ["voice-clone", template_id.strip(), "--text", text]
        else:
            args = ["clone", "--ref-audio", required_string(body, "ref_audio"), "--text", text]
            if body.get("ref_text"):
                args += ["--ref-text", str(body["ref_text"])]
            parameter_map = {
                "temperature": "--temperature", "top_p": "--top-p",
                "top_k": "--top-k", "repetition_penalty": "--repetition-penalty",
            }
            for key, flag in parameter_map.items():
                if key in body:
                    args += [flag, str(body[key])]
    else:
        raise ValueError("unsupported action")

    if output_dir:
        args += ["--output-dir", str(output_dir)]
    return args


def task_snapshot(task: dict) -> dict:
    return {
        key: value for key, value in task.items()
        if key not in {"worker_args", "future"}
    }


def prune_tasks_locked() -> None:
    if len(TASKS) <= MAX_TASKS:
        return
    removable = [t for t in TASKS.values() if t["status"] in {"completed", "failed", "cancelled"}]
    removable.sort(key=lambda t: t["created_at"])
    for task in removable[:max(0, len(TASKS) - MAX_TASKS)]:
        TASKS.pop(task["id"], None)
        FUTURES.pop(task["id"], None)

def run_task(task_id: str, args: list[str]) -> None:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task or task["status"] == "cancelled":
            return
        task["status"] = "running"
        task["started_at"] = utc_now()

    try:
        result = run_worker(args)
        status = "completed" if result.get("ok") else "failed"
        error = None if result.get("ok") else result.get("error", "task failed")
    except subprocess.TimeoutExpired:
        result, status, error = {"ok": False, "error": "task timed out"}, "failed", "task timed out"
    except Exception as exc:
        result, status, error = {"ok": False, "error": str(exc)}, "failed", str(exc)

    with TASK_LOCK:
        task = TASKS.get(task_id)
        if task:
            task.update(status=status, result=result, error=error, finished_at=utc_now())


def submit_task(action: str, body: dict) -> dict:
    args = build_worker_args(action, body)
    task_id = "task_" + uuid.uuid4().hex[:12]
    summary = {key: body[key] for key in ("input", "template_id", "output_dir") if key in body}
    task = {
        "id": task_id, "action": action, "status": "queued",
        "created_at": utc_now(), "started_at": None, "finished_at": None,
        "summary": summary, "result": None, "error": None,
    }
    with TASK_LOCK:
        TASKS[task_id] = task
        prune_tasks_locked()
        future = TASK_EXECUTOR.submit(run_task, task_id, args)
        FUTURES[task_id] = future
    return task_snapshot(task)

def cancel_task(task_id: str) -> tuple[int, dict]:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return 404, {"ok": False, "error": "task not found"}
        if task["status"] == "cancelled":
            return 200, {"ok": True, "task": task_snapshot(task)}
        if task["status"] != "queued":
            return 409, {"ok": False, "error": "only queued tasks can be cancelled safely", "task": task_snapshot(task)}
        future = FUTURES.get(task_id)
        if future and future.cancel():
            task.update(status="cancelled", finished_at=utc_now(), error="cancelled")
            return 200, {"ok": True, "task": task_snapshot(task)}
        return 409, {"ok": False, "error": "task has already started", "task": task_snapshot(task)}


def capabilities() -> dict:
    return {
        "ok": True,
        "name": "SPP Audio Studio Agent API",
        "api_version": API_VERSION,
        "transport": "http-loopback",
        "authentication": "Bearer token",
        "task_execution": "serial",
        "actions": {
            "convert_audio": {
                "required": ["input"], "optional": ["output_dir"],
            },
            "separate_audio": {
                "required": ["input"], "optional": ["output_dir", "keep", "format", "bitrate"],
                "enums": {"keep": ["instrumental", "vocals", "both"], "format": ["MP3", "WAV", "FLAC"]},
            },
            "clone_voice": {
                "required": ["text", "template_id OR ref_audio"],
                "optional": ["ref_text", "output_dir", "temperature", "top_p", "top_k", "repetition_penalty"],
            },
        },

        "endpoints": {
            "capabilities": "GET /v1/capabilities",
            "status": "GET /v1/status",
            "doctor": "GET /v1/doctor",
            "voices": "GET /v1/voices",
            "submit_task": "POST /v1/tasks",
            "list_tasks": "GET /v1/tasks",
            "task_status": "GET /v1/tasks/{task_id}",
            "cancel_queued_task": "POST /v1/tasks/{task_id}/cancel",
        },
        "legacy_sync_endpoints": ["/v1/convert", "/v1/separate", "/v1/clone"],
    }


def load_token(explicit: str | None, reset: bool) -> tuple[str, Path | None]:
    if explicit:
        return explicit, None
    env_token = os.environ.get("SPP_API_TOKEN")
    if env_token:
        return env_token, None
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not reset and TOKEN_FILE.is_file():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(token) >= 16:
            return token, TOKEN_FILE
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    return token, TOKEN_FILE


def write_discovery(port: int, token_path: Path | None) -> None:
    payload = {
        "name": "SPP Audio Studio Agent API", "api_version": API_VERSION,
        "base_url": f"http://127.0.0.1:{port}", "pid": os.getpid(),
        "token_file": str(token_path) if token_path else None,
        "capabilities_url": f"http://127.0.0.1:{port}/v1/capabilities",
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DISCOVERY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def handler_factory(token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if os.environ.get("SPP_API_VERBOSE") == "1":
                super().log_message(fmt, *args)

        def respond(self, code: int, value: dict):
            data = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self) -> bool:
            return secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)

        def read_json(self) -> dict:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 2 or size > 65536:
                raise ValueError("request size must be 2–65536 bytes")
            value = json.loads(self.rfile.read(size))
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return value

        def do_GET(self):
            if not self.authorized():
                return self.respond(401, {"ok": False, "error": "unauthorized"})
            path = urlparse(self.path).path
            if path == "/v1/capabilities":
                return self.respond(200, capabilities())

            if path == "/v1/status":
                return self.respond(200, run_worker(["model-status"]))
            if path == "/v1/doctor":
                result = run_worker(["doctor"])
                return self.respond(200 if result.get("ok") else 422, result)
            if path == "/v1/voices":
                result = run_worker(["voice-list"])
                return self.respond(200 if result.get("ok") else 422, result)
            if path == "/v1/tasks":
                with TASK_LOCK:
                    tasks = [task_snapshot(t) for t in TASKS.values()]
                tasks.sort(key=lambda t: t["created_at"], reverse=True)
                return self.respond(200, {"ok": True, "tasks": tasks})
            if path.startswith("/v1/tasks/"):
                task_id = path.rsplit("/", 1)[-1]
                with TASK_LOCK:
                    task = TASKS.get(task_id)
                    snapshot = task_snapshot(task) if task else None
                return self.respond(200, {"ok": True, "task": snapshot}) if snapshot else self.respond(404, {"ok": False, "error": "task not found"})
            return self.respond(404, {"ok": False, "error": "unknown endpoint"})

        def do_POST(self):
            if not self.authorized():
                return self.respond(401, {"ok": False, "error": "unauthorized"})
            path = urlparse(self.path).path
            try:
                if path.startswith("/v1/tasks/") and path.endswith("/cancel"):
                    task_id = path.split("/")[-2]
                    code, result = cancel_task(task_id)
                    return self.respond(code, result)

                body = self.read_json()
                if path == "/v1/tasks":
                    action = required_string(body, "action")
                    task = submit_task(action, body)
                    return self.respond(202, {"ok": True, "task": task})

                legacy = {
                    "/v1/convert": "convert_audio",
                    "/v1/separate": "separate_audio",
                    "/v1/clone": "clone_voice",
                }
                action = legacy.get(path)
                if not action:
                    return self.respond(404, {"ok": False, "error": "unknown endpoint"})
                result = run_worker(build_worker_args(action, body))
                return self.respond(200 if result.get("ok") else 422, result)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                return self.respond(400, {"ok": False, "error": str(exc)})
            except subprocess.TimeoutExpired:
                return self.respond(504, {"ok": False, "error": "task timed out"})
            except Exception as exc:
                return self.respond(500, {"ok": False, "error": str(exc)})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="SPP Audio Studio loopback Agent API")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token")
    parser.add_argument("--reset-token", action="store_true")
    args = parser.parse_args()
    if args.port != 0 and not 1024 <= args.port <= 65535:
        parser.error("port must be 0 or 1024–65535")

    token, token_path = load_token(args.token, args.reset_token)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_factory(token))

    server.daemon_threads = True
    port = server.server_port
    write_discovery(port, token_path)
    print(f"SPP Agent API listening on http://127.0.0.1:{port}", flush=True)
    if token_path:
        print(f"Token file: {token_path}", flush=True)
    else:
        print("Token source: command line or SPP_API_TOKEN", flush=True)
    print(f"Discovery: {DISCOVERY_FILE}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        TASK_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        try:
            if DISCOVERY_FILE.is_file():
                current = json.loads(DISCOVERY_FILE.read_text(encoding="utf-8"))
                if current.get("pid") == os.getpid():
                    DISCOVERY_FILE.unlink()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
