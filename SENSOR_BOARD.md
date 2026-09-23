# Sensor board: passive taps on the existing gauges

A Raspberry Pi HAT that reads **fuel level, trim, oil pressure, engine temperature, battery
voltage and RPM** by
*listening* to the wires the boat's analog gauges already use -- and, since rev 1.1, puts the Pi on
the boat's **NMEA 2000** network (the Fusion stereo, the depth transducer) through an isolated
CAN interface, so no separate CAN HAT is needed. Since rev 1.2 it also powers the Pi, from an
external 12 V to 5 V converter wired to J7, and has a connector (J6, LIGHTS) for a separate LED
controller board. Nothing is disconnected: every
analog gauge stays wired exactly as it is and keeps working, so if the Pi is off or broken the
helm is just a normal helm. It replaces buying an engine-data converter (the CX5003 route).

Built for this boat: a MerCruiser 3.0 4-cylinder with the **Delco EST ignition** (the newer
coil with two terminals, BAT +12 V in and TACH out), US-standard resistive senders, and the
stock analog gauges.

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
  fuel gauge   S ── to the fuel sender ───[10k]── tap wire ── J1.1 FUEL ────┐
  trim gauge   S ── to the trim sender ───[10k]── tap wire ── J1.2 TRIM ────┤ divider, clamp, filter
  any gauge    I ── key-on +12 V ─────────[10k]── tap wire ── J1.4 IGN ─────┤   ADS1115 U1 (0x48)
               G ── ground ────────────────────────────────── J1.7 GND      │     AIN0 fuel   AIN1 trim
  oil gauge    S ── to the oil sender ────[10k]── tap wire ── J1.5 OIL ─────┤     AIN2 batt   AIN3 gauge supply
  temp gauge   S ── to the temp sender ───[10k]── tap wire ── J1.6 TEMP ────┤   ADS1115 U2 (0x49)
  dashboard's switched 12 V (fused 1 A) ───────────────────── J1.3 BAT+ ────┘     AIN0 oil    AIN1 temp
  helm ground (battery -) ─────────────────────────────────── J1.8 GND

  gray wire: EST coil TACH terminal ── tach gauge ─────────── J2.1 TACH ──── optocoupler ──── GPIO 13
  the tach gauge's G terminal ─────────────────────────────── J2.2 GND (the tach's own, isolated)

  DS18B20 probes (engine, water) ──────────────────────────── J3   3V3 / DATA / GND ──────── GPIO 26

  NMEA 2000 drop cable (Fusion stereo, depth transducer) ───── J5 ── isolated CAN ── SPI0 + GPIO 25 (can0)

  LED controller board (separate) ─────────────────────────── J6 ── I2C, GPIO 18 / 19 / 21, 3V3, GND

  12 V -> 5 V converter (separate) ── J7 ── ideal diode ── header pins 2, 4: the Pi, and from it this board's 3V3
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
  the Pi. It has to: the EST coil's **TACH** terminal is the coil's switched side. It sits at
  12 V, drops to ground while the coil charges, and flies up to a few hundred volts at every
  spark. That's what the analog tach counts, and the board reads the same wire.
- A tap loads the gauge by about 0.2 mA (59 kΩ from the S terminal to ground). Next to a 33–240 Ω
  sender that is well under half a percent -- the analog gauges read the same as before.

---

## Schematic, block by block

Reference designators match the KiCad schematic
([PDF](hardware/sensor-board/fab/sensor-board-schematic.pdf)). `3V3` and `GND` are the Pi's.

### Tap inputs (×6: fuel, trim, battery, gauge supply, oil, engine temperature)

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
  gauge supply (…4), oil (…5), engine temperature (…6).

### Battery voltage (J1.3, U1 AIN2)

Same as a tap input, except the top resistor is a single **47 kΩ 1% 1206** (R3) and there is no
remote resistor -- the feed is fused at its source instead -- giving `V_adc = V_batt × 10/57`. The
software default `BOAT_BATT_R_TOP=47000`, `BOAT_BATT_R_BOTTOM=10000` matches.

