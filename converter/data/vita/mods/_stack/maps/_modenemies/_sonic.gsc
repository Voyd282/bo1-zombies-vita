#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

// mod: shared enemy module (mods/_stack; used by mods/horde and mods/nightmare, moved from
// mods/horde/maps/horde/enemies) - Shangri-La's shrieker (sonic) zombie in Five. Not part of the original game.
// Needs +set bo1_mod_zones zombie_temple (the engine loads zombie_temple.ff after Five, keeps Five's copy of every
// shared asset, and appends maps\zombie_pentagon.zombie_temple.ents: one actor_zombie_sonic spawner).
// The shrieker itself is retail maps\_zombiemode_ai_sonic (zombie_temple.ff), unmodified; this file only does what
// zombie_temple.gsc / zombie_temple_spawning.gsc do for it in Shangri-La.
// Dvars: horde_enemy_sonic 1 (default 0 = off), horde_enemy_sonic_round N (first round; default = retail's
// level.nextSonicSpawnRound, 4-7, re-rolled by the retail death watch), horde_enemy_sonic_max N (alive at once;
// retail 1), horde_enemy_sonic_per_round N (spawns per round; retail 1), horde_enemy_sonic_respawn S (L60b, nightmare;
// default 0 = off: horde unchanged): no per-round count and no mid-round wait, a new shrieker S seconds after the last
// shrieker death whenever fewer than max are alive.
// Not retail: temple's level.special_zombie_spawned_this_round (one napalm OR one sonic per round) is not applied, so
// both can come in one round.

