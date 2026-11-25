"""convert_all.py - TEST converter: everything in a Black Ops (PC) folder -> ux0:data/bo1zombies for the Vita.

    python -I convert_all.py "<Black Ops folder>" [--out output] [--engine ../bo1-zombies-decompiled] [--jobs 4]

What it does:
 1. checks the game folder: the base zones (data/base/inputs.txt) must be the known ones; each original map in
    data/maps is converted when its zones are there and match (else: listed as not installed / different version)
 2. each folder in <game>/mods: a custom map (it has its own map zone, mod.arena lists it) or a mod (mod.ff, on the
    original maps). Copied into work/, its zones read in Python (tools/zonereader - no game or engine run: zone
    maps, image stats, streamed sounds, the models and anims each map can show), converted when its zones fit the
    Vita's zone memory
 3. writes <out>/bo1zombies: zone/, main/ (iw_vita*.iwd, the menus), mods/ (the converted mods + the port's
    modifiers), cmdline.txt (the normal game), localization.txt; and <out>/report.txt
It never writes into the game folder. The tools are the engine repository's (tools/vita, tools/zonereader: Python
only; the sounds need ffmpeg). --play-check alone runs the engine's PC build (build/Release: a developer's check).
make_data.py fills data/ from that repository's recordings.
"""
import argparse
import concurrent.futures
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
# --world-verts-compact (2026-11-25, perf493, user-tested on Kino/Five/Ascension): the world's vertices 36 bytes instead
# of 44 (tangent and binormal sign left out) - 2.2-3.8 MB of zone pool on the original maps; not with --world-verts-gpu
FLAGS = ["--drop-shaders", "--convert-wma", "--low-poly", "--shrink-adpcm", "--shrink-lightmaps", "--world-verts-compact"]
TEXTURE_MAX = 128   # as the Vita's tested archives
CUBE_MAX = 256      # sky cube maps (no mip levels to cut)
ART_MAX = 512       # the menus' map art (the loading screens); a custom map's loading screen too (its mod.arena names
                    # the material, the PC run's 'matstats' lines its images)
# images a player looks at up close that 128 px (64 in a big map) made unreadable: the wallbuys' chalk outlines (retail
# chalk_weapon_*, chalkmarks_*; custom maps' own copies). Few and small: ~1-2 MB of video memory in all
DETAIL_MAX = 256
DETAIL_WORDS = ("chalk",)
# the settings' QUALITY HIGH (r_vita_quality 1, user 2026-11-21): main/iw_vitahd.iwd, which the engine loads only then
# (com_files.cpp; its name sorts after every iw_vita_*.iwd, so its images win): the perk machines, their bottles and
# Pack-a-Punch at up to the PC's 512 px - their signs were unreadable at 128. A map's ~2-10 MB more video memory
HD_MAX = 512
HD_WORDS = ("vending", "perkbottle", "pack_a_punch", "packapunch")
REFL_MAX = 128   # iw_vitarefl.iwd: the weapons' reflection masks' size cap (WEAPON SHINE, both modes)
PERK_MAX = 256   # iw_vitaperk.iwd: the perk machines', perk bottles' and Pack-a-Punch's color maps (always loaded)
# ... except the machines themselves: their color maps at the PC's size (512, Pack-a-Punch's and Stamin-Up's 512x1024,
# which the 256 limit had cut to 128x256 - blocky; user 2026-11-22: the perks still looked poor on every map). The
# bottles (small on screen) stay at PERK_MAX
PERK_MACHINE_MAX = 1024
def perk_size(name):
    n = name.lower()
    return PERK_MACHINE_MAX if ("vending" in n or "pack_a_punch" in n or "please_wait" in n) else PERK_MAX
# images without mip levels (UI, overlays - and some custom maps' model textures) cannot be cut: trimmed to what the
# engine reads and halved down to this (iwi_repack.shrink_nomip). The game's own are at most 1024 (unchanged); custom
# maps had 2048-4096 ones in files up to 16 MB, which the Vita's 4 MB image buffer cannot load (3D Cinema, Stairway to
# Hell: "Needed to allocate at least 8.0 MB to load images", 2026-11-11). A font only trimmed (glyphs stay readable)
NOMIP_MAX = 1024
FONT_MAX = 2048
# zone memory (the zone pool's 'virtual' block, MB): Kino's map zones take 58.5 and leave ~26 free, Rust's 65.1
# leave 11.6 - a map with its mod (or an original map with a mod) may take about this much
MAP_BUDGET = 76.0
# a mod / custom map too big at the retail model budgets is converted again with smaller models (zone_repack.py
# --low-poly-scale: triangle budgets times this): the mildest that fits is kept
LOW_POLY_SCALES = (1.0, 0.5, 0.35, 0.25)
# a custom map whose zones do not fit as they are gets the big-map conversion, all of it at once: its world's
# vertices in the zone's GPU block (zone_repack.py --world-verts-gpu: video memory when there is room when the map
# loads, else the zone pool), its sounds at 16 kHz (--adpcm-rate), each model one more of its own LODs lower
# (--drop-lods all: as the debug switch bo1_minlod 1, which looked better than simplifying harder - Der Berg's
# teleporters at low-poly scale 0.25 were mangled, user 2026-11-11 - and saves more: Der Berg 87.5 MB against 90.1)
# and its own textures at 64 px (such a map fills both the zone pool and video memory: Der Berg, 2026-11-11, had 0.15
# MB of the pool left with its vertices there and 4 MB of textures without video memory at the retail models, 16 kHz
# and 128 px). Smaller models (LOW_POLY_SCALES) only when it still does not fit
BIG_MAP_FLAGS = ("--world-verts-gpu", "--adpcm-rate", "16000", "--drop-lods", "all")
# the steps a custom map too big as it is goes through, the least visible first (each zone's measured budget -
# zone_repack.py --budget - says what takes its memory; 2026-11-24: Der Berg's map zone 35 MB world, 26 MB models,
# 8 MB collision; its mod.ff 26.5 MB of loaded sounds in video memory, 13 MB weapons): 16 kHz sounds; the world's
# vertices in video memory; one LOD lower for just the biggest models the measurement says are needed ("lods"), then
# for all ("--drop-lods all"); then smaller models (LOW_POLY_SCALES). Weapons (model_lowpoly.is_weapon: the mystery
# box and wall buys show them up close) keep their detail until the very last steps (--low-poly-weapons)
BIG_BASE_FLAGS = ("--world-verts-gpu", "--adpcm-rate", "16000")
# then the weapons: first gently (zone_repack.py --low-poly-weapons gentle: fewer triangles but the retail error
# limit, so each weapon only as far as its shape allows - Der Berg's 72 weapons 13.0 -> 11.6 MB at 0.25, 2026-11-24),
# the last resort with the error limits scaled too (10.7 MB: the box's weapons visibly coarser)
# (both at the smallest scale the other models already had: the weapons are what is left to make smaller)
WEAPON_STEPS = ((0.25, ("--low-poly-weapons", "gentle")), (0.25, ("--low-poly-weapons",)),
                (0.125, ("--low-poly-weapons",)))
# a mod: its perk machines and other models stay as they are; first its zombies and characters a little simpler
# (half their triangles), then its weapons too, gently (the retail error limits: their shape kept), with fewer
# triangles step by step - smaller scales of everything made WDS's hand weapons and perk machines look bad (user
# 2026-11-24). A mod that does not fit this way is not made smaller
CHARACTER_STEP = ("--character-scale", "0.5")
# a mod is measured next to each original map it is for and made smaller step by step until it fits (user
# 2026-11-24: "dass der converter diese messung bei jedem mod macht und diese runterschraubt bis es passend ist"):
# (what the step does, its repack flags, whether its own copies of models the game has give way to the game's)
#   0 as it is - its dead models, unused weapon anims and duplicate surfaces already left out (no visible change)
#   1 the game's own perk machines, power-ups and the like instead of the mod's copies (--rename-models)
#   2 its zombies and characters a little simpler
#   3/4 its weapons' world models (the box, the wall buys) gently simpler; first-person weapons stay
#   5/6 its first-person weapons too, their sights kept (model_lowpoly.SIGHT_REGION: locked, and measured)
# A map it does not fit next to even then is not offered for it (the report says how much is missing)
MOD_LADDER = (
    ("as it is", (), False),
    ("the game's own models instead of its copies", (), True),
    ("zombies and characters a little simpler", CHARACTER_STEP, True),
    ("world weapons gently simpler", CHARACTER_STEP + ("--low-poly-weapons", "gentle-world", "--weapon-scale",
                                                       "0.25"), True),
    ("world weapons simpler", CHARACTER_STEP + ("--low-poly-weapons", "gentle-world", "--weapon-scale", "0.125"),
     True),
    ("weapons in hand gently simpler, sights kept", CHARACTER_STEP + ("--low-poly-weapons", "gentle",
                                                                      "--weapon-scale", "0.25"), True),
    ("weapons in hand simpler, sights kept", CHARACTER_STEP + ("--low-poly-weapons", "gentle", "--weapon-scale",
                                                               "0.125"), True),
)
# the Vita's zone pool, measured (the 'pool after' lines of WDS on Kino and Five, 2026-11-24: each zone took its
# blocks 0, 1, 2, 4 and 5 - the GPU ones go to video memory - plus a few KB, within 10 KB of this)
POOL_KB = 205824
POOL_BASE_KB = 32393      # the code and patch zones and the script tables, before a map (perf490 halved the script
                          # value tables: 9216 KB less, measured on the Vita 2026-11-24; before: 41609)
POOL_CLIENT_KB = 15938    # the local client's memory (PerLocalClient; perf494 server archive 16 -> 4 MB: 28226 before,
                          # 15.6 MB measured on the Vita 2026-11-25)
POOL_ZONE_EXTRA_KB = 8    # each zone's own bookkeeping
POOL_RESERVE_KB = 512     # kept free (the pool does not grow while a map plays - WDS on Kino 9 minutes, its 'pool
                          # after' lines unchanged - only a map change loads more; the measurement is within ~0.1 MB)
# a custom map whose zones fit the pool but put more than this in video memory (loaded sounds: Stairway to Hell's
# three zones 38.9 MB) is tried with its sounds at 16 kHz first (SOUND_FLAGS), then gets the big-map conversion. Der
# Berg ran with 20.8 MB there (its world's vertices in the pool) and 1 MB of textures short; Kino's map has 6.8
GPU_BUDGET = 24.0
SOUND_FLAGS = ("--adpcm-rate", "16000")
BIG_MAP_TEXTURE_MAX = 64
BIG_MAP_NOMIP_MAX = 512
# its zones' blocks in the zone pool (zone_pool_mb: all but the GPU ones, and those of a zone with moved vertices):
# Der Berg's 94.8 MB left 0.15 MB of the pool after the map loaded - this keeps ~4 MB
BIG_MAP_POOL = 91.0
MODIFIERS = {"_stack", "zinfo", "noperks", "horde", "sandbox"}
# models nothing can show any more when a mod runs are left without geometry in the mod's own copies of the zones it
# loads with (the shared common_zombie, and an original map's main zone for a mod): a PC run of each of its maps lists
# what the game can still show (bo1_livemodels) - WDS on Kino: common_zombie's stock weapons 13.6 MB, Kino's zombies
# 2.7 MB (2026-11-24). A mod for more maps than this is left as it is (a run and a zone copy for each)
STRIP_ZONES = ("common_zombie",)
STRIP_MAX_MAPS = 4
MAP_NAMES = {"zombie_theater": "Kino der Toten", "zombie_pentagon": "\"Five\"", "zombie_cosmodrome": "Ascension",
             "zombie_coast": "Call of the Dead", "zombie_temple": "Shangri-La", "zombie_moon": "Moon",
             "zombie_cod5_prototype": "Nacht der Untoten", "zombie_cod5_asylum": "Verr\u00fcckt",
             "zombie_cod5_sumpf": "Shi No Numa", "zombie_cod5_factory": "Der Riese"}
