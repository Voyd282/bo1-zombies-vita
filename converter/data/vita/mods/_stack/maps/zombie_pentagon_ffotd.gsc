// FS_MODS BASE - not part of the original game. mods/_stack goes on top of the search path only with +set fs_mods.
// Retail maps\zombie_pentagon_ffotd.gsc is this file with both functions empty: Five's own "fix for the day" hook, called
// at the start and at the end of maps\zombie_pentagon::main() (and by mods/mapkit's maps\mapkit\mapkit_main.gsc).
// Here it runs every stacked mod's registered hook (maps\_modstack.gsc).

main_start()
{
	maps\_modstack::run( "zombie_pentagon_ffotd::main_start" );
}

main_end()
{
	maps\_modstack::run( "zombie_pentagon_ffotd::main_end" );
}
