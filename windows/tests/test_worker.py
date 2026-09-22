"""Model-free Windows worker contract tests: python -m unittest discover -s windows/tests -v."""
import http.client
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

    def test_download_resumes_with_discovered_curl_and_falls_back(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_download", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        target = Path(self.tmp.name) / "model.bin"
        part = target.with_suffix(".bin.part")
        part.write_bytes(b"12")
        commands = []
        class FakeProcess:
            def __init__(self, cmd):
                commands.append(cmd)
                self.returncode = 22 if len(commands) == 1 else 0
                if len(commands) == 2:
                    part.write_bytes(b"12345")
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
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][0], "C:/tools/curl.exe")
        self.assertIn("-C", commands[0])
        self.assertIn("hf-mirror.com", commands[0][-1])
        self.assertIn("huggingface.co", commands[1][-1])

    def test_download_rejects_wrong_sha256(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_hash", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        target = Path(self.tmp.name) / "model.bin"
        part = target.with_suffix(".bin.part")
        payload = b"12345"
        class FakeProcess:
            def __init__(self, cmd):
                part.write_bytes(payload)
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
                self.assertTrue(worker._runtime_installed(name))

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
             patch.object(worker, "locate_ffmpeg", return_value=Path("C:/tools/ffmpeg.exe")), \
             patch.object(worker, "run_capture", side_effect=fake_run):
            self.assertEqual(worker.cmd_separate(args), 0)
        self.assertEqual(capture["PATH"].split(os.pathsep)[0], "C:\\tools")
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
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_install", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        commands = []
        def fake_run(cmd, env=None):
            commands.append(cmd)
            if "install" in cmd:
                site = Path(cmd[cmd.index("--target") + 1])
                (site / "qwen_tts").mkdir()
            return subprocess.CompletedProcess(cmd, 0, "QWEN_RUNTIME_OK", "")
        with patch.object(worker, "CACHE_DIR", Path(self.tmp.name)), \
             patch.object(worker, "QWEN_RUNTIME_DIR", Path(self.tmp.name) / "managed"), \
             patch.object(worker, "_runtime_installed", return_value=False), \
             patch.object(worker, "run_capture", side_effect=fake_run):
            result = worker.cmd_runtime_install(worker.build_parser().parse_args(["runtime-install", "qwen"]))
        self.assertEqual(result, 0)
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
        python_core = Path("C:/release/python-core/python.exe")
        worker_exe = "C:/release/worker/SPPWorker/SPPWorker.exe"
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
                Path("C:/release/worker/bin/format_converter.exe"),
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
        worker_exe = "C:/release/worker/SPPWorker/SPPWorker.exe"
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", worker_exe), \
             patch.dict(os.environ, {"SPP_PYTHON_CORE": "C:/wrong/python.exe"}):
            spec.loader.exec_module(worker)
        self.assertEqual(worker.PYTHON_CORE, Path("C:/release/python-core/python.exe"))
        self.assertEqual(worker.BRIDGE, Path("C:/release/worker/qwen_bridge.py"))
        self.assertEqual(worker.MEL_BRIDGE, Path("C:/release/worker/mel_bridge.py"))
        self.assertEqual(worker.BUNDLED_FORMAT_BIN, Path("C:/release/worker/bin/format_converter.exe"))

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
             patch.object(bridge.shutil, "which", return_value="C:/tools/ffmpeg.exe"):
            self.assertEqual(bridge.main(), 0)
        self.assertEqual(generated[0]["language"], "Chinese")
        self.assertEqual(generated[0]["ref_text"], "你好")
        self.assertEqual(generated[0]["ref_audio"].endswith("reference.wav"), True)
        self.assertEqual(generated[0]["top_k"], 50)

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
             patch.object(worker.shutil, "which", return_value=None):
            self.assertEqual(worker.locate_ffmpeg(), binary)

    def test_bridge_respects_explicit_ffmpeg_executable(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_qwen_ffmpeg", ROOT / "worker/qwen_bridge.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        with patch.dict(os.environ, {"SPP_FFMPEG": "C:/tools/imageio/ffmpeg-win64.exe"}), \
             patch.object(bridge.shutil, "which", return_value=None):
            self.assertEqual(bridge.find_ffmpeg(), "C:/tools/imageio/ffmpeg-win64.exe")

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
