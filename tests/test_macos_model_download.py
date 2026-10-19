"""Regression tests for Qwen model download recovery without multi-GB traffic."""
import hashlib
import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKER_PATH = Path(__file__).resolve().parents[1] / "worker" / "spp_worker.py"
spec = importlib.util.spec_from_file_location("spp_worker_macos_test", WORKER_PATH)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

GOOD = b"safetensors-fixture-good"
SHA = hashlib.sha256(GOOD).hexdigest()


class DownloadRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "model.safetensors"
        self.source = "https://modelscope.cn"
        self.part = self.target.with_suffix(".safetensors.part.https-modelscope-cn")

    def download(self, source_calls):
        calls = []

        def mock_run(cmd):
            self.assertEqual(cmd[0], "/usr/bin/curl")
            resumed = "-C" in cmd
            filename = Path(cmd[cmd.index("-o") + 1])
            result, payload = source_calls.pop(0)
            calls.append((resumed, cmd[-1], filename))
            with open(filename, "ab" if resumed else "wb") as f:
                f.write(payload)
            return SimpleNamespace(returncode=result)

        with patch.object(worker.subprocess, "run", side_effect=mock_run), redirect_stdout(io.StringIO()):
            worker._download_one("test/repo", "pinned", "model.safetensors", len(GOOD),
                                 SHA, self.target, "auto", endpoint_order=[self.source])
        return calls

    def test_oversize_partial_resets_before_curl(self):
        self.part.write_bytes(GOOD + b"x" * 10)
        calls = self.download([(0, GOOD)])
        self.assertEqual([c[0] for c in calls], [False])
        self.assertEqual(self.target.read_bytes(), GOOD)
        self.assertFalse(self.part.exists())

    def test_complete_partial_with_valid_hash_promoted_without_network(self):
        self.part.write_bytes(GOOD)
        calls = self.download([])
        self.assertEqual(calls, [])
        self.assertEqual(self.target.read_bytes(), GOOD)

    def test_complete_partial_with_invalid_hash_restarts(self):
        self.part.write_bytes(b"x" * len(GOOD))
        calls = self.download([(0, GOOD)])
        self.assertEqual([c[0] for c in calls], [False])
        self.assertEqual(self.target.read_bytes(), GOOD)

    def test_resume_hash_mismatch_retries_from_zero(self):
        self.part.write_bytes(b"BAD")
        calls = self.download([(0, b"x" * (len(GOOD) - 3)), (0, GOOD)])
        self.assertEqual([c[0] for c in calls], [True, False])
        self.assertEqual(self.target.read_bytes(), GOOD)

    def test_valid_target_skips_network(self):
        self.target.write_bytes(GOOD)
        calls = self.download([])
        self.assertEqual(calls, [])
        self.assertEqual(self.target.read_bytes(), GOOD)

    def test_invalid_new_result_uses_alternative_source(self):
        calls = []
        def fake_run(cmd):
            endpoint = cmd[-1]
            part = Path(cmd[cmd.index("-o") + 1])
            calls.append(endpoint)
            part.write_bytes(GOOD + b"bad" if "modelscope" in endpoint else GOOD)
            return SimpleNamespace(returncode=0)
        with patch.object(worker.subprocess, "run", side_effect=fake_run), redirect_stdout(io.StringIO()):
            worker._download_one("test/repo", "pinned", "model.safetensors", len(GOOD),
                                 SHA, self.target, "auto",
                                 endpoint_order=[worker.MODELSCOPE_ENDPOINT, worker.HF_OFFICIAL])
        self.assertEqual(len(calls), 2)
        self.assertIn("huggingface.co", calls[-1])
        self.assertEqual(self.target.read_bytes(), GOOD)

    def test_network_failure_keeps_smaller_part(self):
        def fake_run(cmd):
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"PART")
            return SimpleNamespace(returncode=28)
        with patch.object(worker.subprocess, "run", side_effect=fake_run), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "curl=28"):
                worker._download_one("test/repo", "pinned", "model.safetensors", len(GOOD),
                                     SHA, self.target, "auto", endpoint_order=[self.source])
        self.assertEqual(self.part.read_bytes(), b"PART")

    def test_bad_full_target_is_not_deleted_until_valid_replacement(self):
        self.target.write_bytes(b"x" * len(GOOD))
        self.download([(0, GOOD)])
        self.assertEqual(self.target.read_bytes(), GOOD)

    def test_qwen_sources_allow_explicit_official_and_mirror(self):
        self.assertEqual(worker._model_endpoint_order("qwen", "official")[0], worker.HF_OFFICIAL)
        self.assertEqual(worker._model_endpoint_order("qwen", "mirror")[0], worker.HF_MIRROR)
        self.assertEqual(worker._model_endpoint_order("qwen", "auto")[0], worker.MODELSCOPE_ENDPOINT)
        self.assertEqual(len(worker._model_endpoint_order("qwen", "auto")), 3)


if __name__ == "__main__":
    unittest.main()
