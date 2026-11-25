#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

// HORDE MOD - not part of the original game (L52). User: in big crowds zombies get stuck on each other anywhere on the
// map. Retail steering (Actor_MoveAwayNoWorse, team-move dodge / pile-up) makes a packed zombie wait for its neighbours,
// and in a crowd they all wait for each other. Backup fix: a zombie that is stuck gets no zombie-vs-zombie collision
// (the noactorcollision actor field, see _horde_windows.gsc horde_nocol_set) for a short time, then it is back on.
// "Stuck" here: out of the window phase (not ignoreall), alive with legs, has an enemy, 96+ units from every player,
// not in melee (a.script zombie_combat), not in a traverse, not rising, and moved < 16 units for 1 s (fix) / 2 s (log).
// Dvars:
//   horde_unjam      1 (default) stuck 1 s -> collision off 1.5 s; again while still stuck, at most 3 times in a row,
//                    then 5 s rest. 0 = off (retail steering; the log below still runs, for before/after runs)
//   horde_unjam_log  1 (default) logs "horde_uj: ..." every 5 s and one line per stuck spell / zombie reaching a player;
//                    2 also lists where stuck zombies are (neighbours within 40 units, path, speed); 0 = off
// One thread for all zombies: every 0.1 s it visits a fifth of them (each zombie every 0.5 s).

