#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;

#using_animtree( "generic_human" );

// mod: shared enemy module (mods/_stack) - Five's Pentagon thief on another map (L61: Kino). Not part of the original game.
// Needs +set bo1_mod_zones ... zombie_pentagon (zombie_pentagon.ff after the map, and maps\<map>.zombie_pentagon.ents:
// one actor_zombie_electrician spawner, targetname thief_zombie_spawner, parked). Five itself keeps its retail thief round.
// Retail maps\_zombiemode_ai_thief (zombie_pentagon.ff) is reused for the actor (thief_prespawn, anims, speeds, damage,
// steal + knuckle-crack, loot return); its hunt/chase/escape (thief_zombie_hunt T:672, thief_chasing T:1126, thief_take_player
// T:1746, thief_end_game T:1909) walks Five's floors, portals and elevators, so this module drives him instead
// (T = nyx/ref/zombie_pentagon_patch/maps/_zombiemode_ai_thief.gsc):
// - spawns at a path node 500-1400 units from a player (one at a time), stalks each player in turn (retail walk, run when
//   shot, sprint after 15% damage) and steals the current weapon on contact (retail grab, T:1268 without the teleport);
// - after the last victim he sprints to the path node farthest from the players and escapes after nightmare_thief_flee s;
//   the loot goes with him, and the NEXT thief carries it (and skips players he already robbed): kill him to get it back;
// - on death the loot is returned (retail thief_return_loot T:1005), plus retail's drops: full ammo, and bonfire sale if
//   killed before his first steal (fire sale after it) when the map includes that powerup;
// - escape drops a max ammo where he leaves (retail T:2030).
// Dvars: nightmare_thief_round N (first round), nightmare_thief_respawn S (seconds after the last thief died or escaped),
// nightmare_thief_flee S (flee time before he escapes).

init()
{
	if ( IsDefined( level.mod_thief_init_done ) )
		return;
	level.mod_thief_init_done = true;
	spawners = GetEntArray( "thief_zombie_spawner", "targetname" );
	if ( spawners.size == 0 )
	{
		level thread maps\_modenemy::log_later( "thief: no thief_zombie_spawner (needs +set bo1_mod_zones zombie_pentagon)" );
		return;
	}
	// retail init T:6-79 without thief_round_tracker (T:89, Five's thief round), thief_init_portals (T:90) and
	// thief_init_trap_clips (T:93, Five's trap clips)
	PrecacheRumble( "explosion_generic" );
	PrecacheItem( "zombie_gunstolen" );
	maps\_zombiemode_ai_thief::init_thief_zombie_anims();
	level._effect["ape_spawn"] = loadfx( "maps/zombie/fx_zombie_ape_spawn_dust" );
	level._effect["tech_trail"] = loadfx( "maps/zombie/fx_zombie_tech_trail" );
	thief_dvar_default( "scr_thief_health_pregame", "15000" );
	thief_dvar_default( "scr_thief_health_endgame", "15000" );
	thief_dvar_default( "scr_thief_speed_damage", "2000" );
	thief_dvar_default( "scr_thief_health_max", "40000" );
	PrecacheShellshock( "electrocution" );
	level.num_thief_zombies = 0;
	level.thief_zombie_spawners = spawners;
	array_thread( spawners, ::add_spawn_function, maps\_zombiemode_ai_thief::thief_prespawn );
	level.max_thief_zombies = 1;
	level.thief_thundergun_damage = 250;
	level.thief_health_multiplier = 2000;
	level.max_thief_health = 60000;
	level.thief_debug = false;
	level.thief_info = false;
	level.thief_intermission = false;
	flag_init( "thief_round" );
	flag_init( "tgun_react" );
	flag_init( "last_thief_down" );
	flag_init( "death_in_pre_game" );
	level.portal_trig = [];
	level.mod_thief_spawner = spawners[0];
	level.mod_thief_carry_player = [];
	level.mod_thief_carry_weapon = [];
	level thread thief_spawner_loop();
	level thread maps\_modenemy::log_later( "thief: init" );
}


