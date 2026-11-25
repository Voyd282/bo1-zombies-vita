"""converter_gui.py - the converter's window: pick the Black Ops folder, choose the maps and mods, convert.

    pythonw converter_gui.py        (or "BO1 Zombies Converter.bat")

The conversion itself is convert_all.py's (the same code the command line runs); this window lists what the game
folder has (convert_all.scan), shows each map's loading screen, and runs the converter on a worker thread with its
progress (convert_all.listener).
"""
import base64
import json
import os
import queue
import re
import struct
import sys
import threading
import traceback
import zipfile
import zlib
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import convert_all  # noqa: E402

# the tools: the converter's own copies (tools/ next to it: the release), else the engine repository's (development)
if os.path.isfile(os.path.join(HERE, "tools", "zone_repack.py")):
    ENGINE = None
    sys.path.insert(0, os.path.join(HERE, "tools"))
else:
    ENGINE = os.path.join(os.path.dirname(HERE), "bo1-zombies-decompiled")
    sys.path.insert(0, os.path.join(ENGINE, "tools", "vita"))
try:
    import iwi_repack  # noqa: E402 (the loading screens' pictures)
except ImportError:
    iwi_repack = None

SETTINGS = os.path.join(HERE, "converter_settings.json")

# ---- the look
BG = "#111316"
PANEL = "#181b20"
CARD = "#1f2329"
CARD_ON = "#262b33"
CARD_LINE = "#2c323b"
TEXT = "#e8eaed"
MUTED = "#8f98a3"
ACCENT = "#e8732c"
ACCENT_DARK = "#b85a20"
OK = "#5cb85c"
WARN = "#e0a030"
BAD = "#d9534f"
FONT = "Segoe UI"
THUMB_W, THUMB_H = 128, 72

# the converter's steps and how much of the whole each is (the overall bar)
STEPS = [("Checking", 0.04), ("Reading the mods", 0.14), ("Zones", 0.46), ("Textures", 0.12),
         ("Streamed sounds", 0.10), ("Mods and custom maps", 0.12), ("The port's files", 0.02)]


