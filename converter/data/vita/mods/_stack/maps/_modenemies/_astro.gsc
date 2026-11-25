#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

// mod: shared enemy module (mods/_stack) - Moon's astronaut zombie in Five. Not part of the original game.
// Needs +set bo1_mod_zones zombie_moon (zombie_moon.ff after Five, and maps\zombie_pentagon.zombie_moon.ents: one
// actor_zombie_moon_zombie_astronaut spawner, targetname astronaut_zombie, parked outside the map). The astronaut is
// retail maps\_zombiemode_ai_astro (zombie_moon.ff), unmodified; it uses no client flags and no csc.
// Grab + headbutt teleports the player (_zombiemode_ai_astro.gsc:670-760): on Moon to a black hole struct, on Five
// (a deliberate choice) to Five's initial player spawn points in the conference room; on Kino (L61) to Kino's (the foyer).
// Dvars: nightmare_astro_max N (alive at once; retail 1), nightmare_astro_round N (first round; default 1),
// nightmare_astro_per_round N (spawns per round; default 1), nightmare_astro_respawn S (L60b; 0 = off): no per-round count,
// a new astronaut S seconds after the last astronaut death whenever fewer than max are alive.

init()
{
	// a module registered by two stacked mods starts once (a second retail init doubles its callbacks, A:25/A:51)
	if ( IsDefined( level.mod_astro_init_done ) )
		return;
	level.mod_astro_init_done = true;
	spawners = GetEntArray( "astronaut_zombie", "targetname" );
	if ( spawners.size == 0 )
	{
		level thread maps\_modenemy::log_later( "astro: no astronaut_zombie spawner (needs +set bo1_mod_zones zombie_moon)" );
		return;
	}
	maps\_zombiemode_ai_astro::init();
	// _zombiemode_ai_astro.gsc:49: the retail manager (zombie_total_set_func -> astro_zombie_manager, A:310) waits
	// for level.on_the_moon, which only Moon sets; the mod spawns them itself (several at once in Hell). Five sets
	// no zombie_total_set_func of its own.
	level.zombie_total_set_func = undefined;
	level._special_blackhole_bomb_structs = ::astro_teleport_structs;
	level._black_hole_teleport_override = ::astro_teleport_log;
	level.mod_astro_spawner = spawners[0];
	level thread astro_spawner_loop();
	level thread maps\_modenemy::log_later( "astro: init, max " + GetDvarInt( #"nightmare_astro_max" ) );
}


// level._special_blackhole_bomb_structs (A:680): Five's initial_spawn_points (grounded in place by
// coop_player_spawn_placement, _zombiemode.gsc:5414) as copies whose script_string is their zone (A:698 indexes
// level.zones with it; the originals hold character names)
astro_teleport_structs()
{
	if ( !IsDefined( level.mod_astro_teleport_structs ) )
	{
		level.mod_astro_teleport_structs = [];
		points = getstructarray( "initial_spawn_points", "targetname" );
		zone = maps\_modenemy::start_zone();
		for ( i = 0; i < points.size; i++ )
		{
			s = SpawnStruct();
			s.origin = points[i].origin;
			s.angles = points[i].angles;
			s.script_string = zone;
			level.mod_astro_teleport_structs[level.mod_astro_teleport_structs.size] = s;
		}
		// A:705 compares the zone volume's script_string with "lowgravity" once the power is on; Five's volumes have
		// none (an undefined compare is a script error), and nothing on Five reads it
		volume = level.zones[zone].volumes[0];
		if ( !IsDefined( volume.script_string ) )
			volume.script_string = "mod_astro_teleport";
	}
	return level.mod_astro_teleport_structs;
}


// level._black_hole_teleport_override (A:759): called with the player just before the teleport; measurement only
astro_teleport_log( player )
{
	maps\_modenemy::log( "astro: teleporting player from " + player.origin );
	player thread astro_teleport_log_after();
}


astro_teleport_log_after()
{
	self endon( "disconnect" );
	wait( 0.1 );
	maps\_modenemy::log( "astro: player teleported to " + self.origin );
}


astro_spawner_loop()
{
	level endon( "end_game" );
	flag_wait( "all_players_connected" );
	level.mod_astro_alive = 0;
	level.mod_astro_spawned = 0;
	level.mod_astro_last_death = -1000000;
	spawnedRound = -1;
	spawnedThisRound = 0;
	lastSpawn = 0;
	for ( ;; )
	{
		wait 1;
		firstRound = 1;
		if ( GetDvarInt( #"nightmare_astro_round" ) > 0 )
			firstRound = GetDvarInt( #"nightmare_astro_round" );
		maxAlive = 1;
		if ( GetDvarInt( #"nightmare_astro_max" ) > 0 )
			maxAlive = GetDvarInt( #"nightmare_astro_max" );
		perRound = 1;
		if ( GetDvarInt( #"nightmare_astro_per_round" ) > 0 )
			perRound = GetDvarInt( #"nightmare_astro_per_round" );
		if ( !IsDefined( level.round_number ) || level.round_number < firstRound )
			continue;
		if ( spawnedRound != level.round_number )
		{
			spawnedRound = level.round_number;
			spawnedThisRound = 0;
		}
		respawn = GetDvarInt( #"nightmare_astro_respawn" );
		if ( level.mod_astro_alive >= maxAlive || GetTime() - lastSpawn < 5000 )
			continue;
		if ( respawn > 0 && GetTime() - level.mod_astro_last_death < respawn * 1000 )
			continue;
		if ( respawn <= 0 && spawnedThisRound >= perRound )
			continue;
		if ( IsDefined( astro_spawn_one() ) )
		{
			spawnedThisRound++;
			lastSpawn = GetTime();
		}
	}
}


// astro_zombie_spawn (A:256) spawns at the spawner: move it to _modenemy::special_spawn_point first
// L61s2 test dvar nightmare_test_astro_near 1 (the nightmare smokes): a path node 150-450 units from a player instead, so
// an astronaut reaches the test client before Hell's napalm blasts kill it on the way (L61n/L61n2: 0 teleports, every
// astronaut "died (other)" without one hit); the grab itself stays retail
astro_spawn_one()
{
	if ( GetDvarInt( #"nightmare_test_astro_near" ) > 0 )
		point = maps\_modenemy::special_node_spawn_point( true, 150, 450 );
	else
		point = maps\_modenemy::special_spawn_point( true );
	if ( !IsDefined( point ) )
		return undefined;
	spawner = level.mod_astro_spawner;
	spawner.origin = point.origin;
	spawner.angles = point.angles;
	ai = spawner maps\_zombiemode_ai_astro::astro_zombie_spawn();
	if ( spawn_failed( ai ) )
	{
		maps\_modenemy::log( "astro: spawn failed" );
		return undefined;
	}
	ai ForceTeleport( point.origin, point.angles );
	level.mod_astro_alive++;
	level.mod_astro_spawned++;
	ai.mod_astro_id = level.mod_astro_spawned;
	ai.mod_astro_spawn_time = GetTime();
	maps\_modenemy::log( "astro: spawned #" + ai.mod_astro_id + " round " + level.round_number + " alive " + level.mod_astro_alive + " at " + point.origin );
	ai thread astro_watch_death();
	players = get_players();
	for ( i = 0; i < players.size; i++ )
		players[i] thread astro_watch_player_damage( ai, ai.mod_astro_id );
	return ai;
}


astro_watch_death()
{
	id = self.mod_astro_id;
	spawnTime = self.mod_astro_spawn_time;
	self waittill( "death", attacker );
	level.mod_astro_alive--;
	level.mod_astro_last_death = GetTime();
	cause = "other";
	if ( IsDefined( attacker ) && IsPlayer( attacker ) )
		cause = "player";
	maps\_modenemy::log( "astro: #" + id + " died (" + cause + "), alive " + level.mod_astro_alive + ", lived " + ( GetTime() - spawnTime ) + " ms" );
}


// mod: measurement only: the astronaut's headbutt / melee on a player
astro_watch_player_damage( astro, id )
{
	self endon( "disconnect" );
	astro endon( "death" );
	for ( ;; )
	{
		self waittill( "damage", amount, attacker );
		if ( IsDefined( attacker ) && attacker == astro )
			maps\_modenemy::log( "astro: #" + id + " hit player " + amount + " health " + self.health );
	}
}