thief_dvar_default( name, value )
{
	if ( GetDvar( name ) == "" )
		SetDvar( name, value );
}


thief_spawner_loop()
{
	level endon( "end_game" );
	flag_wait( "all_players_connected" );
	level.mod_thief_alive = 0;
	level.mod_thief_spawned = 0;
	level.mod_thief_last_gone = -1000000;
	for ( ;; )
	{
		wait 1;
		firstRound = 1;
		if ( GetDvarInt( #"nightmare_thief_round" ) > 0 )
			firstRound = GetDvarInt( #"nightmare_thief_round" );
		if ( !IsDefined( level.round_number ) || level.round_number < firstRound || level.mod_thief_alive > 0 )
			continue;
		if ( GetTime() - level.mod_thief_last_gone < GetDvarInt( #"nightmare_thief_respawn" ) * 1000 )
			continue;
		thief_spawn_one();
	}
}


// retail thief_zombie_spawn (T:320) with this module's think and death function
thief_spawn_one()
{
	point = maps\_modenemy::special_spawn_point( true );
	if ( !IsDefined( point ) )
		return;
	spawner = level.mod_thief_spawner;
	spawner.origin = point.origin;
	spawner.angles = point.angles;
	spawner.script_moveoverride = true;
	ai = spawner maps\_zombiemode_net::network_safe_stalingrad_spawn( "thief_zombie_spawn", 1 );
	spawner.count = 666;
	if ( spawn_failed( ai ) )
	{
		maps\_modenemy::log( "thief: spawn failed" );
		return;
	}
	level.num_thief_zombies++;
	ai ForceTeleport( point.origin, point.angles );
	ai.targetname = spawner.targetname;
	ai.deathFunction = ::thief_die;
	ai.animname = "thief_zombie";
	ai.exit_origin = ai.origin;
	level.mod_thief_alive++;
	level.mod_thief_spawned++;
	ai.mod_thief_id = level.mod_thief_spawned;
	PlayFX( level._effect["ape_spawn"], ai.origin );
	maps\_modenemy::log( "thief: spawned #" + ai.mod_thief_id + " round " + level.round_number + " at " + point.origin );
	ai thread thief_think();
}


// retail thief_zombie_think (T:619) up to the hunt, then thief_hunt
thief_think()
{
	self endon( "death" );
	self maps\_zombiemode_ai_thief::thief_set_state( "stalking" );
	flag_set( "death_in_pre_game" );
	self thread maps\_zombiemode_ai_thief::thief_zombie_choose_run();
	self.goalradius = 32;
	self.ignoreall = false;
	self.pathEnemyFightDist = 64;
	self.meleeAttackDist = 64;
	start_health = level.round_number * level.thief_health_multiplier;
	if ( start_health > level.max_thief_health )
		start_health = level.max_thief_health;
	start_health = self maps\_zombiemode_ai_thief::thief_scale_health( start_health );
	self.maxhealth = start_health;
	self.health = start_health;
	self.maxsightdistsqrd = 96 * 96;
	self.zombie_move_speed = "walk";
	self.can_speed_up = true;
	self maps\_zombiemode_ai_thief::thief_zombie_setup_victims();
	// loot an escaped thief took: this thief carries it (returned on his death) and leaves those players alone
	for ( i = 0; i < level.mod_thief_carry_player.size; i++ )
	{
		for ( j = 0; j < self.victims.player.size; j++ )
		{
			if ( self.victims.player[j] == level.mod_thief_carry_player[i] && !IsDefined( self.victims.weapon[j] ) )
				self.victims.weapon[j] = level.mod_thief_carry_weapon[i];
		}
	}
	level.mod_thief_carry_player = [];
	level.mod_thief_carry_weapon = [];
	self.fx_org = Spawn( "script_model", self.origin );
	self.fx_org SetModel( "tag_origin" );
	self.fx_org.angles = self.angles;
	self.fx_org LinkTo( self );
	PlayFxOnTag( level._effect["tech_trail"], self.fx_org, "tag_origin" );
	self thief_hunt();
	self thief_flee();
}


// every player in turn (thief_zombie_setup_victims' random order): walk up and steal (retail STEAL_DIST 64, T:1313)
thief_hunt()
{
	self endon( "death" );
	for ( idx = 0; idx < self.victims.player.size; idx++ )
	{
		self.victims.current_idx = idx;
		player = self.victims.player[idx];
		self.victims.current = player;
		if ( !IsDefined( player ) || IsDefined( self.victims.weapon[idx] ) )
			continue;
		self maps\_zombiemode_ai_thief::thief_zombie_set_visibility();
		if ( self.state != "chasing" && self.state != "sprinting" )
			self maps\_zombiemode_ai_thief::thief_set_state( "stalking" );
		start = GetTime();
		while ( IsDefined( player ) && GetTime() - start < 120000 )
		{
			if ( is_player_valid( player ) && DistanceSquared( self.origin, player.origin ) < 64 * 64 )
				break;
			if ( is_player_valid( player ) )
				self SetGoalPos( player.origin );
			wait 0.2;
		}
		if ( !IsDefined( player ) || !is_player_valid( player ) || DistanceSquared( self.origin, player.origin ) >= 64 * 64 )
			continue;
		self SetGoalPos( self.origin );
		self thief_steal( player );
	}
}


// retail thief_steal (T:1268) without thief_take_player (T:1302: Five's teleport of the victim to another floor)
thief_steal( player )
{
	self endon( "death" );
	self.state = "stealing";
	self SetVisibleToAll();
	self maps\_zombiemode_ai_thief::thief_turn_player();
	self maps\_zombiemode_ai_thief::thief_take_loot();
	zombie_attack = %ai_zombie_tech_grab;
	self thread maps\_zombiemode_audio::do_zombies_playvocals( "steal", self.animname );
	time = getAnimLength( zombie_attack );
	self animscripted( "meleeanim", self.origin, self.angles, zombie_attack, "normal", %body, 1 );
	wait( time );
	if ( flag( "death_in_pre_game" ) )
		flag_clear( "death_in_pre_game" );
	if ( IsDefined( player ) )
	{
		player FreezeControls( false );
		player notify( "released" );
	}
	stolen = "none";
	if ( IsDefined( self.victims.weapon[self.victims.current_idx] ) )
		stolen = self.victims.weapon[self.victims.current_idx];
	maps\_modenemy::log( "thief: #" + self.mod_thief_id + " stole " + stolen );
	self maps\_zombiemode_ai_thief::thief_set_state( "chasing" );
}


// replaces thief_end_game / thief_exit_level (T:1909 / T:2019, Five's portals and lights): sprint to the path node farthest
// from the players, then leave with the loot
thief_flee()
{
	self endon( "death" );
	self.state = "exiting";
	self.thief_speed = "sprint";
	self SetVisibleToAll();
	self.ignoreall = true;
	fleeTime = GetDvarInt( #"nightmare_thief_flee" );
	if ( fleeTime <= 0 )
		fleeTime = 30;
	maps\_modenemy::log( "thief: #" + self.mod_thief_id + " fleeing" );
	start = GetTime();
	while ( GetTime() - start < fleeTime * 1000 )
	{
		goal = thief_far_point();
		if ( IsDefined( goal ) )
			self SetGoalPos( goal );
		wait 3;
	}
	carried = 0;
	for ( j = 0; j < self.victims.player.size; j++ )
	{
		if ( IsDefined( self.victims.player[j] ) && IsDefined( self.victims.weapon[j] ) )
		{
			level.mod_thief_carry_player[level.mod_thief_carry_player.size] = self.victims.player[j];
			level.mod_thief_carry_weapon[level.mod_thief_carry_weapon.size] = self.victims.weapon[j];
			carried++;
		}
	}
	level thread maps\_zombiemode_powerups::specific_powerup_drop( "full_ammo", self.origin );
	maps\_modenemy::log( "thief: #" + self.mod_thief_id + " escaped with " + carried + " weapons" );
	PlayFX( level._effect["ape_spawn"], self.origin );
	thief_gone();
	if ( IsDefined( self.worldgun ) )
		self.worldgun Delete();
	if ( IsDefined( self.fx_org ) )
		self.fx_org Delete();
	self Delete();
}


// the path node (of up to 48 sampled within 2500 units of a player, in an enabled zone) farthest from every player
thief_far_point()
{
	players = get_players();
	if ( players.size == 0 )
		return undefined;
	nodes = GetAnyNodeArray( self.origin, 2500 );
	if ( !IsDefined( nodes ) || nodes.size == 0 )
		return undefined;
	best = undefined;
	bestDist = -1;
	for ( k = 0; k < 48; k++ )
	{
		node = nodes[RandomInt( nodes.size )];
		if ( IsDefined( node.type ) && ( node.type == "Begin" || node.type == "End" ) )
			continue;
		d = 999999999;
		for ( j = 0; j < players.size; j++ )
		{
			dj = DistanceSquared( players[j].origin, node.origin );
			if ( dj < d )
				d = dj;
		}
		if ( d > bestDist && maps\_zombiemode_utility::check_point_in_active_zone( node.origin ) )
		{
			best = node.origin;
			bestDist = d;
		}
	}
	return best;
}


thief_gone()
{
	level.mod_thief_alive--;
	level.num_thief_zombies--;
	level.mod_thief_last_gone = GetTime();
	flag_set( "last_thief_down" );
}


// retail thief_zombie_die (T:770) without Five's achievement (T:808), lights (T:841), portals (T:842) and announcer (T:845);
// a sale powerup only when the map includes it
thief_die()
{
	self maps\_zombiemode_spawner::reset_attack_spot();
	self Unlink();
	self.grenadeAmmo = 0;
	if ( IsDefined( self.worldgun ) )
	{
		self.worldgun Unlink();
		wait_network_frame();
		self.worldgun Delete();
	}
	players = get_players();
	for ( i = 0; i < players.size; i++ )
	{
		players[i] FreezeControls( false );
		players[i] EnableOffhandWeapons();
		players[i] EnableWeaponCycling();
		players[i] AllowLean( true );
		players[i] AllowAds( true );
		players[i] AllowSprint( true );
		players[i] AllowProne( true );
		players[i] AllowMelee( true );
		players[i] Unlink();
	}
	sale = "fire_sale";
	if ( flag( "death_in_pre_game" ) )
	{
		sale = undefined;
		if ( self.bonfire )
			sale = "bonfire_sale";
	}
	if ( IsDefined( sale ) && IsDefined( level.zombie_include_powerups ) && IsDefined( level.zombie_include_powerups[sale] ) )
		level thread maps\_zombiemode_powerups::specific_powerup_drop( sale, self.origin );
	forward = VectorNormalize( AnglesToForward( self.angles ) );
	endPos = self.origin - vector_scale( forward, 32 );
	level thread maps\_zombiemode_powerups::specific_powerup_drop( "full_ammo", endPos );
	self thread maps\_zombiemode_audio::do_zombies_playvocals( "death", self.animname );
	level maps\_zombiemode_spawner::zombie_death_points( self.origin, self.damagemod, self.damagelocation, self.attacker, self );
	if ( self.damagemod == "MOD_BURNED" )
		self thread animscripts\zombie_death::flame_death_fx();
	returned = 0;
	for ( j = 0; j < self.victims.player.size; j++ )
	{
		if ( IsDefined( self.victims.player[j] ) && IsDefined( self.victims.weapon[j] ) )
			returned++;
	}
	self maps\_zombiemode_ai_thief::thief_return_loot();
	cause = "other";
	if ( IsDefined( self.attacker ) && IsPlayer( self.attacker ) )
		cause = "player";
	maps\_modenemy::log( "thief: #" + self.mod_thief_id + " died (" + cause + "), returned " + returned + " weapons" );
	thief_gone();
	return false;
}
