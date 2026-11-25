#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

// mod: shared enemy module (mods/_stack) - Ascension's space monkeys on another map (L62: Kino). Not part of the original game.
// Needs +set bo1_mod_zones ... zombie_cosmodrome (zombie_cosmodrome.ff after the map; the engine leaves its map props
// unlinked, L62) and maps\<map>.zombie_cosmodrome.ents (actor_zombie_monkey_01 spawners, targetname monkey_zombie_spawner,
// parked). Retail maps\_zombiemode_ai_monkey (M = zombie_cosmodrome's copy; Shangri-La ships the same file plus a
// level.max_perks default, so whichever zone's copy links behaves the same) is reused for the actor: prespawn, anims,
// think, the perk attack (M:1662: teleport to an attack spot, climb and pound the machine; at 0 machine health the pack
// takes that perk from everyone who has it, M:2917), ground pound, grenade pick-up, chasing the player once hurt (M:1394).
// This module replaces its round logic (monkey_round_tracker M:1056: a whole monkey round after power + a bought perk):
// - packs of nightmare_monkey_per_pack from nightmare_monkey_round on, at most nightmare_monkey_packs packs at once, the
//   next nightmare_monkey_respawn s after a pack is down; spawned at a path node near a player (_modenemy);
// - each pack goes for a perk machine in an active zone: one a player owns first (retail M:645), else any machine, so they
//   attack perks from round 1 before anyone bought one; a machine they emptied is repaired for the next pack;
// - attack spots: made from zombie_cosmodrome's measured offsets (_monkey_spots) where the map has none;
// - no pack-down reward (retail drops max ammo + a free perk after the round's last pack, M:2617); monkeys give points.

