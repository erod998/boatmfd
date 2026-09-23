# Sensor board: passive taps on the existing gauges

A Raspberry Pi HAT that reads **fuel level, trim, oil pressure, battery voltage and RPM** by
*listening* to the wires the boat's analog gauges already use. Nothing is disconnected: every
analog gauge stays wired exactly as it is and keeps working, so if the Pi is off or broken the
helm is just a normal helm. It replaces buying an engine-data converter (the CX5003 route).

Built for this boat: a MerCruiser 3.0 4-cylinder with the **Delco EST ignition** (the newer
coil with a 12 V input and a tach output), US-standard resistive senders, and the stock
analog gauges.

- **Design files:** [hardware/sensor-board/](hardware/sensor-board/) -- a KiCad 10 project
  (schematic, board, project footprint library) generated from one Python description.
- **Ready to order:** [hardware/sensor-board/fab/](hardware/sensor-board/fab/) -- Gerbers and
  drill files, BOM, pick-and-place, schematic and assembly PDFs, renders.
- **Software:** `BOAT_SENSORS=real` with `BOAT_SENDER_WIRING=tap`
  ([app/sender_tap.py](app/sender_tap.py) has the maths and why it works;
  [tests/test_sensor_board.py](tests/test_sensor_board.py) runs it end to end against simulated
  gauges).

> **Status: a checked design, not a proven board.** The KiCad files pass KiCad's electrical
> rules check, its design rules check and its schematic-to-board parity check with nothing
> reported at any severity, and the software is tested against simulated gauges. No board has
> been built or connected to this boat yet, so the bring-up section below checks each block with
> a multimeter before it is trusted.

![The board, top side](hardware/sensor-board/fab/sensor-board-angle.png)

---

## How it works

```
               BACK OF THE GAUGES (unchanged)                           SENSOR BOARD (Pi HAT)
  fuel gauge   S ── to the fuel sender ───[10k]── tap wire ── J1.1 FUEL_S ──┐
  trim gauge   S ── to the trim sender ───[10k]── tap wire ── J1.2 TRIM_S ──┤ divider, clamp, filter
  any gauge    I ── ignition +12 V ───────[10k]── tap wire ── J1.4 GAUGE_I ─┤   ADS1115 U1 (0x48)
               G ── ground ────────────────────────────────── J1.7 GND      │     AIN0 fuel   AIN1 trim
  oil gauge    S ── to the oil sender ────[10k]── tap wire ── J1.5 OIL_S ───┤     AIN2 batt   AIN3 gauge supply
  (spare tap)                             [10k]── tap wire ── J1.6 SPARE ───┤   ADS1115 U2 (0x49)
  always-on +12 V (fused 1 A at the source) ───────────────── J1.3 BATT ────┘     AIN0 oil    AIN1 spare
  helm ground ─────────────────────────────────────────────── J1.8 GND

  Delco EST gray tach wire (at the analog tach) ───────────── J2.1 TACH ──── optocoupler ──── GPIO 13
  the analog tach's ground ────────────────────────────────── J2.2 TACH_GND

  DS18B20 probes (engine, water) ──────────────────────────── J3   3V3 / DATA / GND ──────── GPIO 26
```

- **Taps** read the voltage on each gauge's **S** (sender) terminal, and once on the gauges' shared
  **I** (ignition supply) terminal. Each level is worked out from the sender voltage *as a share of
  the gauge supply*, so it doesn't shift when the alternator starts charging. With the key off the
  gauges are dark, and so are these readings ("--").
- The voltage on the S terminal is **not linear** in fuel level: the gauge and the sender form a
  divider. The software models that divider exactly (see app/sender_tap.py), so two calibration
  points anywhere on the scale -- "just filled up", and one mark read off the analog gauge later --
  calibrate the whole range, near-empty included. A straight line would be ~20 points out at half
  a tank.
- The **10 kΩ resistor at the gauge end** of every tap wire is a safety rule, not a nicety: if a
  tap wire ever chafes through to ground or to +12 V, that resistor means the gauge circuit
  can't be disturbed (at most ~1.5 mA flows). Solder it within a few centimetres of the ring
  terminal and cover it in adhesive-lined heatshrink. It is also part of the divider (49 kΩ total
  on top, 10 kΩ below), which the software assumes (`BOAT_TAP_R_TOP=49000`).
