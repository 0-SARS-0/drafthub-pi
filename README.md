# DraftHub Pi

DraftHub Pi is the native touchscreen application for a Raspberry Pi Zero 2 W
with a 480x480 display. It is intended to run on Raspberry Pi OS Lite and start
automatically at boot without a desktop environment.

## First milestone

- fullscreen 480x480 touchscreen shell
- Now Playing, Library, and Settings views
- local media and state directories
- systemd service for boot launch and restart
- windowed development mode for testing away from the Pi

## Raspberry Pi setup

1. Flash Raspberry Pi OS Lite 32-bit with Raspberry Pi Imager.
2. Enable Wi-Fi and SSH in the Imager customisation screen.
3. Copy this repo to `/opt/drafthub-pi`.
4. Run:

   ```bash
   sudo ./scripts/install.sh
   ```

5. Reboot:

   ```bash
   sudo reboot
   ```

The service starts `python3 -m drafthub_pi` as the `raspberry` user. This is the
DraftHub device account selected during Raspberry Pi OS setup.

For the 480x480 HDMI display bring-up, follow
[`docs/hdmi-display.md`](docs/hdmi-display.md). Start with HDMI auto-detection
before forcing a custom console mode.

## Development

Install Python 3 and pygame, then run:

```bash
python -m drafthub_pi --windowed
```

From the repo root on Windows:

```powershell
$env:PYTHONPATH = "$PWD\app"
python -m drafthub_pi --windowed
```

## Logs

On the Pi:

```bash
journalctl -u drafthub-pi -f
```

## Next steps

- confirm whether the HDMI display advertises its 480x480 mode through EDID
- identify and test the USB HID touchscreen input device
- add playback for DraftHub `.vid` media
- add a local upload API and device pairing
- add playlist persistence with SQLite

