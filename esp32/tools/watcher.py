#!/usr/bin/env python3
"""Build, back up, flash, and inspect a SenseCAP Watcher using ESP-IDF 6.0.1.

Credentials are entered with getpass and kept in the ignored build directory.
Serial writes always use the paced CH342 transport. No command erases NVS.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ESP32 = Path(__file__).resolve().parents[1]
BUILD = ESP32 / "build-muse-sensecap-watcher"
DEFAULTS = "sdkconfig.defaults;devices/sdkconfig.muse;devices/sdkconfig.muse-sensecap-watcher"
TOKEN_PATTERN = r"mgst_[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]"
CONSOLE_ROLE = "ESP32-S3 console"
CAMERA_ROLE = "camera UART (do not flash)"
UNKNOWN_ROLE = "unknown UART (do not flash)"
sys.path.insert(0, str(ESP32 / "tools/muse"))


def run(args, cwd=ESP32):
    subprocess.run([str(a) for a in args], cwd=cwd, check=True)


def idf_command(action):
    idf = shutil.which("idf.py")
    if not idf:
        raise ValueError("Activate ESP-IDF 6.0.1 first: . /path/to/esp-idf/export.sh")
    result = subprocess.run([idf, "--version"], capture_output=True, text=True, check=True)
    if not re.search(r"\bv6\.0\.1(?:\s|$)", result.stdout):
        raise ValueError("This checkout requires ESP-IDF v6.0.1.")
    return [idf, "-B", BUILD, "-DIDF_TARGET=esp32s3", f"-DSDKCONFIG={BUILD / 'sdkconfig'}",
            f"-DSDKCONFIG_DEFAULTS={DEFAULTS}", action]


def config_value(text, key):
    match = re.search(r"^" + re.escape(key) + r"=(.*)$", text, re.MULTILINE)
    return match[1] if match else None


def has_token(text):
    value = config_value(text, "CONFIG_GADGET_SDK_TOKEN")
    return bool(value and re.fullmatch('"' + TOKEN_PATTERN + '"', value))


def configure(replace_token=False, timezone=None):
    command = idf_command("reconfigure")  # Validate environment before prompting.
    path = BUILD / "sdkconfig"
    text = path.read_text() if path.exists() else ""
    if replace_token or not has_token(text):
        if not sys.stdin.isatty():
            raise ValueError("Run configure in a terminal to enter your SDK token securely.")
        token = getpass.getpass("Muse SDK token (hidden; mgst_...): ").strip()
        if not re.fullmatch(TOKEN_PATTERN, token):
            raise ValueError("Expected a 48-character Muse SDK token beginning with mgst_. No file changed.")
        text = re.sub(r"^CONFIG_GADGET_SDK_TOKEN=.*\n?", "", text, flags=re.MULTILINE)
        text += "\nCONFIG_GADGET_SDK_TOKEN=" + json.dumps(token) + "\n"
    if timezone is not None:
        if not timezone or any(c in timezone for c in '\n\r\x00"\\'):
            raise ValueError("Use a POSIX timezone such as UTC0 or PST8PDT,M3.2.0,M11.1.0.")
        text = re.sub(r"^CONFIG_MUSE_TIME_ZONE=.*\n?", "", text, flags=re.MULTILINE)
        text += "CONFIG_MUSE_TIME_ZONE=" + json.dumps(timezone) + "\n"
    BUILD.mkdir(parents=True, exist_ok=True)
    # Atomic replacement with private permissions, including an existing config.
    with tempfile.NamedTemporaryFile(mode="w", dir=BUILD, delete=False) as stream:
        stream.write(text)
        temporary = Path(stream.name)
    temporary.replace(path)
    path.chmod(0o600)
    run(command)
    path.chmod(0o600)
    print("Configured. Token stays in the ignored build directory; pair Wi-Fi in the Muse app.")


def watcher_port_roles(detected):
    """Identify both UARTs only when a complete CH342 pair is unambiguous."""
    import ports
    roles, groups = {}, {}
    for port in detected:
        if (port.vid, port.pid) != ports.CH342:
            continue
        roles[port.device] = UNKNOWN_ROLE
        serial = (port.serial_number or "").strip()
        if serial:
            groups.setdefault(serial, []).append(port)
    for pair in groups.values():
        if len(pair) != 2 or pair[0].device == pair[1].device:
            continue
        locations = [getattr(port, "location", None) or "" for port in pair]
        # Linux pyserial exposes physical USB location/configuration/interface.
        # Its free-form `interface` string is not a portable interface number.
        interfaces = [re.fullmatch(r"(.+):(\d+)\.(\d+)", location) for location in locations]
        if any(interfaces):
            if not all(interfaces) or interfaces[0].groups()[:2] != interfaces[1].groups()[:2]:
                continue
            order = [int(interface[3]) for interface in interfaces]
        else:
            # macOS supplies the same physical USB location for both UARTs.
            # Without interface numbers, require matching names and compare
            # numeric suffixes: ttyACM10 follows ttyACM9, not the reverse.
            if locations[0] != locations[1]:
                continue
            names = [re.fullmatch(r"(.*\D)(\d+)", port.device) for port in pair]
            if not all(names) or names[0][1] != names[1][1]:
                continue
            order = [int(name[2]) for name in names]
        if order[0] == order[1]:
            continue
        camera, console = sorted(range(2), key=lambda index: order[index])
        roles[pair[camera].device] = CAMERA_ROLE
        roles[pair[console].device] = CONSOLE_ROLE
    return roles


def serial_port(port):
    import ports
    if watcher_port_roles(ports.comports()).get(port) != CONSOLE_ROLE:
        raise ValueError("That port is not an unambiguous Watcher ESP32 console. Run ports; both UARTs and their USB identity must be visible.")
    return port


def paced_args(port, *args):
    return [sys.executable, ESP32 / "tools/muse/paced_esptool.py", "--chip", "esp32s3",
            "-p", port, "-b", "115200", "--before", "default-reset", "--after", "hard-reset", *args]


def backup(port, directory, full=False):
    directory = directory.expanduser().resolve()
    files = [("nvsfactory.bin", "0x9000", "0x32000")]
    if full:
        files.append(("full-flash.bin", "0", "0x2000000"))
    if any((directory / name).exists() for name, _, _ in files) or (directory / "checksums.json").exists():
        raise ValueError("Backup files already exist. Choose a new output directory; no files overwritten.")
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    checksums = {}
    for name, offset, length in files:
        path = directory / name
        run(paced_args(port, "read-flash", offset, length, path))
        path.chmod(0o600)
        if path.stat().st_size != int(length, 0):
            raise ValueError("Incomplete backup. Keep the device on stock firmware and retry in a new folder.")
        checksums[name] = {"offset": offset, "bytes": path.stat().st_size,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (directory / "checksums.json").write_text(json.dumps(checksums, indent=2) + "\n")
    print("Backup verified by size and SHA-256. Keep this device-specific directory private:", directory)


def require_config():
    path = BUILD / "sdkconfig"
    if not path.exists() or not has_token(path.read_text()):
        raise ValueError("Run configure first to save your SDK token privately.")
    text = path.read_text()
    if config_value(text, "CONFIG_MUSE_BOARD_SENSECAP_WATCHER") != "y":
        raise ValueError("The build config is not for a SenseCAP Watcher. Run configure in a fresh build directory.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ports", help="List both CH342 interfaces without opening the device")
    config = commands.add_parser("configure", help="Securely enter an SDK token and configure IDF")
    config.add_argument("--replace-token", action="store_true")
    config.add_argument("--timezone", help="POSIX TZ string for the default avatar; default UTC0")
    commands.add_parser("build", help="Build the Watcher profile")
    for name in ("backup", "flash", "status", "screenshot"):
        command = commands.add_parser(name)
        command.add_argument("--port", required=True, help="ESP32-S3 interface, not the camera UART")
        if name == "backup":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--full", action="store_true", help="Also read all 32 MiB; this takes much longer")
        if name == "screenshot":
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.command == "ports":
            import ports
            roles = watcher_port_roles(ports.comports())
            for device, role in roles.items():
                print(device, "—", role)
            if not roles:
                raise ValueError("No CH342 bridge found. Use a data cable in the Watcher's bottom USB-C port.")
            if CONSOLE_ROLE not in roles.values():
                raise ValueError("No unambiguous Watcher console found. Reconnect the bridge and check that both UARTs and their USB identity are visible.")
        elif args.command == "configure":
            configure(args.replace_token, args.timezone)
        elif args.command == "build":
            require_config()
            run(idf_command("build"))
        else:
            port = serial_port(args.port)
            if args.command == "backup":
                backup(port, args.output, args.full)
            elif args.command == "flash":
                require_config()
                if not (BUILD / "flash_args").is_file():
                    raise ValueError("No firmware build found. Run build first.")
                # Verify the artifacts are current before any write to hardware.
                run(idf_command("build"))
                run(paced_args(port, "write-flash", "@flash_args"), cwd=BUILD)
            elif args.command == "screenshot":
                run([sys.executable, ESP32 / "tools/muse/screenshot.py", port, args.output.resolve(), "--timeout", "90"])
            else:
                from chat import Board
                with Board(port) as board:
                    status = board.status()
                    if not status:
                        raise ValueError("No Muse status received. Close other serial tools and retry.")
                    device = status.get("device", {})
                    print(json.dumps({"board": status.get("board"), "chat": status.get("chat"),
                                      "wifi_state": device.get("wifi", {}).get("state"),
                                      "muse_state": device.get("hatch", {}).get("state"),
                                      "paired": device.get("hatch", {}).get("token")}, indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        # Never include a supplied token, raw serial data, or config text.
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
