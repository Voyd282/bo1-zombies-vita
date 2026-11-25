// ZINFO MOD - not part of the original game. With +set fs_mods "... zinfo" (mods/_stack/maps/_modstack.gsc): the Tab
// zombie counter on every map, as mods/zinfo/maps/_zombiemode_ffotd.gsc does with +set fs_game mods/zinfo.

register()
{
	maps\_modstack::add( "_zombiemode_ffotd::main_end", ::main_end );
}

main_end()
{
	maps\zinfo\_zinfo::main( "1" ); // dvar zinfo 0/1, default on
}
