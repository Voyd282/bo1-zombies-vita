// NOPERKS MOD - not part of the original game. With +set fs_mods "... noperks" +set noperks 1: no perk machines
// (maps\noperks\_noperks.gsc; the engine removes their clip, src/qcommon/cm_noperks.cpp). mods/_stack/maps/_modstack.gsc.

register()
{
	maps\_modstack::add( "_zombiemode_ffotd::main_start", maps\noperks\_noperks::main_start ); // before _zombiemode_perks::init
	maps\_modstack::add( "_zombiemode_ffotd::main_end", maps\noperks\_noperks::main_end ); // right after it (same frame)
}