# ---- pictures: a loading screen (.iwi, DXT) as a small PNG for Tk
def png_bytes(width, height, rgb):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def iwi_thumbnail(data, out_w=THUMB_W, out_h=THUMB_H):
    """the image's largest level scaled to out_w x out_h (the middle of a square picture cut to 16:9), RGB bytes;
    None when it is not a DXT or plain RGBA image"""
    if data[:3] != b"IWi" or data[3] != 13:
        return None
    fmt, flags = data[4], data[5]
    w, h = struct.unpack_from("<2h", data, 6)
    if w <= 0 or h <= 0:
        return None
    block_bytes = {11: 8, 12: 16, 13: 16}.get(fmt)
    if block_bytes:
        size = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block_bytes
    elif fmt in (1, 2):
        size = w * h * (4 if fmt == 1 else 3)
    else:
        return None
    offset = 48 if flags & 3 else len(data) - size  # (no mip levels: right after the header, else the last level)
    if offset < 48 or offset + size > len(data):
        offset = 48
        if offset + size > len(data):
            return None
    level = data[offset:offset + size]
    # the source rectangle: the whole width, the middle rows for 16:9
    src_h = min(h, w * out_h // out_w)
    top = (h - src_h) // 2
    blocks = {}
    out = bytearray(out_w * out_h * 3)
    bw = (w + 3) // 4
    def texel(sx, sy):
        if block_bytes:
            key = (sy >> 2) * bw + (sx >> 2)
            texels = blocks.get(key)
            if texels is None:
                texels = iwi_repack._dxt_texels(fmt, level[key * block_bytes:(key + 1) * block_bytes])
                blocks[key] = texels
            return texels[(sy & 3) * 4 + (sx & 3)][:3]
        px = (sy * w + sx) * (4 if fmt == 1 else 3)  # (stored B, G, R(, A))
        return level[px + 2], level[px + 1], level[px]

    # (each pixel the mean of 4 x 4 texels spread over the area it covers: smooth, not speckled)
    for y in range(out_h):
        for x in range(out_w):
            r = g = b = 0
            for j in range(4):
                sy = top + ((y * 4 + j) * src_h + src_h // 8) // (out_h * 4)
                for k in range(4):
                    sx = ((x * 4 + k) * w + w // 8) // (out_w * 4)
                    t = texel(min(sx, w - 1), min(sy, h - 1))
                    r += t[0]
                    g += t[1]
                    b += t[2]
            i = (y * out_w + x) * 3
            out[i:i + 3] = bytes((r >> 4, g >> 4, b >> 4))
    return bytes(out)


def find_image(name, index, own_iwds=()):
    """an image's .iwi bytes: from the mod's own archives first, then the game's (index); None if not there"""
    if not name:
        return None
    member = "images/%s.iwi" % name.lower()
    for iwd in own_iwds:
        try:
            with zipfile.ZipFile(iwd) as z:
                for n in z.namelist():
                    if n.lower() == member:
                        return z.read(n)
        except (OSError, zipfile.BadZipFile):
            pass
    found = index.get(name.lower()) if index else None
    if found:
        with zipfile.ZipFile(found[0]) as z:
            return z.read(found[1])
    return None


def mod_picture_names(mod):
    """a mod's picture candidates: its loading screen material's images (a converter run's record log has them),
    the material's name, images in its archives named like a loading screen"""
    names = []
    safe = re.sub(r"[^a-z0-9_]+", "_", mod["folder"].lower()).strip("_")
    record = os.path.join(HERE, "work", "logs", "record_%s.log" % safe)
    if mod["loadscreen"] and os.path.isfile(record):
        want = mod["loadscreen"].lower()
        for line in open(record, encoding="latin-1"):
            m = re.match(r"^matstats '[^']*' (\S+)(.*)$", line.strip())
            if m and m.group(1).lower() == want:
                names += m.group(2).split()
    if mod["loadscreen"]:
        names.append(mod["loadscreen"])
    for iwd in mod["image_iwds"]:
        try:
            with zipfile.ZipFile(iwd) as z:
                for n in z.namelist():
                    low = n.lower()
                    if low.startswith("images/loadscreen_") and low.endswith(".iwi") and "locked" not in low                             and "_mp_" not in low:
                        names.append(low[len("images/"):-len(".iwi")])
        except (OSError, zipfile.BadZipFile):
            pass
    return names


# ---- the window
class Card(tk.Frame):
    """one map or mod: check box, picture, name, details, status"""

    def __init__(self, parent, app, item, title, details, enabled=True, checked=True, hint=""):
        super().__init__(parent, bg=CARD, highlightthickness=1, highlightbackground=CARD_LINE, cursor="hand2")
        self.app, self.item, self.enabled = app, item, enabled
        self.var = tk.BooleanVar(value=checked and enabled)
        self.check = tk.Label(self, bg=CARD, font=(FONT, 15), width=2)
        self.check.grid(row=0, column=0, rowspan=3, padx=(10, 4))
        self.picture = tk.Canvas(self, bg="#0b0c0e", width=THUMB_W, height=THUMB_H, highlightthickness=0, bd=0)
        self.picture.grid(row=0, column=1, rowspan=3, padx=6, pady=8)
        initials = "".join(w[0] for w in re.split(r"[\s_\-]+", title) if w[:1].isalnum())[:3].upper()
        self.picture.create_text(THUMB_W // 2, THUMB_H // 2, text=initials, fill="#3a414a", font=(FONT, 20, "bold"))
        self.title = tk.Label(self, text=title, bg=CARD, fg=TEXT if enabled else MUTED, font=(FONT, 11, "bold"),
                              anchor="w")
        self.title.grid(row=0, column=2, sticky="sw", padx=(8, 8), pady=(10, 0))
        self.details = tk.Label(self, text=details, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w", justify="left")
        self.details.grid(row=1, column=2, sticky="nw", padx=(8, 8))
        self.hint = tk.Label(self, text=hint, bg=CARD, fg=WARN, font=(FONT, 9), anchor="w", justify="left",
                             wraplength=560)
        self.hint.grid(row=2, column=2, sticky="nw", padx=(8, 8), pady=(0, 8))
        self.status = tk.Label(self, text="", bg=CARD, fg=MUTED, font=(FONT, 10, "bold"), anchor="e")
        self.status.grid(row=0, column=3, rowspan=3, sticky="e", padx=14)
        self.columnconfigure(2, weight=1)
        for w in (self, self.check, self.picture, self.title, self.details, self.hint, self.status):
            w.bind("<Button-1>", self.toggle)
            w.bind("<Enter>", lambda e: self.hover(True))
            w.bind("<Leave>", lambda e: self.hover(False))
        self.draw()

    def toggle(self, _=None):
        if self.enabled and not self.app.busy:
            self.var.set(not self.var.get())
            self.draw()
            self.app.update_summary()

    def set(self, on):
        if self.enabled:
            self.var.set(on)
            self.draw()

    def draw(self):
        if not self.enabled:
            self.check.config(text="—", fg=MUTED)
        else:
            self.check.config(text="☑" if self.var.get() else "☐", fg=ACCENT if self.var.get() else MUTED)

    def hover(self, on):
        color = CARD_ON if on and self.enabled else CARD
        for w in (self, self.check, self.title, self.details, self.hint, self.status):
            w.config(bg=color)

    def show_result(self, status, text):
        if text == "not chosen":
            self.status.config(text="")
            return
        color, mark = {"ok": (OK, "✔ converted"), "reduced": (WARN, "✔ reduced quality"),
                       "skipped": (BAD, "✖ skipped")}.get(status, (MUTED, status))
        self.status.config(text=mark, fg=color)
        if status != "ok" or self.item.get("kind"):
            self.hint.config(text=text, fg=color if status != "ok" else MUTED)


class App:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.cards = {}
        self.events = queue.Queue()
        self.scan_result = None
        self.step_index = -1
        self.pictures = []  # (the PhotoImages stay referenced)
        settings = {}
        try:
            settings = json.load(open(SETTINGS, encoding="utf-8-sig"))
        except (OSError, ValueError):
            pass
        root.title("BO1 Zombies for PS Vita - Converter")
        root.configure(bg=BG)
        root.geometry(settings.get("geometry", "980x760"))
        root.minsize(820, 560)
        self.style()

        head = tk.Frame(root, bg=BG)
        head.pack(fill="x", padx=22, pady=(18, 6))
        tk.Label(head, text="BO1 ZOMBIES", bg=BG, fg=ACCENT, font=(FONT, 20, "bold")).pack(side="left")
        tk.Label(head, text="  for PS Vita · converter", bg=BG, fg=TEXT, font=(FONT, 14)).pack(side="left",
                                                                                                    pady=(6, 0))
        tk.Label(root, text="Makes the files the Vita needs from your copy of Call of Duty: Black Ops (PC). "
                            "Your game folder is only read.", bg=BG, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x", padx=24)

        folders = tk.Frame(root, bg=PANEL)
        folders.pack(fill="x", padx=22, pady=12)
        self.game_var = tk.StringVar(value=settings.get("game", ""))
        self.out_var = tk.StringVar(value=settings.get("out", os.path.join(HERE, "output")))
        self.folder_row(folders, 0, "Black Ops folder", self.game_var, self.pick_game, "Read")
        self.folder_row(folders, 1, "Output folder", self.out_var, self.pick_out, None)
        folders.columnconfigure(1, weight=1)

        # the list
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=22)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview, style="Dark.Vertical.TScrollbar")
        self.list = tk.Frame(self.canvas, bg=BG)
        self.list_id = self.canvas.create_window((0, 0), window=self.list, anchor="nw")
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.list.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.list_id, width=e.width))
        root.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.message(self.list, "Pick your Black Ops folder (the one with BlackOps.exe) and press Read.")

        # the bottom: progress, summary, buttons
        foot = tk.Frame(root, bg=PANEL)
        foot.pack(fill="x", side="bottom", padx=0, pady=0)
        self.step_label = tk.Label(foot, text="", bg=PANEL, fg=TEXT, font=(FONT, 10, "bold"), anchor="w")
        self.step_label.grid(row=0, column=0, columnspan=4, sticky="ew", padx=22, pady=(12, 2))
        self.bar = ttk.Progressbar(foot, style="Accent.Horizontal.TProgressbar", maximum=1000)
        self.bar.grid(row=1, column=0, columnspan=4, sticky="ew", padx=22)
        self.detail_label = tk.Label(foot, text="", bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w")
        self.detail_label.grid(row=2, column=0, columnspan=4, sticky="ew", padx=22, pady=(2, 6))
        self.summary = tk.Label(foot, text="", bg=PANEL, fg=MUTED, font=(FONT, 10), anchor="w")
        self.summary.grid(row=3, column=0, sticky="w", padx=22, pady=(4, 14))
        self.log_button = self.button(foot, "Log", self.show_log, False)
        self.log_button.grid(row=3, column=1, padx=4, pady=(4, 14))
        self.open_button = self.button(foot, "Open output folder", self.open_output, False)
        self.open_button.grid(row=3, column=2, padx=4, pady=(4, 14))
        self.go = self.button(foot, "CONVERT", self.convert, True)
        self.go.grid(row=3, column=3, padx=(4, 22), pady=(4, 14))
        foot.columnconfigure(0, weight=1)
        self.log_lines = []
        self.log_window = None
        self.set_busy(False)
        self.update_summary()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.pump)
        if self.game_var.get():
            root.after(200, self.read_folder)
        if os.environ.get("BO1CONV_SCROLL"):  # (tests: where the list starts)
            root.after(4000, lambda: self.canvas.yview_moveto(float(os.environ["BO1CONV_SCROLL"])))

    # -- widgets
    def style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Accent.Horizontal.TProgressbar", troughcolor="#2a2f36", background=ACCENT, bordercolor=PANEL,
                    lightcolor=ACCENT, darkcolor=ACCENT_DARK, thickness=10)
        s.configure("Dark.Vertical.TScrollbar", troughcolor=BG, background="#2a2f36", bordercolor=BG,
                    arrowcolor=MUTED, lightcolor="#2a2f36", darkcolor="#2a2f36")

    def button(self, parent, text, command, accent):
        b = tk.Label(parent, text=text, bg=ACCENT if accent else "#2a2f36", fg="white" if accent else TEXT,
                     font=(FONT, 10, "bold") if accent else (FONT, 10), padx=18 if accent else 12, pady=7,
                     cursor="hand2")
        b.command = command
        b.accent = accent
        b.enabled = True
        b.bind("<Button-1>", lambda e: b.enabled and command())
        b.bind("<Enter>", lambda e: b.enabled and b.config(bg=ACCENT_DARK if accent else "#353b44"))
        b.bind("<Leave>", lambda e: self.paint_button(b))
        return b

    def paint_button(self, b):
        if not b.enabled:
            b.config(bg="#23272d", fg="#5c636b")
        else:
            b.config(bg=ACCENT if b.accent else "#2a2f36", fg="white" if b.accent else TEXT)

    def enable(self, b, on):
        b.enabled = on
        self.paint_button(b)

    def folder_row(self, parent, row, label, var, pick, action):
        tk.Label(parent, text=label, bg=PANEL, fg=MUTED, font=(FONT, 9), width=16, anchor="w").grid(
            row=row, column=0, padx=(14, 4), pady=(10 if row == 0 else 4, 10 if row else 4), sticky="w")
        e = tk.Entry(parent, textvariable=var, bg="#0f1114", fg=TEXT, insertbackground=TEXT, relief="flat",
                     font=(FONT, 10), highlightthickness=1, highlightbackground=CARD_LINE, highlightcolor=ACCENT)
        e.grid(row=row, column=1, sticky="ew", ipady=5, pady=(10 if row == 0 else 4, 10 if row else 4))
        b = self.button(parent, "Browse…", pick, False)
        b.grid(row=row, column=2, padx=(8, 4))
        if action:
            r = self.button(parent, action, self.read_folder, True)
            r.grid(row=row, column=3, padx=(4, 14))
            self.read_button = r
            e.bind("<Return>", lambda ev: self.read_folder())

    def message(self, parent, text, color=MUTED):
        for w in parent.winfo_children():
            w.destroy()
        tk.Label(parent, text=text, bg=BG, fg=color, font=(FONT, 11), pady=60, wraplength=700).pack(fill="x")

    def section(self, text, keys):
        row = tk.Frame(self.list, bg=BG)
        row.pack(fill="x", pady=(16, 6))
        tk.Label(row, text=text, bg=BG, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left")
        for label, on in (("none", False), ("all", True)):
            link = tk.Label(row, text=label, bg=BG, fg=ACCENT, font=(FONT, 9, "underline"), cursor="hand2")
            link.pack(side="right", padx=6)
            link.bind("<Button-1>", lambda e, on=on: self.select(keys, on))

    def select(self, keys, on):
        if self.busy:
            return
        for k in keys:
            self.cards[k].set(on)
        self.update_summary()

    # -- folders
    def pick_game(self):
        d = filedialog.askdirectory(title="Your Black Ops folder (with BlackOps.exe)",
                                    initialdir=self.game_var.get() or None)
        if d:
            self.game_var.set(os.path.normpath(d))
            self.read_folder()

    def pick_out(self):
        d = filedialog.askdirectory(title="Where the converted files go", initialdir=self.out_var.get() or None)
        if d:
            self.out_var.set(os.path.normpath(d))

    def read_folder(self):
        if self.busy:
            return
        game = self.game_var.get().strip().strip('"')
        if not game:
            return
        self.message(self.list, "Reading %s …" % game)
        self.cards = {}
        self.set_busy(True, reading=True)
        threading.Thread(target=self.scan_worker, args=(game,), daemon=True).start()

    def scan_worker(self, game):
        try:
            result = convert_all.scan(game)
        except Exception as e:  # (shown, not fatal)
            result = {"ok": False, "why": "Could not read the folder: %s" % e, "maps": [], "mods": []}
        self.events.put(("scanned", result))
        if result.get("ok") and iwi_repack:
            try:
                index = iwi_repack.archive_index(os.path.join(result["game"], "main"))
            except Exception:
                index = {}
            for m in result["maps"]:
                self.picture_for(m["key"], m["loadscreen"], index, ())
            for m in result["mods"]:
                self.picture_for(m["key"], mod_picture_names(m), index, m["image_iwds"])

    def picture_for(self, key, names, index, own):
        for name in [names] if isinstance(names, str) else names:
            try:
                data = find_image(name, index, own)
                rgb = iwi_thumbnail(data) if data else None
                if rgb:
                    self.events.put(("picture", key, png_bytes(THUMB_W, THUMB_H, rgb)))
                    return
            except Exception:
                pass

    def show_scan(self, result):
        self.scan_result = result
        for w in self.list.winfo_children():
            w.destroy()
        if not result["ok"]:
            self.message(self.list, result["why"], BAD)
            self.update_summary()
            return
        maps = result["maps"]
        self.section("ORIGINAL MAPS", [m["key"] for m in maps if m["installed"]])
        for m in maps:
            details = "%s · %.0f MB on the PC" % (m["pack"], m["size_mb"]) if m["installed"] else m["pack"]
            hint = "" if m["installed"] else ("Not installed" if m["why"] == "not installed" else
                                              "A different version of this map's files - it cannot be converted")
            card = Card(self.list, self, m, m["name"], details, enabled=m["installed"], hint=hint)
            card.pack(fill="x", pady=3)
            self.cards[m["key"]] = card
        mods = result["mods"]
        if mods:
            self.section("CUSTOM MAPS AND MODS  (mods/ in your game folder)", [m["key"] for m in mods])
        names = {m["key"]: m["name"] for m in maps}
        for m in mods:
            kind = "Custom map" if m["kind"] == "custommap" else "Mod"
            details = "%s · %.0f MB on the PC" % (kind, m["size_mb"])
            if m["author"]:
                details += " · by " + m["author"]
            if m["kind"] == "mod":
                details += " · for " + (", ".join(names.get(k, k) for k in m["maps"]) if m["maps"] else
                                             "the original maps")
            hint = "Has videos: the Vita does not show them yet." if m["video"] else ""
            title = m["name"].replace("_", " ") if m["name"] == m["folder"] else m["name"]
            card = Card(self.list, self, m, title, details, hint=hint)
            card.pack(fill="x", pady=3)
            self.cards[m["key"]] = card
        self.update_summary()
        pick = os.environ.get("BO1CONV_TEST_PICK")  # (tests: choose these keys and convert at once)
        if pick:
            keys = pick.split(",")
            for k, c in self.cards.items():
                c.set(k in keys)
            self.update_summary()
            self.root.after(500, lambda: self.convert(ask=False))

    def update_summary(self):
        if not self.scan_result or not self.scan_result.get("ok"):
            self.summary.config(text="")
            self.enable(self.go, False)
            return
        chosen = [c for c in self.cards.values() if c.enabled and c.var.get()]
        size = sum(c.item["size_mb"] for c in chosen) / 1024.0
        maps = sum(1 for c in chosen if "kind" not in c.item)
        mods = len(chosen) - maps
        self.summary.config(text="%d maps and %d mods chosen · %.1f GB of game files to convert" % (
            maps, mods, size))
        self.enable(self.go, not self.busy)

    # -- converting
    def convert(self, ask=True):
        if self.busy or not self.scan_result or not self.scan_result.get("ok"):
            return
        game = self.scan_result["game"]
        out = os.path.abspath(self.out_var.get().strip().strip('"') or os.path.join(HERE, "output"))
        if out.lower() == game.lower() or out.lower().startswith(game.lower() + os.sep):
            messagebox.showerror("Output folder", "The output folder must not be inside the game folder.")
            return
        maps = [k for k, c in self.cards.items() if "kind" not in c.item and c.enabled and c.var.get()]
        mods = [c.item["folder"] for c in self.cards.values() if "kind" in c.item and c.var.get()]
        if not maps and not mods:
            messagebox.showinfo("Nothing chosen", "Choose at least one map or mod.")
            return
        target = os.path.join(out, "bo1zombies")
        if ask and os.path.isdir(target) and not messagebox.askyesno(
                "Output folder", "%s already exists and will be made anew.\n\nContinue?" % target):
            return
        self.save_settings()
        for c in self.cards.values():
            c.status.config(text="")
        self.log_lines = []
        self.step_index = -1
        self.set_busy(True)
        args = type("Args", (), {})()
        args.game, args.out, args.engine, args.jobs, args.play_check = game, out, ENGINE, 4, False
        args.maps = ",".join(maps)
        args.only = ",".join(mods) if mods else "\x01none"  # (a name no folder has: no mods)
        threading.Thread(target=self.convert_worker, args=(args,), daemon=True).start()

    def convert_worker(self, args):
        convert_all.report.clear()
        convert_all.listener = lambda *e: self.events.put(("conv",) + e)
        try:
            convert_all.Converter(args).run()
            self.events.put(("finished", True, ""))
        except convert_all.ConvertError as e:
            self.events.put(("finished", False, str(e)))
        except Exception as e:
            self.events.put(("say", traceback.format_exc()))
            self.events.put(("finished", False, "Unexpected error: %s" % e))
        finally:
            convert_all.listener = None

    def pump(self):
        try:
            while True:
                e = self.events.get_nowait()
                self.handle(e)
        except queue.Empty:
            pass
        self.root.after(80, self.pump)

    def handle(self, e):
        kind = e[0]
        if kind == "scanned":
            self.set_busy(False)
            self.show_scan(e[1])
        elif kind == "picture":
            card = self.cards.get(e[1])
            if card:
                img = tk.PhotoImage(data=base64.b64encode(e[2]).decode(), format="png")
                self.pictures.append(img)
                card.picture.delete("all")
                card.picture.create_image(0, 0, image=img, anchor="nw")
        elif kind == "say":
            self.log(e[1])
        elif kind == "finished":
            self.set_busy(False)
            ok, why = e[1], e[2]
            if ok:
                self.bar["value"] = 1000
                self.step_label.config(text="Done", fg=OK)
                self.enable(self.open_button, True)
            else:
                self.step_label.config(text="Stopped: " + why, fg=BAD)
        elif kind == "conv":
            self.converter_event(e[1:])

    def converter_event(self, e):
        what = e[0]
        if what == "say":
            self.log(e[1])
        elif what == "step":
            for i, (prefix, _) in enumerate(STEPS):
                if e[1].startswith(prefix):
                    self.step_index = i
            self.step_label.config(text=e[1].split(" (")[0], fg=TEXT)
            self.set_overall(0.0)
        elif what == "progress":
            done, total, text = e[1], e[2], e[3]
            self.set_overall(done / float(total) if total else 1.0)
            self.detail_label.config(text=("%d of %d · %s" % (done, total, text)) if total else text)
        elif what == "item":
            key, status, text = e[1], e[2], e[3]
            card = self.cards.get(key)
            if card:
                card.show_result(status, text)
        elif what == "done":
            self.detail_label.config(text=e[2])

    def set_overall(self, fraction):
        if self.step_index < 0:
            self.bar["value"] = 0
            return
        before = sum(w for _, w in STEPS[:self.step_index])
        self.bar["value"] = 1000 * (before + STEPS[self.step_index][1] * min(1.0, fraction))

    def set_busy(self, busy, reading=False):
        self.busy = busy
        self.enable(self.go, not busy and bool(self.scan_result and self.scan_result.get("ok")))
        if hasattr(self, "read_button"):
            self.enable(self.read_button, not busy)
        self.enable(self.open_button, not busy and os.path.isdir(os.path.join(self.out_var.get(), "bo1zombies")))
        self.enable(self.log_button, bool(self.log_lines) or busy)
        if busy and not reading:
            self.step_label.config(text="Starting…", fg=TEXT)
            self.detail_label.config(text="")
            self.bar["value"] = 0

    def log(self, text):
        self.log_lines.append(text)
        self.enable(self.log_button, True)
        if self.log_window and self.log_window.winfo_exists():
            self.log_text.config(state="normal")
            self.log_text.insert("end", text + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")

    def show_log(self):
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.lift()
            return
        w = tk.Toplevel(self.root, bg=BG)
        w.title("Converter log")
        w.geometry("900x520")
        t = tk.Text(w, bg="#0d0f12", fg=TEXT, insertbackground=TEXT, font=("Consolas", 9), relief="flat",
                    wrap="none")
        t.pack(fill="both", expand=True, padx=8, pady=8)
        t.insert("end", "\n".join(self.log_lines) + "\n")
        t.see("end")
        t.config(state="disabled")
        self.log_window, self.log_text = w, t

    def open_output(self):
        path = os.path.join(self.out_var.get(), "bo1zombies")
        if os.path.isdir(path):
            os.startfile(path)

    def save_settings(self):
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump({"game": self.game_var.get(), "out": self.out_var.get(),
                           "geometry": self.root.geometry()}, f)
        except OSError:
            pass

    def close(self):
        if self.busy and not messagebox.askyesno("Converting", "The conversion is still running. Quit anyway?"):
            return
        self.save_settings()
        self.root.destroy()


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)  # (sharp text on scaled screens)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
