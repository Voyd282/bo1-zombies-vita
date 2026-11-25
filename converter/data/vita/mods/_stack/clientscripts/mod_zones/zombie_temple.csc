#include clientscripts\_utility;

// FS_MODS BASE - client side of the shared enemy modules (maps\_modenemies; mods/horde, mods/nightmare) that need
// Shangri-La's scripts; moved from mods/horde, unchanged. The engine runs main() after
// the level's client main only when +set bo1_mod_zones zombie_temple loaded zombie_temple.ff (CScr_LoadLevel).
// Not part of the original game.

main()
{
	if ( GetDvarInt( #"horde_enemy_napalm" ) )
	{
		level._CF_ACTOR_IS_NAPALM_ZOMBIE = 0;
		level._CF_ACTOR_NAPALM_ZOMBIE_EXPLODE = 2;
		level._CF_ACTOR_NAPALM_ZOMBIE_WET = 4;
		// zombie_temple_fx.csc:73: the napalm footstep fx (_zombiemode_ai_napalm.csc:229 sets it as the footstep override;
		// without it _footsteps.csc:187 errors on every napalm step)
		level._effect["napalm_zombie_footstep"] = LoadFX( "maps/zombie_temple/fx_ztem_napalm_zombie_ground2" );
		clientscripts\_zombiemode_ai_napalm::init_napalm_zombie();
		level thread napalm_overlay_start();
	}
	if ( GetDvarInt( #"horde_enemy_sonic" ) )
	{
		// zombie_temple.csc:47 and :102
		level._CF_ACTOR_IS_SONIC_ZOMBIE = 3;
		clientscripts\_zombiemode_ai_sonic::init_sonic_zombie();
	}
}


// mod: zombie_temple.csc:688-692 starts the flame overlay per local player once it has a dobj (single local player only)
napalm_overlay_start()
{
	waitforclient( 0 );
	players = GetLocalPlayers();
	if ( players.size == 1 )
		players[0] thread clientscripts\_zombiemode_ai_napalm::player_napalm_radius_overlay_fade();
}