- The **tach** input goes through an **optocoupler**, so nothing on the ignition side can reach
  the Pi. It works whichever kind of signal the gray wire turns out to carry: a switched 12 V from
  the EST coil's tach output, or the coil negative itself with spikes of a few hundred volts.
- A tap loads the gauge by about 0.2 mA (59 kΩ from the S terminal to ground). Next to a 33–240 Ω
  sender that is well under half a percent -- the analog gauges read the same as before.

---

## Schematic, block by block

Reference designators match the KiCad schematic
([PDF](hardware/sensor-board/fab/sensor-board-schematic.pdf)). `3V3` and `GND` are the Pi's.

### Tap inputs (×6: fuel, trim, battery, gauge supply, oil, spare)

```
                     at the gauge                             on the board
  gauge terminal ──[ Rg 10k ¼W ]── tap wire ── J1.x ──[ R1 39k 1% 1206 ]──┬──[ R13 1k ]──┬── ADS1115 AINx
                  (in heatshrink)                                          │              │
                                                                  [ R7 10k 1% ]      [ C1 100n ]
                                                                           │              │
                                                                          GND            GND
                                                   D1 BAT54S: clamps the node (R1/R7) between GND and 3V3
```

- **R1/R7 with Rg** divide 0–16 V down to 0–2.7 V (`V_adc = V_tap × 10/59`).
- **D1 (BAT54S)** clamps the divider node between GND and 3V3 so a transient can never push the
  ADC input past its limits; the 49 kΩ in front of it keeps the clamp current to about 2 mA even
  at 100 V.
