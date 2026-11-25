#include maps\_utility;
#include common_scripts\utility;

// mod: shared enemy module (mods/_stack) - not part of the original game. The perk-machine attack spots Ascension's space
// monkeys need (retail maps\_zombiemode_ai_monkey monkey_zombie_get_perk_pos: getstructarray( machine.target ), three
// structs, script_int 1 left / 2 front / 3 right). Only zombie_cosmodrome's map has them; this file makes them on another
// map from offsets in the machine's frame measured on zombie_cosmodrome (log_perk_spots, +set nightmare_log_perk_spots 1).
// Links no zone script, so it compiles on any map.

// the perk machine model a zombie_vending trigger targets (retail monkey_zombie_perk_init: the first script_model)
perk_machine( trig )
{
	if ( !IsDefined( trig.target ) )
		return undefined;
	targets = GetEntArray( trig.target, "targetname" );
	for ( i = 0; i < targets.size; i++ )
	{
		if ( targets[i].classname == "script_model" )
			return targets[i];
	}
	return undefined;
}


local_offset( machine, pos )
{
	d = pos - machine.origin;
	f = AnglesToForward( ( 0, machine.angles[1], 0 ) );
	r = AnglesToRight( ( 0, machine.angles[1], 0 ) );
	return ( Int( VectorDot( d, f ) ), Int( VectorDot( d, r ) ), Int( d[2] ) );
}


yaw_delta( a, b )
{
	d = a - b;
	while ( d > 180 )
		d -= 360;
	while ( d <= -180 )
		d += 360;
	return Int( d );
}


// measurement: each machine and its attack spots in the machine's frame (forward, right, up), to the game log
log_perk_spots()
{
	wait 5;
	trigs = GetEntArray( "zombie_vending", "targetname" );
	LogPrint( "perkspots: " + trigs.size + " vending triggers on " + GetDvar( #"mapname" ) + "\n" );
	for ( i = 0; i < trigs.size; i++ )
	{
		machine = perk_machine( trigs[i] );
		if ( !IsDefined( machine ) )
		{
			LogPrint( "perkspots: " + trigs[i].script_noteworthy + " no machine\n" );
			continue;
		}
		target = "none";
		if ( IsDefined( machine.target ) )
			target = machine.target;
		LogPrint( "perkspots: " + trigs[i].script_noteworthy + " model " + machine.model + " at " + machine.origin + " angles " + machine.angles + " trigger " + local_offset( machine, trigs[i].origin ) + " target " + target + "\n" );
		if ( !IsDefined( machine.target ) )
			continue;
		spots = getstructarray( machine.target, "targetname" );
		if ( !IsDefined( spots ) )
			continue;
		for ( j = 0; j < spots.size; j++ )
		{
			n = -1;
			if ( IsDefined( spots[j].script_int ) )
				n = spots[j].script_int;
			LogPrint( "perkspots:   spot " + n + " local " + local_offset( machine, spots[j].origin ) + " yaw " + yaw_delta( spots[j].angles[1], machine.angles[1] ) + "\n" );
		}
	}
}


// the attack spots zombie_cosmodrome's map places at each machine, in the machine's frame (forward, right, up; the machine's
// front is its right side, each spot faces it at the machine's yaw + 90), measured with log_perk_spots (L62):
// revive (-35,38) (0,52) (37,38); sleight (-36,29) (0,40) (38,30); marathon (-35,35) (0,44) (36,34); jugg (-26,30) (0,45)
// (27,30); nuke (-24,30) (0,41) (24,31); three_gun (-39,22) (0,40) (40,22). Other machines (Kino's double tap): the mean.
spot_offset( model, n )
{
	switch ( model )
	{
	case "zombie_vending_revive":
	case "zombie_vending_revive_on":
		a = ( -35, 38, 1 );
		b = ( 0, 52, 1 );
		c = ( 37, 38, 1 );
		break;
	case "zombie_vending_sleight":
	case "zombie_vending_sleight_on":
		a = ( -36, 29, 1 );
		b = ( 0, 40, 1 );
		c = ( 38, 30, 1 );
		break;
	case "zombie_vending_jugg":
	case "zombie_vending_jugg_on":
		a = ( -26, 30, 1 );
		b = ( 0, 45, 1 );
		c = ( 27, 30, 1 );
		break;
	default:
		a = ( -33, 32, 1 );
		b = ( 0, 44, 1 );
		c = ( 34, 32, 1 );
		break;
	}
	if ( n == 1 )
		return a;
	if ( n == 2 )
		return b;
	return c;
}


// make the three attack spots (script_int 1-3) for each perk machine that has none (retail monkey_zombie_get_perk_pos
// reads getstructarray( machine.target ))
add_perk_spots()
{
	trigs = GetEntArray( "zombie_vending", "targetname" );
	made = 0;
	for ( i = 0; i < trigs.size; i++ )
	{
		machine = perk_machine( trigs[i] );
		if ( !IsDefined( machine ) )
			continue;
		if ( IsDefined( machine.target ) )
		{
			spots = getstructarray( machine.target, "targetname" );
			if ( IsDefined( spots ) && spots.size >= 3 )
				continue;
			LogPrint( "modenemy: monkey: machine " + trigs[i].script_noteworthy + " target " + machine.target + " replaced by attack spots\n" );
		}
		name = "mod_monkey_perk_spots_" + i;
		machine.target = name;
		level.struct_class_names["targetname"][name] = [];
		f = AnglesToForward( ( 0, machine.angles[1], 0 ) );
		r = AnglesToRight( ( 0, machine.angles[1], 0 ) );
		for ( n = 1; n <= 3; n++ )
		{
			off = spot_offset( machine.model, n );
			s = SpawnStruct();
			s.origin = machine.origin + vector_scale( f, off[0] ) + vector_scale( r, off[1] ) + ( 0, 0, off[2] );
			s.angles = ( 0, machine.angles[1] + 90, 0 );
			s.script_int = n;
			s.targetname = name;
			level.struct_class_names["targetname"][name][n - 1] = s;
		}
		made++;
	}
	LogPrint( "modenemy: monkey: attack spots made for " + made + " of " + trigs.size + " perk machines\n" );
}
