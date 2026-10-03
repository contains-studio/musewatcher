# Changes in the Muse Watcher fork

Based on `facebookincubator/muse-gadget-sdk` revision `b9008abba7dc4109c66212b9b82e459d08b98b85`. Upstream copyright and license notices are retained.

- Reworked the Watcher home, response reader, settings access, touch gestures, and wheel navigation.
- Added camera preview, frozen-frame review, explicit send/retake/cancel, and reliable camera frame handling.
- Added microphone/speaker settings and coordinated recording cancellation across input sources.
- Preserved the latest completed reply and image card while the device remains running.
- Added thirteen weather outfits, persistent outfit selection, weather scenery, and an excited tap pose for each outfit.
- Added default-avatar ambient scenes and a configurable device time zone.
- Switched off the Watcher's persistent RGB indicator during startup.
- Added a secure setup helper, paced flashing and backup commands, robust USB screenshots, artwork tools, a local weather-card renderer, and a configurable Muse skill.
- Added host and simulator regression coverage, setup instructions, engineering notes, and documented visual evidence.

This fork's own additions are licensed under Apache 2.0 to the extent controlled by its contributors. The upstream character/artwork exclusion and third-party licenses still apply. See [LICENSE](LICENSE) and [the artwork notice](assets/weather/NOTICE.md).
