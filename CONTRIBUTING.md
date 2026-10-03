# Contributing to Muse Watcher

This guide was adapted by contains-studio for the Muse Watcher fork.

Create your branch from **`watcher`**, the fork's default branch. Keep fixes small,
add regression coverage for behavior changes, and update the setup guide or
controls documentation when users need to do something differently.

Run the checks relevant to your change in [the README](README.md#development-and-lessons-learned)
and [simulator guide](esp32/simulator/README.md). Firmware changes should build
the SenseCAP Watcher profile; changes to shared code should also build another
board such as AIPI. Report which checks used hardware and which used fixtures.

Every pull request needs **Screenshots** and **Video** sections. Show the actual
changed feature from the reviewed revision. Embed repository-accessible images
and embed or directly link a playable video. Label simulator inputs and edited
timing. For nonvisual work, show the relevant terminal/API workflow when useful;
if meaningful visual evidence is unavailable, explain why in both sections.

Never commit SDK tokens, Wi-Fi credentials, paired-device data, local logs,
factory backups, generated configs, or firmware binaries containing credentials.
Do not publish private device identifiers in issues or captures. The upstream
development signing key is intentionally shared; it is not a production key.

Use this fork's issues for reproducible Watcher bugs. For issues belonging to the
upstream SDK, follow its [contribution process](https://github.com/facebookincubator/muse-gadget-sdk/blob/main/CONTRIBUTING.md),
including its CLA when submitting there. This fork does not collect a separate CLA.

Contributions to this fork use [Apache 2.0](LICENSE). Preserve existing license
notices and mark modified upstream files. The underlying Muse/Jollybot character
has a separate exclusion: see [the artwork notice](assets/weather/NOTICE.md).
