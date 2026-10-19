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


def archive_index(iwd_dir):
    """{lower-case image name: (zip path, member)}: later archives win, as the engine searches the highest first"""
    index = {}
    archives = sorted(p for p in os.listdir(iwd_dir) if p.lower().endswith(".iwd") and
                      (p.lower().startswith("iw_") or p.lower().startswith("localized_english")))
    for archive in archives:
        path = os.path.join(iwd_dir, archive)
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
    args = ap.parse_args()

    semantics = None if args.semantics == "all" else {int(x) for x in args.semantics.split(",")}
    images = wanted_images(args.log, semantics)
    index = archive_index(args.iwd_dir)
    out_images = os.path.join(args.out_dir, "images")
    archive = None
    if not args.dry_run:
        os.makedirs(out_images if args.loose else args.out_dir, exist_ok=True)
        if not args.loose:
            archive = zipfile.ZipFile(os.path.join(args.out_dir, "iw_vita.iwd"), "w", zipfile.ZIP_STORED)

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
