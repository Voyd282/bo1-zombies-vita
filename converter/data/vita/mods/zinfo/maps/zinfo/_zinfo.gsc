#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

// ZINFO MOD - not part of the original game. Loaded only by mods (mods/zinfo, mods/horde); the base game never
// calls it. While a player holds Tab (the zombies scoreboard) the engine draws two numbers (cgame, gated on the
// mod dvar bo1_mod_tabinfo, see CG_SP_DrawTabInfo in src/cgame/cg_sp_hud.cpp):
//   alive = get_enemy_count() (maps\_zombiemode_utility): the axis AI alive now, dogs and specials included,
//           minus the ones retail marks ignore_enemy_count; the same count retail's round_wait() waits on.
//   left  = level.zombie_total (retail: zombies of this round not spawned yet) + alive = what is left this round.
// Sent to every player with SetClientDvars (a reliable server command, so remote clients get it too) twice a second
// while the numbers change, and again every 5 s. Dvar zinfo 0/1 turns it off/on while playing (default: the caller's).
// Started by mods/zinfo's hook: maps/_zombiemode_ffotd.gsc (fs_game mods/zinfo) or maps/zinfo/_hooks.gsc (fs_mods).

main( default_on )
{
	if ( GetDvar( #"zinfo" ) == "" )
	{
		SetDvar( "zinfo", default_on );
	}
	flag_wait( "all_players_connected" );

	last_alive = -1;
	last_left = -1;
	last_on = -1;
	last_send = 0;
	for ( ;; )
	{
		on = GetDvarInt( #"zinfo" ) != 0;
		alive = get_enemy_count();
		left = alive;
		if ( IsDefined( level.zombie_total ) && level.zombie_total > 0 )
		{
			left += level.zombie_total;
		}
		if ( on != last_on || ( on && ( alive != last_alive || left != last_left || GetTime() - last_send >= 5000 ) ) )
		{
			players = get_players();
			for ( i = 0; i < players.size; i++ )
			{
				players[i] SetClientDvars( "bo1_mod_tabinfo", on, "zinfo_alive", alive, "zinfo_left", left );
			}
			if ( on && ( alive != last_alive || left != last_left ) )
			{
				LogPrint( "zinfo: alive " + alive + " left " + left + " round " + level.round_number + " t " + GetTime() + "\n" );
			}
			last_on = on;
			last_alive = alive;
			last_left = left;
			last_send = GetTime();
		}
		wait( 0.5 );
	}
}
