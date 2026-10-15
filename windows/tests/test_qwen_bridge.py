"""Model-free unit coverage for managed Windows Whisper CUDA DLL bootstrap."""
import importlib.util
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

BRIDGE_PATH = Path(__file__).resolve().parents[1] / "worker" / "qwen_bridge.py"
spec = importlib.util.spec_from_file_location("spp_win_qwen_bridge_cuda_test", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class WhisperCudaEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Runtime"
        self.site = self.root / "asr" / "site-packages"
        self.site.mkdir(parents=True)
        self.patch_site = patch.object(bridge, "ASR_SITE", str(self.site))
        self.patch_site.start()
        self.addCleanup(self.patch_site.stop)
        self.patch_dll_env = patch.dict(os.environ, {"SPP_ASR_CUDA_DLL_DIR": ""})
        self.patch_dll_env.start()
        self.addCleanup(self.patch_dll_env.stop)

    def create_cuda_pair(self, subdir):
        lib = self.root / subdir / "site-packages" / "torch" / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        for dll in ("cublas64_12.dll", "cublasLt64_12.dll"):
            (lib / dll).write_bytes(b"test dummy dll")
        return lib

    def test_qwen_cublas_preferred_and_asr_path_exported(self):
        qwen = self.create_cuda_pair("qwen")
        self.create_cuda_pair("mel")
        self.assertEqual(bridge.find_asr_cuda_dll_directory(), qwen)
        with patch.dict(os.environ, {"PATH": "original_path", "PYTHONPATH": "old_qwen_site"}):
            env = bridge.build_asr_environment()
        self.assertEqual(env["SPP_ASR_CUDA_DLL_DIR"], str(qwen))
        self.assertEqual(env["PYTHONPATH"], str(self.site))
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(env["PATH"], str(qwen) + os.pathsep + "original_path")

    def test_mel_fallback_without_qwen_cuda(self):
        mel = self.create_cuda_pair("mel")
        self.assertEqual(bridge.find_asr_cuda_dll_directory(), mel)

    def test_incomplete_dll_directory_does_not_replace_existing_path(self):
        lib = self.root / "qwen" / "site-packages" / "torch" / "lib"
        lib.mkdir(parents=True)
        (lib / "cublas64_12.dll").write_bytes(b"test")
        self.assertIsNone(bridge.find_asr_cuda_dll_directory())
        with patch.dict(os.environ, {"PATH": "original_path"}):
            self.assertEqual(bridge.build_asr_environment()["PATH"], "original_path")

    def test_explicit_override_directory_must_have_both_libraries(self):
        default = self.create_cuda_pair("qwen")
        custom = self.root / "custom"
        custom.mkdir()
        with patch.dict(os.environ, {"SPP_ASR_CUDA_DLL_DIR": str(custom)}):
            self.assertEqual(bridge.find_asr_cuda_dll_directory(), default)
        for dll in ("cublas64_12.dll", "cublasLt64_12.dll"):
            (custom / dll).write_bytes(b"test")
        with patch.dict(os.environ, {"SPP_ASR_CUDA_DLL_DIR": str(custom)}):
            self.assertEqual(bridge.find_asr_cuda_dll_directory(), custom)

    def test_asr_child_preloads_cuda_and_decodes_utf8(self):
        dll_dir = self.create_cuda_pair("qwen")
        python_exe = Path(self.temp.name) / "python.exe"
        python_exe.write_bytes(b"stub")
        model = Path(self.temp.name) / "whisper-model"
        model.mkdir()
        with patch.object(bridge, "ASR_PY", python_exe), patch.object(bridge, "ASR_MODEL", model):
            with patch.object(bridge.subprocess, "run", return_value=types.SimpleNamespace(returncode=0, stdout="你好", stderr="SoX could not be found!")) as call:
                self.assertEqual(bridge.transcribe(Path(self.temp.name) / "reference.wav"), "你好")
        command = call.call_args.args[0]
        self.assertEqual(command[1], "-c")
        inline = command[2]
        self.assertLess(inline.index("ctypes.WinDLL"), inline.index("from faster_whisper import WhisperModel"))
        self.assertLess(inline.index("os.add_dll_directory"), inline.index("ctypes.WinDLL"))
        self.assertEqual(call.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(call.call_args.kwargs["env"]["SPP_ASR_CUDA_DLL_DIR"], str(dll_dir))

    def test_cublas_failure_has_actionable_manual_reference_text_workaround(self):
        python_exe = Path(self.temp.name) / "python.exe"
        python_exe.write_bytes(b"stub")
        model = Path(self.temp.name) / "whisper-model"
        model.mkdir()
        with patch.object(bridge, "ASR_PY", python_exe), patch.object(bridge, "ASR_MODEL", model):
            with patch.object(bridge.subprocess, "run", return_value=types.SimpleNamespace(returncode=1, stdout="", stderr="RuntimeError: Library cublas64_12.dll is not found or cannot be loaded")):
                with self.assertRaisesRegex(RuntimeError, "填写音频中的实际内容"):
                    bridge.transcribe(Path(self.temp.name) / "reference.wav")


if __name__ == "__main__":
    unittest.main()
