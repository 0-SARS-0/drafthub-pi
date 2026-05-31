# HDMI Display Bring-Up

The DraftHub display connects over HDMI. On a Raspberry Pi Zero 2 W, use a
mini-HDMI to HDMI cable or adapter and connect the display before powering on
the Pi.

## Start with auto-detection

Raspberry Pi OS uses the display EDID to choose a resolution automatically. Do
not force a display mode until the panel has been tested with the default
configuration.

After booting Raspberry Pi OS Lite, check the active HDMI connector and modes:

```bash
cat /sys/class/drm/card?-HDMI-A-1/status
cat /sys/class/drm/card?-HDMI-A-1/modes
```

If `480x480` appears in the modes list and the console fills the panel, keep the
default configuration.

## Force a 480x480 console mode only if needed

Recent Raspberry Pi OS Lite releases use KMS console settings in
`/boot/firmware/cmdline.txt`. That file must remain a single line.

1. Back up the original file:

   ```bash
   sudo cp /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.backup
   ```

2. Append this value to the existing line, separated by a space:

   ```text
   video=HDMI-A-1:480x480M@60
   ```

3. Reboot:

   ```bash
   sudo reboot
   ```

The same setting is stored in
[`config/cmdline.hdmi-480x480.example.txt`](../config/cmdline.hdmi-480x480.example.txt)
for reference.

If the panel remains blank, restore the backup SD card file from another
computer or over SSH. Some HDMI panels need vendor-specific timings, so record
the display model before adding further overrides.

## Touch input

HDMI carries the image, but touchscreen input is usually a separate USB cable.
After connecting the touch cable, inspect the detected devices:

```bash
cat /proc/bus/input/devices
ls -l /dev/input/by-id /dev/input/by-path
```