init()
{
	if ( GetDvar( #"horde_unjam" ) == "" )
	{
		SetDvar( "horde_unjam", "1" );
	}
	if ( GetDvar( #"horde_unjam_log" ) == "" )
	{
		SetDvar( "horde_unjam_log", "1" );
	}
	if ( GetDvar( #"horde_unjam_radius" ) == "" )
	{
		SetDvar( "horde_unjam_radius", "250" );
	}
	if ( GetDvar( #"horde_unjam_exit" ) == "" )
	{
		SetDvar( "horde_unjam_exit", "2" );
	}
	level.horde_uj_ghosts = 0;
	level.horde_uj_exits = [];
	level.horde_uj_inner = [];
	level.horde_uj_capped = 0;
	level thread horde_unjam_think();
}


horde_uj_log( msg )
{
	LogPrint( "horde_uj: " + msg + "\n" );
}


horde_unjam_think()
{
	flag_wait( "all_players_spawned" );
	last_log = GetTime();
	for ( ;; )
	{
		zombies = GetAiSpeciesArray( "axis", "all" );
		players = get_players();
		on = ( GetDvarInt( #"horde_unjam" ) != 0 );
		logging = GetDvarInt( #"horde_unjam_log" );
		// ring radius R: a zombie is ghosted only while R+ from every player, so the crowd packs around the player
		// with collision on (a ring) instead of ghosts stacking inside each other at the player
		r = GetDvarInt( #"horde_unjam_radius" );
		level.horde_uj_r2 = r * r;
		level.horde_uj_exit = GetDvarInt( #"horde_unjam_exit" );
		level.horde_uj_zombies = zombies;
		cand = 0;
		stuck1 = 0;
		stuck2 = 0;
		ghost = 0;
		// every zombie out of the window phase by nearest-player distance band (<96, <256, <512, more):
		// all / still 2 s (any reason) / in zombie_combat
		band_all = [];
		band_still = [];
		band_combat = [];
		// zombies closer to a player than 32 * (k + 1), for the "interior full" test of horde_unjam_exit 2
		bins = [];
		for ( k = 0; k < 16; k++ )
		{
			bins[k] = 0;
		}
		for ( b = 0; b < 4; b++ )
		{
			band_all[b] = 0;
			band_still[b] = 0;
			band_combat[b] = 0;
		}
		for ( slice = 0; slice < 5; slice++ )
		{
			now = GetTime();
			for ( i = slice; i < zombies.size; i += 5 )
			{
				z = zombies[i];
				if ( !IsDefined( z ) || !IsAlive( z ) || ( IsDefined( z.animname ) && z.animname != "zombie" ) )
				{
					continue;
				}
				still = horde_uj_visit( z, now, on, players, logging );
				if ( still >= 0 )
				{
					cand++;
				}
				if ( still >= 1000 )
				{
					stuck1++;
				}
				if ( still >= 2000 )
				{
					stuck2++;
				}
				if ( IsDefined( z.horde_uj_ghost_end ) )
				{
					ghost++;
				}
				if ( !z.ignoreall )
				{
					b = horde_uj_band( z.horde_uj_d );
					band_all[b]++;
					for ( k = Int( Sqrt( z.horde_uj_d ) / 32 ); k < 16; k++ )
					{
						bins[k]++;
					}
					if ( now - z.horde_uj_any_time >= 2000 )
					{
						band_still[b]++;
					}
					if ( IsDefined( z.a ) && IsDefined( z.a.script ) && z.a.script == "zombie_combat" )
					{
						band_combat[b]++;
					}
				}
			}
			horde_uj_inner_pass( players, on );
			wait( 0.1 );
		}
		level.horde_uj_bins = bins;
		now = GetTime();
		if ( logging != 0 && now - last_log >= 5000 )
		{
			horde_uj_log( "on " + on + " alive " + zombies.size + " cand " + cand + " stuck1s " + stuck1 + " stuck2s " + stuck2 + " ghost " + ghost + " ghosts " + level.horde_uj_ghosts + " capped " + level.horde_uj_capped );
			horde_uj_log( "bands <96/<256/<512/more all " + band_all[0] + "/" + band_all[1] + "/" + band_all[2] + "/" + band_all[3] + " still " + band_still[0] + "/" + band_still[1] + "/" + band_still[2] + "/" + band_still[3] + " combat " + band_combat[0] + "/" + band_combat[1] + "/" + band_combat[2] + "/" + band_combat[3] );
			last_log = now;
			horde_uj_log( "exits timer " + horde_uj_exit_n( "timer" ) + " free " + horde_uj_exit_n( "free" ) + " at96ov " + horde_uj_exit_n( "at96ov" ) + " capov " + horde_uj_exit_n( "capov" ) + " fullov " + horde_uj_exit_n( "fullov" ) );
			if ( logging > 1 )
			{
				horde_uj_log_overlap( zombies );
				horde_uj_log_stuck( zombies, players, now );
			}
		}
	}
}


// nearest player distance, squared
horde_uj_player_dist( z, players )
{
	d = 1000000000;
	for ( p = 0; p < players.size; p++ )
	{
		dp = DistanceSquared( z.origin, players[p].origin );
		if ( dp < d )
		{
			d = dp;
		}
	}
	return d;
}


horde_uj_band( d )
{
	if ( d < 96 * 96 )
	{
		return 0;
	}
	if ( d < 256 * 256 )
	{
		return 1;
	}
	if ( d < 512 * 512 )
	{
		return 2;
	}
	return 3;
}


// may this zombie count as stuck (it wants to move and nothing retail holds it)
horde_uj_can_stick( z, d )
{
	if ( z.ignoreall || is_true( z.in_the_ground ) || is_true( z.is_traversing ) || is_true( z.nuked ) || is_true( z.marked_for_death ) )
	{
		return false;
	}
	if ( IsDefined( z.has_legs ) && !z.has_legs )
	{
		return false;
	}
	if ( !IsDefined( z.favoriteenemy ) && !IsDefined( z.enemy ) )
	{
		return false;
	}
	if ( IsDefined( z.a ) && IsDefined( z.a.script ) && z.a.script == "zombie_combat" )
	{
		return false;
	}
	return d > 96 * 96;
}


// one zombie: returns how long (ms) it has been stuck, -1 when it cannot count as stuck
horde_uj_visit( z, now, on, players, logging )
{
	d = horde_uj_player_dist( z, players );
	// collision back on after 1.5 s, or inside the ring radius R: ghosts walking into the melee ring stacked ~400 zombies
	// inside each other there (Kino 600: 88k traces / frame, server frame 45 -> 65 ms). horde_unjam_exit 1: a ghost whose
	// time is up or that crosses into R keeps ghosting while it overlaps another zombie, at most 3 s past its end time,
	// never within 96 of a player. level.horde_uj_exits counts why ghosts end (timer / free / at96 / cap; *ov = overlapping)
	if ( IsDefined( z.horde_uj_ghost_end ) && ( now >= z.horde_uj_ghost_end || !on || d < level.horde_uj_r2 ) )
	{
		why = "timer";
		if ( on && level.horde_uj_exit == 2 )
		{
			// exit 2: every ghost is ended by horde_uj_inner_pass (every 0.1 s)
			why = "keep";
		}
		else if ( on && level.horde_uj_exit != 0 )
		{
			why = "free";
			if ( horde_uj_overlaps( z ) )
			{
				why = "keep";
				if ( d < 96 * 96 )
				{
					why = "at96ov";
				}
				else if ( now >= z.horde_uj_ghost_end + 3000 )
				{
					why = "capov";
				}
			}
		}
		if ( why != "keep" )
		{
			horde_uj_ghost_off( z, why );
		}
	}
	z.horde_uj_d = d;
	// stillness for any reason (the band log)
	if ( !IsDefined( z.horde_uj_any ) || DistanceSquared( z.origin, z.horde_uj_any ) > 16 * 16 )
	{
		z.horde_uj_any = z.origin;
		z.horde_uj_any_time = now;
	}
	if ( !z.ignoreall && !IsDefined( z.horde_uj_inside_time ) )
	{
		z.horde_uj_inside_time = now;
	}
	// time from out of the window phase to 128 units from a player
	if ( IsDefined( z.horde_uj_inside_time ) && !is_true( z.horde_uj_reached ) && d < 128 * 128 )
	{
		z.horde_uj_reached = true;
		if ( logging != 0 )
		{
			horde_uj_log( "reach " + ( now - z.horde_uj_inside_time ) );
		}
	}
	if ( !horde_uj_can_stick( z, d ) )
	{
		horde_uj_spell_end( z, now, "cond", logging );
		z.horde_uj_anchor = undefined;
		return -1;
	}
	if ( !IsDefined( z.horde_uj_anchor ) || DistanceSquared( z.origin, z.horde_uj_anchor ) > 16 * 16 )
	{
		horde_uj_spell_end( z, now, "moved", logging );
		z.horde_uj_anchor = z.origin;
		z.horde_uj_anchor_time = now;
		return 0;
	}
	still = now - z.horde_uj_anchor_time;
	// exit 2: not when the disc around the player inside this zombie is already packed (it is queued in the crowd)
	if ( on && still >= 1000 && d >= level.horde_uj_r2 && !( level.horde_uj_exit == 2 && horde_uj_full( d ) ) && !IsDefined( z.horde_uj_ghost_end ) && ( !IsDefined( z.horde_uj_rest_end ) || now >= z.horde_uj_rest_end ) )
	{
		if ( !IsDefined( z.horde_uj_count ) )
		{
			z.horde_uj_count = 0;
		}
		if ( z.horde_uj_count >= 3 )
		{
			// ghosted 3 times in a row and still here: probably a wall / prop, not the crowd. Rest 5 s.
			z.horde_uj_count = 0;
			z.horde_uj_rest_end = now + 5000;
			level.horde_uj_capped++;
		}
		else
		{
			z.horde_uj_count++;
			z.horde_uj_ghost_end = now + 1500;
			level.horde_uj_ghosts++;
			maps\horde\_horde_windows::horde_nocol_set( z, 2, true );
			if ( level.horde_uj_exit == 2 )
			{
				level.horde_uj_inner[level.horde_uj_inner.size] = z;
			}
		}
	}
	return still;
}


horde_uj_exit_n( why )
{
	if ( IsDefined( level.horde_uj_exits[why] ) )
	{
		return level.horde_uj_exits[why];
	}
	return 0;
}


// horde_unjam_log 2: zombies within 96 / 256 of a player, and how many of them have another zombie within 16 / 32 units
horde_uj_log_overlap( zombies )
{
	n = [];
	for ( k = 0; k < 6; k++ )
	{
		n[k] = 0;
	}
	for ( i = 0; i < zombies.size; i++ )
	{
		z = zombies[i];
		if ( !IsDefined( z ) || !IsAlive( z ) || !IsDefined( z.horde_uj_d ) || z.horde_uj_d >= 256 * 256 )
		{
			continue;
		}
		b = 0;
		if ( z.horde_uj_d >= 96 * 96 )
		{
			b = 3;
		}
		m = z bo1_mod_nearestactordist();
		n[b]++;
		if ( m < 16 )
		{
			n[b + 1]++;
		}
		if ( m < 32 )
		{
			n[b + 2]++;
		}
	}
	horde_uj_log( "overlap <96 n " + n[0] + " within16 " + n[1] + " within32 " + n[2] + " <256 n " + n[3] + " within16 " + n[4] + " within32 " + n[5] );
}


horde_uj_ghost_off( z, why )
{
	if ( !IsDefined( level.horde_uj_exits[why] ) )
	{
		level.horde_uj_exits[why] = 0;
	}
	level.horde_uj_exits[why]++;
	z.horde_uj_ghost_end = undefined;
	maps\horde\_horde_windows::horde_nocol_set( z, 2, false );
}


// another live zombie closer than two actor radii (15 + 15, plus a little); native loop (g_scr_sp_ai.cpp)
horde_uj_overlaps( z )
{
	return z bo1_mod_nearestactordist() < 32;
}


// is the disc of radius sqrt( d ) around the player already packed? zombies closer than it (last sweep's bins) against
// d / 300 (hex packing of 30-unit zombies is d / 248 per unit^2 of pi r^2; a crowd does not pack perfectly)
horde_uj_full( d )
{
	if ( !IsDefined( level.horde_uj_bins ) )
	{
		return false;
	}
	k = Int( Sqrt( d ) / 32 );
	if ( k < 1 )
	{
		return false;
	}
	if ( k > 16 )
	{
		k = 16;
	}
	r = 32 * k;
	return level.horde_uj_bins[k - 1] >= r * r / 300;
}


// horde_unjam_exit 2, every 0.1 s, every ghost: collision back on where it overlaps no zombie once its time is up or it is
// inside the ring radius R; while it overlaps, it goes on until within 96 of a player, or the disc inside it is already
// packed (going deeper would only stack it), or 3 s after its time; so the crowd packs from the player outward
horde_uj_inner_pass( players, on )
{
	if ( !IsDefined( level.horde_uj_inner ) )
	{
		level.horde_uj_inner = [];
	}
	now = GetTime();
	keep = [];
	for ( i = 0; i < level.horde_uj_inner.size; i++ )
	{
		z = level.horde_uj_inner[i];
		if ( !IsDefined( z ) )
		{
			continue;
		}
		if ( !IsAlive( z ) || !IsDefined( z.horde_uj_ghost_end ) )
		{
			continue;
		}
		d = horde_uj_player_dist( z, players );
		why = "keep";
		if ( !on )
		{
			why = "timer";
		}
		else if ( !horde_uj_overlaps( z ) )
		{
			if ( d < level.horde_uj_r2 )
			{
				why = "free";
			}
			else if ( now >= z.horde_uj_ghost_end )
			{
				why = "timer";
			}
		}
		else if ( d < 96 * 96 )
		{
			why = "at96ov";
		}
		else if ( horde_uj_full( d ) )
		{
			why = "fullov";
		}
		else if ( now >= z.horde_uj_ghost_end + 3000 )
		{
			why = "capov";
		}
		if ( why == "keep" )
		{
			keep[keep.size] = z;
		}
		else
		{
			horde_uj_ghost_off( z, why );
		}
	}
	level.horde_uj_inner = keep;
}


// a stuck spell (1 s+ in one place) is over: log its length and why ("moved" 16 units, or "cond": melee / near a player /
// traverse / ...); a zombie that dies while stuck is not logged
horde_uj_spell_end( z, now, why, logging )
{
	if ( IsDefined( z.horde_uj_anchor ) && now - z.horde_uj_anchor_time >= 1000 && logging != 0 )
	{
		horde_uj_log( "spell " + ( now - z.horde_uj_anchor_time ) + " " + why );
	}
	if ( why == "moved" )
	{
		z.horde_uj_count = 0;
	}
}


// horde_unjam_log 2: where the stuck zombies are (up to 12), and what is around them
horde_uj_log_stuck( zombies, players, now )
{
	if ( players.size > 0 )
	{
		horde_uj_log( "player at " + players[0].origin );
	}
	n = 0;
	for ( i = 0; i < zombies.size && n < 12; i++ )
	{
		z = zombies[i];
		if ( !IsDefined( z ) || !IsAlive( z ) || !IsDefined( z.horde_uj_anchor ) || now - z.horde_uj_anchor_time < 2000 )
		{
			continue;
		}
		n++;
		near = 0;
		for ( j = 0; j < zombies.size; j++ )
		{
			if ( j != i && IsDefined( zombies[j] ) && IsAlive( zombies[j] ) && DistanceSquared( z.origin, zombies[j].origin ) < 40 * 40 )
			{
				near++;
			}
		}
		pd = Int( Sqrt( horde_uj_player_dist( z, players ) ) );
		script = "?";
		if ( IsDefined( z.a ) && IsDefined( z.a.script ) )
		{
			script = z.a.script;
		}
		goal = "none";
		if ( IsDefined( z.pathgoalpos ) )
		{
			goal = "" + Int( Distance( z.origin, z.pathgoalpos ) );
		}
		horde_uj_log( "stuck " + ( now - z.horde_uj_anchor_time ) + " at " + z.origin + " player " + pd + " near40 " + near + " script " + script + " pathgoal " + goal + " pathlen " + Int( z GetPathLength() ) + " speed " + Int( Length( z GetAiVelocity() ) ) + " ghost " + IsDefined( z.horde_uj_ghost_end ) + " nocol " + z.noactorcollision );
	}
}
