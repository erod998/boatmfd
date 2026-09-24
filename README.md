# Boat Dashboard — Proof of Concept

A DIY Garmin-style MFD for a 7" 16:9 landscape display, restyled after the
look of Garmin's GPSMAP touchscreen chartplotters (near-black panes, round
segmented dials that fill blue, outlined data boxes, a black menu bar with a
Home button) and built for big touch targets while driving the boat.

**Moving to real hardware?** [HARDWARE.md](HARDWARE.md) has the parts list, a picked depth
transducer, and a step-by-step order to bring it up on the boat.

**Screens** (tap **Home** on the menu bar, or the small thumbnails either side
of it, to switch):

| Screen | What it shows |
| --- | --- |
| **Helm** (default) | Everything at once: GPS speed and RPM dials plus coolant / oil / battery / fuel (with estimated GPH and MPG) / trim bars on top, the chart with big Depth and Water Temp boxes in the middle, and trip, Fusion stereo and RGB lights below |
| **Nav. Chart** | Full-screen chart with Speed / Heading / Depth / Water Temp data boxes, zoom buttons, the boat, its heading line and track, an optional compass rose, and waypoint navigation |
| **Gauges** | Garmin-style engine and vessel dials: coolant, oil, battery, fuel, trim, RPM, GPS speed, fuel flow, plus fuel economy / remaining / water temp boxes |
| **Trip** | Start/stop trip logging: distance, time, average and max speed, fuel used and economy, and the saved-trip list |
| **Media** | Fusion stereo over NMEA 2000: cover art, source chips, big transport buttons, a Master volume that moves every zone together (keeping their relative mix), and a volume bar for each zone (with its own mute), plus a mute for everything, plus a gear icon for RPM Volume Boost |
| **RGB Lights** | Twelve colour presets, rainbow, power, and a segmented brightness bar |
| **Switching** | A grid of simulated digital-switching circuits (nav lights, anchor light, cabin lights, livewell, bilge pump, two accessories, horn) — no real switching module, just remembers on/off |

**Also reachable from Options** (not full screens, but full features): **Waypoints, Routes &
Boundaries** (mark/save/rename/delete waypoints, search saved waypoints by name and go straight
to one — like a GPS — build and follow multi-leg routes, circular geofence alarms), **Navigation
Alarms** (Arrival, Off Course, Anchor Drag, GPS Accuracy), **AIS Targets** (a live list of
simulated nearby vessels with range/bearing/CPA), and **Pinned Screens** (choose which screens
show in the Home overlay's Pinned tab and the prev/next swipe at the bottom of every screen — at
least one has to stay pinned).

**Home** is a translucent overlay like Garmin's: a clock (tap **Home** any time to check
it), a row of screen thumbnails above category tabs (Pinned, Charts, Combo, Vessel, Media,
Lights). The menu bar also has **Mark** (drop a waypoint at the boat), **Info** (data sources,
position, GPS status), **Day/Night** chart mode, **Alerts** (the alarms and
warnings below, and a missing GPS fix; the icon turns amber/red with a count)
and **Options** (units, alarms, chart orientation, map layers & colors, centre chart on
boat, waypoints, calibration). Tap the speed gauge to switch between mph and knots. The
chart defaults to **Old Hickory Lake, TN (37075)**, and has the three orientations a GPSMAP
does: **North Up** (default), **Heading Up** (the bow points to the top of the screen) and
**Course Up** (the *active leg* points to the top). Course Up is the one worth knowing about —
Heading Up re-aims the chart with every wiggle of the boat, which at idle is a constant slow
swim, while Course Up holds the leg still and lets the boat icon swing against it, so the angle
between the icon and straight-up is exactly how much you are crabbing off the track. With no Go
To or route running it falls back to course over ground, then heading, so it is never stuck
pointing somewhere stale. The chart button beside the zoom controls is labelled with the current
mode (**N↑**, **H↑**, **C↑**) and switches in one press between North Up and whichever rotating
mode you last chose; **Options → Chart orientation** cycles through all three. (It used to cycle
all three itself, and since Heading Up and Course Up look alike on a straight run, getting back
to North Up took two presses.)

**Following the boat**: the chart keeps the boat centred (North Up) or a third of the way up the
screen (Heading and Course Up) until you drag it. Then it stays where you put it, and a blue
**Center** button appears above the ruler; tap it to go back to the boat. Pinching or the +/−
buttons zoom without letting go of the boat.

**The chart cursor** — what a tap on the chart does, the way a GPSMAP does it: it drops a cursor
and does nothing else yet. The bar that appears says how far away it is and in which direction,
its position, and what is charted there (the depth band, or a bridge, dam or caution area — tap
that line for the full details); **Go To** navigates there, **Save Waypoint** keeps it. A tap used
to start a Go To immediately, and since a Go To and a route are mutually exclusive, one stray tap
in a chop cancelled an active route. Tapping an aid to navigation, mile marker, landmark or
hazard point identifies it instead, with its own Go To. Only those point features take a tap;
land, depth areas and shorelines used to as well, which between them cover the whole chart, so
every tap opened "Land" and the chart could not be tapped for anything else at all.

**Go To follows the channel** (auto guidance): a Go To is planned over the water, preferring the
Corps' recommended track down the channel, and leaves it only to reach a destination off it -- up a
creek, into a marina. The route is drawn as a dashed line and steered leg by leg: bearing, course
and cross-track error are for the current leg (so Course Up turns with the channel), distance and
time to go are along the rest of the route. With no chart or no way through the water it falls
back to a straight line and says so. Saved routes (Route To) still run straight between their
waypoints, as they do on a GPSMAP.

**Look-ahead**: in North Up the boat sits a third of the way in from the edge it is coming from,
so two thirds of the screen is the water ahead -- the bottom third heading north, the left third
heading east. It follows the course over the ground above 2 kn.

**Measure Distance** (the ruler button beside the zoom controls): the reference starts at the
boat and follows it; each tap on the chart moves the far end, and the range and bearing read out
at the top of the chart. **Set Ref** pins the reference to that point instead, to measure between
two arbitrary points; **From Boat** puts it back. (Built for a finger: an earlier version relied on
mouse hover and read 0.00 on a touchscreen.)

**Range Rings** (**Options → Map layers & colors → My Vessel**): four concentric circles around
the boat at a spacing you pick, the outermost labelled with its radius, so distance to anything
on screen can be eyeballed without measuring it. Drawn in real distance rather than screen
pixels, so they scale with zoom the way the chart does.

The boat's position and heading are eased continuously (every animation frame, not once a
second when a GPS fix arrives) — the same idea as the spring-smoothed gauges, but for a
wrapped compass angle and a lat/lon position: `setBoatTarget()`/`boatTick()` in app.js. This
drives the boat icon, the chart's own bearing in Heading Up (which turns the whole chart so
the boat's current heading points to the top of the screen, the boat icon then always
pointing straight up), and the position lock that keeps the boat horizontally centered and
about a third of the way up from the bottom of the map in Heading Up — a single cheap
`panBy` every frame, not a `setView`, so it holds through the whole rotation instead of only
correcting once a second. The chart itself is vector data held in memory (see **Charts**), so
panning and rotating never wait on anything to load and never show a blank square — the old
raster-tile black-square problem went away with the tiles.

