// FS_MODS BASE - not part of the original game. mods/_stack goes on top of the search path only with +set fs_mods
// (src/universal/com_files.cpp FS_Startup); without fs_mods nothing here is read and the game is retail.
//
// Several mods at once: +set fs_mods "mapkit horde zinfo" puts mods/mapkit, mods/horde, mods/zinfo on the search path in
// that order (a later mod's plain file wins over an earlier one's). The retail "fix for the day" hooks that every mod
// wants to ship (maps\zombie_pentagon_ffotd.gsc, maps\_zombiemode_ffotd.gsc) would shadow each other, so mods/_stack ships
// them (retail body + one run() line) and each mod registers what it wants run there instead, in its own file:
//
//   mods/<name>/maps/<name>/_hooks.gsc
//   register()
//   {
//       maps\_modstack::add( "zombie_pentagon_ffotd::main_start", maps\<name>\_<name>::main_start );
//   }
//
// Hook names: "<ffotd file>::main_start" / "::main_end" of zombie_pentagon_ffotd and _zombiemode_ffotd. run() starts every
// registered function as a level thread, in fs_mods order: it runs at once up to its first wait (same frame, same order as a
// direct call), and a hook that waits does not hold back the next mod's. A mod without _hooks.gsc only adds files.
// maps\_modstack_list.gsc is not a file: the engine writes it from fs_mods (Scr_ReadFile, src/clientscript/cscr_parser.cpp).

add( hook, func )
{
	if ( !IsDefined( level._modstack_hooks[hook] ) )
	{
		level._modstack_hooks[hook] = [];
	}
	level._modstack_hooks[hook][level._modstack_hooks[hook].size] = func;
}

run( hook )
{
	if ( !IsDefined( level._modstack_hooks ) )
	{
		level._modstack_hooks = [];
		maps\_modstack_list::register_all();
	}
	funcs = level._modstack_hooks[hook];
	if ( !IsDefined( funcs ) )
	{
		return;
	}
	for ( i = 0; i < funcs.size; i++ )
	{
		level thread [[ funcs[i] ]]();
	}
}
