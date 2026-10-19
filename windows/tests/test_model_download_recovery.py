"""Windows worker download/recovery and runtime disk-path regression checks (no models)."""
import hashlib
import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKER = Path(__file__).resolve().parents[1] / "worker" / "spp_worker.py"
spec = importlib.util.spec_from_file_location("spp_windows_recovery_test", WORKER)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

GOOD = b"spp-unit-test-qwen-bytes"
SHA = hashlib.sha256(GOOD).hexdigest()


class DownloadRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "model.safetensors"
        self.ms = worker.MODELSCOPE_ENDPOINT
        self.hf = worker.HF_OFFICIAL
        self.part_ms = self.target.with_suffix(".safetensors.part.https-modelscope-cn")

    def run_with_outputs(self, outputs, endpoint_order=None):
        calls = []
        def fake_popen(cmd):
            part = Path(cmd[cmd.index("-o") + 1])
            resumed = "-C" in cmd
            calls.append((resumed, cmd[-1], part))
            rc, data = outputs.pop(0)
            with part.open("ab" if resumed else "wb") as f:
                f.write(data)
            return SimpleNamespace(returncode=rc, poll=lambda: rc)

        with patch.object(worker.shutil, "which", return_value="curl"), \
             patch.object(worker.subprocess, "Popen", side_effect=fake_popen), \
             patch.object(worker.time, "sleep", return_value=None), \
             redirect_stdout(io.StringIO()):
            result = worker._download_one(
                "demo/repo", "revision", "model.safetensors",
                {"size": len(GOOD), "sha256": SHA}, self.target, "auto",
                model="qwen", completed_before=0, model_total=len(GOOD),
                endpoint_order=endpoint_order or [self.ms])
        self.assertEqual(result, len(GOOD))
        self.assertEqual(self.target.read_bytes(), GOOD)
        return calls

    def test_oversized_part_is_reset_not_resumed(self):
        self.part_ms.write_bytes(GOOD + b"bad")
        calls = self.run_with_outputs([(0, GOOD)])
        self.assertEqual([c[0] for c in calls], [False])

    def test_valid_completed_part_is_recovered_without_network(self):
        self.part_ms.write_bytes(GOOD)
        calls = self.run_with_outputs([])
        self.assertEqual(calls, [])
        self.assertFalse(self.part_ms.exists())

    def test_resume_failure_restarts_from_zero(self):
        self.part_ms.write_bytes(b"BAD")
        calls = self.run_with_outputs([(0, b"*" * (len(GOOD)-3)), (0, GOOD)])
        self.assertEqual([c[0] for c in calls], [True, False])

    def test_fallback_sources_have_isolated_parts(self):
        calls = self.run_with_outputs([(0, GOOD + b"bad"), (0, GOOD)],
                                      endpoint_order=[self.ms, self.hf])
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0][2], calls[1][2])
        self.assertIn("huggingface.co", calls[1][1])

    def test_good_model_skips_network(self):
        self.target.write_bytes(GOOD)
        calls = self.run_with_outputs([])
        self.assertEqual(calls, [])

    def test_legacy_verified_part_is_recovered(self):
        self.target.with_suffix(".safetensors.part").write_bytes(GOOD)
        calls = self.run_with_outputs([])
        self.assertEqual(calls, [])

    def test_sources_allow_modelscope_fallback(self):
        files = {"model.safetensors": {"size": len(GOOD), "sha256": SHA}}
        for source in ["official", "mirror"]:
            order = worker._resolve_model_endpoint_order(
                source, worker.QWEN_REPO, worker.QWEN_REVISION, files, "qwen")
            self.assertEqual(order[-1], self.ms)
            self.assertEqual(len(order), 3)

    def test_runtime_install_staging_and_cache_follow_runtime_root(self):
        root = Path(self.tmp.name) / "redirected-runtime"
        with patch.object(worker, "RUNTIME_DIR", root):
            staging, cache = worker._runtime_install_storage()
        self.assertEqual(staging.parent, root)
        self.assertEqual(cache.parent, root)
        self.assertTrue(staging.is_dir())
        self.assertTrue(cache.is_dir())

    def test_pip_temp_and_cache_follow_runtime_root(self):
        root = Path(self.tmp.name) / "runtime"
        captured = []
        def fake_capture(_cmd, env=None):
            captured.append(env)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        def fake_verify(_kind, _site):
            return SimpleNamespace(returncode=0, stdout="ASR_RUNTIME_OK", stderr="")
        with patch.object(worker, "RUNTIME_DIR", root), \
             patch.object(worker, "ASR_RUNTIME_DIR", root / "asr"), \
             patch.object(worker, "PYTHON_CORE", Path(sys.executable)), \
             patch.object(worker, "_runtime_installed", return_value=False), \
             patch.object(worker, "_resolve_pypi_endpoint_order", return_value=[worker.PYPI_OFFICIAL]), \
             patch.object(worker, "_verify_python_import", side_effect=fake_verify), \
             patch.object(worker, "run_capture", side_effect=fake_capture), \
             patch.object(worker, "ensure_dirs"), redirect_stdout(io.StringIO()):
            result = worker.cmd_runtime_install(SimpleNamespace(runtime="asr", source="official"))
        self.assertEqual(result, 0)
        self.assertEqual(len(captured), 1)
        env = captured[0]
        for key in ("PIP_CACHE_DIR", "TMP", "TEMP", "TMPDIR"):
            self.assertTrue(Path(env[key]).is_relative_to(root), (key, env[key]))


if __name__ == "__main__":
    unittest.main()
