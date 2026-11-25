#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

// mod: shared enemy module (mods/_stack) - hellhound packs outside dog rounds (L61, nightmare on Kino). Not part of the
// original game. Needs a map with native dogs (Kino: level.dogs_enabled, zombie_theater.gsc:49; the retail init runs from
// level.custom_ai_type, :68) - no extra zone. The pack is the retail mixed-round entry point special_dog_spawn
// (_zombiemode_ai_dogs.gsc:854, also used by _zombiemode.gsc:3268): lightning, sounds, a dog from
// level.enemy_dog_spawns[0] at a level.dog_spawn_func location in an active zone. It leaves zombie_total alone; living
// dogs hold the round end like any enemy (get_enemy_count), as on retail mixed rounds.
// Dvars: nightmare_dog_round N (first round), nightmare_dog_pack N (dogs per pack), nightmare_dog_interval S (s from one
// pack to the next), nightmare_dog_max N (no new pack while N or more dogs live; the retail cap is 9, :858).

init()
{
	if ( IsDefined( level.mod_dogs_init_done ) )
		return;
	level.mod_dogs_init_done = true;
	level thread dog_pack_loop();
}


dog_pack_loop()
{
	level endon( "end_game" );
	// started from the map's first hook (nightmare main_start), before _zombiemode::main's flag_init
	while ( !IsDefined( level.flag ) || !IsDefined( level.flag["all_players_connected"] ) )
		wait 0.05;
	flag_wait( "all_players_connected" );
	// the retail init (custom_ai_type) and the zone manager's dog locations (_zombiemode_zone_manager.gsc:704)
	for ( i = 0; i < 60; i++ )
	{
		if ( IsDefined( level.enemy_dog_spawns ) && level.enemy_dog_spawns.size > 0 && IsDefined( level.enemy_dog_locations ) && level.enemy_dog_locations.size > 0 )
			break;
		wait 1;
	}
	if ( !IsDefined( level.enemy_dog_spawns ) || level.enemy_dog_spawns.size == 0 )
	{
		maps\_modenemy::log( "dogs: no zombie_spawner_dog_init spawner on this map, no packs" );
		return;
	}
	maps\_modenemy::log( "dogs: packs of " + GetDvarInt( #"nightmare_dog_pack" ) + " every " + GetDvarInt( #"nightmare_dog_interval" ) + " s, max " + GetDvarInt( #"nightmare_dog_max" ) );
	packs = 0;
	for ( ;; )
	{
		if ( level.round_number >= GetDvarInt( #"nightmare_dog_round" ) && !flag( "dog_round" ) && level.enemy_dog_locations.size > 0 )
		{
			alive = GetAISpeciesArray( "axis", "dog" ).size;
			n = GetDvarInt( #"nightmare_dog_pack" );
			max = GetDvarInt( #"nightmare_dog_max" );
			if ( n > max - alive )
				n = max - alive;
			if ( n > 0 )
			{
				packs++;
				maps\_modenemy::log( "dogs: pack #" + packs + " of " + n + " round " + level.round_number + " alive " + alive );
				level thread maps\_zombiemode_ai_dogs::special_dog_spawn( undefined, n );
				wait GetDvarInt( #"nightmare_dog_interval" );
				continue;
			}
		}
		wait 1;
	}
}
