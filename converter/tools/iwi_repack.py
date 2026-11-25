"""iwi_repack.py - the image files (.iwi) a map loads, cut down to the Vita's texture size, as loose files.

    python -I tools/vita/iwi_repack.py <imagestats log> <iwd dir> <out dir> [--max 128] [--dry-run]

    python -I tools/vita/iwi_repack.py build/Release/main/console_mp.log build/Release/main build/vita-images --max 128

<imagestats log>: a PC run's console log with '+set bo1_imagestats 1' (r_image.cpp): one 'imagestats' line per image a
zone loads; those whose pixels are external (in .iwi files) are the ones copied. Run the map as a client
(tools/headless.ps1 -Zombies -Client) so the client's zones are in it too.

An .iwi (version 13) is a 48-byte header and the mip levels from the smallest to the largest; fileSizeForPicmip[k] is
the file's size without its k largest levels. A file is cut to the first k whose largest level fits --max (the engine
loads such a file like any other: dimensions and sizes in the header say what is there). Files without mip levels
(header flags 0x3: UI images and the like) stay as they are: those of the zombies zones and small ones, while the
rest (multiplayer menus, emblems, other maps' loading screens) become a 4x4 grey placeholder. Only the semantics the
Vita's programs sample are copied (--semantics: 2D, function, color maps); cube and 3D maps are left out.

The files go into one archive, <out dir>/iw_vita.iwd (a zip, stored: no inflating on the Vita), as images/<name>.iwi:
copy it to ux0:data/bo1zombies/main/. One archive, not loose files: opening a file in a folder of thousands on the
memory card took about 250 ms (a load of 4 images a second); the engine reads an archive's directory once and each
image is then a seek and a read. --loose writes <out dir>/images/<name>.iwi instead.
"""
import argparse
import collections
import os
import re
import struct
import sys
import zipfile

HEADER = 48
# (r_image_load_obj.cpp Image_LoadFromData: 1-5 bitmaps, 6-10 the same as wavelets)
FORMAT_NAMES = {1: "ARGB32", 2: "RGB24", 3: "L8A8", 4: "L8", 5: "A8", 6: "wavelet ARGB32", 7: "wavelet RGB24",
                8: "wavelet L8A8", 9: "wavelet L8", 10: "wavelet A8", 11: "DXT1", 12: "DXT3", 13: "DXT5", 14: "DXN",
                19: "RGBA16F"}


def wanted_images(log_path, semantics):
    """{name: (map type, zones)} of the images with external pixels in the log, of the given semantics (None: all); an
    image several zones load (the font: a multiplayer and a zombies zone) has all of them"""
    images = {}
    pattern = re.compile(r"^imagestats '([^']*)' (\S+) map (\d+) sem (\d+) .* external (\d+)")
    with open(log_path, encoding="latin-1") as f:
        for line in f:
            m = pattern.match(line)
            if m and int(m.group(5)) > 0 and (semantics is None or int(m.group(4)) in semantics):
                entry = images.setdefault(m.group(2), (int(m.group(3)), set()))
                entry[1].add(m.group(1))
    return images


def archive_index(iwd_dir, mod_dirs=()):
    """{lower-case image name: (zip path, member)}: later archives win, as the engine searches the highest first;
    a mod folder's archives (every .iwd there) come after the game's: the engine searches fs_game's folder first"""
    index = {}
    archives = [os.path.join(iwd_dir, p) for p in sorted(os.listdir(iwd_dir)) if p.lower().endswith(".iwd") and
                (p.lower().startswith("iw_") or p.lower().startswith("localized_english"))]
    for mod_dir in mod_dirs:
        archives += [os.path.join(mod_dir, p) for p in sorted(os.listdir(mod_dir)) if p.lower().endswith(".iwd")]
    for path in archives:
        with zipfile.ZipFile(path) as z:
            for member in z.namelist():
                low = member.lower()
                if low.startswith("images/") and low.endswith(".iwi"):
                    index[low[len("images/"):-len(".iwi")]] = (path, member)
    return index