init()
{
	// a module registered by two stacked mods (horde + nightmare) starts once
	if ( IsDefined( level.mod_sonic_init_done ) )
		return;
	level.mod_sonic_init_done = true;
	level thread maps\_modenemy::log_later( "sonic: init, horde_enemy_sonic '" + GetDvar( #"horde_enemy_sonic" ) + "'" );
	if ( GetDvar( #"horde_enemy_sonic" ) == "" )
		SetDvar( "horde_enemy_sonic", "0" );
	if ( !GetDvarInt( #"horde_enemy_sonic" ) )
		return;
	if ( GetEntArray( "sonic_zombie_spawner", "script_noteworthy" ).size == 0 )
	{
		level thread maps\_modenemy::log_later( "sonic: no sonic_zombie_spawner (needs +set bo1_mod_zones zombie_temple)" );
		return;
	}
	// zombie_temple.gsc:169
	level._CF_ACTOR_IS_SONIC_ZOMBIE = 3;
	// temple runs this from maps\_zombiemode::main (level.custom_ai_type, zombie_temple.gsc:67); Five resets that list
	// in its main(), so the mod runs it from main_end, still before the first frame
	maps\_zombiemode_ai_sonic::sonic_zombie_init();
	level thread sonic_round_spawner();
}


sonic_round_spawner()
{
	level endon( "end_game" );
	flag_wait( "all_players_connected" );
	level.mod_sonic_spawned = 0;
	level.mod_sonic_last_death = -1000000;
	spawnedRound = -1;
	spawnedThisRound = 0;
	for ( ;; )
	{
		wait 1;
		firstRound = level.nextSonicSpawnRound;
		if ( GetDvarInt( #"horde_enemy_sonic_round" ) > 0 )
			firstRound = GetDvarInt( #"horde_enemy_sonic_round" );
		maxAlive = 1;
		if ( GetDvarInt( #"horde_enemy_sonic_max" ) > 0 )
			maxAlive = GetDvarInt( #"horde_enemy_sonic_max" );
		perRound = 1;
		if ( GetDvarInt( #"horde_enemy_sonic_per_round" ) > 0 )
			perRound = GetDvarInt( #"horde_enemy_sonic_per_round" );
		if ( !IsDefined( level.round_number ) || level.round_number < firstRound )
			continue;
		if ( spawnedRound != level.round_number )
		{
			spawnedRound = level.round_number;
			spawnedThisRound = 0;
			level.zombiesLeftBeforeSonicSpawn = undefined;
		}
		respawn = GetDvarInt( #"horde_enemy_sonic_respawn" );
		if ( respawn > 0 )
		{
			alive = 0;
			if ( IsDefined( level.sonicZombieCount ) )
				alive = level.sonicZombieCount;
			if ( alive < maxAlive && GetTime() - level.mod_sonic_last_death >= respawn * 1000 && !maps\_modenemy::special_first_wave_wait( alive, level.mod_napalm_spawned ) )
				sonic_spawn_one();
			continue;
		}
		// zombie_temple_spawning.gsc:639 _update_sonic_variables (temple runs it at round start; round_number goes up
		// before round_spawning sets zombie_total, so here: the first second zombie_total > 0 in the round)
		if ( !IsDefined( level.zombiesLeftBeforeSonicSpawn ) )
		{
			if ( level.zombie_total <= 0 )
				continue;
			lo = Int( level.zombie_total * 0.25 );
			hi = Int( level.zombie_total * 0.75 );
			level.zombiesLeftBeforeSonicSpawn = lo;
			if ( hi > lo )
				level.zombiesLeftBeforeSonicSpawn = RandomIntRange( lo, hi );
		}
		alive = 0;
		if ( IsDefined( level.sonicZombieCount ) )
			alive = level.sonicZombieCount;
		if ( spawnedThisRound >= perRound || alive >= maxAlive )
			continue;
		// zombie_temple_spawning.gsc:712 _can_spawn_sonic: not before mid round (zombiemode_debug_sonic forces it)
		if ( GetDvarInt( #"zombiemode_debug_sonic" ) == 0 && level.zombie_total >= level.zombiesLeftBeforeSonicSpawn )
			continue;
		if ( IsDefined( sonic_spawn_one() ) )
			spawnedThisRound++;
	}
}


// zombie_temple_spawning.gsc:644 _try_spawn_sonic: the spawner is moved to the spawn origin as a "zombie_chaser"
// (no window route), spawned, reset and the AI teleported. Origin: _modenemy::special_spawn_point.
sonic_spawn_one()
{
	point = maps\_modenemy::special_spawn_point();
	if ( !IsDefined( point ) )
		return undefined;
	spawner = level.sonic_zombie_spawners[0];
	spawner.target = "";
	spawner.script_string = "zombie_chaser";
	spawner.origin = point.origin;
	ai = spawn_zombie( spawner );
	spawner.script_string = undefined;
	spawner.count = 100;
	spawner.last_spawn_time = GetTime();
	if ( spawn_failed( ai ) )
	{
		maps\_modenemy::log( "sonic: spawn failed" );
		return undefined;
	}
	ai ForceTeleport( point.origin, point.angles );
	level.mod_sonic_spawned++;
	maps\_modenemy::log( "sonic: spawned #" + level.mod_sonic_spawned + " round " + level.round_number + " at " + point.origin );
	ai.mod_sonic_id = level.mod_sonic_spawned;
	ai thread sonic_watch_death( point.origin, GetTime() );
	ai thread sonic_watch_screams();
	players = get_players();
	for ( i = 0; i < players.size; i++ )
		players[i] thread sonic_watch_player_damage( ai, ai.mod_sonic_id );
	return ai;
}


// mod: measurement only (modenemy log lines "sonic: ..."): retail _zombie_scream_attack_done (_zombiemode_ai_sonic.gsc:582)
// notifies scream_attack_done after each scream attack
sonic_watch_screams()
{
	self endon( "death" );
	for ( ;; )
	{
		self waittill( "scream_attack_done" );
		maps\_modenemy::log( "sonic: #" + self.mod_sonic_id + " scream attack done" );
	}
}


// mod: measurement only: player damage by this sonic while it lives
sonic_watch_player_damage( sonic, id )
{
	self endon( "disconnect" );
	sonic endon( "death" );
	for ( ;; )
	{
		self waittill( "damage", amount, attacker );
		if ( IsDefined( attacker ) && attacker == sonic )
			maps\_modenemy::log( "sonic: #" + id + " hit player " + amount + " health " + self.health );
	}
}


// mod: measurement only: who damages this sonic (attacker classname, means of death, weapon, inflictor)
sonic_watch_self_damage( id )
{
	self endon( "death" );
	while ( 1 )
	{
		self waittill( "damage", amount, attacker, direction, point, mod, modelName, tagName, partName, weapon, inflictor );
		who = "undefined";
		if ( IsDefined( attacker ) && IsPlayer( attacker ) )
			who = "player";
		else if ( IsDefined( attacker ) && IsDefined( attacker.classname ) )
			who = attacker.classname;
		inf = "undefined";
		if ( IsDefined( inflictor ) && IsDefined( inflictor.classname ) )
			inf = inflictor.classname;
		maps\_modenemy::log( "sonic: #" + id + " damage " + amount + " by " + who + " mod " + sonic_str( mod ) + " weapon " + sonic_str( weapon ) + " inflictor " + inf + " at " + self.origin + " health " + self.health );
	}
}

sonic_str( v )
{
	if ( !IsDefined( v ) )
		return "undefined";
	return "" + v;
}

sonic_watch_death( spawnOrigin, spawnTime )
{
	id = self.mod_sonic_id;
	self thread sonic_watch_self_damage( id );
	self waittill( "death", attacker, mod, weapon );
	level.mod_sonic_last_death = GetTime();
	maps\_modenemy::log( "sonic: #" + id + " death mod " + sonic_str( mod ) + " weapon " + sonic_str( weapon ) + " damagemod " + sonic_str( self.damagemod ) + " damageweapon " + sonic_str( self.damageweapon ) + " at " + self.origin + " health " + self.health );
	cause = "shot";
	if ( !IsDefined( attacker ) )
		cause = "other: no attacker";
	else if ( !IsPlayer( attacker ) && IsDefined( attacker.classname ) )
		cause = "other: " + attacker.classname;
	else if ( !IsPlayer( attacker ) )
		cause = "other";
	maps\_modenemy::log( "sonic: #" + id + " died (" + cause + "), lived " + ( GetTime() - spawnTime ) + " ms, from spawn " + Int( Distance( spawnOrigin, self.origin ) ) + ", next sonic round " + level.nextSonicSpawnRound );
}
