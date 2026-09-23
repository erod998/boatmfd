# Sensor board: passive taps on the existing gauges

A Raspberry Pi HAT that reads **fuel level, trim, oil pressure, battery voltage and RPM** by
*listening* to the wires the boat's analog gauges already use. Nothing is disconnected: every
analog gauge stays wired exactly as it is and keeps working, so if the Pi is off or broken the
helm is just a normal helm. It replaces buying an engine-data converter (the CX5003 route).

Software: `BOAT_SENSORS=real` with `BOAT_SENDER_WIRING=tap` ([app/sender_tap.py](app/sender_tap.py)
has the maths and why it works; [tests/test_sensor_board.py](tests/test_sensor_board.py) runs it
end to end against simulated gauges).

> **Status: a design, not a proven board.** The circuit follows standard practice for each block,
> and the software is tested against simulated gauges, but no board has been built or connected
> to this boat yet. The bring-up section below is written so each block is checked with a
> multimeter before it is trusted.

---

## How it works

```
                 BACK OF THE GAUGES (unchanged)                     SENSOR BOARD (Pi HAT)
  fuel gauge   I ─┬─ ignition +12 V ───────[10k]──── tap wire ──── J1.4  GAUGE_I ─┐
                  │                                                                 │  divider + clamp + RC
               S ─┼─ to the fuel sender ───[10k]──── tap wire ──── J1.1  FUEL_S ───┼─ ADS1115 #1 (0x48)
               G ─┼─ ground ─────────────────────────────────────── J1.6  GND       │   AIN0 fuel
  trim gauge   S ─┼─ to the trim sender ───[10k]──── tap wire ──── J1.2  TRIM_S ───┤   AIN1 trim
  oil gauge    S ─┴─ to the oil sender ────[10k]──── tap wire ──── J1.3  OIL_S ────┤   AIN2 battery
                                                                                    │   AIN3 gauge supply
  12 V always-on feed (fused 1 A at the source) ──────────────────── J3.1  BATT ────┘
                                                                                     ADS1115 #2 (0x49)
  analog tach: signal (coil -) ─────────────────────────────────── J2.1  TACH ── opto ── GPIO 17   AIN0 oil
               ground ──────────────────────────────────────────── J2.2                            AIN1-3 spare
  DS18B20 probes (engine, water) ───────────────────────────────── J4    1-Wire ─────── GPIO 19
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
- The **tach** input goes through an **optocoupler**. The coil-negative wire spikes to hundreds of
  volts at every spark; the opto means none of that can reach the Pi.
- A tap loads the gauge by about 0.25 mA (49 kΩ + 10 kΩ from the S terminal to ground). Next to a
  33–240 Ω sender that is under half a percent -- the analog gauges read the same as before.

---

## Schematic, block by block

Part values here match the parts list. `3V3` and `GND` are the Pi's (header pins 1 and 6).

### Tap input (×3 on ADC #1: FUEL_S, TRIM_S, GAUGE_I; ×1 on ADC #2: OIL_S; plus any spare you use)

```
                     at the gauge                            on the board
  gauge terminal ──[ Rg 10k ¼W ]── tap wire ── J1.x ──┬──[ R1 39k 1% 1206 ]──┬──[ R3 1k ]──┬── ADS1115 AINx
                  (in heatshrink)                     │                      │             │
                                                   [ D1 ]                    ├─[ R2 10k 1% ]── GND
                                                  SMAJ18A                    │             │
                                                      │                  [ D2 BAT54S ]  [ C1 100n ]
                                                     GND                 to 3V3 and GND    │
                                                                                          GND
