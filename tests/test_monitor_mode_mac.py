"""Offline protocol and recovery tests; no physical monitor is accessed."""

from contextlib import redirect_stdout, redirect_stderr
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "monitor-mode-mac"
loader = importlib.machinery.SourceFileLoader("monitor_mode_mac", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
mac = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = mac
loader.exec_module(mac)
UUID = "00000000-0000-0000-0000-000000000001"


class SimulatedMonitor(mac.Monitor):
    def __init__(self, pbp=False, local=0x11, resolution=True):
        super().__init__("/mock/BetterDisplay", [f"--UUID={UUID}"], local, resolution)
        self.registers = {0xE2: 0x3101 if pbp else 0x3100, 0x60: 0x11, 0xE3: 1}
        self.commands = []
        self.writes = []
        self.size = "1720x1440" if pbp else "3440x1440"
        self.desktop_mode = 77
        self.fail_resolution = False
        self.handoff_error = False

    def run(self, operation, *parameters):
        self.commands.append((operation, parameters))
        if "--ddc" in parameters:
            code = int(next(p.split("=", 1)[1] for p in parameters if p.startswith("--vcp=")), 16)
            if operation == "get":
                return str(self.registers[code])
            value = int(next(p.split("=", 1)[1] for p in parameters if p.startswith("--value=")), 16)
            self.writes.append((code, value))
            if code == 0xE2:
                self.registers[code] = 0x3100 | value
            elif code == 0xE3:
                self.registers[code] = (self.registers[code] & 0xff00) | (value & 0xff)
            else:
                self.registers[code] = value
                if self.registers[0xE2] == 0x3101:
                    index = {0x11: 0x00, 0x12: 0x01, 0x0f: 0x10}[value]
                    self.registers[0xE3] = (index << 8) | (self.registers[0xE3] & 0xff)
                if self.handoff_error and value != self.local_input and self.registers[0xE2] == 0x3100:
                    raise mac.DDCError("Connection disappeared after write")
            return ""
        if operation == "get":
            if parameters == ("--displayModeNumber",):
                return str(self.desktop_mode)
            if parameters == ("--resolution",):
                return self.size
        for parameter in parameters:
            if parameter.startswith("--resolution="):
                if self.fail_resolution:
                    raise mac.DDCError("Requested mode unavailable")
                self.size = parameter.split("=", 1)[1]
                self.desktop_mode = 88
            if parameter.startswith("--displayModeNumber="):
                self.desktop_mode = int(parameter.split("=", 1)[1])
        return ""


class MacMonitorTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(mac.time, "sleep", return_value=None)
        self.sleep.start()
        self.output = io.StringIO()
        self.stdout = redirect_stdout(self.output)
        self.stderr = redirect_stderr(self.output)
        self.stdout.__enter__()
        self.stderr.__enter__()

    def tearDown(self):
        self.stderr.__exit__(None, None, None)
        self.stdout.__exit__(None, None, None)
        self.sleep.stop()

    def test_only_unambiguous_16_bit_values_are_accepted(self):
        for text, expected in [("12545", 0x3101), ("0x3100", 0x3100), ("17.0", 17), (" 257\n", 257)]:
            self.assertEqual(mac.integer(text), expected)
        for text in ["", "true", "17\n18", "value=17", "-1", "65536", "17.5", "Failed 17"]:
            with self.assertRaises(RuntimeError):
                mac.integer(text)

    def test_backend_arguments_and_16_bit_vendor_write(self):
        monitor = mac.Monitor("/mock/BetterDisplay", [f"--UUID={UUID}"], 0x11)
        with patch.object(mac.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "12545\n", "")) as run:
            self.assertEqual(monitor.read(0xE2), 0x3101)
            self.assertEqual(run.call_args.args[0], ["/mock/BetterDisplay", "get", f"--UUID={UUID}", "--ddc", "--vcp=0xE2", "--value"])
            monitor.write(0xE3, 0x0101)
            self.assertEqual(run.call_args.args[0][-1], "--value=0x0101")
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_backend_read_retries_transient_errors(self):
        monitor = mac.Monitor("/mock/BetterDisplay", [f"--UUID={UUID}"], 0x11)
        with patch.object(mac.subprocess, "run", side_effect=[subprocess.TimeoutExpired([], 20), subprocess.CompletedProcess([], 1, "", "private diagnostic"), subprocess.CompletedProcess([], 0, "17", "")]) as run:
            self.assertEqual(monitor.read(0x60), 17)
            self.assertEqual(run.call_count, 3)

    def test_full_to_pbp(self):
        monitor = SimulatedMonitor()
        monitor.switch("pbp")
        self.assertEqual(monitor.registers[0xE2], 0x3101)
        self.assertEqual(monitor.registers[0xE3], 1)
        self.assertEqual(monitor.size, "1720x1440")

    def test_pbp_to_mac_full_forces_input_after_disable(self):
        monitor = SimulatedMonitor(pbp=True)
        monitor.switch("full")
        self.assertEqual(monitor.writes[-2:], [(0xE2, 0), (0x60, 0x11)])
        self.assertEqual(monitor.size, "3440x1440")

    def test_pbp_pair_is_restored(self):
        monitor = SimulatedMonitor(pbp=True)
        monitor.registers.update({0x60: 0x12, 0xE3: 0x0100})
        monitor.switch("pbp")
        self.assertEqual(monitor.writes, [(0x60, 0x11), (0xE3, 0x0101)])
        self.assertEqual(monitor.registers[0xE3], 0x0001)

    def test_handoff_has_no_reads_after_final_write(self):
        monitor = SimulatedMonitor(pbp=True)
        monitor.switch("hdmi2")
        self.assertEqual(monitor.writes[-2:], [(0xE2, 0), (0x60, 0x12)])
        self.assertEqual(monitor.commands[-1][0], "set")
        self.assertIn("command sent", self.output.getvalue())
        self.assertFalse(any(any(p.startswith("--resolution=") for p in params) for _, params in monitor.commands))

    def test_unknown_handoff_does_not_roll_back(self):
        monitor = SimulatedMonitor(pbp=True)
        monitor.handoff_error = True
        with self.assertRaises(mac.HandoffUncertain):
            monitor.switch("hdmi2")
        self.assertEqual(monitor.writes[-2:], [(0xE2, 0), (0x60, 0x12)])
        self.assertEqual(monitor.registers[0x60], 0x12)
        self.assertNotIn("attempting to restore", self.output.getvalue())

    def test_resolution_failure_restores_full_mode_and_desktop(self):
        monitor = SimulatedMonitor()
        monitor.fail_resolution = True
        with self.assertRaises(mac.DDCError):
            monitor.switch("pbp")
        self.assertEqual(monitor.registers[0xE2], 0x3100)
        self.assertEqual(monitor.registers[0x60], 0x11)
        self.assertEqual(monitor.desktop_mode, 77)
        self.assertIn("attempting to restore", self.output.getvalue())

    def test_invalid_initial_state_sends_no_writes(self):
        monitor = SimulatedMonitor()
        monitor.registers[0xE2] = 1
        with self.assertRaises(RuntimeError):
            monitor.switch("pbp")
        self.assertEqual(monitor.writes, [])
        with patch.object(monitor, "pbp", side_effect=mac.DDCError("No readback")):
            with self.assertRaises(mac.DDCError):
                monitor.switch("pbp")
        self.assertEqual(monitor.writes, [])

    def test_toggle_and_mac_on_hdmi2(self):
        monitor = SimulatedMonitor(local=0x12, resolution=False)
        monitor.switch("toggle")
        self.assertEqual(monitor.registers[0xE2], 0x3101)
        monitor.switch("toggle")
        self.assertEqual(monitor.registers[0xE2], 0x3100)
        self.assertEqual(monitor.registers[0x60], 0x12)
        self.assertFalse(any("--displayModeNumber" in params for _, params in monitor.commands))

    def test_status_never_writes(self):
        monitor = SimulatedMonitor(pbp=True)
        monitor.status()
        self.assertEqual(monitor.writes, [])
        self.assertTrue(all(operation == "get" for operation, _ in monitor.commands))

    def test_dry_run_is_offline_and_platform_independent(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(mac.subprocess, "run", side_effect=AssertionError("No backend allowed")), patch.object(mac, "switch_lock", side_effect=AssertionError("No state writes allowed")):
            mac.main(["--display", UUID, "pbp", "--dry-run"])
        self.assertIn("no commands sent", self.output.getvalue())

    def test_cli_requires_explicit_target_and_valid_uuid(self):
        with patch.dict(os.environ, {}, clear=True):
            for arguments in [["status"], ["--display", "YOUR_DISPLAY_UUID", "full", "--dry-run"], ["--serial", "YOUR_MONITOR_SERIAL", "pbp", "--dry-run"]]:
                with self.assertRaises(SystemExit) as error:
                    mac.main(arguments)
                self.assertEqual(error.exception.code, 2)

    def test_explicit_uuid_overrides_serial_environment(self):
        with patch.dict(os.environ, {"MONITOR_SERIAL": "TEST_SERIAL"}), patch.object(mac.sys, "platform", "darwin"), patch.object(mac.shutil, "which", return_value="/mock/tool"), patch.object(mac.Monitor, "status") as status:
            mac.main(["--display", UUID, "status"])
            self.assertEqual(status.call_count, 1)

    def test_switch_lock_rejects_concurrent_switches(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(mac.Path, "home", return_value=Path(directory)):
            with mac.switch_lock():
                with self.assertRaises(RuntimeError):
                    with mac.switch_lock():
                        self.fail("Second switch entered")


if __name__ == "__main__":
    unittest.main()
