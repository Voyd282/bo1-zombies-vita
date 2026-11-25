#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

// SANDBOX MOD for Five (zombie_pentagon) - NOT part of the original game.
// Loaded only with +set fs_game mods/sandbox, through the map's retail FFOTD hook
// (mods/sandbox/maps/zombie_pentagon_ffotd.gsc). Without fs_game none of this exists.
//
// Every effect calls the retail scripts' own functions. Where a control does what a retail developer tool does,
// it follows maps\_zombiemode_devgui.gsc (that whole file is inside /# #/, so it is compiled out of retail builds
// and cannot be called from here); the function it follows is named in the comment.
//
// Dvars: sandbox_points (points given at start, default 500000; the points pad adds 100000), sandbox_round (target of the "go to round" pad,
// default 20), sandbox_selftest 1 (uses every pad once with player 0 and logs each result; for headless checks;
// 3 = the same, but only in the first game; 2 = screenshot views over the pad field).

main_start()
{
}


main_end()
{
	// still inside maps\zombie_pentagon::main(), before its first wait: precache is allowed here
	sandbox_precache();
	level.sandbox_round_active = false;
	level thread sandbox_round_tracker();
	level thread sandbox_main();
}


// round_think() notifies "start_of_round" right after it starts round_spawning() (which ends on "kill_round")
// and "end_of_round" when round_wait() returns; between the two a round can be ended the devgui way
sandbox_round_tracker()
{
	for ( ;; )
	{
		level waittill( "start_of_round" );
		level.sandbox_round_active = true;
		level waittill( "end_of_round" );
		level.sandbox_round_active = false;
	}
}


sandbox_log( msg )
{
	LogPrint( "sandbox: " + msg + "\n" );
}


sandbox_precache()
{
	level.sandbox_weapons = [];
	keys = GetArrayKeys( level.zombie_include_weapons );
	for ( i = 0; i < keys.size; i++ )
	{
		weapon = keys[i];
		if ( !IsDefined( level.zombie_weapons[weapon] ) )
		{
			continue;	// upgraded versions are included but are not zombie_weapons entries
		}
		level.sandbox_weapons[level.sandbox_weapons.size] = weapon;
		model = GetWeaponModel( weapon );
		if ( IsDefined( model ) && model != "" )
		{
			PrecacheModel( model );
		}
	}
}