```

- **R1/R2 with Rg** divide 0–16 V down to 0–2.7 V (`V_adc = V_tap × 10/59`).
- **D2 (BAT54S)** clamps the node between GND and 3V3 so a transient can never push the ADC input
  past its limits; R1+Rg keep that clamp current to a couple of milliamps even at 100 V.
- **R3/C1** are a noise filter right at the ADC pin (and keep the clamp's forward voltage away from it).
- **D1** is for ESD on the connector. It never conducts in normal use (standoff 18 V).
- R1 is a 1206 for its voltage rating; everything else can be 0805.

### Battery voltage (ADC #1 AIN2)

Same as a tap input, except the top resistor is a single **47 kΩ 1% 1206** on the board (no
remote resistor; the feed is fused at its source instead), giving `V_adc = V_batt × 10/57`. The
software default `BOAT_BATT_R_TOP=47000`, `BOAT_BATT_R_BOTTOM=10000` matches.

### Tach (optocoupler)

```
  analog tach signal ── J2.1 ──[ R10 3.3k ]──[ R11 3.3k ]──[ R12 3.3k ]──┬────────┬──────┬── U3 pin 1 (LED anode)
  (coil -, gray wire)             1206          1206          1206        │        │      │
                                                                     [ C10 10n ] [ D10 ] │   D10: 1N4148W, cathode
                                                                          │    1N4148W   │   toward pin 1
  analog tach ground ── J2.2 ─────────────────────────────────────────────┴────────┴──────┴── U3 pin 2 (LED cathode)
                          (TACH_GND: its own net -- not the board's GND)

                                              3V3
                                               │
                                          [ R13 10k ]
                                               │
  U3 pin 4 (collector) ────────────────────────┴──── JP1 ──┬── GPIO 17 (header pin 11)   default
                                                           └── GPIO 27 (header pin 13)   alternative
  U3 pin 3 (emitter) ── GND
```

- Three 1206 resistors in series share the ignition spike (≈130 V each at a 400 V spike, inside a
  1206's rating) and set about 1 mA through the LED at 12 V -- plenty for a PC817 rank B/C.
- **C10** filters the ring-down after each spark; **D10** stops the ring-down's negative swing from
  reverse-biasing the LED.
- The Pi sees one clean rising edge per ignition cycle (the LED turning off as the points/module
  closes), which is what the tach code counts. Set **`BOAT_TACH_PULL=none`**: R13 is the pull-up.
- If your ignition's tach wire is known to spike past ~400 V (some CD ignitions), add a fourth resistor.

### 1-Wire temperature probes

```
  J4.1 ── 3V3
  J4.2 ──┬── JP2 ──┬── GPIO 19 (header pin 35)   default: leaves GPIO 4 free for CAN/NMEA 0183 HATs
         │         └── GPIO 4  (header pin 7)    alternative
         ├──[ R20 4.7k ]── 3V3
         └──[ D20 ESD diode ]── GND
  J4.3 ── GND
```

All DS18B20 probes (engine, water) share these three wires.

### Converters and header

```
  ADS1115 #1 (U1)                        ADS1115 #2 (U2, optional: oil + spares)
    VDD ── 3V3, with 100n + 1µ to GND      VDD ── 3V3, with 100n + 1µ to GND
    GND ── GND                             GND ── GND
    SCL ── GPIO 3 (pin 5)                  SCL ── GPIO 3 (pin 5)
    SDA ── GPIO 2 (pin 3)                  SDA ── GPIO 2 (pin 3)
    ADDR ── GND      -> address 0x48       ADDR ── 3V3   -> address 0x49
    AIN0 fuel  AIN1 trim                   AIN0 oil  AIN1-3 spare (fit the tap front end on any you use)
    AIN2 battery  AIN3 gauge supply
    ALERT/RDY: not connected               ALERT/RDY: not connected
```

No I²C pull-ups on the board: the Pi already has them on GPIO 2/3, and a second board stacked on
the header would double them up.

---

## Pi header pins used

| Pin | Signal | Use |
|---|---|---|
| 1 | 3V3 | converters, pull-ups, probes (a few mA in total) |
| 3 | GPIO 2 / SDA | both ADS1115 |
| 5 | GPIO 3 / SCL | both ADS1115 |
| 6, 9, 14, 20, 25, 30, 34, 39 | GND | |
| 11 | GPIO 17 | tach (JP1 alternative: pin 13, GPIO 27) |
| 35 | GPIO 19 | 1-Wire (JP2 alternative: pin 7, GPIO 4) |

Everything else is free for a CAN HAT (for the Fusion stereo over NMEA 2000) stacked on the same
header -- check its pin list against this one; both jumpers exist for exactly that.

## Connectors

Use **pluggable terminal blocks with screw-locking flanges** (for example the Phoenix Contact
MC / MCV 1,5 series, 3.81 mm pitch) or Molex Micro-Fit 3.0: something that latches, because a
plain friction plug will walk out on a boat.

| Connector | Pin | Signal | Goes to |
|---|---|---|---|
| **J1** gauges (6) | 1 | FUEL_S | fuel gauge **S** terminal, through its 10k |
| | 2 | TRIM_S | trim gauge **S** terminal, through its 10k |
| | 3 | OIL_S | oil gauge **S** terminal, through its 10k (if fitted) |
| | 4 | GAUGE_I | any gauge's **I** terminal (they share the ignition feed), through its 10k |
| | 5 | SPARE | spare tap (ADC #2), through its 10k |
| | 6 | GND | the same gauge's **G** terminal: measures against the gauges' own ground |
| **J2** tach (2) | 1 | TACH | the analog tach's signal terminal (gray wire on a MerCruiser) |
| | 2 | TACH_GND | the analog tach's **G** terminal -- twist these two wires together |
| **J3** battery (2) | 1 | BATT | the helm's always-on +12 V (the feed that powers the Pi), **fused 1 A at the source** |
| | 2 | GND | helm ground |
| **J4** probes (3) | 1 / 2 / 3 | 3V3 / DATA / GND | DS18B20 red / yellow / black |

---

## Parts list

| Ref | Qty | Part | Notes |
|---|---|---|---|
| U1 | 1 | **ADS1115IDGSR** (TI, VSSOP-10) | fuel, trim, battery, gauge supply |
| U2 | 0–1 | ADS1115IDGSR | only for oil / spares |
| U3 | 1 | **PC817**, rank B or C (DIP-4 or SMD-4) | tach optocoupler |
| R1 | 1 per tap | 39 kΩ 1% **1206** | tap top resistor (on the board) |
| R2 | 1 per input | 10 kΩ 1% 0805 | divider bottom |
| R3 | 1 per input | 1 kΩ 0805 | ADC pin series resistor |
| C1 | 1 per input | 100 nF X7R 0805 | ADC pin filter |
| D1 | 1 per input | SMAJ18A (SMA) | connector ESD/transient |
| D2 | 1 per input | BAT54S (SOT-23) | clamp to 3V3/GND |
| R4 | 1 | 47 kΩ 1% 1206 | battery top resistor |
| R10–R12 | 3 | 3.3 kΩ 1206 | tach input, in series |
| C10 | 1 | 10 nF X7R 0805 | tach ring-down filter |
| D10 | 1 | 1N4148W (SOD-123) | across the opto LED |
| R13 | 1 | 10 kΩ 0805 | tach pull-up |
| R20 | 1 | 4.7 kΩ 0805 | 1-Wire pull-up |
| D20 | 1 | ESD protection diode, 3.3–5 V (SOD-323) | 1-Wire line |
| — | 2 per ADC | 100 nF + 1 µF X7R 0805 | ADC decoupling |
| JP1, JP2 | 2 | solder jumpers | tach and 1-Wire pin choice |
| J1–J4 | 4 | locking pluggable terminal blocks, 6/2/2/3 pin | see Connectors |
| — | 1 | 2×20 female **stacking** header, extra-tall | so a CAN HAT can stack |
| — | 4 | M2.5 standoffs and screws | HAT mounting holes |
| **Rg** | 1 per tap | **10 kΩ ¼ W through-hole** + adhesive-lined heatshrink | at the gauge end of each tap wire |
| — | 1 | inline fuse holder + 1 A fuse | J3 feed, at its source |
| — | 1–2 | DS18B20 waterproof probes | engine (on the thermostat housing), water |
| — | | 20–22 AWG tinned marine wire, ring terminals | taps; tach pair twisted |

"Per input" means every analog input you populate: typically 5 (fuel, trim, gauge supply, battery,
oil). The whole board is tens of dollars in parts; a 2-layer board of this size costs a few dollars
per board from the usual online fabs.

---

## Layout rules

- **Two layers**, the bottom a solid GND pour -- **except under the tach input**: J2, R10–R12,
  C10, D10 and U3's pins 1–2 are on their own net (TACH_GND) with **at least 3 mm clearance** to
  everything else, including under the opto. That side sees ignition spikes; no board ground goes
  anywhere near it.
- Each ADC input's R3/C1 right at the ADC pin; the dividers and clamps between the connector and
  the ADC, analog traces kept away from the tach corner.
- Standard HAT outline (65 × 56.5 mm) with the four M2.5 holes at the standard positions; all
  connectors on one edge, every pin labelled on the silkscreen.
- **Conformal coat** everything except the connectors and the header after the board passes bring-up.

---

## Wiring at the gauges

1. Key off. At the back of each gauge, the terminals are marked **I** (ignition +12 V), **G**
   (ground) and **S** (sender, sometimes SND). Don't remove anything: put a ring terminal for the tap
   *on top of* the existing one on the stud, and snug the nut back down.
2. Solder a **10 kΩ resistor** into each tap wire within a few centimetres of its ring terminal and
   cover it with adhesive-lined heatshrink. Fuel S, trim S, oil S, and one gauge's I terminal.
3. Run a ground from that same gauge's **G** terminal to J1.6.
4. **Tach:** a ring terminal on the analog tach's signal terminal, and a ground from the tach's G
   terminal, twisted together to J2. The analog tach stays connected.
5. **Battery:** from the always-on helm +12 V feed, through a 1 A inline fuse at the feed, to J3.
6. Keep the tap and tach wires away from ignition leads, and zip-tie them against chafe.

## Bring-up, one block at a time

Do these in order, and don't connect the gauges until the board has passed the bench steps.

1. **Converters.** Board on the Pi, nothing on J1–J3. Enable I²C
   (`sudo raspi-config nonint do_i2c 0`, reboot), then `i2cdetect -y 1` must show **48** (and **49**
   if U2 is fitted).
2. **One tap, on the bench.** A 12 V supply through a 10 kΩ resistor into J1.4 (the Rg the software
   expects) and its negative to J1.6. The calibration page (`http://<pi>:8090/calibrate`, with the
   software settings below) should show **about 12.0 V at the gauges**. Repeat for each input by
   moving the wire. A reading 20% high means the 10 kΩ was left out.
