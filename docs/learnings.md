# Watcher implementation notes

These notes record constraints that matter when maintaining this fork or repeating a fresh build. They complement the [setup guide](setup.md) and the upstream [ESP32 instructions](../esp32/AGENTS.md).

## A successful local build can hide missing source inputs

The upstream customization flow deliberately ignores `esp32/components/muse/avatar/`. CMake chooses its `muse_pixel.c` when it exists and otherwise builds `esp32/avatar/muse_pixel.c`. A local override can make a developer build display a different character from a fresh clone. This fork bundles its intended default renderer and `muse_ambient.h` dependency in `esp32/avatar/`.

The wardrobe renderer requires the tracked `esp32/components/muse/wardrobe_assets.h`. Omitting it breaks every Watcher build. Publish the renderer, the generated wardrobe header, the source PNGs, and the catalog together. Keep scratch previews and generated binaries out of the source package. The converter uses repository-local paths by default so regeneration does not depend on a sibling personal workspace.

The thirteen outfit IDs are shared by the catalog, firmware command schema, saved NVS setting, and renderer. Their order also matches the happy-pose anchor table. Replacing artwork or reordering the generated assets requires inspecting every neutral and happy pose. An asset count assertion alone cannot detect a reordered eye or shoulder anchor.

## Pixel art must survive its final display size

The first generated outfit images contained a thin tiled lattice across the face, fur, and clothes. Nearest-neighbor reduction to 72 pixels tall made those lines alias into thick stripes. The artifacts were already present in the idle sprite; changing display scaling could not repair the source. Regenerating all thirteen outfits with broad color areas and stepped silhouettes removed the grid while retaining the cream fur and weather clothing.

Inspect both the full source image and the converted RGB565 sprite. After changing artwork, remeasure eye, mouth, and sleeve bounds in the cropped sprite's coordinates. The happy mouth needs its own bounds; inferring it from the eyes can leave parts of the old smile visible. Erasing facial marks by copying one neighboring color into each half of a row can also introduce a seam. The renderer interpolates between the clean skin samples on either side instead. Check both expressions for every outfit, including the return to idle.

The default procedural renderer uses `localtime_r` for its day-period calculation. The app sets the POSIX timezone from `CONFIG_MUSE_TIME_ZONE`, which defaults to `UTC0`; `watcher.py configure --timezone` lets an owner change it. It does not discover a timezone from weather or location. An external weather job uses its own scheduler timezone, commonly an IANA name, independently. The dressed renderer's scenery comes from the outfit ID, independently of the procedural renderer's weather hook.

## Profiles must describe the feature that ships

The Watcher overlay must enable `CONFIG_MUSE_WATCHER_CAMERA=y` to reproduce this interface. The generic Kconfig option defaults off. In this revision, the wheel gesture recognizer and rotation/click navigation are also inside camera feature guards; a camera-disabled build therefore loses more than the camera shortcut. If camera-free Watcher builds become a supported product variant, separate wheel navigation from the camera guard and test both variants.

`CONFIG_MUSE_HATCH=y` follows from the Watcher's PSRAM, and `CONFIG_MUSE_CONSOLE_UART=y` follows from its board selection. There is no additional camera-backend toggle: the Watcher registers the SSCMA camera through its existing Himax chip when camera support is enabled.

The committed Watcher profile also enables `CONFIG_LV_USE_SNAPSHOT=y`. It allocates a screen-sized RGB565 buffer while capturing. The generic profile still leaves this feature off, and the upstream bench profile remains available for other boards. There is no separate bench build required for Watcher screenshots.

ESP-IDF's generated `sdkconfig` overrides defaults on later builds. A change to an overlay is not evidence that an existing local build uses it. Check the active profile and rebuild from a clean configuration when validating defaults. Keep SDK tokens and network credentials out of overlays, examples, build logs, and published firmware. The SDK token is compiled into the binary even though its source setting is ignored by Git.

The Watcher helper checks for ESP-IDF v6.0.1 before prompting, accepts the SDK token through `getpass`, and writes the local configuration with private permissions. It requires a configured Watcher profile and existing build before flashing, then rebuilds before invoking the paced writer. A missing or failed build must never fall through to a hardware write.

## The Watcher's USB bridge needs paced writes

The bottom USB-C port exposes the CH342's two UARTs. The ESP32 console is the second port; the other port belongs to the Himax camera. Identify the USB device each time rather than preserving a machine-specific path.

