# Hardware list and Pi bring-up guide

This is the shopping list and the step-by-step for moving from the simulator to real
hardware on the boat. It assumes you've read the relevant sections of [README.md](README.md)
for the *why* and the wiring detail; this file is the *what to buy* and *what order to do
it in*. Nothing here is final until you've soldered it — treat part numbers as a strong
starting point, not gospel, and check current price/stock before ordering.


> **Engine data without a converter:** instead of the CX5003, the [passive-tap sensor board](SENSOR_BOARD.md) reads fuel, trim, oil, battery and RPM straight off the existing gauges' wires while they keep working. The KiCad design and ready-to-order fabrication files are in [hardware/sensor-board/](hardware/sensor-board/); it has not been built and proven on the boat yet.

## Parts list

| # | Part | Why | Rough price |
| - | --- | --- | --- |
| 1 | Raspberry Pi 4 or 5 (4 GB+), power supply, microSD (32 GB+) or SSD | Runs the dashboard | $60-100 |
| 2 | [Orient Display 10.1" 1280x800, HDMI/USB](https://www.orientdisplay.com/products/10-1-raspberry-pi-tft-with-pcap1280x800-900-nits-hdmi-usb-interface/) (`AFY1280800A2-10.1INTH-C-HDMI`) | The screen | ~$180 |
| 3 | Matsutec CX5003 analog-to-NMEA 2000 converter | Reads your MerCruiser's tach, trim, oil, and (once you add one) a coolant-temp sender | ~$130 |
| 4 | A NMEA 2000 interface for the Pi — **built into the [sensor board](SENSOR_BOARD.md)** (rev 1.1: isolated, J5 takes a drop cable), or a PiCAN-M / MacArthur HAT (see README's "The NMEA 2000 backbone" section) | Lets the Pi join the CAN bus | included / $60-100 |
| 5 | NMEA 2000 backbone parts: a power tee, 2 terminators (120 Ω), 3-5 A fuse, T-connectors and drop cables for the CX5003, the Fusion, the Pi's HAT and the depth transducer | Ties everything together | $60-100 |
| 6 | Depth transducer: **Airmar DST800PV-N2** (plastic, thru-hull, NMEA 2000 native) — see "Choosing a transducer" below | Makes the Depth box real | $370-410 |
| 7 | VDO-type engine temperature sender (301-22 Ω, matches the CX5003's temperature input) | Coolant temperature, since the boat has none | $15-30 |
| 8 | ADS1115 board (I2C 16-bit ADC) + a 47 kΩ / 10 kΩ resistor pair | Battery voltage (`BOAT_BATTERY_ADC=true`) | $10 |
| 9 | PCA9685 PWM board + 3 logic-level N-channel MOSFETs (e.g. IRLZ44N) + gate resistors/pulldowns -- or, with the sensor board, the [LED board](LED_BOARD.md) (4 RGBW zones + 4 addressable strips, 20 A, every output fused) | Drives the 12 V strips | $15-25 |
| 10 | GPS receiver (u-blox NEO-6M/NEO-M8N or similar, UART or USB) | Position and heading | $15-30 |
| 11 | Fuses, tinned marine wire, connectors, a project box, a 12 V-to-5 V converter for the Pi (3 A+, with output overvoltage protection; with the sensor board it plugs into the board's J7 and powers the Pi through the header) | Wiring and enclosure | $40-80 |

That's roughly **$950-1200** all-in, dominated by the transducer. If that's too much for a first
pass, skip #6 and #7 for now — everything else (engine data, lights, GPS, media) works and is
testable without them, and Depth/coolant just show `--` until you add them.

### Choosing a transducer

**[Airmar DST800PV-N2](https://www.airmar.com/Product/DST800P-2000)** (depth, speed and
water temperature in one housing, native NMEA 2000, ~$370-410): this is the recommendation.
It needs a **thru-hull install** — one hole in the hull, below the waterline, which normally
means hauling the boat out. In exchange: it's a well-proven part with a huge install base,
it's *retractable* (the sensor pulls up from inside the hull on its valve for cleaning or
replacement without hauling out again), and it gives water temperature too, which the boat
otherwise has no good source for. Prices and part-number suffixes vary a bit by retailer
(`DST800PV-N2` / `DST800P-2000` both show up for the plastic NMEA 2000 version); confirm
the listing says **NMEA 2000**, not **NMEA 0183** — Airmar sells both, and only the 2000
version talks to this dashboard directly.

**If you'd rather not haul out and drill a hole:** an *in-hull* depth transducer (a puck
epoxied to the inside of a solid fiberglass hull, reading through the glass) needs no hole,
but only works reliably on a solid (non-cored) hull, gives depth only (no speed or
temperature — you already have GPS speed, so that's a smaller loss), and typically comes as
a plain analog/pulse sender needing a separate NMEA 2000 converter box, the same pattern as
the CX5003 for engine data. This dashboard's depth code (PGN 128267) doesn't care which kind
of transducer feeds it, so either route works; this option just hasn't been priced out or
tested here the way the DST800 has.

Either way, mount it where it won't cavitate (bubbles under the boat at speed) — away from
strakes, steps and the propeller — and forward enough to read at speed, not right at the
transom.

## Setting it up: do this in order

Bring hardware up **one piece at a time**, checking the dashboard after each step, rather
than wiring everything and hoping. Every step below has something you can look at to confirm
it worked before moving to the next.

### 1. The Pi, headless, with the software already proven

1. Flash Raspberry Pi OS (64-bit, Bookworm or newer) to the SD card / SSD. Enable SSH and
   Wi-Fi in the imager's advanced options so you don't need a monitor yet.
2. Boot it, SSH in, then:
   ```bash
   sudo apt update && sudo apt install -y python3-venv git
   git clone <your fork or a copy of this repo> boat-dashboard
   cd boat-dashboard
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8090
   ```
3. From your phone or laptop on the same network, open `http://<the Pi's address>:8090`.
   You should see the full dashboard running on **simulators** — a moving boat, engine
   gauges, lights you can tap. This proves the Pi, Python, and the app itself all work,
   with zero hardware attached yet. **Don't move on until this works.**
4. `Ctrl-C` it for now; it comes back permanently in the last step below.

### 2. The screen

1. Connect the display's HDMI and USB (touch) to the Pi, and its 12 V power to a bench
   supply or the boat's battery through a fuse.
2. Re-run the `uvicorn` command from step 1 and confirm the dashboard shows on the display
   itself, and that touch works (tap a screen thumbnail in the Home overlay, drag the
   brightness slider). If touch is inverted or offset, that's an OS display-rotation/touch
   calibration issue, not this app.

### 3. Engine data (the CX5003)

This is the biggest wiring job, so it gets its own careful pass. Full wiring, DIP-switch and
calibration detail is in README's ["Engine data over NMEA 2000: the CX5003"](README.md#engine-data-over-nmea-2000-the-cx5003-boat_sensorsn2k)
section — this is the short version of the order to do it in:

1. Build the NMEA 2000 backbone (item #5): power tee, a terminator at each end, T-connectors.
   Power it from switched 12 V through its own fuse.
2. Wire the CX5003 to the backbone and to your senders (tach off the coil-negative wire, the
   fuel and trim senders, and the new engine-temperature sender). Set its speed-ratio DIP
   switches for a 4-cylinder 4-stroke gasoline engine (ratio 2) and, for the fuel sender's
   "switch" wire, connect it to ground for a US-standard 240-33 Ω sender.
3. Set up the Pi's NMEA 2000 interface (item #4; for the sensor board's, see SENSOR_BOARD.md's
   "NMEA 2000" section) and bring the interface up
   (`sudo ip link set can0 up type can bitrate 250000 restart-ms 100`; `candump can0` should show frames
   once the CX5003 has power).
4. On the Pi:
   ```bash
   export BOAT_SENSORS=n2k
   export BOAT_CAN=can0
   ```
   Restart the app and open `/calibrate`. You should see "Engine data from device ..." with
   live RPM, trim, oil and temperature. If RPM reads roughly double or half the analog tach,
   fix the DIP switches rather than the calibration scale.
5. Calibrate trim (drive fully down, then fully up), oil (type your sender's full-scale psi),
   and the tach (type what the analog tach shows), all from `/calibrate`.

### 4. Battery voltage

Wire the ADS1115 (item #8) as described in README's sensor-wiring table, set
`BOAT_BATTERY_ADC=true`, and check `/calibrate` shows a sensible voltage; type what a
multimeter shows to calibrate it.

### 5. Depth transducer

1. Install the transducer (see "Choosing a transducer" above) and wire it to the same NMEA
   2000 backbone as the CX5003.
2. Restart the app (still `BOAT_SENSORS=n2k`) and check `/calibrate` — you should see
   "Depth from device ..." and, if the transducer sends it, "Sea temperature from device
   ...". The Depth box on the Helm and Chart screens should stop showing `--`.
3. If the depth reads off by a fixed amount (common — the transducer's own offset is often
   left at its factory default), type the true depth from a lead line or a marked piling
   into the Depth card's "true depth" field; that's a *correction on top of* whatever the
   transducer itself reports, so you don't need to reconfigure the transducer.

### 6. RGB lights

Wire the PCA9685 + MOSFETs (item #9) as in README's "RGB lighting" section, set
`BOAT_LED_DRIVER=pwm`, and check that tapping a color swatch actually changes the strip.

### 7. GPS

Wire the GPS receiver (item #10), set `BOAT_GPS_PORT` (and `BOAT_GPS_BAUD` if it's not
9600), and confirm the boat icon moves to your real position instead of the simulated lake
route.

### 8. Fusion stereo

Wire the RA210 onto the same NMEA 2000 backbone (README's "The NMEA 2000 backbone" section
already covers this alongside the CX5003) and confirm the Media screen finds it.

### 9. Make it permanent

Once everything above works from a manual `uvicorn` command, set up the systemd service and
Chromium kiosk autostart from README's ["Run it on the Pi at boot"](README.md#run-it-on-the-pi-at-boot)
section, with all the `export`s from the steps above moved into its `boat.env` file.

## After it's running

- Go through **every** card on `/calibrate` once more with the engine actually running and
  the boat actually on the water — numbers that looked right on the bench can still be off
  (a coolant sender reading warm at idle in your driveway, for instance).
- Set your alarm levels (hold a gauge, or **Options → Alarms**) — the defaults are
  reasonable guesses, not tuned to your engine.
- If the RPM needle looks too jumpy or too sluggish once it's reading a real, noisy tach
  signal instead of the smooth simulator, hold either RPM gauge and adjust "Needle response".
