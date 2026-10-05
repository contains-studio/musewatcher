# HTTPS weather-card delivery evidence

Captured on 2026-10-04 from a physical SenseCAP Watcher. The installed source is
commit `a96a15ad3f178672501664bc8dd0e95cb4f87515`; firmware was built from that
working tree before committing it. Firmware binaries and generated configuration
remain private.

`device-weather.png` is the unedited 412 × 412 framebuffer captured over USB
after the second successful download. The card is the user's existing approved
upload, reused to reproduce and test delivery; its forecast is not a fresh lookup.

`serial.txt` contains filtered, recorded firmware diagnostics. The old firmware
refused the same HTTPS card with 19 KiB internal RAM free and a 12 KiB largest
block. With the fix, both downloads started at that same memory level and
completed in 1,241 ms and 1,034 ms. Both decoded a 24,470-byte JPEG at 412 × 412.
After each transfer, internal free memory returned to 20 KiB; after the repeat,
PSRAM returned to the same 2,919 KiB level. This is a two-download hardware check,
not a long-duration fragmentation test.

The source uses PSRAM for the worker stack on supported full-UI builds while
retaining the TLS budget, internal reserve, contiguous cleanup-task reserve,
runtime memory floor, deadline, and redirect scheme checks. Four host harness
configurations exercise the measured pressure, refusals, allocation failures,
cleanup, redirects and deadlines. The final host suite passed 327 tests with
3 skips, and both Watcher and AIPI firmware builds passed.

No continuous hardware video was captured: that revision's USB framebuffer
capture paused the main UI task for roughly 40 seconds, and no external camera was available.
The newer background transfer is documented in [native weather evidence](../native-weather/README.md).
The screenshot and recorded serial workflow provide the hardware evidence.
The cancellation video elsewhere in this PR demonstrates the separate wheel
control change; it is not evidence of this HTTPS download.
