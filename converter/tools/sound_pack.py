"""sound_pack.py - the streamed sounds a map plays (voice lines, music, long ambience), for the Vita.

    python -I tools/vita/sound_pack.py <stream list> <game main folder> <out.iwd> [--rate 24000] [--ffmpeg path]

The usual run, from the repository root:
    python -I tools/vita/sound_pack.py build/Release/snddump_streams.txt build/Release/main build/vita-sound/iw_vita_snd.iwd

--rate (default 24000; 0 keeps them as they are): the streams, 48 kHz MS ADPCM, re-encoded at this rate by ffmpeg
(decoded, resampled, encoded in blocks of 256 bytes a channel, the block size written to the header's block_size,
where the Vita's sound driver reads it): half the decoding for the Vita's mixer and half the size. The 2096-byte
header keeps everything else; frame count, rate and data size change, and a looping stream of more than 1 MB is
padded to a multiple of 524 KB as snd_stream.cpp (Snd_StreamLoadHeader) checks. A stream a zone primes (its first
bytes, header included, in the zone; 'primed' in the list) is copied unchanged. Cached by content (--sound-cache).

The list comes from a PC run of the map with 'bo1_snddump' on the command line (sound/snd_db.cpp SND_BO1Dump):
every streamed alias's file, such as english\\sound\\voiceovers\\zmb\\ann\\ann_nuke.wav. Streamed sounds are not in the
zones but in the game's .iwd files (the player voice lines of Kino in localized_English_iw05.iwd, the zombie music
in iw_23.iwd), 1.6 GB for the whole game: the Vita gets only the map's, stored (not compressed) as the game's are,
so the stream reads them without inflating. They are MS ADPCM already, which the Vita's sound driver plays.
Never writes next to its input.
"""
import argparse
import concurrent.futures
import hashlib
import os
import shutil
import struct
import subprocess
# (no console window for each ffmpeg: under the converter's window, pythonw, every one would open its own)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# (threads for the ffmpeg conversions: the converter runs several zones at once and divides the CPU among them)
FFMPEG_THREADS = int(os.environ.get("BO1_TOOL_THREADS") or 0) or max(2, os.cpu_count() or 2)
import sys
import tempfile
import zipfile

HEADER_SIZE = 2096
FORMAT_MSADPCM = 6
GAME_BLOCK = 262   # the game's MS ADPCM blocks: bytes a channel (512 frames)
VITA_BLOCK = 256   # ffmpeg's encoder takes powers of 2 only (500 frames)
COEFS = [(256, 0), (512, -256), (0, 0), (192, 64), (240, 0), (460, -208), (392, -232)]