### Tach: the EST coil's TACH terminal, gray wire (optocoupler)

```
  gray wire (TACH) ───── J2.1 ──[ R19 3.3k ]──[ R20 3.3k ]──[ R21 3.3k ]──┬─────────┬─────────┬── U3 pin 1 (LED anode)
                                   1206          1206          1206        │         │         │
                                                                     [ C11 10n ] [ D7 ]        │  D7: 1N4148W, cathode
                                                                           │     1N4148W       │  toward pin 1
  tach gauge G ───────── J2.2 ─────────────────────────────────────────────┴─────────┴─────────┴── U3 pin 2 (LED cathode)
                          (printed GND, but its own net, TACH_GND -- not the board's GND)

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

### NMEA 2000: isolated CAN interface

```
                    THE NETWORK'S SIDE (its own 0 V: N2K_GND)          │  THE PI'S SIDE
  J5.2 NET-S ──[ D9 SS14 ]──┬── N2K_12V ──[ U6 L78L05 ]── N2K_5V ──┐   │
                            ├─[ D10 SMAJ18A ]─ N2K_GND             │   │
                            └─[ C16 1µ 50V ]── N2K_GND   C17, C18 ─┤   │
  J5.3 NET-C ── N2K_GND                                            │   │
  J5.4 NET-H ──┬── D11 NUP2105L ── N2K_GND              U5 ISO1044 ┴ VCC2 │ VCC1 ── 3V3
  J5.5 NET-L ──┤   R26 120 + JP1 (open)                    CANH/CANL  │  TXD/RXD ── U4 MCP2518FD ── SPI0: GPIO 8-11
  J5.1 shield: not connected                                          │             INT ────── GPIO 25
                                                                      │             OSC1 ───── Y1 40 MHz
```

- **U4 (MCP2518FD)** is a CAN FD controller on the Pi's SPI0, the same chip family as the
  MacArthur HAT and most current CAN HATs; Raspberry Pi OS's own `mcp251xfd` driver makes it
  `can0`. R27 keeps it deselected while the Pi boots, R28 pulls its interrupt line up.
- **U5 (ISO1044)** is an isolated CAN transceiver: its Pi side runs from 3V3 and the Pi's ground,
  its network side from the NMEA 2000 network's own NET-S and NET-C. The two never share a ground,
  so the stereo's power wiring and the Pi's can't form a ground loop through the CAN wires, which
  is what the NMEA 2000 standard expects of a device with its own power supply.
- **D9** stops a reversed NET-S from doing anything; **D10** clamps surges on it; **U6** makes 5 V
  for U5's network side. The board draws well under 50 mA from the network: **1 LEN**.
- **D11** protects NET-H and NET-L from ESD and surges.
- **JP1 is open, and stays open on the boat.** Bridging it puts R26 (120 Ω) across NET-H/NET-L,
  for a bench test with just the Pi and the stereo and no backbone. A device on a real network
  must not terminate it -- the backbone's two terminators do that.
- The network side is its own island on the board: every N2K net is at least **2 mm** from all
  other copper (a rule KiCad's DRC checks, in `sensor-board.kicad_dru`), with its own ground pour,
  kept 4 mm clear of the Pi mounting hole beside it so a metal standoff can't bridge the two.

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
    AIN0 fuel  AIN1 trim                    AIN0 oil  AIN1 temp  AIN2-3 not connected
    AIN2 battery  AIN3 gauge supply
    ALERT/RDY: not connected                ALERT/RDY: not connected
```

No I²C pull-ups on the board: the Pi already has them on GPIO 2/3, and a second board stacked on
the header would double them up.

### 5 V in (J7): the Pi's power

