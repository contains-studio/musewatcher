---
name: muse-weather-display
description: Select a weather outfit and prepare or send a pixel weather card to a Muse-connected SenseCAP Watcher. Use for a requested weather card or daily weather delivery.
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
time, temperature basis, and selected outfit ID alongside the generated card.
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

## Compose the card

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

## Send and verify

For a requested send or scheduled occurrence, use the selected outfit for both
the card and the saved idle animation:

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

Record the actual command results. A successful render or upload is not proof
that the device displayed the card. A command timeout leaves delivery uncertain;
inspect available device evidence before resending. Use a screenshot when
available, and distinguish a device capture from a local preview. A USB capture
may wake the screen and pauses drawing during transfer. The device needs power
and network access for scheduled delivery; sleep can interrupt its connection.
