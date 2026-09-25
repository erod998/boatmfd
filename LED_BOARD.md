# LED board: four RGBW zones, four addressable strips, 20 A

The boat's lighting controller. It sits in its own box near the strips and the 12 V supply, and
takes its orders from the Pi over an ordinary patch cable to the [sensor board](SENSOR_BOARD.md)'s J6.

- **Four RGBW zones** (J3-J6): 12 V common-anode strips, 5050 RGBW, 5 m each -- a MOSFET per colour.
- **Four addressable outputs** (J7-J10): 12 V pixel strips (WS2815 and the like), 5 m each. An
  RP2040 on the board runs them, so each can show its own colour or effect.
- **Every output fused**, each on its own standard blade fuse (ATO/ATC) in a holder on the board.
- **20 A in all**, from one 12 V feed, measured by the board itself: the software holds the whole
  board inside it.
- **The link**: an RJ45 for a straight-through Cat5e/Cat6 patch cable to the sensor board's J6,
  carrying the board's I2C bus as differential I2C. **Not Ethernet** -- never plug it into a switch.

**Design files:** [hardware/led-board/](hardware/led-board/) -- a KiCad 10 project generated from
Python, like the sensor board's; the RP2040's firmware in
[hardware/led-board/firmware/](hardware/led-board/firmware/).
**Software:** `BOAT_LED_DRIVER=ledboard` ([app/lighting.py](app/lighting.py), `LedBoardDriver`;
tested in [tests/test_led_board.py](tests/test_led_board.py)).

> **Status: a checked design, not a proven board.** KiCad's electrical rules check, design rules
> check and schematic-parity check all come back clean, with nothing reported at any severity, and
> the fabrication files are in [hardware/led-board/fab/](hardware/led-board/fab/) -- ordering notes in
> [fab/pcbway/ORDER.md](hardware/led-board/fab/pcbway/ORDER.md) (2 oz copper). The firmware compiles;
> the driver is tested against a simulated board. Nothing has been built yet.

---

## What it has to carry

At 12 V, at full white:

| Output | Strip | Current |
|---|---|---|
| a zone | 5 m of 5050 RGBW, 60 LEDs/m | about 8 A (2 A per colour wire; all of it in the shared +12V wire) |
| an addressable output | 5 m of WS2815, 60 pixels/m | about 7.5 A (25 mA a pixel) |
| **everything** | four of each | **about 60 A** |

The board is built for **20 A**: that's what its input, its copper and the feed to it are sized
for, and it's plenty to light a boat -- a third of full white across all eight strips, or any
four at full brightness. Past 20 A everything dims together (see "The 20 A budget"). Each output
alone can take a whole 5 m strip at full white.

## How it works

```
  12 V feed (10 AWG, 25 A fuse at the supply) ── J2 ── D1 clamp ── R47 1 mΩ shunt ── the 12 V bus
                                                                      │ U4 INA226 reads it
      the 12 V bus ── F1..F4 ── J3..J6 zone +12V ── strip ── colour wires ── Q1..Q16 to ground
                  └── F5..F8 ── J7..J10 +12V ────── strip ── its own GND pin back to the board

  sensor board J6 ══ patch cable ══ J1 ── U1 PCA9615 ── the board's 5 V I2C bus:
                                            ├── U5 PCA9685 (0x40): 16 PWM outputs ── the 16 MOSFETs' gates
                                            ├── U4 INA226 (0x45): the board's current and voltage
                                            └── Q17/Q18 level shift ── U2 RP2040 (0x30) ── U7..U10 ── J7..J10 data
```

### The link (J1)

The same as the sensor board's end (SENSOR_BOARD.md, "Lights connector"): the I2C bus as
differential I2C on two of the cable's pairs -- 1/2 clock, 3/6 data -- with each pair terminated
at both ends, 620 Ω / 120 Ω / 620 Ω; the other two pairs reserved. **No ground and no power go
down the cable**: the strips' current can only return on the board's own ground wire, and the
PCA9615s tolerate a few volts between the two boards' grounds. Any ordinary straight-through
patch cable works (T568A or T568B at both ends). **Never plug either end into a network switch,
router or PoE injector.**

### Zones (J3-J6)

**Common-anode** 12 V RGBW strips -- the shared wire is +12 V, each colour wire is switched to
ground -- which is nearly every 5-wire strip. Each colour is a BUK9M7R2-40E (40 V, 7 mΩ at a 5 V
gate) driven by the PCA9685 through 100 Ω, with 47 kΩ holding it off while the board powers up.
The PCA9685 runs at 5 V so the gates see 5 V.

Zone *z*'s colour *c* is PCA9685 output **4(z-1) + (R 0, G 1, B 2, W 3)**:

| Zone | Connector | R | G | B | W |
|---|---|---|---|---|---|
| 1 | J3 (top edge, left) | 0 | 1 | 2 | 3 |
| 2 | J4 | 4 | 5 | 6 | 7 |
| 3 | J5 | 8 | 9 | 10 | 11 |
| 4 | J6 (top edge, right) | 12 | 13 | 14 | 15 |

