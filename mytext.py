#!/usr/bin/env python3
import argparse, time, re
from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics

def parse_color(s: str):
    # Accept "#RRGGBB" or "R,G,B"
    if re.match(r"^#?[0-9a-fA-F]{6}$", s):
        s = s.lstrip("#")
        return graphics.Color(int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    if re.match(r"^\d{1,3},\d{1,3},\d{1,3}$", s):
        r, g, b = map(int, s.split(","))
        return graphics.Color(r, g, b)
    raise argparse.ArgumentTypeError("Color must be #RRGGBB or R,G,B")

def main():
    ap = argparse.ArgumentParser(description="Scroll text on an RGB LED matrix")
    ap.add_argument("--text", "-t", default="HELLO WORLD!", help="Message to scroll")
    ap.add_argument("--font", "-f", default="/home/pi/led-project/10x20.bdf", help="Path to .bdf font")
    ap.add_argument("--color", "-c", type=parse_color, default=parse_color("#FF0000"), help="Text color (#RRGGBB or R,G,B)")
    ap.add_argument("--speed", "-s", type=float, default=60.0, help="Scroll speed in pixels/second")
    ap.add_argument("--fps", type=float, default=60.0, help="Max frame rate")
    ap.add_argument("--brightness", type=int, default=75, help="Matrix brightness (0-100)")
    # Hardware/matrix options (adjust if your setup is different)
    ap.add_argument("--hardware-mapping", default="adafruit-hat", help='e.g. "adafruit-hat", "regular"')
    ap.add_argument("--rows", type=int, default=32)
    ap.add_argument("--cols", type=int, default=64)
    ap.add_argument("--chain-length", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--gpio-slowdown", type=int, default=4, help="Useful on Pi 4")
    ap.add_argument("--repeat", type=int, default=0, help="Times to repeat (0 = forever)")
    args = ap.parse_args()

    # Matrix setup
    opts = RGBMatrixOptions()
    opts.hardware_mapping = args.hardware_mapping
    opts.rows = args.rows
    opts.cols = args.cols
    opts.chain_length = args.chain_length
    opts.parallel = args.parallel
    opts.brightness = max(0, min(100, args.brightness))
    opts.gpio_slowdown = args.gpio_slowdown

    matrix = RGBMatrix(options=opts)
    off = matrix.CreateFrameCanvas()

    # Graphics setup
    font = graphics.Font()
    font.LoadFont(args.font)
    color = args.color

    # Compute starting position and baseline (vertically centered)
    baseline_y = (off.height + font.height) // 2 - 1
    x = off.width

    # Measure text width by drawing once on a cleared buffer (then immediately clear)
    off.Clear()
    text_width = graphics.DrawText(off, font, 0, baseline_y, color, args.text)
    off.Clear()

    # Timing
    min_dt = 1.0 / max(1.0, args.fps)
    px_per_frame = max(1.0, args.speed * min_dt)

    loops_done = 0
    try:
        while True:
            off.Clear()
            # Draw the text at current x
            graphics.DrawText(off, font, int(x), baseline_y, color, args.text)

            # Swap buffers (vsync if available)
            off = matrix.SwapOnVSync(off)

            # Advance position
            x -= px_per_frame

            # When text fully left of screen, reset (and count loop)
            if x + text_width < 0:
                x = off.width
                if args.repeat > 0:
                    loops_done += 1
                    if loops_done >= args.repeat:
                        break

            time.sleep(min_dt)
    except KeyboardInterrupt:
        pass
    finally:
        off.Clear()
        matrix.SwapOnVSync(off)

if __name__ == "__main__":
    main()
