#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

// mod: shared enemy module (mods/_stack) - Call of the Dead's George Romero (the director) in Five. Not part of the
// original game. Needs +set bo1_mod_zones zombie_coast (zombie_coast.ff after Five, and
// maps\zombie_pentagon.zombie_coast.ents: one actor_zombie_director_romero spawner, targetname boss_zombie_spawner,
// parked outside the map). George himself is retail maps\_zombiemode_ai_director (zombie_coast.ff), unmodified; this
// file does what zombie_coast.gsc / zombie_coast_ai_director.gsc do for him, without the water (Five has none):
// enter = the retail default bolt + Show + weapon, exit (after 250000 x players damage he is "defeated") = bolt out and
// park hidden, reenter = back at a zombie's spot in the playable area. He never dies (retail).
// Dvars: nightmare_george_max N (alive at once; retail 1), nightmare_george_round N (first round; default 1),
// nightmare_george_pace S (seconds between spawns; default 10, retail), nightmare_george_respawn S (seconds after a
// death; default 30).

init()
{
	// a module registered by two stacked mods starts once
	if ( IsDefined( level.mod_director_init_done ) )
		return;
	level.mod_director_init_done = true;
	spawners = GetEntArray( "boss_zombie_spawner", "targetname" );
	if ( spawners.size == 0 )
	{
		level thread maps\_modenemy::log_later( "director: no boss_zombie_spawner (needs +set bo1_mod_zones zombie_coast)" );
		return;
	}
	// zombie_coast.gsc:54-59 uses actor flags 2-5; on Five napalm/sonic already use actor flags 0, 2, 3, 4
	// (maps\_modenemies\_napalm, _sonic) and Five's common csc 7-9 and 14-15, so George gets the free 10-13. The
	// numbers only have to match clientscripts\mod_zones\zombie_coast.csc. Player flag 8 is free on Five.
	level._CF_PLAYER_ELECTRIFIED = 8;
	level._ZOMBIE_ACTOR_FLAG_ELECTRIFIED = 10;
	level._ZOMBIE_ACTOR_FLAG_DIRECTOR_LIGHT = 11;
	level._ZOMBIE_ACTOR_FLAG_DIRECTORS_STEPS = 12;
	level._ZOMBIE_ACTOR_FLAG_DIRECTOR_DEATH = 13;
	// zombie_coast_ai_director.gsc:15-24; max 0 keeps the retail manager (_zombiemode_ai_director.gsc:526) idle, the
	// mod spawns him itself (several at once in Hell)
	level.max_director_zombies = 0;
	level.director_zombie_enter_level = ::director_enter_level;
	level.director_reenter_level = ::director_reenter_level;
	level.director_exit_level = ::director_exit_level;
	level.director_find_exit = ::director_find_exit;
	// coast starts the activation watch when he leaves the water (zombie_coast_ai_director.gsc:163-170)
	level.director_zombie_custom_think = ::director_custom_think;
	level.mod_director_spawner = spawners[0];
	level.mod_director_park = spawners[0].origin;
	// retail coast's server fx script precaches George's buff fx; clientscripts\mod_zones\zombie_coast.csc LoadFX finds
	// it only in the server's fx configstrings (CScr_LoadedFX: "loadedfx could not find effect", seen in L60 s1)
	level._effect["director_buff_electrified"] = LoadFX( "maps/zombie/fx_zmb_coast_electrified_torso" );
	maps\_zombiemode_ai_director::init();
	level thread director_spawner_loop();
	level thread maps\_modenemy::log_later( "director: init, max " + GetDvarInt( #"nightmare_george_max" ) );
}


// before Five's main runs include_powerups (its hook main_start): a defeated George drops "free_perk"
// (_zombiemode_ai_director.gsc:816), which Five does not include
main_start()
{
	include_powerup( "free_perk" );
}


director_spawner_loop()
{
	level endon( "end_game" );
	flag_wait( "all_players_connected" );
	level.mod_director_spawned = 0;
	level.mod_director_last_death = -30000;
	lastSpawn = -1000000;
	for ( ;; )
	{
		// the retail manager's pace (_zombiemode_ai_director.gsc:546-553: one every 10 s); the spot is polled every
		// second like napalm's (special_spawn_point is often taken in Five's small start room: L60 n4, 40 of 40 polls
		// at 10 s found none while astronauts and napalms were alive)
		wait 1;
		pace = 10;
		if ( GetDvarInt( #"nightmare_george_pace" ) > 0 )
			pace = GetDvarInt( #"nightmare_george_pace" );
		if ( GetTime() - lastSpawn < pace * 1000 )
			continue;
		firstRound = 1;
		if ( GetDvarInt( #"nightmare_george_round" ) > 0 )
			firstRound = GetDvarInt( #"nightmare_george_round" );
		maxAlive = 1;
		if ( GetDvarInt( #"nightmare_george_max" ) > 0 )
			maxAlive = GetDvarInt( #"nightmare_george_max" );
		if ( !IsDefined( level.round_number ) || level.round_number < firstRound )
			continue;
		// he cannot be shot dead (a defeat sends him away and back), but a napalm explosion kills him (retail
		// _zombiemode_ai_napalm DoDamage health + 666 on every zombie within 250; they never meet in retail: L60 n7, all
		// three Georges died at 3:49 next to napalm #1): a new George comes 30 s after a death
		respawn = 30;
		if ( GetDvarInt( #"nightmare_george_respawn" ) > 0 )
			respawn = GetDvarInt( #"nightmare_george_respawn" );
		if ( director_count( true ) >= maxAlive || GetTime() - level.mod_director_last_death < respawn * 1000 )
			continue;
		if ( IsDefined( director_spawn_one() ) )
			lastSpawn = GetTime();
	}
}


// _zombiemode_ai_director.gsc:489-516 director_zombie_spawn, except that Hide() waits for a defined spawn: retail hides
// the result before its spawn_failed check, a script error when the spawn fails ("undefined is not an entity", line
// 500), which ended director_spawner_loop (L60 hn1: horde + nightmare, round 16, horde's SetAILimit 128 reached)
director_zombie_spawn_safe()
{
	self.script_moveoverride = true;
	if ( !IsDefined( level.num_director_zombies ) )
	{
		level.num_director_zombies = 0;
	}
	level.num_director_zombies++;
	director_zombie = self maps\_zombiemode_net::network_safe_stalingrad_spawn( "boss_zombie_spawn", 1 );
	if ( IsDefined( director_zombie ) )
	{
		director_zombie Hide();
	}
	self.count = 666;
	self.last_spawn_time = GetTime();
	if ( !spawn_failed( director_zombie ) )
	{
		director_zombie.script_noteworthy = self.script_noteworthy;
		director_zombie.targetname = self.targetname;
		director_zombie.target = self.target;
		director_zombie.deathFunction = maps\_zombiemode_ai_director::director_zombie_die;
		director_zombie.animname = "director_zombie";
		director_zombie thread maps\_zombiemode_ai_director::director_zombie_think();
	}
	else
	{
		level.num_director_zombies--;
	}
}


director_spawn_one()
{
	point = maps\_modenemy::special_spawn_point( true );
	if ( !IsDefined( point ) )
		return undefined;
	spawner = level.mod_director_spawner;
	spawner.origin = point.origin;
	spawner.angles = point.angles;
	before = 0;
	if ( IsDefined( level.num_director_zombies ) )
		before = level.num_director_zombies;
	spawner director_zombie_spawn_safe();
	if ( !IsDefined( level.num_director_zombies ) || level.num_director_zombies <= before )
	{
		maps\_modenemy::log( "director: spawn failed" );
		return undefined;
	}
	// director_zombie_spawn returns nothing; the new one is the director without a mod id
	ai = undefined;
	zombies = GetAiSpeciesArray( "axis", "all" );
	for ( i = 0; i < zombies.size; i++ )
	{
		if ( IsDefined( zombies[i].animname ) && zombies[i].animname == "director_zombie" && !IsDefined( zombies[i].mod_director_id ) )
			ai = zombies[i];
	}
	if ( !IsDefined( ai ) )
	{
		maps\_modenemy::log( "director: spawned but not found" );
		return undefined;
	}
	ai ForceTeleport( point.origin, point.angles );
	level.mod_director_spawned++;
	ai.mod_director_id = level.mod_director_spawned;
	maps\_modenemy::log( "director: spawned #" + ai.mod_director_id + " round " + level.round_number + " alive " + director_count() + " at " + point.origin );
	ai thread director_watch_aggro();
	ai thread director_watch_death();
	ai thread director_watch_reach();
	players = get_players();
	for ( i = 0; i < players.size; i++ )
		players[i] thread director_watch_player_damage( ai, ai.mod_director_id );
	return ai;
}


director_count( includeAway )
{
	n = 0;
	zombies = GetAiSpeciesArray( "axis", "all" );
	for ( i = 0; i < zombies.size; i++ )
	{
		if ( IsAlive( zombies[i] ) && IsDefined( zombies[i].animname ) && zombies[i].animname == "director_zombie" && ( is_true( includeAway ) || !is_true( zombies[i].mod_director_away ) ) )
			n++;
	}
	return n;
}


// level.director_zombie_enter_level: the retail default (_zombiemode_ai_director.gsc:2327) plus what coast's water rise
// does after it (zombie_coast_ai_director.gsc:306-323): Show, collision, the weapon
director_enter_level()
{
	self endon( "death" );
	PlayFX( level._effect["director_spawn"], self.origin );
	playsoundatposition( "zmb_bolt", self.origin );
	PlayRumbleOnPosition( "explosion_generic", self.origin );
	self Show();
	self thread director_delay_weapon();
	self SetPlayerCollision( 1 );
	self.goalradius = 90;
	self.on_break = undefined;
}


// zombie_coast_ai_director.gsc:325-342
director_delay_weapon()
{
	self endon( "death" );
	if ( is_true( self.has_weapon ) )
	{
		self maps\_zombiemode_ai_director::director_flip_light_flag();
		return;
	}
	wait( 0.25 );
	self maps\_zombiemode_ai_director::director_add_weapon();
	self.has_weapon = true;
}


director_custom_think()
{
	self endon( "death" );
	self thread maps\_zombiemode_ai_director::director_zombie_check_for_activation();
}


// level.director_find_exit (coast: the nearest water exit in an active zone): Five has no water, he leaves where he is
director_find_exit()
{
	exit = SpawnStruct();
	exit.origin = self.origin;
	exit.angles = self.angles;
	return exit;
}


// level.director_exit_level: zombie_coast_ai_director.gsc:385-465 without the walk to the water and the water anim
director_exit_level( exit, calm )
{
	self endon( "death" );
	self endon( "stop_exit" );
	self.exit = exit;
	self.calm = calm;
	self.on_break = true;
	if ( is_true( calm ) )
	{
		self.is_activated = false;
		self notify( "director_calmed" );
	}
	else
	{
		self.is_activated = true;
	}
	maps\_modenemy::log( "director: #" + self.mod_director_id + " defeated, leaving" );
	wait( 2 );
	PlayFX( level._effect["director_spawn"], self.origin );
	playsoundatposition( "zmb_bolt", self.origin );
	self SetPlayerCollision( 0 );
	self clearclientflag( level._ZOMBIE_ACTOR_FLAG_DIRECTOR_DEATH );
	players = getplayers();
	rand = RandomIntRange( 0, players.size );
	players[rand] thread maps\_zombiemode_audio::create_and_play_dialog( "director", "exit" );
	self.is_activated = false;
	self Hide();
	self.mod_director_away = true;
	self thread director_hold_away();
	self.exit = undefined;
	self.calm = undefined;
}


// mod: coast parks him under the water; Five parks him at the spawner's spot outside the map, hidden and ignoring
// everyone, until director_reenter_level
director_hold_away()
{
	self endon( "death" );
	while ( is_true( self.mod_director_away ) )
	{
		self.ignoreall = true;
		if ( DistanceSquared( self.origin, level.mod_director_park ) > 64 * 64 )
			self ForceTeleport( level.mod_director_park, self.angles );
		self SetGoalPos( level.mod_director_park );
		wait( 0.5 );
	}
}


// level.director_reenter_level: zombie_coast_ai_director.gsc:372-380 (the reentry point is a zombie's spot in the
// playable area, as for the spawn); _zombiemode_ai_director.gsc:2627 skips the light reset after a defeat, coast's
// water rise flips it back (coast_director_delay_weapon), and the activation watch was disabled at the defeat
director_reenter_level()
{
	self endon( "death" );
	point = undefined;
	while ( !IsDefined( point ) )
	{
		point = maps\_modenemy::special_spawn_point( true );
		if ( !IsDefined( point ) )
			wait( 1 );
	}
	self.mod_director_away = undefined;
	self ForceTeleport( point.origin, point.angles );
	PlayFX( level._effect["director_spawn"], self.origin );
	playsoundatposition( "zmb_bolt", self.origin );
	self Show();
	self SetPlayerCollision( 1 );
	self.ignoreall = false;
	self thread director_delay_weapon();
	self.goalradius = 90;
	self.on_break = undefined;
	self thread maps\_zombiemode_ai_director::director_zombie_check_for_activation();
	maps\_modenemy::log( "director: #" + self.mod_director_id + " back at " + point.origin + " round " + level.round_number );
}


// mod: measurement only ("modenemy: director: ..." in games_mp.log)
director_watch_aggro()
{
	self endon( "death" );
	for ( ;; )
	{
		self waittill( "director_aggro" );
		maps\_modenemy::log( "director: #" + self.mod_director_id + " angry" );
	}
}


// mod: measurement only: first time within melee range (64) of a player after each (re)entry (a god-mode test client
// gets no "damage" notify, so the hit log above stays empty for it)
director_watch_reach()
{
	self endon( "death" );
	for ( ;; )
	{
		wait( 0.5 );
		if ( is_true( self.mod_director_away ) )
		{
			self.mod_director_reached = undefined;
			continue;
		}
		if ( is_true( self.mod_director_reached ) )
			continue;
		players = get_players();
		for ( i = 0; i < players.size; i++ )
		{
			if ( DistanceSquared( players[i].origin, self.origin ) < 64 * 64 )
			{
				self.mod_director_reached = true;
				maps\_modenemy::log( "director: #" + self.mod_director_id + " reached a player (angry " + is_true( self.is_activated ) + ")" );
				break;
			}
		}
	}
}


director_watch_death()
{
	id = self.mod_director_id;
	self waittill( "death", attacker, mod );
	level.mod_director_last_death = GetTime();
	desc = "no attacker";
	if ( IsDefined( attacker ) && IsPlayer( attacker ) )
		desc = "player";
	else if ( IsDefined( attacker ) && IsDefined( attacker.classname ) )
		desc = attacker.classname;
	if ( IsDefined( mod ) )
		desc = desc + " " + mod;
	maps\_modenemy::log( "director: #" + id + " gone (death notify: " + desc + "), alive " + director_count() + " round " + level.round_number );
}


director_watch_player_damage( director, id )
{
	self endon( "disconnect" );
	while ( IsDefined( director ) )
	{
		self waittill( "damage", amount, attacker );
		if ( IsDefined( director ) && IsDefined( attacker ) && attacker == director )
			maps\_modenemy::log( "director: #" + id + " hit player " + amount + " health " + self.health );
	}
}
