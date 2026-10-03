# Build and set up a Muse Watcher

This fork targets the Seeed SenseCAP Watcher: ESP32-S3, 32 MB flash, 8 MB PSRAM, a 412 × 412 round touch display, a wheel, microphone, speaker, and Himax camera. It retains the upstream SDK's other board profiles. These instructions use the Watcher helper so the target, partition table, and paced USB flashing stay consistent.

You need a Watcher, a USB data cable, a macOS or Linux computer, the Muse phone app, and your own SDK token from [Muse Gadgets](https://gadgets.muse.ai/settings/sdk-tokens). Enable developer devices in the app during pairing. A standard USB connection is sufficient; no JTAG adapter is needed.

## 1. Install the toolchain

Use **ESP-IDF v6.0.1**. Other versions are not supported by this checkout. On macOS, install Apple's command-line tools and the build prerequisites:

```sh
xcode-select --install
brew install git cmake ninja dfu-util python3
```

If the command-line tools are already installed, continue. On Linux, install the packages in Espressif's [Linux setup instructions](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/linux-macos-setup.html), then install ESP-IDF:

```sh
git clone -b v6.0.1 --recursive https://github.com/espressif/esp-idf.git ~/esp/esp-idf-v6.0.1
~/esp/esp-idf-v6.0.1/install.sh esp32s3
. ~/esp/esp-idf-v6.0.1/export.sh
python -m pip install pillow
```

Run the `export.sh` line in each new build terminal. ESP-IDF provides esptool and pyserial in its Python environment; Pillow is used by the artwork and preview tools. A first build downloads managed components and needs internet access.

Clone the fork and enter its firmware directory:

```sh
git clone --branch watcher https://github.com/contains-studio/musewatcher.git
cd musewatcher/esp32
python tools/watcher.py --help
```

The `watcher` branch contains this fork's firmware and artwork. The `main` branch retains upstream source. Unless a section says otherwise, run the remaining commands from `esp32/`.

## 2. Connect the correct USB port and back up the factory firmware

Connect the Watcher's **bottom USB-C port** with a data cable. Its CH342 bridge exposes two serial ports: the first belongs to the Himax camera chip, and the second is the ESP32-S3 console used here. On macOS, the ESP32 console commonly ends in `3`. Port names change with cables and hubs; use device detection instead of copying another person's port name.

```sh
python tools/watcher.py ports
```

Use the reported ESP32 console path wherever these instructions say `PORT`. Listing ports does not reset the device. Close other serial monitors before backup, flashing, or issuing commands.

The helper requires both UARTs with a shared USB serial identity. It uses USB interface numbers when available, or numeric port ordering for a matched pair. If it labels a port **unknown**, it refuses to use it. Reconnect the bridge and check that the OS exposes both ports and their USB descriptors; do not guess a path or substitute the camera UART.

Before the **first** custom-firmware flash, save a backup outside the repository:

```sh
python tools/watcher.py backup --port PORT --output ~/watcher-backup
```

This saves `nvsfactory.bin` and `checksums.json`, which records its size and SHA-256. The helper refuses to overwrite an existing backup. Keep that directory private and preserve it independently of this checkout. The Watcher's unique factory identity is in `nvsfactory`, beginning at **`0x9000`**, with length **`0x32000`** and end address `0x3B000`. Muse's partition table and NVS overlap that region. A backup made after installing Muse cannot recover the original factory identity.

For a complete stock-firmware recovery image, add `--full` and choose a new directory:

```sh
python tools/watcher.py backup --port PORT --output ~/watcher-full-backup --full
```

This also saves `full-flash.bin`: **32 MiB**, beginning at address `0x0`. Reading the entire flash at 115200 baud takes much longer than saving the factory region. Keep the device connected until it finishes.

The CH342 needs writes paced in 64-byte chunks at **115200 baud**. The helper uses `tools/muse/paced_esptool.py`; ordinary `idf.py flash` or plain esptool can fail while uploading its stub or after erasing part of the existing image. Let a flash finish. A quiet console for several minutes does not mean it is stuck.

## 3. Configure your SDK token and build

```sh
python tools/watcher.py configure
python tools/watcher.py build
```

The configuration step asks for your SDK token without echoing it. Supply your own token; this repository ships neither a shared token nor a preconfigured device identity. The token is stored in the ignored local build configuration and is compiled into your firmware. Keep generated configuration files, firmware binaries, backups, and logs private. Publish source changes rather than a binary containing your token.

The default avatar uses **UTC** for morning/day/night scenery. Set your local POSIX timezone during configuration; for example, US Pacific time with daylight-saving rules is:

```sh
python tools/watcher.py configure --timezone 'PST8PDT,M3.2.0,M11.1.0'
```

This sets `CONFIG_MUSE_TIME_ZONE`; the default is `UTC0`. Reconfiguration keeps an existing token unless you pass `--replace-token`. The firmware's POSIX timezone is separate from the IANA timezone, such as `America/Los_Angeles`, used by an external daily weather job.

The Watcher profile enables camera support and USB screenshots. Its PSRAM also enables Muse's own voice session. Wi-Fi and pairing credentials are provisioned through the Muse app and retained in NVS; no hardcoded network name or password is required.

The ordinary build directory is `build-muse-sensecap-watcher/`, and its application image is `muse-gadget.bin`. The complete flash set also includes the bootloader, partition table, and OTA initialization data. Use the helper's flash command instead of writing just the application image to a guessed address.

A generated `sdkconfig` takes precedence over later edits to defaults. If you reuse an older checkout or build directory, reconfigure the correct profile. Do not assume that changing a defaults file changed an existing build. The SDK checks cJSON nesting and TCP buffer settings during configuration and reports stale values explicitly.

For advanced configuration, the equivalent Watcher menu command is:

```sh
idf.py -B build-muse-sensecap-watcher -DIDF_TARGET=esp32s3 \
  -DSDKCONFIG=build-muse-sensecap-watcher/sdkconfig \
  -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;devices/sdkconfig.muse;devices/sdkconfig.muse-sensecap-watcher" \
  menuconfig
```

The SDK token setting is under **ESP32 Device SDK → Muse Gadgets SDK token**. Running bare `idf.py menuconfig` configures the default board, not this Watcher build directory.

## 4. Flash and pair

```sh
python tools/watcher.py flash --port PORT
python tools/watcher.py status --port PORT
```

Normal reflashing preserves Muse's NVS, including Wi-Fi, pairing, saved outfit, and sound settings. An erase operation clears these settings and is not part of an update.

In the Muse phone app, open **Settings → Devices → Developer mode**, enable it, and choose **Add Device**. Select the nearby `MuseGadget-XXXXXX`, follow Wi-Fi setup, and press the Watcher's wheel when the display requests physical pairing confirmation. Use your own nearby device rather than a device ID from an example. Keep USB power and Wi-Fi available while connecting.

Check that the firmware boots without repeated resets and that the app can reach the device. A configured SDK token, successful flash, connected Wi-Fi, and a working Muse session are separate checks. A missing token can still produce a firmware build with a warning; it is not a complete pairing setup.

## Controls

| Action | Result |
| --- | --- |
| Tap the idle home | Pet Muse; the dressed character changes expression, raises its arms, and hops. |
| Hold the home screen for about 0.4 seconds | Record while held; release to send. Dragging away or leaving home cancels. |
| Hold the physical wheel | Push to talk. |
| Turn the wheel during a reply | Read backward or forward; manual reading pauses automatic paging. Press to finish. |
| Turn the wheel at idle | Browse the latest card, last reply, or return to Muse. Press to open the selection. |
| Swipe left / right | Open Settings / return home. |
| Double-tap home or double-click the wheel | Open camera preview; repeat the camera action to freeze a frame. |
| Touch a sleeping screen | Wake it; the waking touch does not also record or pet. |

The idle home keeps the character low on the display. Replies appear above a smaller Muse and page automatically whether sound is on or off. The latest completed card and last reply are retained in RAM until restart. Settings → Sound controls the microphone and speaker; microphone mute cancels active recording and pauses queued voice notes.

In camera preview, choose **Take photo**; in review, choose **Send photo**, **Retake**, or **×** to discard. Wheel rotation highlights an action and a press selects it. Turning alone never sends a photo. A failed send retains the frozen JPEG for an explicit retry. Camera images are separate messages, not automatic attachments to voice recordings. The camera uses the Watcher's existing Himax firmware; this build does not replace that firmware.

## Outfits and weather

The repository includes thirteen outfit PNGs, the catalog, and ordered selection rules under [`assets/weather/`](../assets/weather/). The generated RGB565 header is tracked at `esp32/components/muse/wardrobe_assets.h`, and the default character and scenery are bundled in `esp32/avatar/muse_pixel.c` and `esp32/avatar/muse_ambient.h`. A fresh clone does not need image generation, an external art service, or a personal workspace directory.

An agent connected to **your** Watcher can invoke these advertised Home Link commands:

```text
display.set_outfit {"outfit_id":"hot-shorts"}
display.set_outfit {}
display.set_outfit {"outfit_id":"default"}
```

The first command persists a selection; the second queries it; `default` restores the procedural character. Selecting an outfit does not dismiss an existing image card. `display.show_animation {}` returns to the character, while `display.draw_url {"url":"<your card URL>"}` displays a card. These are device commands through Muse's existing connection, not public HTTP endpoints.

The weather selector takes current conditions supplied by a caller. From the repository root:

```sh
python skills/muse-weather-display/scripts/choose_outfit.py weather.json
```

For example, an input file containing `{"conditions":["sunny"],"temp_f":72,"wind_mph":3}` selects the warm crochet outfit. The selector prefers feels-like temperature, falls back to current temperature, and applies precipitation rules before dry-weather rules. Its output includes the selected ID and asset path.

The firmware does **not** fetch a forecast or create a daily schedule. Configure a weather source, location, and schedule in your own agent workflow. Use the same selected outfit for both the weather card and `display.set_outfit`. No location, device ID, account, or schedule is required to build the firmware. Outfit scenery follows the selected outfit; it is not an independent live weather observation.

To regenerate sprites after editing the bundled artwork, run from `esp32/`:

```sh
python tools/muse/wardrobe_assets.py
```

The converter defaults to the bundled catalog, PNGs, and tracked header. It crops the character, preserves pixel-art scaling with nearest-neighbor sampling, and records source hashes. Eye and arm anchors in `muse_wardrobe.c` match the current art: replacing an image also requires checking its neutral and happy poses. If serving artwork directly from GitHub, use the `watcher` branch in raw-file URLs.

## Tests, previews, and screenshots

After building, run the host tests from `esp32/`:

```sh
python -m unittest discover -s tests -p 'test_*.py'
python tools/muse/wardrobe_preview.py --output-dir /tmp/watcher-outfits
```

The renderer preview compiles the production C renderer with a host compiler. Its PNGs and GIFs are **host previews**, not device screenshots. Some host tests require downloaded managed components from an ESP-IDF build. See the separate [simulator instructions](../esp32/simulator/README.md) for desktop touch, reply, and camera-control fixtures; simulated camera frames do not test the Himax hardware or upload service.

The committed Watcher profile enables `CONFIG_LV_USE_SNAPSHOT=y`. Capture the actual device display with:

```sh
python tools/watcher.py screenshot --port PORT --output /tmp/watcher.png
```

A 412 × 412 screenshot needs roughly 40 seconds to traverse the 115200-baud console, plus processing time. The helper supplies a 90-second timeout. Capture temporarily allocates a screen-sized RGB565 buffer and can wake the screen. Capture the feature being demonstrated and label forced poses or fixtures; do not present a simulator image as a hardware screenshot.

## Recovery and troubleshooting

| Symptom | Check |
| --- | --- |
| No port is listed | Bottom USB-C port, a data cable, and OS serial permissions. On Linux, your user may need membership in the serial-access group. |
| `0107: Checksum error` or `0105: The format of the received message is invalid` | Use the paced Watcher flash helper at 115200 baud; close competing serial readers. A failed write can leave the device unable to boot until a complete flash succeeds. |
| Firmware builds but camera or wheel navigation is missing | Check `CONFIG_MUSE_WATCHER_CAMERA=y` in the active Watcher profile, then rebuild. Existing generated configuration can override new defaults. |
| Connected to Wi-Fi but no response from Muse | Verify pairing and the Muse session separately. Check the app and sanitized device status; Wi-Fi alone does not prove service availability. |
| Photo upload times out | Delivery is uncertain until acknowledged. Check the app before explicitly retrying; do not blindly resend. |
| Remote weather/card delivery times out | Inspect the display or other delivery evidence before resending. A timeout is not proof that the card was not displayed. |
| Serial command unexpectedly resets or fails | Close other serial tools. `tools/muse/monitor.py` deliberately resets the board before reading its log. |
| Screenshot reports `SNAP OFF` | Check `CONFIG_LV_USE_SNAPSHOT=y` in the active Watcher configuration, rebuild, and flash. An older generated configuration may override the current profile. |

Battery sleep can disable network access. Keep USB power and Wi-Fi available for scheduled deliveries. Inspect logs locally, and remove credentials, account/device identifiers, and private content before sharing them.

To restore a **full 32 MiB backup from this same Watcher**, use its original full-flash file and the paced writer:

```sh
python tools/muse/paced_esptool.py --chip esp32s3 -p PORT -b 115200 \
  --before default-reset --after hard-reset write-flash 0x0 ~/watcher-full-backup/full-flash.bin
```

If you instead reinstall Seeed's stock firmware separately, restore this Watcher's original factory identity afterward:

```sh
python tools/muse/paced_esptool.py --chip esp32s3 -p PORT -b 115200 \
  --before default-reset --after hard-reset write-flash 0x9000 ~/watcher-backup/nvsfactory.bin
```

The factory-region backup is not a full firmware image. Do not write it over a running Muse layout as a routine update, and do not use another Watcher's identity backup.
