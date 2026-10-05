# Native weather and background screenshots

The native `display.weather` command draws forecast values above the existing
animated character. The forecast payload occupies 56 bytes; labels use LVGL.
There is no image upload, HTTPS connection, JPEG decoding, or retained full-screen
weather bitmap. Custom images still use `display.draw_url`.

## Simulator

`simulator-weather.png`, `simulator-cold.png`, and `simulator-hot.png` are rendered
by the production LVGL UI and wardrobe renderer. Forecasts are synthetic fixtures:
75°F with a warm crochet outfit, −12°F with a puffer, and 100°F with shorts.
Missing values are omitted. The desktop simulator uses a 412 × 412 framebuffer.

[Play the five-second video](simulator-weather.mp4), or view the
[animated GIF](simulator-weather.gif). The renderer advances at fixed 50 ms steps,
20 frames per second. A simulated pet at 1.5 seconds triggers the excited pose.
This is a simulator recording, not continuous footage of physical hardware.

Reproduce with the simulator already built:

```sh
MUSE_WEATHER_VIDEO=1 python3 esp32/simulator/tests/test_wheel_navigation.py \
  --build build/simulator --out /tmp/watcher-weather
```

The integration harness also checks wheel recall, JPEG replacement/failure,
voice and camera priority, missing values, and deferral during settings,
pairing confirmation, and initial setup. A controlled stalled outfit save verifies
that wheel navigation continues while persistence is pending; the same test
linked against the pre-fix UI hits its five-second timeout.

## Physical Watcher

Captured on 2026-10-05 after the final paced flash (all four region hashes
verified). Firmware was built from the working tree recorded by this change;
the evidence and source are committed together. Pairing and Wi-Fi were retained.

`device-weather.png` is the unedited USB framebuffer after two sequential
`display.weather` deliveries through Muse. Both returned success. The test
uses 75°F, high 80°F, low 57°F, wind 10 mph, humidity 52%, and `warm-crochet`.
These are **synthetic test values, not a fresh forecast**. The 7 a.m. schedule
was not changed by this delivery test.

Repeated heartbeat samples after delivery remained at 19 KiB internal free,
12 KiB largest internal block, and 3,589 KiB PSRAM free. This is a bounded
repeat-delivery check, not a long-duration fragmentation test.

`device-capture-thinking.png` freezes a locally forced thinking state at the
start of a USB capture. At 1.07 seconds, the test injected a wheel click and a
second capture request through the console. The first transfer completed at
40.29 seconds. The second request was consumed while busy and did not queue
another capture, showing that the UI continued processing during transfer.
`device-capture-after-cancel.png` is the next complete framebuffer and shows
idle. This uses **synthetic thinking and a USB-injected wheel click**, not a
live Muse upload or a newly filmed physical button press.

USB snapshots capture one instant; they are not continuous video. No external
camera recording was made. Serial transport still takes about 40 seconds at
115200 baud, while the UI remains available after the initial capture/copy.
No credentials, device IDs, signed URLs, or private serial logs are included.
