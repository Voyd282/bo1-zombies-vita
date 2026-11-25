#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

// HORDE MOD - not part of the original game. Keeps a big horde flowing through the barrier windows.
// Retail (maps\_zombiemode_spawner): a zombie picks a window (first_node, one of its 3 closest entrance_nodes), walks
// there (goalradius 128), then tear_into_building() loops: take one of the window's 3 attack_spots, or taunt and
// retry every 0.5 s. Even when every board is down it must still win a spot, walk to it with goalradius 2 and get
// "goal" before it climbs through. With 100+ zombies the crowd at a window blocks that 2-unit goal, the spot holders
// never get "goal", and everyone behind them waits for ever.
// Dvars:
//   horde_window_fix  bits, 0 = off. Acts only above retail's limit (level.zombie_ai_limit > 24):
//                     1 (the user's) no zombie-vs-zombie steering from the window until through it (horde_win_nocol)
//                     2 the script helpers below
//                     a) boards all down: free the spots and send "goal" to a spot holder at the window, so it
//                        takes retail's own "all chunks destroyed" exit (tear_into_building returns, find_flesh)
//                     b) boards up, waiting for a spot for 4 s at a window with 6+ waiting: move it to the least
//                        crowded of its own retail entrance_nodes (a window with boards down counts as empty)
//                     c) not moved 48 units in 20 s and 800+ units from every player: kill it and put it back in the
//                        spawn queue (retail round_spawn_failsafe does this after 30 s near players too; the mod
//                        turns that off above 24 because it culled the crowd around the player)
//   horde_window_log  1 (default) logs "horde_win: ..." every 5 s (also with the fix off, for before/after runs)