The Watcher helper requires a complete pair with a shared nonempty USB serial number. Linux pyserial locations include USB interface numbers, which take precedence over device names. On systems without those numbers, compare matched port names numerically: lexical ordering puts `ttyACM10` before `ttyACM9`. A missing serial number must not turn each UART into an independent candidate. Incomplete or contradictory descriptors remain unknown, and the helper refuses to open them.

The bridge can drop bytes when an entire esptool packet arrives faster than its UART drains. Lower baud alone does not solve this. `paced_esptool.py` writes 64-byte chunks, flushes, and delays for their transmission time; the Watcher helper uses 115200 baud. Use that path for backups and restores as well as firmware installation.

The factory `nvsfactory` range is `0x9000` through `0x3AFFF` inclusive. Muse places its partition table at `0x10000`, NVS at `0x11000`, and the first 4 MiB application slot at `0x20000`. Preserve the factory identity before the first custom flash. Normal Muse updates preserve Muse NVS; restoring factory identity is a separate recovery procedure after reinstalling stock firmware.

`watcher.py backup` saves that factory region as `nvsfactory.bin` by default. `--full` additionally saves all 32 MiB as `full-flash.bin`; it is much slower at 115200 baud. The helper checks output size, records SHA-256 hashes in `checksums.json`, and refuses to overwrite existing backups. These device-specific files can contain credentials and must stay private.

Do not interrupt a flash because the helper produces little output. `board.sh` pipes the flasher's output through `tail`, so the terminal can appear quiet for minutes. If a write fails after erasing boot data, the ROM bootloader remains the route to a complete recovery flash.

## Keep shared build dependencies under one owner

The upstream `tools/muse/board.sh` builds profiles one at a time and clears `managed_components/` and `dependencies.lock` before and after a build. Both paths are shared by board profiles in the same `esp32/` directory. Concurrent builds for different boards can remove files the other build is using.

Use separate checkouts for parallel board builds, or serialize them. A fresh build must resolve its dependencies rather than inherit a previously populated directory. Host tests that compile cJSON or other managed dependencies need those inputs available; distinguish an intentional dependency skip from a tested pass.

Keep a non-Watcher build in validation when changing shared UI, voice, settings, or build files. In this work, the AIPI profile exposed whether Watcher-specific behavior leaked into the common code.

## Input events are ordered, and recording can have several owners

Touch, the physical wheel, and serial controls can hold push-to-talk concurrently. Releasing one source must not stop recording while another remains held. Preserve per-source edges, their ordering, and queued events under backpressure; a combined bitmask can erase a release followed by the next press.

Touch recording starts after a 400 ms hold. Dragging, lost touch, sleep, or navigation cancels it. A waking touch is consumed as a wake action. Wheel rotation retains direction and encoder remainder so fast turns advance multiple items without losing steps; the first sleeping-wheel movement only wakes the display.

Test the real input-to-voice bridge, not just the gesture state machine. Also test duplicate presses on other boards: a Watcher-specific fix to start the next note must not reinterpret another board's duplicate `DOWN` as a new recording.

## The image decoder renders several strips concurrently

LVGL can draw separate tiles through separate decoder instances. Each instance needs its own strip buffer. Sharing one mutable strip lets a tile blend pixels that another decoder has already replaced.

The wardrobe renderer latches the outfit selection and pose once per UI frame. Decoder workers then read that frame; remote selection only changes the next requested outfit. Keep this boundary intact when adding scene state. Scenery is a bounded array of rectangles, not a new full-screen framebuffer, and is composited only into black pixels after the repeated-row copy optimization.

The happy rig samples the selected outfit's original sleeves and fur using fixed-point transforms. Trigonometry runs while preparing the pose, not for every display pixel. Scaling still costs more during the happy pose; host timings do not establish an ESP32 frame budget. Coordinate-map types must represent negative source coordinates without colliding with their out-of-canvas sentinel.

Useful regression checks compare full frames with strips and clipped regions, verify row padding and extreme coordinates, switch selection while decoding a latched frame, and require unaffected quiet states to remain byte-identical. A test that merely finds new hearts outside the character cannot prove that a tap changes its face.

## Gate ambient animation using the visible workflow

Idle mode alone does not mean the home is unobstructed. The BLE passkey card can appear while Wi-Fi remains ready, and messages such as `MIC OFF` can be shown in idle mode. Check the pairing overlay and the current caption state as well as reading, browsing, camera, menu, settings, and sleep state.

