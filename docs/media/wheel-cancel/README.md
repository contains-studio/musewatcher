# Wheel cancellation evidence

These captures use the production LVGL UI in the desktop simulator at the
Watcher's 412 × 412 resolution. The waiting state, reply text, voice service,
and wheel input are simulated. No voice message or photo was sent to Muse.

- `thinking.png`: the contextual **Press wheel to cancel** hint.
- `cancelled.png`: the same UI after one wheel press, back at idle.
- `photo-cancel-hint.png`: the hint remains readable over a simulated photo
  during upload, including before any wheel camera interaction.
- `wheel-cancel.mp4`: five seconds of rendered UI at 20 fps, enlarged 2× with
  nearest-neighbor scaling. A synthetic wheel press occurs at two seconds.
  Simulator time is fixed; this is not a recording of physical hardware.

Generate the source frames from the wheel integration test:

```sh
MUSE_WHEEL_VIDEO=1 python3 esp32/simulator/tests/test_wheel_navigation.py \
  --build build/simulator --out /tmp/wheel-cancel
```

The separate `esp32/tests/test_muse_voice_cancel.py` host tests execute the
production cancellation, upload, and reply loops against fake transports.
They verify stream closure, discard instead of retry, callback completion,
microphone-off behavior, and isolation from subsequent turns.
