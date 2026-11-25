"""lightmap_primary.py - a map's primary lightmaps for the Vita's QUALITY HIGH lighting (main/vitalight/<map>.bin).

The primary lightmap (*lightmap<n>_primary, DXT1) holds in its green channel how much of the surface's primary light
(the sun, a spot or an omni light: the PC's LIT_SUN / LIT_SPOT / LIT_OMNI techniques) reaches each texel - the light's
baked shadow. zone_repack.py --shrink-lightmaps replaces them with a 4x4 black block (DEFAULT reads none); with QUALITY
HIGH the engine loads this file at a map's start instead and lights the world by the primary lights as the PC.

The file: "BO1LP" + version byte, the lightmap count (u16), then per lightmap its index (u16), width and height (u16)
and width * height bytes: the green channel (DXT1 decoded), halved (2x2 box) while larger than --max. Lightmaps the
zone has no inline primary for are left out.

usage: lightmap_primary.py <zone .ff> <zone .zmap> <out .bin> [--max 1024]
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lightmap_pack  # noqa: E402
from zone_repack import read_ff, read_zmap  # noqa: E402

D3DFMT_DXT1 = lightmap_pack.D3DFMT_DXT1


def dxt1_green(data, width, height):
    """the green channel (0..255) of a DXT1 image's first level"""
    out = bytearray(width * height)
    bw, bh = max(1, width // 4), max(1, height // 4)
    at = 0
    for by in range(bh):
        for bx in range(bw):
            c0, c1, bits = struct.unpack_from("<HHI", data, at)
            at += 8
            g0 = ((c0 >> 5) & 63) * 255 // 63
            g1 = ((c1 >> 5) & 63) * 255 // 63
            pal = (g0, g1, (2 * g0 + g1) // 3, (g0 + 2 * g1) // 3) if c0 > c1 else (g0, g1, (g0 + g1) // 2, 0)
            for t in range(16):
                x, y = bx * 4 + (t & 3), by * 4 + (t >> 2)
                if x < width and y < height:
                    out[y * width + x] = pal[(bits >> (2 * t)) & 3]
    return out


def halve(g, width, height):
    w2, h2 = width // 2, height // 2
    out = bytearray(w2 * h2)
    for y in range(h2):
        r0, r1 = 2 * y * width, (2 * y + 1) * width
        for x in range(w2):
            out[y * w2 + x] = (g[r0 + 2 * x] + g[r0 + 2 * x + 1] + g[r1 + 2 * x] + g[r1 + 2 * x + 1] + 2) >> 2
    return out, w2, h2


def extract(ff, zmap, limit):
    _, stream, _ = read_ff(ff)
    _, events = read_zmap(zmap)
    found = []
    for image in lightmap_pack.collect(stream, events):
        name = image.name or ""
        if not (name.startswith("*lightmap") and name.endswith("_primary")):
            continue
        try:
            index = int(name[len("*lightmap"):-len("_primary")])
        except ValueError:
            continue
        levels = stream[image.loaddef]
        fmt, size = struct.unpack_from("<iI", stream, image.loaddef + 4)
        width, height = struct.unpack_from("<HH", stream, image.a + 20)
        data = b"".join(stream[o:o + n] for o, n in image.reads)
        if (fmt & 0xFFFFFFFF) != D3DFMT_DXT1 or len(data) < max(1, width // 4) * max(1, height // 4) * 8:
            print("  %s: %dx%d format %#x, %d bytes: left out" % (name, width, height, fmt & 0xFFFFFFFF, len(data)))
            continue
        if width <= 4 and height <= 4:
            print("  %s: 4x4 (already replaced?): left out" % name)
            continue
        g = dxt1_green(data, width, height)
        while (width > limit or height > limit) and width >= 8 and height >= 8:
            g, width, height = halve(g, width, height)
        found.append((index, width, height, bytes(g)))
        print("  %s: %d levels, -> %dx%d" % (name, levels, width, height))
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ff")
    ap.add_argument("zmap")
    ap.add_argument("out")
    ap.add_argument("--max", type=int, default=1024)
    args = ap.parse_args()
    found = extract(args.ff, args.zmap, args.max)
    if not found:
        sys.exit("%s: no primary lightmaps" % args.ff)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(b"BO1LP\x01" + struct.pack("<H", len(found)))
        for index, width, height, g in sorted(found):
            f.write(struct.pack("<HHH", index, width, height))
            f.write(g)
    print("  wrote %s: %d lightmaps, %.2f MB" % (args.out, len(found), os.path.getsize(args.out) / 1048576.0))


if __name__ == "__main__":
    main()
