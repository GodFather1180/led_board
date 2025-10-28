#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
64x32 now-playing — big logo + big centered title (upper-right) + one small scrolling lyric line below.
- No "next line" preview at all.
- Guaranteed gap between title and lyrics.
- Lyrics baseline is forced BELOW both the album art height and the title.

.env:
  SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI
  LED_HARDWARE=adafruit-hat, LED_ROWS=32, LED_COLS=64, LED_GPIO_SLOWDOWN=5, LED_RGB_SEQUENCE=RGB, LED_BRIGHTNESS=65
  FONT_PATH_TITLE=/home/pi/rpi-rgb-led-matrix/fonts/6x10.bdf   # bigger title
  FONT_PATH_LYRIC=/home/pi/rpi-rgb-led-matrix/fonts/4x6.bdf    # small lyrics
  ALBUM_SIDE=28
  TITLE_SCROLL_SPEED=3     # chars/sec when title is too long
  LYRIC_SCROLL_SPEED=6     # chars/sec for lyric line
  GAP_CHARS=3              # spaces between marquee loops
  TITLE_LYRIC_GAP_PX=3     # pixels gap between title baseline and lyric baseline (min; lyric may be pushed lower by album)
  TITLE_BASELINE_PX=12     # where to place title baseline on the right (aim "middle bit upper")
  LRC_OFFSET_MS=0
  POLL_MS=900              # throttle Spotify polling to avoid lag