Zone 1's R, G, B are outputs 0, 1, 2 -- the plain `BOAT_LED_DRIVER=pwm` driver's defaults -- so
zone 1 also works with that driver alone.

### Addressable outputs (J7-J10)

**12 V pixel strips**: WS2815 (recommended: its backup data line means one dead pixel doesn't
blank the rest), or 12 V WS2811. The RP2040 makes the four data streams itself (PIO and DMA), so
each output can run its own colour or effect and the link only carries a few bytes when something
changes. U7-U10 buffer the data to 5 V; 330 Ω and a clamp protect each data pin.

Each connector is 4-way: **+12V, DATA, BI, GND**. BI is a WS2815's backup data input, which wants
ground at the strip's start -- it's grounded here. A 3-wire strip (WS2811) leaves BI empty.

5 V strips (WS2812B) aren't supported: 5 m of them would need 18 A at 5 V.

### The 20 A budget

Two layers:

1. **Before anything is sent**, the software works out what the scene will draw -- each zone
   colour's share of `BOAT_LED_ZONE_AMPS` by its PWM duty, each pixel strip by its length,
   brightness and colour at `BOAT_LED_PIXEL_MA` a pixel -- and if that's over `BOAT_LED_MAX_AMPS`
   (20), dims everything by the same factor. Zones through the PCA9685's duty; the pixel strips
   through the RP2040's limit register.
2. **The INA226 measures what really flows** (five times a second, through the 1 mΩ shunt with
   Kelvin sense). If it's still over -- longer strips than configured, say -- it dims further in
   steps until it isn't, and comes back up slowly once there's room.

The fuses are the backstop, and they protect the wires, not the budget.

### Supplies

The logic runs from the 12 V bus through D2 (reverse polarity) and a small buck converter, U6
(AP63205, fixed 5 V: a linear regulator would burn a watt from 14.8 V), then U11 (AP2112, 3.3 V)
for the RP2040. D1 (SMCJ18A) clamps surges on the input; wired backwards, it conducts and blows
the feed's fuse.

---

## Fuses and wiring

Every output has its own **standard blade fuse (ATO/ATC)** in a Littelfuse 178.6165.0002 holder
on the board (rated 30 A). **Size each fuse for the wire it protects**, not for the strip:

| Output | Wire | Fuse |
|---|---|---|
| zone, 5 m RGBW at full white (8 A) | 16 AWG (1.5 mm²) for all five wires | 10 A |
| addressable, 5 m WS2815 (7.5 A) | 16 AWG for +12V and GND; data can be thinner | 10 A |
| a shorter strip, on 18 AWG | 18 AWG | 7.5 A |

The **12 V feed** to J2 is **10 AWG (6 mm²)** from its own **25 A** fuse or breaker at the
supply (J2 is a 24 A screw terminal for 10 AWG), and its ground goes back on 10 AWG to the same
ground point as the Pi's supply. Tinned marine wire throughout; the electronics in a sealed box.

Over 5 m at 12 V, a strip is dimmer at its far end; for long runs, feed +12V and GND to both ends
(from the same fused output).

## Connectors

The outputs are Phoenix Contact **MSTB 2,5 / 5.08 mm pluggable terminal blocks with screw-locking
flanges** (12 A per pin, up to 2.5 mm² wire): the plug screws to the header, so it can't walk out.
Every pin is labelled on the board.

| Connector | Pins | Plug |
|---|---|---|
| **J1** link | RJ45: pairs 1/2, 3/6 | a straight-through Cat5e/Cat6 patch cable to the sensor board's J6 |
| **J2** 12V IN | +12V, GND | screw terminal (METZ CONNECT RT10N02HGLU), 10 AWG |
| **J3-J6** zones 1-4 | +12V, R, G, B, W | MSTB 2,5/ 5-STF-5,08 (1778014) |
| **J7-J10** addressable 1-4 | +12V, DATA, BI, GND | MSTB 2,5/ 4-STF-5,08 (1778001) |
| **J11** USB-C | | firmware loading only |

## The board

178 × 94 mm, two layers, **2 oz copper** (order it that way: the 20 A paths need it).

- The four zone connectors along the top edge, each with its MOSFETs right behind its colour pins;
  the four addressable outputs along the bottom edge; the 12 V input on the right edge, then its
  clamp, shunt and current monitor.
- A 12 V bus across the middle -- a top-layer pour -- with the eight fuses standing in two rows
  along it, the zones' above and the addressable outputs' below, each in line with its output's
  +12V pin.
- The strips' current on 3-4 mm tracks and pours on the top layer only (no vias in its path);
  ground poured on both layers, with a via beside every surface-mount ground pad.
- The PCA9685 in the middle above the bus; the RP2040, the link and the USB-C in the left-hand
  column. The RP2040's supply pins escape inward to vias under the chip, so every signal pin has
  its way out.
