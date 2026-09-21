#!/usr/bin/env python3
"""Opt-in loopback API for SPP Audio Studio. No network listener until launched."""
import argparse
import json
import os
import secrets
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKER = Path(__file__).with_name("spp_worker.py")
PYTHON = Path(__file__).resolve().parent.parent / "runtime/python/bin/python3.11"


def run_worker(args):
    proc = subprocess.run([str(PYTHON), str(WORKER), *args], capture_output=True, text=True, timeout=3600)
    lines = proc.stdout.strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        result = {}
    if proc.returncode != 0 and not result.get("error"):
        result = {"ok": False, "error": (proc.stderr or proc.stdout)[-1200:]}
    return result


def handler_factory(token):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, value):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            return secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)

        def do_GET(self):
            if not self.authorized():
                return self.respond(401, {"ok": False, "error": "unauthorized"})
            if self.path == "/v1/status":
                return self.respond(200, run_worker(["model-status"]))
            return self.respond(404, {"ok": False, "error": "unknown endpoint"})

        def do_POST(self):
            if not self.authorized():
                return self.respond(401, {"ok": False, "error": "unauthorized"})
            if self.path not in ("/v1/convert", "/v1/separate", "/v1/clone"):
                return self.respond(404, {"ok": False, "error": "unknown endpoint"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 2 or size > 65536:
                    raise ValueError("request size must be 2–65536 bytes")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                def required(key):
                    value = body.get(key)
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(key + " is required")
                    return value
                if self.path == "/v1/convert":
                    args = ["convert", required("input")]
                elif self.path == "/v1/separate":
                    args = ["separate", required("input"), "--keep", body.get("keep", "instrumental"), "--format", body.get("format", "MP3")]
                else:
                    args = ["clone", "--ref-audio", required("ref_audio"), "--text", required("text")]
                    if body.get("ref_text"):
                        args += ["--ref-text", str(body["ref_text"])]
                if body.get("output_dir"):
                    args += ["--output-dir", str(body["output_dir"])]
                result = run_worker(args)
                self.respond(200 if result.get("ok") else 422, result)
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                self.respond(400, {"ok": False, "error": str(e)})
            except subprocess.TimeoutExpired:
                self.respond(504, {"ok": False, "error": "task timed out"})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=os.environ.get("SPP_API_TOKEN"))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024–65535")
    if not PYTHON.is_file() or not WORKER.is_file():
        parser.error("run the API script from inside the installed App bundle")
    token = args.token or secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_factory(token))
    print(f"SPP API listening on http://127.0.0.1:{args.port}", flush=True)
    print(f"Token: {token}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
