#include maps\_utility;
#include common_scripts\utility;
#include maps\_zombiemode_utility;
#include animscripts\zombie_Utility;

// SANDBOX MOD - not part of the original game. Loaded only with +set fs_game mods/sandbox.
// Retail maps\zombie_pentagon_ffotd.gsc is this file with both functions empty: it is the map's own
// "fix for the day" hook, called at the start and at the end of maps\zombie_pentagon::main().
// The mod's only change is the one call in each; everything else is in maps\sandbox\_sandbox.gsc.

main_start()
{
	maps\sandbox\_sandbox::main_start();
}


main_end()
{
	maps\sandbox\_sandbox::main_end();
}
