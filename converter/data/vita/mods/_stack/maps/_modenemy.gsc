// FS_MODS BASE - not part of the original game. Shared helpers of the enemy modules in maps\_modenemies\ (enemies from
// other maps, brought in with +set bo1_mod_zones <zone>), used by mods/horde and mods/nightmare. Factored out of
// mods/horde/maps/horde/_horde.gsc (horde_log, horde_special_spawn_point) unchanged apart from the names.
//
// A zone's maps\mod_zones\<zone>.gsc (here in mods/_stack) is run by the engine after the level main when the zone is
// loaded; it only runs the hook "mod_zones/<zone>::main" (maps\_modstack), so every stacked mod starts the modules it
// wants from its own maps\<name>\_hooks.gsc. A module started by two mods runs once (its own guard).

log_later( msg )
{
	wait 1;
	log( "(logged 1 s late) " + msg );
}


log( msg )
{
	LogPrint( "modenemy: " + msg + "\n" );
}


// L61: the zone of the map's initial player spawn points (getstructarray "initial_spawn_points", used by retail
// coop_player_spawn_placement): Five conference_level1, Kino foyer_zone (zombie_theater.gsc:97 init_zones); else the
// first enabled zone
start_zone()
{
	map = Tolower( GetDvar( #"mapname" ) );
	if ( map == "zombie_pentagon" )
		return "conference_level1";
	if ( map == "zombie_theater" )
		return "foyer_zone";
	keys = GetArrayKeys( level.zones );
	for ( i = 0; i < keys.size; i++ )
	{
		if ( IsDefined( level.zones[keys[i]].is_enabled ) && level.zones[keys[i]].is_enabled )
			return keys[i];
	}
	return keys[0];
}


// spawn point for a special enemy module. Temple's _get_special_spawn_point (zombie_temple_spawning.gsc:532) needs
// temple's special_zombie_spawn structs, Five has none: use the origin/angles of a zombie already in the playable area,
// else undefined (the module tries again next second; the old fallback, one of Five's zombie spawners outside the
// barriers, is where napalm #1 of the L33 n3/n4 runs spawned and never got out: farthest 44, died "other" at 30 s).
// A special spawns at least 600 units from every living special. Measured (L33-s10): napalm #1 and sonic #1 took
// the same zombie's spot in the same frame, walked together, and the retail napalm explosion (DoDamage health + 666 on
// every non-napalm zombie within 250 with line of sight, _zombiemode_ai_napalm.gsc) killed the sonic with attacker
// worldspawn. That kill is retail behaviour; sharing the spawn spot was the mod's.
// L60: George and the astronaut (nightmare) do not block a spot (George cannot die; an astronaut near a napalm can, as any zombie,
// and in Hell up to 3 + 3 of them in Five's start room left no spot for napalm/sonic: n5, sonic 0 in 6.5 min). Spots picked
// in the last 10 s still count for every special. Horde has neither, so horde is unchanged.
special_spot_ignores( o )
{
	return o.animname == "director_zombie" || o.animname == "astro_zombie";
}


// ignorable (optional, L60b): George / an astronaut asks: 200 units are enough (not on top of another special); the 600 rule
// kept the third astronaut out of Five's start room until 41 s and the second George until 54 s (r2h3, Hell)
special_spot_clear( zombies, origin, ignorable )
{
	r = 600;
	if ( IsDefined( ignorable ) && ignorable )
		r = 200;
	else if ( IsDefined( level.mod_special_node_spots ) && level.mod_special_node_spots )
		r = 400; // nightmare: still beyond a napalm explosion (250); 600 fits 2 spots in Five's start room (mc L60b: shrieker #1 at 43 s behind 2 napalms)
	for ( j = 0; j < zombies.size; j++ )
	{
		o = zombies[j];
		if ( IsAlive( o ) && IsDefined( o.animname ) && o.animname != "zombie" && !special_spot_ignores( o ) && DistanceSquared( o.origin, origin ) < r * r )
			return false;
	}
	// a special spawned this frame has no animname yet (L33 shot2: napalm #1 and sonic #1 again shared a spot), so spots
	// handed out in the last 10 s count as specials too
	if ( IsDefined( level.mod_special_picks ) )
	{
		for ( j = 0; j < level.mod_special_picks.size; j++ )
		{
			pick = level.mod_special_picks[j];
			if ( IsDefined( pick.ignorable ) && pick.ignorable )
				continue;
			if ( GetTime() - pick.time < 10000 && DistanceSquared( pick.origin, origin ) < r * r )
				return false;
		}
	}
	return true;
}

// ignorable (optional): the caller is George or an astronaut (special_spot_ignores): its pick does not block the other
// specials' spots either (L60b r2h2: George and astronaut picks at 9-31 s kept napalm/shrieker out of Five's start room
// until 32-41 s)
special_spawn_point( ignorable )
{
	zombies = GetAiSpeciesArray( "axis", "all" );
	for ( i = 0; i < zombies.size; i++ )
	{
		z = zombies[i];
		if ( IsDefined( z.animname ) && z.animname == "zombie" && IsDefined( z.completed_emerging_into_playable_area ) && z.completed_emerging_into_playable_area && special_spot_clear( zombies, z.origin, ignorable ) )
		{
			point = SpawnStruct();
			point.origin = z.origin;
			point.angles = z.angles;
			point.time = GetTime();
			point.ignorable = IsDefined( ignorable ) && ignorable;
			special_pick_add( point );
			return point;
		}
	}
	if ( IsDefined( level.mod_special_node_spots ) && level.mod_special_node_spots )
		return special_node_spawn_point( ignorable );
	return undefined;
}


// nightmare (node spots): napalm and shrieker share the spots, so the second napalm waits until the first shrieker is in
// (and the other way round), at most 30 s; horde unchanged
special_first_wave_wait( alive, otherSpawned )
{
	if ( !IsDefined( level.mod_special_node_spots ) || !level.mod_special_node_spots )
		return false;
	if ( !IsDefined( level.mod_first_wave_start ) )
		level.mod_first_wave_start = GetTime();
	return alive >= 1 && ( !IsDefined( otherSpawned ) || otherSpawned == 0 ) && GetTime() - level.mod_first_wave_start < 30000;
}


special_pick_add( point )
{
	if ( !IsDefined( level.mod_special_picks ) || level.mod_special_picks.size >= 8 )
		level.mod_special_picks = [];
	level.mod_special_picks[level.mod_special_picks.size] = point;
}


// L60b (nightmare round 2): every special on the first round. On Five's round 1 the first zombie is through a window
// only at ~45 s level time (L60c: George 45150, astronaut 55150), so with level.mod_special_node_spots set (nightmare
// only; horde alone keeps the zombie spots above) a special may also take a path node 500-1400 units from a player,
// inside an enabled zone (retail check_point_in_active_zone, _zombiemode_utility.gsc:200), once zombies are spawning.
// Traversal begin/end nodes (window barriers) are skipped. At most 16 nodes are tested per call (each test spawns and
// deletes one script_origin).
// L61s2: minDist/maxDist (optional; default 500/1400): the astronaut's test dvar nightmare_test_astro_near asks 150/450
special_node_spawn_point( ignorable, minDist, maxDist )
{
	if ( !IsDefined( minDist ) )
		minDist = 500;
	if ( !IsDefined( maxDist ) )
		maxDist = 1400;
	if ( !common_scripts\utility::flag( "begin_spawning" ) )
		return undefined;
	players = maps\_utility::get_players();
	if ( players.size == 0 )
		return undefined;
	player = players[RandomInt( players.size )];
	nodes = GetAnyNodeArray( player.origin, maxDist );
	if ( !IsDefined( nodes ) || nodes.size == 0 )
		return undefined;
	zombies = GetAiSpeciesArray( "axis", "all" );
	tested = 0;
	start = RandomInt( nodes.size );
	for ( k = 0; k < nodes.size && tested < 16; k++ )
	{
		node = nodes[( start + k ) % nodes.size];
		if ( IsDefined( node.type ) && ( node.type == "Begin" || node.type == "End" ) )
			continue;
		near = false;
		for ( j = 0; j < players.size; j++ )
		{
			if ( DistanceSquared( players[j].origin, node.origin ) < minDist * minDist )
				near = true;
		}
		if ( near || !special_spot_clear( zombies, node.origin, ignorable ) )
			continue;
		tested++;
		if ( !maps\_zombiemode_utility::check_point_in_active_zone( node.origin ) )
			continue;
		point = SpawnStruct();
		point.origin = node.origin;
		point.angles = ( 0, VectorToAngles( player.origin - node.origin )[1], 0 );
		point.time = GetTime();
		point.ignorable = IsDefined( ignorable ) && ignorable;
		special_pick_add( point );
		return point;
	}
	return undefined;
}
