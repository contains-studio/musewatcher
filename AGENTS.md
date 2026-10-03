# Working on Muse Watcher

This is a public fork of the Muse Gadget SDK. `watcher` is the product branch; retain upstream history, copyright notices, and third-party license distinctions.

- Read `esp32/AGENTS.md` for hardware/build rules. Use ESP-IDF 6.0.1 and the paced Watcher flasher. Never publish SDK tokens, Wi-Fi credentials, generated firmware/configs, factory backups, or personal device IDs.
- Maintained weather artwork lives in `assets/weather/`. Its generated header is `esp32/components/muse/wardrobe_assets.h`. Keep catalog order stable because expression rigs use that order. Read `assets/weather/NOTICE.md`; do not claim the original character is covered by Apache 2.0.
- `esp32/avatar/muse_pixel.c` and `muse_ambient.h` contain the bundled default renderer. `esp32/components/muse/avatar/` remains an ignored personal override directory.
- Describe clothing by weather, temperature, color, or garment. Avoid fashion-brand references.
- Keep home quiet. Single tap pets Muse, hold records, double-tap opens camera preview. Settings belong on the second screen. Uploading a photo requires an explicit send action.
- Validate affected host tests and the Watcher build. For shared firmware changes also check another full-UI board. Document device tests separately from simulations.
- Every PR must have **Screenshots** and **Video** sections showing the changed feature from the reviewed revision. Embed screenshots and embed or directly link a playable video within the repository's access boundary. Label fixtures and simulated timing. For nonvisual work, show the relevant terminal/API workflow where useful, or explain why meaningful visual evidence is unavailable.