init()
{
	if ( GetDvar( #"horde_window_fix" ) == "" )
	{
		SetDvar( "horde_window_fix", "1" );
	}
	if ( GetDvar( #"horde_window_log" ) == "" )
	{
		SetDvar( "horde_window_log", "1" );
	}
	level.horde_win_entered = 0;
	level.horde_win_pushed = 0;
	level.horde_win_redirected = 0;
	level.horde_win_recycled = 0;
	level thread horde_windows_think();
}


horde_win_log( msg )
{
	LogPrint( "horde_win: " + msg + "\n" );
}


horde_win_is_zombie( z )
{
	if ( !IsAlive( z ) )
	{
		return false;
	}
	if ( IsDefined( z.animname ) && z.animname != "zombie" )
	{
		return false;
	}
	return true;
}


horde_win_boards_down( node )
{
	if ( !IsDefined( node ) || !IsDefined( node.barrier_chunks ) || !IsDefined( node.target ) )
	{
		return false;
	}
	return all_chunks_destroyed( node.barrier_chunks );
}


// zombies waiting at a window (got there, still ignoreall, i.e. tear_into_building has not returned)
horde_win_at_window( z )
{
	return z.ignoreall && is_true( z.got_to_entrance ) && IsDefined( z.first_node );
}


horde_windows_think()
{
	flag_wait( "all_players_spawned" );
	last_log = GetTime();
	for ( ;; )
	{
		wait( 0.5 );
		now = GetTime();
		// horde_window_fix bits: 1 no zombie-vs-zombie steering at the window, 2 the script helpers a) b) c)
		fix = GetDvarInt( #"horde_window_fix" );
		if ( level.zombie_ai_limit <= 24 )
		{
			fix = 0;
		}
		zombies = GetAiSpeciesArray( "axis", "all" );
		players = get_players();

		for ( i = 0; i < level.exterior_goals.size; i++ )
		{
			level.exterior_goals[i].horde_win_crowd = 0;
		}

		approach = 0;
		at_window = 0;
		inside = 0;
		stuck = 0;
		stuck_window = 0;
		near = 0;
		nocol = 0;
		for ( i = 0; i < zombies.size; i++ )
		{
			z = zombies[i];
			if ( !horde_win_is_zombie( z ) )
			{
				continue;
			}
			if ( !IsDefined( z.horde_win_anchor ) || DistanceSquared( z.origin, z.horde_win_anchor ) > 48 * 48 )
			{
				z.horde_win_anchor = z.origin;
				z.horde_win_anchor_time = now;
			}
			// nearest player, squared
			d = 1000000000;
			for ( p = 0; p < players.size; p++ )
			{
				dp = DistanceSquared( z.origin, players[p].origin );
				if ( dp < d )
				{
					d = dp;
				}
			}
			// stuck: not moved 48 units in 10 s while 400+ units from every player (the crowd packed around a
			// player does not move either; that is not a jam)
			is_stuck = ( now - z.horde_win_anchor_time >= 10000 && d > 400 * 400 );
			if ( is_stuck )
			{
				stuck++;
			}
			if ( horde_win_nocol( z, fix & 1, now ) )
			{
				nocol++;
			}
			if ( !z.ignoreall )
			{
				inside++;
				if ( !is_true( z.horde_win_inside ) )
				{
					z.horde_win_inside = true;
					z.horde_win_inside_time = now;
					level.horde_win_entered++;
				}
				if ( d < 200 * 200 )
				{
					near++;
				}
			}
			else if ( horde_win_at_window( z ) )
			{
				at_window++;
				if ( is_stuck )
				{
					stuck_window++;
				}
				if ( !IsDefined( z.attacking_node ) )
				{
					z.first_node.horde_win_crowd++;
				}
			}
			else
			{
				approach++;
			}
		}

		if ( fix & 2 )
		{
			for ( i = 0; i < zombies.size; i++ )
			{
				z = zombies[i];
				if ( !horde_win_is_zombie( z ) )
				{
					continue;
				}
				if ( horde_win_recycle( z, players, now ) )
				{
					continue;
				}
				if ( horde_win_at_window( z ) )
				{
					horde_win_window( z, now );
				}
			}
		}

		if ( GetDvarInt( #"horde_window_log" ) != 0 && now - last_log >= 5000 )
		{
			hits = 0;
			for ( p = 0; p < players.size; p++ )
			{
				if ( IsDefined( players[p].horde_hits ) )
				{
					hits += players[p].horde_hits;
				}
			}
			horde_win_log( "fix " + fix + " alive " + zombies.size + " approach " + approach + " window " + at_window + " inside " + inside + " stuckfar " + stuck + " stuckwin " + stuck_window + " near " + near + " nocol " + nocol + " entered " + level.horde_win_entered + " hits " + hits + " pushed " + level.horde_win_pushed + " redirected " + level.horde_win_redirected + " recycled " + level.horde_win_recycled );
			last_log = now;
			if ( GetDvarInt( #"horde_window_log" ) > 1 )
			{
				horde_win_log_stuck( zombies, players, now );
			}
		}
	}
}


// fix bit 1 (the user's): no zombie-vs-zombie steering (BO1Zombies actor field noactorcollision: no close-ent / move-away /
// team-move dodge or pile-up for or against this zombie) from the time it is at its window (got_to_entrance, or within
// 256 units of first_node) until it has played the window traverse (retail zombie_complete_emerging_into_playable_area),
// or 15 s after tear_into_building returned (a window without a traverse); then back on. Bullets still hit it.
horde_win_nocol( z, on, now )
{
	want = false;
	if ( on && IsDefined( z.first_node ) && !is_true( z.completed_emerging_into_playable_area ) )
	{
		if ( z.ignoreall )
		{
			want = ( is_true( z.got_to_entrance ) || DistanceSquared( z.origin, z.first_node.origin ) < 256 * 256 );
		}
		else
		{
			want = ( !IsDefined( z.horde_win_inside_time ) || now - z.horde_win_inside_time < 15000 );
		}
	}
	if ( want != is_true( z.horde_win_nocol ) )
	{
		z.horde_win_nocol = want;
		horde_nocol_set( z, 1, want );
	}
	return want;
}


// the one owner of noactorcollision in the horde mod: reason bits in z.horde_nocol_bits (1 window fix above, 2 unjam in
// _horde_unjam.gsc); the field is 1 while any bit is set, so neither fix turns the other's off
horde_nocol_set( z, bit, on )
{
	bits = 0;
	if ( IsDefined( z.horde_nocol_bits ) )
	{
		bits = z.horde_nocol_bits;
	}
	if ( on && ( bits & bit ) == 0 )
	{
		bits += bit;
	}
	else if ( !on && ( bits & bit ) != 0 )
	{
		bits -= bit;
	}
	z.horde_nocol_bits = bits;
	if ( bits != 0 )
	{
		z.noactorcollision = 1;
	}
	else
	{
		z.noactorcollision = 0;
	}
}


// horde_window_log 2: where the stuck zombies are (up to 12 lines)
horde_win_log_stuck( zombies, players, now )
{
	if ( players.size > 0 )
	{
		horde_win_log( "player at " + players[0].origin );
	}
	n = 0;
	for ( i = 0; i < zombies.size && n < 12; i++ )
	{
		z = zombies[i];
		if ( !horde_win_is_zombie( z ) || now - z.horde_win_anchor_time < 10000 || ( players.size > 0 && DistanceSquared( z.origin, players[0].origin ) < 400 * 400 ) )
		{
			continue;
		}
		msg = "stuck " + ( now - z.horde_win_anchor_time ) + " ms at " + z.origin + " ignoreall " + z.ignoreall + " got_to_entrance " + is_true( z.got_to_entrance ) + " emerged " + is_true( z.completed_emerging_into_playable_area );
		if ( players.size > 0 )
		{
			msg = msg + " dist " + Int( Distance( z.origin, players[0].origin ) );
		}
		if ( IsDefined( z.first_node ) )
		{
			msg = msg + " node " + z.first_node.origin + " boards_down " + horde_win_boards_down( z.first_node ) + " spot " + IsDefined( z.attacking_node );
		}
		horde_win_log( msg );
		n++;
	}
}


// a) and b) for a zombie inside retail tear_into_building()
horde_win_window( z, now )
{
	node = z.first_node;
	if ( horde_win_boards_down( node ) )
	{
		if ( !IsDefined( z.attacking_node ) )
		{
			// retail frees every spot itself once a holder sees the boards down (SP tear_into_building); do it now
			for ( s = 0; s < node.attack_spots_taken.size; s++ )
			{
				node.attack_spots_taken[s] = false;
			}
			return;
		}
		// holder still walking to its 2-unit spot through the crowd: end its waittill( "goal" ); retail then
		// sees all_chunks_destroyed, frees the spots and returns into find_flesh
		if ( DistanceSquared( z.origin, node.origin ) < 160 * 160 && ( !IsDefined( z.horde_win_push_time ) || now - z.horde_win_push_time >= 1000 ) )
		{
			z.horde_win_push_time = now;
			z notify( "goal" );
			level.horde_win_pushed++;
		}
		return;
	}

	// boards up and no spot: remember when it started waiting
	if ( IsDefined( z.attacking_node ) )
	{
		z.horde_win_wait_since = undefined;
		return;
	}
	if ( !IsDefined( z.horde_win_wait_since ) )
	{
		z.horde_win_wait_since = now;
		return;
	}
	if ( now - z.horde_win_wait_since < 4000 || node.horde_win_crowd < 6 || !IsDefined( z.entrance_nodes ) )
	{
		return;
	}
	best = undefined;
	best_crowd = node.horde_win_crowd - 3;
	for ( n = 0; n < z.entrance_nodes.size; n++ )
	{
		e = z.entrance_nodes[n];
		if ( !IsDefined( e ) || e == node || !IsDefined( e.attack_spots ) )
		{
			continue;
		}
		crowd = e.horde_win_crowd;
		if ( !IsDefined( crowd ) )
		{
			crowd = 0;
		}
		if ( horde_win_boards_down( e ) )
		{
			crowd = 0;
		}
		if ( crowd < best_crowd )
		{
			best = e;
			best_crowd = crowd;
		}
	}
	if ( !IsDefined( best ) )
	{
		return;
	}
	// retail's next tear_into_building() pass takes a spot at the new window and walks there
	node.horde_win_crowd--;
	if ( !IsDefined( best.horde_win_crowd ) )
	{
		best.horde_win_crowd = 0;
	}
	best.horde_win_crowd++;
	z.first_node = best;
	z.horde_win_wait_since = undefined;
	level.horde_win_redirected++;
}


// c) returns true when the zombie was recycled
horde_win_recycle( z, players, now )
{
	if ( now - z.horde_win_anchor_time < 20000 || is_true( z.marked_for_death ) || is_true( z.nuked ) )
	{
		return false;
	}
	if ( IsDefined( z.lastchunk_destroy_time ) && now - z.lastchunk_destroy_time < 8000 )
	{
		return false;
	}
	for ( p = 0; p < players.size; p++ )
	{
		if ( DistanceSquared( z.origin, players[p].origin ) < 800 * 800 )
		{
			return false;
		}
	}
	if ( flag( "dog_round" ) )
	{
		return false;
	}
	// retail round_spawn_failsafe: zombie_total++ then dodamage( health + 100 ); no power-up for this death
	level.zombie_total++;
	z.no_powerups = true;
	z.horde_win_recycled = true;
	level.horde_win_recycled++;
	z DoDamage( z.health + 100, ( 0, 0, 0 ) );
	return true;
}