```
  J7.1 +5V ──┬──────────── Q1 IRLML0030 (S -> D) ──────────────┬──── header pins 2, 4 (the Pi's 5 V)
             │                     │ G                         ├── C21 22µ
         [ C20 100n ]         U7 LM74700 ideal diode:           └── D12 SMAJ5.0A (to GND)
             │                ANODE/EN to J7.1, CATHODE to the Pi side,
  J7.2 GND ──┴── GND          GATE to Q1, VCAP: C19 100n to ANODE
```

- The converter's output (set it to **5.1-5.2 V**, rated 3 A or more) runs the Pi through the
  header's 5 V pins, as the Raspberry Pi HAT guide allows (5 V ±5 %, up to 2.5 A). This board's
  3V3 comes from the Pi, as before; the NMEA 2000 side still runs from the network's own 12 V.
- **U7 + Q1 are an ideal diode**: about 20 mV forward drop, where a Schottky would lose 0.4 V the
  Pi can't spare (it warns of undervoltage below 4.63 V). **J7 wired backwards** is blocked --
  nothing conducts, and U7 is rated to -65 V. With the Pi's **USB-C plugged in as well**, that
  supply can't push current back into the converter. (The Pi 4 has no diode of its own on
  USB-C, though, so the converter would feed the USB supply: don't plug both in on the boat.)
- **D12** clamps spikes on the rail. None of this saves the Pi from a converter that fails with
  12 V on its output, so pick one with output overvoltage protection.
- J7 and these parts sit on a **tab above the header's left half** (see Ordering): the Pi's 5 V
  pins are at that corner, and the tab keeps the 5 V run to them short.

### Lights connector (J6): for a separate LED board

The LED controllers are deliberately **not** on this board: several amps of switched strip
current would sit next to the millivolt-level gauge taps, and a shorted strip shouldn't be able
to take the engine data down with it. J6 gives a separate LED board everything it needs from the
Pi, and nothing it switches:

```
  J6.1 3V3  ── the Pi's 3.3 V, for the LED board's logic only (keep it under 50 mA)
  J6.2 SDA  ── GPIO 2  ┐ the same I2C bus as the converters: PWM channels are PCA9685s on the LED
  J6.3 SCL  ── GPIO 3  ┘ board, 16 each, as many as needed
  J6.4 GND
  J6.5 DAT1 ── GPIO 18 (PWM0)  addressable strip data (WS2812B / WS2815...), 3.3 V
  J6.6 DAT2 ── GPIO 19 (PWM1)  a second addressable strip, driven independently
  J6.7 GND
  J6.8 AUX  ── GPIO 21         spare: an output enable, a third data line (PCM), a button...
```

The rules for the LED board, whatever it ends up carrying:

- **Logic only on this cable.** The strips' 12 V comes into the LED board on its own fused
  feed, and their current returns on its own heavy ground wire to the same ground point as the
  Pi's supply -- never through J6. J6's GND is the signal reference.
