#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

// NOPERKS MOD - not part of the original game. dvar noperks 0/1 (default 0 = retail: nothing here changes the level).
// Every perk machine goes at map load: its buy trigger ("zombie_vending") and the entities the trigger targets (the
// machine models; Quick Revive's clip brushmodel, script_noteworthy "clip"). Retail maps\_zombiemode_perks::init() then
// finds no perk trigger and returns before any perk thread: no hum, jingle, power-on light or fx (they all run on
// those entities). The collision baked into the bsp as world clip brushes is switched off by the engine at map load
// (src/qcommon/cm_noperks.cpp, same dvar; count in dvar noperks_clip). Pack-a-Punch and the box stay; the free-perk
// power-up never drops. Its own mod: +set fs_mods "... noperks" (maps/noperks/_hooks.gsc), with or without horde,
// called from each mod's maps\_zombiemode_ffotd.gsc: main_start() runs before _zombiemode_perks::init(), main_end()
// right after it (same frame, so the precache calls below are still allowed).
// Self-test: one line per machine, "noperks PASS|FAIL <perk> at <origin>" (player-sized trace down through where it
// stood, and no perk trigger left). noperks_selftest 1 with noperks 0 prints the same trace for the retail level.

main_start()
{
	on = GetDvarInt( #"noperks" ) == 1;
	if ( !on && GetDvarInt( #"noperks_selftest" ) != 1 )
		return;
	level.noperks_machines = [];
	if ( on )
		level.zombie_additionalprimaryweapon_machine_origin = undefined; // Mule Kick (spawned by perks init) is never placed
	triggers = GetEntArray( "zombie_vending", "targetname" );
	machines = 0;
	clip = 0;
	had_revive = false;
	for ( i = 0; i < triggers.size; i++ )
	{
		rec = SpawnStruct();
		rec.perk = triggers[i].script_noteworthy;
		if ( IsDefined( rec.perk ) && ( rec.perk == "specialty_quickrevive" || rec.perk == "specialty_quickrevive_upgrade" ) )
			had_revive = true;
		rec.origin = triggers[i].origin;
		rec.angles = triggers[i].angles;
		if ( IsDefined( triggers[i].target ) )
		{
			ents = GetEntArray( triggers[i].target, "targetname" );
			for ( j = 0; j < ents.size; j++ )
			{
				if ( IsDefined( ents[j].script_noteworthy ) && ents[j].script_noteworthy == "clip" )
				{
					clip++;
				}
				else
				{
					machines++;
					rec.origin = ents[j].origin;
					rec.angles = ents[j].angles;
				}
				if ( on )
					ents[j] Delete();
			}
		}
		level.noperks_machines[ level.noperks_machines.size ] = rec;
		if ( on )
			triggers[i] Delete();
	}
	level.noperks_had_revive = had_revive;
	// retail vending_trigger_think does this per trigger; a wait on it starts before main_end. Also with no retail machine
	// (the mapkit empty base: only Mule Kick's trigger, which noperks stops, would have done it before that wait)
	if ( on )
		flag_init( "_start_zm_pistol_rank" );
	if ( on )
		LogPrint( "noperks: removed " + machines + " machines, " + triggers.size + " triggers, " + ( clip + GetDvarInt( #"noperks_clip" ) ) + " clip (" + clip + " clip entities, " + GetDvarInt( #"noperks_clip" ) + " world brushes)\n" );
}


main_end()
{
	if ( !IsDefined( level.noperks_machines ) )
		return;
	if ( GetDvarInt( #"noperks" ) != 1 )
	{
		level thread noperks_selftest( false );
		return;
	}
	// also with no machine removed (the mapkit empty base; mapkit places no layout perk with noperks 1): someone sets the
	// flag main_start initialised, or _zombiemode last_stand_pistol_rank_init waits on it for ever
	level thread noperks_solo_flags( level.noperks_had_revive );
	if ( level.noperks_machines.size > 0 )
		noperks_packapunch();
	noperks_no_free_perk();
	level thread noperks_selftest( true );
}


// retail maps\_zombiemode_perks::init() past its "no perk trigger" return, the perk machine parts left out: what
// Pack-a-Punch needs (its trigger thread, the vending precache, the timeout, the perk vars, its power-on thread)
noperks_packapunch()
{
	vending_weapon_upgrade_trigger = GetEntArray( "zombie_vending_upgrade", "targetname" );
	if ( vending_weapon_upgrade_trigger.size >= 1 )
	{
		array_thread( vending_weapon_upgrade_trigger, maps\_zombiemode_perks::vending_weapon_upgrade );
	}
	if ( !IsDefined( level.custom_vending_precaching ) )
	{
		level.custom_vending_precaching = maps\_zombiemode_perks::default_vending_precaching;
	}
	[[ level.custom_vending_precaching ]]();
	if ( !IsDefined( level.packapunch_timeout ) )
	{
		level.packapunch_timeout = 15;
	}
	set_zombie_var( "zombie_perk_cost", 2000 );
	if ( level.mutators["mutator_susceptible"] )
	{
		set_zombie_var( "zombie_perk_juggernaut_health", 80 );
		set_zombie_var( "zombie_perk_juggernaut_health_upgrade", 95 );
	}
	else
	{
		set_zombie_var( "zombie_perk_juggernaut_health", 160 );
		set_zombie_var( "zombie_perk_juggernaut_health_upgrade", 190 );
	}
	level thread maps\_zombiemode_perks::turn_PackAPunch_on();
}


// the free-perk power-up (_zombiemode_powerups: random drops, the special drop list, the quantum bomb) is never picked
noperks_no_free_perk()
{
	if ( IsDefined( level.zombie_powerup_array ) )
		level.zombie_powerup_array = array_remove( level.zombie_powerup_array, "free_perk" );
	if ( IsDefined( level.zombie_special_drop_array ) )
		level.zombie_special_drop_array = array_remove( level.zombie_special_drop_array, "free_perk" );
}


// retail vending_trigger_think() (maps\_zombiemode_perks) does this for every perk trigger, the Quick Revive one also
// the solo setup; other code waits on these flags (maps\_zombiemode last_stand_pistol_rank_init: "_start_zm_pistol_rank").
// (The flag_init is in main_start: that wait starts before main_end().)
noperks_solo_flags( had_revive )
{
	if ( had_revive )
	{
		flag_wait( "all_players_connected" );
		players = GetPlayers();
		if ( players.size == 1 )
		{
			flag_set( "solo_game" );
			level.solo_lives_given = 0;
			players[0].lives = 0;
			level maps\_zombiemode::zombiemode_solo_last_stand_pistol();
		}
	}
	flag_set( "_start_zm_pistol_rank" );
}


noperks_selftest( on )
{
	wait 0.5;
	left = GetEntArray( "zombie_vending", "targetname" ).size;
	for ( i = 0; i < level.noperks_machines.size; i++ )
	{
		rec = level.noperks_machines[i];
		// a standing player's hull from 40 units up down to the floor at the machine origin and 20 units off it along
		// the machine's axes (the origin is at the machine's back, usually against a wall, so some spots always touch
		// the wall): with the machine there every spot starts solid (noperks_selftest 1 shows it), without it one is clear
		spots = "";
		clear = 0;
		fwd = AnglesToForward( rec.angles ) * 20;
		side = AnglesToRight( rec.angles ) * 20;
		offsets = array( ( 0, 0, 0 ), fwd, fwd * -1, side, side * -1 );
		for ( k = 0; k < offsets.size; k++ )
		{
			spot = rec.origin + offsets[k];
			start = spot + ( 0, 0, 40 );
			pos = PlayerPhysicsTrace( start, spot + ( 0, 0, 1 ) );
			if ( pos[2] < start[2] - 20 )
			{
				clear++;
				spots = spots + "o";
			}
			else
			{
				spots = spots + "x";
			}
		}
		what = "solid";
		if ( clear > 0 )
			what = "clear";
		what = what + " " + spots + ", yaw " + rec.angles[1];
		if ( !on )
		{
			LogPrint( "noperks before " + rec.perk + " at " + rec.origin + ": " + what + " (noperks 0)\n" );
			continue;
		}
		result = "FAIL";
		if ( clear > 0 && left == 0 )
			result = "PASS";
		LogPrint( "noperks " + result + " " + rec.perk + " at " + rec.origin + " (trace " + what + ", perk triggers left " + left + ")\n" );
	}
}
