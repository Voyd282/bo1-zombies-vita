"""image_list.py - a map's image list for the release converter (converter/data/<map>/images.txt): every image the map's
zones load with external pixels, as 'image' (cut to the Vita's size) or 'placeholder' (the 4x4 grey stand-in).

    python -I tools/vita/image_list.py <imagestats log> <map> <out images.txt> [--base <another map's images.txt>]

<imagestats log>: a PC client run of the map with '+set bo1_imagestats 1' (see iwi_repack.py). The rules are
iwi_repack.py's: the semantics the Vita samples (2D, function, color) are images, except large ones without mip
levels from zones that are not the map's or the zombies' own (multiplayer art); normal, specular and reflection maps
are placeholders; cube and 3D maps are left out. Kept as images besides: detail maps (semantic 19) and specular maps of
weapons (semantic 8, 'weapon' in the name), which the Vita's weapon shaders sample (FS_DETAIL / FS_SPEC: 128 px costs
~10-20 KB each), and sky cubes (skybox_*, *_ft). --base: an image both maps load keeps the kind the other map's list gives
it (Kino's were chosen by hand from the weapon materials), so one archive serves both. Prints the counts.
"""
import argparse
import collections
import re

PATTERN = re.compile(r"^imagestats '([^']*)' (\S+) map (\d+) sem (\d+) .* flags 0x([0-9a-fA-F]+) .* external (\d+)")
SAMPLED = {0, 1, 2}
NOMIP_SMALL = 16 * 1024


def read_log(path):
    """{name: (map type, semantic, flags, external bytes, zones)}"""
    images = {}
    with open(path, encoding="latin-1") as f:
        for line in f:
            m = PATTERN.match(line)
            if not m or int(m.group(6)) <= 0:
                continue
            zone, name = m.group(1), m.group(2)
            entry = images.get(name)
            if entry:
                entry[4].add(zone)
            else:
                images[name] = (int(m.group(3)), int(m.group(4)), int(m.group(5), 16), int(m.group(6)), {zone})
    return images


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("map")
    ap.add_argument("out")
    ap.add_argument("--base")
    # a mod's zones count as the map's own too (mod.ff: its menus, loading screen and HUD art)
    ap.add_argument("--zones", default="", help="more zones whose UI images are kept (comma-separated)")
    args = ap.parse_args()
    nomip_zones = {args.map, args.map + "_patch", "en_" + args.map, "common_zombie", "en_common_zombie",
                   "code_post_gfx", "en_code_post_gfx"} | {z for z in args.zones.split(",") if z}
    base = {}
    if args.base:
        for line in open(args.base, encoding="utf-8"):
            fields = line.rstrip("\n").split("\t")
            if len(fields) == 2:
                base[fields[0]] = fields[1]

    kinds, counts, disagree, cubes = {}, collections.Counter(), [], []
    for name, (map_type, sem, flags, external, zones) in sorted(read_log(args.log).items()):
        # (a sky cube is named after its front face: skybox_*_ft, and the WaW maps' zombie_asylum_ft, zombie_proto_ft,
        # zombie_factory_ft - those were left out, the Vita drew their sky with the missing image's stand-in)
        sky = map_type == 5 and (name.startswith("skybox_") or name.endswith("_ft"))
        if map_type in (4, 5):
            cubes.append("%s (%s, %d KB, %s)%s" % (name, "cube" if map_type == 5 else "3D", external >> 10,
                                                   ",".join(sorted(zones)), " - kept: sky" if sky else ""))
            if base.get(name) != "image" and not sky:
                continue
        if sky or sem == 19 or (sem == 8 and "weapon" in name):
            rule = "image"
        elif sem not in SAMPLED:
            rule = "placeholder"
        elif (flags & 3) and not (zones & nomip_zones) and external > NOMIP_SMALL:
            rule = "placeholder"
        else:
            rule = "image"
        kind = base.get(name, rule)
        if name in base:
            counts["shared"] += 1
            if kind != rule:
                disagree.append((name, kind, rule))
        else:
            counts["new " + kind] += 1
        kinds[name] = kind
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        for name in sorted(kinds):
            f.write("%s\t%s\n" % (name, kinds[name]))
    print("%d images -> %s: %s" % (len(kinds), args.out, ", ".join("%s %d" % kv for kv in sorted(counts.items()))))
    print("%d shared images where the base list differs from the rule (kept as the base has them)" % len(disagree))
    for name, kind, rule in disagree[:12]:
        print("  %s: %s (rule: %s)" % (name, kind, rule))
    if cubes:
        print("cube / 3D maps (left out unless the base has them as images):")
        for c in cubes:
            print("  " + c)
    if base:
        only_base = sorted(set(base) - set(kinds))
        print("%d images of the base list this map does not load" % len(only_base))


if __name__ == "__main__":
    main()
