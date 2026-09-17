# DraftHub Device

DraftHub Device is the native touchscreen application for the DraftHub media
controller. It targets small Linux SBCs such as the Radxa Zero 3W and Raspberry
Pi Zero 2 W, starts automatically at boot, and draws directly to `/dev/fb0`
without a desktop environment.

## Current Build

- fullscreen 480x480 touchscreen shell
- Connection, Media, Playlist, and Media Management views
- DraftHub branded header logo with a text fallback
- circular-display safe layout with centred content and inset navigation
- local media and state directories
- systemd service for boot launch and restart
- HTTP media upload endpoint on port `8080`
- raw RGB565LE `.vid` playback path for 800x800 30 FPS content
- MP4 playback through an ffmpeg decode-and-scale stream
- windowed development mode for testing away from the device

## Radxa Zero 3W Setup

1. Flash Radxa OS / Debian for the Zero 3W.
2. Connect Wi-Fi or Ethernet-over-adapter, enable SSH, and copy this repo to
   `/opt/drafthub-pi`.
3. Run the installer:

   ```bash
   cd /opt/drafthub-pi
   sudo ./scripts/install.sh
   ```

4. Reboot:

   ```bash
   sudo reboot
   ```

The installer creates a dedicated `drafthub` service user, adds it to any
available `video`, `render`, and `input` groups, installs Python/pygame/ffmpeg,
creates `/var/lib/drafthub`, and starts `drafthub-pi.service`.

### Dual Wi-Fi Management Network

DraftHub includes an optional NetworkManager-based Wi-Fi setup for the Radxa
Zero 3W. It keeps the player connected to venue Wi-Fi as a normal client while
also providing a local management AP:

```text
Venue Wi-Fi / Internet -> Radxa STA
DraftHub-XXXX -> Radxa AP at http://10.77.x.1:8080/manage
```

Enable it explicitly from the device:

```bash
cd /opt/drafthub-pi
sudo ./scripts/install.sh --enable-networking
```

This installs `network-manager`, `dnsmasq-base`, and `iw`, then enables:

- `drafthub-network.service` to create the virtual AP interface and
  NetworkManager AP profile
- `drafthub-ap-dnsmasq.service` to provide DHCP/DNS only on the DraftHub AP

Each device derives a stable ID from its Wi-Fi MAC address. That ID is used for
the AP SSID, for example `DraftHub-A7F3`, and for a per-device management subnet
in the `10.77.x.0/24` range. This avoids multiple DraftHub units all claiming
the same AP address when they are commissioned or maintained side by side. The
AP does not set up NAT or routing to the venue LAN. Cloud traffic should
continue to use the venue Wi-Fi default route. The AP SSID, AP subnet, device
name, and generated WPA password are stored root-only in:

```text
/etc/drafthub/network.json
```

The installer backs up existing netplan files to `/etc/drafthub/netplan-backup`
before enabling NetworkManager services. Repeated installer runs are idempotent:
they update the same DraftHub profiles/services rather than creating duplicates.

The manager page exposes Wi-Fi status, network scanning, and venue Wi-Fi
credential submission. Passwords are submitted to the local device only and are
not returned by the API or written by the app logs.

The app reads `/sys/class/graphics/fb0/virtual_size` and
`/sys/class/graphics/fb0/bits_per_pixel`, then scales the square DraftHub UI
into the configured framebuffer viewport. Both 16-bit and 32-bit framebuffers
are accepted. The built-in fallback viewport is:

```text
480x480+0+0
```

For a round display that is offset inside a larger HDMI framebuffer, edit
`systemd/drafthub-pi.service` and set:

```ini
Environment=DRAFTHUB_FRAMEBUFFER_VIEWPORT=600x600+100+100
Environment=DRAFTHUB_FRAMEBUFFER_CLEAR=full
Environment=DRAFTHUB_VIDEO_SIZE=800x800
Environment=DRAFTHUB_VIDEO_VIEWPORT=800x800+0+0
```

