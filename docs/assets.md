# Weather images and animations

The [weather catalog](../assets/weather/catalog.json) contains thirteen outfits
for temperature, precipitation, fog, and wind. Each source is a full-body PNG
with a black background. The regenerated art uses broad color areas without
tile-grid texture, so faces stay clean at the firmware's 72-pixel sprite height.
The [generation prompts](../assets/weather/PROMPTS.md) describe each outfit.
The catalog, PNGs, and
[selection rules](../assets/weather/weather-rules.json) live together in
`assets/weather/`; the skill and firmware tools share that one copy.

Read the [artwork notice](../assets/weather/NOTICE.md) before reusing the character
images. Project-authored weather additions are available under Apache 2.0 to the
extent controlled by the contributors. The upstream character is explicitly
outside the SDK's Apache license, and this project does not grant underlying
Meta character rights. The files are available to inspect, adapt, and build with
subject to that distinction; they are not presented as unrestricted character
artwork.

## Get the files

Clone the project to obtain the originals, rules, source code, and preview tools:

```sh
git clone https://github.com/contains-studio/musewatcher.git
cd musewatcher
```

For a single image and its notice:

```sh
curl -fL https://raw.githubusercontent.com/contains-studio/musewatcher/watcher/assets/weather/hot-shorts.png -o hot-shorts.png
curl -fL https://raw.githubusercontent.com/contains-studio/musewatcher/watcher/assets/weather/NOTICE.md -o weather-artwork-NOTICE.md
```

The stable IDs are also filenames: `mild-knit`, `warm-crochet`, `cool-suede`,
`cold-layers`, `wind-shell`, `rain-shell`, `heatwave-linen`, `freezing-puffer`,
`cold-rain-parka`, `fog-overshirt`, `warm-rain-shell`, `hot-wind-stripes`, and
`hot-shorts`. Add `.png` to obtain an image filename.

## Select an outfit

The chooser uses only Python's standard library. This example supplies local
sample inputs; it does not fetch live weather or contact a device:

```sh
python3 skills/muse-weather-display/scripts/choose_outfit.py <<'JSON'
{"conditions":["clear"],"temperature_c":26,"wind_kmh":8}
JSON
```

It returns `hot-shorts`, its resolved image path, the temperature basis, and the
matching rule number. Feels-like temperature takes priority when present. Rain,
snow, and icy precipitation take priority over dry-weather clothing. A fallback
has `rule_order: null`. Read the rules before changing thresholds; these are
visual styling choices, not official alert definitions.

The script resolves assets relative to its checkout, independent of the shell's
current directory. To move it separately, copy the whole `assets/weather/`
directory and pass its new location explicitly:

```sh
python3 choose_outfit.py --assets-dir ./weather weather.json
```

The [weather skill](../skills/muse-weather-display/SKILL.md) describes card layout,
delivery commands, and a configurable 7 a.m. schedule. It needs a user's location,
time zone, command device ID, and a Muse environment that supports delivery and
scheduling. The local selector alone neither uploads a card nor creates a job.

## Render a weather card

Current Watcher firmware accepts forecast values and an outfit ID through
`display.weather`, then renders the numbers above the live animated character.
Use that path for routine deliveries; the [weather skill](../skills/muse-weather-display/SKILL.md)
describes its fields. It does not need Pillow, a JPEG, an upload, or a download.

The local image renderer remains useful for previews, sharing a standalone
card, and delivery to older firmware. Install Pillow in a local environment for rendering and asset conversion:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install Pillow
.venv/bin/python tools/weather_card.py examples/weather.json --output /tmp/weather-card.jpg
```

The [example JSON](../examples/weather.json) contains **synthetic demo values,
not a live forecast**. Its [sample card](../examples/weather-card.jpg) shows the
output format: a baseline 412×412 JPEG with pixel text above the selected
character. The renderer uses Fahrenheit and mph, converting metric selector
inputs when supplied. It accepts `high_f`, `low_f`, and `humidity_pct` in addition
to the selector's fields, and omits missing measurements. Current temperature is
the headline even when feels-like controls the outfit; if current temperature is
missing and feels-like is available, the headline says “Feels.”

Pass a local JSON file from your weather provider to render a real report. The
tool does not fetch a forecast, upload an image, or communicate with a device.
Its JSON output includes `outfit_id`; pass that same ID to `display.set_outfit`
when delivering the JPEG so the idle character keeps the card's outfit. No
location, date, source line, or condition label is printed on the card. Keep
provider attribution and retrieval time in the workflow's separate run record.

```sh
.venv/bin/python tools/weather_card.py weather.json --output weather-card.jpg
.venv/bin/python -m unittest discover -s tests -p 'test_weather_card.py'
```

## Rebuild sprites and previews

Using the same Pillow environment:

```sh
.venv/bin/python esp32/tools/muse/wardrobe_assets.py \
  --catalog assets/weather/catalog.json \
  --assets assets/weather \
  --output esp32/components/muse/wardrobe_assets.h \
  --preview-dir /tmp/musewatcher-sprites
.venv/bin/python esp32/tools/muse/wardrobe_preview.py \
  --outfit hot-shorts --size 256 --output-dir /tmp/musewatcher-preview
```

The converter isolates the largest connected character component, excludes
separate weather symbols, scales with nearest-neighbor sampling, and writes
RGB565 pixels. The header records source hashes and crop coordinates. The
preview tool compiles the production C renderer, so a C compiler is also needed.
Its images and GIFs use simulated animation time; they are not physical device
recordings. The Watcher's current avatar canvas is 256 pixels inside its
412×412 display.

To customize an outfit, replace its PNG with your artwork while keeping the
catalog ID and filename, then regenerate the header and previews. Keep catalog
order stable: the tap reaction's face and arm anchors correspond to that order.
Changes to character proportions require updating those anchors in
`esp32/components/muse/muse_wardrobe.c` and checking every outfit's idle and
excited states. Preserve the source artwork and edit the generator inputs;
hand-editing the generated pixel array makes the next build harder to reproduce.

The firmware's ambient weather effects follow the selected outfit. They do not
fetch weather themselves. A native weather delivery includes the outfit in
`display.weather`; older image delivery uses `display.set_outfit`. The saved
choice persists when a card is dismissed and across device restarts. Rebuild and flash firmware after changing sprite pixels
or animation code.

Run the portable selection scenarios after editing the rules or catalog:

```sh
python3 -m unittest discover -s skills/muse-weather-display/scripts -p 'test_*.py'
```