- Six M3 holes: the four corners, and two in the middle of the bus with the copper kept 3.4 mm
  away (use nylon screws or plain standoffs there all the same).

## Firmware

The RP2040's firmware is [hardware/led-board/firmware/](hardware/led-board/firmware/) (C, Raspberry
Pi's Pico SDK 2.x). GitHub builds it on every change
([.github/workflows/led-firmware.yml](.github/workflows/led-firmware.yml)): the run's artifact is
`led_board.uf2`. To build it yourself:

```bash
export PICO_SDK_PATH=/path/to/pico-sdk
cmake -S hardware/led-board/firmware -B build && cmake --build build   # build/led_board.uf2
```

**Loading it**: with the board on its 12 V, plug a computer into J11 (USB-C), hold **SW1
(BOOTSEL)**, press and release **SW2 (RESET)**, let go of BOOTSEL: the board appears as a drive
called RPI-RP2. Copy `led_board.uf2` onto it. The yellow LED (D8) blinks once a second while the
firmware runs. (A blank board appears as the drive on its own, without the buttons.)

It's an I2C target at **0x30**. Registers (writes and reads run on through consecutive registers;
a write takes effect at the end of the transfer):

| Register | What |
|---|---|
| 0x00-0x01 | `L` `B` (read-only: identifies the board) |
| 0x02-0x03 | firmware version, major / minor |
| 0x04 | status: bit 0 running |
| 0x05 | frame counter |
| 0x08 | limit, 0-255: scales every output's current, linearly (the budget) |
| 0x10 + 0x10·n | output n (0-3 = J7-J10): +0 mode (0 off, 1 solid, 2 rainbow), +1..+3 R G B, +4 brightness (colour × brightness is gamma-corrected, 2.2, like the zones' PWM, from firmware 1.1), +5 rainbow speed (turns a minute), +6 byte order (0 GRB, 1 RGB, 2 BRG, 3 RBG, 4 GBR, 5 BGR), +7 flags (bit 0 reverse), +8..+9 pixel count (up to 600) |
| 0x7E | write 0xB0: restart as the USB drive, to load new firmware |
| 0x7F | write 0x5A: restart |

Everything starts off; the Pi repeats its settings every two seconds, so the board recovers from a
restart on its own.

## Software

On the Pi (with the sensor board):

```bash
echo "dtoverlay=i2c-gpio,bus=7,i2c_gpio_sda=5,i2c_gpio_scl=6" | sudo tee -a /boot/firmware/config.txt
```

and in `boat.env`:

```bash
BOAT_LED_DRIVER=ledboard
BOAT_LED_I2C_BUS=7
BOAT_LED_PIXELS=300,300,300,300   # pixels on J7, J8, J9, J10 (0 for none)
BOAT_LED_ORDER=GRB                # the pixel strips' byte order (WS2815: GRB)
BOAT_LED_MAX_AMPS=20              # the whole board's budget
BOAT_LED_ZONE_AMPS=8              # one zone's strip at full white
BOAT_LED_PIXEL_MA=25              # one pixel at full white
BOAT_LED_WHITE=true               # zones light whites with their W LEDs
```

The dashboard's lighting controls then drive everything: every zone shows the chosen colour (its
white part on the W LEDs), every addressable strip the same colour, or the rainbow, which the
RP2040 runs itself. The API's lighting state carries the board's measured amps and volts, and how
far the budget has dimmed it (`board`).

## Changing the design

As the sensor board: everything in `hardware/led-board/` is generated; edit the Python.

| File | What it is |
|---|---|
| `design.py` | every part and what each pin connects to: the single source of truth |
| `schematic.py` | draws `led-board.kicad_sch` from it |
| `board.py` | places and routes `led-board.kicad_pcb`; `python board.py --dry-run` does it without KiCad and draws `reports/led-board-dry-run.png` |
| `padgeom.py` | footprint pad geometry from KiCad's library files, for the dry run (checked against pcbnew under KiCad) |
| `router.py` | the grid router |
| `footprints.py` | builds `led-board.pretty`, the small parts with their silkscreen moved to the fab layer |
| `build.py` | all of the above under KiCad, then ERC, DRC with schematic parity, and the fabrication files in `fab/` |
| `kicadpaths.py` | where KiCad is: the Windows install by default, `KICAD_DIR` elsewhere |

```
& "C:\Users\erod9\AppData\Local\Programs\KiCad\10.0\bin\python.exe" build.py
```

## Bring-up

1. Before fitting any fuse, power J2 from a current-limited bench supply at 12 V: the green LED
   (D7) lights; 5 V and 3.3 V are right at C13 and C34; under 100 mA drawn.
2. Load the firmware (above); the yellow LED blinks.
3. Connect the patch cable to the sensor board. On the Pi, `i2cdetect -y 7` shows 0x30, 0x40,
   0x45 (and 0x70, the PCA9685's all-call).
4. One output at a time: fit its fuse, connect its strip, and try it from the dashboard. Watch
   the measured current in the API's lighting state.