# which pack each map comes with (the window groups them)
MAP_PACKS = {"zombie_theater": "Black Ops", "zombie_pentagon": "Black Ops", "zombie_cosmodrome": "First Strike",
             "zombie_coast": "Escalation", "zombie_temple": "Annihilation", "zombie_moon": "Rezurrection",
             "zombie_cod5_prototype": "Rezurrection", "zombie_cod5_asylum": "Rezurrection",
             "zombie_cod5_sumpf": "Rezurrection", "zombie_cod5_factory": "Rezurrection"}

report = []
# a window's hook (converter_gui.py): listener(kind, *values) - ("say", text), ("note", text), ("step", name),
# ("progress", done, total, text), ("item", key, status, text); None: the console only
listener = None
# windows of the tools and of the PC runs stay hidden (the window runs this without a console)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ConvertError(Exception):
    pass


def tell(kind, *values):
    if listener:
        listener(kind, *values)


def say(text):
    print(text, flush=True)
    tell("say", text)


def note(text):
    report.append(text)
    say("  " + text)


def step(name):
    say("\n== " + name)
    tell("step", name)


def progress(done, total, text=""):
    tell("progress", done, total, text)


def fail(message):
    say("\nERROR: " + message)
    raise ConvertError(message)


def find_ffmpeg():
    candidates = [shutil.which("ffmpeg")]
    local = os.environ.get("LOCALAPPDATA", "")
    candidates += sorted(glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin",
                                                "ffmpeg.exe")), reverse=True)
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    fail("ffmpeg was not found (winget install Gyan.FFmpeg)")


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_inputs(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        if len(f) == 3:
            rows.append((f[0], int(f[1]), f[2]))
    return rows


def check_inputs(game, inputs):
    """'ok', or why not: 'not installed' (a file missing) / 'different version' (a zone that is not the recorded one)"""
    for rel, size, digest in inputs:
        path = os.path.join(game, rel)
        if not os.path.isfile(path):
            return "not installed (%s missing)" % rel
        if digest != "-" and (os.path.getsize(path) != size or sha1(path) != digest):
            return "different version or damaged (%s)" % rel
    return "ok"


def build_hd_archive(index, out_iwd, lists):
    """main/iw_vitahd.iwd (QUALITY HIGH): every image the lists name that is sampled (not a placeholder) and one of
    HD_WORDS, at up to HD_MAX (mip levels cut; without mip levels halved down to it)"""
    names = sorted({name for entries in lists for name, kind in entries if kind != "placeholder"
                    and any(w in name.lower() for w in HD_WORDS)})
    images = 0
    with zipfile.ZipFile(out_iwd, "w", zipfile.ZIP_STORED) as archive:
        for name in names:
            found = index.get(name.lower())
            if not found:
                continue
            data = zipfile.ZipFile(found[0]).read(found[1])
            if data[:3] != b"IWi" or data[3] != 13:
                continue
            while (data[5] & 3) and data[4] in (1, 2) and max(struct.unpack_from("<2h", data, 6)) > HD_MAX:
                data = iwi_repack.halve_uncompressed(data)
            small, _ = iwi_repack.cut(data, HD_MAX)
            small, _ = iwi_repack.shrink_nomip(small, HD_MAX)
            archive.writestr("images/%s.iwi" % name, small)
            images += 1
    return images


def build_perk_archive(index, out_iwd, names_path):
    """main/iw_vitaperk.iwd (always loaded, its name after every iw_vita_*.iwd so its images win): every color map the
    perk machines, perk bottles and Pack-a-Punch are drawn with (data/perk_images.txt, from the zone reader's model
    dump: their models' materials' color maps on the ten maps) at up to PERK_MAX instead of TEXTURE_MAX"""
    names = sorted({line.strip() for line in open(names_path, encoding="utf-8") if line.strip()})
    images = 0
    with zipfile.ZipFile(out_iwd, "w", zipfile.ZIP_STORED) as archive:
        for name in names:
            found = index.get(name.lower())
            if not found:
                continue
            data = zipfile.ZipFile(found[0]).read(found[1])
            if data[:3] != b"IWi" or data[3] != 13:
                continue
            while (data[5] & 3) and data[4] in (1, 2) and max(struct.unpack_from("<2h", data, 6)) > perk_size(name):
                data = iwi_repack.halve_uncompressed(data)
            size = perk_size(name)
            small, _ = iwi_repack.cut(data, size)
            small, _ = iwi_repack.shrink_nomip(small, size)
            archive.writestr("images/%s.iwi" % name, small)
            images += 1
    return images


def build_refl_archive(index, out_iwd, names_path):
    """main/iw_vitarefl.iwd (QUALITY HIGH): the models' reflection masks (the PC's occMap - rgb where the surface
    reflects, alpha its ambient occlusion; the names in data/refl_images.txt), at up to REFL_MAX"""
    names = sorted({line.strip() for line in open(names_path, encoding="utf-8") if line.strip()})
    images = 0
    with zipfile.ZipFile(out_iwd, "w", zipfile.ZIP_STORED) as archive:
        for name in names:
            found = index.get(name.lower())
            if not found:
                continue
            data = zipfile.ZipFile(found[0]).read(found[1])
            if data[:3] != b"IWi" or data[3] != 13:
                continue
            while (data[5] & 3) and data[4] in (1, 2) and max(struct.unpack_from("<2h", data, 6)) > REFL_MAX:
                data = iwi_repack.halve_uncompressed(data)
            small, _ = iwi_repack.cut(data, REFL_MAX)
            small, _ = iwi_repack.shrink_nomip(small, REFL_MAX)
            archive.writestr("images/%s.iwi" % name, small)
            images += 1
    return images


def read_list(path):
    rows = []
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            line = line.rstrip("\r\n")
            if line:
                rows.append(line.split("\t"))
    return rows


def zone_blocks(path):
    """a zone's block sizes (its XFile header)"""
    with open(path, "rb") as f:
        data = f.read(1 << 16)
    head = zlib.decompressobj().decompress(data[12:], 36)
    return struct.unpack_from("<7I", head, 8)


def zone_cost_kb(path):
    """the Vita zone pool a zone takes as it loads (see POOL_KB): its blocks but the GPU ones, in KB"""
    blocks = zone_blocks(path)
    return (blocks[0] + blocks[1] + blocks[2] + blocks[4] + blocks[5]) // 1024 + POOL_ZONE_EXTRA_KB


def zone_virtual_mb(path):
    """the zone pool memory a zone takes (its XFile header's virtual block)"""
    return zone_blocks(path)[4] / 1048576.0


def zone_gpu_mb(path):
    """the video memory a zone takes on the Vita (its physical blocks: loaded sounds, moved world vertices)"""
    blocks = zone_blocks(path)
    return (blocks[3] + blocks[6]) / 1048576.0


def zone_pool_mb(path):
    """the zone pool memory a zone may take on the Vita: its blocks but the GPU ones (which go to video memory), and
    those too when it has its world's vertices there (version 473 | 0x10000: they go to the pool when video memory is
    full as the map loads)"""
    with open(path, "rb") as f:
        version = struct.unpack("<I", f.read(12)[8:12])[0]
    blocks = zone_blocks(path)
    gpu = blocks[3] + blocks[6]
    return (sum(blocks) - (0 if version == (473 | 0x10000) else gpu)) / 1048576.0


def intended_maps(mod_path, game_zones):
    """the original maps a mod brings its own level scripts for (maps/<map>.gsc in its .iwd files)"""
    originals = {m for m in game_zones.values() if m}
    found = set()
    for iwd in glob.glob(os.path.join(mod_path, "*.iwd")):
        try:
            with zipfile.ZipFile(iwd) as z:
                for n in z.namelist():
                    m = re.match(r"^maps/([a-z0-9_]+)\.gsc$", n.lower())
                    if m and m.group(1) in originals:
                        found.add(m.group(1))
        except (OSError, zipfile.BadZipFile):
            pass
    return found


def scan(game):
    """the game folder's original maps and mods for a choice, quickly (file names and sizes, no hashes):
    {"ok": bool, "why": str, "maps": [{key, name, pack, installed, size_mb, loadscreen}],
     "mods": [{key, folder, kind, name, author, size_mb, zones, maps, loadscreen, image_iwds}]}"""
    game = os.path.abspath(game.strip().strip('"'))
    result = {"ok": False, "why": "", "maps": [], "mods": [], "game": game}
    if not os.path.isdir(os.path.join(game, "zone")) or not os.path.isdir(os.path.join(game, "main")):
        result["why"] = "This folder has no 'zone' and 'main' folders - pick the folder with BlackOps.exe."
        return result
    for rel, size, _ in read_inputs(os.path.join(DATA, "base", "inputs.txt")):
        path = os.path.join(game, rel)
        if not os.path.isfile(path):
            result["why"] = "The base game is incomplete: %s is missing." % rel
            return result
    result["ok"] = True
    for m in sorted(os.listdir(os.path.join(DATA, "maps")), key=lambda k: list(MAP_NAMES).index(k)
                    if k in MAP_NAMES else 99):
        inputs = read_inputs(os.path.join(DATA, "maps", m, "inputs.txt"))
        installed = all(os.path.isfile(os.path.join(game, rel)) for rel, _, _ in inputs)
        changed = installed and any(digest != "-" and os.path.getsize(os.path.join(game, rel)) != size
                                    for rel, size, digest in inputs)
        size = sum(os.path.getsize(os.path.join(game, rel)) for rel, _, _ in inputs
                   if os.path.isfile(os.path.join(game, rel)))
        result["maps"].append({"key": m, "name": MAP_NAMES.get(m, m), "pack": MAP_PACKS.get(m, ""),
                               "installed": installed and not changed,
                               "why": "" if installed and not changed else
                               "different version" if changed else "not installed",
                               "size_mb": size / 1048576.0, "loadscreen": "loadscreen_" + m.replace("cod5_", "")})
    root = os.path.join(game, "mods")
    game_zones = {os.path.basename(rel)[:-3].lower() for rel, _, _ in read_inputs(
        os.path.join(DATA, "base", "inputs.txt")) if rel.startswith("zone/")}
    map_of_zone = {}
    for m in os.listdir(os.path.join(DATA, "maps")):
        for z in (m, m + "_patch", "en_" + m):
            map_of_zone[z] = m
    for folder in sorted(os.listdir(root), key=str.lower) if os.path.isdir(root) else []:
        path = os.path.join(root, folder)
        if not os.path.isdir(path):
            continue
        files = os.listdir(path)
        zones = [f[:-3] for f in files if f.lower().endswith(".ff")]
        arena_path = os.path.join(path, "mod.arena")
        arena = open(arena_path, encoding="latin-1").read() if os.path.isfile(arena_path) else ""
        arena_maps = re.findall(r'map\s+"([^"]+)"', arena)
        low = {z.lower() for z in zones}
        replaced = low & (game_zones | set(map_of_zone))
        extra = {z for z in low if re.match(r"^mod\d+$", z) or z.endswith("_override")}
        map_zones = [z for z in zones if z.lower() != "mod" and z.lower() not in replaced and z.lower() not in extra
                     and (z in arena_maps or not arena_maps)]
        if map_zones:
            kind = "custommap"
        elif "mod" in low or replaced or any(f.lower().endswith(".iwd") for f in files):
            kind = "mod"
        else:
            continue
        longname = (re.findall(r'longname\s+"([^"]+)"', arena) or [""])[0]
        name = longname if longname and not re.match(r"^[A-Z0-9_]+$", longname) else folder
        author = ""
        if os.path.isfile(os.path.join(path, "mod.json")):
            desc = re.findall(r'"desc"\s*:\s*"([^"]*)"', open(os.path.join(path, "mod.json"), encoding="latin-1").read())
            if desc and re.match(r"^\s*by\s+", desc[0], flags=re.I):
                author = re.sub(r"^\s*by\s+", "", desc[0], flags=re.I)
        size = 0
        for d, _, fs in os.walk(path):
            size += sum(os.path.getsize(os.path.join(d, f)) for f in fs)
        targets = sorted({map_of_zone[z] for z in replaced if z in map_of_zone})
        result["mods"].append({"key": "mods/" + folder, "folder": folder, "kind": kind, "name": name,
                               "author": author, "size_mb": size / 1048576.0, "zones": zones,
                               "maps": targets if targets and "mod" not in low else [],
                               "loadscreen": (re.findall(r'loadscreen\s+"([^"]+)"', arena) or [""])[0],
                               "video": os.path.isdir(os.path.join(path, "video")),
                               "image_iwds": [os.path.join(path, f) for f in files if f.lower().endswith(".iwd")]})
    return result


class Converter:
    def __init__(self, args):
        self.game = os.path.abspath(args.game.strip().strip('"'))
        # the tools: the converter's own copies (tools/, zonereader/ next to it: the release) - or, with --engine or
        # without them, the engine repository's (tools/vita, tools/zonereader: development)
        engine = getattr(args, "engine", None)
        if not engine and os.path.isfile(os.path.join(HERE, "tools", "zone_repack.py")):
            self.engine = None
            self.tools = os.path.join(HERE, "tools")
            self.zonereader = os.path.join(HERE, "zonereader")
        else:
            self.engine = os.path.abspath(engine or os.path.join(os.path.dirname(HERE), "bo1-zombies-decompiled"))
            self.tools = os.path.join(self.engine, "tools", "vita")
            self.zonereader = os.path.join(self.engine, "tools", "zonereader")
        self.out_root = os.path.abspath(args.out)
        self.out = os.path.join(self.out_root, "bo1zombies")
        self.work = os.path.join(HERE, "work")
        self.cache = os.path.join(self.work, "cache")
        self.jobs = args.jobs
        self.play_check = args.play_check
        self.only = {n.strip().lower() for n in (args.only or "").split(",") if n.strip()}
        # (--maps: only these original maps - the window's choice; None: every one that is installed)
        self.pick_maps = {n.strip().lower() for n in args.maps.split(",") if n.strip()} if getattr(
            args, "maps", None) is not None else None
        self.ffmpeg = find_ffmpeg()
        self.budget_of = {}  # converted zone -> its measured budget (JSON, zone_repack.py --budget)
        sys.path.insert(0, self.tools)
        global iwi_repack
        import iwi_repack  # noqa: F401 (the engine repository's)
        self.main_index = iwi_repack.archive_index(os.path.join(self.game, "main"))
        self.base_images = {r[0] for r in read_list(os.path.join(DATA, "base", "images.txt"))}
        # the game's zones by name: the original map each belongs to ("" for the shared ones)
        self.game_zones = {}
        for rel, _, _ in read_inputs(os.path.join(DATA, "base", "inputs.txt")):
            if rel.startswith("zone/"):
                self.game_zones[os.path.basename(rel)[:-3].lower()] = ""
        for m in os.listdir(os.path.join(DATA, "maps")):
            for z in (m, m + "_patch", "en_" + m):
                self.game_zones[z] = m
        self.base_streams = {r[0].lower() for r in read_list(os.path.join(DATA, "base", "streams.txt"))}
        # (and each original map's own: every main/iw_vita_<map>.iwd is in the search path, whichever map runs - a mod
        # read on a map, as a game_mod mod's <map>_patch_override zones are, does not need that map's images again)
        self.map_images, self.map_streams = {}, {}
        for m in os.listdir(os.path.join(DATA, "maps")):
            self.map_images[m] = {r[0] for r in read_list(os.path.join(DATA, "maps", m, "images.txt"))}
            self.map_streams[m] = {r[0].lower() for r in read_list(os.path.join(DATA, "maps", m, "streams.txt"))}

    def tool(self, script, args, log):
        with open(log, "w", encoding="utf-8") as f:
            # (the tools' ffmpeg threads: the CPU's share of one of the jobs running at once - else every zone of
            # several would start as many ffmpeg as the CPU has cores)
            env = dict(os.environ, BO1_TOOL_THREADS=str(max(1, (os.cpu_count() or 4) // max(1, self.jobs))))
            r = subprocess.run([sys.executable, "-I", os.path.join(self.tools, script)] + args, stdout=f,
                               stderr=subprocess.STDOUT, creationflags=NO_WINDOW, env=env)
        return r.returncode

    # ---- zones
    def repack(self, job):
        src, zmap, dst = job[:3]
        scale = job[3] if len(job) > 3 else 1.0  # (the low-poly scale: < 1 for a mod that does not fit otherwise)
        extra = list(job[4]) if len(job) > 4 else []  # (BIG_MAP_FLAGS)
        flags = FLAGS + extra + (["--low-poly-scale", "%g" % scale] if scale != 1.0 else [])
        if "--world-verts-gpu" in flags:  # (the two do not go together: the moved array stays 44 bytes a vertex)
            flags.remove("--world-verts-compact")
        # a mod's zone: its stereo sounds only 3D aliases play become mono (the reader's snddump_usage.txt; the game
        # plays only their first channel - zone_repack.py MONO_NAMES)
        in_mods = os.path.relpath(dst, os.path.join(self.out, "mods"))
        if not in_mods.startswith("..") and len(job) > 5:  # (a game zone's copy for the mod: its own list only)
            flags = flags + list(job[5])
        elif not in_mods.startswith(".."):
            dead = os.path.join(self.work, "run_" + in_mods.split(os.sep)[0], "dead_mod.txt")
            if os.path.isfile(dead):
                flags = flags + ["--empty-models", dead]
            usage = os.path.join(self.work, "run_" + in_mods.split(os.sep)[0], "snddump_usage.txt")
            if os.path.isfile(usage):
                flags = flags + ["--mono-3d", usage]
            # and its models' collision meshes simplified (ported models carry their whole render mesh as collision:
            # WDS's perk machines, 128815 -> 31323 triangles, 4.5 MB - zone_repack.py --simplify-collision), and its
            # surfaces that are copies of an earlier one's data share it (WDS: 84 of them, 2 MB; the retail zones
            # share theirs already - zone_repack.py --dedup-surfaces)
            flags = flags + ["--simplify-collision", "--dedup-surfaces"]
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not zmap:
            shutil.copyfile(src, dst)
            return dst, True, "copied"
        # (work/zonecache: a zone converted before from the same file, zone map and flags is reused; a model list's
        # content counts, not its path)
        # (v6: model_lowpoly.py keeps foliage LOD 0 when LOD 1 is a card - Nacht's bushes, 2026-11-25)
        lists = [sha1(flags[i + 1]) for i, f in enumerate(flags) if f in ("--drop-lods-models", "--mono-3d",
                                                                         "--empty-models", "--drop-anims",
                                                                         "--rename-models")]
        key = hashlib.sha1(("%s %s %s %s v6" % (sha1(src), sha1(zmap), " ".join(flags), " ".join(lists))).encode()
                           ).hexdigest()
        cached = os.path.join(self.work, "zonecache", key + ".ff")
        self.budget_of[dst] = cached[:-3] + ".json"  # (its measured memory: zone_repack.py --budget)
        if os.path.isfile(cached) and os.path.isfile(cached[:-3] + ".json"):
            shutil.copyfile(cached, dst)
            return dst, True, "ok (cached)"
        # (named by the output path: every mod has a mod.ff, and they convert at the same time)
        log = os.path.join(self.work, "logs", "repack_%s.log" % re.sub(r"[\\/:]+", "_", os.path.relpath(dst, self.out))[:-3])
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        rc = self.tool("zone_repack.py", ["repack", src, zmap, dst] + flags + ["--ffmpeg", self.ffmpeg,
                                                                               "--sound-cache", self.cache,
                                                                               "--budget", cached[:-3] + ".json"], log)
        text = open(log, encoding="utf-8", errors="replace").read()
        ok = rc == 0 and os.path.isfile(dst) and "Traceback" not in text
        if ok:
            shutil.copyfile(dst, cached)
        return dst, ok, "ok" if ok else "failed (%s)" % log

    def live_maps(self, mod):
        """the maps a mod's live models are listed on (STRIP_ZONES): a custom map's own, a mod's original maps (at most
        STRIP_MAX_MAPS)"""
        folder, safe, kind, map_zones, info = mod
        if kind == "custommap":
            return [map_zones[0]]
        maps = info.get("intended") or ([] if info["maps"] == "original" else info["maps"].split())
        maps = [m for m in maps if m in self.maps]
        return maps if 0 < len(maps) <= STRIP_MAX_MAPS else []

    def plan_strip(self, mod, swapped=frozenset()):
        """the mod's copies of game zones with the models nothing shows left out, and its own zones' dead models: ->
        [repack jobs] (and work/run_<safe>/dead_*.txt). swapped: models the mod's own copies give way to the game's
        for (MOD_LADDER): kept in the game zones"""
        folder, safe, kind, map_zones, info = mod
        run = os.path.join(self.work, "run_" + safe)
        lives = {m[len("live_"):-4]: f for f in glob.glob(os.path.join(run, "live_*.txt"))
                 for m in [os.path.basename(f)]}
        if not lives:
            return []
        live = {m: {line.split("\t")[0].lower() for line in open(f, encoding="latin-1") if line.strip()}
                for m, f in lives.items()}
        every = set().union(*live.values())
        # names quoted in scripts (the mod's archives, the zones' raw files): kept, whatever the runs saw
        quoted = set()
        for iwd in glob.glob(os.path.join(run, "mods", safe, "*.iwd")):
            with zipfile.ZipFile(iwd) as z:
                for n in z.namelist():
                    if n.lower().endswith((".gsc", ".csc", ".csv", ".txt")):
                        quoted |= {q.lower() for q in re.findall(r'"([A-Za-z0-9_]+)"', z.read(n).decode("latin-1"))}
        sources = [os.path.join(run, "mods", safe, f) for f in os.listdir(os.path.join(run, "mods", safe))
                   if f.lower().endswith(".ff")]
        sources += [os.path.join(self.game, "zone", "Common", z + ".ff") for z in STRIP_ZONES]
        if kind == "mod":
            sources += [os.path.join(self.game, "zone", "Common", m + ".ff") for m in live]
        for ff in sources:
            if os.path.isfile(ff):
                with open(ff, "rb") as f:
                    raw = zlib.decompressobj().decompress(f.read()[12:])
                quoted |= {q.decode("latin-1").lower() for q in re.findall(rb'"([A-Za-z0-9_]+)"', raw)}
        import zone_repack
        import model_lowpoly

        anim_live = {m: {line.split("\t")[0].lower() for line in open(f, encoding="latin-1")
                         if line.rstrip("\n").endswith("\tanim")} for m, f in lives.items()}
        anim_every = set().union(*anim_live.values())

        def anims_in(ff, zmap, alive, lst):
            """the zone's first-person weapon anims nothing used, written to lst: -> its --drop-anims flags"""
            _, stream, _ = zone_repack.read_ff(ff)
            _, events = zone_repack.read_zmap(zmap)
            bounds = [i for i, e in enumerate(events) if e[0] == zone_repack.ZM_ASSET] + [len(events)]
            dead = []
            for k in range(len(bounds) - 1):
                if events[bounds[k]][2] != zone_repack.ASSET_XANIM:
                    continue
                reads = [e for e in events[bounds[k]:bounds[k + 1]] if e[0] == zone_repack.ZM_READ and e[5]]
                if len(reads) < 2:
                    continue
                name = stream[reads[1][4]:reads[1][4] + reads[1][5]].rstrip(bytes(1)).decode("latin-1").lower()
                # (only the weapons' view anims: an AI or player anim can be looked up later than a short run)
                if name.startswith("viewmodel_") and name not in alive and name not in quoted:
                    dead.append(name)
            if not dead:
                return ()
            with open(lst, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(dead) + "\n")
            return ("--drop-anims", lst)

        def model_names(ff, zmap):
            _, stream, _ = zone_repack.read_ff(ff)
            _, events = zone_repack.read_zmap(zmap)
            models, _ = model_lowpoly.collect(stream, events)
            return {x.name for x in models if x.name}

        def dead_in(ff, zmap, alive, overridden=frozenset()):
            """the zone's models nothing shows, and (a game zone's) those the mod's own zones replace: a mod zone's
            asset wins over a game zone's of the same name (DB_OverrideAsset: the zone priority) - WDS's perk
            machines, arms and weapons, 4.6 MB of Kino's and common_zombie's (2026-11-24)"""
            return sorted(n for n in model_names(ff, zmap) if n.lower() in overridden or (
                n.lower() not in alive and n.lower() not in quoted))

        mod_models = set()  # (the models of the mod's own zones)
        for z in os.listdir(os.path.join(run, "mods", safe)):
            zmap = os.path.join(run, "zonemap", z[:-3] + ".zmap")
            if z.lower().endswith(".ff") and os.path.isfile(zmap):
                mod_models |= {n.lower() for n in model_names(os.path.join(run, "mods", safe, z), zmap)}
        mod_models -= set(swapped)

        jobs = []
        for z in STRIP_ZONES:  # (shared by all its maps: what none of them shows)
            ff = os.path.join(self.game, "zone", "Common", z + ".ff")
            zmap = os.path.join(DATA, "base", "zonemaps", z + ".zmap")
            if os.path.isfile(ff) and os.path.isfile(zmap):
                dead = dead_in(ff, zmap, every, mod_models)
                lst = os.path.join(run, "dead_%s%s.txt" % (z, "_swap" if swapped else ""))
                with open(lst, "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(dead) + "\n")
                jobs.append((ff, zmap, os.path.join(self.out, "mods", safe, z + ".ff"), 1.0, (),
                             ("--empty-models", lst) + anims_in(ff, zmap, anim_every,
                                                                os.path.join(run, "anims_%s.txt" % z))))
        if kind == "mod":
            for m in live:
                ff = os.path.join(self.game, "zone", "Common", m + ".ff")
                zmap = os.path.join(DATA, "maps", m, "zonemaps", m + ".zmap")
                if os.path.isfile(ff) and os.path.isfile(zmap):
                    dead = dead_in(ff, zmap, live[m], mod_models)
                    lst = os.path.join(run, "dead_%s%s.txt" % (m, "_swap" if swapped else ""))
                    with open(lst, "w", encoding="utf-8", newline="\n") as f:
                        f.write("\n".join(dead) + "\n")
                    jobs.append((ff, zmap, os.path.join(self.out, "mods", safe, m + ".ff"), 1.0, (),
                                 ("--empty-models", lst) + anims_in(ff, zmap, anim_live[m],
                                                                    os.path.join(run, "anims_%s.txt" % m))))
        # the mod's own zones: what none of its maps shows
        dead = set()
        for z in os.listdir(os.path.join(run, "mods", safe)):
            zmap = os.path.join(run, "zonemap", z[:-3] + ".zmap")
            if z.lower().endswith(".ff") and os.path.isfile(zmap):
                dead |= set(dead_in(os.path.join(run, "mods", safe, z), zmap, every))
        with open(os.path.join(run, "dead_mod.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(sorted(dead)) + "\n")
        return jobs

    def fit_mod(self, mod, zones, own, targets, failed_zones):
        """a mod measured next to each of its maps and made smaller step by step (MOD_LADDER) until it fits all of
        them: -> ((scale, zone MB, maps it fits, flags), report note) - or (None, why) when it fits none"""
        folder, safe, kind, map_zones, info = mod
        run = os.path.join(self.work, "run_" + safe)
        mod_dir = os.path.join(run, "mods", safe)
        out = os.path.join(self.out, "mods", safe)
        swap_names, current_swap, results = None, frozenset(), []
        self.last_rooms = {}

        def apply(flags, swap):
            nonlocal current_swap
            if swap != current_swap:  # (its copies of game zones: the swapped models kept there)
                with concurrent.futures.ThreadPoolExecutor(self.jobs) as pool:
                    if not all(ok for _, ok, _ in pool.map(self.repack, self.plan_strip(mod, swap))):
                        return False
                current_swap = swap
            jobs = [(os.path.join(mod_dir, z + ".ff"), os.path.join(run, "zonemap", z + ".zmap"),
                     os.path.join(out, z + ".ff"), 1.0, flags) for z in zones]
            with concurrent.futures.ThreadPoolExecutor(self.jobs) as pool:
                return all(ok for _, ok, _ in pool.map(self.repack, jobs))

        for step, (label, flags, swap) in enumerate(MOD_LADDER):
            flags = tuple(flags)
            swapped = frozenset()
            if swap:
                if swap_names is None:
                    swap_names = self.swap_candidates(mod, targets)
                if not swap_names and step == 1:
                    continue  # (nothing of the game's to use instead)
                swapped = frozenset(swap_names)
                if swap_names:
                    flags += ("--rename-models", os.path.join(run, "swap.txt"))
            if not apply(flags, swapped):
                break
            rooms = {m: self.mod_room_kb(safe, zones, own, m) for m in targets}
            results.append((step, label, flags, swapped, rooms))
            self.last_rooms = rooms
            note("mods/%s: step %d (%s): %s" % (folder, step, label, ", ".join(
                "%s %+.1f MB" % (m, r / 1024.0) for m, r in sorted(rooms.items()))))
            if all(r >= 0 for r in rooms.values()):
                break
        if not results:
            return None, "a zone could not be converted"
        best = next((r for r in results if all(v >= 0 for v in r[4].values())), None)
        if best is None:  # (the mildest step that fits the most maps)
            best = max(results, key=lambda r: (sum(v >= 0 for v in r[4].values()), -r[0]))
        fits = [m for m in targets if best[4][m] >= 0]
        missing = ", ".join("%s %.1f MB missing" % (m, -best[4][m] / 1024.0) for m in targets if best[4][m] < 0)
        if not fits:
            last = results[-1]
            return None, ", ".join("%s %.1f MB missing" % (m, -last[4][m] / 1024.0) for m in targets)
        if best is not results[-1]:
            apply(best[2], best[3])
        need = sum(zone_virtual_mb(os.path.join(out, z + ".ff")) for z in zones if z.lower() not in own)
        return (1.0, need, fits, best[2]), ("; not offered for %s (even made smaller)" % missing) if missing else ""

    def swap_candidates(self, mod, targets):
        """the mod's models that can give way to the game's own (MOD_LADDER step 1): a model of its zones with the
        name of one every target map has (common_zombie or the map's zone), not a weapon, first-person model,
        character or zombie (those must match the mod's animations), and one the game finds by name (nothing else
        in its zone points at it): -> sorted names (written to work/run_<safe>/swap.txt)"""
        folder, safe, kind, map_zones, info = mod
        run = os.path.join(self.work, "run_" + safe)
        import zone_repack
        import model_lowpoly

        def names_of(ff, zmap):  # (every model of a game zone: a map's are mostly inside its other assets)
            if not (os.path.isfile(ff) and os.path.isfile(zmap)):
                return set()
            _, stream, _ = zone_repack.read_ff(ff)
            _, events = zone_repack.read_zmap(zmap)
            models, _ = model_lowpoly.collect(stream, events)
            return {x.name.lower() for x in models if x.name}

        common = set()
        for z in STRIP_ZONES:
            common |= names_of(os.path.join(self.game, "zone", "Common", z + ".ff"),
                               os.path.join(DATA, "base", "zonemaps", z + ".zmap"))
        game = None
        for m in targets:
            have = common | names_of(os.path.join(self.game, "zone", "Common", m + ".ff"),
                                     os.path.join(DATA, "maps", m, "zonemaps", m + ".zmap"))
            game = have if game is None else game & have
        found = set()
        for z in os.listdir(os.path.join(run, "mods", safe)):
            zmap = os.path.join(run, "zonemap", z[:-3] + ".zmap")
            if not (z.lower().endswith(".ff") and os.path.isfile(zmap)):
                continue
            _, stream, _ = zone_repack.read_ff(os.path.join(run, "mods", safe, z))
            _, events = zone_repack.read_zmap(zmap)
            names = zone_repack.model_names_at(stream, events)
            refs = zone_repack.referenced_models(stream, events)
            found |= {n for n in names if n in (game or set()) and n not in refs and not model_lowpoly.is_weapon(n)
                      and not model_lowpoly.is_first_person(n) and not model_lowpoly.is_character(n)}
        with open(os.path.join(run, "swap.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(sorted(found)) + "\n")
        return sorted(found)

    def mod_room_kb(self, safe, zones, own, m):
        """the zone pool left on the Vita when the mod runs on map m (POOL_KB): its zones, the shared zones and the
        map's - each the mod's own copy when it has one - against the measured pool; < 0: that much missing"""
        out = os.path.join(self.out, "mods", safe)
        used = POOL_BASE_KB + POOL_CLIENT_KB
        for z in zones:
            if own.get(z.lower(), m) == m:
                used += zone_cost_kb(os.path.join(out, z + ".ff"))
        rels = [rel for rel, _, _ in read_inputs(os.path.join(DATA, "base", "inputs.txt"))
                if os.path.basename(rel)[:-3].lower() in ("common_zombie", "common_zombie_patch", "en_common_zombie")]
        rels += [rel for rel, _, _ in read_inputs(os.path.join(DATA, "maps", m, "inputs.txt"))]
        for rel in rels:
            copy = os.path.join(out, os.path.basename(rel))
            path = copy if os.path.isfile(copy) else os.path.join(self.out, rel)
            if os.path.isfile(path):
                used += zone_cost_kb(path)
        return POOL_KB - POOL_RESERVE_KB - used

    def read_budget(self, zone_path):
        """a converted zone's measured budget (zone_repack.py --budget), {} when there is none"""
        path = self.budget_of.get(zone_path)
        try:
            return json.load(open(path, encoding="utf-8")) if path else {}
        except (OSError, ValueError):
            return {}

    def measured(self, out, zones):
        """'; measured: ...' - what takes the zones' memory (the biggest kinds), for the report"""
        types = {}
        for z in zones:
            for k, v in self.read_budget(os.path.join(out, z + ".ff")).get("types", {}).items():
                row = types.setdefault(k, [0.0, 0.0])
                row[0] += v[0]
                row[1] += v[1]
        if not types:
            return ""
        label = {"gfxworld": "world", "xmodel": "models", "clipmap": "collision", "sound": "sounds", "xanim": "animations",
                 "fx": "effects", "image": "images", "techniqueset": "shaders", "material": "materials"}
        top = sorted(types.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:5]
        return "; measured: " + ", ".join("%s %.1f MB%s" % (label.get(k, k), v[0] + v[1], " (video)" if v[1] > v[0]
                                                                 else "") for k, v in top)

    def repack_all(self, jobs):
        failed = []
        progress(0, len(jobs), "")
        with concurrent.futures.ThreadPoolExecutor(self.jobs) as pool:
            for i, (dst, ok, why) in enumerate(pool.map(self.repack, jobs)):
                say("  %s %s" % (os.path.relpath(dst, self.out), why))
                progress(i + 1, len(jobs), os.path.basename(dst))
                if not ok:
                    failed.append(dst)
        return failed

    # ---- textures
    def build_archive(self, entries, index, out_iwd, texture_max=TEXTURE_MAX, nomip_max=NOMIP_MAX, art=()):
        """an archive of images as a list gives them: cut to texture_max (without mip levels: trimmed and halved down
        to nomip_max, a font to FONT_MAX; art - a loading screen - to ART_MAX, the wallbuys' chalk to DETAIL_MAX), or
        the 4x4 placeholder"""
        art = {a.lower() for a in art}
        missing, open_zips, images = [], {}, 0
        os.makedirs(os.path.dirname(out_iwd), exist_ok=True)
        with zipfile.ZipFile(out_iwd, "w", zipfile.ZIP_STORED) as archive:
            for name, kind in entries:
                if kind == "placeholder":
                    archive.writestr("images/%s.iwi" % name, iwi_repack.PLACEHOLDER)
                    continue
                found = index.get(name.lower())
                if not found:
                    missing.append(name)
                    continue
                zip_path, member = found
                z = open_zips.get(zip_path) or open_zips.setdefault(zip_path, zipfile.ZipFile(zip_path))
                data = z.read(member)
                if data[:3] != b"IWi" or data[3] != 13:
                    missing.append(name)
                    continue
                if name.lower() in art:
                    image_max = nomip = ART_MAX
                elif any(w in name.lower() for w in DETAIL_WORDS):
                    image_max = nomip = DETAIL_MAX
                else:
                    image_max, nomip = texture_max, FONT_MAX if "font" in name.lower() else nomip_max
                while (data[5] & 3) and data[4] in (1, 2) and max(struct.unpack_from("<2h", data, 6)) > image_max:
                    data = iwi_repack.halve_uncompressed(data)
                while (data[5] & 4) and data[4] == 11 and max(struct.unpack_from("<2h", data, 6)) > CUBE_MAX:
                    data = iwi_repack.halve_dxt1(data)
                small, _ = iwi_repack.cut(data, image_max)
                small, _ = iwi_repack.shrink_nomip(small, nomip)
                archive.writestr("images/%s.iwi" % name, small)
                images += 1
        for z in open_zips.values():
            z.close()
        return images, len(entries) - images - len(missing), missing

    def menu_art(self):
        out_iwd = os.path.join(self.out, "main", "iw_vita_menu.iwd")
        with zipfile.ZipFile(out_iwd, "w", zipfile.ZIP_STORED) as archive:
            for name, in read_list(os.path.join(DATA, "menu", "loadscreens.txt")):
                found = self.main_index.get(name.lower())
                if not found:
                    continue
                data = zipfile.ZipFile(found[0]).read(found[1])
                while data[4] == 11 and max(struct.unpack_from("<2h", data, 6)) > ART_MAX:
                    data = iwi_repack.halve_dxt1(data)
                # (the port's cover instead of a picture: data/cover - loadscreen_zombie_deadops is the default
                # loading picture of custom maps and mods without their own, ui_shared.cpp Bo1_CoverArtLoaded)
                cover = os.path.join(DATA, "cover", name + ".iwi")
                if os.path.isfile(cover):
                    data = open(cover, "rb").read()
                archive.writestr("images/%s.iwi" % name, data)

    def build_buttons(self):
        """main/iw_vitabuttons.iwd: the PS Vita's buttons instead of the Xbox pad's - in the fonts (the button glyphs of
        the converted gamefonts_pc atlas painted over, data/icons: Xelu's prompts, CC0) and the HUD's d-pad pictures
        (tools/ps_buttons.py). Its name sorts after iw_vita*.iwd: its images win"""
        out_iwd = os.path.join(self.out, "main", "iw_vitabuttons.iwd")
        icons = os.path.join(DATA, "icons")
        tmp = os.path.join(self.work, "buttons")
        os.makedirs(tmp, exist_ok=True)
        log = os.path.join(self.work, "logs", "buttons.log")
        with zipfile.ZipFile(os.path.join(self.out, "main", "iw_vita.iwd")) as z:
            open(os.path.join(tmp, "gamefonts_pc_in.iwi"), "wb").write(z.read("images/gamefonts_pc.iwi"))
        names = []
        if self.tool("ps_buttons.py", [os.path.join(tmp, "gamefonts_pc_in.iwi"), os.path.join(DATA, "glyphs.json"),
                                       os.path.join(tmp, "gamefonts_pc.iwi"), "--icons", icons], log) == 0:
            names.append("gamefonts_pc")
        for name in ("ui_button_xenon_dpad_64x64", "hud_dpad_xenon"):
            found = self.main_index.get(name)
            if not found:
                continue
            open(os.path.join(tmp, name + "_in.iwi"), "wb").write(zipfile.ZipFile(found[0]).read(found[1]))
            if self.tool("ps_buttons.py", ["--hud-dpad", icons, os.path.join(tmp, name + "_in.iwi"),
                                           os.path.join(tmp, name + ".iwi")], log) == 0:
                names.append(name)
        with zipfile.ZipFile(out_iwd, "w", zipfile.ZIP_STORED) as archive:
            for name in names:
                archive.write(os.path.join(tmp, name + ".iwi"), "images/%s.iwi" % name)
        return len(names)

    def build_world_lights(self):
        """main/vitalight/<map>.bin: each converted original map's primary lightmaps (HD MODE's world lighting,
        tools/lightmap_primary.py - the Vita bakes <map>.baked from it at the map's first HD start)"""
        out_dir = os.path.join(self.out, "main", "vitalight")
        os.makedirs(out_dir, exist_ok=True)
        done = 0
        for m in self.maps:
            zmap = os.path.join(DATA, "maps", m, "zonemaps", m + ".zmap")
            ff = os.path.join(self.game, "zone", "Common", m + ".ff")
            if os.path.isfile(zmap) and os.path.isfile(ff) and self.tool(
                    "lightmap_primary.py", [ff, zmap, os.path.join(out_dir, m + ".bin")],
                    os.path.join(self.work, "logs", "vitalight_%s.log" % m)) == 0:
                done += 1
        return done

    # ---- streamed sounds
    def pack_streams(self, rows, out_iwd, log, mod_dir=None):
        lst = log[:-4] + "_list.txt"
        with open(lst, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write("\t".join(r) + "\n")
        args = [lst, os.path.join(self.game, "main"), out_iwd, "--ffmpeg", self.ffmpeg, "--sound-cache", self.cache]
        if mod_dir:
            args += ["--mod-dir", mod_dir]
        return self.tool("sound_pack.py", args, log) == 0

    # ---- the steps
    def check_game(self):
        say("\n== Checking %s" % self.game)
        if not os.path.isdir(os.path.join(self.game, "zone")) or not os.path.isdir(os.path.join(self.game, "main")):
            fail("'%s' has no zone and main folders: use the folder with BlackOps.exe" % self.game)
        status = check_inputs(self.game, read_inputs(os.path.join(DATA, "base", "inputs.txt")))
        if status != "ok":
            fail("the base game: " + status)
        self.maps = []
        names = sorted(os.listdir(os.path.join(DATA, "maps")))
        for i, m in enumerate(names):
            progress(i, len(names), "checking " + MAP_NAMES.get(m, m))
            if self.pick_maps is not None and m not in self.pick_maps:
                note("map %-22s not chosen" % m)
                tell("item", m, "skipped", "not chosen")
                continue
            status = check_inputs(self.game, read_inputs(os.path.join(DATA, "maps", m, "inputs.txt")))
            if status == "ok":
                self.maps.append(m)
            note("map %-22s %s" % (m, "converted" if status == "ok" else "SKIPPED: " + status))
            tell("item", m, "ok" if status == "ok" else "skipped", "converted" if status == "ok" else status)
        for m in ("zombie_theater", "zombie_pentagon"):
            if not os.path.isfile(os.path.join(self.game, "zone", "common", m + ".ff")):
                fail("%s is part of the base game and must be there" % m)

    def find_mods(self):
        """[(folder, safe name, kind, map zones, info)]"""
        mods = []
        root = os.path.join(self.game, "mods")
        for folder in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            path = os.path.join(root, folder)
            if not os.path.isdir(path):
                continue
            safe = re.sub(r"[^a-z0-9_]+", "_", folder.lower()).strip("_") or "mod"
            if safe in MODIFIERS or any(safe == m[1] for m in mods):
                safe = "user_" + safe
            zones = [f[:-3] for f in os.listdir(path) if f.lower().endswith(".ff")]
            arena = open(os.path.join(path, "mod.arena"), encoding="latin-1").read() if os.path.isfile(
                os.path.join(path, "mod.arena")) else ""
            arena_maps = re.findall(r'map\s+"([^"]+)"', arena)
            # zones named as the game's are replacements (a changed zombie_theater_patch: a mod for Kino, the engine
            # loads the mod folder's copy instead of the game's); the others with a level are custom maps
            replaced = {z.lower() for z in zones} & set(self.game_zones)
            # (the game_mod loader's names, which the engine loads as game_mod does: mod1.ff .. mod7.ff with mod.ff,
            # and <map>_patch_override.ff after that map - no maps either; other *_override zones it does not load)
            extra = {z.lower() for z in zones if re.match(r"^mod\d+$", z.lower()) or z.lower().endswith("_override")}
            overrides = {}  # {original map: its _patch_override zone}
            for z in zones:
                m = re.match(r"^(.+)_patch_override$", z.lower())
                if m and m.group(1) in self.maps:
                    overrides[m.group(1)] = z
            targets = sorted({self.game_zones[z] for z in replaced if self.game_zones[z]})
            map_zones = [z for z in zones if z.lower() != "mod" and z.lower() not in replaced
                         and z.lower() not in extra and (z in arena_maps or not arena_maps)]
            info = {"loadscreen": (re.findall(r'loadscreen\s+"([^"]+)"', arena) or [""])[0],
                    "longname": (re.findall(r'longname\s+"([^"]+)"', arena) or [""])[0], "author": "",
                    "overrides": overrides}
            if os.path.isfile(os.path.join(path, "mod.json")):
                # (its "desc": "By <author>", "Version <n>" or a description; its "vers": the version)
                text = open(os.path.join(path, "mod.json"), encoding="latin-1").read()
                desc = re.findall(r'"desc"\s*:\s*"([^"]*)"', text)
                vers = re.findall(r'"vers"\s*:\s*"([^"]*)"', text)
                is_version = desc and re.match(r"^\s*v(ersion)?\s*([\d.]+\w*)\s*$", desc[0], flags=re.I)
                if desc and re.match(r"^\s*by\s+", desc[0], flags=re.I):
                    info["author"] = re.sub(r"^\s*by\s+", "", desc[0], flags=re.I).strip()
                elif is_version:
                    vers = vers or [is_version.group(2)]
                elif desc and desc[0].strip():
                    info["description"] = desc[0].strip()
                if vers and vers[0].strip():
                    info["version"] = vers[0].strip()
            if map_zones:
                kind = "custommap"
            elif "mod" in [z.lower() for z in zones] or replaced or glob.glob(os.path.join(path, "*.iwd")):
                kind = "mod"
            else:
                note("mods/%s: SKIPPED: no zone or archive to convert" % folder)
                continue
            # (a mod that only replaces one map's zones works on that map; one with its own mod.ff, or replacing shared
            # zones, on all of them)
            has_mod_ff = "mod" in [z.lower() for z in zones]
            info["maps"] = " ".join(targets) if targets and not has_mod_ff and len(targets) == len(
                {self.game_zones[z] for z in replaced}) else "original"
            # a mod with its own level scripts for some original maps (maps/<map>.gsc in its archives: WDS has Kino,
            # Five, Ascension and Moon's - "requires DLC", user 2026-11-24) is made for those maps: listed for them,
            # not for a map it was not made for that its zones would fit next to
            info["intended"] = sorted(intended_maps(path, self.game_zones))
            mods.append((folder, safe, kind, map_zones, info))
        return mods

    def record(self, mod):
        """copies the mod into its run folder and records it: a PC run of its map (a mod: on Kino)"""
        folder, safe, kind, map_zones, info = mod
        run = os.path.join(self.work, "run_" + safe)
        mod_dir = os.path.join(run, "mods", safe)
        if os.path.isdir(mod_dir):
            shutil.rmtree(mod_dir)
        os.makedirs(mod_dir)
        src = os.path.join(self.game, "mods", folder)
        for name in os.listdir(src):
            if os.path.isfile(os.path.join(src, name)):
                shutil.copyfile(os.path.join(src, name), os.path.join(mod_dir, name))
        for old in ["zonemap", "snddump_streams.txt", "snddump_usage.txt", "snddump.csv", "dead_mod.txt",
                    os.path.join("main", "bo1_emergency.log")] + [os.path.basename(f) for f in glob.glob(
                        os.path.join(run, "live_*.txt")) + glob.glob(os.path.join(run, "dead_*.txt"))
                        + glob.glob(os.path.join(run, "anims_*.txt"))]:
            p = os.path.join(run, old)
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.isfile(p):
                os.remove(p)
        level = map_zones[0] if kind == "custommap" else info["maps"].split()[0] if info["maps"] != "original" \
            else "zombie_theater"
        # read in Python (tools/zonereader, no engine run): the level's zones as a map start loads them - the zone maps
        # of the mod's own zones, the image and material lines, the sound lists - and for each map the mod is for the
        # models and weapon view anims it can show (what bo1_livemodels listed after a minute of the map). A game
        # zone's results are cached in work/zonecache (the same for every mod).
        sys.path.insert(0, self.zonereader)
        import zr_record
        import zr_models
        mod_dirs = [mod_dir]
        own = {f[:-3] for f in os.listdir(mod_dir) if f.lower().endswith(".ff")}
        cache = os.path.join(self.work, "zonecache")
        log = os.path.join(self.work, "logs", "record_%s.log" % safe)
        files = zr_models.read_mod_files(
            [os.path.join(mod_dir, f) for f in os.listdir(mod_dir) if f.lower().endswith(".iwd")])
        levels = sorted(set(MAP_NAMES) | set(os.listdir(os.path.join(DATA, "maps"))) | {z.lower() for z in map_zones})
        lines, merged, usage, memo = [], {}, None, {}
        maps = [level] + sorted(set(info["overrides"]) - {level})
        for m in self.live_maps(mod):
            if m not in maps:
                maps.append(m)
        live_maps = set(self.live_maps(mod))
        for i, m in enumerate(maps):
            zones = zr_record.find_zones(zr_record.level_zone_names(m, [safe]), mod_dirs, self.game)
            if not any(n == m for n, _ in zones):
                return mod, False, "its map %s has no zone" % m
            try:
                one_lines, models = zr_record.record_level(zones, own, run, cache, memo)
            except Exception as e:  # (a zone this reader cannot read: the mod is left out)
                return mod, False, "a zone could not be read (%s)" % str(e)[:160]
            if i == 0:  # (the level's sound use: which stereo sounds only 3D aliases play)
                usage = open(os.path.join(run, "snddump_usage.txt"), encoding="latin-1").read()
            if i == 0 or m in info["overrides"]:
                lines += one_lines
                for r in read_list(os.path.join(run, "snddump_streams.txt")):
                    merged.setdefault(r[0].lower(), r)
            if m in live_maps:
                # the live list: the models it can show (its weapons, precached, static, effects ...: zr_models.live_set)
                # and every anim but the weapon view anims only weapons it does not register use
                live, anims, dead_anims = zr_models.live_set(models, files, m, levels)
                with open(os.path.join(run, "live_%s.txt" % m), "w", encoding="latin-1", newline="\n") as f:
                    for n in sorted(live):
                        f.write("%s\tlive\n" % n)
                    for n in sorted(models.anims - dead_anims):
                        f.write("%s\tanim\n" % n)
        with open(log, "w", encoding="latin-1", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        with open(os.path.join(run, "snddump_streams.txt"), "w", encoding="utf-8", newline="\n") as f:
            for r in merged.values():
                f.write("\t".join(r) + "\n")
        with open(os.path.join(run, "snddump_usage.txt"), "w", encoding="latin-1", newline="\n") as f:
            f.write(usage or "")
        # --play-check: also a minute of the map on the PC (catches what only a level start shows: script errors,
        # crashes); not recorded
        if self.play_check:  # (needs the engine's PC build: a developer's check, not the release's)
            base = "+set fs_game mods/%s +set bo1_mod_zones %s " % (safe, safe)
            log = os.path.join(self.work, "logs", "play_%s.log" % safe)
            emergency = os.path.join(run, "main", "bo1_emergency.log")
            if os.path.isfile(emergency):
                os.remove(emergency)
            self.headless(run, base + "+devmap " + level, log, client=True, autoquit=60000)
            why = self.run_problem(run, log, r"bo1_autoquit: .*level\.time [1-9]")
            if why:
                return mod, False, "the PC play check failed: " + why
        return mod, True, ""

    def headless(self, run, commands, log, client, autoquit):
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        os.path.join(self.engine, "tools", "headless.ps1"), "-Zombies"] + (["-Client"] if client else [])
                       + ["-RunDir", run, "-GameDir", self.game, "-Commands", commands, "-AutoQuitMs", str(autoquit),
                          "-TimeoutSec", "600", "-SaveLog", log, "-TailLines", "1"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)

    def run_problem(self, run, log, success):
        """why a PC run failed (its log, its emergency log), or None when the log has the success line"""
        text = open(log, encoding="latin-1").read() if os.path.isfile(log) else ""
        error = re.search(r"Com_ERROR: ([^\r\n]*)|Sys_Error: ([^\r\n]*)", text)
        emergency = os.path.join(run, "main", "bo1_emergency.log")
        emergency_text = open(emergency, encoding="latin-1").read() if os.path.isfile(emergency) else ""
        crash = re.search(r"CRASH: ([^\r\n]*)", emergency_text)
        if "Out of memory" in emergency_text:
            return "too big: it ran out of memory even on the PC (far more than the Vita has)"
        if crash:
            return "the game crashed on the PC (%s)" % crash.group(1)[:120]
        if error:
            return (error.group(1) or error.group(2)).strip()[:160]
        if not re.search(success, text):
            return "the run did not finish (%s)" % log
        return None

    def convert_mod(self, mod, failed_zones):
        folder, safe, kind, map_zones, info = mod
        run = os.path.join(self.work, "run_" + safe)
        mod_dir = os.path.join(run, "mods", safe)
        out = os.path.join(self.out, "mods", safe)
        zones = [z[:-3] for z in os.listdir(mod_dir) if z.lower().endswith(".ff") and
                 os.path.isfile(os.path.join(run, "zonemap", z[:-3] + ".zmap"))]
        unused = [z[:-3] for z in os.listdir(mod_dir) if z.lower().endswith(".ff") and z[:-3] not in zones]
        if any(os.path.join(out, z + ".ff") in failed_zones for z in zones):
            shutil.rmtree(out, ignore_errors=True)
            return "SKIPPED: a zone could not be converted (see work/logs)"
        # does it fit? A custom map: its zones in the map budget. A mod: next to each original map it is for (the
        # map's own zones + the mod's in the budget) - listed only for those. Too big: converted again with smaller
        # models (LOW_POLY_SCALES), the mildest that fits (for a mod: that fits on Kino, else any of its maps)
        targets = [m for m in (self.maps if info["maps"] == "original" else info["maps"].split()) if m in self.maps]
        if kind == "mod" and info.get("intended"):
            targets = [m for m in info["intended"] if m in self.maps]
        if kind == "mod" and not targets:
            shutil.rmtree(out, ignore_errors=True)
            return "SKIPPED: the original maps it is for are not chosen"
        tried, chosen = [], None
        own = {z.lower(): m for m, z in info["overrides"].items()}
        # its copies of game zones: the room they make (a shared zone's: for every map; a map's: for that map)
        map_mb, freed, strip_note = dict(self.map_mb), 0.0, ""
        for job in getattr(self, "strip_jobs", {}).get(safe, []):
            src, dst = job[0], job[2]
            name = os.path.basename(dst)[:-3]
            if not os.path.isfile(dst) or dst in failed_zones:
                continue
            full = zone_virtual_mb(os.path.join(self.out, "zone", "Common", os.path.basename(dst)))
            gain = full - zone_virtual_mb(dst) if os.path.isfile(os.path.join(self.out, "zone", "Common",
                                                                              os.path.basename(dst))) else 0.0
            if name in STRIP_ZONES:
                freed += gain
            elif name in map_mb:
                map_mb[name] -= gain
            strip_note += "%s %.1f MB, " % (name, gain)
        for m in map_mb:
            map_mb[m] -= freed
        lods_file = os.path.join(self.work, "logs", "lods_%s.txt" % safe)
        variants = []
        mod_note = ""
        if kind == "mod":  # (measured next to each of its maps, smaller step by step: MOD_LADDER)
            # (maps it does not fit even made smaller: measured again without them - fewer maps, more of the game's
            # own models in common to use instead of its copies; when it fits none, without the one most missing)
            maps, dropped = list(targets), {}
            while True:
                chosen, mod_note = self.fit_mod(mod, zones, own, maps, failed_zones)
                rooms = self.last_rooms
                if chosen is not None and len(chosen[2]) == len(maps):
                    break
                # (one map at a time, the one most missing: without it the others may share more of the game's own)
                if len(maps) <= 1 or not rooms:
                    break
                worst = min(maps, key=lambda m: rooms[m])
                dropped[worst] = rooms[worst]
                maps.remove(worst)
            if chosen is not None and dropped:
                mod_note = "; not offered for %s (even made smaller)" % ", ".join(
                    "%s %.1f MB missing" % (m, -kb / 1024.0) for m, kb in sorted(dropped.items()))
            elif chosen is None and dropped:
                mod_note = ", ".join("%s %.1f MB missing" % (m, -kb / 1024.0) for m, kb in sorted(
                    dict(dropped, **(self.last_rooms or {})).items()) if kb < 0)
            if chosen is None:
                shutil.rmtree(out, ignore_errors=True)
                return "SKIPPED: too big for the Vita even made smaller (%s)" % mod_note
        if kind == "custommap":  # (a map too big as it is: the steps of BIG_BASE_FLAGS)
            variants = [(1.0, ()), (1.0, SOUND_FLAGS), (1.0, BIG_BASE_FLAGS), (1.0, "lods"),
                        (1.0, BIG_MAP_FLAGS)] + [(s, BIG_MAP_FLAGS) for s in LOW_POLY_SCALES[1:]] + [
                (s, BIG_MAP_FLAGS + f) for s, f in WEAPON_STEPS]
        for scale, flags in variants:
            if flags == "lods":  # the biggest models one LOD lower, as many as the last step's measurement needs
                over = tried[-1][1] - BIG_MAP_POOL if tried else 0
                rows = []
                for z in zones:
                    if z.lower() in own:
                        continue
                    budget = self.read_budget(os.path.join(out, z + ".ff"))
                    rows += [r for r in budget.get("models", []) if len(r) > 5 and r[5] > 0]
                rows.sort(key=lambda r: -r[5])
                picked, saved = [], 0.0
                for r in rows:
                    if saved >= over * 1.15 + 0.5:
                        break
                    picked.append(r[0])
                    saved += r[5]
                if over <= 0 or saved < over or not picked:  # (not enough to gain this way: the next step)
                    continue
                with open(lods_file, "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(picked) + "\n")
                flags = BIG_BASE_FLAGS + ("--drop-lods-models", lods_file)
                note("mods/%s: %d of its biggest models one LOD lower (%.1f MB over, %.1f MB expected back)" % (
                    folder, len(picked), over, saved))
            if (scale, flags) != (1.0, ()):
                jobs = [(os.path.join(mod_dir, z + ".ff"), os.path.join(run, "zonemap", z + ".zmap"),
                         os.path.join(out, z + ".ff"), scale, flags) for z in zones]
                with concurrent.futures.ThreadPoolExecutor(self.jobs) as pool:
                    if not all(ok for _, ok, _ in pool.map(self.repack, jobs)):
                        break
            sizes = {z: zone_virtual_mb(os.path.join(out, z + ".ff")) for z in zones}
            # (a <map>_patch_override zone counts only next to its map)
            need = sum(v for z, v in sizes.items() if z.lower() not in own)
            extra = {m: sum(v for z, v in sizes.items() if own.get(z.lower()) == m) for m in targets}
            fits = [] if kind == "custommap" else [m for m in targets if map_mb[m] + need + extra[m] <= MAP_BUDGET]
            big = "--world-verts-gpu" in flags
            if big:  # (the big-map conversion's own check: its blocks in the pool, see BIG_MAP_POOL)
                need = sum(zone_pool_mb(os.path.join(out, z + ".ff")) for z in zones if z.lower() not in own)
            if kind == "custommap":  # (what its copy of a shared zone frees counts for it)
                need -= freed
            video = sum(zone_gpu_mb(os.path.join(out, z + ".ff")) for z in zones if z.lower() not in own)
            tried.append((scale, need, fits, flags))
            if kind == "custommap" and need <= (BIG_MAP_POOL if big else MAP_BUDGET) and (big or video <= GPU_BUDGET) \
                    or "zombie_theater" in fits or fits and "zombie_theater" not in targets:
                chosen = tried[-1]
                break
        if not chosen:
            chosen = next((t for t in tried if t[2]), None)
            if chosen and (chosen[0], chosen[3]) != (tried[-1][0], tried[-1][3]):  # (the milder conversion that
                # fit some maps: from the cache)
                jobs = [(os.path.join(mod_dir, z + ".ff"), os.path.join(run, "zonemap", z + ".zmap"),
                         os.path.join(out, z + ".ff"), chosen[0], chosen[3]) for z in zones]
                with concurrent.futures.ThreadPoolExecutor(self.jobs) as pool:
                    list(pool.map(self.repack, jobs))
        over_note = mod_note
        if not chosen and kind == "mod" and info.get("intended") and tried:
            small = min(tried, key=lambda t: t[1])
            room = MAP_BUDGET - min(map_mb[m] for m in targets)
            over_note = "; WARNING: %.1f MB over the measured zone memory next to its maps (%.0f MB fit) - it may " \
                        "run out of memory loading" % (small[1] - room, room)
            if small is not tried[-1]:
                jobs = [(os.path.join(mod_dir, z + ".ff"), os.path.join(run, "zonemap", z + ".zmap"),
                         os.path.join(out, z + ".ff"), small[0], small[3]) for z in zones]
                with concurrent.futures.ThreadPoolExecutor(self.jobs) as pool:
                    list(pool.map(self.repack, jobs))
            chosen = (small[0], small[1], list(targets), small[3])
        if not chosen:
            shutil.rmtree(out, ignore_errors=True)
            smallest = min(t[1] for t in tried) if tried else 0
            room = MAP_BUDGET if kind == "custommap" else MAP_BUDGET - min(map_mb[m] for m in targets or self.maps)
            return "SKIPPED: too big for the Vita - its zones need %.0f MB of zone memory even with the smallest " \
                   "models, about %.0f MB fit" % (smallest, room)
        scale, need, fits, flags = chosen
        gpu = sum(zone_gpu_mb(os.path.join(out, z + ".ff")) for z in zones)
        big = "--world-verts-gpu" in flags
        measured = self.measured(out, [z for z in zones if z.lower() not in own])
        if kind == "mod":
            info["maps"] = " ".join(targets) if info.get("intended") and set(fits) == set(targets) else \
                info["maps"] if set(fits) == set(targets) else " ".join(fits)
        # (the _patch_override zones of maps it is not listed for are not shipped, nor its copies of their zones)
        listed = self.maps if info["maps"] == "original" else info["maps"].split()
        for job in getattr(self, "strip_jobs", {}).get(safe, []):
            name = os.path.basename(job[2])[:-3]
            if name not in STRIP_ZONES and name not in listed and os.path.isfile(job[2]):
                os.remove(job[2])
        for m, z in info["overrides"].items():
            if kind == "mod" and m not in listed and z in zones:
                os.remove(os.path.join(out, z + ".ff"))
                zones.remove(z)
        on_maps = [m for m in self.maps if m in self.map_images]
        map_images = set().union(*(self.map_images[m] for m in on_maps))
        map_streams = set().union(*(self.map_streams[m] for m in on_maps))
        logs = os.path.join(self.work, "logs")
        # textures: the run's images by the Vita's rules, without those iw_vita.iwd has (unless the mod has its own)
        own_index = {}  # (every archive in the mod's folder; archive_index's first folder takes iw_* names only)
        for iwd in sorted(glob.glob(os.path.join(mod_dir, "*.iwd"))):
            with zipfile.ZipFile(iwd) as z:
                for member in z.namelist():
                    low = member.lower()
                    if low.startswith("images/") and low.endswith(".iwi"):
                        own_index[low[len("images/"):-len(".iwi")]] = (iwd, member)
        lst = os.path.join(logs, "images_%s.txt" % safe)
        level = map_zones[0] if kind == "custommap" else info["maps"].split()[0] if info["maps"] != "original" \
            else "zombie_theater"
        self.tool("image_list.py", [os.path.join(logs, "record_%s.log" % safe), level, lst, "--base",
                                    os.path.join(DATA, "base", "images.txt"), "--zones", ",".join(zones)],
                  os.path.join(logs, "imagelist_%s.log" % safe))
        entries = [r for r in read_list(lst) if r[0] not in self.base_images and r[0] not in map_images
                   or r[0].lower() in own_index]
        index = dict(self.main_index)
        index.update(own_index)
        # a custom map's loading screen (its mod.arena's material): its images at the menu art's size
        art = set()
        if kind == "custommap" and info["loadscreen"]:
            want = info["loadscreen"].lower()
            for line in open(os.path.join(logs, "record_%s.log" % safe), encoding="latin-1"):
                m = re.match(r"^matstats '[^']*' (\S+)(.*)$", line.strip())
                if m and m.group(1).lower() == want:
                    art.update(m.group(2).split())
            if not art:
                note("mods/%s: its loading screen '%s' was not in the PC run (left at the texture size)" % (
                    folder, info["loadscreen"]))
        images, holders, missing = self.build_archive(entries, index, os.path.join(out, safe + "_vita_images.iwd"),
                                                      BIG_MAP_TEXTURE_MAX if big else TEXTURE_MAX,
                                                      BIG_MAP_NOMIP_MAX if big else NOMIP_MAX, art)
        # streamed sounds: the run's, without those iw_vita_snd.iwd has (unless the mod has its own)
        own_files = set()
        for iwd in glob.glob(os.path.join(mod_dir, "*.iwd")):
            own_files |= {n.lower().replace("/", "\\") for n in zipfile.ZipFile(iwd).namelist()}
        streams = [r for r in read_list(os.path.join(run, "snddump_streams.txt"))
                   if r[0].lower() not in self.base_streams and r[0].lower() not in map_streams
                   or r[0].lower() in own_files]
        if streams:
            self.pack_streams(streams, os.path.join(out, safe + "_vita_snd.iwd"),
                              os.path.join(logs, "soundpack_%s.log" % safe), mod_dir)
        # the mod's own archives without their images and sounds (those are in the converted ones)
        for iwd in glob.glob(os.path.join(mod_dir, "*.iwd")):
            with zipfile.ZipFile(iwd) as z:
                keep = [n for n in z.namelist() if not n.lower().startswith(("images/", "sound/"))
                        and not n.endswith("/")]
                if keep:
                    with zipfile.ZipFile(os.path.join(out, os.path.basename(iwd)), "w", zipfile.ZIP_STORED) as o:
                        for n in keep:
                            o.writestr(n, z.read(n))
        if os.path.isfile(os.path.join(mod_dir, "mod.arena")):
            shutil.copyfile(os.path.join(mod_dir, "mod.arena"), os.path.join(out, "mod.arena"))
        # the lobby's entry
        name = info["longname"] if info["longname"] and not re.match(r"^[A-Z0-9_]+$", info["longname"]) else folder
        # (a description only when the mod brings one: mod.json's "desc" when it is not its author or version)
        lines = ["type %s" % kind, "name %s" % name.upper(), "author %s" % info["author"]]
        if info.get("version"):
            lines.append("version %s" % info["version"])
        if info.get("description"):
            lines.append("description %s" % info["description"])
        if kind == "custommap":
            lines += ["map %s %s" % (z, name.upper() if len(map_zones) == 1 else z.upper()) for z in map_zones]
            if info["loadscreen"]:
                lines.append("image %s" % info["loadscreen"])
        else:
            lines.append("maps %s" % info["maps"])
        with open(os.path.join(out, "vita_mod.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        on = "" if kind == "custommap" else "; on %s" % ("all original maps" if info["maps"] == "original" else
                                                         ", ".join(info["maps"].split()))
        kept = [z for m, z in sorted(info["overrides"].items()) if z in zones]
        if kept:
            on += " (+ %s)" % ", ".join("%s %.1f MB" % (z, zone_virtual_mb(os.path.join(out, z + ".ff"))) for z in kept)
        how = []
        if "--adpcm-rate" in flags:
            how.append("16 kHz sounds")
        if big:
            how.append("its world in video memory and %d px textures" % BIG_MAP_TEXTURE_MAX)
        if "--drop-lods-models" in flags:
            how.append("its biggest models one LOD lower")
        elif "--drop-lods" in flags:
            how.append("all models but weapons one LOD lower")
        if scale != 1.0:
            how.append("smaller models (low-poly scale %g)" % scale)
        if "--rename-models" in flags:
            how.append("the game's own perk machines and the like instead of its copies (%d)" % len(
                read_list(os.path.join(self.work, "run_" + safe, "swap.txt"))))
        if "--character-scale" in flags:
            how.append("zombies and characters a little simpler")
        if "gentle-world" in flags:
            how.append("its weapons' world models (box, wall buys) %ssimpler, the ones in hand as they are" % (
                "gently " if "0.25" in flags else ""))
        elif "--low-poly-weapons" in flags:
            how.append("its weapons gently simpler, in hand too (their sights kept)" if "gentle" in flags else
                       "SIMPLER WEAPONS (it did not fit otherwise)")
        return "converted as a %s%s: zones %.1f MB (+ %.1f MB of video memory), %d images + %d placeholders%s, " \
               "%d streams%s%s%s" % (
                   "custom map" if kind == "custommap" else "mod", " with " + ", ".join(how) if how else "",
                   need, gpu, images, holders, " (%d missing)" % len(missing) if missing else "", len(streams), on,
            "; left out (never loaded): %s" % ", ".join(unused) if unused else "", measured + (
                "; its copies without the models nothing shows free %s" % strip_note.rstrip(", ") if strip_note
                else "") + over_note)

    def run(self):
        start = time.time()
        self.check_game()
        mods = self.find_mods()
        if self.only:  # (--only: just these mods; the base game and the original maps as always)
            mods = [m for m in mods if m[0].lower() in self.only or m[1] in self.only]
        os.makedirs(os.path.join(self.work, "logs"), exist_ok=True)
        if os.path.isdir(self.out):
            shutil.rmtree(self.out)
        os.makedirs(os.path.join(self.out, "main"))

        step("Reading the mods and custom maps (their zones read in Python, a few seconds each)")
        recorded = []
        progress(0, len(mods), "")
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            for i, (mod, ok, why) in enumerate(pool.map(self.record, mods)):
                progress(i + 1, len(mods), mod[0])
                if ok:
                    recorded.append(mod)
                    say("  mods/%s recorded" % mod[0])
                else:
                    note("mods/%s: SKIPPED: the PC run failed: %s" % (mod[0], why))
                    tell("item", "mods/" + mod[0], "skipped", "the PC run failed: " + why)

        step("Zones (the slow part: models and sounds are converted)")
        jobs = []
        for rel, _, _ in read_inputs(os.path.join(DATA, "base", "inputs.txt")):
            if rel.startswith("zone/"):
                name = os.path.basename(rel)[:-3]
                zmap = os.path.join(DATA, "base", "zonemaps", name + ".zmap")
                jobs.append((os.path.join(self.game, rel), zmap if os.path.isfile(zmap) else None,
                             os.path.join(self.out, rel)))
        for m in self.maps:
            for rel, _, _ in read_inputs(os.path.join(DATA, "maps", m, "inputs.txt")):
                name = os.path.basename(rel)[:-3]
                jobs.append((os.path.join(self.game, rel), os.path.join(DATA, "maps", m, "zonemaps", name + ".zmap"),
                             os.path.join(self.out, rel)))
        self.strip_jobs = {}
        for mod in recorded:  # (the mods' copies of game zones without the models nothing shows)
            self.strip_jobs[mod[1]] = self.plan_strip(mod)
            jobs += self.strip_jobs[mod[1]]
        for folder, safe, kind, map_zones, info in recorded:
            mod_dir = os.path.join(self.work, "run_" + safe, "mods", safe)
            for ff in os.listdir(mod_dir):
                if not ff.lower().endswith(".ff"):
                    continue
                zmap = os.path.join(self.work, "run_" + safe, "zonemap", ff[:-3] + ".zmap")
                if not os.path.isfile(zmap):
                    continue  # (the map never loaded it - a leftover such as a test map: not shipped, not counted)
                jobs.append((os.path.join(mod_dir, ff), zmap, os.path.join(self.out, "mods", safe, ff)))
        failed = self.repack_all(jobs)
        # each converted original map's zone memory (its three zones): what is left next to it for a mod
        self.map_mb = {}
        for m in self.maps:
            rels = [rel for rel, _, _ in read_inputs(os.path.join(DATA, "maps", m, "inputs.txt"))]
            if any(os.path.join(self.out, rel) in failed for rel in rels):
                note("map %s: a zone FAILED to convert (see work/logs)" % m)
            self.map_mb[m] = sum(zone_virtual_mb(os.path.join(self.out, rel)) for rel in rels
                                 if os.path.isfile(os.path.join(self.out, rel)))

        step("Textures")
        index = self.main_index
        n = self.build_archive(read_list(os.path.join(DATA, "base", "images.txt")), index,
                               os.path.join(self.out, "main", "iw_vita.iwd"))
        say("  iw_vita.iwd: %d images, %d placeholders%s" % (n[0], n[1], ", %d missing" % len(n[2]) if n[2] else ""))
        for i, m in enumerate(self.maps):
            progress(i, len(self.maps), MAP_NAMES.get(m, m))
            entries = [r for r in read_list(os.path.join(DATA, "maps", m, "images.txt")) if r[0] not in
                       self.base_images]
            if entries:
                short = m[len("zombie_"):]
                n = self.build_archive(entries, index, os.path.join(self.out, "main", "iw_vita_%s.iwd" % short))
                say("  iw_vita_%s.iwd: %d images, %d placeholders%s" % (short, n[0], n[1], ", %d missing" % len(n[2])
                                                                      if n[2] else ""))
        self.menu_art()
        lists = [read_list(os.path.join(DATA, "base", "images.txt"))] + [
            read_list(os.path.join(DATA, "maps", m, "images.txt")) for m in self.maps]
        n = build_perk_archive(index, os.path.join(self.out, "main", "iw_vitaperk.iwd"),
                               os.path.join(DATA, "perk_images.txt"))
        say("  iw_vitaperk.iwd (perk machines, bottles, Pack-a-Punch at %d): %d images" % (PERK_MAX, n))
        n = build_refl_archive(index, os.path.join(self.out, "main", "iw_vitarefl.iwd"),
                               os.path.join(DATA, "refl_images.txt"))
        say("  iw_vitarefl.iwd (weapon reflection masks): %d images" % n)
        n = self.build_buttons()
        say("  iw_vitabuttons.iwd (PS Vita buttons in the fonts and the HUD): %d images" % n)
        n = self.build_world_lights()
        say("  vitalight (HD MODE world lights): %d maps" % n)

        step("Streamed sounds (voice lines, music)")
        packs = [(read_list(os.path.join(DATA, "base", "streams.txt")), "iw_vita_snd.iwd")]
        for m in self.maps:
            rows = [r for r in read_list(os.path.join(DATA, "maps", m, "streams.txt")) if r[0].lower() not in
                    self.base_streams]
            if rows:
                packs.append((rows, "iw_vita_snd_%s.iwd" % m[len("zombie_"):]))
        with concurrent.futures.ThreadPoolExecutor(self.jobs) as pool:
            done = pool.map(lambda p: (p[1], self.pack_streams(p[0], os.path.join(self.out, "main", p[1]),
                                                               os.path.join(self.work, "logs", p[1] + ".log"))), packs)
            for i, (name, ok) in enumerate(done):
                say("  %s %s" % (name, "ok" if ok else "FAILED (see work/logs)"))
                progress(i + 1, len(packs), name)

        step("Mods and custom maps")
        for i, mod in enumerate(recorded):
            progress(i, len(recorded), mod[0])
            result = self.convert_mod(mod, failed)
            note("mods/%s: %s" % (mod[0], result))
            tell("item", "mods/" + mod[0], "skipped" if result.startswith("SKIPPED") else
                 "reduced" if " with " in result.split(":")[0] else "ok", result)

        step("The port's files")
        for root, _, files in os.walk(os.path.join(DATA, "vita")):
            for name in files:
                src = os.path.join(root, name)
                dst = os.path.join(self.out, os.path.relpath(src, os.path.join(DATA, "vita")))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
        shutil.copyfile(os.path.join(self.game, "main", "iw_00.iwd"), os.path.join(self.out, "main", "iw_00.iwd"))
        shutil.copyfile(os.path.join(self.game, "localization.txt"), os.path.join(self.out, "localization.txt"))

        total = sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(self.out) for f in files)
        summary = "Done in %d min: %s (%.0f MB)" % ((time.time() - start) / 60, self.out, total / 1048576.0)
        with open(os.path.join(self.out_root, "report.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(report + ["", summary]) + "\n")
        say("\n== Report (also in %s)" % os.path.join(self.out_root, "report.txt"))
        for line in report:
            say("  " + line)
        say("\n" + summary)
        tell("done", self.out, summary)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("game", help="the Black Ops folder (BlackOps.exe, zone, main, mods)")
    ap.add_argument("--out", default=os.path.join(HERE, "output"))
    ap.add_argument("--engine", default=None, help="the engine repository's tools instead of the converter's own")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", help="convert only these mods (folder names, comma separated; for tests: use another "
                                   "--out, the output folder is made anew)")
    ap.add_argument("--maps", help="convert only these original maps (comma separated, e.g. zombie_theater,"
                                   "zombie_coast; default: every installed one)")
    ap.add_argument("--play-check", action="store_true",
                    help="also play each mod a minute on the PC (catches script errors and crashes)")
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    game = os.path.abspath(args.game.strip().strip('"'))
    if out.lower().startswith(game.lower() + os.sep) or out.lower() == game.lower():
        fail("the output folder must not be inside the game folder")
    try:
        Converter(args).run()
    except ConvertError:
        sys.exit(1)


if __name__ == "__main__":
    main()
