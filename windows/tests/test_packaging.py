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

    def test_publish_ships_python_core_instead_of_duplicate_worker_runtime(self) -> None:
        publish = (WINDOWS / "publish.ps1").read_text(encoding="utf-8")
        worker = (WINDOWS / "worker/spp_worker.py").read_text(encoding="utf-8")
        qwen = (WINDOWS / "worker/qwen_bridge.py").read_text(encoding="utf-8")
        self.assertIn("Join-Path $publishRoot 'python-core'", publish)
        self.assertIn("'python3.dll'", publish)
        self.assertIn("'python311.dll'", publish)
        self.assertIn("$coreSitePackages", publish)
        self.assertIn("'pip'", publish)
        self.assertNotIn("'--collect-all' 'pip'", publish)
        self.assertNotIn("'--hidden-import' 'qwen_bridge'", publish)
        self.assertNotIn("'stdlib-dlls'", publish)
        self.assertNotIn("_prepend_bundled_stdlib", worker)
        self.assertNotIn("_run_hidden_command", worker)
        self.assertIn('site / "torch" / "lib"', qwen)
        self.assertNotIn('site.rglob("*.dll")', qwen)

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

    def test_vswhere_discovery_survives_missing_programfiles_x86_env(self) -> None:
        common = WINDOWS / "common.ps1"
        command = (
            "Remove-Item 'Env:ProgramFiles(x86)' -ErrorAction SilentlyContinue; "
            f". '{common}'; "
            "if(-not (Get-VsWherePath)){ exit 1 }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_launch_uses_the_clean_root_launcher(self) -> None:
        launch = (WINDOWS / "launch.ps1").read_text(encoding="utf-8")
        self.assertIn("'SPP Audio Studio.exe'", launch)
        self.assertNotIn("Get-ChildItem $appDirectory -Filter '*.exe'", launch)

    def test_publish_keeps_winui_payload_below_app_directory(self) -> None:
        publish = (WINDOWS / "publish.ps1").read_text(encoding="utf-8")
        launcher = (WINDOWS / "src/AppLauncher/launcher.cpp").read_text(encoding="utf-8")
        self.assertIn("Join-Path $publishRoot 'app'", publish)
        self.assertIn("'SPP Audio Studio.exe'", publish)
        self.assertIn("src\\AppLauncher", publish)
        self.assertIn('app\\\\SPPAudioStudio.Windows.exe', launcher)

    def test_msbuild_output_properties_do_not_end_with_backslash(self) -> None:
        build = (WINDOWS / "build.ps1").read_text(encoding="utf-8")
        publish = (WINDOWS / "publish.ps1").read_text(encoding="utf-8")
        self.assertIn('"/p:OutputPath=$output"', build)
        self.assertIn('"/p:PublishDir=$appOutput"', publish)
        self.assertNotIn('"/p:OutputPath=$output\\"', build)
        self.assertNotIn('"/p:PublishDir=$appOutput\\"', publish)

    def test_environment_ui_starts_compact_while_checking(self) -> None:
        source = (WINDOWS / "src/SPPAudioStudio.Windows/MainWindow.xaml.cs").read_text(encoding="utf-8")
        self.assertIn("_environmentInstallNotice.Visibility = Visibility.Collapsed", source)
        self.assertIn("download.Visibility = Visibility.Collapsed", source)
        self.assertIn("install.Visibility = Visibility.Collapsed", source)
        self.assertIn("_workbenchInstallNotice.Visibility = Visibility.Collapsed", source)
        self.assertIn("Height = 145", source)

    def test_windows_clone_page_matches_bundled_mac_defaults(self) -> None:
        source = (WINDOWS / "src/SPPAudioStudio.Windows/MainWindow.xaml.cs").read_text(encoding="utf-8")
        publish = (WINDOWS / "publish.ps1").read_text(encoding="utf-8")
        worker = (WINDOWS / "worker/spp_worker.py").read_text(encoding="utf-8")
        app_xaml = (WINDOWS / "src/SPPAudioStudio.Windows/App.xaml").read_text(encoding="utf-8")
        window_xaml = (WINDOWS / "src/SPPAudioStudio.Windows/MainWindow.xaml").read_text(encoding="utf-8")
        self.assertIn("SizeInt32(1320, 1440)", source)
        self.assertIn("大家好，我是宋盼盼。", source)
        self.assertIn('WorkerCommand("seed-default-voice"', source)
        self.assertIn("assets\\default_voice", publish)
        self.assertIn("workerOutput 'default_voice'", publish)
        self.assertIn('return Path(sys.executable).resolve().parent.parent / "default_voice"', worker)
        self.assertIn('RequestedTheme="Light"', app_xaml)
        self.assertIn('Background="#F5F7FB"', window_xaml)
        self.assertIn('ms-appx:///Assets/pixel_icon.png', source)
        self.assertIn('Width = 72', source)
        self.assertIn('Height = 72', source)
        self.assertIn('ActionButton("复制诊断", CopyDiagnostics)', source)
        self.assertIn('ActionButton("打开模型目录", OpenModelDirectory)', source)
        self.assertIn('new ProgressBar', source)
        self.assertIn('"download_progress"', source)
        self.assertIn('FormatBytes(completed)', source)
        self.assertIn('WorkerCommand("model-storage"', source)
        self.assertIn(r'软件目录\Models（便携）', source)
        self.assertIn(r'SPP Audio Studio\out\Clone', source)
        self.assertIn(r'SPP Audio Studio\out\Music Separation', source)
        self.assertIn('Content = "默认目录"', source)
        self.assertIn('Content = "原音频文件夹"', source)
        self.assertIn('Content = "指定文件夹"', source)
        self.assertIn('Content = "选择…"', source)
        self.assertIn('PYTHONIOENCODING', worker)
        self.assertIn('OUTPUT_ROOT = APP_ROOT / "out"', worker)
        self.assertIn('CLONE_OUTPUT_DIR = OUTPUT_ROOT / "Clone"', worker)
        self.assertIn('SEPARATION_OUTPUT_DIR = OUTPUT_ROOT / "Music Separation"', worker)

    def test_powershell_arguments_are_precomposed(self) -> None:
        for path in WINDOWS.glob("*.ps1"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"'[^'\r\n]*='\s*\+\s*\$", path.name)
        launch = (WINDOWS / "launch.ps1").read_text(encoding="utf-8")
        self.assertIn("Start-OptionalArgumentProcess", launch)


if __name__ == "__main__":
    unittest.main()
