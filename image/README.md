# The boat MFD's system image

A system of its own for the Pi, instead of Raspberry Pi OS with the dashboard added
([kiosk/](../kiosk/)): power on, the boot logo ("Starting up..."), then the dashboard, and
nothing else on screen at any point. It is built with Raspberry Pi's own image builder,
[rpi-image-gen](https://github.com/raspberrypi/rpi-image-gen), on GitHub
([.github/workflows/image.yml](../.github/workflows/image.yml)) whenever the dashboard or
this folder changes.

What's in it:

- Minimal Debian 13 from Raspberry Pi's A/B configuration: **two copies of a read-only system**
  (an update goes into the other copy; one that fails to boot goes back to the one that worked),
  and a data partition for everything that changes. Cutting the power with the switch can't
  damage the system, because nothing writes to it.
- The dashboard's server (`boat-dashboard.service`) and the display (`boatmfd-kiosk.service`:
  labwc and Chromium, the same session as [kiosk/](../kiosk/)). No desktop, no login screen.
- SSH, by key only; the network (systemd-networkd, with iwd for WiFi); `boatmfd.local` on the
  local network.
- The sensor board's hardware turned on in `config.txt`: I2C, SPI and its CAN controller, and the
  LED board's I2C bus (7).

| Where | What |
|---|---|
| `/opt/boatmfd` | The dashboard (read-only) and the charts it was built with |
| `/var/lib/boatmfd` | What the boat writes: tracks, waypoints, settings, calibration |
| `/var/lib/boatmfd/boat.env` | The boat's settings ([README](../README.md): `BOAT_SENSORS=real`, `BOAT_CAN=can0`, ...), one `NAME=value` per line; then `sudo systemctl restart boat-dashboard` |
| `/home/admin` | The dashboard browser's profile (its screens and units) and SSH keys |

To see what it's doing: `journalctl -u boat-dashboard` and `journalctl -u boatmfd-kiosk`.
Nothing in the image is private. SSH keys and WiFi passwords go onto the drive after the image is
written, and it picks them up at boot (`/usr/lib/boatmfd/apply-settings`).

## The Pi 4 test build, on a USB flash drive

[config/boatmfd-pi4.yaml](config/boatmfd-pi4.yaml). The Pi 4 boots from USB when there's no SD
card in it, so the SD card's Raspberry Pi OS stays as it is: take the flash drive out and put the
card back to go back to it.

rpi-image-gen's A/B system only boots from an SD card or NVMe; `usb_boot`
([layer/boatmfd.d/usb-boot.sh](layer/boatmfd.d/usb-boot.sh)) adds USB, as long as the flash
drive is the only USB drive plugged in at boot.

From the Pi, with the flash drive plugged in (here `/dev/sda`: check with `lsblk`):

```bash
sudo ~/boatmfd/image/flash-from-pi.sh https://github.com/erod998/boatmfd/releases/download/pi4-test/boatmfd-pi4.img.zst /dev/sda
```

It asks before erasing the drive, writes the image, and copies this Pi's SSH keys (the computers
that can log in to it now) and its WiFi networks onto it. Then `sudo poweroff`, take out the SD
card, and power on. Log in with `ssh admin@boatmfd.local`, or at its address on the router.

## The boat's Pi 5

The same with `device.layer: rpi5`, booting from its SD card or NVMe, without `usb_boot`. A USB
drive then holds more charts (to come).

## Building it yourself

On a 64-bit Raspberry Pi OS or Debian arm64 machine (a Pi 5 will do):

```bash
git clone https://github.com/raspberrypi/rpi-image-gen && cd rpi-image-gen && sudo ./install_deps.sh
sudo apt install python3-pil
python3 ~/boatmfd/image/make-splash.py ~/boatmfd/kiosk/plymouth/splash.png ~/boatmfd/image/build/splash.tga
./rpi-image-gen build -S ~/boatmfd/image -c boatmfd-pi4.yaml
```
