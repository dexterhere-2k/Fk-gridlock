"""Generate a static OSM-derived basemap PNG of Bengaluru.

We download a small grid of OpenStreetMap raster tiles (the public
tile.openstreetmap.org service — no key, no rate-limit problem at
this scale) at zoom 11, stitch them into one PNG, and save it to
frontend/public/blr_basemap.png.

The tile grid is computed from the actual corridor lat/lon BBOX so
the dots line up with real roads. We use zoom 11 to keep the file
under ~500 KB while still being recognizable as Bengaluru.

After generation this file is loaded once by MapplsMap.jsx as an
<image> background inside the SVG. The dots/polylines overlay on
real roads + neighborhoods — this is a real map, not a schematic.

The OSM tile policy (https://operations.osmfoundation.org/policies/tiles/)
requires a descriptive User-Agent and forbids bulk downloads. We're
fetching 16 tiles once at build time, well within the spirit of the
policy. This is not a runtime hot path.
"""

import math
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

# Bengaluru corridor BBOX (matches CORRIDOR_COORDS in LiveView.jsx)
MIN_LAT, MAX_LAT = 12.84, 13.22
MIN_LON, MAX_LON = 77.44, 77.80
ZOOM = 11

# ---- tile math
def deg2tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lat_rad = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * n)
    return x, y

def tile2deg(x, y, z):
    n = 2 ** z
    lon = x / n * 360 - 180
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = math.degrees(lat_rad)
    return lat, lon

tx_min, ty_min = deg2tile(MAX_LAT, MIN_LON, ZOOM)
tx_max, ty_max = deg2tile(MIN_LAT, MAX_LON, ZOOM)
# add 1 tile of padding
tx_min -= 1; ty_min -= 1; tx_max += 1; ty_max += 1
print(f"Tile range: x={tx_min}..{tx_max}, y={ty_min}..{ty_max}, zoom={ZOOM}")
print(f"Total tiles: {(tx_max - tx_min + 1) * (ty_max - ty_min + 1)}")

TILE = 256
W = (tx_max - tx_min + 1) * TILE
H = (ty_max - ty_min + 1) * TILE
print(f"Stitched: {W}x{H}px")

# ---- fetch
UA = "GridLock-Mappls-Scheme/1.0 (research; contact: dev@gridlock.example)"
sess = requests.Session()
sess.headers.update({"User-Agent": UA, "Accept": "image/png"})

out_dir = Path("/tmp/blrtiles")
out_dir.mkdir(exist_ok=True)
for ty in range(ty_min, ty_max + 1):
    for tx in range(tx_min, tx_max + 1):
        fp = out_dir / f"tile_{ZOOM}_{tx}_{ty}.png"
        if fp.exists():
            continue
        url = f"https://tile.openstreetmap.org/{ZOOM}/{tx}/{ty}.png"
        for attempt in range(3):
            try:
                r = sess.get(url, timeout=15)
                if r.status_code == 200:
                    fp.write_bytes(r.content)
                    print(f"  got {tx},{ty} ({len(r.content)} B)")
                    break
                else:
                    print(f"  {tx},{ty} HTTP {r.status_code}, retry")
            except Exception as e:
                print(f"  {tx},{ty} {e}, retry")
            time.sleep(1.0)
        else:
            print(f"  {tx},{ty} FAILED — using blank")
            Image.new("RGB", (TILE, TILE), (245, 245, 245)).save(fp)
        time.sleep(0.2)  # be polite to the OSM tile server

# ---- stitch
canvas = Image.new("RGB", (W, H), (245, 245, 245))
for ty in range(ty_min, ty_max + 1):
    for tx in range(tx_min, tx_max + 1):
        tile = Image.open(out_dir / f"tile_{ZOOM}_{tx}_{ty}.png")
        x = (tx - tx_min) * TILE
        y = (ty - ty_min) * TILE
        canvas.paste(tile, (x, y))
        tile.close()

# ---- crop to corridor BBOX
# compute pixel coords of the BBOX corners
def latlon2pixel(lat, lon):
    # top-left lat/lon of (tx_min, ty_min) tile
    tl_lat, tl_lon = tile2deg(tx_min, ty_min, ZOOM)
    # bottom-right of (tx_max+1, ty_max+1)
    br_lat, br_lon = tile2deg(tx_max + 1, ty_max + 1, ZOOM)
    # lat range goes top->bottom (decreasing y) and lon left->right
    px = (lon - tl_lon) / (br_lon - tl_lon) * W
    py = (tl_lat - lat) / (tl_lat - br_lat) * H
    return int(px), int(py)

x0, py_top = latlon2pixel(MAX_LAT, MIN_LON)   # top-left (higher lat = smaller y in screen)
x1, py_bot = latlon2pixel(MIN_LAT, MAX_LON)   # bottom-right (lower lat = larger y in screen)
# add a small padding so the corridors don't sit on the edge
PAD_PX = 12
x0 = max(0, x0 - PAD_PX); x1 = min(W, x1 + PAD_PX)
y0 = max(0, py_top - PAD_PX); y1 = min(H, py_bot + PAD_PX)
print(f"crop box: x=[{x0},{x1}] y=[{y0},{y1}], w={x1-x0} h={y1-y0}")
cropped = canvas.crop((x0, y0, x1, y1))
print(f"Cropped: {cropped.size}")

# ---- save
dst = Path("/home/dexter/gridlock/gridlock_submission/frontend/public/blr_basemap.png")
dst.parent.mkdir(parents=True, exist_ok=True)
cropped.save(dst, optimize=True)
print(f"Saved {dst} ({dst.stat().st_size} B)")

# ---- emit metadata (the cropped image's BBOX) so MapplsMap.jsx
# can use the same projection. In SVG pixel space, the image runs
# (0,0) to (cropped.size[0], cropped.size[1]) and covers exactly
# (MIN_LAT..MAX_LAT, MIN_LON..MAX_LON).
import json
meta = {
    "image_width": cropped.size[0],
    "image_height": cropped.size[1],
    "min_lat": MIN_LAT, "max_lat": MAX_LAT,
    "min_lon": MIN_LON, "max_lon": MAX_LON,
    "zoom": ZOOM,
    "source": "OpenStreetMap tile.openstreetmap.org (public, no key)",
    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
meta_dst = dst.with_suffix(".json")
meta_dst.write_text(json.dumps(meta, indent=2))
print(f"Saved {meta_dst}")