On the Radxa's 800x800 framebuffer, `600x600+100+100` uses more of the round
screen while keeping a safe 100-pixel margin on each edge.
`DRAFTHUB_FRAMEBUFFER_VIEWPORT` is where the UI is drawn.
`DRAFTHUB_FRAMEBUFFER_CLEAR=full` makes DraftHub blank the whole framebuffer
around that centred UI square.
`DRAFTHUB_VIDEO_SIZE` and `DRAFTHUB_VIDEO_VIEWPORT` keep playback native to the
full 800x800 panel.

Then reload and restart:

```bash
sudo cp /opt/drafthub-pi/systemd/drafthub-pi.service /etc/systemd/system/drafthub-pi.service
sudo systemctl daemon-reload
sudo systemctl restart drafthub-pi.service
```

Useful first-boot checks:

```bash
cat /etc/os-release
ls -l /dev/fb0 /dev/dri
cat /sys/class/graphics/fb0/name
cat /sys/class/graphics/fb0/virtual_size
cat /sys/class/graphics/fb0/bits_per_pixel
journalctl -u drafthub-pi -b -n 80 --no-pager
```

## Raspberry Pi Setup

The same app still works on Raspberry Pi OS Lite. Copy the repo to
`/opt/drafthub-pi`, run `sudo ./scripts/install.sh`, and reboot. The old
`raspberry` account is no longer required because the service runs as the
dedicated `drafthub` user.

For the 480x480 HDMI display bring-up, see
[`docs/hdmi-display.md`](docs/hdmi-display.md).

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

## Playback Smoke Test

DraftHub loops raw RGB565LE `.vid` files directly through the framebuffer
presenter with no runtime video decoder. Stop the service before running a
manual test:

```bash
sudo systemctl stop drafthub-pi.service
PYTHONPATH=/opt/drafthub-pi/app /usr/bin/python3 -m drafthub_pi \
  --play-vid /var/lib/drafthub/media/test.vid --vid-size 800x800 --vid-fps 30
```

Press `Ctrl+C` to stop playback, then restart the touchscreen shell:

```bash
sudo systemctl restart drafthub-pi.service
```

For the Radxa round display, `.vid` files are still the fastest playback format:
convert source media to an `800x800`, 30 FPS `.vid` file before uploading it.
MP4 files can also be uploaded and played; the app uses ffmpeg at playback time
to decode, crop, scale, and stream RGB565 frames into the framebuffer.

## Uploading Media

The touchscreen shell runs a local upload endpoint on port `8080`. In DraftHub
Studio, set the device URL to:

```text
http://<device-ip>:8080
```

The endpoint supports `.vid`, `.rgb565`, `.mp4`, and compressed `.zlib`
transport copies. To upload a converted VID directly from Windows PowerShell:

```powershell
Invoke-WebRequest -Method Post `
  -Uri "http://<device-ip>:8080/upload?name=example.vid" `
  -InFile "C:\path\to\example.vid"
```

Start and stop VID playback:

```powershell
Invoke-WebRequest -Method Post -Uri "http://<device-ip>:8080/play?name=example.vid"
Invoke-WebRequest -Method Post -Uri "http://<device-ip>:8080/stop"
```

Upload and play an MP4:

```powershell
Invoke-WebRequest -Method Post `
  -Uri "http://<device-ip>:8080/upload?name=example.mp4" `
  -InFile "C:\path\to\example.mp4"
Invoke-WebRequest -Method Post -Uri "http://<device-ip>:8080/play?name=example.mp4"
```

List uploaded files:

```text
http://<device-ip>:8080/media-index
```

## Shared Wall Messages

Multiple DraftHub devices on the same venue network can show a synchronized
scrolling message across the group. Each device stores:

- its position in the wall, shown in the manager as device `1`, `2`, etc.
- the total number of devices in the wall
- peer manager URLs to broadcast commands to

