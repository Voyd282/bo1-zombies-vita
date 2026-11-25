// SANDBOX MOD - with +set fs_mods "... sandbox" (mods/_stack/maps/_modstack.gsc): the same calls as its
// maps/zombie_pentagon_ffotd.gsc makes with +set fs_game mods/sandbox.

register()
{
	maps\_modstack::add( "zombie_pentagon_ffotd::main_start", maps\sandbox\_sandbox::main_start );
	maps\_modstack::add( "zombie_pentagon_ffotd::main_end", maps\sandbox\_sandbox::main_end );
}
