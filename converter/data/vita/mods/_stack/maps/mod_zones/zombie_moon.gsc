// FS_MODS BASE - not part of the original game. Run by the engine after the level main only when +set bo1_mod_zones
// loaded zombie_moon.ff (Scr_LoadLevel, g_modZoneLevelScripts). Each stacked mod registers what it starts here in its
// maps\<name>\_hooks.gsc: maps\_modstack::add( "mod_zones/zombie_moon::main", <func> ) (see maps\_modenemy.gsc).

main()
{
	maps\_modstack::run( "mod_zones/zombie_moon::main" );
}
