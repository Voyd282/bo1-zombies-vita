#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

// mod: shared enemy module (mods/_stack; used by mods/horde and mods/nightmare, moved from
// mods/horde/maps/horde/enemies) - Shangri-La's napalm zombie in Five. Not part of the original game.
// Needs +set bo1_mod_zones zombie_temple (the engine then loads zombie_temple.ff after Five, keeping Five's copy of
// every shared asset, and appends maps\zombie_pentagon.zombie_temple.ents: one actor_zombie_napalm spawner).
// The napalm zombie itself is retail maps\_zombiemode_ai_napalm (zombie_temple.ff), unmodified; this file only does
// what zombie_temple.gsc / zombie_temple_spawning.gsc do for it (init, client flag values, the spawn).
// Dvars: horde_enemy_napalm 1 (default 0 = off), horde_enemy_napalm_round N (first round; default = retail's
// level.nextNapalmSpawnRound, 5-7), horde_enemy_napalm_max N (alive at once; retail 1), horde_enemy_napalm_per_round N
// (spawns per round; retail 1), horde_enemy_napalm_respawn S (L60b, nightmare; default 0 = off: horde unchanged): no per-round
// count, a new napalm S seconds after the last napalm death whenever fewer than max are alive.

init()
{
	// a module registered by two stacked mods (horde + nightmare) starts once
	if ( IsDefined( level.mod_napalm_init_done ) )
		return;
	level.mod_napalm_init_done = true;
	level thread maps\_modenemy::log_later( "napalm: init, horde_enemy_napalm '" + GetDvar( #"horde_enemy_napalm" ) + "'" );
	if ( GetDvar( #"horde_enemy_napalm" ) == "" )
		SetDvar( "horde_enemy_napalm", "0" );
	if ( !GetDvarInt( #"horde_enemy_napalm" ) )
		return;
	if ( GetEntArray( "napalm_zombie_spawner", "script_noteworthy" ).size == 0 )
	{
		level thread maps\_modenemy::log_later( "napalm: no napalm_zombie_spawner (needs +set bo1_mod_zones zombie_temple)" );
		return;
	}
	// zombie_temple.gsc:166-170
	level._CF_ACTOR_IS_NAPALM_ZOMBIE = 0;
	level._CF_ACTOR_NAPALM_ZOMBIE_EXPLODE = 2;
	level._CF_ACTOR_NAPALM_ZOMBIE_WET = 4;
	// temple runs this from maps\_zombiemode::main (level.custom_ai_type); Five resets that list in its main(), so
	// the mod runs it from main_end, still before the first frame
	maps\_zombiemode_ai_napalm::napalm_zombie_init();
	level thread napalm_round_spawner();
}


napalm_round_spawner()
{
	level endon( "end_game" );
	flag_wait( "all_players_connected" );
	level.mod_napalm_alive = 0;
	level.mod_napalm_spawned = 0;
	level.mod_napalm_last_death = -1000000;
	spawnedRound = -1;
	spawnedThisRound = 0;
	for ( ;; )
	{
		wait 1;
		firstRound = level.nextNapalmSpawnRound;
		if ( GetDvarInt( #"horde_enemy_napalm_round" ) > 0 )
			firstRound = GetDvarInt( #"horde_enemy_napalm_round" );
		maxAlive = 1;
		if ( GetDvarInt( #"horde_enemy_napalm_max" ) > 0 )
			maxAlive = GetDvarInt( #"horde_enemy_napalm_max" );
		perRound = 1;
		if ( GetDvarInt( #"horde_enemy_napalm_per_round" ) > 0 )
			perRound = GetDvarInt( #"horde_enemy_napalm_per_round" );
		if ( !IsDefined( level.round_number ) || level.round_number < firstRound )
			continue;
		if ( spawnedRound != level.round_number )
		{
			spawnedRound = level.round_number;
			spawnedThisRound = 0;
		}
		respawn = GetDvarInt( #"horde_enemy_napalm_respawn" );
		if ( level.mod_napalm_alive >= maxAlive )
			continue;
		if ( respawn > 0 && GetTime() - level.mod_napalm_last_death < respawn * 1000 )
			continue;
		if ( maps\_modenemy::special_first_wave_wait( level.mod_napalm_alive, level.mod_sonic_spawned ) )
			continue;
		if ( respawn <= 0 && spawnedThisRound >= perRound )
			continue;
		if ( IsDefined( napalm_spawn_one() ) )
			spawnedThisRound++;
	}
}


// zombie_temple_spawning.gsc _try_spawn_napalm: the spawner is moved to the spawn origin as a "zombie_chaser"
// (no window route), spawned, reset and the AI teleported. Origin: _modenemy::special_spawn_point.
napalm_spawn_one()
{
	point = maps\_modenemy::special_spawn_point();
	if ( !IsDefined( point ) )
		return undefined;
	spawnOrigin = point.origin;
	spawnAngles = point.angles;
	spawner = level.napalm_zombie_spawners[0];
	spawner.target = "";
	spawner.script_string = "zombie_chaser";
	spawner.origin = spawnOrigin;
	ai = spawn_zombie( spawner );
	spawner.script_string = undefined;
	spawner.count = 100;
	spawner.last_spawn_time = GetTime();
	if ( spawn_failed( ai ) )
	{
		maps\_modenemy::log( "napalm: spawn failed" );
		return undefined;
	}
	ai ForceTeleport( spawnOrigin, spawnAngles );
	level.mod_napalm_alive++;
	level.mod_napalm_spawned++;
	maps\_modenemy::log( "napalm: spawned #" + level.mod_napalm_spawned + " round " + level.round_number + " at " + spawnOrigin );
	ai.mod_napalm_id = level.mod_napalm_spawned;
	ai.mod_napalm_spawn_origin = spawnOrigin;
	ai.mod_napalm_spawn_time = GetTime();
	ai.mod_napalm_path = 0;
	ai.mod_napalm_far = 0;
	ai thread napalm_watch_death();
	ai thread napalm_watch_move();
	ai thread napalm_watch_charge();
	players = get_players();
	for ( i = 0; i < players.size; i++ )
		players[i] thread napalm_watch_player_damage( ai, ai.mod_napalm_id );
	return ai;
}


// mod: measurement only (modenemy log lines "napalm: ..."): distance walked (sum of 0.5 s steps) and farthest from spawn
napalm_watch_move()
{
	self endon( "death" );
	last = self.origin;
	for ( ;; )
	{
		wait 0.5;
		self.mod_napalm_path += Distance( last, self.origin );
		last = self.origin;
		d = Distance( self.mod_napalm_spawn_origin, self.origin );
		if ( d > self.mod_napalm_far )
			self.mod_napalm_far = d;
	}
}


// mod: measurement only: retail _zombie_ExplodeNearPlayers (_zombiemode_ai_napalm.gsc:476-583) plays the wind-up with
// animscripted( "napalm_explode", ... ) near a player, then kills itself (self.killed_self)
napalm_watch_charge()
{
	self endon( "death" );
	self waittill( "napalm_explode" );
	self.mod_napalm_charged = true;
	maps\_modenemy::log( "napalm: #" + self.mod_napalm_id + " charge (explode wind-up) at " + Int( Distance( self.mod_napalm_spawn_origin, self.origin ) ) + " from spawn" );
}


// mod: measurement only: player damage while this napalm lives and 25 s after (its fire trigger lasts 20 s):
// attacker == the napalm (its explosion or melee) or MOD_BURNED (the fire trigger's "burned" DoDamage)
napalm_watch_player_damage( napalm, id )
{
	self endon( "disconnect" );
	level endon( "mod_napalm_done_" + id );
	for ( ;; )
	{
		self waittill( "damage", amount, attacker, dir, point, mod );
		if ( IsDefined( napalm ) && IsDefined( attacker ) && attacker == napalm )
			maps\_modenemy::log( "napalm: #" + id + " hit player " + amount + " " + mod + " health " + self.health );
		else if ( IsDefined( mod ) && mod == "MOD_BURNED" )
			maps\_modenemy::log( "napalm: #" + id + " fire hit player " + amount + " health " + self.health );
	}
}


napalm_watch_death()
{
	id = self.mod_napalm_id;
	spawnOrigin = self.mod_napalm_spawn_origin;
	spawnTime = self.mod_napalm_spawn_time;
	self waittill( "death", attacker );
	origin = self.origin;
	level.mod_napalm_alive--;
	level.mod_napalm_last_death = GetTime();
	cause = "shot";
	if ( IsDefined( self.killed_self ) && self.killed_self )
		cause = "exploded near a player";
	else if ( !IsDefined( attacker ) || !IsPlayer( attacker ) )
		cause = "other";
	if ( cause == "other" && IsDefined( attacker ) && IsDefined( attacker.classname ) )
		cause = "other: " + attacker.classname;
	else if ( cause == "other" )
		cause = "other: no attacker";
	near = [];
	zombies = GetAiSpeciesArray( "axis", "all" );
	for ( i = 0; i < zombies.size; i++ )
	{
		if ( zombies[i] != self && IsAlive( zombies[i] ) && DistanceSquared( zombies[i].origin, origin ) < 250 * 250 )
			near[near.size] = zombies[i];
	}
	maps\_modenemy::log( "napalm: #" + id + " died (" + cause + "), alive " + level.mod_napalm_alive + ", lived " + ( GetTime() - spawnTime ) + " ms, walked " + Int( self.mod_napalm_path ) + ", farthest " + Int( self.mod_napalm_far ) + ", zombies within 250: " + near.size );
	wait 1;
	killed = 0;
	for ( i = 0; i < near.size; i++ )
	{
		if ( !IsDefined( near[i] ) || !IsAlive( near[i] ) )
			killed++;
	}
	fires = 0;
	triggers = GetEntArray( "trigger_radius", "classname" );
	for ( i = 0; i < triggers.size; i++ )
	{
		if ( IsDefined( triggers[i].napalm_fire_damage ) && triggers[i].napalm_fire_damage == 40 && DistanceSquared( triggers[i].origin, origin ) < 100 * 100 )
			fires++;
	}
	maps\_modenemy::log( "napalm: #" + id + " explosion killed " + killed + " of " + near.size + " zombies, fire triggers " + fires );
	wait 24;
	level notify( "mod_napalm_done_" + id );
}
