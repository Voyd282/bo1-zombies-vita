# BO1 Zombies for PS Vita

Call of Duty: Black Ops (2010) Zombies running natively on a hacked PS Vita. **First public test: Kino der Toten only.**

This repository contains **no game files**. You build the files the Vita needs yourself, on your own PC, from your own
copy of Call of Duty: Black Ops for PC. The VPK in the releases is only the program.

## What you need

**On the PC**
- Call of Duty: Black Ops for PC (Steam), installed, **English** game language
- [Python 3.8 or newer](https://www.python.org/downloads/) (tick *Add python.exe to PATH* when installing)
- [ffmpeg](https://ffmpeg.org/download.html), for example `winget install Gyan.FFmpeg`, or put `ffmpeg.exe` into the
  `converter` folder
- About 250 MB of free disk space

**On the Vita**
- A hacked PS Vita / PS TV (HENkaku, h-encore or Ensō) with VitaShell
- *Unsafe homebrew* enabled: Settings > HENkaku Settings > Enable Unsafe Homebrew
- `ur0:data/libshacccg.suprx` (the shader compiler; extract it once with
  [ShaRKF00D](https://github.com/Rinnegatamante/ShaRKF00D) if you don't have it yet)
- About 220 MB free on ux0

## Install

1. Download `bo1zombies.vpk` and the **source code (zip)** of the same release from the
   [Releases](../../releases) page, and unpack the zip. The converter and the VPK belong together: always use the
   ones from the same release.
2. Run **`Convert Game Files.bat`** and drag your Black Ops folder into the window (the folder with `BlackOps.exe`;
   on Steam usually `C:\Program Files (x86)\Steam\steamapps\common\Call of Duty Black Ops`). It checks your game
   files, then writes `output\bo1zombies`. It only reads the game folder.
3. Copy the folder `output\bo1zombies` to `ux0:data/` on the Vita, so you have `ux0:data/bo1zombies/`.
4. Install `bo1zombies.vpk` with VitaShell.
5. Start **BO1 Zombies** from the LiveArea.

The first start takes longer: the Vita compiles the game's shaders once and keeps them in
`ux0:data/bo1zombies/shaders/`.

**Please don't share the converted files** (`output/bo1zombies`): they contain Activision's game data.

## Controls

| Vita | Action |
| --- | --- |
| Left stick / right stick | Move / look |
| R / L | Fire / aim down sights |
| Cross | Jump |
| Circle | Crouch / prone |
| Square | Use / reload |
| Triangle | Switch weapon |
| D-pad | Action slots |
| Start / Select | Menu / scores |
| Rear touch, left half | Sprint |
| Rear touch, right half | Melee |
| Front touch, right edge | Grenade |
| Front touch, left edge | Special grenade (monkey bomb, ...) |

In menus: d-pad (or left stick) to move, Cross to select, Circle or Start to go back.

## Settings

In the game: Start > Settings > Graphics. The PC's graphics options are replaced by the Vita's own, so you can try
what runs best on your Vita (the other settings pages, sound, controls and so on, are not there for now):

| Setting | Values | What it does |
| --- | --- | --- |
| FPS limit | 20, 24, 30, 60 | 30 gives steadier frame pacing than an uneven 60 |
| Resolution (restart) | 960x544, 720x408, 640x368 | Fewer pixels for the GPU; applies the next time the game starts |
| Render distance | Full, far, medium, near | Nothing farther away is drawn |
| Model detail | High, medium, low, lowest | When models switch to their simpler versions |
| Lighting | Quality, fast | Fast does the world's lighting at lower precision |
| Weapon detail | On, off | The weapons' wood and metal detail textures |
| Weapon shine | Off, low, medium, high | The weapons' metal reflection (off by default) |
| Show FPS | On, off | A small frame counter at the top left |
| Aim assist | On, off | Aim slowdown, lock-on and the aim-down-sights snap, as on the consoles |
| Bullet impacts, number of corpses, field of view | | As on the PC |

Settings take effect at once, except resolution. They are saved in `ux0:data/bo1zombies/settings.txt` when you
leave the menu; deleting `settings.txt` puts them all back.

`ux0:data/bo1zombies/cmdline.txt` is how the game starts; `settings.txt` wins over the values in it.

## Known issues

- Only Kino der Toten, only English.
- The frame rate is roughly 30-50 fps, lower with many zombies on screen; big explosions can still cause short drops.
- Simplified graphics compared to the PC: no dynamic shadows, flat lighting on models, textures at reduced size.
- The sky is not drawn yet (it stays black).
- Up to 32 sounds play at once.

## Reporting a problem

Please open an issue with what happened and attach `ux0:data/bo1zombies/bo1_vita.log` (written every time the game
runs; copy it right after the problem, the next start overwrites it).

## Legal

This is an unofficial fan project, not affiliated with or endorsed by Activision, Treyarch or Sony. Call of Duty and
Black Ops are trademarks of Activision Publishing, Inc. You need to own the PC game; see [NOTICE.md](NOTICE.md).