3. **Tach, on the bench.** A 12 V supply switched on and off into J2: `pinctrl get 17` (current
   Raspberry Pi OS) should follow it -- low while 12 V is applied, high when it is off. Then on the engine: compare the page's
   RPM with the analog tach at idle and at cruise, and use the page's tach calibration to correct it.
4. **Gauges.** Wire the taps (above). Key ON, engine off: the page shows the gauge supply and
   "gauges on", and each tap shows a voltage between 0 and the supply.
5. **The ratiometric check** (tells you what kind of gauges you have): once fuel is calibrated,
   note the fuel % with the key on, then start the engine. It should stay put. If it moves by more
   than a percent or two, your gauges regulate their own supply: switch the page to **Plain volts**
   and capture the points again.

## Software settings

```bash
export BOAT_SENSORS=real
export BOAT_SENDER_WIRING=tap          # listen to the gauges instead of driving the senders
export BOAT_TACH_PULL=none             # the opto has its own pull-up (R13)
export BOAT_TACH_GPIO=17               # 27 if JP1 is moved
export BOAT_OIL_SENDER=true            # if U2 and the oil tap are fitted
# BOAT_FUEL_SENDER=false               # only if fuel level comes from NMEA 2000 instead

sudo raspi-config nonint do_i2c 0
echo "dtoverlay=w1-gpio,gpiopin=19" | sudo tee -a /boot/firmware/config.txt   # gpiopin=4 if JP2 is moved
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
