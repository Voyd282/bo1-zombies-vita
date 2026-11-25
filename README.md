# BO1 Zombies for PS Vita

Call of Duty: Black Ops (2010) Zombies running natively on a hacked PS Vita: every zombies map of the PC game, plus
mods and custom maps from your own PC install.

This repository contains **no game files**. You build the files the Vita needs yourself, on your own PC, from your own
copy of Call of Duty: Black Ops for PC. The VPK in the releases is only the program.

## What you need

**On the PC (Windows)**
- Call of Duty: Black Ops for PC (Steam), installed, **English** game language. The DLC maps (Ascension, Call of the
  Dead, Shangri-La, Moon, the four World at War maps) are converted when you own and have installed them.
- [Python 3.8 or newer](https://www.python.org/downloads/) with Tkinter (the python.org installer has it; tick
  *Add python.exe to PATH*)
- [ffmpeg](https://ffmpeg.org/download.html), for example `winget install Gyan.FFmpeg` (it converts the sounds)
- Free disk space: about 1 GB for Kino der Toten alone, up to about 6 GB for every map and many mods (the result and
  the converter's working files)

**On the Vita**
- A hacked PS Vita / PS TV (HENkaku, h-encore or Ensō) with VitaShell
- *Unsafe homebrew* enabled: Settings > HENkaku Settings > Enable Unsafe Homebrew
- `ur0:data/libshacccg.suprx` (the shader compiler; extract it once with
  [ShaRKF00D](https://github.com/Rinnegatamante/ShaRKF00D) if you don't have it yet)
- Free space on ux0: about 350 MB for Kino der Toten alone, about 2.7 GB for every map and many mods

## Install

1. Download `bo1zombies.vpk` and the **source code (zip)** of the same release from the
   [Releases](../../releases) page, and unpack the zip. The converter and the VPK belong together: always use the
   ones from the same release.
2. Run **`BO1 Zombies Converter.bat`**. Pick your Black Ops folder (the folder with `BlackOps.exe`; on Steam usually
   `C:\Program Files (x86)\Steam\steamapps\common\Call of Duty Black Ops`). The converter lists the maps it found and
   the mods and custom maps in the game's `mods` folder; tick what you want and press **Convert**. It only reads the
   game folder - no game or other program is started.
3. When it is done, copy the folder `converter\output\bo1zombies` to `ux0:data/` on the Vita, so you have
   `ux0:data/bo1zombies/`. `converter\output\report.txt` lists what was converted and how.
4. Install `bo1zombies.vpk` with VitaShell.
5. Start **BO1 Zombies** from the LiveArea.

The first start, and the first start of each map, takes longer: the Vita compiles the game's shaders once and keeps
them in `ux0:data/bo1zombies/shaders.pak`. The same happens once when you switch HD MODE on.

To add maps or mods later, convert again and copy the new folders over (the converter keeps its working files in
`converter\work`, so a second run is much faster).

**Please don't share the converted files** (`converter/output/bo1zombies`): they contain Activision's game data.

## The main menu

- **Play**: pick a map (original maps and your custom maps), a mod for the original maps, and modifiers (Horde,
  No Perks, Sandbox, Zinfo; they can be combined). A mod made for some maps only greys the others out.
- **Settings**: five pages, switched with L / R.
- **HD MODE**: lit world, lit models and shadows, as on the PC - nicer, slower; the game restarts to switch it.

## Controls

| Vita | Action |
| --- | --- |
| Left stick / right stick | Move / look |
| R / L | Fire / aim down sights |
| Cross | Jump |
| Circle | Crouch (hold: prone) - or melee with the TACTICAL layout |
| Square | Use / reload |
| Triangle | Switch weapon |
| D-pad | Action slots |
| Start / Select | Menu / scores |
| Rear touch, left half | Sprint |
| Rear touch, right half | Melee |
| Front touch, right edge | Grenade |
| Front touch, left edge | Special grenade (monkey bomb, ...) |
| Gyro | Aiming (Settings > Controls) |

In menus: d-pad (or left stick) to move, Cross to select, Circle or Start to go back. The game shows the Vita's own
buttons.

## Settings

| Page | Settings |
| --- | --- |
| Graphics | FPS limit, resolution (restart), render distance, field of view, show FPS, model detail, weapon shine, color filters, snow and dust |
| HD (HD MODE only) | World lights, model lights, shadows |
| Controls | Button layout, look / aim sensitivity, invert look, aim assist, gyro aiming and sensitivity, rear touch, front touch, auto sprint |
| Sound | Master, music, effects and voice volume |
| Dev | Index buffer, debug display (frame rate, timings, memory) |

Each setting explains itself at the bottom of the page. They are saved in `ux0:data/bo1zombies/settings.txt` when you
leave the page; deleting `settings.txt` (or RESET TO DEFAULTS) puts them all back.

## Mods and custom maps

Put them into the game's `mods` folder on the PC as for the PC game, then convert. Each one is measured against the
Vita's memory: if it does not fit as it is, the converter makes it smaller step by step (16 kHz sounds, the world in
video memory, simpler models; weapons last) and reports what it did. One that does not fit even then is left out.
Mods that need a game_mod / LinkerMod feature the port does not have, or scripts that only work on the PC, may not
work.

## Known issues

- English only. The loading videos are not played (a picture is shown).
- Frame rates are roughly 25-50 fps depending on the map and the fight; HD MODE costs some more.
- Big custom maps and mods are made simpler to fit the Vita's memory.
- Dead Ops Arcade is not included.

## Reporting a problem

Please open an issue with what happened and attach `ux0:data/bo1zombies/bo1_vita.log` (written every time the game
runs; copy it right after the problem, the next start overwrites it) and, for a conversion problem,
`converter\output\report.txt`.

## Legal

This is an unofficial fan project, not affiliated with or endorsed by Activision, Treyarch or Sony. Call of Duty and
Black Ops are trademarks of Activision Publishing, Inc. You need to own the PC game; see [NOTICE.md](NOTICE.md).
