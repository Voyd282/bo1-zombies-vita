// FS_MODS BASE - not part of the original game. Run by the engine after the level main only when +set bo1_mod_zones
// loaded zombie_cosmodrome.ff (Scr_LoadLevel, g_modZoneLevelScripts). Each stacked mod registers what it starts here in its
// maps\<name>\_hooks.gsc: maps\_modstack::add( "mod_zones/zombie_cosmodrome::main", <func> ) (see maps\_modenemy.gsc).

main()
{
	// L62: the space monkeys (maps\_modenemies\_monkey links Ascension's monkey AI script), taken from here as Shangri-La's
	if ( !IsDefined( level.mod_zone_enemy_init ) )
		level.mod_zone_enemy_init = [];
	level.mod_zone_enemy_init["monkey"] = maps\_modenemies\_monkey::init;
	maps\_modstack::run( "mod_zones/zombie_cosmodrome::main" );
}
