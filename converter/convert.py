"""convert.py - your Call of Duty: Black Ops (PC) game files -> the files BO1 Zombies for the PS Vita reads.

    python convert.py "<Black Ops folder>" [--out <folder>] [--ffmpeg <ffmpeg.exe>] [--map zombie_theater]

Writes <out>/bo1zombies (default: output/bo1zombies next to the converter): copy that folder to ux0:data/bo1zombies
on the Vita. It only reads the game's folder, never writes into it. Needs Python 3.8 or newer and ffmpeg (for the
sounds). "Convert Game Files.bat" runs it for you.

What it makes, for the map in data/<map>/:
  zone/      the map's zones, re-packed for the Vita (zone_repack.py with the recorded zone maps in zonemaps.zip):
             the PC's shader programs left out, the WMA sounds turned into MS ADPCM (ffmpeg)
  main/      iw_vita.iwd: the map's textures cut to 128 pixels (iwi_repack.py, the list in images.txt)
             iw_vita_snd.iwd: the map's streamed sounds (voice lines, music) at 24 kHz (sound_pack.py, streams.txt)
             iw_00.iwd (the game's configs) and playlists_sp.info
  localization.txt (the game's), cmdline.txt (how the game starts)
The zone maps only fit the game's own zones byte for byte: they are checked first (inputs.txt), and a zone that
differs (another version, a modded file) stops the conversion before anything is written.
"""
import argparse
import glob
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # (run with -I: the tools next to this file)

import iwi_repack  # noqa: E402
import zone_repack  # noqa: E402

TEXTURE_MAX = 128  # the size the tested texture archive was made at


def fail(message):
    print("\nERROR: " + message)
    sys.exit(1)


def step(text):
    print("\n== " + text)