init()
{
	if ( IsDefined( level.mod_monkey_init_done ) )
		return;
	level.mod_monkey_init_done = true;
	spawners = GetEntArray( "monkey_zombie_spawner", "targetname" );
	if ( spawners.size == 0 )
	{
		level thread maps\_modenemy::log_later( "monkey: no monkey_zombie_spawner (needs +set bo1_mod_zones zombie_cosmodrome)" );
		return;
	}
	// retail init M:6-112 without monkey_round_tracker (M:105)
	PrecacheRumble( "explosion_generic" );
	maps\_zombiemode_ai_monkey::init_monkey_zombie_anims();
	level._effect["monkey_groundhit"] = loadfx( "maps/zombie/fx_zmb_monkey_ground_hit" );
	level._effect["monkey_death"] = loadfx( "maps/zombie/fx_zmb_monkey_death" );
	level._effect["monkey_spawn"] = loadfx( "maps/zombie/fx_zombie_ape_spawn_dust" );
	level._effect["monkey_eye_glow"] = loadfx( "maps/zombie/fx_zmb_monkey_eyes" );
	if ( !IsDefined( level.monkey_zombie_spawn_heuristic ) )
		level.monkey_zombie_spawn_heuristic = maps\_zombiemode_ai_monkey::monkey_zombie_default_spawn_heuristic;
	if ( !IsDefined( level.monkey_zombie_enter_level ) )
		level.monkey_zombie_enter_level = maps\_zombiemode_ai_monkey::monkey_zombie_default_enter_level;
	PrecacheShellshock( "electrocution" );
	level.num_monkey_zombies = 0;
	level.monkey_zombie_spawners = spawners;
	array_thread( spawners, ::add_spawn_function, maps\_zombiemode_ai_monkey::monkey_prespawn );
	level.max_monkey_zombies = 1;
	level.monkey_zombie_min_health = 150;
	level.monkey_zombie_groundhit_damage = 100;
	level.monkey_zombie_groundhit_trigger_radius = 96;
	level.monkey_zombie_groundhit_damage_radius = 280;
	level.monkey_ground_attack_delay = 5000;
	level.monkeys_per_pack = 3;
	level.monkey_pack_max = 1000000; // retail's reward after the round's last pack (M:2636) never comes
	level.monkey_pack = [];
	level.machine_health_max = 100;
	level.machine_damage_min = 1;
	level.machine_damage_max = 8;
	level.ground_hit_delay = RandomFloatRange( 4.5, 6.5 ) * 1000;
	level.monkey_death = 0;
	level.monkey_death_total = 0;
	level.monkey_packs_killed = 0;
	level.monkey_encounters = 1;
	level.monkey_intermission = false;
	flag_init( "monkey_round" );
	flag_init( "last_monkey_down" );
	flag_init( "monkey_pack_down" );
	flag_init( "perk_bought" );
	flag_init( "monkey_free_perk" );
	flag_init( "monkey_spawning" );
	level.perk_lost_func = maps\_zombiemode_ai_monkey::monkey_perk_lost;
	level.perk_bought_func = maps\_zombiemode_ai_monkey::monkey_perk_bought;
	level.revive_solo_fx_func = maps\_zombiemode_ai_monkey::monkey_revive_solo_fx;
	if ( !IsDefined( level.max_perks ) )
		level.max_perks = 4; // Shangri-La's copy's default (M:2673 reads it)
	level.mod_monkey_spawner = spawners[0];
	level thread monkey_spawner_loop();
	if ( GetDvarInt( #"nightmare_test_monkey_cam" ) )
		level thread monkey_test_cam();
	level thread maps\_modenemy::log_later( "monkey: init, " + spawners.size + " spawners" );
}


monkey_spawner_loop()
{
	level endon( "end_game" );
	flag_wait( "all_players_connected" );
	maps\_modenemies\_monkey_spots::add_perk_spots();
	// retail monkey_round_start (M:1106): machine health, grenade lists
	maps\_zombiemode_ai_monkey::monkey_zombie_setup_perks();
	level thread maps\_zombiemode_ai_monkey::monkey_grenade_watcher();
	level.mod_monkey_packs = 0;
	level.mod_monkey_spawned = 0;
	level.mod_monkey_last_down = -1000000;
	packs = 0;
	for ( ;; )
	{
		wait 1;
		if ( level.monkey_pack.size < packs )
			level.mod_monkey_last_down = GetTime();
		packs = level.monkey_pack.size;
		firstRound = 1;
		if ( GetDvarInt( #"nightmare_monkey_round" ) > 0 )
			firstRound = GetDvarInt( #"nightmare_monkey_round" );
		if ( !IsDefined( level.round_number ) || level.round_number < firstRound || flag( "monkey_spawning" ) )
			continue;
		if ( packs >= GetDvarInt( #"nightmare_monkey_packs" ) )
			continue;
		// the first packs come at once; after that one per nightmare_monkey_respawn s since the last pack was down
		if ( level.mod_monkey_packs >= GetDvarInt( #"nightmare_monkey_packs" ) && GetTime() - level.mod_monkey_last_down < GetDvarInt( #"nightmare_monkey_respawn" ) * 1000 )
			continue;
		monkey_pack_spawn();
		packs = level.monkey_pack.size;
	}
}


// retail monkey_pack_spawn (M:708) + monkey_pack_think (M:725) with this module's perk choice and spawn spots
monkey_pack_spawn()
{
	point = maps\_modenemy::special_spawn_point( true );
	if ( !IsDefined( point ) )
		return;
	pack = SpawnStruct();
	pack.monkeys = [];
	pack.attack = [];
	pack.target = undefined;
	pack.ground_hit_time = GetTime();
	pack.spawning_done = false;
	level.mod_monkey_packs++;
	pack.mod_id = level.mod_monkey_packs;
	level.monkey_pack[ level.monkey_pack.size ] = pack;
	pack monkey_pick_perk();
	pack maps\_zombiemode_ai_monkey::monkey_pack_set_machine();
	if ( IsDefined( pack.machine ) && ( !IsDefined( pack.machine.monkey_health ) || pack.machine.monkey_health <= 0 ) )
		pack.machine.monkey_health = level.machine_health_max;
	pack maps\_zombiemode_ai_monkey::monkey_pack_choose_enemy();
	perk = "none";
	if ( IsDefined( pack.perk ) )
		perk = pack.perk.script_noteworthy;
	maps\_modenemy::log( "monkey: pack #" + pack.mod_id + " round " + level.round_number + " at " + point.origin + " perk " + perk );
	flag_set( "monkey_spawning" );
	pack thread monkey_pack_think( point );
}


monkey_pack_think( point )
{
	level.monkey_zombie_health = level.zombie_health * 0.5; // retail monkey_setup_health (M:559): 0.25-1.0 x by encounter
	if ( level.nightmare_level >= 2 )
		level.monkey_zombie_health = level.zombie_health;
	if ( level.monkey_zombie_health < 150 )
		level.monkey_zombie_health = 150;
	count = GetDvarInt( #"nightmare_monkey_per_pack" );
	if ( count < 1 )
		count = 1;
	spawner = level.mod_monkey_spawner;
	for ( i = 0; i < count; i++ )
	{
		spawner.origin = point.origin;
		spawner.angles = point.angles;
		monkey = spawner maps\_zombiemode_ai_monkey::monkey_zombie_spawn( self );
		if ( !spawn_failed( monkey ) )
		{
			monkey ForceTeleport( point.origin, point.angles );
			self.monkeys[ self.monkeys.size ] = monkey;
			level.mod_monkey_spawned++;
			monkey.mod_monkey_id = level.mod_monkey_spawned;
			maps\_modenemy::log( "monkey: spawned #" + monkey.mod_monkey_id + " pack #" + self.mod_id + " alive " + level.num_monkey_zombies );
			monkey thread monkey_watch_attack( self );
		}
		else
			maps\_modenemy::log( "monkey: spawn failed" );
		if ( i < count - 1 )
			wait RandomFloatRange( 1.0, 2.0 );
	}
	self.spawning_done = true;
	flag_clear( "monkey_spawning" );
	if ( self.monkeys.size == 0 )
	{
		level.monkey_pack = array_remove( level.monkey_pack, self );
		return;
	}
	self thread maps\_zombiemode_ai_monkey::monkey_pack_update_enemy();
	self thread monkey_pack_update_perk();
}


// the nightmare perk choice: retail monkey_pack_man_setup_perks (M:645) takes only perks a player owns in an active zone;
// before anyone owns one, any machine in an active zone (not targeted by another pack)
monkey_pick_perk()
{
	self.perk = undefined;
	owned = [];
	other = [];
	trigs = GetEntArray( "zombie_vending", "targetname" );
	players = getplayers();
	for ( i = 0; i < trigs.size; i++ )
	{
		if ( is_true( trigs[i].targeted ) || !IsDefined( maps\_modenemies\_monkey_spots::perk_machine( trigs[i] ) ) )
			continue;
		org = trigs[i].origin;
		if ( IsDefined( trigs[i].realorigin ) )
			org = trigs[i].realorigin;
		if ( !check_point_in_active_zone( org ) )
			continue;
		has = false;
		for ( j = 0; j < players.size; j++ )
		{
			if ( players[j] HasPerk( trigs[i].script_noteworthy ) )
				has = true;
		}
		if ( has )
			owned[ owned.size ] = trigs[i];
		else
			other[ other.size ] = trigs[i];
	}
	if ( owned.size )
		self.perk = owned[ RandomInt( owned.size ) ];
	else if ( other.size )
		self.perk = other[ RandomInt( other.size ) ];
	if ( IsDefined( self.perk ) )
		self.perk.targeted = 1;
}


// retail monkey_pack_update_perk (M:763) with this module's perk choice
monkey_pack_update_perk()
{
	while ( IsDefined( self.perk ) && pack_alive( self ) )
	{
		if ( self.machine.monkey_health == 0 )
		{
			maps\_modenemy::log( "monkey: pack #" + self.mod_id + " took " + self.perk.script_noteworthy );
			self maps\_zombiemode_ai_monkey::monkey_pack_take_perk();
			wait_network_frame();
			self.perk.targeted = 0;
			self maps\_zombiemode_ai_monkey::monkey_pack_clear_perk_pos();
			self monkey_pick_perk();
			self maps\_zombiemode_ai_monkey::monkey_pack_set_machine();
			if ( IsDefined( self.machine ) && self.machine.monkey_health <= 0 )
				self.machine.monkey_health = level.machine_health_max;
			for ( i = 0; i < self.monkeys.size; i++ )
			{
				if ( IsDefined( self.monkeys[i] ) && !self.monkeys[i].charge_player )
				{
					self.monkeys[i].perk = self.perk;
					self.monkeys[i] notify( "stop_perk_attack" );
				}
			}
		}
		wait_network_frame();
	}
}


// log a monkey's first perk attack (the smoke's check that they reach a machine)
monkey_watch_attack( pack )
{
	self endon( "death" );
	for ( ;; )
	{
		wait 0.5;
		if ( IsDefined( self.state ) && self.state == "attack_perk" && IsDefined( self.perk ) && IsDefined( pack.machine ) )
		{
			maps\_modenemy::log( "monkey: #" + self.mod_monkey_id + " attacking " + self.perk.script_noteworthy + " at " + Int( Distance( self.origin, pack.machine.origin ) ) + " from the machine" );
			return;
		}
	}
}


pack_alive( pack )
{
	for ( i = 0; i < level.monkey_pack.size; i++ )
	{
		if ( level.monkey_pack[i] == pack )
			return true;
	}
	return false;
}


// test aid (screenshot runs, +set nightmare_test_monkey_cam 1): the first player, invulnerable, stands 220 units in front of
// the machine a monkey attacks and looks at it
monkey_test_cam()
{
	flag_wait( "all_players_connected" );
	for ( ;; )
	{
		wait 0.25;
		player = getplayers()[0];
		if ( !IsDefined( player ) )
			continue;
		player EnableInvulnerability();
		machine = undefined;
		for ( i = 0; i < level.monkey_pack.size && !IsDefined( machine ); i++ )
		{
			for ( j = 0; j < level.monkey_pack[i].monkeys.size; j++ )
			{
				m = level.monkey_pack[i].monkeys[j];
				if ( IsDefined( m ) && IsDefined( m.state ) && m.state == "attack_perk" && IsDefined( level.monkey_pack[i].machine ) )
					machine = level.monkey_pack[i].machine;
			}
		}
		if ( !IsDefined( machine ) )
			continue;
		pos = machine.origin + vector_scale( AnglesToRight( ( 0, machine.angles[1], 0 ) ), 220 );
		player SetOrigin( pos );
		player SetPlayerAngles( VectorToAngles( machine.origin + ( 0, 0, 40 ) - ( pos + ( 0, 0, 60 ) ) ) );
	}
}