"""

import os
import sys
import time
import threading
import queue
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlparse, parse_qs
from io import BytesIO

import requests
from dotenv import load_dotenv
from PIL import Image

from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# ---------------- env & defaults ----------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "https://oauth.pstmn.io/v1/callback").strip()
SCOPE = "user-read-currently-playing user-read-playback-state"
CACHE_PATH = os.path.join(BASE_DIR, ".cache-spotify-matrix")

if not CLIENT_ID or not CLIENT_SECRET:
    print("Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET in .env", file=sys.stderr)
    sys.exit(1)

# ------------- helpers -------------
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def env_int(key, default):
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return default

# ---------- matrix & fonts ----------
def build_matrix_and_fonts():
    opts = RGBMatrixOptions()
    opts.hardware_mapping = os.getenv("LED_HARDWARE", "adafruit-hat")
    opts.rows            = int(os.getenv("LED_ROWS", "32"))
    opts.cols            = int(os.getenv("LED_COLS", "64"))
    opts.chain_length    = int(os.getenv("LED_CHAIN", "1"))
    opts.parallel        = int(os.getenv("LED_PARALLEL", "1"))
    opts.gpio_slowdown   = int(os.getenv("LED_GPIO_SLOWDOWN", "5"))
    opts.brightness      = int(os.getenv("LED_BRIGHTNESS", "65"))
    rgb_seq = os.getenv("LED_RGB_SEQUENCE", "RGB").upper()
    if len(rgb_seq) == 3:
        # attribute name differs across versions; try both
        try:
            opts.led_rgb_sequence = rgb_seq  # older lib
        except Exception:
            try:
                opts.rgb_sequence = rgb_seq   # newer lib
            except Exception:
                pass

    matrix = RGBMatrix(options=opts)

    def load_font(candidates) -> Tuple["graphics.Font", str, Tuple[int, int]]:
        font = graphics.Font()
        for path in candidates:
            if path and os.path.isfile(path):
                try:
                    font.LoadFont(path)
                    return font, path, guess_char_px(path)
                except Exception:
                    pass
        print("Could not load any BDF font from:", candidates, file=sys.stderr)
        sys.exit(1)

    title_candidates = [
        os.getenv("FONT_PATH_TITLE", "").strip(),
        "/home/pi/rpi-rgb-led-matrix/fonts/6x10.bdf",
        "/home/pi/rpi-rgb-led-matrix/fonts/7x13.bdf",
        "/home/pi/rpi-rgb-led-matrix/fonts/5x7.bdf",
    ]
    lyric_candidates = [
        os.getenv("FONT_PATH_LYRIC", "").strip(),
        "/home/pi/rpi-rgb-led-matrix/fonts/4x6.bdf",
        "/home/pi/rpi-rgb-led-matrix/fonts/tom-thumb.bdf",
        "/home/pi/rpi-rgb-led-matrix/fonts/5x7.bdf",
    ]

    title_font, _tp, (tw, th) = load_font(title_candidates)
    lyric_font, _lp, (lw, lh) = load_font(lyric_candidates)
    return matrix, (title_font, (tw, th)), (lyric_font, (lw, lh))

def guess_char_px(font_path: str) -> Tuple[int, int]:
    name = os.path.basename(font_path).lower()
    if "tom-thumb" in name: return 3, 6
    if "4x6" in name:      return 4, 7
    if "5x7" in name:      return 5, 8
    if "6x9" in name:      return 6, 10
    if "6x10" in name:     return 6, 11
    if "7x13" in name:     return 7, 14
    return 6, 11

# ------------- images -------------
def fetch_album_image(url: str, side: int) -> Optional[Image.Image]:
    if not url:
        return None
    for i in range(2):
        try:
            r = requests.get(url, timeout=6, headers={"Connection": "close"})
            r.raise_for_status()
            img = Image.open(BytesIO(r.content)).convert("RGB").resize((side, side), Image.LANCZOS)
            return img
        except Exception as e:
            if i == 1:
                print(f"Album art fetch failed: {e}", file=sys.stderr)
            time.sleep(0.15)
    return None

def blit_pillow(canvas, img: Image.Image, x0: int, y0: int) -> None:
    w, h = img.size
    pix = img.load()
    for y in range(h):
        for x in range(w):
            r, g, b = pix[x, y]
            canvas.SetPixel(x0 + x, y0 + y, int(r), int(g), int(b))

def draw_rect(canvas, x0, y0, x1, y1, color):
    graphics.DrawLine(canvas, x0, y0, x1, y0, color)  # top
    graphics.DrawLine(canvas, x1, y0, x1, y1, color)  # right
    graphics.DrawLine(canvas, x1, y1, x0, y1, color)  # bottom
    graphics.DrawLine(canvas, x0, y1, x0, y0, color)  # left


# ---------------- lyrics ----------------
@dataclass
class LyricLine:
    t: float
    text: str

def parse_lrc(text: str) -> List[LyricLine]:
    out: List[LyricLine] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        parts = raw.split("]")
        lyric = parts[-1].strip()
        for p in parts[:-1]:
            if p.startswith("["):
                tag = p[1:]
                try:
                    m, s = tag.split(":")
                    out.append(LyricLine(int(m) * 60 + float(s), lyric))
                except Exception:
                    pass
    out.sort(key=lambda a: a.t)
    return out

def fetch_lyrics(track: str, artist: str) -> Tuple[List[LyricLine], Optional[str]]:
    try:
        r = requests.get(
            "https://lrclib.net/api/get",
            params={"track_name": track, "artist_name": artist},
            timeout=6,
        )
        if r.status_code == 200:
            data = r.json()
            synced = (data.get("syncedLyrics") or "").strip()
            plain  = (data.get("plainLyrics") or "").strip()
            if synced:
                return parse_lrc(synced), None
            if plain:
                return [], plain
    except Exception as e:
        print(f"Lyrics fetch failed: {e}", file=sys.stderr)
    return [], None

def current_line_index(lrc: List[LyricLine], t: float) -> int:
    if not lrc:
        return -1
    lo, hi, ans = 0, len(lrc) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if lrc[mid].t <= t:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans

# ------------- spotify auth --------------
def ensure_auth() -> SpotifyOAuth:
    sp_oauth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=CACHE_PATH,
        open_browser=False,
        show_dialog=False,
    )
    token = sp_oauth.get_cached_token()
    if token:
        return sp_oauth

    url = sp_oauth.get_authorize_url()
    print("\nOpen this URL:\n", url)
    print("\nAfter login you'll be redirected to:", REDIRECT_URI)
    pasted = input("Paste the FULL redirected URL or JUST the 'code': ").strip()

    code = pasted
    if pasted.startswith("http"):
        try:
            code = parse_qs(urlparse(pasted).query).get("code", [""])[0]
        except Exception:
            code = ""
    if not code:
        print("No authorization code provided.")
        sys.exit(1)

    sp_oauth.get_access_token(code, check_cache=False)
    print("Spotify token cached in", CACHE_PATH)
    return sp_oauth

# ---------------- worker threads ----------------
class PlaybackSnapshot:
    __slots__ = ("item", "is_playing", "progress_ms", "ts")
    def __init__(self, item=None, is_playing=False, progress_ms=0, ts=0.0):
        self.item = item
        self.is_playing = is_playing
        self.progress_ms = progress_ms
        self.ts = ts

class Poller(threading.Thread):
    """Polls Spotify on an interval and emits PlaybackSnapshot objects."""
    def __init__(self, sp, out_q, poll_ms: int):
        super().__init__(daemon=True)
        self.sp = sp
        self.out_q = out_q
        self.poll_ms = max(300, poll_ms)
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                pb = self.sp.current_user_playing_track()
                ts = time.time()
                if pb and pb.get("item"):
                    snap = PlaybackSnapshot(
                        item=pb["item"],
                        is_playing=bool(pb.get("is_playing")),
                        progress_ms=int(pb.get("progress_ms") or 0),
                        ts=ts,
                    )
                else:
                    snap = PlaybackSnapshot(None, False, 0, ts)
                try:
                    # keep only the most recent snapshot
                    while True:
                        self.out_q.get_nowait()
                except queue.Empty:
                    pass
                self.out_q.put_nowait(snap)
            except Exception:
                # swallow errors but keep polling
                pass
            time.sleep(self.poll_ms / 1000.0)

    def stop(self):
        self._stop.set()

class TrackAssets:
    __slots__ = ("album_img", "lrc", "plain_lines", "title", "artists", "tid")
    def __init__(self):
        self.album_img = None
        self.lrc: List[LyricLine] = []
        self.plain_lines: Optional[List[str]] = None
        self.title = ""
        self.artists = ""
        self.tid = None

class Fetcher(threading.Thread):
    """Fetches album art and lyrics when a new track dict is queued."""
    def __init__(self, in_q, out_q, album_side: int):
        super().__init__(daemon=True)
        self.in_q = in_q
        self.out_q = out_q
        self.album_side = album_side
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                item = self.in_q.get(timeout=0.5)  # track dict
            except queue.Empty:
                continue

            assets = TrackAssets()
            assets.tid = item.get("id") or ""
            assets.title = item.get("name", "") or ""
            assets.artists = ", ".join(a.get("name", "") for a in item.get("artists", []))

            # Album art
            imgs = item.get("album", {}).get("images", [])
            aurl = None
            if imgs:
                try:
                    target = max(32, self.album_side)
                    aurl = min(imgs, key=lambda im: abs(int(im.get("width", 64)) - target)).get("url")
                except Exception:
                    aurl = imgs[-1].get("url")
            assets.album_img = fetch_album_image(aurl, self.album_side) if aurl else None

            # Lyrics (synced or plain)
            lrc, plain = fetch_lyrics(assets.title, assets.artists)
            assets.lrc = lrc
            if plain:
                assets.plain_lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]

            try:
                # keep only the most recent assets
                while True:
                    self.out_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.out_q.put_nowait(assets)
            except queue.Full:
                pass

    def stop(self):
        self._stop.set()

# ------------- text layout & marquee (tick-based) -------------
def marquee_slice_tick(text: str, max_chars: int, tick: int, cps: float, gap: int, fps: float) -> str:
    """Scrolls text using integer ticks for stable motion across uneven frames."""
    if len(text) <= max_chars:
        return text
    step_per_frame = cps / fps  # chars per frame
    offset = int(tick * step_per_frame) % (len(text) + gap)
    loop = text + (" " * gap) + text
    return loop[offset: offset + max_chars]

# ------------- main loop -----------------
def main():
    print("Tip: better stability if you set 'isolcpus=3' in /boot/cmdline.txt")

    # Spotipy with request timeout to avoid render stalls
    sp = spotipy.Spotify(
        auth_manager=ensure_auth(),
        requests_timeout=2
    )

    matrix, (title_font, (tw, th)), (lyric_font, (lw, lh)) = build_matrix_and_fonts()
    canvas = matrix.CreateFrameCanvas()

    # Layout + tuning (compute invariants once)
    ALBUM = env_int("ALBUM_SIDE", 28)
    right_x = ALBUM + 2
    right_w = matrix.width - right_x

    TITLE_CPS  = max(1, env_int("TITLE_SCROLL_SPEED", 3))
    LYRIC_CPS  = max(1, env_int("LYRIC_SCROLL_SPEED", 6))
    GAP        = max(1, env_int("GAP_CHARS", 3))
    LRC_OFFSET = env_int("LRC_OFFSET_MS", 0) / 1000.0
    POLL_MS    = max(300, env_int("POLL_MS", 900))
    TITLE_GAP  = max(0, env_int("TITLE_LYRIC_GAP_PX", 3))
    TITLE_BASE = clamp(env_int("TITLE_BASELINE_PX", 12), th, matrix.height - lh - 1)

    max_title_chars = max(1, right_w // tw)
    max_lyric_chars = max(1, right_w // lw)
    lyric_base      = max(TITLE_BASE + TITLE_GAP + lh, ALBUM + 1)

    white = graphics.Color(255, 255, 255)
    dim   = graphics.Color(170, 170, 170)

    # Queues & threads
    pb_q    = queue.Queue(maxsize=2)     # Poller -> main
    req_q   = queue.Queue(maxsize=1)     # main  -> Fetcher (track dict)
    asset_q = queue.Queue(maxsize=1)     # Fetcher -> main

    poller  = Poller(sp, pb_q, POLL_MS)
    fetcher = Fetcher(req_q, asset_q, ALBUM)
    poller.start()
    fetcher.start()

    # Live state
    cur_assets = TrackAssets()
    last_seen_tid = None
    last_snapshot = PlaybackSnapshot(None, False, 0, time.time())

    target_fps = 30.0  # smoother with tick-based scroll
    frame_dt = 1.0 / target_fps
    tick = 0

    try:
        while True:
            frame_start = time.time()

            # Drain latest playback snapshot (non-blocking)
            while True:
                try:
                    last_snapshot = pb_q.get_nowait()
                except queue.Empty:
                    break

            item = last_snapshot.item
            is_playing = last_snapshot.is_playing

            # On track change, request new assets (non-blocking)
            tid = (item.get("id") if item else None)
            if tid and tid != last_seen_tid:
                last_seen_tid = tid
                # Drop any pending request and enqueue newest
                try:
                    while True:
                        req_q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    req_q.put_nowait(item)
                except queue.Full:
                    pass

            # Incorporate newly fetched assets (non-blocking)
            try:
                cur_assets = asset_q.get_nowait()
            except queue.Empty:
                pass

            # Compute progression locally to avoid polling jitter
            if item and is_playing:
                elapsed_ms = int((time.time() - last_snapshot.ts) * 1000.0)
                progress_ms = last_snapshot.progress_ms + elapsed_ms
            else:
                progress_ms = last_snapshot.progress_ms if item else 0
            t_sec = float(progress_ms) / 1000.0 + LRC_OFFSET

            # ---- Draw frame (no blocking I/O) ----
            canvas.Clear()

            if not item:
                graphics.DrawText(canvas, lyric_font, right_x, lyric_base, dim, "Nothing")
                canvas = matrix.SwapOnVSync(canvas)
                time.sleep(0.25)
                continue

            # LEFT: big album art
            if cur_assets.album_img:
                blit_pillow(canvas, cur_assets.album_img, 0, 0)
            else:
                draw_rect(canvas, 0, 0, ALBUM - 1, ALBUM - 1, dim)


            # ---------- RIGHT: BIG TITLE (centered if it fits; scroll if long) ----------
            ttxt = cur_assets.title if cur_assets.title else "(untitled)"
            if len(ttxt) <= max_title_chars:
                px = len(ttxt) * tw
                cx = right_x + (right_w - px) // 2
                graphics.DrawText(canvas, title_font, cx, TITLE_BASE, white, ttxt)
            else:
                tdraw = marquee_slice_tick(ttxt, max_title_chars, tick, TITLE_CPS, GAP, target_fps)
                graphics.DrawText(canvas, title_font, right_x, TITLE_BASE, white, tdraw)

            # ---------- RIGHT: ONE SMALL SCROLLING LYRIC LINE UNDER TITLE & LOGO ----------
            # baseline must be below title AND below album art
            if cur_assets.lrc:
                idx = current_line_index(cur_assets.lrc, t_sec)
                line_now = (cur_assets.lrc[idx].text if idx >= 0 else cur_assets.artists)
                l1 = marquee_slice_tick(line_now, max_lyric_chars, tick, LYRIC_CPS, GAP, target_fps)
                graphics.DrawText(canvas, lyric_font, right_x, lyric_base, white, l1)
            elif cur_assets.plain_lines:
                # plain lyric: cycle every 2s, then scroll
                which = (int((tick / target_fps) / 2) % len(cur_assets.plain_lines)) if cur_assets.plain_lines else 0
                cur = cur_assets.plain_lines[which] if cur_assets.plain_lines else cur_assets.artists
                l1 = marquee_slice_tick(cur, max_lyric_chars, tick, LYRIC_CPS, GAP, target_fps)
                graphics.DrawText(canvas, lyric_font, right_x, lyric_base, white, l1)
            else:
                # fallback: scroll artist
                l1 = marquee_slice_tick(cur_assets.artists, max_lyric_chars, tick, LYRIC_CPS, GAP, target_fps)
                graphics.DrawText(canvas, lyric_font, right_x, lyric_base, white, l1)

            canvas = matrix.SwapOnVSync(canvas)

            # Tick / frame pacing
            tick += 1
            dt = time.time() - frame_start
            sleep_for = frame_dt - dt
            if sleep_for > 0:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            poller.stop()
            fetcher.stop()
        except Exception:
            pass

if __name__ == "__main__":
    main()
