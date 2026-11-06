from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest
import zipfile

MODULE = pathlib.Path(__file__).resolve().parents[1] / "ci" / "verify_portable.py"
spec = importlib.util.spec_from_file_location("verify_portable", MODULE)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class PortableIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.root = self.base / "SPP Audio Studio"
        for name in (*verifier.REQUIRED, "app/SPPAudioStudio.Windows.pri",
                     "worker/default_voice/reference.wav",
                     "python-core/Lib/site-packages/pip-25.2.dist-info/METADATA"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test payload\n")
        self.seal()

    def seal(self):
        entries = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and path.name not in verifier.METADATA:
                entries.append({"path": path.relative_to(self.root).as_posix(), "size": path.stat().st_size,
                                "sha256": verifier.digest(path)})
        manifest = {"schemaVersion": 2, "version": "1.0.3", "commit": "a" * 40,
                    "sourceBuild": True, "portable": True, "runtimeIdentifier": "win-x64", "files": entries}
        (self.root / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        sums = [f"{verifier.digest(p)} *{p.relative_to(self.root).as_posix()}" for p in sorted(self.root.rglob("*"))
                if p.is_file() and p.name != "SHA256SUMS"]
        (self.root / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")

    def zip(self):
        archive = self.base / "portable.zip"
        with zipfile.ZipFile(archive, "w") as target:
            for path in self.root.rglob("*"):
                if path.is_file():
                    target.write(path, path.relative_to(self.base).as_posix())
        return archive

    def test_valid_folder_and_zip_cover_every_file(self):
        result = verifier.verify_root(self.root, "1.0.3", "a" * 40)
        self.assertTrue(result["ok"])
        self.assertEqual(result, verifier.verify_zip(self.zip(), "1.0.3", "a" * 40))

    def test_windows_runtime_filename_case_matches_required_component(self):
        # The Windows App SDK MSIX ships Microsoft.ui.xaml.dll. Windows loads
        # it under the same name as Microsoft.UI.Xaml.dll, while ZIP preserves case.
        original = self.root / "app/Microsoft.UI.Xaml.dll"
        temporary = original.with_name("runtime-case.tmp")
        original.rename(temporary)
        temporary.rename(original.with_name("Microsoft.ui.xaml.dll"))
        self.seal()
        self.assertTrue(verifier.verify_root(self.root)["ok"])
        self.assertTrue(verifier.verify_zip(self.zip())["ok"])

    def test_modified_binary_is_rejected(self):
        (self.root / "app/SPPAudioStudio.Windows.exe").write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError, "size/hash mismatch"):
            verifier.verify_root(self.root)

    def test_unlisted_stale_file_is_rejected(self):
        (self.root / "stale-worker.dll").write_bytes(b"old")
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            verifier.verify_root(self.root)

    def test_missing_icon_is_rejected_even_after_manifest_regeneration(self):
        (self.root / "app/Assets/pixel_icon.ico").unlink()
        self.seal()
        with self.assertRaisesRegex(ValueError, "components missing"):
            verifier.verify_root(self.root)

    def test_zero_byte_executable_is_rejected_even_with_matching_hash(self):
        (self.root / "worker/SPPWorker/SPPWorker.exe").write_bytes(b"")
        self.seal()
        with self.assertRaisesRegex(ValueError, "Empty binary"):
            verifier.verify_root(self.root)

    def test_wrong_version_and_commit_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Version mismatch"):
            verifier.verify_root(self.root, "1.0.2")
        with self.assertRaisesRegex(ValueError, "commit mismatch"):
            verifier.verify_root(self.root, commit="b" * 40)

    def test_manifest_tampering_is_detected_by_checksum_file(self):
        path = self.root / "release-manifest.json"
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "SHA256SUMS"):
            verifier.verify_root(self.root)

    def test_zip_path_traversal_is_rejected_before_extraction(self):
        archive = self.zip()
        with zipfile.ZipFile(archive, "a") as target:
            target.writestr("SPP Audio Studio/../../outside.exe", "bad")
        with self.assertRaisesRegex(ValueError, "Unsafe portable path"):
            verifier.verify_zip(archive)
        self.assertFalse((self.base / "outside.exe").exists())

    def test_archive_sidecar_binds_hash_and_filename(self):
        archive = self.zip()
        sidecar = archive.with_suffix(".zip.sha256")
        sidecar.write_text(f"{verifier.digest(archive)} *{archive.name}\n")
        self.assertTrue(verifier.verify_zip(archive, sha256_file=sidecar)["ok"])
        sidecar.write_text(f"{'0' * 64} *{archive.name}\n")
        with self.assertRaisesRegex(ValueError, "sidecar mismatch"):
            verifier.verify_zip(archive, sha256_file=sidecar)

    def test_zip_case_collisions_are_rejected(self):
        archive = self.zip()
        with zipfile.ZipFile(archive, "a") as target:
            target.writestr("SPP Audio Studio/APP/SPPAudioStudio.Windows.exe", "bad")
        with self.assertRaisesRegex(ValueError, "Duplicate ZIP"):
            verifier.verify_zip(archive)


if __name__ == "__main__":
    unittest.main()
