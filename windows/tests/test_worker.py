"""Model-free Windows worker contract tests: python -m unittest discover -s windows/tests -v."""
import http.client
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
                              capture_output=True, text=True, timeout=20)
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

    def test_local_api_loopback_auth_and_status(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_local_api", API)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", 0), module.handler_factory("secret"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        conn.request("GET", "/v1/status")
        self.assertEqual(conn.getresponse().status, 401)
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
        self.assertIn("qwen-tts", " ".join(worker.QWEN_RUNTIME_PACKAGES))
        self.assertIn("faster-whisper", " ".join(worker.ASR_RUNTIME_PACKAGES))
        self.assertNotIn("mlx", " ".join(worker.ASR_RUNTIME_PACKAGES + worker.QWEN_RUNTIME_PACKAGES))
        self.assertIn("model.bin", worker.WHISPER_FILES)

    def test_download_resumes_with_discovered_curl_and_falls_back(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_download", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        target = Path(self.tmp.name) / "model.bin"
        part = target.with_suffix(".bin.part")
        part.write_bytes(b"12")
        commands = []
        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if len(commands) == 2:
                part.write_bytes(b"12345")
            return subprocess.CompletedProcess(cmd, 22 if len(commands) == 1 else 0)
        with patch.object(worker.shutil, "which", return_value="C:/tools/curl.exe"), patch.object(worker.subprocess, "run", side_effect=fake_run):
            worker._download_one("a/b", "main", "model.bin", 5, target, "mirror")
        self.assertEqual(target.read_bytes(), b"12345")
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][0], "C:/tools/curl.exe")
        self.assertIn("-C", commands[0])
        self.assertIn("hf-mirror.com", commands[0][-1])
        self.assertIn("huggingface.co", commands[1][-1])

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
        self.assertTrue(any("https://download.pytorch.org/whl/cu128" in c for c in commands))
        self.assertIn("torch==2.7.1+cu128", " ".join(commands[0]))

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
        self.assertEqual(generated[0]["top_k"], 50)

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
        self.assertNotIn("mlx", commands[0][2])

    def test_locate_ffmpeg_uses_managed_qwen_imageio_binary(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_worker_ffmpeg", WORKER)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        site = Path(self.tmp.name) / "site"
        binary = site / "imageio_ffmpeg/binaries/ffmpeg-win64-v7.exe"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"exe")
        with patch.object(worker, "QWEN_SITE", site), patch.object(worker.shutil, "which", return_value=None):
            self.assertEqual(worker.locate_ffmpeg(), binary)

    def test_bridge_respects_explicit_ffmpeg_executable(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("win_qwen_ffmpeg", ROOT / "worker/qwen_bridge.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        with patch.dict(os.environ, {"SPP_FFMPEG": "C:/tools/imageio/ffmpeg-win64.exe"}), \
             patch.object(bridge.shutil, "which", return_value=None):
            self.assertEqual(bridge.find_ffmpeg(), "C:/tools/imageio/ffmpeg-win64.exe")


if __name__ == "__main__":
    unittest.main()
