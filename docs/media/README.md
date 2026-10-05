# Images and animation evidence

These files show the implemented Watcher UI. No device credentials, private conversations, camera photos, or location identifiers are included.

| File | What it shows | Capture limits |
|---|---|---|
| `device-idle.png`, `device-excited.png` | Physical Watcher with the earlier hot-weather artwork | Historical USB screenshots from before the artwork regeneration. The excited state was triggered by a serial home-wheel click, which calls the same happiness state as touch. Snapshot transfer pauses drawing. |
| `device-weather-card.png` | A weather card delivered to the physical Watcher | Historical weather values and earlier artwork, not a current forecast or capture of the regenerated art. The image-card display path is unchanged. |
| `tap-reaction.gif`, `tap-reaction-preview.mp4` | Production wardrobe renderer: idle → excited → idle | Host rendering with a simulated tap and 1.6-second happiness timer. The HOST PREVIEW label is an evidence label, not device UI. |
| `ambient-weather.gif` | Moving scenery around the hot-weather outfit | Current production renderer and regenerated artwork, with simulated time. |
| `weather-effects.png` | Sunny, rainy, snowy, and windy outfit scenes | Production-renderer host previews, not device captures. |
| `outfit-reactions.png` | All thirteen outfits in idle and happy poses | Current production-renderer host previews with simulated idle and tap timing. |
| `artwork-before-after.png` | Four representative outfits before and after removing the tile grid | Source art converted to 72 pixels tall using nearest-neighbor sampling, then enlarged for comparison. |
| `wheel-navigation-simulator.mp4` | Reply paging, card recall, and camera actions | Production LVGL UI with scripted inputs and simulated time. Reply text, colored cards, and checkerboard camera are local fixtures; no photo is sent. |
| `wheel-cancel/device-thinking.png`, `wheel-cancel/device-cancelled.png` | Physical Watcher before and after cancellation, with the current artwork | Forced local thinking state and USB-injected wheel click; no live Muse request. The physical wheel was checked separately after capture. |
| `wheel-cancel/wheel-cancel.mp4` | Thinking → one wheel press → idle | Production LVGL simulator with synthetic wait and input. The click occurs at two seconds; fixed simulator time, 20 fps, enlarged 2×. Not a physical-device recording. |

[Play the tap preview](tap-reaction-preview.mp4) · [Play wheel navigation](wheel-navigation-simulator.mp4) · [Play wheel cancellation](wheel-cancel/wheel-cancel.mp4)

The main README uses the current previews and cancellation screenshots. The
older `device-idle.png`, `device-excited.png`, and `device-weather-card.png`
remain historical evidence and do not show the latest artwork. USB screenshot
transfer pauses drawing and wheel handling for about 40 seconds; finish the
capture before testing controls. See [cancellation capture details](wheel-cancel/README.md).

The artwork remains subject to the [asset notice](../../assets/weather/NOTICE.md). Videos and GIFs are demonstrations; they do not promise a particular frame rate on every device.