- **Buffer the data lines to 5 V** on the LED board (a 74AHCT125 or similar, powered from the
  LED board's own 5 V), with a ~330 Ω series resistor at each strip's data input. WS28xx strips
  want 5 V logic; the Pi's pins are 3.3 V and must never see more.
- **PCA9685 addresses:** anything except 0x48 and 0x49 (the converters) and 0x70 (the PCA9685's
  all-call address). The software's default is 0x40 (`BOAT_PCA9685_ADDR`). Run the PCA9685 from
  J6's 3V3, so its I2C levels match the Pi's.
- **Addressable strips on the Pi's PWM** (`BOAT_LED_DRIVER=ws281x`, `BOAT_LED_GPIO=18`) need the
  Pi's analog audio off: `dtparam=audio=off` in `/boot/firmware/config.txt`. The stereo is on NMEA
  2000, so nothing is lost.
- The connector is a **JST GH** 8-way (1.25 mm, latching): use a ready-made GH 8-pin cable wired
  **pin 1 to pin 1** ("same direction"), with the same connector on the LED board.

---

## Pi header pins used

| Pin | Signal | Use |
|---|---|---|
| 2, 4 | 5V | **in**: the Pi's power, from J7 through the ideal diode |
| 1, 17 | 3V3 | converters, pull-ups, probes (a few mA in total); J6's 3V3 (under 50 mA) |
| 3 | GPIO 2 / SDA | both ADS1115; J6 |
| 5 | GPIO 3 / SCL | both ADS1115; J6 |
| 6, 9, 14, 20, 25, 30, 34, 39 | GND | |
| 33 | GPIO 13 | tach |
| 37 | GPIO 26 | 1-Wire |
| 19, 21, 23, 24 | GPIO 10, 9, 11, 8 (SPI0 MOSI, MISO, SCLK, CE0) | NMEA 2000 CAN controller |
| 22 | GPIO 25 | CAN controller interrupt |
| 12 | GPIO 18 (PWM0) | J6 DAT1: addressable LED data |
| 35 | GPIO 19 (PWM1) | J6 DAT2: second addressable LED data |
| 40 | GPIO 21 | J6 AUX: spare |

Everything else is left free, GPIO 4 (used for NMEA 0183 by some marine HATs) included. The
header is a stacking socket, so another HAT can go on top; check its pin list against this one
first -- in particular, a CAN HAT on SPI0 CE0 would clash with the one built in here.

## Connectors

J1, J2, J3 and J5 are Phoenix Contact **MC 1,5 / 3.81 mm pluggable terminal blocks with
screw-locking flanges**: the plug screws to the header, so it can't walk out on a boat. Each
connector's plug overhangs the board edge. J6, board to board, is a latching **JST GH**.

Since rev 1.2 every pin is labelled on the board itself, beside the pin, with the name in the
**Printed** column; the **WIRING** block in the middle of the board sums up the table below.

| Connector | Pin | Printed | Goes to |
|---|---|---|---|
| **J1** helm (8, left edge) | 1 | FUEL | fuel gauge **S** terminal, through its 10k |
| | 2 | TRIM | trim gauge **S** terminal, through its 10k |
| | 3 | BAT+ | the dashboard switch's 12 V (the same feed as the 5 V converter), **fused 1 A at the source** |
| | 4 | IGN | the **I** terminal on the back of any one gauge, through its 10k. That's the key-switched +12 V that lights the gauges; they all share it, so any gauge will do. The board measures the senders as a share of it, and uses it to tell that the key is on |
| | 5 | OIL | oil gauge **S** terminal, through its 10k |
| | 6 | TEMP | engine temperature gauge **S** terminal, through its 10k |
| | 7 | GND | the **G** terminal of the same gauge as IGN: the taps measure against the gauges' own ground |
| | 8 | GND | helm ground / battery - (the BAT+ feed's return) |
| **J2** tach (2, bottom edge) | 1 | TACH | the **gray wire** from the EST coil's **TACH** terminal -- easiest where it lands on the tach gauge's signal terminal at the helm. No resistor on this one |
| | 2 | GND | the tach gauge's **G** terminal -- twist these two wires together |
| **J3** probes (3, right edge) | 1 / 2 / 3 | 3V3 / DATA / GND | DS18B20 red / yellow / black |
| **J5** NMEA 2000 (5, bottom edge) | 1 | BARE | the drop cable's bare drain wire (shield) -- **not connected** here (the backbone grounds its shield at the power tee) |
| | 2 | RED | NET-S: the network's +12 V |
| | 3 | BLK | NET-C: the network's 0 V |
| | 4 | WHT | NET-H: CAN high |
| | 5 | BLU | NET-L: CAN low |
| **J6** lights (8, right edge, JST GH) | 1 / 2 / 3 / 4 | 3V3 / SDA / SCL / GND | the LED board (see "Lights connector" above) |
| | 5 / 6 / 7 / 8 | DAT1 / DAT2 / GND / AUX | GPIO 18 / GPIO 19 / ground / GPIO 21 |
| **J7** 5V IN (2, on the tab, plug faces up) | 1 | +5V | the 12 V -> 5 V converter's **+5 V** output |
| | 2 | GND | the converter's **0 V** output |
| **J4** | | | the Pi's 40-pin header, underneath |

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
| U4 | 1 | **MCP2518FD** (Microchip, SOIC-14) | CAN controller |
| U5 | 1 | **ISO1044BD** (TI, SOIC-8) | isolated CAN transceiver |
| U6 | 1 | L78L05 (SOT-89) | 5 V for the network side |
| Y1 | 1 | 40 MHz 3.3 V oscillator, 3.2 × 2.5 mm | CAN controller clock |
| D9 / D10 / D11 | 1 each | SS14 / SMAJ18A (SMA) / NUP2105L (SOT-23) | reverse polarity / surge / CAN ESD |
| R26, JP1 | 1 | 120 Ω 0805 + solder jumper (open) | bench-only terminator |
| C12-C18 | 7 | 100 nF / 1 µF 0805, one 1 µF **50 V 1206** (C16) | decoupling |
| J1 / J2 / J3 / J5 | 1 each | Phoenix MC 1,5/ 8-, 2-, 3-, 5-GF-3,81 | plus the matching **MC 1,5/ n-STF-3,81** plugs |
| J6 | 1 | JST **SM08B-GHS-TB** (GH, 8-way, side entry, SMD) | lights; a GH 8-pin 1:1 cable to the LED board |
| J7 | 1 | Phoenix MC 1,5/ 2-GF-3,81 (as J2) | 5 V in, plus its **MC 1,5/ 2-STF-3,81** plug |
| U7 / Q1 | 1 each | **LM74700-Q1** (TI, SOT-23-6) / **IRLML0030** (Infineon, SOT-23) | ideal diode |
| C19, C20 / C21 | 2 / 1 | 100 nF 0805 / 22 µF 25 V X5R **1206** | charge pump, input / 5 V bulk |
| D12 | 1 | SMAJ5.0A (SMA), as D8 | 5 V rail clamp |
| J4 | 1 | 2×20 female **stacking** header, extra-tall (e.g. Adafruit 1979) | |
| — | 5 | M2.5 standoffs and screws | four HAT holes on the Pi, one (H5) supporting the part past the Pi's edge |
| — | 1 | NMEA 2000 drop cable with a female Micro-C end | cut the other end into J5's plug |
| **Rg** | 5 | **10 kΩ ¼ W through-hole** + adhesive-lined heatshrink | at the gauge end of each tap wire (fuel, trim, oil, temp, and one gauge's I) |
| — | 1 | inline fuse holder + 1 A fuse | J1.3 battery feed, at its source |
| — | 1 | **12 V -> 5 V converter**, 3 A or more, adjustable or fixed at 5.1-5.2 V, **with output overvoltage protection**, potted or sealed | J7; fed from the dashboard switch through its own fuse |
| — | 1–2 | DS18B20 waterproof probes | engine (on the thermostat housing), water |
| — | | 20–22 AWG tinned marine wire, ring terminals | taps; tach pair twisted |

---

## Ordering the board

For **PCBWay**, everything is ready in [hardware/sensor-board/fab/pcbway/](hardware/sensor-board/fab/pcbway/): the Gerbers, a BOM and centroid in PCBWay's assembly format, and [ORDER.md](hardware/sensor-board/fab/pcbway/ORDER.md) with every option to pick on their quote forms. The notes below are the general version.

The board is a 2-layer, 1.6 mm Pi HAT, **65 × 88 mm overall**: the standard HAT outline and
mounting holes, 20 mm longer on the side away from the header to make room for the NMEA 2000
interface, and a **35 × 12 mm tab above the left half of the header** for the 5 V input.
- The 20 mm reach past the Pi 4's USB-C / micro-HDMI edge and sit about 5 mm above those plugs
  (on the Pi's usual 11 mm standoffs) -- fine for straight plugs; check first if you use a
  right-angle HDMI adapter there. H5, in the extra corner, takes a standoff to the enclosure.
- The tab reaches 12 mm past the Pi's GPIO edge, at the SD-card end, and J7's plug and wires
  come out of its top edge: leave room for them there in the enclosure.
0.2 mm tracks and spacing, 0.3 mm vias, all within any online fab's standard service.

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
  -- **except under the NMEA 2000 network's side** (its own pour, 2 mm away; see above) and
  **around the tach input**: J2, R19–R21, C11, D7 and U3's pins 1–2 are on their own
  nets (TACH_*) with **at least 3 mm clearance** to everything else and no pour near them. That
  side sees the ignition; no board ground goes anywhere near it. Inside it, the three series
  resistors' nets keep 0.6 mm from each other. Both rules are in `sensor-board.kicad_dru`, so
  KiCad's design rules check enforces them.
- The six input channels run in lanes from J1 on the left to the converters in the middle,
  each with its filter right at the ADC pin; the tach sits in the bottom-right corner, away
  from them.
- **Silkscreen** (rev 1.2): every connector pin is labelled with what it connects to, printed
  on the wire side of the connector, and a WIRING block in the middle of the board sums up the
  rules. J5's labels are the drop cable's wire colours.
- **The 5 V input** is on the tab: J7's +5V pin sits right above the header's 5 V pins, and the
  converter's current runs J7 -> Q1 -> header pins 2/4 on 0.8 mm tracks, the last stretch on the
  bottom layer (on top it walled the header's GND pin 6 off from the pour). The ideal diode's
  sense pins, caps and D12 join on thin tracks. No via sits in a surface-mount pad anywhere on
  the board, so none can wick solder away from a joint.
- **J6** sits on the right edge between the probe connector and the mounting hole. Its two
  outer data/spare lines leave the header's far corner on the bottom layer, so they don't wall
  off the header's ground pin there from the top pour.
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
   cover it with adhesive-lined heatshrink: fuel S to J1 FUEL, trim S to TRIM, oil S to OIL, the
   temperature gauge's S to TEMP, and one gauge's **I** terminal to **IGN** (J1.4).
3. Run a ground from that same gauge's **G** terminal to J1.7 (GND).
4. **Tach:** the EST coil has two terminals, **BAT** (+12 V in) and **TACH**. The **gray** wire
   runs from TACH to the tach gauge's signal terminal (marked TACH, SIG or S). Put a ring terminal
   on top of the gray wire's at the back of the tach gauge, to **J2 TACH**, and a second from the
   tach gauge's **G** terminal to **J2 GND**. Twist the two together. No 10 kΩ on these -- the
   board has its own. The tach gauge stays connected.
5. **Power:** the dashboard switch's 12 V output feeds the 12 V -> 5 V converter (through its
   own fuse) and, through a 1 A inline fuse, J1.3 (BAT+), which measures the battery. Helm ground
   to J1.8 (GND). The converter's output goes to **J7: +5V and GND** -- that powers the Pi and the
   whole board; leave the Pi's USB-C unplugged.
6. **NMEA 2000:** a drop cable from a T on the backbone to J5 (colours in the Connectors table).
   The backbone needs its power tee and two terminators, as always; see README's "The NMEA 2000
   backbone".
7. Keep the tap and tach wires away from ignition leads, and zip-tie them against chafe.

## Bring-up, one block at a time

Do these in order, and don't connect the gauges until the board has passed the bench steps.

1. **5 V in, before the Pi goes on.** Board on its own, a bench supply at 5.1 V into J7:
   header pins 2 and 4 read about 5.08 V to GND. Swap the supply's leads: they read 0 V, and the
   supply sees no load (the ideal diode blocks). Then set the real converter to 5.1-5.2 V the same
   way before connecting it.
2. **Converters.** Board on the Pi, powered from J7, nothing plugged into J1–J3. Enable I²C
   (`sudo raspi-config nonint do_i2c 0`, reboot), then `i2cdetect -y 1` must show **48** and **49**.
3. **One tap, on the bench.** A 12 V supply through a 10 kΩ resistor into J1.4 (the Rg the software
   expects) and its negative to J1.7. The calibration page (`http://<pi>:8090/calibrate`, with the
   software settings below) should show **about 12.0 V at the gauges**. Repeat for each tap input by
   moving the wire. A reading 20% high means the 10 kΩ was left out. The battery input (J1.3) takes
   12 V directly, without the 10 kΩ.
4. **Tach, on the bench.** A 12 V supply switched on and off into J2 (+ to pin 1): `pinctrl get 13`
   should follow it -- low while 12 V is applied, high when it is off. Then on the engine: compare
   the page's RPM with the analog tach at idle and at cruise, and use the page's tach calibration
   to correct it.
5. **Gauges.** Wire the taps (above). Key ON, engine off: the page shows the gauge supply and
   "gauges on", and each tap shows a voltage between 0 and the supply.
6. **NMEA 2000.** With the overlay below set and the Pi rebooted, `dmesg | grep -i mcp251xfd`
   shows the controller and `ip link` lists `can0`. Connect J5 to the backbone, power the network,
   then `sudo ip link set can0 up type can bitrate 250000 restart-ms 100` and `candump can0`: with
   the stereo on you see its frames. The dashboard's Media card finds the stereo by itself. (For a
   bench test with no backbone, bridge JP1 and power NET-S/NET-C from a 12 V supply -- then
   un-bridge it before the board goes on the boat.)
7. **The ratiometric check** (tells you what kind of gauges you have): once fuel is calibrated,
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
BOAT_TEMP_SENDER=true           # the engine temperature tap on U2 (J1.6)
BOAT_CAN=can0                   # the NMEA 2000 interface on J5 (Fusion stereo, depth)
# BOAT_TACH_PPR=2               # the default: 2 pulses per revolution on a 4-cylinder
# BOAT_FUEL_SENDER=false        # only if fuel level comes from NMEA 2000 instead
```

And once on the Pi:

```bash
sudo raspi-config nonint do_i2c 0
echo "dtoverlay=w1-gpio,gpiopin=26" | sudo tee -a /boot/firmware/config.txt
echo "dtparam=spi=on" | sudo tee -a /boot/firmware/config.txt
echo "dtoverlay=mcp251xfd,spi0-0,oscillator=40000000,interrupt=25" | sudo tee -a /boot/firmware/config.txt
sudo apt install can-utils && venv/bin/pip install python-can
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
| Engine temp, cold | key on after the engine has sat a few hours | type the air or lake temperature |
| Engine temp, warm | warmed up | type what the analog gauge reads (an infrared thermometer on the thermostat housing is better) |
| RPM | engine running steady | type what the analog tach reads |

Fuel shows "--" until it has two points at least 25% apart; after that the whole scale is live,
empty included, and more points refine it (the page says how well they agree). Engine temperature
works the same way, with two points at least 40 F apart: the software knows the shape of a
temperature sender's curve (app/sender_tap.py), so the cold and warm points also set the overheat
end, which straight lines through them would read 15-45 F low. With the gauges off (key off) the
engine temperature falls back to a DS18B20 probe on J3, if one is assigned to Engine. A capture is
refused while the reading is still settling (fuel is smoothed against slosh), and a new point at
the same level replaces the old one.

---

`can0` has to be up before the dashboard starts: the `ExecStartPre` line in README's systemd
unit does that. Add it to the Pi's unit **once this board is fitted** -- without a `can0` to bring
up, that line fails and takes the whole service down with it, which is why it is left out today.