The caption label is updated after wardrobe rendering. Reading the label at that point can lag one frame. The renderer gate therefore uses a small, independently versioned read of the current caption state. Keep its version separate from the full label consumer so neither consumes the other's update.

## Camera review and network delivery are separate states

The Watcher uses 416 × 416 live-preview frames and requests 640 × 480 stills from its existing Himax firmware. Opening preview powers the camera and displays frames; freezing a frame does not send it. Sending is an explicit action on the reviewed JPEG.

UI callbacks enqueue work instead of waiting for capture, JPEG decoding, shutdown, or upload. Preserve generation checks when closing and reopening the camera so an old completion cannot update a new view. A send failure retains the reviewed image and offers a deliberate retry. Do not automatically retry a possibly accepted upload.

The latest card is retained separately from the camera preview. It can be a downloaded image or a native weather card. Temporary camera frames and failed downloads must not replace the last successful card. Both saved card and reply history are in RAM and disappear at restart.

`camera.capture` returns `payload.format: "jpeg-base64"` and `payload.data_base64` through Home Link. It is only advertised in camera-enabled builds. `display.set_outfit` persists a supported outfit ID in the `muse_wardrobe` NVS namespace and does not dismiss the current card. These are authenticated device-session commands, not unauthenticated web routes.

## Weather automation lives outside firmware

The bundled selector maps caller-supplied weather to an outfit using `assets/weather/weather-rules.json`. It does not fetch weather or own a recurring job. Users supply their own location, weather source, device selection, and schedule. The image-delivery fallback also needs their storage/upload policy.

Use current conditions and feels-like temperature when available; do not infer an outfit from the day's high or calendar season. Match the same outfit in the weather card and the saved idle selection. Record provenance outside the display and label missing-data fallbacks.

A remote timeout leaves delivery uncertain. A card can already be visible even if the caller did not receive the response. Confirm the display or inspect another reliable receipt before resending. Keep USB power available for scheduled delivery because battery sleep can suspend network access.

## Send weather values instead of downloading a full-screen image

A weather report contains a handful of numbers and an outfit ID. Sending those as `display.weather` avoids the image upload, HTTPS connection, JPEG decode, and full-screen card buffers required by `display.draw_url`. The firmware renders pixel text over the existing animated wardrobe. The command validates every field before persisting the outfit and queuing the card; invalid data must not partly change either the card or the saved outfit.

The forecast shares **Latest card** with downloaded images. A successful replacement becomes the latest card, while a failed image download preserves the previous one. Card history stays in RAM; outfit selection stays in NVS. Camera, recording, replies, settings, and pairing take priority over weather presentation. `display.show_animation` dismisses the card without deleting recall. These distinctions matter when testing delivery during another workflow, rather than only at idle.

The native command uses Fahrenheit/mph and omits unknown optional measurements. Weather selection still belongs to the caller, including feels-like precedence and precipitation rules. Retain the JPEG route for custom images and firmware that does not advertise `display.weather`; a command timeout does not justify switching routes and duplicating delivery.

## Keep slow snapshot transfer outside the UI task

The panel's RGB565 snapshot is about 332 KiB before base64 encoding, and the Watcher's 115200-baud console needs roughly 40 seconds to transfer it. Writing the whole stream inside the LVGL frame callback stalled drawing and wheel handling for that duration, which made cancellation appear broken during capture.

Capture under the display lock, copy the pixels while accounting for row stride, and release LVGL's draw buffer before returning to the UI. A background task owns the immutable copy and writes small chunks, freeing its buffer on success or failure. Admit only one active transfer so repeated requests cannot accumulate screen-sized allocations. Use bounded USB writes when the host can disappear. The worker must not access LVGL objects or hold the display lock while transmitting.

A snapshot records the capture instant even though the character keeps moving afterward. It cannot demonstrate that controls remained responsive throughout transfer; check the interaction separately and label any local or simulated input. Older evidence captured before this change retains its original pause limitation.

## Name the kind of evidence that was captured

The host wardrobe preview uses the production renderer, but it does not reproduce the panel, camera, radio, power timing, or available ESP32 memory. The SDL simulator exercises production UI with host adapters and synthetic camera frames. Both are useful before hardware verification; neither is a device screenshot.

A hardware snapshot is 339,488 bytes (about 332 KiB) of raw RGB565 pixels for this display. Base64 and framing take roughly 40 seconds at 115200 baud, so `watcher.py screenshot` gives the reader a 90-second timeout. A snapshot can itself wake the display. Capture the relevant final feature, identify fixtures or forced states, and recapture after changes to the demonstrated behavior.
