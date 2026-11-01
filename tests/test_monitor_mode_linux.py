"""Linux switching regressions using simulated hardware and a temporary profile."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "monitor-mode-linux"
loader = importlib.machinery.SourceFileLoader("monitor_mode_linux_switching", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
linux = importlib.util.module_from_spec(spec)
loader.exec_module(linux)


class LinuxSwitchingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "monitors.lua"
        self.config.write_text("local samsung_pbp = true\n")
        self.pbp = True
        self.source = 0x11
        self.secondary = 0x01
        self.size = (1720, 1440)
        self.fail_resolution = False
        self.observations = []
        self.output = io.StringIO()
        self.patch(linux, "CONFIG", self.config)
        self.patch(linux.Path, "home", return_value=self.root)
        self.patch(linux.sys, "platform", "linux")
        self.patch(linux.shutil, "which", return_value="/mock/tool")
        self.patch(linux.time, "sleep")
        self.patch(linux, "run", side_effect=self.run_tool)
        self.patch(linux, "monitor_mode", side_effect=lambda: self.pbp)
        self.patch(linux, "feature", side_effect=self.feature)
        self.patch(linux, "set_monitor_mode", side_effect=self.set_mode)
        self.patch(linux, "set_input", side_effect=self.set_input)
        self.patch(linux, "set_secondary_pane", side_effect=self.set_secondary)
        self.patch(linux, "desktop", side_effect=self.desktop)
        self.patch(linux, "report")
        self.restore = self.patch(linux, "restore", wraps=linux.restore)

    def patch(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def run_tool(self, command):
        if command == linux.HYPR + ["configerrors"]:
            return ""
        if command == linux.HYPR + ["reload"]:
            # Applying full-screen timings during PBP leaves a fallback mode.
            # The mode must be applied again after the hardware switch.
            self.size = (1720, 1440) if self.pbp or linux.saved_mode() or self.fail_resolution else (3440, 1440)
            return "ok"
        raise AssertionError(f"Unexpected external command: {command}")

    def feature(self, code):
        if code == "60":
            return self.source
        if code == "E3":
            return self.secondary
        raise AssertionError(f"Unexpected control: {code}")

    def set_mode(self, pbp, verify=True):
        self.pbp = pbp

    def set_input(self, source, fullscreen=False):
        self.source = source

    def set_secondary(self, source):
        self.secondary = source

    def desktop(self):
        self.observations.append((self.pbp, self.source, self.size))
        if not self.pbp and self.source != linux.LOCAL_INPUT:
            raise RuntimeError("Local HDMI output is hidden")
        return {"width": self.size[0], "height": self.size[1]}

    def switch(self, mode):
        argv = ["monitor-mode", "--serial", "TEST_SERIAL", "--output", "TEST_OUTPUT", mode]
        with patch.object(sys, "argv", argv), redirect_stdout(self.output), redirect_stderr(self.output):
            linux.main()

    def test_hdmi2_checks_full_width_after_hardware_switch_and_reapply(self):
        self.switch("hdmi2")
        self.assertEqual(self.observations, [(False, 0x12, (3440, 1440))])
        self.assertFalse(linux.saved_mode())
        self.restore.assert_not_called()

    def test_full_and_toggle_use_local_hdmi2(self):
        for mode in ("full", "toggle"):
            with self.subTest(mode=mode):
                self.pbp = True
                self.observations.clear()
                self.switch(mode)
                self.assertEqual(self.observations, [(False, 0x12, (3440, 1440))])
        self.restore.assert_not_called()

    def test_hdmi1_handoff_allows_hidden_linux_output(self):
        self.switch("hdmi1")
        self.assertFalse(self.pbp)
        self.assertEqual(self.source, 0x11)
        self.assertFalse(linux.saved_mode())
        self.assertEqual(self.observations, [])
        self.restore.assert_not_called()

    def test_pbp_and_toggle_from_full_verify_half_width(self):
        for mode in ("pbp", "toggle"):
            with self.subTest(mode=mode):
                self.pbp = False
                self.source = 0x12
                self.size = (3440, 1440)
                self.observations.clear()
                linux.save_mode(False)
                self.switch(mode)
                self.assertEqual(self.observations, [(True, 0x11, (1720, 1440))])
                self.assertTrue(linux.saved_mode())
        self.restore.assert_not_called()

    def test_resolution_failure_restores_hardware_and_profile(self):
        self.fail_resolution = True
        with self.assertRaisesRegex(RuntimeError, "Desktop did not reach 3440 x 1440"):
            self.switch("hdmi2")
        self.restore.assert_called_once_with(True, 0x11, 0x01, True)
        self.assertTrue(self.pbp)
        self.assertEqual(self.source, 0x11)
        self.assertTrue(linux.saved_mode())


if __name__ == "__main__":
    unittest.main()
