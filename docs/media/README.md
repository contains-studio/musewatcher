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

[Play the tap preview](tap-reaction-preview.mp4) · [Play wheel navigation](wheel-navigation-simulator.mp4)

The artwork remains subject to the [asset notice](../../assets/weather/NOTICE.md). Videos and GIFs are demonstrations; they do not promise a particular frame rate on every device.
