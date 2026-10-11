"""Unified CLI routing and offline tests; no monitor is accessed."""

from contextlib import redirect_stdout, redirect_stderr
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
UUID = "00000000-0000-0000-0000-000000000001"


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / file))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cli = load("monitor_mode_cli", "monitor-mode")
linux = load("monitor_mode_linux", "monitor-mode-linux")


class UnifiedCLITests(unittest.TestCase):
    def dispatch(self, platform, arguments):
        with patch.object(cli.sys, "platform", platform), patch.object(cli.os, "execv") as execute:
            cli.main(arguments)
        executable, argv = execute.call_args.args
        self.assertEqual(executable, sys.executable)
        self.assertEqual(argv[0], sys.executable)
        return argv

    def reject(self, platform, arguments):
        with patch.object(cli.sys, "platform", platform), patch.object(cli.os, "execv") as execute, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                cli.main(arguments)
            self.assertEqual(error.exception.code, 2)
            execute.assert_not_called()

    def run_cli(self, *arguments):
        return subprocess.run([sys.executable, str(ROOT / "monitor-mode"), *arguments], env={"PATH": os.environ.get("PATH", "")}, capture_output=True, text=True, timeout=5)

    def test_platform_detection_and_default_read_only_status(self):
        for platform, backend in [("linux", "monitor-mode-linux"), ("linux2", "monitor-mode-linux"), ("darwin", "monitor-mode-mac")]:
            argv = self.dispatch(platform, [])
            self.assertEqual(Path(argv[1]).name, backend)
            self.assertEqual(argv[2:], ["status"])

    def test_arguments_with_spaces_and_mac_options_are_preserved(self):
        argv = self.dispatch("linux", ["--serial", "TEST_SERIAL", "--output", "TEST_OUTPUT", "--config", "/mock/config with spaces/monitors.lua", "pbp"])
        self.assertEqual(argv[2:], ["pbp", "--serial", "TEST_SERIAL", "--output", "TEST_OUTPUT", "--config", "/mock/config with spaces/monitors.lua"])
        argv = self.dispatch("darwin", ["--display", UUID, "--backend", "/mock/Better Display", "--no-resolution", "--local-input", "hdmi2", "toggle"])
        self.assertIn("/mock/Better Display", argv)
        self.assertIn("--no-resolution", argv)
        self.assertIn("--local-input", argv)
        self.assertEqual(argv[2], "toggle")

    def test_platform_specific_flags_are_validated(self):
        for args in [["--display", UUID], ["--backend", "/mock/tool"], ["--no-resolution"], ["--local-input", "hdmi1"]]:
            self.reject("linux", args)
        for args in [["--output", "TEST_OUTPUT"], ["--config", "/mock/config.lua"]]:
            self.reject("darwin", args)
        self.assertEqual(self.dispatch("linux", ["--local-input", "hdmi2", "full"])[2:], ["full"])

    def test_invalid_flags_and_conflicting_selectors_stop_before_dispatch(self):
        self.reject("darwin", ["--display", UUID, "--serial", "TEST_SERIAL"])
        self.reject("linux", ["--unexpected"])
        self.reject("linux", ["--plat", "linux"])

    def test_platform_override_only_permits_offline_plans(self):
        self.reject("linux", ["--platform", "macos", "pbp"])
        self.reject("darwin", ["--platform", "linux", "full"])
        argv = self.dispatch("linux", ["--platform", "macos", "--display", UUID, "pbp", "--dry-run"])
        self.assertEqual(Path(argv[1]).name, "monitor-mode-mac")
        self.assertIn("--dry-run", argv)
        for mode in ["list", "status", "toggle"]:
            self.reject("linux", [mode, "--dry-run"])

    def test_missing_backend_never_executes(self):
        with patch.object(cli.sys, "platform", "linux"), patch.object(cli.Path, "is_file", return_value=False), patch.object(cli.os, "execv") as execute:
            with self.assertRaisesRegex(RuntimeError, "save all three"):
                cli.main(["full"])
            execute.assert_not_called()

    def test_unsupported_os_rejects_live_commands(self):
        with patch.object(cli.sys, "platform", "win32"), patch.object(cli.os, "execv") as execute:
            with self.assertRaisesRegex(RuntimeError, "Linux and macOS"):
                cli.main(["full"])
            execute.assert_not_called()
        argv = self.dispatch("win32", ["--platform", "macos", "--display", UUID, "full", "--dry-run"])
        self.assertEqual(Path(argv[1]).name, "monitor-mode-mac")

    def test_launcher_runs_both_backends_offline_with_correct_full_input(self):
        result = self.run_cli("--platform", "linux", "--serial", "TEST_SERIAL", "--output", "TEST_OUTPUT", "full", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VCP 0x60 = 0x0012", result.stdout)
        result = self.run_cli("--platform", "macos", "--display", UUID, "full", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VCP 0x60 = 0x0011", result.stdout)

    def test_backend_error_exit_code_is_preserved(self):
        arguments = ["--platform", "macos", "--serial", "YOUR_MONITOR_SERIAL", "full", "--dry-run"] if sys.platform == "darwin" else ["status"]
        result = self.run_cli(*arguments)
        self.assertEqual(result.returncode, 2)

    def test_help_without_dependencies_or_selectors(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Linux options", result.stdout)
        self.assertIn("macOS options", result.stdout)


class LinuxBackendTests(unittest.TestCase):
    def test_dry_run_calls_no_tools_and_touches_no_config_or_locks(self):
        argv = ["monitor-mode", "--serial", "TEST_SERIAL", "--output", "TEST_OUTPUT", "pbp", "--dry-run"]
        output = io.StringIO()
        with patch.object(sys, "argv", argv), patch.object(linux, "run", side_effect=AssertionError("No tools allowed")), patch.object(Path, "read_text", side_effect=AssertionError("No config reads allowed")), patch.object(Path, "mkdir", side_effect=AssertionError("No locks allowed")), redirect_stdout(output):
            linux.main()
        self.assertIn("1720x1440", output.getvalue())

    def test_list_requires_no_selectors_or_config_and_only_reads(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", ["monitor-mode", "list"]), patch.object(sys, "platform", "linux"), patch.object(linux.shutil, "which", return_value="/mock/tool"), patch.object(linux, "run", return_value="offline fixture") as run, patch.object(Path, "read_text", side_effect=AssertionError("No config reads allowed")), redirect_stdout(io.StringIO()):
            linux.main()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], ["ddcutil", "detect"])
        self.assertEqual(run.call_args_list[1].args[0][-1], "monitors")


if __name__ == "__main__":
    unittest.main()