def find_ffmpeg(given):
    """ffmpeg.exe: --ffmpeg, next to the converter, on the PATH, or where winget installs it"""
    candidates = [given] if given else []
    candidates += [os.path.join(HERE, "ffmpeg.exe"), os.path.join(HERE, "ffmpeg", "bin", "ffmpeg.exe"), "ffmpeg"]
    local = os.environ.get("LOCALAPPDATA", "")
    candidates += sorted(glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin",
                                                "ffmpeg.exe")), reverse=True)
    for c in candidates:
        path = shutil.which(c) if not os.path.isabs(c) else (c if os.path.isfile(c) else None)
        if not path:
            continue
        try:
            out = subprocess.run([path, "-hide_banner", "-h", "encoder=adpcm_ms"], capture_output=True, text=True,
                                 timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if "adpcm_ms" in out.stdout:
            return path
    fail("ffmpeg was not found. Install it (for example: winget install Gyan.FFmpeg), or put ffmpeg.exe into the "
         "converter folder, then run this again.")


def read_inputs(map_dir):
    rows = []
    for line in open(os.path.join(map_dir, "inputs.txt"), encoding="utf-8"):
        fields = line.strip().split("\t")
        if len(fields) == 3:
            rows.append((fields[0], int(fields[1]), fields[2]))
    return rows


def check_game(game, inputs):
    """the game folder holds every input file, and the zones are the ones the zone maps were recorded from"""
    if not os.path.isdir(os.path.join(game, "zone")) or not os.path.isdir(os.path.join(game, "main")):
        fail("'%s' is not the Black Ops folder: it has no 'zone' and 'main' folders. Use the folder that holds "
             "BlackOps.exe (Steam: steamapps\\common\\Call of Duty Black Ops)." % game)
    bad = []
    for rel, size, sha1 in inputs:
        path = os.path.join(game, rel)
        if not os.path.isfile(path):
            if rel.startswith("zone/English/"):
                fail("%s is missing. The converter needs the English game files (in Steam: the game's Properties > "
                     "Language > English, then let Steam update)." % rel)
            fail("%s is missing from the game folder. In Steam: the game's Properties > Installed Files > Verify "
                 "integrity of game files." % rel)
        if not rel.startswith("zone/"):
            continue  # (configs and the language file: any version works)
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if os.path.getsize(path) != size or h.hexdigest() != sha1:
            bad.append(rel)
    if bad:
        fail("these zones differ from the ones the converter knows (another game version, or modded files):\n  "
             + "\n  ".join(bad) + "\nIn Steam: Verify integrity of game files. Mods that replace zones must be removed.")


def convert_zones(game, map_dir, out, ffmpeg, cache, inputs):
    work = tempfile.mkdtemp(prefix="bo1vita_zmap_")
    try:
        with zipfile.ZipFile(os.path.join(map_dir, "zonemaps.zip")) as z:
            z.extractall(work)
        for rel, _, _ in inputs:
            if not rel.startswith("zone/"):
                continue
            sub, name = rel.split("/")[1], os.path.basename(rel)[:-3]
            src = os.path.join(game, rel)
            dst = os.path.join(out, "zone", sub.lower(), name + ".ff")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            zmap = os.path.join(work, name + ".zmap")
            if not os.path.isfile(zmap):  # (loaded before the zone recorder starts: the game's own)
                shutil.copyfile(src, dst)
                print("  copied %s" % rel)
                continue
            print("  %s" % rel)
            zone_repack.process("repack", src, zmap, dst, True, True, ffmpeg, cache)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def build_textures(game, map_dir, out_iwd):
    """iw_vita.iwd as images.txt lists it: each image cut to TEXTURE_MAX from the game's archives, or the 4x4
    placeholder iwi_repack.py writes for one the Vita does not sample (normal and specular maps, other maps' art)"""
    entries = [line.rstrip("\n").split("\t") for line in open(os.path.join(map_dir, "images.txt"), encoding="utf-8")
               if line.strip()]
    index = iwi_repack.archive_index(os.path.join(game, "main"))
    missing, open_zips, done = [], {}, 0
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
            while (data[5] & 3) and data[4] in (1, 2) and max(struct.unpack_from("<2h", data, 6)) > TEXTURE_MAX:
                data = iwi_repack.halve_uncompressed(data)
            small, _ = iwi_repack.cut(data, TEXTURE_MAX)
            archive.writestr("images/%s.iwi" % name, small)
            done += 1
            if done % 200 == 0:
                print("  %d images" % done)
    for z in open_zips.values():
        z.close()
    print("  %d images, %d placeholders -> %s (%.1f MB)" % (done, len(entries) - done - len(missing), out_iwd,
                                                            os.path.getsize(out_iwd) / 1048576.0))
    if missing:
        fail("%d images are not in the game's .iwd archives (e.g. %s). In Steam: Verify integrity of game files."
             % (len(missing), ", ".join(missing[:5])))


def main():
    ap = argparse.ArgumentParser(description="BO1 Zombies for the PS Vita: convert your Black Ops (PC) game files")
    ap.add_argument("game", help="the Black Ops folder (the one with BlackOps.exe, zone and main)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(HERE), "output"))
    ap.add_argument("--ffmpeg")
    ap.add_argument("--map", default="zombie_theater")
    args = ap.parse_args()
    if sys.version_info < (3, 8):
        fail("Python 3.8 or newer is needed (this is %s)." % sys.version.split()[0])
    game = os.path.abspath(args.game.strip().strip('"'))
    map_dir = os.path.join(HERE, "data", args.map)
    if not os.path.isdir(map_dir):
        fail("no data for the map '%s' (data folder: %s)" % (args.map, os.path.join(HERE, "data")))
    out = os.path.join(os.path.abspath(args.out), "bo1zombies")
    if os.path.abspath(out).lower().startswith(game.lower() + os.sep):
        fail("the output folder must not be inside the game folder")
    cache = os.path.join(os.path.abspath(args.out), ".cache")  # converted sounds: a second run is quick
    start = time.time()

    step("Checking the game files in %s" % game)
    inputs = read_inputs(map_dir)
    check_game(game, inputs)
    ffmpeg = find_ffmpeg(args.ffmpeg)
    print("  game files OK; ffmpeg: %s" % ffmpeg)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "main"))

    step("Zones (a few minutes: the sounds are converted)")
    convert_zones(game, map_dir, out, ffmpeg, cache, inputs)

    step("Textures")
    build_textures(game, map_dir, os.path.join(out, "main", "iw_vita.iwd"))

    step("Streamed sounds (voice lines, music)")
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "sound_pack.py"), os.path.join(map_dir, "streams.txt"),
                        os.path.join(game, "main"), os.path.join(out, "main", "iw_vita_snd.iwd"), "--ffmpeg", ffmpeg,
                        "--sound-cache", cache])
    if r.returncode:
        fail("the streamed sounds could not be packed (see above)")

    step("Configs")
    shutil.copyfile(os.path.join(game, "main", "iw_00.iwd"), os.path.join(out, "main", "iw_00.iwd"))
    shutil.copyfile(os.path.join(game, "localization.txt"), os.path.join(out, "localization.txt"))
    shutil.copyfile(os.path.join(map_dir, "playlists_sp.info"), os.path.join(out, "main", "playlists_sp.info"))
    shutil.copyfile(os.path.join(map_dir, "cmdline.txt"), os.path.join(out, "cmdline.txt"))

    total = sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(out) for f in files)
    print("\nDone in %d s: %s (%.0f MB)." % (time.time() - start, out, total / 1048576.0))
    print("Copy the folder 'bo1zombies' to ux0:data/ on the Vita (it becomes ux0:data/bo1zombies).")
    print("These files are made from your game: please do not share them.")


if __name__ == "__main__":
    main()
