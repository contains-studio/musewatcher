"""Exercise Watcher setup safeguards with fake ports, builds, and credentials.

No test opens a serial device, invokes ESP-IDF, or uses a real SDK token.
"""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


ESP32 = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("watcher_setup", ESP32 / "tools/watcher.py")
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)
import ports

# Synthetic strings with the SDK's canonical shape; these are not credentials.
TOKEN = "mgst_" + "A" * 43
REPLACEMENT = "mgst_" + "B" * 42 + "E"
CAMERA_PORT = "/dev/cu.usbmodemTEST1"
ESP_PORT = "/dev/cu.usbmodemTEST3"


def fake_port(device, serial="test-bridge", usb=ports.CH342, location=None):
    return SimpleNamespace(device=device, serial_number=serial, vid=usb[0], pid=usb[1],
                           description="test bridge", interface="", location=location)


class WatcherSetupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.build = self.directory / "build"
        self.stack.enter_context(patch.object(watcher, "BUILD", self.build))
        self.run = self.stack.enter_context(patch.object(watcher, "run"))
        self.detected = self.stack.enter_context(patch.object(ports, "comports", return_value=[
            fake_port(CAMERA_PORT), fake_port(ESP_PORT)]))
        # main() sets a process-wide umask; file-mode assertions below still
        # exercise configure()/backup()'s explicit private permissions.
        self.stack.enter_context(patch.object(watcher.os, "umask"))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.stack.enter_context(redirect_stdout(self.stdout))
        self.stack.enter_context(redirect_stderr(self.stderr))

    def write_config(self, token=TOKEN, board=True):
        self.build.mkdir(exist_ok=True)
        path = self.build / "sdkconfig"
        path.write_text('CONFIG_GADGET_SDK_TOKEN=' + json.dumps(token) + '\n' +
                        ('CONFIG_MUSE_BOARD_SENSECAP_WATCHER=y\n' if board else ''))
        return path

    def mock_idf(self):
        return self.stack.enter_context(patch.object(watcher, "idf_command", return_value=["idf.py", "reconfigure"]))

    def test_hidden_prompt_saves_private_config_without_echo_or_token_arguments(self):
        self.mock_idf()
        with patch.object(watcher.sys.stdin, "isatty", return_value=True), \
                patch.object(watcher.getpass, "getpass", return_value=TOKEN) as prompt:
            watcher.configure(timezone="PST8PDT,M3.2.0,M11.1.0")
        prompt.assert_called_once()
        path = self.build / "sdkconfig"
        self.assertTrue(watcher.has_token(path.read_text()))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertIn('CONFIG_MUSE_TIME_ZONE="PST8PDT,M3.2.0,M11.1.0"', path.read_text())
        self.assertNotIn(TOKEN, self.stdout.getvalue() + self.stderr.getvalue())
        self.assertNotIn(TOKEN, str(self.run.call_args_list))

    def test_configure_keeps_existing_token_without_prompt(self):
        path = self.write_config()
        self.mock_idf()
        with patch.object(watcher.getpass, "getpass") as prompt:
            watcher.configure(timezone="UTC0")
        prompt.assert_not_called()
        self.assertEqual(watcher.config_value(path.read_text(), "CONFIG_GADGET_SDK_TOKEN"), json.dumps(TOKEN))

    def test_replace_token_removes_old_value(self):
        path = self.write_config()
        self.mock_idf()
        with patch.object(watcher.sys.stdin, "isatty", return_value=True), \
                patch.object(watcher.getpass, "getpass", return_value=REPLACEMENT):
            watcher.configure(replace_token=True)
        self.assertNotIn(TOKEN, path.read_text())
        self.assertEqual(path.read_text().count("CONFIG_GADGET_SDK_TOKEN="), 1)
        self.assertNotIn(REPLACEMENT, self.stdout.getvalue() + self.stderr.getvalue())

    def test_invalid_replacement_preserves_working_config(self):
        path = self.write_config()
        original = path.read_bytes()
        self.mock_idf()
        invalid = ("mgst_x", "mgst_" + "A" * 42 + "B", TOKEN + "A", TOKEN + "\nCONFIG_OTHER=y")
        for value in invalid:
            with self.subTest(value=value), patch.object(watcher.sys.stdin, "isatty", return_value=True), \
                    patch.object(watcher.getpass, "getpass", return_value=value):
                with self.assertRaises(ValueError):
                    watcher.configure(replace_token=True)
                self.assertEqual(path.read_bytes(), original)
        self.run.assert_not_called()

    def test_noninteractive_token_entry_does_not_fall_back_to_echo(self):
        self.mock_idf()
        with patch.object(watcher.sys.stdin, "isatty", return_value=False), \
                patch.object(watcher.getpass, "getpass") as prompt:
            with self.assertRaisesRegex(ValueError, "terminal"):
                watcher.configure()
        prompt.assert_not_called()
        self.run.assert_not_called()
        self.assertFalse(self.build.exists())

    def test_invalid_timezone_cannot_inject_configuration(self):
        path = self.write_config()
        original = path.read_bytes()
        self.mock_idf()
        for timezone in ("", "UTC0\nCONFIG_OTHER=y", 'UTC0"', "UTC0\\", "UTC0\x00"):
            with self.subTest(timezone=timezone), self.assertRaises(ValueError):
                watcher.configure(timezone=timezone)
            self.assertEqual(path.read_bytes(), original)
        self.run.assert_not_called()

    def test_idf_validation_precedes_token_prompt(self):
        with patch.object(watcher, "idf_command", side_effect=ValueError("wrong IDF")), \
                patch.object(watcher.getpass, "getpass") as prompt:
            with self.assertRaisesRegex(ValueError, "wrong IDF"):
                watcher.configure()
        prompt.assert_not_called()
        self.assertFalse(self.build.exists())

    def test_idf_version_is_pinned_and_watcher_profile_is_explicit(self):
        with patch.object(watcher.shutil, "which", return_value="/fake/idf.py"), \
                patch.object(watcher.subprocess, "run", return_value=SimpleNamespace(stdout="ESP-IDF v6.0.1\n")):
            command = watcher.idf_command("build")
        self.assertIn("-DIDF_TARGET=esp32s3", command)
        self.assertIn(f"-DSDKCONFIG={self.build / 'sdkconfig'}", command)
        self.assertIn(f"-DSDKCONFIG_DEFAULTS={watcher.DEFAULTS}", command)
        self.assertEqual(command[-1], "build")
        for version in ("ESP-IDF v6.0.10", "ESP-IDF v5.5.1", "ESP-IDF v6.1.0"):
            with self.subTest(version=version), patch.object(watcher.shutil, "which", return_value="/fake/idf.py"), \
                    patch.object(watcher.subprocess, "run", return_value=SimpleNamespace(stdout=version)), \
                    self.assertRaises(ValueError):
                watcher.idf_command("build")

    def test_missing_idf_has_no_subprocess_side_effect(self):
        with patch.object(watcher.shutil, "which", return_value=None), \
                patch.object(watcher.subprocess, "run") as process, self.assertRaises(ValueError):
            watcher.idf_command("build")
        process.assert_not_called()

    def test_port_guard_rejects_camera_and_unrelated_devices(self):
        self.detected.return_value.append(fake_port("/dev/cu.unrelated", usb=(0x303A, 0x1001)))
        self.assertEqual(watcher.serial_port(ESP_PORT), ESP_PORT)
        for port in (CAMERA_PORT, "/dev/cu.unrelated", "/dev/cu.absent"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                watcher.serial_port(port)

    def test_ports_listing_labels_roles_without_opening_hardware(self):
        self.assertEqual(watcher.main(["ports"]), 0)
        output = self.stdout.getvalue()
        self.assertIn(CAMERA_PORT, output)
        self.assertIn(ESP_PORT, output)
        self.assertIn("camera", output.lower())
        self.assertIn("ESP32", output)
        self.run.assert_not_called()

    def test_linux_console_selection_uses_numeric_port_order(self):
        camera, console = "/dev/ttyACM9", "/dev/ttyACM10"
        self.detected.return_value = [fake_port(console), fake_port(camera)]
        self.assertEqual(watcher.serial_port(console), console)
        with self.assertRaises(ValueError):
            watcher.serial_port(camera)

    def test_linux_interface_numbers_take_precedence_over_port_names(self):
        # Enumeration order is not the USB interface order on every reconnect.
        camera, console = "/dev/ttyACM10", "/dev/ttyACM9"
        self.detected.return_value = [fake_port(camera, location="1-2:1.0"),
                                      fake_port(console, location="1-2:1.2")]
        self.assertEqual(watcher.serial_port(console), console)
        with self.assertRaises(ValueError):
            watcher.serial_port(camera)

    def test_missing_serial_or_incomplete_pair_never_selects_a_console(self):
        groups = ([fake_port(CAMERA_PORT, serial=None), fake_port(ESP_PORT, serial=None)],
                  [fake_port(CAMERA_PORT, serial=""), fake_port(ESP_PORT, serial="")],
                  [fake_port(ESP_PORT)],
                  [fake_port(CAMERA_PORT), fake_port(ESP_PORT), fake_port("/dev/cu.usbmodemTEST5")])
        for group in groups:
            with self.subTest(devices=[p.device for p in group]):
                self.detected.return_value = group
                for port in group:
                    with self.assertRaises(ValueError):
                        watcher.serial_port(port.device)
                    self.assertEqual(watcher.main(["flash", "--port", port.device]), 1)
        self.run.assert_not_called()

    def test_conflicting_or_partial_location_descriptors_fail_closed(self):
        for locations in (("1-2:1.0", "1-3:1.2"), ("1-2:1.0", "1-2:2.2"),
                          ("1-2:1.0", "1-2:1.0"), ("1-2:1.0", None),
                          ("1-2", "1-3"), (None, "1-2")):
            with self.subTest(locations=locations):
                self.detected.return_value = [fake_port(CAMERA_PORT, location=locations[0]),
                                              fake_port(ESP_PORT, location=locations[1])]
                for port in (CAMERA_PORT, ESP_PORT):
                    with self.assertRaises(ValueError):
                        watcher.serial_port(port)

    def test_matching_macos_bus_location_allows_numeric_pair_selection(self):
        self.detected.return_value = [fake_port(CAMERA_PORT, location="0-1.2"),
                                      fake_port(ESP_PORT, location="0-1.2")]
        self.assertEqual(watcher.serial_port(ESP_PORT), ESP_PORT)

    def test_multiple_bridges_are_selected_independently(self):
        self.detected.return_value.extend([fake_port("/dev/ttyACM9", serial="second-bridge"),
                                          fake_port("/dev/ttyACM10", serial="second-bridge")])
        for port in (ESP_PORT, "/dev/ttyACM10"):
            self.assertEqual(watcher.serial_port(port), port)
        for port in (CAMERA_PORT, "/dev/ttyACM9"):
            with self.assertRaises(ValueError):
                watcher.serial_port(port)

    def test_unrecognized_or_duplicate_port_names_fail_closed(self):
        for names in (("/dev/ttyACM1", "/dev/ttyUSB2"), ("/dev/customA", "/dev/customB"),
                      (ESP_PORT, ESP_PORT), ("/dev/ttyACM01", "/dev/ttyACM1")):
            with self.subTest(names=names):
                self.detected.return_value = [fake_port(name) for name in names]
                for port in names:
                    with self.assertRaises(ValueError):
                        watcher.serial_port(port)

    def test_ambiguous_listing_does_not_label_any_port_as_camera_or_console(self):
        self.detected.return_value = [fake_port(CAMERA_PORT, serial=None), fake_port(ESP_PORT, serial=None)]
        self.assertEqual(watcher.main(["ports"]), 1)
        output = self.stdout.getvalue()
        self.assertIn(CAMERA_PORT, output)
        self.assertIn(ESP_PORT, output)
        self.assertIn("unknown", output.lower())
        self.assertNotIn("ESP32", output)
        self.assertNotIn("camera UART", output)
        self.run.assert_not_called()

    def test_backup_refuses_existing_files_before_reading(self):
        for name, full in (("nvsfactory.bin", False), ("checksums.json", False), ("full-flash.bin", True)):
            with self.subTest(name=name):
                directory = self.directory / name.replace(".", "-")
                directory.mkdir()
                existing = directory / name
                existing.write_bytes(b"original backup")
                with self.assertRaisesRegex(ValueError, "already exist"):
                    watcher.backup(ESP_PORT, directory, full=full)
                self.assertEqual(existing.read_bytes(), b"original backup")
        self.run.assert_not_called()

    def test_backup_records_exact_factory_and_full_flash_sizes_and_hashes(self):
        def fake_read(args):
            with Path(args[-1]).open("wb") as stream:
                stream.truncate(int(args[-2], 0))
        self.run.side_effect = fake_read
        directory = self.directory / "backup"
        watcher.backup(ESP_PORT, directory, full=True)
        checksums = json.loads((directory / "checksums.json").read_text())
        for name, offset, length in (("nvsfactory.bin", "0x9000", 0x32000), ("full-flash.bin", "0", 0x2000000)):
            path = directory / name
            self.assertEqual(checksums[name], {"offset": offset, "bytes": length,
                                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for call in self.run.call_args_list:
            args = call.args[0]
            self.assertEqual(Path(args[1]).name, "paced_esptool.py")
            self.assertEqual(args[args.index("-b") + 1], "115200")
            self.assertIn("read-flash", args)

    def test_default_backup_reads_only_factory_region(self):
        self.run.side_effect = lambda args: Path(args[-1]).write_bytes(b"\x00" * 0x32000)
        directory = self.directory / "backup"
        watcher.backup(ESP_PORT, directory)
        self.run.assert_called_once()
        self.assertEqual(self.run.call_args.args[0][-3:-1], ["0x9000", "0x32000"])
        self.assertFalse((directory / "full-flash.bin").exists())

    def test_incomplete_backup_has_no_success_manifest(self):
        self.run.side_effect = lambda args: Path(args[-1]).write_bytes(b"short")
        directory = self.directory / "backup"
        with self.assertRaisesRegex(ValueError, "Incomplete backup"):
            watcher.backup(ESP_PORT, directory)
        self.assertFalse((directory / "checksums.json").exists())

    def test_flash_requires_firmware_artifacts_before_build_or_write(self):
        self.write_config()
        idf = self.mock_idf()
        self.assertEqual(watcher.main(["flash", "--port", ESP_PORT]), 1)
        self.assertIn("Run build first", self.stderr.getvalue())
        idf.assert_not_called()
        self.run.assert_not_called()

    def test_flash_rejects_wrong_profile_before_build_or_write(self):
        self.write_config(board=False)
        (self.build / "flash_args").write_text("fixture")
        idf = self.mock_idf()
        self.assertEqual(watcher.main(["flash", "--port", ESP_PORT]), 1)
        idf.assert_not_called()
        self.run.assert_not_called()

    def test_build_rejects_missing_or_invalid_token_before_invoking_idf(self):
        idf = self.mock_idf()
        self.assertEqual(watcher.main(["build"]), 1)
        self.write_config(token="")
        self.assertEqual(watcher.main(["build"]), 1)
        idf.assert_not_called()
        self.run.assert_not_called()

    def test_flash_uses_paced_writer_only_after_successful_build(self):
        self.write_config()
        (self.build / "flash_args").write_text("fixture")
        with patch.object(watcher, "idf_command", return_value=["idf.py", "build"]):
            self.assertEqual(watcher.main(["flash", "--port", ESP_PORT]), 0)
        self.assertEqual(self.run.call_args_list[0].args[0], ["idf.py", "build"])
        write = self.run.call_args_list[1]
        args = write.args[0]
        self.assertEqual(Path(args[1]).name, "paced_esptool.py")
        self.assertEqual(args[args.index("-b") + 1], "115200")
        self.assertEqual(args[args.index("-p") + 1], ESP_PORT)
        self.assertEqual(args[-2:], ["write-flash", "@flash_args"])
        self.assertEqual(write.kwargs["cwd"], self.build)

    def test_failed_build_never_reaches_flash(self):
        self.write_config()
        (self.build / "flash_args").write_text("fixture")
        self.mock_idf()
        self.run.side_effect = subprocess.CalledProcessError(1, ["idf.py", "build"])
        self.assertEqual(watcher.main(["flash", "--port", ESP_PORT]), 1)
        self.run.assert_called_once()
        self.assertNotIn("write-flash", str(self.run.call_args_list))

    def test_camera_port_never_reaches_backup_or_flash(self):
        for command in (["flash", "--port", CAMERA_PORT],
                        ["backup", "--port", CAMERA_PORT, "--output", str(self.directory / "backup")]):
            with self.subTest(command=command):
                self.assertEqual(watcher.main(command), 1)
        self.run.assert_not_called()

    def test_screenshot_uses_long_timeout(self):
        output = self.directory / "screen.png"
        self.assertEqual(watcher.main(["screenshot", "--port", ESP_PORT, "--output", str(output)]), 0)
        args = self.run.call_args.args[0]
        self.assertEqual(Path(args[1]).name, "screenshot.py")
        self.assertEqual(args[-2:], ["--timeout", "90"])
        self.assertIn(output.resolve(), args)

    def test_status_does_not_print_raw_device_identity_or_network(self):
        board_class = MagicMock()
        board_class.return_value.__enter__.return_value.status.return_value = {
            "board": "Seeed SenseCAP Watcher", "chat": "ready", "private": "private-fixture",
            "device": {"name": "private-fixture", "wifi": {"state": "connected", "ssid": "private-fixture"},
                       "hatch": {"state": "ready", "token": True, "vm": "private-fixture"}}}
        with patch.dict(sys.modules, {"chat": SimpleNamespace(Board=board_class)}):
            self.assertEqual(watcher.main(["status", "--port", ESP_PORT]), 0)
        output = self.stdout.getvalue()
        self.assertNotIn("private-fixture", output)
        self.assertIs(json.loads(output)["paired"], True)


if __name__ == "__main__":
    unittest.main()