- **R13/C1** are a noise filter right at the ADC pin (and keep the clamp's forward voltage away from it).
- No TVS diode on these inputs: the 49 kΩ already limits any transient to a couple of milliamps,
  and a TVS's leakage current, flowing through 49 kΩ, would show up as a reading error.
- The top resistors are 1206 for their voltage rating; everything else is 0805.
- Channels, top to bottom on the board: fuel (R1, R7, R13, C1, D1), trim (…2), battery (…3),
  gauge supply (…4), oil (…5), spare (…6).

### Battery voltage (J1.3, U1 AIN2)

Same as a tap input, except the top resistor is a single **47 kΩ 1% 1206** (R3) and there is no
remote resistor -- the feed is fused at its source instead -- giving `V_adc = V_batt × 10/57`. The
software default `BOAT_BATT_R_TOP=47000`, `BOAT_BATT_R_BOTTOM=10000` matches.

### Tach: Delco EST gray wire (optocoupler)

```
  Delco EST gray wire ── J2.1 ──[ R19 3.3k ]──[ R20 3.3k ]──[ R21 3.3k ]──┬─────────┬─────────┬── U3 pin 1 (LED anode)
                                   1206          1206          1206        │         │         │
                                                                     [ C11 10n ] [ D7 ]        │  D7: 1N4148W, cathode
                                                                           │     1N4148W       │  toward pin 1
  analog tach ground ─── J2.2 ─────────────────────────────────────────────┴─────────┴─────────┴── U3 pin 2 (LED cathode)
                          (TACH_GND: its own net -- not the board's GND)

                                              3V3
                                               │
                                          [ R22 10k ]
                                               │
  U3 pin 4 (collector) ────────────────────────┴───[ R23 1k ]─── GPIO 13 (header pin 33)
  U3 pin 3 (emitter) ── GND
```

- Three 1206 resistors in series share an ignition spike (≈130 V each at a 400 V spike, inside a
  1206's rating) and set about 1 mA through the LED at 12 V -- plenty for a PC817 rank B/C.
- **C11** filters the ring-down after each spark; **D7** stops the ring-down's negative swing from
  reverse-biasing the LED.
- **R23** protects the GPIO if it is ever set as an output by mistake.
- The Pi sees one clean rising edge per ignition cycle, which is what the tach code counts: two per
  revolution on the 4-cylinder (`BOAT_TACH_PPR=2`, the default). Set **`BOAT_TACH_PULL=none`**:
  R22 is the pull-up.
- The board draws about 1 mA from the gray wire, next to the analog tach, which stays connected.

### 1-Wire temperature probes

```
  J3.1 ── 3V3
  J3.2 ──┬──[ R25 100 ]──┬── GPIO 26 (header pin 37)
         │               └──[ R24 4.7k ]── 3V3
         └──[ D8 SMAJ5.0A ]── GND          the probe cable runs to the engine bay
  J3.3 ── GND
```

All DS18B20 probes (engine, water) share these three wires.

### Converters and header

```
  ADS1115 U1                              ADS1115 U2
    VDD ── 3V3, C7 100n + C8 1µ to GND      VDD ── 3V3, C9 100n + C10 1µ to GND
    SCL ── GPIO 3 (pin 5)                   SCL ── GPIO 3 (pin 5)
    SDA ── GPIO 2 (pin 3)                   SDA ── GPIO 2 (pin 3)
    ADDR ── GND   -> address 0x48           ADDR ── 3V3   -> address 0x49
    AIN0 fuel  AIN1 trim                    AIN0 oil  AIN1 spare  AIN2-3 not connected
    AIN2 battery  AIN3 gauge supply
    ALERT/RDY: not connected                ALERT/RDY: not connected
```

No I²C pull-ups on the board: the Pi already has them on GPIO 2/3, and a second board stacked on
the header would double them up.

---

## Pi header pins used

| Pin | Signal | Use |
|---|---|---|
| 1, 17 | 3V3 | converters, pull-ups, probes (a few mA in total) |
| 3 | GPIO 2 / SDA | both ADS1115 |
| 5 | GPIO 3 / SCL | both ADS1115 |
| 6, 9, 14, 20, 25, 30, 34, 39 | GND | |
| 33 | GPIO 13 | tach |
| 37 | GPIO 26 | 1-Wire |

Everything else is left free -- GPIO 4 (used for NMEA 0183 by some marine HATs), the SPI bus and
GPIO 25 (used by many CAN HATs for the Fusion stereo over NMEA 2000) included. The header is a
stacking socket, so another HAT can go on top; check its pin list against this one first.

## Connectors

Phoenix Contact **MC 1,5 / 3.81 mm pluggable terminal blocks with screw-locking flanges**: the
plug screws to the header, so it can't walk out on a boat. Each connector's plug overhangs the
board edge.

| Connector | Pin | Signal | Goes to |
|---|---|---|---|
| **J1** helm (8, left edge) | 1 | FUEL_S | fuel gauge **S** terminal, through its 10k |
| | 2 | TRIM_S | trim gauge **S** terminal, through its 10k |
| | 3 | BATT | the helm's always-on +12 V (the feed that powers the Pi), **fused 1 A at the source** |
| | 4 | GAUGE_I | any gauge's **I** terminal (they share the ignition feed), through its 10k |
| | 5 | OIL_S | oil gauge **S** terminal, through its 10k |
| | 6 | SPARE | a spare tap, through its 10k (unused by the software today) |
| | 7 | GND | the tapped gauges' **G** terminal: the taps measure against the gauges' own ground |
| | 8 | GND | helm ground (the battery feed's return) |
| **J2** tach (2, bottom edge) | 1 | TACH | the **Delco EST gray tach wire**, at the analog tach's signal terminal |
| | 2 | TACH_GND | the analog tach's **G** terminal -- twist these two wires together |
| **J3** probes (3, right edge) | 1 / 2 / 3 | 3V3 / DATA / GND | DS18B20 red / yellow / black |
| **J4** | | | the Pi's 40-pin header, underneath |

The silkscreen carries this pinout in a legend block in the bottom-left corner.

---

## Parts list

The full list, with manufacturer part numbers, is
[fab/sensor-board-bom.csv](hardware/sensor-board/fab/sensor-board-bom.csv). In short:

| Ref | Qty | Part | Notes |
|---|---|---|---|
| U1, U2 | 2 | **ADS1115IDGSR** (TI, TSSOP-10) | 0x48 and 0x49 |
| U3 | 1 | **PC817**, rank B or C, SMD gull-wing | tach optocoupler |
| R1, R2, R4–R6 | 5 | 39 kΩ 1% **1206** | tap top resistors |
| R3 | 1 | 47 kΩ 1% **1206** | battery top resistor |
| R7–R12, R22 | 7 | 10 kΩ 1% 0805 | divider bottoms; tach pull-up |
| R13–R18, R23 | 7 | 1 kΩ 0805 | ADC pin series resistors; GPIO protection |
| R19–R21 | 3 | 3.3 kΩ **1206** | tach input, in series |
| R24 / R25 | 1 / 1 | 4.7 kΩ / 100 Ω 0805 | 1-Wire pull-up / series |
| C1–C7, C9 | 8 | 100 nF X7R 0805 | ADC pin filters; decoupling |
| C8, C10 | 2 | 1 µF X7R 0805 | decoupling |
| C11 | 1 | 10 nF X7R 0805 | tach ring-down filter |
| D1–D6 | 6 | BAT54S (SOT-23) | clamps |
| D7 | 1 | 1N4148W (SOD-123) | across the opto LED |
| D8 | 1 | SMAJ5.0A (SMA) | 1-Wire line |
| J1 / J2 / J3 | 1 each | Phoenix MC 1,5/ 8-, 2-, 3-GF-3,81 | plus the matching **MC 1,5/ n-STF-3,81** plugs |
| J4 | 1 | 2×20 female **stacking** header, extra-tall (e.g. Adafruit 1979) | |
| — | 4 | M2.5 standoffs and screws | HAT mounting holes |
| **Rg** | 5 | **10 kΩ ¼ W through-hole** + adhesive-lined heatshrink | at the gauge end of each tap wire (fuel, trim, gauge I, oil, and spare if used) |
| — | 1 | inline fuse holder + 1 A fuse | J1.3 battery feed, at its source |
| — | 1–2 | DS18B20 waterproof probes | engine (on the thermostat housing), water |
| — | | 20–22 AWG tinned marine wire, ring terminals | taps; tach pair twisted |

---

## Ordering the board

The board is a standard 2-layer, 1.6 mm, 65 × 56 mm Pi HAT: 0.2 mm tracks and spacing, 0.3 mm
vias, all within any online fab's standard service.

- **Bare board:** upload `fab/sensor-board-gerbers.zip`. Any colour and finish (HASL lead-free
  is fine; ENIG makes the fine-pitch ADS1115 easier to solder by hand).
- **Assembled (surface-mount parts):** also upload `fab/sensor-board-bom.csv` and
  `fab/sensor-board-cpl.csv` (JLCPCB's column names). Check every part's orientation in the fab's
  placement preview before paying -- fabs' rotation conventions differ from KiCad's for some
  packages, typically SOT-23, TSSOP and the opto's SMD-4. The connectors and the stacking header
  are through-hole: solder those yourself.
- `fab/sensor-board-assembly.pdf` is the placement drawing (references and pin-1 marks);
  `fab/sensor-board-layers.pdf` shows each copper layer.

## Layout

- **Two layers**, a GND pour on both with a stitching via beside every surface-mount ground pad
  -- **except around the tach input**: J2, R19–R21, C11, D7 and U3's pins 1–2 are on their own
  nets (TACH_*) with **at least 3 mm clearance** to everything else and no pour near them. That
  side sees the ignition; no board ground goes anywhere near it. Inside it, the three series
  resistors' nets keep 0.6 mm from each other. Both rules are in `sensor-board.kicad_dru`, so
  KiCad's design rules check enforces them.
- The six input channels run in lanes from J1 on the left to the converters in the middle,
  each with its filter right at the ADC pin; the tach sits in the bottom-right corner, away
  from them.
- **Conformal coat** everything except the connectors and the header after the board passes bring-up.

## Changing the design

Everything in `hardware/sensor-board/` is generated. Edit the Python, not the KiCad files:

| File | What it is |
|---|---|
| `design.py` | every part, its value, footprint, part number, and what each pin connects to -- the one source of truth |
| `schematic.py` | draws the schematic from design.py |
| `board.py` | places the parts, routes the board (with `router.py`), pours ground, writes the design rules |
| `footprints.py` | the project footprint library: KiCad's own resistor, capacitor and SOT-23 footprints with their outline moved off the silkscreen (the board is too dense for it) |
| `build.py` | runs all of the above, then KiCad's ERC, DRC and parity checks, then writes `fab/` |

Rebuild with KiCad's bundled Python (KiCad 10). `build.py` stops before writing `fab/` if any
check reports anything.

```powershell
& "C:\Users\erod9\AppData\Local\Programs\KiCad\10.0\bin\python.exe" hardware\sensor-board\build.py
```

---

## Wiring at the gauges

1. Key off. At the back of each gauge, the terminals are marked **I** (ignition +12 V), **G**
   (ground) and **S** (sender, sometimes SND). Don't remove anything: put a ring terminal for the tap
   *on top of* the existing one on the stud, and snug the nut back down.
2. Solder a **10 kΩ resistor** into each tap wire within a few centimetres of its ring terminal and
   cover it with adhesive-lined heatshrink: fuel S, trim S, oil S, and one gauge's I terminal.
3. Run a ground from that same gauge's **G** terminal to J1.7.
4. **Tach:** a ring terminal on the analog tach's signal terminal -- where the **gray** wire from
   the engine harness lands (the Delco EST coil's tach output) -- and a ground from the tach's G
   terminal, twisted together to J2. The analog tach stays connected.
5. **Battery:** from the always-on helm +12 V feed, through a 1 A inline fuse at the feed, to J1.3;
   helm ground to J1.8.
6. Keep the tap and tach wires away from ignition leads, and zip-tie them against chafe.

## Bring-up, one block at a time

Do these in order, and don't connect the gauges until the board has passed the bench steps.

1. **Converters.** Board on the Pi, nothing plugged into J1–J3. Enable I²C
   (`sudo raspi-config nonint do_i2c 0`, reboot), then `i2cdetect -y 1` must show **48** and **49**.
2. **One tap, on the bench.** A 12 V supply through a 10 kΩ resistor into J1.4 (the Rg the software
   expects) and its negative to J1.7. The calibration page (`http://<pi>:8090/calibrate`, with the
   software settings below) should show **about 12.0 V at the gauges**. Repeat for each tap input by
   moving the wire. A reading 20% high means the 10 kΩ was left out. The battery input (J1.3) takes
   12 V directly, without the 10 kΩ.
3. **Tach, on the bench.** A 12 V supply switched on and off into J2 (+ to pin 1): `pinctrl get 13`
   should follow it -- low while 12 V is applied, high when it is off. Then on the engine: compare
   the page's RPM with the analog tach at idle and at cruise, and use the page's tach calibration
   to correct it.
4. **Gauges.** Wire the taps (above). Key ON, engine off: the page shows the gauge supply and
   "gauges on", and each tap shows a voltage between 0 and the supply.
5. **The ratiometric check** (tells you what kind of gauges you have): once fuel is calibrated,
   note the fuel % with the key on, then start the engine. It should stay put. If it moves by more
   than a percent or two, your gauges regulate their own supply: switch the page to **Plain volts**
   and capture the points again.

## Software settings

In `boat.env` (see the README's service section), or exported before starting the app:

```bash
BOAT_SENSORS=real
BOAT_SENDER_WIRING=tap          # listen to the gauges instead of driving the senders
BOAT_TACH_GPIO=13               # the board's tach output (header pin 33)
BOAT_TACH_PULL=none             # the board has its own pull-up (R22)
BOAT_OIL_SENDER=true            # the oil tap on U2
# BOAT_TACH_PPR=2               # the default: 2 pulses per revolution on a 4-cylinder
# BOAT_FUEL_SENDER=false        # only if fuel level comes from NMEA 2000 instead
```

And once on the Pi:

```bash
sudo raspi-config nonint do_i2c 0
echo "dtoverlay=w1-gpio,gpiopin=26" | sudo tee -a /boot/firmware/config.txt
sudo apt install python3-lgpio && venv/bin/pip install smbus2
```

The fuel sender is assumed to be the US standard (240 Ω empty, 33 Ω full). For anything else,
set `"empty_ohm"` and `"full_ohm"` under `"fuel"` in `data/calibration.json` before capturing points.

## Calibrating on the boat

All on `http://<pi>:8090/calibrate`, from a phone or the iPad, **key ON**:

| What | When | How |
|---|---|---|
| Oil 0 psi | any time, engine **off** | "Key on, engine off: save 0 psi" |
| Trim | at the dock | drive fully DOWN → save; fully UP → save; optionally the analog gauge's middle mark |
| Fuel, first point | **right after filling up** | "Just filled up: save FULL" |
| Fuel, second point | any later day | when the analog gauge sits on a mark, tap ¾, ½ or ¼ |
| Oil, running | engine running | type what the analog gauge reads, at idle and at cruise |
| RPM | engine running steady | type what the analog tach reads |

Fuel shows "--" until it has two points at least 25% apart; after that the whole scale is live,
empty included, and more points refine it (the page says how well they agree). A capture is
refused while the reading is still settling (fuel is smoothed against slosh), and a new point at
the same level replaces the old one.

---

## Later: NMEA 2000 on the same board

The Fusion stereo needs a CAN interface. A proven CAN HAT stacked on this board's header is the
low-risk route; putting CAN on a second revision of this board is also reasonable (an MCP2518FD
controller with a 40 MHz crystal on SPI0 and an interrupt pin, a CAN transceiver, and an M12
"Micro-C" connector). Leave out any termination resistor -- an NMEA 2000 device is a drop on a
backbone that is already terminated at both ends.
