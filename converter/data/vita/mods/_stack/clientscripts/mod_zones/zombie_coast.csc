#include clientscripts\_utility;

// FS_MODS BASE - client side of maps\_modenemies\_director (George Romero in Five). The engine runs main() after the
// level's client main only when +set bo1_mod_zones zombie_coast loaded zombie_coast.ff (CScr_LoadLevel).
// What zombie_coast.csc:61, :108-113 and :133-138 do for George, with the mod's flag numbers (see _director.gsc init:
// actor flags 10-13, player flag 8). Not part of the original game.

main()
{
	level._CF_PLAYER_ELECTRIFIED = 8;
	level._ZOMBIE_ACTOR_FLAG_ELECTRIFIED = 10;
	level._ZOMBIE_ACTOR_FLAG_DIRECTOR_LIGHT = 11;
	level._ZOMBIE_ACTOR_FLAG_DIRECTORS_STEPS = 12;
	level._ZOMBIE_ACTOR_FLAG_DIRECTOR_DEATH = 13;
	// zombie_coast_fx.csc:75
	level._effect["buff_electrified"] = LoadFX( "maps/zombie/fx_zmb_coast_electrified_torso" );
	clientscripts\_zombiemode_ai_director::init();
	register_clientflag_callback( "player", level._CF_PLAYER_ELECTRIFIED, ::director_player_electrified );
	register_clientflag_callback( "actor", level._ZOMBIE_ACTOR_FLAG_ELECTRIFIED, ::director_zombie_electrified );
	register_clientflag_callback( "actor", level._ZOMBIE_ACTOR_FLAG_DIRECTOR_LIGHT, clientscripts\_zombiemode_ai_director::zombie_director_light_update );
	register_clientflag_callback( "actor", level._ZOMBIE_ACTOR_FLAG_DIRECTORS_STEPS, ::director_footsteps );
	register_clientflag_callback( "actor", level._ZOMBIE_ACTOR_FLAG_DIRECTOR_DEATH, clientscripts\_zombiemode_ai_director::zombie_director_death );
}


// zombie_coast.csc:665-728 (coast_player_electrified and its fx helpers)
director_player_play_electric_fx( localclientnum )
{
	if ( !IsDefined( level.player_electric_fx ) )
		level.player_electric_fx = [];
	if ( IsDefined( level.player_electric_fx[ localclientnum ] ) )
		return;
	level.player_electric_fx[ localclientnum ] = PlayFxOnTag( localclientnum, level._effect[ "buff_electrified" ], self, "J_SpineLower" );
}


director_player_end_electric_fx( localclientnum )
{
	if ( IsDefined( level.player_electric_fx ) && IsDefined( level.player_electric_fx[ localclientnum ] ) )
	{
		deletefx( localclientnum, level.player_electric_fx[ localclientnum ], true );
		level.player_electric_fx[ localclientnum ] = undefined;
	}
}


director_player_electrified( local_client_num, set, newEnt )
{
	if ( local_client_num != 0 )
		return;
	player = GetLocalPlayers()[ local_client_num ];
	players = getlocalplayers();
	for ( i = 0; i < players.size; i++ )
	{
		if ( set )
		{
			if ( player GetEntityNumber() != self GetEntityNumber() )
				self director_player_play_electric_fx( i );
			else
				self PlayRumbleOnEntity( local_client_num, "explosion_generic" );
		}
		else if ( player GetEntityNumber() != self GetEntityNumber() )
		{
			self director_player_end_electric_fx( i );
		}
	}
}


// zombie_coast.csc:731-764 (coast_zombie_electrified): zombies buffed by George
director_zombie_electrified( local_client_num, int_set, actor_new )
{
	self endon( "death" );
	self endon( "entityshutdown" );
	if ( local_client_num != 0 )
		return;
	players = GetLocalPlayers();
	ent_num = self GetEntityNumber();
	for ( i = 0; i < players.size; i++ )
	{
		player = players[i];
		if ( !IsDefined( player._zombie_elec_fx ) )
			player._zombie_elec_fx = [];
		if ( IsDefined( player._zombie_elec_fx[ent_num] ) )
		{
			DeleteFx( i, player._zombie_elec_fx[ent_num] );
			player._zombie_elec_fx[ent_num] = undefined;
		}
		if ( int_set )
			player._zombie_elec_fx[ent_num] = PlayFXOnTag( i, level._effect[ "buff_electrified" ], self, "J_SpineLower" );
	}
}


// zombie_coast.csc:1202
director_footsteps( localClientNum, set, newEnt )
{
	self.footstepPrepend = "fly_step_director_";
}
