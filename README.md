# LED Board — Spotify “Now Playing” + Text Scroller (Raspberry Pi)

Drives a 64×32 RGB LED matrix with:
- **Spotify Now Playing**: album art + centered title + one scrolling lyric line
- **Text Scroller**: simple web-controlled marquee

Single-process per mode, smooth tick-based scrolling, and no blocking I/O in the render loop.

## Setup
```bash
cp .env.example .env
# fill in .env (Spotify keys if using Spotify mode)
./run.sh deps
Run
Spotify mode
./run.sh spotify
Scroller (web)
./run.sh scroll
Open http://<pi-ip>:8080 (or http://led.local:8080 with mDNS).
Only one process can own the matrix at a time. Stop one before starting the other.
Tips
Colors wrong? Try LED_RGB_SEQUENCE=BGR in .env.
School Wi-Fi blocking devices? Use your phone hotspot or Cloudflare Tunnel.
Spotify redirect URI must match your Spotify app settings.
License
MIT
