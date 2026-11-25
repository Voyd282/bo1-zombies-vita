# Release notes

## 1.0

- **All zombies maps**: Kino der Toten and "Five", the DLC maps Ascension, Call of the Dead, Shangri-La and Moon, and
  the World at War maps Nacht der Untoten, Verrückt, Shi No Numa and Der Riese (the DLC maps when you own them).
- **Mods and custom maps** from your PC's `mods` folder, measured against the Vita's memory and made smaller where
  needed; **modifiers** (Horde, No Perks, Sandbox, Zinfo) that combine with any map.
- **New converter with a window** (`BO1 Zombies Converter.bat`): pick the maps and mods, press Convert. It reads the
  game's files itself - no game or other program is run.
- **New main menu and lobby**: map / mod / modifier selection, map pictures, the Vita account name in the game.
- **HD MODE**: world lit by its lamps, spotlights and the sun, lit models, shadows - as on the PC.
- **Settings** on five pages (Graphics, HD, Controls, Sound, Dev); gyro aiming, touch options, button layouts.
- Weapon reflections as on the PC (WEAPON SHINE), the maps' skies, fog and color filters, particle clouds.
- The dog rounds' fog only during a dog round (Nacht der Untoten's own light fog no longer costs frames all game).
- Bushes drawn whole up close (the converter keeps a foliage model's full detail when its next level is a flat card).
- Deadshot Daiquiri aims at the head as on the consoles; the Gersch device's black hole sits on the device; Moon's
  helmet overlay stays under the HUD.
- PS Vita buttons in the game's prompts and the HUD (Xelu's controller prompts, CC0).
- The port's own cover: icon, LiveArea, start screen, loading picture.
- Many fixes and speed-ups since 0.1.

## 0.1 - first public test

- Kino der Toten, English, from your own PC copy of Black Ops (`Convert Game Files.bat`).
- Native PS Vita renderer: world with its lightmaps, models, effects, HUD and menus.
- Sound: loaded sounds and streamed voice lines / music.
- Performance work so far: the world's geometry copied into fast GPU memory, the GPU fed while the frame is still being
  recorded, sprites that would fill the screen in explosions left out, half the particles in bursts, 32 voices.
- Console aim assist (slowdown, lock on, aim down sights snap), `+set aim_vita_assist 0` turns it off.
- Kino's own loading screen art (still image; the PC's animated loading video is not played).
- Weapons: their detail textures (wood, gunmetal), and an approximate metal shine from their specular maps (off by
  default: Settings > Graphics > Weapon shine).
- Settings > Graphics holds the Vita's settings: FPS limit, resolution, render distance, model detail, lighting,
  weapon detail and shine, FPS counter, aim assist. They are saved in `settings.txt`. The other settings pages are
  left out for now.
- The menus move with the d-pad (and the left stick); Start goes back.
- Not yet: other maps and languages, dynamic shadows, lighting on models.

Please report problems with `ux0:data/bo1zombies/bo1_vita.log` attached.