For a two-device wall:

1. Open device 1 manager and set `Device #` to `1`, `Total` to `2`.
2. Open device 2 manager and set `Device #` to `2`, `Total` to `2`.
3. On the device you will send from, add the other device URL in `Peer manager
   URLs`, for example `http://192.168.0.64:8080`.
4. Enter a shared message and press `Send`.

The sender posts a shared start time to every peer. Each device renders only
its slice of the virtual wall, so a left-to-right message appears to move from
device 1 to device 2. This first shared-wall implementation is for text
messages; synchronized multi-device video can build on the same peer/config
model once the media files are present on every device.

## Shared Hub Management

One DraftHub manager can act as the local hub for the group. Use the same
`Managed device URLs` box in the Shared Wall section to list every other player
on the venue network, one URL per line.

From the hub manager you can then:

- upload a media file locally and sync it to all managed devices with
  `Upload To All Devices`
- save the local playlist and push the same playlist to all managed devices with
  `Sync To All Devices`
- start the synced playlist on the hub plus every managed device with
  `Play On All Devices`
- send a shared scrolling wall message across the same managed device group

This gives one point of control for media and imagery on a local network. The
current hub model is deliberately local-first: each device still stores its own
copy of media, and the hub pushes files/playlist commands over HTTP to the
other DraftHub managers. That keeps playback resilient if the hub disappears
after content has been synced.

## Dual Wi-Fi Test Procedure

After enabling networking, verify:

```bash
systemctl status NetworkManager drafthub-network drafthub-ap-dnsmasq --no-pager
sudo /usr/local/sbin/drafthub-network status
ip route
```

Test cases:

- Fresh device with no venue profile: its unique `DraftHub-XXXX` SSID is visible
  and `http://<device-ap-address>:8080/manage` loads after joining it. The AP
  address is shown by `sudo /usr/local/sbin/drafthub-network status`.
- Successful venue provisioning: scan, select SSID, enter password, and confirm
  the manager shows the venue SSID plus Internet online.
- Incorrect venue password: connection reports failure and the DraftHub AP stays
  available.
- Venue Wi-Fi unavailable or drops: local cached playback continues and the AP
  remains available.
- Internet unavailable while venue Wi-Fi is connected: manager reports Internet
  offline but the player and AP remain available.
- Venue password changes: connect to the DraftHub AP, submit the new password,
  and confirm reconnection without SSH.
- Reboot: the unique `DraftHub-XXXX` SSID returns, DHCP works, and the venue
  profile reconnects.
- Simultaneous AP + STA: `iw dev` shows the venue managed interface and `dhap0`
  AP interface at the same time.
- Cloud/default traffic: `ip route` default route points at the venue Wi-Fi
  interface, not `dhap0`.

## Logs

Follow the service log:

```bash
journalctl -u drafthub-pi -f
```

To test the same runtime environment manually before restarting the service:

```bash
sudo systemctl stop drafthub-pi.service
sudo install -d -m 0700 -o drafthub -g drafthub /run/drafthub-pi
sudo -u drafthub env PYTHONPATH=/opt/drafthub-pi/app XDG_RUNTIME_DIR=/run/drafthub-pi \
  /usr/bin/python3 -m drafthub_pi
```

If the framebuffer cannot initialize, collect:

```bash
journalctl -u drafthub-pi -b --no-pager
systemctl show drafthub-pi.service -p Environment -p RuntimeDirectory -p User
ls -ld /run/drafthub-pi
ls -l /dev/fb0 /dev/dri
id drafthub
```

## Next Steps

- confirm the Radxa Zero 3W framebuffer mode and bit depth on the target image
- tune `DRAFTHUB_FRAMEBUFFER_VIEWPORT` for the round panel
- identify and test the USB HID touchscreen input device
- optimize 32-bit framebuffer VID output if Radxa does not expose RGB565 fbdev
- add playlist persistence with SQLite
