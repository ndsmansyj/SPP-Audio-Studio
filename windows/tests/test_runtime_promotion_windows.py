"""Exercise runtime directory promotion under real Windows sharing locks."""

import ctypes
from ctypes import wintypes
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest


@unittest.skipUnless(sys.platform == "win32", "requires real Win32 file sharing semantics")
class RuntimePromotionWindowsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="SPP 运行环境 验收 ")
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name) / "Runtime"
        self.staged = self.runtime / ".spp-install-staging" / "runtime-asr-候选 新"
        self.final = self.runtime / "asr"
        self.staged.mkdir(parents=True)
        self.final.mkdir(parents=True)
        (self.final / "old-only.txt").write_text("previous", encoding="utf-8")
        (self.final / "shared.txt").write_text("old", encoding="utf-8")
        (self.staged / "new-only.txt").write_text("replacement", encoding="utf-8")
        (self.staged / "shared.txt").write_text("new", encoding="utf-8")

        worker_path = Path(__file__).resolve().parents[1] / "worker" / "spp_worker.py"
        spec = importlib.util.spec_from_file_location("worker_windows_promotion", worker_path)
        self.worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.worker)

        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    @staticmethod
    def _directory_identity(path):
        info = path.stat()
        return info.st_dev, info.st_ino

    def _assert_promotion_after_temporary_lock(self, locked_path):
        staged_identity = self._directory_identity(self.staged)
        previous_identity = self._directory_identity(self.final)
        self.assertNotEqual(staged_identity, previous_identity)
        self.assertNotEqual(staged_identity[1], 0)

        # Share reads and writes but withhold FILE_SHARE_DELETE. Opening a
        # directory requires FILE_FLAG_BACKUP_SEMANTICS even without elevation.
        handle = self.kernel32.CreateFileW(
            str(locked_path),
            0x80000000,  # GENERIC_READ; metadata-only access does not block rename
            0x0001 | 0x0002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())

        closed = threading.Event()
        cancel_release = threading.Event()
        close_guard = threading.Lock()
        release_errors = []
        release_thread = None
        released_before_return = False
        probe = locked_path.with_name(locked_path.name + "-rename-probe")

        def close_handle():
            with close_guard:
                if not closed.is_set():
                    if not self.kernel32.CloseHandle(handle):
                        raise ctypes.WinError(ctypes.get_last_error())
                    closed.set()

        def release_handle_later():
            # Early cancellation only accelerates cleanup if promotion fails.
            cancel_release.wait(0.35)
            try:
                close_handle()
            except BaseException as error:
                release_errors.append(error)

        try:
            # Prove this handle actually prevents a directory rename on this
            # filesystem before exercising the unmodified production function.
            with self.assertRaises(OSError) as denied:
                os.replace(locked_path, probe)
            self.assertIn(denied.exception.winerror, (5, 32, 33))
            self.assertTrue(locked_path.is_dir())
            self.assertFalse(probe.exists())

            release_thread = threading.Thread(
                target=release_handle_later,
                name="release-runtime-directory-handle",
            )
            release_thread.start()
            self.worker._promote_runtime(self.staged, self.final)
            released_before_return = closed.is_set()
        finally:
            cancel_release.set()
            if release_thread is not None:
                release_thread.join(timeout=5)
            close_handle()
            if release_thread is not None:
                self.assertFalse(release_thread.is_alive(), "handle-release thread did not finish")

        self.assertFalse(release_errors, f"CloseHandle failed: {release_errors!r}")
        self.assertTrue(released_before_return, "promotion returned before the real lock was released")
        # File identity proves the staged directory itself was renamed into
        # place, rather than copied over the previous runtime directory.
        self.assertEqual(self._directory_identity(self.final), staged_identity)
        self.assertNotEqual(self._directory_identity(self.final), previous_identity)
        self.assertEqual((self.final / "shared.txt").read_text(encoding="utf-8"), "new")
        self.assertEqual((self.final / "new-only.txt").read_text(encoding="utf-8"), "replacement")
        self.assertFalse((self.final / "old-only.txt").exists())
        self.assertFalse(self.staged.exists())
        self.assertFalse(probe.exists())
        self.assertEqual(list(self.runtime.glob(".asr-runtime-previous-*")), [])

    def test_staged_directory_lock_is_retried_until_released(self):
        self._assert_promotion_after_temporary_lock(self.staged)

    def test_previous_runtime_directory_lock_is_retried_until_released(self):
        self._assert_promotion_after_temporary_lock(self.final)


if __name__ == "__main__":
    unittest.main()
