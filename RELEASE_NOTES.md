# Release notes

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
