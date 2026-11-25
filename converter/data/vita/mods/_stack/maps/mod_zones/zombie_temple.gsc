// FS_MODS BASE - not part of the original game. Run by the engine after the level main only when +set bo1_mod_zones
// loaded zombie_temple.ff (Scr_LoadLevel, g_modZoneLevelScripts). Each stacked mod registers what it starts here in its
// maps\<name>\_hooks.gsc: maps\_modstack::add( "mod_zones/zombie_temple::main", <func> ) (see maps\_modenemy.gsc).

main()
{
	// L60: the enemy modules link Shangri-La's AI scripts, so only this file (compiled only with zombie_temple loaded)
	// names them; a mod's hook takes them from here (a link in its _hooks.gsc fails to compile without the zone)
	if ( !IsDefined( level.mod_zone_enemy_init ) )
		level.mod_zone_enemy_init = [];
	level.mod_zone_enemy_init["napalm"] = maps\_modenemies\_napalm::init;
	level.mod_zone_enemy_init["sonic"] = maps\_modenemies\_sonic::init;
	maps\_modstack::run( "mod_zones/zombie_temple::main" );
}
