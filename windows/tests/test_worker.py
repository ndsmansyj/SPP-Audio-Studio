"""Model-free Windows worker contract tests: python -m unittest discover -s windows/tests -v."""
import http.client
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "worker" / "spp_worker.py"
API = ROOT / "worker" / "local_api.py"


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {**os.environ, "LOCALAPPDATA": self.tmp.name, "SPP_PYTHON_CORE": sys.executable}

    def load_worker(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("worker_regression", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        return worker

    def valid_ffmpeg(self, path, **kwargs):
        return {"ready": True, "path": str(path), "version": "ffmpeg version test", "reason": ""}

    def populate_runtime(self, worker, root, kind):
        import runtime_manifest
        site = root / "site-packages"
        for module in runtime_manifest.MODULES[kind]:
            (site / module).mkdir(parents=True, exist_ok=True)
        metadata = site / f"{kind}_test-1.0.dist-info/METADATA"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(f"Name: {kind}-test\nVersion: 1.0\n")
        bundled = site / "imageio_ffmpeg/binaries/ffmpeg-test.exe"
        bundled.parent.mkdir(parents=True, exist_ok=True)
        bundled.write_bytes(b"test executable")
        ffmpeg = root / "ffmpeg/ffmpeg.exe"
        ffmpeg.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.write_bytes(b"test executable")
        if kind == "mel":
            (site / "audio_separator/models.json").write_text(json.dumps({
                "roformer_download_list": {worker.MEL_REGISTRY_NAME: {worker.MEL_MODEL: worker.MEL_CONFIG}}}))
        marker = {"schema_version": worker.SCHEMA_VERSION, "manifest_id": worker.manifest_id(kind),
                  "distributions": worker.distribution_inventory(site), "hardware": {"ready": False}}
        (root / "installed.json").write_text(json.dumps(marker))

    def probe_success(self, command, hardware_ready=True):
        report = {"python_abi": f"{sys.version_info.major}.{sys.version_info.minor}",
                  "hardware": {"ready": hardware_ready, "reason": "" if hardware_ready else "CUDA 不可用"}}
        return subprocess.CompletedProcess(command, 0, "SPP_RUNTIME_PROBE=" + json.dumps(report), "SoX could not be found!")

    def call(self, *args):
        proc = subprocess.run([sys.executable, str(WORKER), *args], env=self.env,
                              capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return proc.returncode, json.loads(proc.stdout.splitlines()[-1])

    def test_doctor_and_model_status_use_localappdata_and_keep_contract(self):
        code, doctor = self.call("doctor")
        self.assertEqual(code, 0)
        self.assertTrue(doctor["ok"])
        self.assertIn("core_ready", doctor)
        self.assertIn("qwen_bridge", doctor["checks"])
        self.assertTrue(doctor["paths"]["data"].startswith(self.tmp.name))
        code, status = self.call("model-status")
        self.assertEqual(code, 0)
        self.assertEqual(set(status["models"]), {"qwen", "mel_deux", "whisper"})
        self.assertEqual(set(status["runtime"]), {"qwen_python", "separator", "asr_python"})
        self.assertEqual(status["models"]["qwen"]["revision"], "fd4b254389122332181a7c3db7f27e918eec64e3")
        self.assertEqual(status["models"]["whisper"]["revision"], "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf")
        self.assertFalse(status["models"]["qwen"]["installed"])
        self.assertTrue(status["models"]["qwen"]["path"].endswith("Qwen3-TTS-12Hz-1.7B-Base"))

    def test_voice_save_persists_across_processes(self):
        ref = Path(self.tmp.name) / "sample.wav"
        ref.write_bytes(b"RIFFmock")
        code, saved = self.call("voice-save", "--name", "测试声音", "--ref-audio", str(ref),
                                "--ref-text", "你好", "--default")
        self.assertEqual(code, 0)
        self.assertTrue(saved["ok"])
        code, listed = self.call("voice-list")
        self.assertEqual(code, 0)
        self.assertEqual(len(listed["templates"]), 1)
        self.assertEqual(listed["templates"][0]["reference_text"], "你好")
        self.assertTrue(listed["templates"][0]["default"])
        self.assertTrue((Path(saved["folder"]) / "reference.wav").is_file())

    def test_voice_delete_removes_saved_template_and_default_reference(self):
        ref = Path(self.tmp.name) / "sample.wav"
        ref.write_bytes(b"RIFFmock")
        code, saved = self.call("voice-save", "--name", "可删除声音", "--ref-audio", str(ref),
                                "--ref-text", "你好", "--default")
        self.assertEqual(code, 0)
        voice_id = saved["template"]["id"]

        code, deleted = self.call("voice-delete", voice_id)
        self.assertEqual(code, 0)
        self.assertTrue(deleted["ok"])
        self.assertFalse(Path(saved["folder"]).exists())

        code, listed = self.call("voice-list")
        self.assertEqual(code, 0)
        self.assertEqual(listed["templates"], [])

    def test_download_source_persists(self):
        self.assertEqual(self.call("download-source", "official")[1]["download_source"], "official")
        self.assertEqual(self.call("model-status")[1]["download_source"], "official")

    def test_model_storage_can_switch_between_appdata_and_portable_root(self):
        portable_root = Path(self.tmp.name) / "portable-app"
        self.env["SPP_APP_ROOT"] = str(portable_root)

        code, initial = self.call("model-status")
        self.assertEqual(code, 0)
        self.assertEqual(initial["model_storage"], "appdata")
        self.assertTrue(initial["model_root"].endswith(str(Path("SPP Audio Studio") / "Models")))

        code, portable = self.call("model-storage", "portable")
        self.assertEqual(code, 0)
        self.assertEqual(portable["model_storage"], "portable")
        self.assertEqual(Path(portable["model_root"]), portable_root / "Models")
        self.assertTrue((portable_root / "Models").is_dir())

        code, persisted = self.call("model-status")
        self.assertEqual(code, 0)
        self.assertEqual(persisted["model_storage"], "portable")
        self.assertEqual(Path(persisted["model_root"]), portable_root / "Models")

        code, appdata = self.call("model-storage", "appdata")
        self.assertEqual(code, 0)
        self.assertEqual(appdata["model_storage"], "appdata")
        self.assertEqual(Path(appdata["model_root"]), Path(self.tmp.name) / "SPP Audio Studio" / "Models")

    def test_local_api_loopback_auth_and_status(self):
        environment = patch.dict(os.environ, self.env)
        environment.start()
        self.addCleanup(environment.stop)
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_local_api", API)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.PYTHON = Path(sys.executable)
        module.run_worker = lambda args: {"ok": True, "args": args}
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", 0), module.handler_factory("secret"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        conn.request("GET", "/v1/status")
        unauthorized = conn.getresponse()
        self.assertEqual(unauthorized.status, 401)
        unauthorized.read()
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        conn.request("GET", "/v1/status", headers={"Authorization": "Bearer secret"})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.read())["ok"])
        conn.close()

    def test_local_api_agent_capabilities_and_async_task_lifecycle(self):
        environment = patch.dict(os.environ, self.env)
        environment.start()
        self.addCleanup(environment.stop)
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_local_api_async", API)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calls = []
        module.run_worker = lambda args: calls.append(args) or {"ok": True, "output": "C:/out/result.wav"}

        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", 0), module.handler_factory("secret"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        headers = {"Authorization": "Bearer secret"}

        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        conn.request("GET", "/v1/capabilities", headers=headers)
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        caps = json.loads(response.read())
        self.assertEqual(caps["api_version"], "1.1")
        self.assertIn("convert_audio", caps["actions"])
        conn.close()

        payload = json.dumps({"action": "convert_audio", "input": "C:/in/demo.m4a"}).encode()
        headers_json = {**headers, "Content-Type": "application/json", "Content-Length": str(len(payload))}
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        conn.request("POST", "/v1/tasks", body=payload, headers=headers_json)
        response = conn.getresponse()
        self.assertEqual(response.status, 202)
        task_id = json.loads(response.read())["task"]["id"]
        conn.close()

        module.FUTURES[task_id].result(timeout=2)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        conn.request("GET", f"/v1/tasks/{task_id}", headers=headers)
        response = conn.getresponse()
        task = json.loads(response.read())["task"]
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["result"]["output"], "C:/out/result.wav")
        self.assertEqual(calls, [["convert", "C:/in/demo.m4a"]])
        conn.close()

    def test_runtime_manifests_are_windows_backends(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        self.assertEqual(worker.QWEN_REPO, "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
        self.assertIn("qwen-tts==0.1.1", worker.QWEN_RUNTIME_PACKAGES)
        self.assertIn("torch==2.11.0+cu126", worker.QWEN_RUNTIME_PACKAGES)
        self.assertIn("torchaudio==2.11.0+cu126", worker.QWEN_RUNTIME_PACKAGES)
        self.assertIn("faster-whisper==1.2.1", worker.ASR_RUNTIME_PACKAGES)
        self.assertIn("ctranslate2==4.8.2", worker.ASR_RUNTIME_PACKAGES)
        self.assertNotIn("mlx", " ".join(worker.ASR_RUNTIME_PACKAGES + worker.QWEN_RUNTIME_PACKAGES))
        self.assertIn("model.bin", worker.WHISPER_FILES)
        self.assertEqual(worker.QWEN_REVISION, "fd4b254389122332181a7c3db7f27e918eec64e3")
        self.assertEqual(worker.WHISPER_REVISION, "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf")
        self.assertEqual(worker.QWEN_FILES["model.safetensors"]["sha256"], "38fc7fc51c5e776e840414b6fd443962e9411b9654888fd7913e4da643cb857c")
        self.assertIsNone(worker.QWEN_FILES["config.json"]["sha256"])

    def test_qwen_auto_download_can_select_modelscope(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_auto_modelscope", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        files = {"model.safetensors": {"size": 100, "sha256": None}}

        def fake_probe(_curl, endpoint, _repo, _revision, _relpath):
            speeds = {
                worker.MODELSCOPE_ENDPOINT: 95.0,
                worker.HF_MIRROR: 80.0,
                worker.HF_OFFICIAL: 100.0,
            }
            return {"endpoint": endpoint, "ok": True, "bytes": 100, "seconds": 1.0, "bps": speeds[endpoint], "http_code": 206}

        with patch.object(worker.shutil, "which", return_value="C:/tools/curl.exe"), \
             patch.object(worker, "_probe_hf_endpoint", side_effect=fake_probe):
            order = worker._resolve_model_endpoint_order(
                "auto", worker.QWEN_REPO, worker.QWEN_REVISION, files, "qwen"
            )

        self.assertEqual(order[0], worker.MODELSCOPE_ENDPOINT)
        self.assertCountEqual(order, [worker.MODELSCOPE_ENDPOINT, worker.HF_MIRROR, worker.HF_OFFICIAL])
        url = worker._model_download_url(
            worker.MODELSCOPE_ENDPOINT, worker.QWEN_REPO, worker.QWEN_REVISION, "speech_tokenizer/model.safetensors"
        )
        self.assertIn("/api/v1/models/Qwen/Qwen3-TTS-12Hz-1.7B-Base/repo", url)
        self.assertIn("Revision=master", url)
        self.assertIn("FilePath=speech_tokenizer%2Fmodel.safetensors", url)

    def test_auto_download_prefers_mirror_when_speed_is_close(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_auto_mirror", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        files = {"model.safetensors": {"size": 100, "sha256": None}}

        def fake_probe(_curl, endpoint, _repo, _revision, _relpath):
            bps = 90.0 if endpoint == worker.HF_MIRROR else 100.0
            return {"endpoint": endpoint, "ok": True, "bytes": 100, "seconds": 1.0, "bps": bps, "http_code": 206}

        with patch.object(worker.shutil, "which", return_value="C:/tools/curl.exe"), \
             patch.object(worker, "_probe_hf_endpoint", side_effect=fake_probe):
            order = worker._resolve_model_endpoint_order("auto", "a/b", "rev", files, "qwen")

        self.assertEqual(order, [worker.HF_MIRROR, worker.HF_OFFICIAL])

    def test_auto_download_uses_official_when_materially_faster(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_auto_official", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        files = {"model.safetensors": {"size": 100, "sha256": None}}

        def fake_probe(_curl, endpoint, _repo, _revision, _relpath):
            bps = 40.0 if endpoint == worker.HF_MIRROR else 100.0
            return {"endpoint": endpoint, "ok": True, "bytes": 100, "seconds": 1.0, "bps": bps, "http_code": 206}

        with patch.object(worker.shutil, "which", return_value="C:/tools/curl.exe"), \
             patch.object(worker, "_probe_hf_endpoint", side_effect=fake_probe):
            order = worker._resolve_model_endpoint_order("auto", "a/b", "rev", files, "qwen")

        self.assertEqual(order, [worker.HF_OFFICIAL, worker.HF_MIRROR])

    def test_download_resumes_with_discovered_curl_and_falls_back(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_download", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        target = Path(self.tmp.name) / "model.bin"
        part = target.with_suffix(".bin.part.https-hf-mirror-com")
        part.write_bytes(b"12")
        commands = []
        class FakeProcess:
            def __init__(self, cmd):
                commands.append(cmd)
                # Failed range resume -> clean retry -> alternate mirror.
                self.returncode = 22 if len(commands) <= 2 else 0
                if len(commands) == 3:
                    Path(cmd[cmd.index("-o") + 1]).write_bytes(b"12345")
            def poll(self):
                return self.returncode
        with patch.object(worker.shutil, "which", return_value="C:/tools/curl.exe"), \
             patch.object(worker.subprocess, "Popen", side_effect=FakeProcess), \
             patch.object(worker.time, "sleep", return_value=None):
            worker._download_one(
                "a/b", "main", "model.bin", {"size": 5, "sha256": None}, target, "mirror",
                model="qwen", completed_before=0, model_total=5,
            )
        self.assertEqual(target.read_bytes(), b"12345")
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[0][0], "C:/tools/curl.exe")
        self.assertIn("-C", commands[0])
        self.assertNotIn("-C", commands[1])
        self.assertIn("hf-mirror.com", commands[0][-1])
        self.assertIn("hf-mirror.com", commands[1][-1])
        self.assertIn("huggingface.co", commands[2][-1])

    def test_download_rejects_wrong_sha256(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_hash", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        target = Path(self.tmp.name) / "model.bin"
        payload = b"12345"
        class FakeProcess:
            def __init__(self, cmd):
                Path(cmd[cmd.index("-o") + 1]).write_bytes(payload)
                self.returncode = 0
            def poll(self):
                return self.returncode
        expected = hashlib.sha256(b"other").hexdigest()
        with patch.object(worker.shutil, "which", return_value="C:/tools/curl.exe"), \
             patch.object(worker.subprocess, "Popen", side_effect=FakeProcess), \
             patch.object(worker.time, "sleep", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                worker._download_one(
                    "a/b", "rev", "model.bin", {"size": len(payload), "sha256": expected}, target, "official",
                    model="qwen", completed_before=0, model_total=len(payload),
                )
        self.assertFalse(target.exists())

    def test_auto_runtime_source_prefers_china_mirror_when_close(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_runtime_auto_mirror", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)

        def fake_probe(_curl, endpoint):
            speeds = {
                worker.PYPI_MIRROR: 90.0,
                worker.PYPI_USTC: 80.0,
                worker.PYPI_ALIYUN: 70.0,
                worker.PYPI_OFFICIAL: 100.0,
            }
            return {"endpoint": endpoint, "ok": True, "bytes": 100, "seconds": 1.0, "bps": speeds[endpoint], "http_code": 206}

        with patch.object(worker.shutil, "which", return_value="C:/tools/curl.exe"), \
             patch.object(worker, "_probe_pypi_endpoint", side_effect=fake_probe):
            order = worker._resolve_pypi_endpoint_order("auto", "qwen")

        self.assertEqual(order[0], worker.PYPI_MIRROR)
        self.assertCountEqual(order, [worker.PYPI_MIRROR, worker.PYPI_USTC, worker.PYPI_ALIYUN, worker.PYPI_OFFICIAL])

    def test_auto_runtime_source_uses_official_when_materially_faster(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_runtime_auto_official", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)

        def fake_probe(_curl, endpoint):
            speeds = {
                worker.PYPI_MIRROR: 40.0,
                worker.PYPI_USTC: 30.0,
                worker.PYPI_ALIYUN: 20.0,
                worker.PYPI_OFFICIAL: 100.0,
            }
            return {"endpoint": endpoint, "ok": True, "bytes": 100, "seconds": 1.0, "bps": speeds[endpoint], "http_code": 206}

        with patch.object(worker.shutil, "which", return_value="C:/tools/curl.exe"), \
             patch.object(worker, "_probe_pypi_endpoint", side_effect=fake_probe):
            order = worker._resolve_pypi_endpoint_order("auto", "qwen")

        self.assertEqual(order[0], worker.PYPI_OFFICIAL)
        self.assertCountEqual(order, [worker.PYPI_MIRROR, worker.PYPI_USTC, worker.PYPI_ALIYUN, worker.PYPI_OFFICIAL])

    def test_auto_pytorch_source_prefers_sjtu_when_speed_is_close(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_torch_auto_sjtu", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)

        def fake_probe(_curl, endpoint):
            bps = 90.0 if endpoint == worker.PYTORCH_CUDA_SJTU else 100.0
            return {"endpoint": endpoint, "ok": True, "bytes": 100, "seconds": 1.0, "bps": bps, "compatible": True}

        with patch.object(worker.shutil, "which", return_value="C:/tools/curl.exe"), \
             patch.object(worker, "_probe_pytorch_endpoint", side_effect=fake_probe):
            order = worker._resolve_pytorch_endpoint_order("auto", "qwen")

        self.assertEqual(order, [worker.PYTORCH_CUDA_SJTU, worker.PYTORCH_CUDA_OFFICIAL])

    def test_auto_pytorch_source_uses_official_when_materially_faster(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_torch_auto_official", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)

        def fake_probe(_curl, endpoint):
            bps = 40.0 if endpoint == worker.PYTORCH_CUDA_SJTU else 100.0
            return {"endpoint": endpoint, "ok": True, "bytes": 100, "seconds": 1.0, "bps": bps, "compatible": True}

        with patch.object(worker.shutil, "which", return_value="C:/tools/curl.exe"), \
             patch.object(worker, "_probe_pytorch_endpoint", side_effect=fake_probe):
            order = worker._resolve_pytorch_endpoint_order("auto", "qwen")

        self.assertEqual(order, [worker.PYTORCH_CUDA_OFFICIAL, worker.PYTORCH_CUDA_SJTU])

    def test_runtime_detection_uses_qwen_tts_and_faster_whisper(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_runtime", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        with patch.object(worker, "QWEN_RUNTIME_DIR", Path(self.tmp.name) / "qwen"), \
             patch.object(worker, "ASR_RUNTIME_DIR", Path(self.tmp.name) / "asr"), \
             patch.object(worker, "QWEN_SITE", Path(self.tmp.name) / "qwen/site-packages"), \
             patch.object(worker, "ASR_SITE", Path(self.tmp.name) / "asr/site-packages"):
            for name, package in (("qwen", "qwen_tts"), ("asr", "faster_whisper")):
                runtime = Path(self.tmp.name) / name
                (runtime / "site-packages" / package).mkdir(parents=True)
                (runtime / "installed.json").write_text("{}")
                self.assertFalse(worker._runtime_installed(name))
                health = worker._runtime_health(name)
                self.assertTrue(health["installed"])
                self.assertFalse(health["healthy"])
                self.assertEqual(health["status"], "repair_required")

    def test_separator_preserves_windows_path_for_ffmpeg(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_sep", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        src = Path(self.tmp.name) / "song.wav"
        src.write_bytes(b"RIFF")
        mel_dir = Path(self.tmp.name) / "mel"
        mel_dir.mkdir()
        capture = {}
        def fake_run(cmd, env):
            capture.update(env)
            output = Path(cmd[cmd.index("--output_dir") + 1])
            (output / "song_(Vocals)_becruily_deux.wav").write_bytes(b"v")
            (output / "song_(Instrumental)_becruily_deux.wav").write_bytes(b"i")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        args = worker.build_parser().parse_args(["separate", str(src), "--format", "WAV"])
        with patch.object(worker, "CACHE_DIR", Path(self.tmp.name)), \
             patch.object(worker, "resolve_environment", return_value={"mel_model_dir": str(mel_dir), "separator_runtime_installed": True, "separator_source": "managed", "mel_site": str(mel_dir)}), \
             patch.object(worker, "locate_ffmpeg", return_value=Path(self.tmp.name) / "tools/ffmpeg.exe"), \
             patch.object(worker, "gpu_lock", return_value=nullcontext()), \
             patch.object(worker, "run_capture", side_effect=fake_run):
            self.assertEqual(worker.cmd_separate(args), 0)
        self.assertEqual(capture["PATH"].split(os.pathsep)[0], str(Path(self.tmp.name) / "tools"))
        self.assertEqual(capture["SPP_FFMPEG"], capture["IMAGEIO_FFMPEG_EXE"])
        self.assertEqual(capture["SPP_FFMPEG"], capture["FFMPEG_BINARY"])
        self.assertIn(os.environ["PATH"], capture["PATH"])

    def test_separator_rejects_unverifiable_linked_runtime(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_linked_sep", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        src = Path(self.tmp.name) / "song.wav"
        src.write_bytes(b"RIFF")
        args = worker.build_parser().parse_args(["separate", str(src), "--format", "WAV"])
        with patch.object(worker, "CACHE_DIR", Path(self.tmp.name)), \
             patch.object(worker, "resolve_environment", return_value={"mel_model_dir": self.tmp.name, "separator_runtime_installed": True, "separator_source": "linked", "separator_bin": "C:/tools/audio-separator.exe"}):
            with patch("builtins.print") as output:
                self.assertEqual(worker.cmd_separate(args), 3)
        self.assertIn("CUDA", output.call_args.args[0])

    def test_runtime_install_verifies_windows_imports_without_unix_pip_flags(self):
        worker = self.load_worker()
        commands = []
        def fake_run(cmd, env=None, timeout=None):
            commands.append(cmd)
            if "install" in cmd:
                self.populate_runtime(worker, Path(cmd[cmd.index("--target") + 1]).parent, "qwen")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return self.probe_success(cmd, hardware_ready=False)
        with patch.object(worker, "RUNTIME_DIR", Path(self.tmp.name) / "Runtime"), \
             patch.object(worker, "QWEN_RUNTIME_DIR", Path(self.tmp.name) / "Runtime/qwen"), \
             patch.object(worker, "_runtime_installed", return_value=False), \
             patch.object(worker, "_resolve_pypi_endpoint_order", return_value=[worker.PYPI_OFFICIAL]), \
             patch.object(worker, "_resolve_pytorch_endpoint_order", return_value=[worker.PYTORCH_CUDA_OFFICIAL]), \
             patch.object(worker, "gpu_lock", return_value=nullcontext()), \
             patch("media_tools.probe_ffmpeg", side_effect=self.valid_ffmpeg), \
             patch.object(worker, "run_capture", side_effect=fake_run):
            result = worker.cmd_runtime_install(worker.build_parser().parse_args(["runtime-install", "qwen"]))
            health = worker._runtime_health("qwen")
        self.assertEqual(result, 0)
        self.assertTrue(health["healthy"])
        self.assertFalse(health["hardware"]["ready"])
        self.assertTrue(any("qwen_tts" in " ".join(c) for c in commands))
        self.assertFalse(any("mlx" in " ".join(c) for c in commands))
        self.assertFalse(any("--break-system-packages" in c for c in commands))
        self.assertTrue(any("https://download.pytorch.org/whl/cu126" in c for c in commands))
        self.assertIn("torch==2.11.0+cu126", " ".join(commands[0]))
        self.assertTrue(any("torch.cuda.is_available" in " ".join(c) for c in commands))

    def test_frozen_worker_uses_python_core_for_managed_commands(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_frozen_commands", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        release = Path(self.tmp.name).resolve() / "release"
        python_core = release / "python-core/python.exe"
        worker_exe = str(release / "worker/SPPWorker/SPPWorker.exe")
        with patch.object(worker, "IS_FROZEN", True), \
             patch.object(worker, "PYTHON_CORE", python_core), \
             patch.object(worker.sys, "executable", worker_exe):
            self.assertEqual(
                worker._managed_command("qwen", ["--text", "hello"]),
                [str(python_core), str(worker.BRIDGE), "--text", "hello"],
            )
            self.assertEqual(
                worker._managed_command("mel", ["--output_dir", "out"]),
                [str(python_core), str(worker.MEL_BRIDGE), "--output_dir", "out"],
            )
            self.assertEqual(
                worker._pip_command(["install", "demo"]),
                [str(python_core), "-m", "pip", "install", "demo"],
            )
            verify = worker._verify_command("asr", Path("C:/runtime/asr/site-packages"))
            self.assertEqual(verify[:2], [str(python_core), "-c"])

            self.assertTrue(worker._managed_bridge_available("qwen"))
            self.assertTrue(worker._managed_bridge_available("mel"))
            self.assertEqual(
                worker._bundled_format_binary(),
                release / "worker/bin/format_converter.exe",
            )

    def test_development_worker_keeps_normal_python_execution(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_dev_commands", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        python = Path(sys.executable)
        with patch.object(worker, "IS_FROZEN", False), \
             patch.object(worker, "PYTHON_CORE", python):
            self.assertEqual(worker._managed_command("qwen", ["--text", "hello"]),
                             [str(python), str(worker.BRIDGE), "--text", "hello"])
            self.assertEqual(worker._managed_command("mel", ["--output_dir", "out"]),
                             [str(python), str(worker.MEL_BRIDGE), "--output_dir", "out"])
            self.assertEqual(worker._pip_command(["install", "demo"]),
                             [str(python), "-m", "pip", "install", "demo"])
            verify = worker._verify_command("qwen", Path("C:/runtime/qwen/site-packages"))
            self.assertEqual(verify[:2], [str(python), "-c"])
            self.assertIn("qwen_tts", verify[2])

    def test_frozen_worker_resolves_python_core_and_bridges_from_release_layout(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_frozen_core", WORKER)
        worker = importlib.util.module_from_spec(spec)
        release = Path(self.tmp.name).resolve() / "release"
        worker_exe = str(release / "worker/SPPWorker/SPPWorker.exe")
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", worker_exe), \
             patch.dict(os.environ, {"SPP_PYTHON_CORE": "C:/wrong/python.exe"}):
            spec.loader.exec_module(worker)
        self.assertEqual(worker.PYTHON_CORE, release / "python-core/python.exe")
        self.assertEqual(worker.BRIDGE, release / "worker/qwen_bridge.py")
        self.assertEqual(worker.MEL_BRIDGE, release / "worker/mel_bridge.py")
        self.assertEqual(worker.BUNDLED_FORMAT_BIN, release / "worker/bin/format_converter.exe")

    def test_qwen_bridge_transcribe_uses_python_interpreter(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_qwen_asr", ROOT / "worker/qwen_bridge.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        python = Path(self.tmp.name) / "python.exe"
        python.write_bytes(b"exe")
        model = Path(self.tmp.name) / "asr"
        model.mkdir()
        commands = []
        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "linked result\n", "")
        with patch.object(bridge, "ASR_PY", python), \
             patch.object(bridge, "ASR_MODEL", model), \
             patch.object(bridge.subprocess, "run", side_effect=fake_run):
            self.assertEqual(bridge.transcribe(Path(self.tmp.name) / "ref.wav"), "linked result")
        self.assertEqual(commands[0][:2], [str(python), "-c"])

    def test_manifest_marks_unavailable_hashes_unknown(self):
        manifest = json.loads((ROOT / "models.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 2)
        self.assertIsNone(manifest["qwen"]["files"]["config.json"]["sha256"])
        for model in ("qwen", "mel", "whisper"):
            self.assertRegex(manifest[model]["revision"], r"^[0-9a-f]{40}$")
            for metadata in manifest[model]["files"].values():
                self.assertGreater(metadata["size"], 0)
                if metadata["sha256"] is not None:
                    self.assertRegex(metadata["sha256"], r"^[0-9a-f]{64}$")

    def test_qwen_bridge_uses_official_pytorch_clone_api(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_qwen", ROOT / "worker/qwen_bridge.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        model_dir = Path(self.tmp.name) / "model"
        model_dir.mkdir()
        ref = Path(self.tmp.name) / "ref.wav"
        ref.write_bytes(b"RIFF")
        args = bridge.build_parser().parse_args(["--ref-audio", str(ref), "--ref-text", "你好", "--text", "声音", "--output-dir", self.tmp.name])
        generated = []
        class FakeModel:
            def generate_voice_clone(self, **kwargs):
                generated.append(kwargs)
                return [[0.0, 0.0]], 24000
        qwen = ModuleType("qwen_tts")
        qwen.Qwen3TTSModel = type("Qwen3TTSModel", (), {"from_pretrained": staticmethod(lambda *a, **k: FakeModel())})
        torch = ModuleType("torch")
        torch.cuda = type("Cuda", (), {"is_available": staticmethod(lambda: True)})()
        torch.bfloat16 = "bfloat16"
        soundfile = ModuleType("soundfile")
        soundfile.write = lambda path, audio, sr: Path(path).write_bytes(b"RIFF")
        soundfile.info = lambda path: type("Info", (), {"duration": 0.01})()
        def fake_ffmpeg(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"RIFF")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with patch.object(bridge, "QWEN_MODEL", model_dir), patch.object(bridge, "build_parser", return_value=type("Parser", (), {"parse_args": lambda self: args})()), \
             patch.dict(sys.modules, {"qwen_tts": qwen, "torch": torch, "soundfile": soundfile}), \
             patch.object(bridge.subprocess, "run", side_effect=fake_ffmpeg), \
             patch.object(bridge, "find_ffmpeg", return_value="C:/tools/ffmpeg.exe"):
            self.assertEqual(bridge.main(), 0)
        self.assertEqual(generated[0]["language"], "Chinese")
        self.assertEqual(generated[0]["ref_text"], "你好")
        self.assertEqual(generated[0]["ref_audio"].endswith("reference.wav"), True)
        self.assertEqual(generated[0]["top_k"], 50)
        outputs = [path.name for path in Path(self.tmp.name).glob("声音_*.wav")]
        self.assertEqual(len(outputs), 1)
        self.assertNotIn("t0.9", outputs[0])
        self.assertNotIn("AI Clone", outputs[0])

    def test_qwen_bridge_refuses_cpu_fallback(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_qwen_gate", ROOT / "worker/qwen_bridge.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        torch = ModuleType("torch")
        torch.cuda = type("Cuda", (), {"is_available": staticmethod(lambda: False)})()
        with patch.dict(sys.modules, {"torch": torch}):
            with self.assertRaisesRegex(RuntimeError, "CUDA"):
                bridge.require_cuda(torch)

    def test_optional_asr_uses_ctranslate2_faster_whisper(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_qwen_asr", ROOT / "worker/qwen_bridge.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        python = Path(sys.executable)
        model = Path(self.tmp.name) / "asr"
        model.mkdir()
        commands = []
        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "转写结果\n", "")
        with patch.object(bridge, "ASR_PY", python), patch.object(bridge, "ASR_MODEL", model), \
             patch.object(bridge.subprocess, "run", side_effect=fake_run):
            self.assertEqual(bridge.transcribe(Path(self.tmp.name) / "ref.wav"), "转写结果")
        self.assertIn("faster_whisper", commands[0][2])
        self.assertIn("device='cuda'", commands[0][2])
        self.assertIn("compute_type='float16'", commands[0][2])
        self.assertNotIn("mlx", commands[0][2])

    @unittest.skipUnless(sys.platform == "win32", "Requires Windows msvcrt file locks")
    def test_gpu_jobs_use_cross_process_lock(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_lock", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        self.assertTrue(worker.GPU_LOCK_FILE.name.endswith("gpu.lock"))
        with worker.gpu_lock(timeout=1):
            self.assertTrue(worker.GPU_LOCK_FILE.is_file())

    def test_locate_ffmpeg_uses_managed_qwen_imageio_binary(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_ffmpeg", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        site = Path(self.tmp.name) / "site"
        binary = site / "imageio_ffmpeg/binaries/ffmpeg-win64-v7.exe"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"exe")
        empty = Path(self.tmp.name) / "empty"
        with patch.object(worker, "QWEN_SITE", site), \
             patch.object(worker, "MEL_SITE", empty), \
             patch.object(worker, "ASR_SITE", empty), \
             patch.object(worker, "MEL_RUNTIME_DIR", empty), \
             patch.object(worker, "ASR_RUNTIME_DIR", empty), \
             patch.object(worker, "RUNTIME_DIR", Path(self.tmp.name) / "Runtime"), \
             patch("media_tools.probe_ffmpeg", side_effect=self.valid_ffmpeg), \
             patch.object(worker.shutil, "which", return_value=None):
            resolved = worker.locate_ffmpeg()
            self.assertEqual(resolved, worker.RUNTIME_DIR / "tools/ffmpeg/ffmpeg.exe")
            self.assertEqual(resolved.read_bytes(), binary.read_bytes())

    def test_bridge_respects_valid_explicit_ffmpeg_executable(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_qwen_ffmpeg", ROOT / "worker/qwen_bridge.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        executable = Path(self.tmp.name) / "external/ffmpeg.exe"
        executable.parent.mkdir()
        executable.write_bytes(b"executable")
        with patch.dict(os.environ, {"SPP_FFMPEG": str(executable), "SPP_RUNTIME_ROOT": self.tmp.name}), \
             patch("media_tools.probe_ffmpeg", side_effect=self.valid_ffmpeg), \
             patch.object(bridge.shutil, "which", return_value=None):
            self.assertEqual(bridge.find_ffmpeg(), str(executable))

    def test_managed_ffmpeg_wins_over_system_alias(self):
        self.load_worker()
        import media_tools
        root = Path(self.tmp.name)
        managed = root / "qwen/ffmpeg/ffmpeg.exe"
        managed.parent.mkdir(parents=True)
        managed.write_bytes(b"real")
        alias = root / "alias.exe"
        alias.write_bytes(b"")
        with patch.object(media_tools.shutil, "which", return_value=str(alias)), \
             patch.object(media_tools.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ffmpeg version 7.1", "")) as run:
            result = media_tools.resolve_ffmpeg([root / "qwen"], [])
        self.assertEqual(result["path"], str(managed))
        self.assertEqual(run.call_args.args[0], [str(managed), "-version"])
        self.assertEqual(run.call_args.kwargs["timeout"], 5)

    def test_ffmpeg_execution_failure_and_timeout_fall_back(self):
        self.load_worker()
        import media_tools
        root = Path(self.tmp.name)
        broken = root / "qwen/ffmpeg/ffmpeg.exe"
        good = root / "mel/ffmpeg/ffmpeg.exe"
        for path in (broken, good):
            path.parent.mkdir(parents=True)
            path.write_bytes(b"")
        for error in (OSError(193, "%1 is not a valid Win32 application"), subprocess.TimeoutExpired([str(broken)], 5)):
            with self.subTest(error=error), patch.object(media_tools.shutil, "which", return_value=None), \
                 patch.object(media_tools.subprocess, "run", side_effect=[error, subprocess.CompletedProcess([], 0, "ffmpeg version 7.1", "")]):
                result = media_tools.resolve_ffmpeg([root / "qwen", root / "mel"], [])
            self.assertTrue(result["ready"])
            self.assertEqual(result["path"], str(good))
            self.assertEqual(result["failures"][0]["path"], str(broken))

    def test_non_ffmpeg_success_is_not_healthy(self):
        self.load_worker()
        import media_tools
        with patch.object(media_tools.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "not the tool", "")):
            self.assertFalse(media_tools.probe_ffmpeg(Path("ffmpeg.exe"))["ready"])

    def test_runtime_metadata_drift_requires_repair(self):
        worker = self.load_worker()
        root = Path(self.tmp.name) / "qwen"
        self.populate_runtime(worker, root, "qwen")
        self.assertTrue(worker.runtime_health("qwen", root)["healthy"])
        metadata = next((root / "site-packages").glob("*.dist-info/METADATA"))
        metadata.write_text("Name: changed\nVersion: 99\n")
        health = worker.runtime_health("qwen", root)
        self.assertTrue(health["installed"])
        self.assertFalse(health["healthy"])

    def test_repair_forces_staging_and_failed_verification_preserves_runtime(self):
        worker = self.load_worker()
        root = Path(self.tmp.name) / "Runtime"
        final = root / "qwen"
        self.populate_runtime(worker, final, "qwen")
        before = (final / "installed.json").read_bytes()
        calls = []
        def fake_run(cmd, env=None, timeout=None):
            calls.append(cmd)
            if "install" in cmd:
                self.populate_runtime(worker, Path(cmd[cmd.index("--target") + 1]).parent, "qwen")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 1, "", "ImportError: damaged native library")
        with patch.object(worker, "RUNTIME_DIR", root), patch.object(worker, "QWEN_RUNTIME_DIR", final), \
             patch.object(worker, "_runtime_installed", return_value=True) as installed, \
             patch.object(worker, "_resolve_pypi_endpoint_order", return_value=[worker.PYPI_OFFICIAL]), \
             patch.object(worker, "_resolve_pytorch_endpoint_order", return_value=[worker.PYTORCH_CUDA_OFFICIAL]), \
             patch.object(worker, "gpu_lock", return_value=nullcontext()), \
             patch.object(worker, "run_capture", side_effect=fake_run):
            code = worker.cmd_runtime_install(worker.build_parser().parse_args(["runtime-install", "qwen", "--repair"]))
        self.assertEqual(code, 4)
        installed.assert_not_called()
        self.assertTrue(any("install" in command for command in calls))
        self.assertEqual((final / "installed.json").read_bytes(), before)
        self.assertFalse(list((root / ".spp-install-staging").iterdir()))

    def test_runtime_promotion_failure_rolls_back_previous_installation(self):
        worker = self.load_worker()
        root = Path(self.tmp.name)
        final, staged = root / "qwen", root / "staged"
        final.mkdir()
        staged.mkdir()
        (final / "identity").write_text("previous")
        (staged / "identity").write_text("replacement")
        real_replace = os.replace
        def replace(source, target):
            if Path(source) == staged:
                raise PermissionError("runtime locked")
            return real_replace(source, target)
        with patch.object(worker.os, "replace", side_effect=replace):
            with self.assertRaises(PermissionError):
                worker._promote_runtime(staged, final)
        self.assertEqual((final / "identity").read_text(), "previous")
        self.assertEqual((staged / "identity").read_text(), "replacement")

    def test_missing_gpu_does_not_mark_runtime_for_repair(self):
        worker = self.load_worker()
        health = {"installed": True, "healthy": True, "status": "ready", "reason": "ok"}
        with patch.object(worker, "_verify_python_import", return_value=self.probe_success([], hardware_ready=False)):
            result = worker._live_runtime_health("qwen", health)
        self.assertTrue(result["healthy"])
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["hardware"]["ready"])

    def test_managed_runtime_is_selected_after_repair_even_with_saved_link(self):
        worker = self.load_worker()
        linked = Path(self.tmp.name) / "external-python.exe"
        linked.write_bytes(b"external")
        def healthy(kind):
            return {"installed": kind == "qwen", "healthy": kind == "qwen",
                    "status": "ready" if kind == "qwen" else "missing", "reason": "test"}
        with patch.object(worker, "load_settings", return_value={"paths": {"qwen_python": str(linked)}}), \
             patch.object(worker, "_runtime_health", side_effect=healthy), patch.object(worker, "ensure_dirs"):
            env = worker.resolve_environment()
        self.assertEqual(env["qwen_python_source"], "managed")
        self.assertEqual(env["qwen_python"], worker.PYTHON_CORE)
        self.assertTrue(env["runtime_health"]["qwen"]["healthy"])
        self.assertTrue(linked.is_file())
        with patch.object(worker, "load_settings", return_value={"paths": {"qwen_python": str(linked)}}), \
             patch.object(worker, "_runtime_health", return_value={"installed": False, "healthy": False, "status": "missing", "reason": "test"}), \
             patch.object(worker, "ensure_dirs"):
            env = worker.resolve_environment()
        self.assertEqual(env["qwen_python_source"], "linked")
        self.assertFalse(env["runtime_health"]["qwen"]["healthy"])
        self.assertIn("外部", env["runtime_health"]["qwen"]["reason"])

    def test_doctor_busy_gpu_keeps_installation_healthy_and_skips_cuda_probe(self):
        worker = self.load_worker()
        health = {"qwen": {"installed": True, "healthy": True, "status": "ready", "reason": "ok"}}
        captured = []
        def verify(kind, site, **kwargs):
            captured.append(kwargs)
            self.assertFalse(kwargs["probe_gpu"])
            result = self.probe_success([], hardware_ready=False)
            report = worker._probe_result(result)
            report["hardware"].update(status="busy", reason="GPU 正在执行任务")
            return subprocess.CompletedProcess([], 0, "SPP_RUNTIME_PROBE=" + json.dumps(report), "")
        with patch.object(worker, "gpu_lock", side_effect=TimeoutError("busy")), \
             patch.object(worker, "_verify_python_import", side_effect=verify):
            result = worker._doctor_runtime_health(health)
        self.assertTrue(result["qwen"]["healthy"])
        self.assertEqual(result["qwen"]["hardware"]["status"], "busy")
        self.assertEqual(len(captured), 1)

    def test_staging_verification_skips_cuda_when_gpu_is_busy(self):
        worker = self.load_worker()
        with patch.object(worker, "gpu_lock", side_effect=TimeoutError("busy")), \
             patch.object(worker, "run_capture", return_value=self.probe_success([], False)) as run:
            result = worker._verify_python_import("qwen", Path(self.tmp.name) / "site-packages")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(run.call_args.args[1]["SPP_RUNTIME_PROBE_GPU"], "0")
        self.assertEqual(run.call_args.kwargs["timeout"], 60)
        self.assertIn("qwen_tts", run.call_args.args[0][2])

    def test_runtime_probe_distinguishes_gpu_busy_and_missing_without_failing_imports(self):
        worker = self.load_worker()
        modules = {name: ModuleType(name) for name in ("qwen_tts", "torch", "torchaudio", "soundfile", "imageio_ffmpeg")}
        modules["imageio_ffmpeg"].get_ffmpeg_exe = lambda: "ffmpeg.exe"
        calls = []
        modules["torch"].cuda = type("Cuda", (), {"is_available": staticmethod(lambda: calls.append("cuda") or False)})()
        for probe_gpu, expected_status in (("0", "busy"), ("1", "unavailable")):
            calls.clear()
            with self.subTest(probe_gpu=probe_gpu), patch.dict(sys.modules, modules), \
                 patch.dict(os.environ, {"SPP_RUNTIME_PROBE_GPU": probe_gpu}), patch("builtins.print") as output:
                exec(compile(worker._verification_script("qwen"), "<probe>", "exec"), {})
            report = json.loads(output.call_args.args[0].split("=", 1)[1])
            self.assertFalse(report["hardware"]["ready"])
            self.assertEqual(report["hardware"]["status"], expected_status)
            self.assertEqual(calls, [] if probe_gpu == "0" else ["cuda"])

    def test_runtime_requirement_documents_match_manifest(self):
        self.load_worker()
        import runtime_manifest
        for kind, packages in runtime_manifest.PACKAGES.items():
            lines = (ROOT / f"requirements-{kind}.txt").read_text().splitlines()
            actual = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
            self.assertEqual(actual, packages)

    def test_run_capture_replaces_undecodable_native_output(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_decode", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        command = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xb2')"]
        result = worker.run_capture(command)
        self.assertEqual(result.returncode, 0)
        self.assertIsInstance(result.stdout, str)


if __name__ == "__main__":
    unittest.main()