def resample_stream(data, rate, ffmpeg, cache_dir):
    """a stream file (2096-byte header, MS ADPCM) at the given rate; unchanged if it is not 48 kHz-ish ADPCM"""
    version, frames, src_rate, channels, header_size = struct.unpack_from("<5I", data, 0)
    fmt = struct.unpack_from("<I", data, 28)[0]
    flags = struct.unpack_from("<I", data, 36)[0]
    data_size = struct.unpack_from("<I", data, 48)[0]
    if version != 1 or header_size != HEADER_SIZE or fmt != FORMAT_MSADPCM or channels not in (1, 2) \
            or src_rate <= rate + 500:
        return data
    key = hashlib.sha1(struct.pack("<I", rate) + data).hexdigest()
    cached = os.path.join(cache_dir, key + ".stream")
    if os.path.isfile(cached):
        return open(cached, "rb").read()
    align = GAME_BLOCK * channels
    wfx = struct.pack("<HHIIHHHHH", 2, channels, src_rate, src_rate * align // 512, align, 4, 32, 512, 7)
    wfx += b"".join(struct.pack("<hh", a, b) for a, b in COEFS)
    payload = data[HEADER_SIZE:HEADER_SIZE + data_size]
    riff = b"WAVE" + b"fmt " + struct.pack("<I", len(wfx)) + wfx + b"data" + struct.pack("<I", len(payload)) + payload
    work = tempfile.mkdtemp(prefix="bo1strm_")
    try:
        src, dst = os.path.join(work, "in.wav"), os.path.join(work, "out.wav")
        with open(src, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", len(riff)) + riff)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", src, "-ar", str(rate), "-ac", str(channels), "-c:a",
                        "adpcm_ms", "-block_size", str(VITA_BLOCK * channels), dst],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        wav = open(dst, "rb").read()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    pos, new_frames, adpcm, new_align = 12, None, None, None
    while pos + 8 <= len(wav):
        cid, size = wav[pos:pos + 4], struct.unpack_from("<I", wav, pos + 4)[0]
        body = wav[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            new_align = struct.unpack_from("<H", body, 12)[0]
        elif cid == b"fact":
            new_frames = struct.unpack_from("<I", body, 0)[0]
        elif cid == b"data":
            adpcm = body
        pos += 8 + size + (size & 1)
    if adpcm is None or new_align != VITA_BLOCK * channels:
        raise RuntimeError("ffmpeg made no MS ADPCM of %d-byte blocks" % (VITA_BLOCK * channels))
    if new_frames is None:
        new_frames = len(adpcm) // new_align * ((VITA_BLOCK - 7) * 2 + 2)
    header = bytearray(data[:HEADER_SIZE])
    struct.pack_into("<I", header, 4, new_frames)
    struct.pack_into("<I", header, 8, rate)
    struct.pack_into("<I", header, 20, VITA_BLOCK * channels)
    struct.pack_into("<I", header, 48, len(adpcm))
    out = bytes(header) + adpcm
    if (flags & 1) and len(adpcm) > 0x106000:  # snd_stream.cpp: a long looping stream fills whole buffers
        out += b"\0" * ((0x83000 - len(out) % 0x83000) % 0x83000)
    with open(cached, "wb") as f:
        f.write(out)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("streams")
    ap.add_argument("main")
    ap.add_argument("out")
    ap.add_argument("--rate", type=int, default=24000)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--sound-cache", default="build/vita-sndcache")
    # a mod's folder (mods/<name>): its .iwd files are searched before the game's, as the engine does with fs_game
    ap.add_argument("--mod-dir", action="append", default=[])
    args = ap.parse_args()
    names_path, main_dir, out = args.streams, args.main, args.out
    wanted = {}
    primed = set()
    for line in open(names_path, encoding="latin-1"):
        fields = line.rstrip("\r\n").split("\t")
        name = fields[0].strip()
        if name:
            key = name.replace("\\", "/").lower()
            wanted[key] = name
            if "primed" in fields[1:]:
                primed.add(key)
    if os.path.abspath(os.path.dirname(os.path.abspath(out))) == os.path.abspath(main_dir):
        sys.exit("refusing to write into the game's main folder")
    found = {}  # lower-case path -> (iwd, entry name)
    archives = [os.path.join(d, p) for d in args.mod_dir for p in sorted(os.listdir(d)) if p.lower().endswith(".iwd")]
    archives += [os.path.join(main_dir, p) for p in sorted(os.listdir(main_dir)) if p.lower().endswith(".iwd")]
    for iwd in archives:
        with zipfile.ZipFile(iwd) as z:
            for info in z.infolist():
                key = info.filename.replace("\\", "/").lower()
                if key in wanted and key not in found:
                    found[key] = (iwd, info.filename)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    total = before = 0
    by_iwd = {}
    for key, (iwd, entry) in found.items():
        by_iwd.setdefault(iwd, []).append(entry)
    if args.rate:
        os.makedirs(args.sound_cache, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as dst:
        for iwd, entries in sorted(by_iwd.items()):
            with zipfile.ZipFile(iwd) as src:
                datas = [(entry, src.read(entry)) for entry in entries]
            def convert(item):
                entry, data = item
                if not args.rate or entry.replace("\\", "/").lower() in primed:
                    return entry, data, data
                return entry, data, resample_stream(data, args.rate, args.ffmpeg, args.sound_cache)
            with concurrent.futures.ThreadPoolExecutor(max_workers=FFMPEG_THREADS) as pool:
                for entry, original, data in pool.map(convert, datas):
                    dst.writestr(zipfile.ZipInfo(entry), data, compress_type=zipfile.ZIP_STORED)
                    total += len(data)
                    before += len(original)
    missing = sorted(set(wanted) - set(found))
    print("%d of %d streamed sounds packed (%.1f MB, %.1f MB as the game has them; %d primed, unchanged) into %s"
          % (len(found), len(wanted), total / 1048576.0, before / 1048576.0, len(primed), out))
    for iwd, entries in sorted(by_iwd.items()):
        print("  %-30s %d" % (iwd, len(entries)))
    if missing:
        print("%d not found in any .iwd, e.g.:" % len(missing))
        for name in missing[:10]:
            print("  " + wanted[name])


if __name__ == "__main__":
    main()