**Map layers & colors** (**Options → Map layers & colors**, or the app/gps.py and
app/state.py simulators' more realistic noise): chart colors are Day/Dusk/Night — three real
palettes applied to the chart vectors themselves, not a filter smeared over an image, so the
boat, track and AIS targets keep their own colours while only the chart changes;
**Chart** layer toggles (Land & shoreline, Depths & contours, Aids to navigation, Hazards &
caution areas, Landmarks) add and remove the matching vector layers on the spot, with no
refetching — the data is already local; **My Vessel** has a Heading Line
(projects ahead of the boat by a fixed distance or by how far it'll travel in a set time at
its current speed — Distance/Time, matching a real GPSMAP's own Heading Line options) and a
Compass Rose (a fixed-size ring around the boat marked N/E/S/W — left un-rotated in CSS on
purpose, since leaflet-rotate spins the whole marker pane with the chart, the same reason the
boat icon has to counter-rotate to stay pointing up in Heading Up); **User Data** can hide the
recorded track, clear it, name and **save** the current breadcrumb trail as a permanent record
(kept in `data/tracks.json`, up to the same ~600-point/~10-minute window the live trail itself
keeps — a real chartplotter's active-track memory has the same kind of limit), and lists every
saved track — tap one to draw it on the chart in green, tap again to hide it, tap the pencil to
rename it in place, tap the arrow to drop a waypoint back at its starting point (retracing it, the
way you'd want to find your way back to the dock after an out-and-back cruise), or delete it. All
of this is a per-browser preference (localStorage) except clearing the track, and saved tracks
themselves, which live on the server (`/api/track`, `/api/tracks`).

**Data overlays** (the Speed/Heading/Depth/... boxes on the Helm and Nav. Chart screens): **hold**
any box (same gesture, and the same fill-as-you-hold feedback, as the RPM display and alarm
menus) to swap in a different field, matching a real GPSMAP's "Edit Overlays". There are **36
fields in seven groups**, and the picker shows the group headings because a flat list of that
many is not something anyone navigates while driving:

| Group | Fields |
| --- | --- |
| **Navigation** | Bearing, Course, Distance, Time To Dest, Arrival Time, VMG, Cross Track, Turn, Destination |
| **Vessel** | Speed, Heading, Course Over Ground, Position, GPS Accuracy, Satellites |
| **Depth & Water** | Depth, Water Temp |
| **Engine** | RPM, Engine Temp, Oil Pressure, Trim, Battery |
| **Fuel** | Fuel Level, Fuel Remaining, Fuel Rate, Fuel Economy, Range |
| **Trip** | Trip Distance, Trip Time, Average Speed, Max Speed, Trip Fuel Used |
| **Time** | Time of Day, Sunrise, Sunset, Moon Phase |

The Navigation group needs a Go To or a route running and reads `--` otherwise, the Trip group
needs a trip started, and everything reads `--` without a GPS fix — which is what a real unit
does rather than showing a stale or invented number. **VMG** is velocity made good: how fast the
distance to the destination is actually shrinking, which is only the same as speed when steering
straight at it. **Time To Dest** is computed from VMG rather than speed for exactly that reason
— sliding sideways past a waypoint at 20 kn is not arriving at 20 kn — and reads `--` when VMG
has gone negative, because a boat pointed away is not arriving at all. **Fuel Remaining**,
**Economy** and **Range** need the tank size (`BOAT_FUEL_CAPACITY_GAL`, default 40).
Which fields are picked, per screen, is a per-browser preference (localStorage);
the Chart screen defaults to Speed/Heading/Depth/Water Temp and the Helm screen (which already
shows speed via its own dial) to Depth/Water Temp, same as before this was made editable.
Sunrise, sunset and moon phase come from `app/celestial.py` — sunrise/sunset from the standard
astronomical "sunrise equation" using the boat's own position, moon phase from the days elapsed
since a known new moon (the same for every observer, so it only needs a date) — both accurate to
civil (not celestial-navigation) precision, no network call or API key, and (deliberately) not
tide/current predictions or moonrise/moonset times, which either wouldn't mean anything on a
freshwater lake (tide/current) or need meaningfully more work to compute well (the moon's real
position, not just its phase angle) than this dashboard's use case calls for.

**Alarms:** hold your finger on a gauge for about half a second (the gauge
fills as you hold) to open its alarm menu: coolant / engine temperature, oil
pressure, battery voltage (a low and a high alarm), fuel level, or the Depth
box. **Options → Alarms** lists them all. Each has an on/off switch and a
level you set with the big −/+ buttons or by tapping the bar; a small bell
marks every gauge that has an alarm on, and the gauge's red and amber bands
move to match the level. Coolant and engine temperature are the same reading
here (one engine-temperature gauge). Details are in "Alarms" below.

**Waypoints, Routes & Boundaries** (**Options → Waypoints, Routes & Boundaries**): mark the
boat's current position as a named, saved waypoint (separate from the single "Go To" target the
chart cursor sets); go to one, rename it in place, or delete it. Build a **route** by
checking two or more saved waypoints in the order you want to visit them — following one steers
leg to leg and auto-advances to the next point on arrival (within ~300 ft), the same way a single
Go To does, and the two are mutually exclusive (starting a route stops a Go To and vice versa).
**Boundaries** are a circular geofence around a point — pick a radius and whether the alarm fires
on entering, exiting, or both — for a no-wake zone, a marina, water you want a heads-up about
either way. Only circles, not the arbitrary polygons a real Garmin supports: a circle is a tap
and a radius, a polygon needs collecting an ordered ring of points on a touchscreen, and circles
already cover "stay near/away from this spot."

**Navigation Alarms** (**Options → Navigation Alarms**): **Arrival** (nearing the active
waypoint or route leg, on by default), **Off Course** (strayed too far from the direct line, by
cross-track error), **GPS Accuracy** (the fix's own reported HDOP has gotten too poor to trust),
and **Anchor Drag** — drop a reference point at the current position and get alarmed if the boat
drifts past a set radius from it, checked against the real (or simulated) GPS fix every second,
the same way a real chartplotter's anchor watch works. All four, plus boundary crossings, show up
in the same Alerts panel and banner as the engine/boat alarms; unlike those, they don't have an
"acknowledge" — they clear on their own once the condition does (back on course, anchor
re-settled), except a boundary crossing, which is a one-time event held in the alert list for 8
seconds so it doesn't disappear before the once-a-second Alerts badge or banner even shows it.

**Digital Switching** (the **Switching** screen): eight named on/off circuits (nav lights, anchor
light, cabin lights, livewell, bilge pump, two accessories, horn) standing in for a real NMEA
2000 digital-switching module (a CZone/Empirbus-style relay box) — there isn't one wired up, so
each circuit just remembers its own state, the same simulate-what-there's-no-hardware-for
approach as the rest of this dashboard.

**AIS Targets** (**Options → AIS Targets**, and small teal boat icons on the chart itself): a
handful of other vessels moving nearby, each with a name, range, bearing, speed and a simple
straight-line closest-point-of-approach (CPA distance and time until it, when the target is
actually closing) — there's no real AIS receiver, so `app/ais.py` simulates a few boats wandering
around the chart's default position, the same demo-with-no-hardware idea as the simulated GPS and
engine. Sorted nearest first, like a real MARPA/AIS target list ranks by how much it matters.

**Smooth gauges:** engine and boat readings arrive five times a second (GPS,
trip, stereo and lights once a second), and every dial, bar and number glides
to each new reading on a critically damped spring instead of jumping, so
needles start and stop gently and never overshoot. How quickly a needle
follows a new reading is itself adjustable: hold either RPM gauge (Helm or
Gauges screen) to open "Engine RPM display" and pick from Very smooth to
Snappy — useful once a real, noisier tach signal replaces the simulator's.
No amount of easing can fully hide noise with a genuinely slow, drifting
component (a spring only trades lag for smoothness, it can't remove a real
random walk), so the simulated RPM and GPS heading in app/state.py and
app/gps.py were also tuned down to something closer to a real steady-cruise
signal — they were originally noisy enough that even "Very smooth" still
visibly shook.

Engine and battery readings are read-only, like real NMEA 2000 sensors. The
layout scales with screen height and was checked at 800x480, 1024x600,
1280x720, 1280x800 (the 10.1" panel below) and 1920x1080.

Runs today against built-in **simulators** (GPS, engine, stereo, LEDs) so the
whole thing is demoable with zero hardware attached. Swapping in real
hardware is a config change, not a rewrite — see "Going to real hardware".

## The reference build

| Job | Part | Connects with |
| --- | --- | --- |
| Brain | Raspberry Pi 4 or 5 | Runs the server and the browser (Chromium kiosk) |
| Screen | Orient Display 10.1" 1280x800, 900 nit, capacitive touch, **HDMI/USB version** (see "The screen") | HDMI + USB touch + 12 V |
| Engine data | Matsutec **CX5003** analog-to-NMEA 2000 converter | senders on one side, NMEA 2000 on the other |
| Stereo | Fusion MS-RA210 / MS-WB675 | NMEA 2000 |
| NMEA 2000 interface | CAN HAT for the Pi (PiCAN-M, MacArthur HAT, or an isolated MCP2515 HAT) or a USB CAN adapter | `can0`, 250 kbit/s |
| Fuel flow (optional) | NMEA 2000 fuel-flow sensor | NMEA 2000 |
| Lights | 12 V PWM RGB strips: PCA9685 board + 3 logic-level MOSFETs | I2C |
| Position | GPS receiver | USB or UART |
| Battery voltage | ADS1115 board + two resistors (or the converter's alternator volts) | I2C |

```
 senders (tach, oil, temp, trim, fuel) --> CX5003 --+
 Fusion MS-RA210 / MS-WB675 ------------------------+--- NMEA 2000 backbone (250 kbit/s, a terminator
 fuel-flow sensor (optional) -----------------------+    at each end, 12 V power tee)
                                                    |
                                     CAN HAT or USB CAN adapter
                                                    |
 GPS -------------- USB or UART ---------------+    |
 battery ------- ADS1115 ---- I2C -------------+--- Raspberry Pi --- HDMI + USB touch ---> 10.1" screen
 12 V RGB strips <-- MOSFETs <-- PCA9685 <-- I2C +
```

**ESP32** is not a fit for serving the chart UI, but it makes an excellent
*lighting node* for addressable strips:
[`esp32/boat_rgb_node/`](esp32/boat_rgb_node/boat_rgb_node.ino) is an
Arduino sketch that drives a WS2812B strip over WiFi with the same
`/api/lighting` shape this dashboard uses (solid color + rainbow).

## Run it (simulator mode, no hardware)

```bash
python -m venv venv
venv/Scripts/pip install -r requirements.txt      # Windows
# venv/bin/pip install -r requirements.txt         # macOS/Linux
venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8090
```

Open `http://localhost:8090` (the layout scales with screen height, so it
works from 1024x600 up to 1080p). The simulated boat cruises up and down
the Old Hickory Lake channel near Hendersonville (edit `ROUTE` in
[`app/gps.py`](app/gps.py) to move it). Tap the chart to drop the cursor, press **Go To**, and
watch bearing/distance/ETA/cross-track-error update live (**Options →
Waypoint / go to…** shows them). Use **Night** on the menu bar, tap a color
swatch to light the (virtual) LED strip, and try the Media screen (a simulated
stereo, badge says DEMO). On a desktop browser the screen is sized by window
height, so resize to 1024x600 to see it as it will look on the boat.

Tests (protocol layer, including a fake stereo on a virtual CAN bus):

```bash
venv/Scripts/pip install python-can
venv/Scripts/python -m unittest discover -s tests -t . -v
```

## Charts

The chart is **vector data drawn by this app**, not pictures downloaded from a
map server — the same thing a real chartplotter does, and the reason it works
with no internet, stays sharp at any zoom, restyles for night instantly, and
can tell you what a buoy is when you tap it.

Source is **USACE Inland ENC** (`ienccloud.us`, the Corps of Engineers' inland
waterway charts — free, no key, no account). Their map service exposes every
S-57 feature class as a queryable layer, so the fetch pulls real GeoJSON:
depth areas with their actual depth ranges in metres, the shoreline, the
navigable channel, and every buoy, beacon and light with full attribution.

For the whole of Old Hickory Lake — Old Hickory Dam (river mile 216) up to Cordell Hull Dam at
Carthage (mile 313), creek arms included — that is **about 9,000 features and 8 MB, in about 107
requests, one every 3 seconds: 6–7 minutes**, once, until you choose to refresh it. (The first
version covered only the lower lake and stopped at Gallatin.) The service publishes no rate
limit and is a shared government server, so the fetcher never bursts; see
`REQUEST_DELAY_S` in `app/chart_data.py`.

### Getting the charts

Run this **on the Pi**, over WiFi at the dock:

```bash
venv/bin/python -m app.fetch_charts old-hickory --dry-run   # coverage check first
venv/bin/python -m app.fetch_charts old-hickory              # then for real
venv/bin/python -m app.fetch_charts --name my-lake --bbox -86.62,36.24,-86.44,36.36
```

Charts land in `data/charts/<area>/` as GeoJSON, two levels of detail (the
zoomed-out one is generalised server-side, which turns 2.1 MB of raw shoreline
into 207 KB without any visible difference at that zoom).

**`data/charts/` is committed**, so Old Hickory arrives with a plain `git pull`
and you only need the command above for a new area or a refresh. Everything else
under `data/` — tracks, waypoints, trips, settings — stays gitignored, since
that is per-boat state rather than project source. Charts are the exception
because the Pi cannot re-create them for itself: it is offline, and
`fetch_charts.py` needs the internet.

USACE reissues IENCs bi-monthly, so re-running this occasionally at the dock is
worth doing. It simply overwrites what's there.

### What you get on screen

Land and shoreline; creeks and marina basins as water; depth-shaded water, with the
**surveyed depths** (below) over the channel; the recommended track; bridges, the dam and lock;
marinas, docks and piers; restricted areas and no-wake notice signs; caution areas and hazards;
roads and railroads on land; and the aids to navigation on top. **Names** print the way a chart
prints them — creeks and bays in italic blue, islands and bends, towns, marinas, bridges and river
miles — upright even in Heading Up, and only as many as fit without overlapping. Detail comes in
as you zoom: the whole lake at once is the shoreline, the channel and the town names, and buoys,
mile markers, hazards and docks appear as you zoom in. Every group can be hidden under
**Options → Map layers & colors**. Tap any of them:

| Tap | It says |
| --- | --- |
| A beacon | `Spencer Creek Light (236.8)` — Starboard-Hand Lateral Mark, Red |
| A light | `Fl(2)R 5s` — built from the S-57 characteristic, group and period |
| The chart, in the surveyed channel | `Depth 18.2 ft, surveyed 2019` — then the charted area below it |
| A charted depth area | `0 ft to 9 ft` outside the channel, `9 ft or more` inside it |
| A marina | its name, and what kind of facility it is |
| A distance mark | `Mile 225`, Cumberland (CR) |

Two things were wrong in how the chart drew before this version and are worth knowing about if
you remember the old one: creeks and marina basins were filled in the **land** colour (Station
Camp Creek, Cedar Creek and every marina looked like dry ground), and in **Heading Up the chart
did not turn** — the boat, track and labels did, while the chart under them stayed north-up and
drew in the wrong place.

### Surveyed depths

The Corps' chart only knows two depths on Old Hickory: **0–9 ft** outside the navigation channel
and **9 ft or more** inside it. That is what the chart is for — the maintained 9-ft channel — and
it is why tapping the chart used to say 0–9 ft or nothing.

The Corps does survey the actual bottom. Its Nashville District runs condition surveys of the
channel and publishes every one through **eHydro** — around 9,000 soundings per mile, across the
old river bed from bank to bank. `app/fetch_depths.py` downloads them and turns them into a depth
grid (25 ft cells, keeping the shallowest sounding in each, a newer survey replacing an older
one) plus the surveyors' own printed soundings:

```bash
venv/bin/python -m app.fetch_depths old-hickory --dry-run   # list the surveys
venv/bin/python -m app.fetch_depths old-hickory              # ~18 downloads, one every 5 s
```

On screen: the channel shaded by depth (darker is shallower, as on a paper chart; the colour key
is under Map layers & colors), depth numbers once zoomed in, and the cursor reading the real
depth wherever you tap in a surveyed stretch. The result is `data/charts/old-hickory/survey_depth.json`
(680 KB, committed with the charts); the downloads are cached in `data/survey-cache/` so a re-run
fetches only new surveys.

**What it covers**: river miles **216–225** (Old Hickory Dam up past Hermitage, surveyed 2018–19)
and **297–313** (the upper lake below Cordell Hull Dam, 2016–17) — the channel, not the coves,
flats or creek arms, and not the middle of the lake between those two stretches. No public data
covers those; Quickdraw, once a transducer is fitted, fills them in from your own sounder.

**Lake level**: the surveys record bottom *elevations* (feet above NAVD88), so a depth is the lake
level minus the bottom — and Old Hickory moves between 442 and 445 ft. **Options → Map layers &
colors → Lake level** sets it (445 ft, normal summer pool, by default), the way a Garmin with
lake charts does; every screen shares the setting. Set it to the day's level, which the Corps
posts daily, and the chart agrees with the sounder.

Day / Dusk / Night are real palettes applied to the vectors, not a CSS filter
smeared over an image — so the boat, track and AIS targets keep their own
colours while only the chart changes.

### Coverage: what this data does and does not include

IENC charts the **commercially-navigable federal waterway system** — the
Cumberland (including Old Hickory Lake), Tennessee, Ohio, Mississippi and
similar. Those are covered properly, to real chart standard.

Lakes off that system may have **nothing at all**. Center Hill Lake was checked
three ways — the IENC vector service, the IENC S-57 cell list, and USACE's
separate eHydro hydrographic survey archive — and has zero USACE data in any of
them. That is a genuine gap in the free federal data, not a bug here.
`--dry-run` reports the feature count before downloading, so an uncovered area
is obvious in seconds.

Closing that gap means commercial data (Navionics, C-MAP and the like, which is
what apps such as Savvy Navvy license). That data is not redistributable into a
project like this one without its own licence, and the terms are not something
to assume — it would need checking with the provider directly, and would be a
separate data pipeline rather than a drop-in. The other route, once a
transducer is fitted, is recording your own depths with Quickdraw, which builds
a personal depth map of exactly the water you actually run.

### Why it is not raster tiles any more

The first version cached rendered tile images. Replacing it with vectors was
not a tuning change, it was a correction:

| | Raster tiles | Vectors (now) |
| --- | --- | --- |
| Requests for one lake | ~4,500, rate-limited | **~60, once** |
| Time | ~3.75 hours | **36 seconds** |
| On disk | 88 MB | **2.1 MB** |
| Zoom | fixed 11–15, blurry between | **any zoom, always sharp** |
| Night mode | CSS filter over the image | **a real palette** |
| Tap a feature | nothing | **full chart attributes** |
| Load with no internet | only pre-cached area | **everything fetched** |

## Going to real hardware

**GPS** (e.g. u-blox NEO-6M/NEO-M8N on the Pi's UART or a USB adapter):

```bash
venv/Scripts/pip install pyserial pynmea2
set BOAT_GPS_PORT=COM5          # or /dev/serial0 on the Pi
set BOAT_GPS_BAUD=9600
```

With `BOAT_GPS_PORT` set, [`app/gps.py`](app/gps.py) uses `SerialGPS`
(real NMEA GGA/RMC/VTG parsing) instead of the simulator. If the port can't
be opened it shows NO FIX rather than a made-up position. Put the antenna
where it sees the sky.

**When hardware is configured, the dashboard never shows made-up numbers:**
a sensor that isn't wired, or has failed, shows `--`. Depth is `--` until you
add a depth transducer, and water temperature is `--` until you assign a
water probe.

### The screen

The listing for the [10.1" Sunlight Readable IPS, 1280x800, 900 nits, capacitive touch panel](https://orientdisplay.com/products/10-1-sunlight-readable-ips-1280x800-900-nits-with-capacitive-touch-panel/)
(`AFY1280800A1-10.1INTH-C`, about $142) is a **bare LVDS panel**: it has no
HDMI input, its touch panel needs its own controller, and its 3.3 V logic and
LED backlight expect a driver board. A Raspberry Pi can't drive it directly.
Orient sells the same panel with the driver board attached as the
[10.1" Raspberry Pi TFT with PCAP, HDMI/USB interface](https://www.orientdisplay.com/products/10-1-raspberry-pi-tft-with-pcap1280x800-900-nits-hdmi-usb-interface/)
(`AFY1280800A2-10.1INTH-C-HDMI`, about $180, listed with HDMI video, USB touch and a
12 V supply). Buy that one. Keeping the bare panel would mean sourcing an
HDMI-to-LVDS board that matches its pinout and backlight, plus a touch
controller: doable, but a project of its own.

- The panel is 16:10, not 16:9. Nothing in the layout assumes 16:9.
- Feed it fused, switched 12 V. The listing gives no current draw, so
  budget generously until you measure it.
- 900 nits is blinding at night. **Day/Night** only changes the chart colors.
  Ask Orient whether the driver board has a backlight dimming input: a
  dimmable backlight is the real fix, and the PCA9685 has spare outputs.
- USB touch works without drivers; there is nothing to calibrate.

### Engine data over NMEA 2000: the CX5003 (`BOAT_SENSORS=n2k`)

The Matsutec **CX5003** ([manual](https://www.scribd.com/document/1074807900/Matsutec-CX5003-Dual-NMEA-2000-converter-user-manual))
reads the boat's analog senders and puts them on the NMEA 2000 bus, so the Pi
needs no ADC, tach circuit or temperature probe for the engine. It sends PGN
127488 (RPM, trim), 127489 (oil pressure, temperature) and 127505 (fuel,
fresh-water and black-water levels), which the app decodes.

| Input | What the manual says | For this boat |
| --- | --- | --- |
| RPM | speed-ratio DIP switches (ratio 1-128) | a 4-cylinder, 4-stroke gasoline inboard is **ratio 2**; set the switches as the table on page 2 of the manual shows for ratio 2 |
| Fuel level | 0-190 ohm, or 240-33 ohm when the **"switch" wire is connected to ground** | US-standard sender: connect "switch" to ground |
| Trim | 0-190 ohm | measure the Alpha One sender with a multimeter first; anything above 190 ohm is cut off |
| Oil pressure | 10-184 ohm = 0-10 bar | US-standard 80 psi senders read high; set the sender's psi on `/calibrate` |
| Temperature | 301-22 ohm = 40-120 C (VDO-type sender) | the engine has no coolant sender: fit a VDO-type one (check the thread of your thermostat-housing port), or use the DS18B20 fallback below |
| Power | 9-32 V DC, under 120 mA, IP67 | fused, ignition-switched 12 V |

Things to know:

- **Reviews are mixed** (3.5 of 5 on Amazon, and the manual is thin). Forum
  posters report the *second engine channel* of the dual unit dead on some
  units; you use one engine, but test it on the bench before you install it.
- It does **not** send battery voltage or fuel rate. Battery voltage comes from an
  ADS1115 on the Pi (`BOAT_BATTERY_ADC=true`, wired as in the next section)
  or, if the converter turns out to send alternator volts, from that. Fuel rate
  comes from a fuel-flow sensor, otherwise it is estimated from RPM.
- The manual doesn't say which **instance numbers** it uses, whether the
  temperature is reported as engine or oil temperature, or what it sends for a
  sender that isn't connected. Open `/calibrate`: it lists every value the
  converter sends, with the instance numbers. The Pi treats a zero oil pressure
  or fuel rate as "nothing connected" until it has seen a real nonzero reading,
  so an unwired input shows `--` rather than 0.
- Trim and oil come through as percentages of the converter's range, so
  you calibrate both on `/calibrate` (below).
- If the CX5003 disappoints, any other NMEA 2000 engine converter works the
  same way: the app reads standard PGNs, not anything specific to this device.

On the Pi (the CAN interface is set up in "The NMEA 2000 backbone" below):

```bash
export BOAT_SENSORS=n2k
export BOAT_CAN=can0
export BOAT_N2K_ENGINE_INSTANCE=0        # RPM, trim, oil, temperature: use what /calibrate shows
export BOAT_N2K_FUEL_TANK_INSTANCE=0     # fuel level
export BOAT_BATTERY_ADC=true             # only if you fitted the ADS1115 for the battery
export BOAT_REDLINE_RPM=4800             # your engine's maximum WOT RPM
export BOAT_ENGINE_HP=135 BOAT_ENGINE_WOT_RPM=4600      # only used for the fuel-burn estimate
export BOAT_FUEL_CAPACITY_GAL=40                        # tank size: turns fuel % into gallons, economy and range
# export BOAT_N2K_TEMP=oil               # if the converter reports its temperature as oil temperature
```

**Calibrate** from a phone at the boat: `http://<pi-address>:8090/calibrate`.

1. Check that the converter appears under "NMEA 2000" with the instance number
   in use, and that RPM, oil and temperature look plausible.
2. **Trim:** run the drive fully down and save, then fully up and save.
3. **Oil:** type your sender's full-scale psi (80 for US-standard). Check it
   against a mechanical gauge if you can.
4. **Fuel level:** works as it arrives; save the readings when the tank is truly
   empty and truly full if it never quite reads 0 and 100.
5. **RPM:** with the engine running, type what the analog tach shows. The DIP
   switches must already be right; this only corrects small errors.
6. **Battery:** type what a multimeter shows.
7. At each fill-up, type the gallons pumped (see "Fuel" below).

Values are saved to `data/calibration.json`. A converter that stops
transmitting turns every engine value to `--` within a few seconds; the Info
panel on the dashboard says whether the bus is being heard.

### Alternative: the Pi reads the senders itself (`BOAT_SENSORS=real`)

Skip this section if you use an NMEA 2000 converter.

**Recommended: the passive-tap sensor board** ([SENSOR_BOARD.md](SENSOR_BOARD.md),
`BOAT_SENDER_WIRING=tap`). It only *listens* to the wires the analog gauges already drive, so every
analog gauge stays connected and working -- if the Pi is off, the helm is a normal helm. It reads
fuel, trim, oil, engine temperature, battery and RPM (through an optocoupler), and calibrates from the gauges
themselves: "just filled up", then one mark read off the analog fuel gauge, calibrates the whole
fuel scale. RPM comes from the Delco EST ignition's gray tach wire. The KiCad schematic, board and
ready-to-order fabrication files are in [hardware/sensor-board/](hardware/sensor-board/). The rest of this section is the older **reference** wiring (`BOAT_SENDER_WIRING=reference`,
the default), which takes each sender over and means disconnecting its analog gauge.

Everything on a boat like this is analog, so a small interface board sits between the senders and the Pi.
This was designed for a MerCruiser 3.0 (4-cylinder, distributor ignition,
Alpha One drive) with US-standard resistive senders, but every value is
configurable.

**Parts:** an ADS1115 module (4-channel 16-bit I2C ADC); 1% metal-film
resistors (one 240 ohm per resistive sender, one 47 k and one 10 k for the
battery, two 47 k for the tach); a 3.3 V zener diode; a 100 nF capacitor; a
1 A fuse; DS18B20 waterproof probes (1-2) with one 4.7 k resistor; wire.

| Signal | Wiring |
|---|---|
| Fuel sender (A0) | Pi 3.3 V, then 240 ohm, then a node wired to ADS1115 A0, then the sender, then boat ground. **Skip this** if you use a NMEA 2000 tank-level adapter, and set `BOAT_FUEL_SENDER=false` (an unwired input must never be read as a fuel level) |
| Trim sender (A1) | Same circuit into A1 |
| Oil sender (A3, optional) | Same circuit into A3, and set `BOAT_OIL_SENDER=true` |
| Battery (A2) | Fused 12 V, then 47 k to a node wired to A2; 10 k from the node to ground; 100 nF node to ground |
| Tach | The coil-negative (gray) tach wire, then two 47 k in series, then a node wired to GPIO 17; a 3.3 V zener from the node to ground (cathode on the node) |
| Temperature probes | DS18B20 red to 3.3 V, black to ground, yellow to GPIO 4; 4.7 k from GPIO 4 to 3.3 V. Several probes share the same three wires. (On a MacArthur HAT, use its 1-Wire connector instead: it is GPIO 19 with the pull-up already fitted, and GPIO 4 is the HAT's NMEA 0183 transmit line) |

Read these before wiring:

- **A resistive sender can feed only one circuit.** The dashboard reads each
  sender through its own reference resistor, so **disconnect the sender wire
  from the old analog gauge** (the gauge stops working). Paralleling the two
  circuits shifts both readings. Keep the analog gauge only if you can fit a
  second sender.
- **The tach wire carries ignition spikes.** On a MerCruiser 3.0 the tach
  signal is the coil-negative wire, which spikes to hundreds of volts. Tap it
  where it reaches the back of your existing tach, keep both 47 k resistors in
  series, and before connecting the GPIO put a multimeter on the node with the
  engine running: it should stay at or below about 3.3 V. The old tach can stay
  connected (this tap loads it negligibly). The counter uses one edge per
  spark (2 per revolution on a 4-cylinder distributor engine; `BOAT_TACH_PPR`)
  and ignores the ring-down after each spark, but that filtering is designed
  from how the ignition behaves, not tested on your engine: check idle RPM
  against your analog tach and use the calibration page to correct it.
- **Alpha One trim sender range:** published values conflict (some sources say
  33-240 ohm, others 0-80 ohm), so nothing is assumed: you measure yours on the
  calibration page. The 240 ohm reference handles anything from 0 to about
  390 ohm.
- **Share one ground** with the boat's DC negative, and keep sender and tach
  wiring away from ignition wires.

On the Pi:

```bash
sudo raspi-config nonint do_i2c 0                       # enable I2C
echo "dtoverlay=w1-gpio" | sudo tee -a /boot/firmware/config.txt   # 1-Wire on GPIO 4, then reboot
                                                                   # (MacArthur HAT: dtoverlay=w1-gpio,gpiopin=19)
sudo apt install python3-lgpio
python -m venv --system-site-packages venv && venv/bin/pip install -r requirements.txt smbus2

export BOAT_SENSORS=real
export BOAT_REDLINE_RPM=4800                            # your engine's maximum WOT RPM
export BOAT_ENGINE_HP=135 BOAT_ENGINE_WOT_RPM=4600      # only used for the fuel-burn estimate
export BOAT_FUEL_CAPACITY_GAL=40                        # tank size: turns fuel % into gallons, economy and range
```

**Calibrate** from a phone at the boat: open `http://<pi-address>:8090/calibrate`.
Live readings update every second. Capture fuel empty/full (defaults are the
US-standard 240/33 ohm), trim fully down/up, type your multimeter's battery
voltage, type your analog tach's RPM while running, assign the temperature
probes (engine / water), and record each fill-up. Values are saved to
`data/calibration.json`.

**Engine temperature:** the boat has no coolant sender, so use a DS18B20
probe strapped to the thermostat housing (thermal paste, then insulate it
from the engine bay air). It reads a little below true coolant temperature;
correct that on the calibration page against an infrared thermometer. A
second probe on the raw-water intake hose gives the water temperature.
Warning colors on the dashboard: amber above 195 F, red above 210 F.

### Fuel: a NMEA 2000 fuel sensor

Both kinds of NMEA 2000 fuel device work, and they share the same powered
backbone and CAN interface as the Fusion stereo (set `BOAT_CAN=can0`; the old
name `BOAT_FUSION_CAN` still works):

- **Fuel-flow sensor** (measures fuel going to the engine and broadcasts fuel
  rate, PGN 127489): GPH is then **measured**, and MPG is GPS speed divided by
  GPH. Install it in the fuel line with the flow arrow pointing the right way and
  a fuel filter ahead of it, and check the sensor's rated flow and line size
  against your engine (for example,
  [Navico's](https://www.svb24.com/en/navico-fuel-flow-transmitter.html) is rated
  0.6-45 GPH on 3/8" lines). A sensor in a supply line only reads net
  consumption if the engine has no fuel return line; fuel-injected engines
  with a return line need a differential kit that outputs the net rate.
- **Tank-level adapter** (converts your existing 240-33 ohm sender to NMEA 2000,
  PGN 127505; the CX5003's fuel input does the same job): fuel level comes from
  the bus and takes priority over the analog sender. The [Maretron TLA100](https://www.maretron.com/products/tla100-tank-level-adapter/)
  does this and, per Maretron, can be used alongside the analog gauge, so with
  that adapter you can keep your old fuel gauge. Set `BOAT_FUEL_SENDER=false` so
  the Pi doesn't also read the sender.
- The `/calibrate` page lists every fuel-flow and tank-level message it hears,
  with the device address and its **instance number**. If yours isn't 0, set
  `BOAT_N2K_ENGINE_INSTANCE` (flow sensor) or `BOAT_N2K_FUEL_TANK_INSTANCE`
  (tank level).
- Messages the device marks "not available", and readings older than 5 seconds,
  count as no data: GPH falls back to the estimate below and the level falls
  back to the analog sender, never to a stale number.

**Without a fuel-flow sensor**, GPH is estimated from RPM (propeller-law
horsepower times a typical fuel consumption) and shown with an "est" tag. With
either kind of GPH, enter the gallons the pump put in at each fill-up on
`/calibrate`: the count of gallons burned since the last fill-up is compared
with what the pump says and the fuel burn is rescaled to match, so it improves
every time.

### Depth: a NMEA 2000 transducer

Depth (and, if the transducer sends it, sea temperature) comes from any NMEA 2000 depth
transducer on the same backbone as the CX5003 and the Fusion stereo — no separate wiring
or converter needed once it's on the bus. [HARDWARE.md](HARDWARE.md) has a specific
transducer picked out (an Airmar DST800, thru-hull) if you're buying one.

- PGN 128267 (Water Depth) gives the raw depth below the transducer and the transducer's
  own configured offset (positive: to the waterline; negative: to the keel) — the two are
  added together for what the dashboard shows.
- PGN 130311 (Environmental Parameters) is filtered to just the "Sea Temperature" reading
  (that PGN is shared with outside air, cabin and other temperatures from other instruments,
  so the rest are ignored) and, if a transducer sends it, wins over the DS18B20 water probe,
  the same "bus beats a local sensor" rule used for engine temperature and fuel level.
- The `/calibrate` page lists what it hears, with the device address, instance number, and
  the raw depth and offset the transducer itself reports. If your transducer is under a
  different instance number, set `BOAT_N2K_DEPTH_INSTANCE` to match.
- If the depth is off by a fixed amount (a transducer's own offset is often left at its
  factory default), type the true depth — from a lead line, a marked dock piling, or a known
  chart depth at your slip — into the Depth card; that's a *further* correction added on top
  of whatever offset the transducer itself reports, so you never need to reconfigure the
  transducer to fix it from here.
- No transducer, or one that's gone quiet for more than a few seconds, means `--`, the same
  no-made-up-data rule as everything else on real hardware.

### RGB lighting

**Recommended for plain 12 V PWM strips: a PCA9685 board and three MOSFETs
next to the Pi** (`BOAT_LED_DRIVER=pwm`, below). The PCA9685 makes steady 12-bit
PWM on its own, so the Pi's load never makes the lights flicker; it works on
any Pi, including the Pi 5; and the strips' current never touches the Pi. Keep
the MOSFETs beside the PCA9685 (I2C and gate wiring are short-range), and run
fused 12 V and the switched color wires out to the strips in tinned marine
wire sized for the strip current (a 5 m, 60 LED/m RGB strip can draw about 6 A
in total, 2 A per color). Fuse each LED circuit and put the electronics in a
sealed box.

**Response time:** the lights change within milliseconds of a tap. A small
background loop in `LightingController` applies every change straight away
(the API also applies it before it answers), keeps the rainbow moving at 30
frames a second, and only writes to the driver when the color actually changed
(it repeats an unchanged color every two seconds, to recover from an I2C glitch).
On the screen, the swatch, power state and preview strip react at the tap,
without waiting for the server, and the brightness slider sends its first change
at once and then at most one update every 60 ms while you drag.

Color presets live in [`app/lighting.py`](app/lighting.py) (`COLOR_PRESETS`);
the UI builds its swatches from `GET /api/lighting/presets`. Pick the driver
that matches your lights:

**Plain 12 V RGB strips (the common 4-wire kind)** — `BOAT_LED_DRIVER=pwm`.
A PCA9685 PWM board on the Pi's I2C bus (address 0x40) drives three
logic-level N-channel MOSFETs (for example IRLZ44N, or a 3-channel MOSFET
board rated above your strip's current). Wire the strip's shared `+12V`
wire to fused 12 V; each color wire (R, G, B) to a MOSFET drain; the sources to
ground; the gates to PCA9685 outputs 0, 1 and 2 (`BOAT_LED_PWM_CHANNELS`) with
a 100 ohm series resistor and a 10 k pulldown each. This assumes a
**common-anode** strip (the shared wire is +12 V), which is how nearly all
4-wire strips are made; if yours is common-cathode this circuit won't work.
All LEDs show one color at a time.

**Addressable WS2812B strips** — `BOAT_LED_DRIVER=ws281x` (needs `rpi_ws281x`
and root), with `BOAT_LED_COUNT` and `BOAT_LED_GPIO`.

**With the sensor board**, the LED electronics go on a separate LED board that
plugs into the sensor board's J6 (LIGHTS) connector: an I2C bus of its own for
PCA9685s (16 PWM channels each, as many as needed -- four RGBW strips per chip),
GPIO 18 and 19 for two independent addressable strips, a spare GPIO, 3.3 V logic
power and ground. The strips' power never goes through it. See SENSOR_BOARD.md, "Lights
connector (J6)".

### The NMEA 2000 backbone (CX5003, Fusion stereo and the Pi)

The CX5003, the Fusion stereo (MS-RA210 or MS-WB675 — same protocol either way) and the Pi all share one NMEA 2000 backbone,
and Fusion-Link control over NMEA 2000 needs a **powered** network. The stereo
does not power the bus, so:

1. Build a small backbone: a power tee fed from switched 12 V through a 3-5 A
   fuse, a terminator (120 ohm) at each end, and T-connectors with a drop cable
   to the CX5003, the stereo and the Pi (and the fuel-flow sensor, if any).
   Power the bus from exactly one place. The CX5003 has its own red/black
   power wires as well as the "Micro-C" bus connector.
2. Give the Pi a CAN interface. Any of these gives you a `can0`:
   - **The sensor board** ([SENSOR_BOARD.md](SENSOR_BOARD.md), rev 1.1 on): it has an isolated
     NMEA 2000 interface built in -- a drop cable into J5, and the network side is powered from
     the backbone and isolated from the Pi, like any NMEA 2000 device. Nothing else to buy.
   - **[PiCAN-M](https://copperhilltech.com/pican-m-nmea-0183-nmea-2000-hat-for-raspberry-pi/)**:
     a Pi HAT built for NMEA 2000, with a Micro-C connector for a
     plain drop cable and an optional 3 A supply that runs the Pi from 12 V. The
     simplest route. Feed that supply from the boat's 12 V, not from the
     NMEA 2000 bus: a Pi draws more than a bus should carry.
   - The **[MacArthur HAT](https://github.com/OpenMarine/MacArthur-HAT)** (open
     hardware, EUR 62 from OpenMarine before tax and shipping): an MCP251xFD CAN controller, so it
     appears as an ordinary `can0`, plus a 1-Wire connector with its pull-up
     fitted, a Qwiic I2C connector, two NMEA 0183 inputs and two outputs
     (a route to a depth sounder) and an optional 12 V to 5 V power module.
     Three things to know. It has **screw terminals**, not an M12 connector, so
     you cut a drop cable and follow the wiring diagrams in
     [its NMEA 2000 chapter](https://macarthur-hat-documentation.readthedocs.io/en/latest/nmea2000.html).
     Its CAN side is **not isolated**: the Pi and the NMEA 2000 network must be
     powered from the same battery, and the HAT's ground goes on first (see
     [its safety note](https://macarthur-hat-documentation.readthedocs.io/en/latest/#safety)).
     And its documentation assumes OpenPlotter, so on plain Raspberry Pi OS you
     set it up yourself (below). Its power module's clean-shutdown handshake is
     configured by OpenPlotter's settings app; a forum poster found that the bare
     `gpio-shutdown` and `gpio-poweroff` overlays cut power at once, so plan on
     a separate 5 V supply unless you install that app for it. Power the Pi from
     the module or from USB-C, never both.
   - An **isolated MCP2515 HAT** such as Waveshare's 2-CH CAN HAT: cheaper, screw
     terminals, so you cut a Micro-C drop cable and wire CAN_H, CAN_L and the
     HAT's isolated ground (**not** the bus's 12 V).
   - A **USB CAN adapter** (a candleLight-firmware one appears as `can0` with no
     overlay to edit); prefer an isolated one.

   Turn the HAT's own 120 ohm termination off unless it sits at the end of
   the backbone (the backbone terminators do that job). With the power off,
   measure between CAN-H and CAN-L on the backbone: 60 ohm means both
   terminators are present, 120 ohm means one is missing, about 40 ohm means
   there is one too many.
3. Enable the HAT in `/boot/firmware/config.txt` (`dtparam=spi=on` and the
   `dtoverlay=...` line for its CAN chip from your HAT's documentation), reboot,
   then bring the interface up at N2K's 250 kbit/s and check that the devices
   are talking before starting the app. Examples:

   | HAT | `config.txt` lines |
   | --- | --- |
   | Sensor board, J5 (SPI0 CE0, GPIO 25) | `dtparam=spi=on` and `dtoverlay=mcp251xfd,spi0-0,oscillator=40000000,interrupt=25` |
   | Waveshare 2-CH CAN HAT (`can0`) | `dtparam=spi=on` and `dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=23` |
   | MacArthur HAT (SPI0 CE1, GPIO 25) | `dtparam=spi=on` and `dtoverlay=mcp251xfd,spi0-1,oscillator=20000000,interrupt=25` (the settings OpenPlotter's CAN app uses for it) |

   After a reboot, `dmesg | grep -i mcp` should show the controller, and
   `ip link` should list `can0`.

```bash
sudo apt install can-utils
sudo ip link set can0 up type can bitrate 250000 restart-ms 100
candump can0                          # power the stereo and the CX5003 on: you should see frames
pip install python-can
export BOAT_CAN=can0                  # shared by the engine converter, the Fusion stereo and the fuel sensor
export BOAT_FUSION_MAX_VOLUME=24      # volume steps your head unit uses (check the manual)
export BOAT_FUSION_ZONES=4            # zones the stereo has -- this boat's head unit is a 4-zone model
```

The Pi claims an NMEA 2000 address (default 42, `BOAT_N2K_ADDRESS`),
discovers the stereo from its status broadcasts, and the Media card shows
what's playing and controls play/pause/next/prev, source, mute, power and
volume. If the CAN interface can't be opened the Media card says "No stereo
found" instead of pretending.

**Zones:** each zone has its own volume. On the Media screen every zone is a
row with the name you gave it on the stereo (otherwise "Zone 1", "Zone 2"), a
speaker button that mutes just that zone, and its own −/+ and volume bar. On
the Helm screen, numbered chips choose which zone the one volume bar controls.
The speaker button beside the transport buttons (or the Helm volume row) mutes
everything at once, which is the stereo's own mute. The stereo has no
per-zone mute, so muting a zone sets its volume to 0 and remembers the level to
return to; if the volume is changed on the stereo itself in the meantime, that
memory is dropped. Each zone's volume bar stops at the volume limit set on the
stereo for that zone.

The zone number in the volume command counts from 0 (Zone 1 is 0, Zone 2 is 1),
although the stereo's screen counts from 1; the code converts. That follows
canboat's captures and the signalk-fusion-stereo plugin, which sends the zone
number minus one. An earlier version of this dashboard sent Zone 1 as 1, which,
going by those sources, would have been Zone 2.

**RPM Volume Boost:** the gear icon next to the power button on the Media
screen opens a panel that turns the stereo up as RPM climbs toward
`BOAT_REDLINE_RPM`, to stay ahead of engine and wind noise, and eases it back
down toward idle — the marine version of a car stereo's speed-compensated
volume. Three settings, all saved server-side (`data/volume_boost.json`, so
every screen agrees): an on/off toggle, **Boost at full RPM** (how many
percent louder than what you set the volume to, applied at redline), and
**Smoothing** (how gradually the boost follows RPM changes instead of
chasing every flicker — 0% is instant, 100% is about a 4-second ease). It
boosts every zone from whatever you last set that zone's volume to, so
turning the volume up or down by hand while boosted just moves the baseline
it boosts from. Implemented in `app/volume_boost.py`, ticked once a second
from the full-frame loop.

**Mute stereo on alarms (TelMute):** when a new NMEA-sourced alarm fires
(coolant, oil pressure, battery, fuel, depth — see "Alarms" below), the
dashboard sends the stereo's ordinary mute command for a few seconds so the
alarm is actually heard over music, then un-mutes on its own; a second alarm
before that window ends extends it instead of double-muting. This is the
same idea as wiring a phone or VHF into a stereo's dedicated TelMute input,
but Fusion has no TelMute message over NMEA 2000 — the regular mute command
(the same one behind the dashboard's own Mute button) has the same audible
effect without extra wiring. Toggle it at **Options → Mute stereo on
alarms** (on by default; the setting is saved server-side too). Implemented
in `app/telemute.py`, ticked five times a second alongside alarm evaluation.

**Album art:** Fusion stereos don't send cover art over NMEA 2000, so the
dashboard looks it up by artist/album from Apple's free iTunes Search API
(cached, on a background thread; needs internet). That sends the track's
artist and album names to Apple. Set `BOAT_ALBUM_ART=false` to turn it off;
with no art (offline, or no match) the UI draws a generated cover instead.

**Read this before trusting it:** the Fusion messages come from
[canboat's](https://github.com/canboat/canboat) reverse-engineered PGN
definitions (proprietary PGN 126720 commands, PGN 130820 status), not an
official Garmin spec. The code is verified against those definitions by unit
tests and a virtual CAN bus with a fake stereo — **it has not been tested
against a real head unit.** Expect to adjust things like the volume range
(unit-specific) and the number of zones once it's on the boat.

### Alarms

Hold a gauge (see the top of this file) to set its alarm. What you can set,
and where each starts:

| Alarm | Fires when the reading | Starts at | Warning (amber) from |
| --- | --- | --- | --- |
| Coolant / engine temp | rises to the level | 210 F, on | 15 F below |
| Oil pressure | falls to the level | 10 psi, on | 10 psi above |
| Battery voltage low | falls to the level | 11.8 V, on | 0.3 V above |
| Battery voltage high | rises to the level | 14.8 V, on | 0.3 V below |
| Fuel level | falls to the level | 10 %, on | 15 % above |
| Depth | falls to the level | 5 ft, **off** | 3 ft above |

The first five start on, at the same levels as the fixed red alerts the dashboard
had before, but they now also raise the banner and the beep described below.
Depth starts off: it needs a depth sounder, and there is none yet.

How they behave (all decided on the server, in [`app/alarms.py`](app/alarms.py),
so the Helm screen and a phone always agree):

- The reading must stay past the level for a few seconds (3 s for temperature
  and oil, 5 s for battery and fuel, 2 s for depth) before the alarm fires, so a
  starter-motor voltage dip or a sounder dropout doesn't set it off. It clears
  when the reading recovers by a small margin, so it doesn't flicker.
- **No data never alarms:** a sensor that isn't there, or a converter that has
  gone quiet, is not a reading. Oil pressure is only watched once the engine
  has been running for 5 seconds, because it takes a moment to build.
- **...and never clears one either.** An alarm already sounding stays up when
  its sensor stops reporting, with the banner saying so ("no reading from the
  sensor", and the last value it had), until real data shows recovery. A depth
  sounder loses the bottom in very shallow water, and an overheating engine can
  burn through its sender wire -- the moments an alarm must not quietly vanish,
  which it used to. Silence still silences it; switching that alarm off clears it.
- When an alarm fires, a red **banner** appears above the menu bar with the
  message and a **Silence** button, the gauge flashes red, and a double beep
  sounds once a second. Silence stops the banner and the beep for everything
  alarming at that moment; the alarm stays in the Alerts list, marked
  "silenced", until the reading recovers. A different alarm later needs its own
  Silence. Warnings are amber, silent, and appear only in the Alerts list and
  on the gauge.
- Levels, on/off and the sound switch are saved in `data/alarms.json`, so they
  survive a restart. Changing a level starts that alarm afresh against the new
  level.

**Sound** is generated in the browser (Web Audio, two alternating tones), so it comes out of
whatever device is actually showing the dashboard: the Pi's own audio (HDMI, a USB speaker, or
the 3.5 mm jack on a Pi 4 -- the Pi 5 has no jack) if it's running in kiosk mode on the boat, or
that device's own speaker if it's a phone or tablet loading the dashboard over the boat's WiFi.
Browsers only allow sound after the page has been touched once; the kiosk command below adds
`--autoplay-policy=no-user-gesture-required` so the Pi's own kiosk browser never hits this, but a
phone or tablet still needs one tap anywhere first -- a "Tap anywhere to enable alarm sound" hint
appears a couple seconds after load if it hasn't happened yet, and disappears on the first tap.
The **Test sound** button in the alarm menu plays the pattern so you can check the volume. The
banner and flashing work without any sound, on every device, regardless of the above.

### Run it on the Pi at boot

A systemd service for the server, with the settings from the sections above in
`boat.env` (one `NAME=value` per line):

```ini
# /etc/systemd/system/boat-dashboard.service
[Unit]
Description=Boat dashboard
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/boat-dashboard
EnvironmentFile=/home/pi/boat-dashboard/boat.env
ExecStartPre=+/bin/sh -c 'ip link set can0 down; ip link set can0 up type can bitrate 250000 restart-ms 100'
ExecStart=/home/pi/boat-dashboard/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8090
Restart=always

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl enable --now boat-dashboard`. To pick up new code later,
from an SSH session on the Pi itself: `./update.sh` (pulls, then restarts the
service -- one-time `chmod +x update.sh` first, or just `bash update.sh`).

**Full screen on the Pi's own display** ([kiosk/](kiosk/)), from an SSH session on the Pi:

- `kiosk/start-kiosk.sh` opens the dashboard in Chromium's kiosk mode on the display, now
  (`--stop` closes it). It finds the desktop session itself, so it works over SSH, and first
  shows a "Starting up..." page that switches to the dashboard as soon as the server answers.
- `kiosk/install-autostart.sh`, once: the Pi logs straight in to a **kiosk session** of its own
  (`kiosk-session.sh`) -- none of the Pi desktop, only the splash and the dashboard, which reopens
  itself if it's closed or crashes. `--desktop` points the auto-login back at the normal desktop
  (with the dashboard opened over it); `--undo`, the plain desktop.
- `kiosk/quiet-boot.sh`, once, then `sudo reboot`: no rainbow square, logos or boot text -- a
  black screen that says "Starting up...", the same as the kiosk's first page, until the
  dashboard appears. Every boot file it touches is backed up first; `--undo` puts them back.
- `kiosk/slim-down.sh`, once, then `sudo reboot`: turns off what a boat dashboard doesn't use --
  cloud-init, waiting for WiFi at boot, remote desktop, automatic updates (an update cut short by
  the power switch can leave the Pi unbootable), printing, Bluetooth, NFS. Disabled, not
  uninstalled, and recorded: `--undo` turns back on exactly those. It saves boot time, not CPU:
  measured, Chromium drawing the dashboard is nearly all of the Pi's CPU.
- `kiosk/network-later.sh`, once, then `sudo reboot`: the dashboard first, the network after.
  NetworkManager takes ~16 s to start on the Pi and the display waits for it; now it starts the
  moment the display is up instead, with a timer that starts it 60 s after boot regardless, so
  WiFi and SSH always come back. `--undo`.

## Architecture

```
app/
  main.py      FastAPI app: REST endpoints + /ws/telemetry WebSocket (engine/boat/alarms 5x a second, everything else 1x)
  alarms.py    User-set alarms: levels, delay, hysteresis, warnings, silencing, saved to data/alarms.json
  gps.py       GPS source: SimulatedGPS (default) or SerialGPS (real NMEA)
  nav.py       Great-circle bearing/distance/ETA/cross-track-error math
  lighting.py  RGB controller with its own 30 Hz loop: color presets, rainbow; MockDriver, WS281xDriver or PCA9685 PWM
  sensors.py   Sensor hub: engine data from NMEA 2000 or from senders/tach/temp probes on the Pi, fuel-burn estimate, calibration
  media.py     Stereo control: SimulatedMedia (default) or FusionMedia (real)
  fusion.py    Fusion PGN encoders + status parsers (canboat layouts), including zone volumes, limits and names
  volume_boost.py  RPM-linked stereo volume boost: boost %, smoothing, saved to data/volume_boost.json
  telemute.py  Mutes the stereo for a few seconds when a new NMEA alarm fires, saved to data/telemute.json
  artwork.py   Cached background album-art lookup (iTunes Search API)
  n2k.py       Minimal shared NMEA 2000 node: CAN IDs, fast-packet, address claim
  n2k_engine.py  Engine (127488, 127489) and tank level (127505) decoders + latest-value store
  n2k_env.py   Depth (128267) and sea-temperature (130311) decoders + latest-value store
  state.py     Settings (env vars) + the simulated engine/battery/depth/water-temp
  trips.py     Start/stop trip logging, saved to data/trips.json
  tracks.py    Saved chart tracks (name, save, list, delete), saved to data/tracks.json
  celestial.py Sunrise/sunset/moon phase from lat/lon/date: the "sunrise equation", no network call
  waypoints.py Saved waypoint list (name, save, rename, move, delete), saved to data/waypoints.json
  routes.py    Multi-leg routes: save/list/rename/delete, plus following one leg-by-leg with auto-advance on arrival
  boundaries.py  Circular geofence alarms (enter/exit/both), saved to data/boundaries.json
  nav_alarms.py  Arrival, Off Course, Anchor Drag, GPS Accuracy alarms
  switching.py  Simulated digital-switching circuits, saved to data/switching.json
  ais.py       Simulated nearby AIS vessels + CPA/TCPA math -- no real AIS receiver
  quickdraw.py Simplified Quickdraw-style depth-sample recording, saved to data/quickdraw.json
  chart_data.py   Local vector chart store: fetches S-57 features from USACE as GeoJSON; see "Charts" above
  fetch_charts.py CLI to download an area's charts over WiFi ahead of time (python -m app.fetch_charts)
static/
  index.html, css/style.css                 The screens, menu bar, Home overlay and side panels
  js/dials.js                               Garmin-style segmented dials and bars (SVG), and the spring animation that smooths every gauge
  js/app.js                                 Chart (Leaflet, drawing local USACE IENC vectors), gauges, trip/media/lights widgets, telemetry rendering
  js/alarms.js                              Hold-a-gauge alarm menus, gauge bands, banner and alarm sound
  js/chrome.js                              Menu bar, Home overlay, screen switching, Alerts / Info / Options panels, WebSocket link
  calibrate.html                         Phone-friendly sensor calibration page (/calibrate)
tests/
  test_media_protocol.py                 N2K framing, Fusion protocol and album-art tests
  test_sensors.py                        Sensor math, tach, probes, fuel burn, calibration, PWM lights
  test_n2k_fuel.py                       NMEA 2000 fuel sensor decoding, priority rules, virtual-bus test
  test_n2k_engine.py                     Engine converter decoding, staleness, unwired inputs, calibration, virtual-bus test
  test_alarms.py                         Alarm thresholds, delay, hysteresis, warnings, silencing, saved settings
  test_lighting.py                       Lights: changes reach the driver at once, rainbow animation, no needless writes
  test_n2k_env.py                        Depth/sea-temperature decoding, offsets, calibration, staleness, virtual-bus test
  test_volume_boost.py                   RPM volume boost math, smoothing, per-zone baselines, saved settings
  test_telemute.py                       Alarm-to-mute timing, extension, manual-unmute handoff, saved settings
  test_tracks.py                         Saved-track save/list/get/rename/delete, generated names, persistence
  test_celestial.py                      Sunrise/sunset and moon-phase invariants (day length, hemispheres, synodic month)
  test_waypoints.py                      Saved waypoints: create/list/rename/move/delete, auto-generated names
  test_routes.py                         Multi-leg routes: save/list/rename/delete, leg-by-leg auto-advance, arrival/finish
  test_boundaries.py                     Circular boundaries: create/list/enable/delete, enter/exit/both crossing detection
  test_switching.py                      Digital switching circuits: defaults, toggle, persistence
  test_ais.py                            Simulated AIS targets: random-walk motion, range/bearing, CPA/TCPA math
  test_quickdraw.py                      Quickdraw depth recording: enable/disable, distance-based dedup, clear
  test_nav_alarms.py                     Navigation alarms: arrival, off course, anchor drag, GPS accuracy
  test_chart_data.py                     Chart query building, paging, atomic writes, per-layer/detail storage, failure handling
esp32/
  boat_rgb_node/boat_rgb_node.ino        Optional WiFi RGB lighting node
```

No build step on the frontend — it's a static page served by FastAPI,
talking to the backend over REST (waypoints, lighting, media, trips) and one
WebSocket (telemetry stream: GPS fix, computed nav, engine, boat info, media
state, LED frame).

### The offline guard

The boat has no internet, so anything the UI loads from a CDN works in testing —
where the machine is online, or the browser still has it cached — and is simply
gone on the water. Leaflet itself shipped that way for a while: every test
passed, the chart drew fine, and a reload out of range would have produced no map
at all, because without `L` the first `L.map()` call throws and takes the whole
chart screen with it.

`app/offline_check.py` scans `static/` for references that need the internet —
HTML `src`/`href`, CSS `url()` and `@import`, and URLs in JS string literals
(comments are skipped, so citing a source in prose is free, and `xmlns` SVG
namespaces are not mistaken for fetches). It runs two places:

- **`tests/test_offline_check.py`** fails the suite. This is the real gate; a
  CDN reference never reaches the Pi.
- **Startup**, which prints `[offline] …` with file and line into the journal
  and carries on. That covers what the test cannot see — a file hand-edited over
  SSH, or restored from an old backup. It deliberately does not refuse to boot:
  a helm display that will not start because of a lint finding is a worse bug
  than the one being guarded against.

Browser libraries therefore live in `static/vendor/`, committed. See
`static/vendor/README.md` for versions and how to upgrade them.

## What's genuinely POC-grade (next steps for a real boat)

- Nav math is great-circle bearing/distance — fine for coastal use, not
  rhumb-line/Mercator-correct for long passages.
- The sensor layer is verified by unit tests and by running the real code
  against fake hardware (a fake ADC, GPIO edge source and 1-Wire directory).
  **It has not been run on a real Pi against real senders**, and the tach
  conditioning circuit and ring-down filter in particular should be checked
  against your analog tach before you trust the RPM.
- The CX5003 support follows its manual and canboat's PGN layouts and is tested
  against a fake converter on a virtual CAN bus, **not against a real CX5003**:
  its instance numbers, its temperature field, its trim scaling and what it
  sends for an unwired sender are all things to confirm on `/calibrate`.
- Fuel GPH is an estimate until a NMEA 2000 fuel-flow sensor is added.
- The depth and sea-temperature decoding (PGN 128267, 130311) follows canboat's PGN layouts
  and is tested against a fake transducer on a virtual CAN bus, **not against a real
  transducer**: confirm its instance number and offset sign on `/calibrate` before trusting it.
- Chart rotation (Heading Up and Course Up) uses [leaflet-rotate](https://github.com/Raruto/leaflet-rotate),
  a third-party Leaflet plugin, not a first-party part of Leaflet itself. It is vendored into
  `static/vendor/` along with Leaflet, because the boat has no internet — see "The offline guard".
  Its bearing convention and click-to-latlng math were checked by hand against its own source
  and by comparing real clicks to their expected position at several bearings; it has not been
  used on a touchscreen on a moving boat.
- The NMEA 2000 fuel decoding follows canboat's PGN layouts and is tested
  against a fake sensor on a virtual CAN bus, not a real fuel sensor. Some
  devices number their instances differently; the calibration page shows what
  the device actually sends.
- The NMEA 2000 node is minimal: no product-information PGN (126996) or
  heartbeat, and it uses manufacturer code 2046 (DIY/unassigned). Some
  chartplotters list unidentified devices oddly.
- The zone byte convention (Zone 1 is 0 on the wire) comes from canboat's
  captures and the signalk-fusion-stereo plugin, not from a real head unit on
  the bench. If Zone 1's dial moves Zone 2 on the boat, that is the first
  thing to look at.
- Alarm sound is generated per-device in the browser (see "Sound" above); depth
  alarms wait for a depth source.
- No auth on the API/WebSocket — fine on an isolated boat WiFi network,
  not fine if exposed further.