def halve_uncompressed(data):
    """an uncompressed single-level image (RGB24 / ARGB32) at half its size (2x2 box filter), header updated"""
    width, height, depth = struct.unpack_from("<3h", data, 6)
    bpp = {1: 4, 2: 3}[data[4]]
    src = data[HEADER:]
    w2, h2 = max(width // 2, 1), max(height // 2, 1)
    out = bytearray(w2 * h2 * bpp)
    for y in range(h2):
        r0 = (2 * y) * width * bpp
        r1 = min(2 * y + 1, height - 1) * width * bpp
        for x in range(w2):
            a = r0 + 2 * x * bpp
            b = a + bpp if 2 * x + 1 < width else a
            c = r1 + 2 * x * bpp
            d = c + bpp if 2 * x + 1 < width else c
            o = (y * w2 + x) * bpp
            for ch in range(bpp):
                out[o + ch] = (src[a + ch] + src[b + ch] + src[c + ch] + src[d + ch] + 2) >> 2
    header = bytearray(data[:HEADER])
    struct.pack_into("<3h", header, 6, w2, h2, depth)
    struct.pack_into("<8i", header, 16, *([HEADER + len(out)] * 8))
    return bytes(header) + bytes(out)


def _dxt1_colors(block):
    """a DXT1 block's 16 texels as (r, g, b), row by row"""
    c0, c1, bits = struct.unpack_from("<HHI", block)
    def rgb(c):
        return ((c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31)
    p0, p1 = rgb(c0), rgb(c1)
    if c0 > c1:
        pal = (p0, p1, tuple((2 * a + b) // 3 for a, b in zip(p0, p1)), tuple((a + 2 * b) // 3 for a, b in zip(p0, p1)))
    else:
        pal = (p0, p1, tuple((a + b) // 2 for a, b in zip(p0, p1)), (0, 0, 0))
    return [pal[bits >> (2 * i) & 3] for i in range(16)]


def _dxt1_block(colors):
    """16 (r, g, b) as a DXT1 block: the box's corners as the end points, each texel the nearest of the four colors"""
    lo = [min(c[ch] for c in colors) for ch in range(3)]
    hi = [max(c[ch] for c in colors) for ch in range(3)]
    def pack(c):
        return ((c[0] * 31 + 127) // 255) << 11 | ((c[1] * 63 + 127) // 255) << 5 | ((c[2] * 31 + 127) // 255)
    c0, c1 = pack(hi), pack(lo)
    if c0 == c1:
        return struct.pack("<HHI", c0, c1, 0)
    if c0 < c1:
        c0, c1 = c1, c0
    # the four colors: a block whose 16 indices are all k decodes to color k
    pal = [_dxt1_colors(struct.pack("<HHI", c0, c1, k * 0x55555555))[0] for k in range(4)]
    bits = 0
    for i, c in enumerate(colors):
        best = min(range(4), key=lambda k: sum((c[ch] - pal[k][ch]) ** 2 for ch in range(3)))
        bits |= best << (2 * i)
    return struct.pack("<HHI", c0, c1, bits)


def halve_dxt1(data):
    """a DXT1 image without mip levels (a cube map's 6 faces too) at half its size: each 2x2 texels averaged, the
    blocks encoded again. The sky's cube map is 512 a face with no mip levels - 768 KB, the cut can't make it smaller."""
    width, height, depth = struct.unpack_from("<3h", data, 6)
    faces = 6 if data[5] & 4 else 1
    bw, bh = max(width // 4, 1), max(height // 4, 1)
    w2, h2 = max(width // 2, 4), max(height // 2, 4)
    ow, oh = w2 // 4, h2 // 4
    face_size = bw * bh * 8
    out = bytearray()
    for f in range(faces):
        src = data[HEADER + f * face_size:HEADER + (f + 1) * face_size]
        for by in range(oh):
            for bx in range(ow):
                # the 8x8 texels of the 2x2 source blocks this block is made from
                texels = [[None] * 8 for _ in range(8)]
                for sy in range(2):
                    for sx in range(2):
                        x, y = min(2 * bx + sx, bw - 1), min(2 * by + sy, bh - 1)
                        cols = _dxt1_colors(src[(y * bw + x) * 8:(y * bw + x) * 8 + 8])
                        for i in range(16):
                            texels[4 * sy + i // 4][4 * sx + i % 4] = cols[i]
                colors = []
                for ty in range(4):
                    for tx in range(4):
                        quad = (texels[2 * ty][2 * tx], texels[2 * ty][2 * tx + 1], texels[2 * ty + 1][2 * tx],
                                texels[2 * ty + 1][2 * tx + 1])
                        colors.append(tuple((sum(q[ch] for q in quad) + 2) // 4 for ch in range(3)))
                out += _dxt1_block(colors)
    header = bytearray(data[:HEADER])
    struct.pack_into("<3h", header, 6, w2, h2, depth)
    struct.pack_into("<8i", header, 16, *([HEADER + len(out)] * 8))
    return bytes(header) + bytes(out)


# ---- images without mip levels that are too big (custom maps' files: the cut cannot make them smaller) -----------
# Stairway to Hell's moon ray gun: 2048x2048 DXT5 without mip levels in a 16 MB file (the level is the first 4 MB, the
# rest data the engine never reads - a mod tool's leftovers); 3D Cinema's font 1024x2048 in 8 MB, its menu backgrounds
# 2048x2048 (4 MB). The Vita loads an image file through a 4 MB buffer (r_image_load_obj.cpp IMAGE_TEMP_MEMORY):
# "Needed to allocate at least 8.0 MB to load images" (2026-11-11). shrink_nomip trims such a file to its level and
# halves it (2x2 texels averaged, encoded again) until it fits a size.
BLOCK_BYTES = {11: 8, 12: 16, 13: 16, 14: 16}   # DXT1, DXT3, DXT5, DXN: bytes per 4x4 block
PIXEL_BYTES = {1: 4, 2: 3, 3: 2, 4: 1, 5: 1}    # ARGB32, RGB24, L8A8, L8, A8


def level_bytes(fmt, width, height):
    """one mip level's bytes, or None for a format this does not know"""
    if fmt in BLOCK_BYTES:
        return max((width + 3) // 4, 1) * max((height + 3) // 4, 1) * BLOCK_BYTES[fmt]
    if fmt in PIXEL_BYTES:
        return width * height * PIXEL_BYTES[fmt]
    return None


def _dxt_rgb(c):
    return ((c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31)


def _dxt_texels(fmt, block):
    """a DXT1/3/5 block's 16 texels as (r, g, b, a), row by row"""
    color = block[8:16] if fmt in (12, 13) else block[:8]
    c0, c1, bits = struct.unpack_from("<HHI", color)
    p0, p1 = _dxt_rgb(c0), _dxt_rgb(c1)
    if c0 > c1 or fmt != 11:  # (DXT3 and DXT5 always use the four colors)
        pal = (p0 + (255,), p1 + (255,), tuple((2 * a + b) // 3 for a, b in zip(p0, p1)) + (255,),
               tuple((a + 2 * b) // 3 for a, b in zip(p0, p1)) + (255,))
    else:
        pal = (p0 + (255,), p1 + (255,), tuple((a + b) // 2 for a, b in zip(p0, p1)) + (255,), (0, 0, 0, 0))
    texels = [list(pal[bits >> (2 * i) & 3]) for i in range(16)]
    if fmt == 12:
        alpha = int.from_bytes(block[:8], "little")
        for i in range(16):
            texels[i][3] = (alpha >> (4 * i) & 15) * 17
    elif fmt == 13:
        a0, a1 = block[0], block[1]
        idx = int.from_bytes(block[2:8], "little")
        if a0 > a1:
            vals = [a0, a1] + [((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)]
        else:
            vals = [a0, a1] + [((5 - k) * a0 + k * a1) // 5 for k in range(1, 5)] + [0, 255]
        for i in range(16):
            texels[i][3] = vals[idx >> (3 * i) & 7]
    return texels


def _dxt_color_block(texels, punch_through):
    """the 8-byte color block: _dxt1_block's four colors, or (DXT1 with transparent texels) three colors and index 3
    transparent"""
    if not punch_through:
        return _dxt1_block([tuple(t[:3]) for t in texels])
    solid = [tuple(t[:3]) for t in texels if t[3] >= 128] or [(0, 0, 0)]
    lo = [min(c[ch] for c in solid) for ch in range(3)]
    hi = [max(c[ch] for c in solid) for ch in range(3)]
    def pack(c):
        return ((c[0] * 31 + 127) // 255) << 11 | ((c[1] * 63 + 127) // 255) << 5 | ((c[2] * 31 + 127) // 255)
    c0, c1 = sorted((pack(lo), pack(hi)))  # c0 <= c1: the three-color mode
    p0, p1 = _dxt_rgb(c0), _dxt_rgb(c1)
    pal = (p0, p1, tuple((a + b) // 2 for a, b in zip(p0, p1)))
    bits = 0
    for i, t in enumerate(texels):
        k = 3 if t[3] < 128 else min(range(3), key=lambda j: sum((t[ch] - pal[j][ch]) ** 2 for ch in range(3)))
        bits |= k << (2 * i)
    return struct.pack("<HHI", c0, c1, bits)


def _dxt_block(fmt, texels):
    """16 (r, g, b, a) texels as a DXT1/3/5 block"""
    if fmt == 11:
        return _dxt_color_block(texels, any(t[3] < 128 for t in texels))
    color = _dxt1_block([tuple(t[:3]) for t in texels])
    alphas = [t[3] for t in texels]
    if fmt == 12:
        return sum(((a * 15 + 127) // 255) << (4 * i) for i, a in enumerate(alphas)).to_bytes(8, "little") + color
    a0, a1 = max(alphas), min(alphas)
    if a0 == a1:
        return bytes((a0, a1)) + bytes(6) + color
    vals = [a0, a1] + [((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)]
    idx = 0
    for i, a in enumerate(alphas):
        idx |= min(range(8), key=lambda k: abs(a - vals[k])) << (3 * i)
    return bytes((a0, a1)) + idx.to_bytes(6, "little") + color


def halve_dxt(data):
    """a DXT1/3/5 image without mip levels (each face of a cube map too) at half its size: each 2x2 texels averaged
    (alpha too), the blocks encoded again"""
    fmt = data[4]
    width, height, depth = struct.unpack_from("<3h", data, 6)
    faces = 6 if data[5] & 4 else 1
    bpb = BLOCK_BYTES[fmt]
    bw, bh = max((width + 3) // 4, 1), max((height + 3) // 4, 1)
    w2, h2 = max(width // 2, 4), max(height // 2, 4)
    ow, oh = (w2 + 3) // 4, (h2 + 3) // 4
    face_size = bw * bh * bpb
    out = bytearray()
    for f in range(faces):
        src = data[HEADER + f * face_size:HEADER + (f + 1) * face_size]
        for by in range(oh):
            for bx in range(ow):
                texels = [[None] * 8 for _ in range(8)]
                for sy in range(2):
                    for sx in range(2):
                        x, y = min(2 * bx + sx, bw - 1), min(2 * by + sy, bh - 1)
                        t = _dxt_texels(fmt, src[(y * bw + x) * bpb:(y * bw + x + 1) * bpb])
                        for i in range(16):
                            texels[4 * sy + i // 4][4 * sx + i % 4] = t[i]
                block = []
                for ty in range(4):
                    for tx in range(4):
                        quad = (texels[2 * ty][2 * tx], texels[2 * ty][2 * tx + 1], texels[2 * ty + 1][2 * tx],
                                texels[2 * ty + 1][2 * tx + 1])
                        block.append([(sum(q[ch] for q in quad) + 2) // 4 for ch in range(4)])
                out += _dxt_block(fmt, block)
    header = bytearray(data[:HEADER])
    struct.pack_into("<3h", header, 6, w2, h2, depth)
    struct.pack_into("<8i", header, 16, *([HEADER + len(out)] * 8))
    return bytes(header) + bytes(out)


def undouble_nomip(data):
    """a DXT file without mip levels that is exactly four times its header's level (with or without the header's 48
    bytes) holds an image of twice the header's width and height: the header says so then, the missing end is
    zero-padded. Custom maps made for the community mod loader have such files (3D Cinema's font: 1024x2048 in the
    header, 8 MB = a 2048x4096 DXT5 - read as the header says, the glyphs came out in stripes on the Vita, 2026-11-11;
    Stairway's ray gun: 2048x2048 in the header, 16 MB = 4096x4096). The engine reads the header's size."""
    sizes = struct.unpack_from("<8i", data, 16)
    width, height, depth = struct.unpack_from("<3h", data, 6)
    if data[4] not in BLOCK_BYTES or data[5] & 12 or any(s != sizes[0] for s in sizes):
        return data
    one = level_bytes(data[4], width, height)
    # (a file of the header's size is what it says - a 4x4 DXT5 is 48 + 16 = 64 bytes, four times 16, too)
    if len(data) == HEADER + one or min(width, height) < 16 or len(data) not in (4 * one, 4 * one + HEADER):
        return data
    full = level_bytes(data[4], 2 * width, 2 * height)
    header = bytearray(data[:HEADER])
    struct.pack_into("<3h", header, 6, 2 * width, 2 * height, depth)
    struct.pack_into("<8i", header, 16, *([HEADER + full] * 8))
    payload = data[HEADER:HEADER + full]
    return bytes(header) + payload + bytes(full - len(payload))


def trim_nomip(data):
    """a file without mip levels holding more than its level (and faces): only what the engine reads"""
    sizes = struct.unpack_from("<8i", data, 16)
    width, height, depth = struct.unpack_from("<3h", data, 6)
    one = level_bytes(data[4], width, height)
    if one is None or any(s != sizes[0] for s in sizes):  # (a format this does not know, or mip levels)
        return data
    want = HEADER + one * (6 if data[5] & 4 else 1) * max(depth, 1)
    if len(data) <= want:
        return data
    header = bytearray(data[:HEADER])
    struct.pack_into("<8i", header, 16, *([want] * 8))
    return bytes(header) + data[HEADER:want]


def shrink_nomip(data, max_size):
    """(data, what was done): a file without mip levels trimmed to its level, then halved while larger than max_size
    (DXT1/3/5, ARGB32, RGB24; other formats only trimmed). Files with mip levels are cut() instead: unchanged here."""
    sizes = struct.unpack_from("<8i", data, 16)
    if any(s != sizes[0] for s in sizes):
        return data, ""
    done = []
    undoubled = undouble_nomip(data)
    if undoubled is not data:
        done.append("read as %dx%d (twice its header's size)" % struct.unpack_from("<2h", undoubled, 6))
    data = undoubled
    trimmed = trim_nomip(data)
    if len(trimmed) < len(data):
        done.append("trimmed %d -> %d bytes" % (len(data), len(trimmed)))
    data = trimmed
    while max(struct.unpack_from("<2h", data, 6)) > max_size:
        if data[4] in (11, 12, 13) and not data[5] & 8:
            data = halve_dxt(data)
        elif data[4] in (1, 2) and not data[5] & 12:
            data = halve_uncompressed(data)
        else:
            break
        done.append("halved to %dx%d" % struct.unpack_from("<2h", data, 6))
    return data, ", ".join(done)


# what a left-out image becomes: a 4x4 grey DXT1 with no mip levels (56 bytes) - a file the engine finds at once (a
# missing one costs a slow search on the Vita) and that takes next to no memory
PLACEHOLDER = (b"IWi" + bytes([13, 11, 0x03]) + struct.pack("<3h", 4, 4, 1) + struct.pack("<f", 0.0) +
               struct.pack("<8i", *([56] * 8)) + struct.pack("<2HI", 0x8410, 0x8410, 0))


def cut(data, max_size):
    """the file without the mip levels above max_size, and how many were cut"""
    width, height, depth = struct.unpack_from("<3h", data, 6)
    sizes = list(struct.unpack_from("<8i", data, 16))
    if sizes[0] != len(data):
        raise ValueError("fileSizeForPicmip[0] %d != file size %d" % (sizes[0], len(data)))
    k = 0
    while k < 7 and max(width >> k, height >> k) > max_size and sizes[k + 1] < sizes[k]:
        k += 1
    if not k:
        return data, 0
    header = bytearray(data[:HEADER])
    struct.pack_into("<3h", header, 6, max(width >> k, 1), max(height >> k, 1), depth)
    struct.pack_into("<8i", header, 16, *[sizes[min(i + k, 7)] for i in range(8)])
    return bytes(header) + data[HEADER:sizes[k]], k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("iwd_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--max", type=int, default=128)
    ap.add_argument("--skip-cubes", action="store_true", default=True)
    # the image semantics the Vita's programs sample (vita_gxm_draw.cpp): 0 2D (UI, effects), 1 function, 2 color maps;
    # not normal (5), specular (8) or reflection (27) maps - most of the bytes. 'all' for every image.
    ap.add_argument("--semantics", default="0,1,2")
    # images without mip levels (UI, overlays) are kept only from these zones, or when small: the multiplayer zones'
    # loading screens and lobby art (tens of MB) are never drawn in a zombies map
    ap.add_argument("--nomip-zones", default="zombie_theater,zombie_theater_patch,en_zombie_theater,common_zombie,"
                    "en_common_zombie,code_post_gfx,en_code_post_gfx")
    ap.add_argument("--nomip-small", type=int, default=16 * 1024)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--loose", action="store_true")
    # a mod's folder (mods/<name>: its .iwd files win over the game's), and the archive's name (in the mod's folder:
    # any name the engine adds there)
    ap.add_argument("--mod-dir", action="append", default=[])
    ap.add_argument("--name", default="iw_vita.iwd")
    args = ap.parse_args()

    semantics = None if args.semantics == "all" else {int(x) for x in args.semantics.split(",")}
    images = wanted_images(args.log, semantics)
    index = archive_index(args.iwd_dir, args.mod_dir)
    out_images = os.path.join(args.out_dir, "images")
    archive = None
    if not args.dry_run:
        os.makedirs(out_images if args.loose else args.out_dir, exist_ok=True)
        if not args.loose:
            archive = zipfile.ZipFile(os.path.join(args.out_dir, args.name), "w", zipfile.ZIP_STORED)

    def write(name, data):
        if args.dry_run:
            return
        if archive:
            archive.writestr("images/%s.iwi" % name, data)
        else:
            with open(os.path.join(out_images, name + ".iwi"), "wb") as f:
                f.write(data)
    stats = collections.Counter()
    by_format = collections.Counter()
    bytes_in = bytes_out = 0
    missing = []
    open_zips = {}
    nomip_zones = set(args.nomip_zones.split(","))
    for name, (map_type, zones) in sorted(images.items()):
        if args.skip_cubes and map_type in (4, 5):  # 3D and cube maps: the Vita's GPU path has none yet
            stats["skipped (cube / 3D)"] += 1
            continue
        found = index.get(name.lower())
        if not found:
            missing.append(name)
            continue
        zip_path, member = found
        z = open_zips.get(zip_path) or open_zips.setdefault(zip_path, zipfile.ZipFile(zip_path))
        data = z.read(member)
        if data[:3] != b"IWi" or data[3] != 13:
            stats["not version 13"] += 1
            continue
        if (data[5] & 3) and not (zones & nomip_zones) and len(data) > args.nomip_small:
            stats["placeholder (large, no mips, other zone)"] += 1
            write(name, PLACEHOLDER)
            continue
        while (data[5] & 3) and data[4] in (1, 2) and max(struct.unpack_from("<2h", data, 6)) > args.max:
            data = halve_uncompressed(data)  # (counted at its reduced size below)
        try:
            small, levels = cut(data, args.max)
        except ValueError as e:
            print("  %s: %s" % (name, e))
            stats["invalid"] += 1
            continue
        stats["cut" if levels else "kept"] += 1
        by_format[FORMAT_NAMES.get(data[4], str(data[4]))] += len(small)
        bytes_in += len(data)
        bytes_out += len(small)
        write(name, small)
    # the other semantics' images (normal, specular, reflection maps): placeholders, so the engine finds them at once
    for name, (map_type, zones) in sorted(wanted_images(args.log, None).items()):
        if name in images or map_type in (4, 5):
            continue
        stats["placeholder (not sampled on the Vita)"] += 1
        write(name, PLACEHOLDER)
    if archive:
        archive.close()
    print("images: %d wanted, %s, %d not in the archives" % (len(images), dict(stats), len(missing)))
    print("size: %.1f MB at PC size -> %.1f MB at max %d" % (bytes_in / 1048576.0, bytes_out / 1048576.0, args.max))
    print("by format (MB): %s" % ", ".join("%s %.1f" % (k, v / 1048576.0) for k, v in by_format.most_common()))
    if missing:
        print("missing (first 10): %s" % ", ".join(missing[:10]))


if __name__ == "__main__":
    sys.exit(main())
