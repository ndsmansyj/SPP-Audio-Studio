from __future__ import annotations

import json
import pathlib
import re
import subprocess
import unittest

WINDOWS = pathlib.Path(__file__).resolve().parents[1]


class WindowsPackagingTests(unittest.TestCase):
    def test_required_files_exist(self) -> None:
        required = [
            "README.md",
            "bootstrap.ps1",
            "build.ps1",
            "publish.ps1",
            "launch.ps1",
            "common.ps1",
            "bootstrap.sh",
            "build.sh",
            "publish.sh",
            "launch.sh",
            "global.json",
            "toolchain.json",
            "requirements-build.txt",
            "ci/windows.yml",
        ]
        missing = [name for name in required if not (WINDOWS / name).is_file()]
        self.assertEqual([], missing)

    def test_powershell_files_parse(self) -> None:
        powershell = "powershell.exe"
        for script in WINDOWS.glob("*.ps1"):
            command = (
                "$e=$null; $t=$null; "
                f"[void][System.Management.Automation.Language.Parser]::ParseFile('{script}',[ref]$t,[ref]$e); "
                "if($e.Count){$e | ForEach-Object { Write-Error $_ }; exit 1}"
            )
            result = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, f"{script.name}: {result.stderr}")

    def test_shell_wrappers_are_strict_and_use_cygpath(self) -> None:
        for script in WINDOWS.glob("*.sh"):
            text = script.read_text(encoding="utf-8")
            self.assertIn("set -euo pipefail", text, script.name)
            self.assertIn("cygpath -w", text, script.name)
            self.assertIn("powershell.exe", text, script.name)

    def test_toolchain_manifest_is_pinned(self) -> None:
        manifest = json.loads((WINDOWS / "toolchain.json").read_text(encoding="utf-8"))
        self.assertRegex(manifest["dotnetSdk"], r"^8\.0\.\d+$")
        self.assertRegex(manifest["python"], r"^3\.11\.\d+$")
        for package in manifest["winget"]:
            self.assertTrue(package["id"])
            self.assertRegex(package["minimumVersion"], r"^\d+(\.\d+)+$")

    def test_scripts_do_not_embed_checkout_path(self) -> None:
        forbidden = ["SONGPANPAN", "spp-win-packaging", "D:\\AI\\Hermes"]
        generated_directories = {"bin", "obj", ".venv", "artifacts", "__pycache__"}
        for path in WINDOWS.rglob("*"):
            relative = path.relative_to(WINDOWS)
            if any(part in generated_directories for part in relative.parts):
                continue
            if path.is_file() and path.suffix.lower() in {".ps1", ".sh", ".json", ".yml", ".md", ".txt"}:
                text = path.read_text(encoding="utf-8")
                for value in forbidden:
                    self.assertNotIn(value, text, str(relative))

    def test_publish_generates_checksums_and_manifest(self) -> None:
        text = (WINDOWS / "publish.ps1").read_text(encoding="utf-8")
        self.assertIn("SHA256SUMS", text)
        self.assertIn("release-manifest.json", text)
        self.assertRegex(text, re.compile(r"Get-FileHash.+SHA256", re.DOTALL))

    def test_publish_writes_portable_lf_checksum_files(self) -> None:
        text = (WINDOWS / "publish.ps1").read_text(encoding="utf-8")
        self.assertRegex(text, r"WriteAllText\(\$checksumPath")
        self.assertRegex(text, r"WriteAllText\(\"\$archive\.sha256\"")

    def test_build_discovers_the_actual_windows_test_tree(self) -> None:
        text = (WINDOWS / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("Join-Path $PSScriptRoot 'tests'", text)
        self.assertRegex(text, r"unittest'\s+'discover'\s+'-s'\s+\$testsRoot")

    def test_python_command_is_always_wrapped_as_an_array(self) -> None:
        for name in ("common.ps1", "bootstrap.ps1"):
            text = (WINDOWS / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"\$pythonCommand\s*=\s*Get-PythonCommand", name)

    def test_worker_bundle_includes_pip_for_frozen_runtime_install(self) -> None:
        text = (WINDOWS / "publish.ps1").read_text(encoding="utf-8")
        self.assertRegex(text, r"--collect-all['\"],?\s*['\"]pip")
        self.assertRegex(text, r"--hidden-import['\"],?\s*['\"]qwen_bridge")
        self.assertRegex(text, r"--hidden-import['\"],?\s*['\"]mel_bridge")

    def test_winui_builds_with_visual_studio_msbuild_and_pri_enabled(self) -> None:
        common = (WINDOWS / "common.ps1").read_text(encoding="utf-8")
        build = (WINDOWS / "build.ps1").read_text(encoding="utf-8")
        publish = (WINDOWS / "publish.ps1").read_text(encoding="utf-8")
        project = (WINDOWS / "src/SPPAudioStudio.Windows/SPPAudioStudio.Windows.csproj").read_text(encoding="utf-8")
        self.assertIn("function Get-VsMsBuildPath", common)
        self.assertRegex(build, r"Invoke-Native\s+\$msbuild")
        self.assertRegex(publish, r"Invoke-Native\s+\$msbuild")
        self.assertIn("Microsoft.VisualStudio.Workload.UniversalBuildTools", (WINDOWS / "bootstrap.ps1").read_text(encoding="utf-8"))
        self.assertIn("-p:WindowsAppSDKSelfContained=true", publish)
        for disabled_property in ("EnableCoreMrtTooling", "AppxGeneratePriEnabled", "IncludeProjectPriFile"):
            self.assertNotIn(disabled_property, project)

    def test_powershell_arguments_are_precomposed(self) -> None:
        for path in WINDOWS.glob("*.ps1"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"'[^'\r\n]*='\s*\+\s*\$", path.name)
        launch = (WINDOWS / "launch.ps1").read_text(encoding="utf-8")
        self.assertIn("Start-OptionalArgumentProcess", launch)


if __name__ == "__main__":
    unittest.main()
