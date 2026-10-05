---
name: muse-weather-display
description: Select a weather outfit and send a native pixel weather card to a Muse-connected SenseCAP Watcher, with image delivery for older firmware. Use for a requested weather card or daily weather delivery.
---

# Muse weather display

Use this skill from a checkout of Muse Watcher. Its single shared asset set is
[assets/weather](../../assets/weather); the images are not duplicated inside the
skill. See [asset usage and customization](../../docs/assets.md) for downloads,
standalone selector usage, and the artwork notice.

## Target and schedule

Resolve the user's **command device ID**, **weather location**, **IANA time zone**,
and preferred **display units** from their request or connected Muse environment.
Use the command ID returned by device discovery, which can differ from the
friendly device name. Do not assume an example device or location belongs to them.

For a requested daily report, use **07:00 in the configured time zone** unless the
user specifies another time. Find a matching existing job before creating one;
save the device, location, time zone, units, and this workflow in its prompt.
Confirm the stored schedule before reporting that it exists. A manual test alone
does not create a schedule. If the target environment has no scheduler, prepare
the prompt and report that scheduling remains unconfigured.

## Select the outfit

Fetch fresh weather for the configured location. Record its source, retrieval
time, temperature basis, and selected outfit ID in the delivery record.
Use current **feels-like temperature**, falling back to **current temperature**;
daily highs/lows and calendar season do not determine the outfit.

From the repository root:

```sh
python3 skills/muse-weather-display/scripts/choose_outfit.py weather.json
```

The selector also reads JSON from stdin. Inputs are `conditions` (a tag or list
of tags), `feels_like_c`, `temperature_c`, `wind_kmh`, and `gust_kmh`. Alternatives
are `feels_like_f`, `temp_f`, `wind_mph`, and `gust_mph`. Missing values can be
omitted or null. Feels-like takes priority over current temperature; metric takes
priority within the same measurement. Example input
`{"conditions":["sunny"],"temperature_c":26,"wind_kmh":8}` selects `hot-shorts`.

The [ordered rules](../../assets/weather/weather-rules.json) take precedence over
visual guesses. Ice, snow, and rain take priority; cold overrides wind. Known dry
conditions at 24°C or warmer use shorts and sandals, with cream fur on the torso
and arms and no top. Otherwise wind, fog, and temperature select the remaining
outfits. Missing data can still match known precipitation or wind; the final
fallback is `mild-knit`. An absent rule number in the selector result identifies
that fallback. Omit unavailable weather numbers rather than inventing them.

## Prefer the native weather command

Discover the target's advertised commands. When `display.weather` is available,
send forecast values and the selected outfit together in one call on the resolved
command device ID. No image render, upload, URL, `set_outfit`, or `show_animation`
call is needed for this path. The firmware draws pixel text above the live,
animated character and saves the selected outfit.

This is a **synthetic example**, not a forecast to send unchanged:

```text
display.weather {"temp_f":79,"outfit_id":"hot-shorts","high_f":83,"low_f":61,"wind_mph":7,"humidity_pct":52}
```

| Field | Requirement |
|---|---|
| `temp_f` | Required current temperature, −238 through 302°F |
| `outfit_id` | Required supported outfit ID returned by the selector |
| `high_f`, `low_f` | Optional temperatures in the same range; high must be at least low when both are supplied |
| `wind_mph` | Optional, 0 through 400 mph |
| `humidity_pct` | Optional, 0 through 100 |

Every supplied measurement must be a finite JSON number. Omit unknown optional
fields; do not send null, numeric strings, invented zeros, source metadata, or
condition labels. Convert provider values to Fahrenheit/mph before sending.
Current firmware displays those units; do not silently substitute them for a
user's explicitly requested units. `temp_f` means current temperature, even when
feels-like determines the outfit. If current temperature is unavailable, report
that the native card cannot be completed rather than labeling feels-like as
current temperature.

The device validates the complete payload before saving the outfit and queuing
the card. It presents the forecast when the home screen can show it without
interrupting another workflow. The saved outfit remains after dismissal and
across restarts. **Latest card** on the wheel recalls the most recent successful
weather or image card until restart. `display.show_animation {}` dismisses a
card while preserving recall; it is not a follow-up to weather delivery.

Use the image path below only when the native command is unavailable on the
target firmware. A native validation error or delivery timeout is not a reason
to upload an image or repeatedly resend. Correct invalid inputs; investigate an
uncertain delivery using the verification guidance below.

## Image path for older firmware

Use the same selected outfit for the rendered card and saved idle animation:

- Produce a **412×412 baseline, non-progressive JPEG** on black. Keep content
  inside the circular display's safe area.
- Put the current temperature at the top, largest, in a readable pixel font;
  follow with high/low, wind, and humidity when supplied. Humidity uses `%`.
- Place the selected character at the bottom with a small margin below the feet.
  Crop unused black padding before fitting the asset; use nearest-neighbor
  scaling. Preserve the outfit, cream fur, and coarse pixel style.
- Keep the visible card free of dates, locations, source lines, timestamps,
  condition labels such as “Fair,” and controls. Store provenance in run metadata.

The [catalog](../../assets/weather/catalog.json) maps each firmware outfit ID to
its source PNG. The selector returns the chosen file's absolute path. Ensure that
the renderer and all assets exist in the environment doing the work; paths from
another computer do not establish availability. The bundled
[renderer](../../tools/weather_card.py) requires Pillow and uses Fahrenheit/mph:

```sh
python3 tools/weather_card.py weather.json --output weather-card.jpg
```

It accepts the selector's fields plus `high_f`, `low_f`, and `humidity_pct`.
It omits missing measurements and reports the selected outfit and displayed
fields as JSON. Current temperature remains the headline when feels-like is used
to select the outfit; if only feels-like is supplied, it is explicitly labeled.
The renderer reads local input and neither fetches weather nor uploads the image.
Inspect its output before delivery. The bundled `examples/weather.json` is
synthetic demonstration data, not a live forecast. Convert the presentation when
the user's requested display units differ from this renderer's Fahrenheit/mph.

For a requested send or scheduled occurrence on older firmware:

1. Upload the completed JPEG through the connected Muse environment's supported
   storage route and obtain a URL the device can retrieve.
2. On the resolved command device ID, invoke
   `display.set_outfit {"outfit_id":"<selected outfit ID>"}`.
3. Invoke `display.show_animation {}` and then
   `display.draw_url {"url":"<uploaded JPEG URL>"}` on the same device.
   Leave the card displayed; dismissing it should reveal the selected outfit.

`display.set_outfit {}` reports the saved outfit. `{"outfit_id":"default"}`
resets it and is not part of ordinary weather delivery. If `set_outfit` is absent,
report that the device firmware lacks wardrobe support. Keep delivery silent.

## Verify delivery

Record the actual command results for either path. A successful render, upload,
or queued command is not proof that the card is visible. A command timeout leaves delivery uncertain;
inspect available device evidence before resending. Use a screenshot when
available, and distinguish a device capture from a local preview. A USB capture
may wake the screen. Current firmware transfers one frozen snapshot in the
background while animation and controls continue; allow roughly 40 seconds at
115200 baud and wait for completion before starting another capture. Older
firmware pauses drawing during transfer. The device needs power and network
access for scheduled delivery; sleep can interrupt its connection.