sandbox_main()
{
	flag_wait( "all_players_connected" );
	flag_wait( "all_players_spawned" );
	wait( 1 );

	players = get_players();
	sandbox_log( "start, players " + players.size + ", round " + level.round_number );

	points = GetDvarInt( #"sandbox_points" );
	if ( points <= 0 )
	{
		points = 500000;
	}
	for ( i = 0; i < players.size; i++ )
	{
		players[i] maps\_zombiemode_score::add_to_player_score( points );
	}
	level thread sandbox_points_for_late_players( points );
	sandbox_log( "points +" + points + ", player 0 score " + players[0].score );

	sandbox_power_on( players[0] );
	sandbox_open_sesame( players[0] );
	sandbox_defcon5( players[0] );

	sandbox_build_pads();

	if ( GetDvarInt( #"sandbox_selftest" ) == 2 )
	{
		level thread sandbox_selftest_view( players[0] );
	}
	else if ( GetDvarInt( #"sandbox_selftest" ) > 0 )
	{
		level thread sandbox_selftest();
	}
}


sandbox_points_for_late_players( points )
{
	for ( ;; )
	{
		level waittill( "connected", player );
		player thread sandbox_points_on_spawn( points );
	}
}


sandbox_points_on_spawn( points )
{
	self endon( "disconnect" );
	self waittill( "spawned_player" );
	self maps\_zombiemode_score::add_to_player_score( points );
}


//-------------------------------------------------------------------------------
// setup: power, doors, DEFCON
//-------------------------------------------------------------------------------

// maps\zombie_pentagon::electric_switch() waits on this trigger; using it runs the retail power-on sequence
sandbox_power_on( player )
{
	if ( flag( "power_on" ) )
	{
		return;
	}
	trig = GetEnt( "use_elec_switch", "targetname" );
	if ( IsDefined( trig ) )
	{
		trig notify( "trigger", player );
	}
	flag_wait( "power_on" );
	sandbox_log( "power on" );
}


// follows zombie_devgui_open_sesame(): the door / debris triggers' own think functions open them
// (door_buy() and debris_think() skip the cost while zombie_unlock_all is set)
sandbox_open_sesame( player )
{
	SetDvar( "zombie_unlock_all", 1 );

	doors = GetEntArray( "zombie_door", "targetname" );
	for ( i = 0; i < doors.size; i++ )
	{
		doors[i] notify( "trigger", player );
		wait( .05 );
	}

	airlocks = GetEntArray( "zombie_airlock_buy", "targetname" );
	for ( i = 0; i < airlocks.size; i++ )
	{
		airlocks[i] notify( "trigger", player );
		wait( .05 );
	}

	debris = GetEntArray( "zombie_debris", "targetname" );
	for ( i = 0; i < debris.size; i++ )
	{
		debris[i] notify( "trigger", player );
		wait( .05 );
	}

	wait( 1 );
	SetDvar( "zombie_unlock_all", 0 );
	sandbox_log( "opened doors " + doors.size + ", airlocks " + airlocks.size + ", debris " + debris.size );
}


// uses each DEFCON switch (maps\zombie_pentagon_teleporter::defcon_sign_setup) until DEFCON 5:
// the portals then lead to the Pack-a-Punch room, and the retail countdown resets it as usual
sandbox_defcon5( player )
{
	sandbox_power_on( player );
	if ( flag( "defcon_active" ) )
	{
		sandbox_log( "defcon already 5" );
		return;
	}
	switches = GetEntArray( "punch_switch", "targetname" );
	for ( i = 0; i < switches.size && level.defcon_level < 5; i++ )
	{
		switches[i] notify( "trigger", player );
		wait( .5 );
	}
	sandbox_log( "defcon " + level.defcon_level + " (switches " + switches.size + ")" );
}


//-------------------------------------------------------------------------------
// pads
//-------------------------------------------------------------------------------

sandbox_add_pad( label, hint, model, func, arg )
{
	def = SpawnStruct();
	def.label = label;
	def.hint = hint;
	def.model = model;
	def.func = func;
	def.arg = arg;
	level.sandbox_defs[level.sandbox_defs.size] = def;
}


sandbox_build_pads()
{
	level.sandbox_defs = [];
	level.sandbox_pads = [];

	// controls
	sandbox_add_pad( "defcon5", "Press ^3[{+activate}]^7 for DEFCON 5 (portals lead to Pack-a-Punch)", "zombie_bomb", ::pad_defcon5 );
	sandbox_add_pad( "pack", "Press ^3[{+activate}]^7 to Pack-a-Punch your weapon", "zombie_bomb", ::pad_pack_current_weapon );
	sandbox_add_pad( "points", "Press ^3[{+activate}]^7 for 100000 points", "zombie_x2_icon", ::pad_points );
	sandbox_add_pad( "ammo", "Press ^3[{+activate}]^7 to refill ammo", "zombie_ammocan", ::pad_refill_ammo );
	sandbox_add_pad( "god", "Press ^3[{+activate}]^7 to toggle god mode", "zombie_skull", ::pad_god );
	sandbox_add_pad( "spawning", "Press ^3[{+activate}]^7 to toggle zombie spawning", "zombie_skull", ::pad_toggle_spawning );
	sandbox_add_pad( "next_round", "Press ^3[{+activate}]^7 for the next round", "zombie_bomb", ::pad_next_round );
	sandbox_add_pad( "set_round", "Press ^3[{+activate}]^7 to go to round sandbox_round (default 20)", "zombie_bomb", ::pad_set_round );
	sandbox_add_pad( "thief", "Press ^3[{+activate}]^7 to make the next round a thief round", "zombie_bomb", ::pad_thief_round );
	sandbox_add_pad( "crawler", "Press ^3[{+activate}]^7 to spawn a crawler", "zombie_skull", ::pad_spawn_crawler );
	sandbox_add_pad( "doors", "Press ^3[{+activate}]^7 to open every door and debris", "zombie_bomb", ::pad_open_sesame );

	// perks: each pad uses that perk's machine trigger (retail vending_trigger_think: cost, bottle, perk)
	vending = GetEntArray( "zombie_vending", "targetname" );
	for ( i = 0; i < vending.size; i++ )
	{
		sandbox_add_pad( "perk " + vending[i].script_noteworthy, "Press ^3[{+activate}]^7 to buy this perk (uses its machine)", sandbox_perk_model( vending[i].script_noteworthy ), ::pad_perk, vending[i] );
	}

	// power-ups: retail specific_powerup_drop()
	keys = GetArrayKeys( level.zombie_include_powerups );
	for ( i = 0; i < keys.size; i++ )
	{
		model = "zombie_bomb";
		if ( IsDefined( level.zombie_powerups[keys[i]] ) && IsDefined( level.zombie_powerups[keys[i]].model_name ) )
		{
			model = level.zombie_powerups[keys[i]].model_name;
		}
		sandbox_add_pad( "powerup " + keys[i], "Press ^3[{+activate}]^7 to drop this power-up", model, ::pad_powerup, keys[i] );
	}

	// weapons: every wall and box weapon, given with retail weapon_give()
	for ( i = 0; i < level.sandbox_weapons.size; i++ )
	{
		weapon = level.sandbox_weapons[i];
		sandbox_add_pad( "weapon " + weapon, "Press ^3[{+activate}]^7 to take this weapon", GetWeaponModel( weapon ), ::pad_weapon, weapon );
	}

	spots = sandbox_find_spots();
	count = level.sandbox_defs.size;
	if ( spots.size < count )
	{
		count = spots.size;
	}
	for ( i = 0; i < count; i++ )
	{
		level.sandbox_pads[i] = sandbox_spawn_pad( level.sandbox_defs[i], spots[i] );
	}
	sandbox_log( "pads " + count + " of " + level.sandbox_defs.size + " (free spots " + spots.size + ")" );
}


sandbox_perk_model( perk )
{
	bottle = undefined;
	switch ( perk )
	{
	case "specialty_armorvest":
		bottle = "zombie_perk_bottle_jugg";
		break;
	case "specialty_quickrevive":
		bottle = "zombie_perk_bottle_revive";
		break;
	case "specialty_fastreload":
		bottle = "zombie_perk_bottle_sleight";
		break;
	case "specialty_rof":
		bottle = "zombie_perk_bottle_doubletap";
		break;
	case "specialty_longersprint":
		bottle = "zombie_perk_bottle_marathon";
		break;
	case "specialty_flakjacket":
		bottle = "zombie_perk_bottle_nuke";
		break;
	case "specialty_additionalprimaryweapon":
		bottle = "zombie_perk_bottle_additionalprimaryweapon";
		break;
	}
	if ( IsDefined( bottle ) )
	{
		model = GetWeaponModel( bottle );
		if ( IsDefined( model ) && model != "" )
		{
			return model;
		}
	}
	return "zombie_bomb";
}


sandbox_spawn_pad( def, origin )
{
	trig = Spawn( "trigger_radius_use", origin, 0, 24, 64 );
	trig SetCursorHint( "HINT_NOICON" );
	trig SetHintString( def.hint );
	trig.def = def;

	if ( IsDefined( def.model ) && def.model != "" )
	{
		trig.marker = Spawn( "script_model", origin + ( 0, 0, 36 ) );
		trig.marker SetModel( def.model );
	}

	trig thread sandbox_pad_think();
	return trig;
}


sandbox_pad_think()
{
	for ( ;; )
	{
		self waittill( "trigger", player );
		if ( !IsDefined( player ) || !IsPlayer( player ) || !IsAlive( player ) )
		{
			continue;
		}
		if ( player maps\_laststand::player_is_in_laststand() )
		{
			continue;
		}
		player [[ self.def.func ]]( self.def.arg, self );
		wait( .5 );
	}
}


// Pad spots: a grid over the conference room (the spawn room, zone conference_level1, floor z 24 in the map's
// entity string), keeping clear of the room's retail use triggers, spawn points, windows and the portal.
sandbox_find_spots()
{
	avoid = [];
	avoid_r = [];
	ents = GetEntArray( "trigger_use", "classname" );
	for ( i = 0; i < ents.size; i++ )
	{
		avoid[avoid.size] = ents[i].origin;
		avoid_r[avoid_r.size] = 72;
	}
	ents = GetEntArray( "trigger_use_touch", "classname" );
	for ( i = 0; i < ents.size; i++ )
	{
		avoid[avoid.size] = ents[i].origin;
		avoid_r[avoid_r.size] = 80;
	}
	structs = getstructarray( "initial_spawn_points", "targetname" );
	for ( i = 0; i < structs.size; i++ )
	{
		avoid[avoid.size] = structs[i].origin;
		avoid_r[avoid_r.size] = 48;
	}
	structs = getstructarray( "exterior_goal", "targetname" );
	for ( i = 0; i < structs.size; i++ )
	{
		avoid[avoid.size] = structs[i].origin;
		avoid_r[avoid_r.size] = 96;
	}
	// conference room portal (trigger_multiple portal_trigs / portal_zone_trigs)
	avoid[avoid.size] = ( -1070, 2501, 24 );
	avoid_r[avoid_r.size] = 150;

	spots = [];
	for ( y = 2260; y <= 3060; y += 56 )
	{
		for ( x = -1040; x <= -300; x += 56 )
		{
			p = ( x, y, 24 );
			near = false;
			for ( i = 0; i < avoid.size; i++ )
			{
				if ( Distance2D( p, avoid[i] ) < avoid_r[i] && abs( p[2] - avoid[i][2] ) < 120 )
				{
					near = true;
					break;
				}
			}
			if ( near )
			{
				continue;
			}
			spot = sandbox_floor_spot( p );
			if ( IsDefined( spot ) )
			{
				spots[spots.size] = spot;
			}
		}
	}
	return spots;
}


// the floor under p if it is the room floor (not a table top) with 28 units of clearance around it
sandbox_floor_spot( p )
{
	trace = BulletTrace( p + ( 0, 0, 64 ), p - ( 0, 0, 64 ), false, undefined );
	if ( trace["fraction"] >= 1 )
	{
		return undefined;
	}
	floor = trace["position"];
	if ( abs( floor[2] - p[2] ) > 8 )
	{
		return undefined;
	}
	dirs = [];
	dirs[0] = ( 28, 0, 0 );
	dirs[1] = ( -28, 0, 0 );
	dirs[2] = ( 0, 28, 0 );
	dirs[3] = ( 0, -28, 0 );
	for ( h = 16; h <= 56; h += 40 )
	{
		c = floor + ( 0, 0, h );
		for ( i = 0; i < dirs.size; i++ )
		{
			if ( !BulletTracePassed( c, c + dirs[i], false, undefined ) )
			{
				return undefined;
			}
		}
	}
	return floor;
}


//-------------------------------------------------------------------------------
// pad functions (self = the player who used the pad)
//-------------------------------------------------------------------------------

pad_defcon5( arg, pad )
{
	sandbox_defcon5( self );
}


pad_open_sesame( arg, pad )
{
	sandbox_open_sesame( self );
}


pad_points( arg, pad )
{
	self maps\_zombiemode_score::add_to_player_score( 100000 );
	sandbox_log( "points now " + self.score );
}


// the same loop as retail full_ammo_powerup(), plus a full clip
pad_refill_ammo( arg, pad )
{
	weapons = self GetWeaponsList();
	for ( i = 0; i < weapons.size; i++ )
	{
		self GiveMaxAmmo( weapons[i] );
		self SetWeaponAmmoClip( weapons[i], WeaponClipSize( weapons[i] ) );
	}
	sandbox_log( "ammo refilled, weapons " + weapons.size );
}


pad_god( arg, pad )
{
	if ( is_true( self.sandbox_god ) )
	{
		self.sandbox_god = false;
		self DisableInvulnerability();
		self IPrintLnBold( "God mode OFF" );
	}
	else
	{
		self.sandbox_god = true;
		self EnableInvulnerability();
		self IPrintLnBold( "God mode ON" );
	}
	sandbox_log( "god " + self.sandbox_god );
}


// round_spawning() waits on this retail flag before every spawn
pad_toggle_spawning( arg, pad )
{
	if ( flag( "spawn_zombies" ) )
	{
		flag_clear( "spawn_zombies" );
		IPrintLnBold( "Zombie spawning OFF" );
	}
	else
	{
		flag_set( "spawn_zombies" );
		IPrintLnBold( "Zombie spawning ON" );
	}
	sandbox_log( "spawn_zombies " + flag( "spawn_zombies" ) );
}


pad_next_round( arg, pad )
{
	sandbox_goto_round( level.round_number + 1 );
}


pad_set_round( arg, pad )
{
	target = GetDvarInt( #"sandbox_round" );
	if ( target <= 0 )
	{
		target = 20;
	}
	sandbox_goto_round( target );
}


// thief_round_tracker() starts a thief round at "between_round_over" once round_number >= next_thief_round
pad_thief_round( arg, pad )
{
	if ( flag( "thief_round" ) )
	{
		sandbox_log( "thief round already running" );
		return;
	}
	level.next_thief_round = level.round_number;
	sandbox_log( "next_thief_round " + level.next_thief_round );
	sandbox_goto_round( level.round_number + 1 );
}


// follows zombie_devgui_goto_round()
sandbox_goto_round( target_round )
{
	if ( target_round < 1 )
	{
		target_round = 1;
	}
	// between rounds (the intermission after "end_of_round") nothing listens for "kill_round" yet, and the next
	// round_spawning() would still start with the old number: wait until the next round has started
	if ( !is_true( level.sandbox_round_active ) )
	{
		// one request waits; a later pad use before the round starts replaces its target
		if ( IsDefined( level.sandbox_goto_pending ) )
		{
			level.sandbox_goto_pending = target_round;
			sandbox_log( "goto round " + target_round + ": replaces the waiting request" );
			return;
		}
		level.sandbox_goto_pending = target_round;
		sandbox_log( "goto round " + target_round + ": waiting for the round to start" );
		level waittill( "start_of_round" );
		wait( .05 );
		target_round = level.sandbox_goto_pending;
		level.sandbox_goto_pending = undefined;
	}
	sandbox_log( "goto round " + target_round + " from " + level.round_number );

	level.zombie_total = 0;
	maps\_zombiemode::ai_calculate_health( target_round );
	level.round_number = target_round - 1;

	level notify( "kill_round" );

	wait( 1 );

	zombies = GetAiSpeciesArray( "axis", "all" );
	if ( IsDefined( zombies ) )
	{
		for ( i = 0; i < zombies.size; i++ )
		{
			if ( is_true( zombies[i].ignore_devgui_death ) )
			{
				continue;
			}
			zombies[i] DoDamage( zombies[i].health + 666, zombies[i].origin );
		}
	}
}


// a normal zombie from the retail spawners (spawn_zombie), then retail make_crawler() - the call
// zombie_spawn_init() has commented out for crawler rounds
pad_spawn_crawler( arg, pad )
{
	if ( !IsDefined( level.enemy_spawns ) || level.enemy_spawns.size == 0 )
	{
		sandbox_log( "crawler: no active spawner" );
		return;
	}
	ai = spawn_zombie( level.enemy_spawns[ RandomInt( level.enemy_spawns.size ) ] );
	if ( !IsDefined( ai ) )
	{
		sandbox_log( "crawler: spawn failed" );
		return;
	}
	while ( IsDefined( ai ) && !is_true( ai.zombie_init_done ) )
	{
		wait( .05 );
	}
	if ( IsDefined( ai ) )
	{
		ai thread maps\_zombiemode_spawner::make_crawler();
		sandbox_log( "crawler spawned at " + ai.origin );
	}
}


// follows zombie_devgui_pack_current_weapon() / get_upgrade()
pad_pack_current_weapon( arg, pad )
{
	weapon = self GetCurrentWeapon();
	if ( self maps\_zombiemode_weapons::has_upgrade( weapon ) )
	{
		sandbox_log( "pack: already have upgrade of " + weapon );
		return;
	}
	if ( !IsDefined( level.zombie_weapons[weapon] ) || !IsDefined( level.zombie_weapons[weapon].upgrade_name ) )
	{
		sandbox_log( "pack: no upgrade for " + weapon );
		return;
	}
	upgrade = level.zombie_weapons[weapon].upgrade_name;
	self TakeWeapon( weapon );
	self GiveWeapon( upgrade, 0, self maps\_zombiemode_weapons::get_pack_a_punch_weapon_options( upgrade ) );
	self GiveStartAmmo( upgrade );
	self SwitchToWeapon( upgrade );
	sandbox_log( "pack: " + weapon + " -> " + upgrade );
}


// uses the machine's own trigger, as zombie_devgui_give_perk() does
pad_perk( vending, pad )
{
	perk = vending.script_noteworthy;
	if ( self HasPerk( perk ) )
	{
		sandbox_log( "perk " + perk + " already owned" );
		return;
	}
	vending notify( "trigger", self );
	sandbox_log( "perk " + perk + " machine used, score " + self.score );
}


pad_powerup( name, pad )
{
	angles = self GetPlayerAngles();
	forward = AnglesToForward( ( 0, angles[1], 0 ) );
	spot = self.origin + ( forward[0] * 80, forward[1] * 80, 0 );
	trace = BulletTrace( spot + ( 0, 0, 40 ), spot - ( 0, 0, 80 ), false, undefined );
	if ( trace["fraction"] < 1 )
	{
		spot = trace["position"];
	}
	level thread maps\_zombiemode_powerups::specific_powerup_drop( name, spot );
	sandbox_log( "powerup " + name + " dropped" );
}


pad_weapon( weapon, pad )
{
	if ( self HasWeapon( weapon ) )
	{
		self SwitchToWeapon( weapon );
		sandbox_log( "weapon " + weapon + " already owned" );
		return;
	}
	self maps\_zombiemode_weapons::weapon_give( weapon );
	current = self GetCurrentWeapon();
	sandbox_log( "weapon " + weapon + " given, current " + current );
}


//-------------------------------------------------------------------------------
// headless self-test: sandbox_selftest 1 uses every pad once with player 0
//-------------------------------------------------------------------------------

sandbox_selftest()
{
	player = get_players()[0];
	level thread sandbox_selftest_log_thief();
	sandbox_log( "selftest start, pads " + level.sandbox_pads.size + ", num_perks " + player.num_perks + ", quickrevive " + player HasPerk( "specialty_quickrevive" ) );

	// god mode first, so the test client is not downed while the zombies of round 1 reach it
	pad = sandbox_selftest_pad( "god" );
	pad notify( "trigger", player );
	wait( 1 );
	health = sandbox_selftest_hit( player );
	sandbox_log( "selftest god on: health after a 1000 damage hit " + health );
	for ( i = 0; i < level.sandbox_pads.size; i++ )
	{
		pad = level.sandbox_pads[i];
		label = pad.def.label;
		if ( label == "set_round" || label == "god" || label == "next_round" || label == "thief" )
		{
			continue;	// run below, in a fixed order
		}
		skip = GetDvar( #"sandbox_selftest_skip" );	// headless bisecting: skip pads whose label contains this
		if ( skip != "" && IsSubStr( label, skip ) )
		{
			continue;
		}
		sandbox_log( "selftest pad " + i + " " + label + " at " + pad.origin + ", score " + player.score + ", num_perks " + player.num_perks + ", quickrevive " + player HasPerk( "specialty_quickrevive" ) );
		pad notify( "trigger", player );
		wait( 2 );
		if ( IsSubStr( label, "perk " ) )
		{
			// the retail machine flow: bottle, "weapon_change_complete", give_perk
			for ( t = 0; t < 30 && IsDefined( player.perk_purchased ); t++ )
			{
				sandbox_log( "selftest perk wait " + t + ": current " + player GetCurrentWeapon() + ", switching " + player IsSwitchingWeapons() + ", is_drinking " + is_true( player.is_drinking ) );
				wait( 1 );
			}
			has = player HasPerk( pad.def.arg.script_noteworthy );
			sandbox_log( "selftest " + label + " has perk " + has + ", score " + player.score + ", num_perks " + player.num_perks );
		}
	}

	// spawning back on (the loop above turned it off), so rounds run normally
	pad = sandbox_selftest_pad( "spawning" );
	pad notify( "trigger", player );
	wait( 1 );

	round = level.round_number;
	pad = sandbox_selftest_pad( "next_round" );
	pad notify( "trigger", player );
	for ( t = 0; t < 90 && level.round_number == round; t++ )
	{
		wait( 1 );
	}
	sandbox_log( "selftest next_round: round " + round + " -> " + level.round_number );

	if ( GetDvar( #"sandbox_selftest_skip" ) != "thief" )
	{
		pad = sandbox_selftest_pad( "thief" );
		pad notify( "trigger", player );
		for ( t = 0; t < 90 && !flag( "thief_round" ); t++ )
		{
			wait( 1 );
		}
		sandbox_log( "selftest thief: thief_round " + flag( "thief_round" ) + ", round " + level.round_number );
		// let the thief round end (the fighting test client kills the thief or it leaves) before changing rounds again
		for ( t = 0; t < 150 && flag( "thief_round" ); t++ )
		{
			wait( 1 );
		}
		sandbox_log( "selftest thief round over: thief_round " + flag( "thief_round" ) + ", round " + level.round_number );
	}

	SetDvar( "sandbox_round", "8" );
	pad = sandbox_selftest_pad( "set_round" );
	pad notify( "trigger", player );
	for ( t = 0; t < 90 && level.round_number != 8; t++ )
	{
		wait( 1 );
	}
	sandbox_log( "selftest set_round 8: round " + level.round_number );

	weapons = player GetWeaponsList();
	list = "";
	for ( i = 0; i < weapons.size; i++ )
	{
		list = list + " " + weapons[i];
	}
	if ( GetDvarInt( #"sandbox_selftest" ) == 3 )
	{
		SetDvar( "sandbox_selftest", "0" );	// once: the game after a game-over restart has no self-test
	}
	pad = sandbox_selftest_pad( "god" );
	pad notify( "trigger", player );
	wait( 1 );
	sandbox_log( "selftest done, round " + level.round_number + ", defcon " + level.defcon_level + ", score " + player.score + ", perks " + player.num_perks + ", weapons" + list );
}


// sandbox_selftest 2 (headless screenshots): god mode, spawning off, and the player looks over the pad field
// from its south-east corner, then from the north-west corner
sandbox_selftest_view( player )
{
	player EnableInvulnerability();
	flag_clear( "spawn_zombies" );
	// look at the middle of the pad field from the first and the last pad (opposite corners of the grid)
	middle = ( 0, 0, 0 );
	for ( i = 0; i < level.sandbox_pads.size; i++ )
	{
		middle = middle + level.sandbox_pads[i].origin;
	}
	middle = middle / level.sandbox_pads.size;
	origins = [];
	origins[0] = level.sandbox_pads[0].origin;
	origins[1] = level.sandbox_pads[level.sandbox_pads.size - 1].origin;
	angles = [];
	for ( i = 0; i < origins.size; i++ )
	{
		look = VectorToAngles( middle - ( origins[i] + ( 0, 0, 60 ) ) );
		angles[i] = ( look[0], look[1], 0 );
	}
	for ( ;; )
	{
		for ( i = 0; i < origins.size; i++ )
		{
			player SetOrigin( origins[i] );
			player SetPlayerAngles( angles[i] );
			sandbox_log( "view " + i + " at " + origins[i] );
			wait( 10 );
		}
	}
}


// logs every thief round (the pad's or the retail tracker's own), for the headless logs
sandbox_selftest_log_thief()
{
	for ( ;; )
	{
		flag_wait( "thief_round" );
		sandbox_log( "thief round starts, round " + level.round_number );
		flag_waitopen( "thief_round" );
		sandbox_log( "thief round over, round " + level.round_number );
	}
}


sandbox_selftest_pad( label )
{
	for ( i = 0; i < level.sandbox_pads.size; i++ )
	{
		if ( level.sandbox_pads[i].def.label == label )
		{
			return level.sandbox_pads[i];
		}
	}
	return undefined;
}


sandbox_selftest_hit( player )
{
	player DoDamage( 1000, player.origin );
	wait( .1 );
	return player.health;
}
