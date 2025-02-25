A nifty little tool I wrote for transfering sounds between Elden Ring soundbanks. 

# What does it do?
You pass the script two unpacked soundbanks and the sound IDs you want to use, and it will copy the JSON entries and WEMs from the first soundbank to the second. This is essentially what is described in [Themyys' tutorial](http://soulsmodding.wikidot.com/tutorial:main#toc9).

# How do I use it?
More specifically, you need to have 3 things:
- an *unpacked* source soundbank, typically from an NPC or map.
- an *unpacked* destination soundbank, typically *cs_main*.
- the wwise IDs of the sounds you want to use (e.g. in animation TAEs).

To unpack your game use [UXM](https://github.com/Nordgaren/UXM-Selective-Unpack). You *have to* unpack the entire game, otherwise the soundbanks will not be included. Soundbanks can be found under `sd` or `sd/enus` and unpacked using [rewwise](https://github.com/vswarte/rewwise/) by dropping them onto `bnk2json.exe`.

To use the script, either modify the uppercase variables at the top, or call it from a terminal. See `--help` for required arguments and such. Paths to soundbanks should lead to the unpacked directories (not e.g. the `soundbank.json` inside). If a passed path is not absolute, the script's folder will be used; that is, you can place your unpacked soundbanks in the same folder as the script to avoid having to type full paths.

# Fine tuning
You may want to do some small adjustments to your soundfiles, e.g. setting a different volume. This process is still entirely manual and up to you. This is done in the edited `soundbank.json`. Look for the IDs the script printed during execution; the volume is usually adjusted on a `RandomSequenceContainer`. It will contain a dict called `node_initial_params` in which you should find the relevant settings. There are different (*mysterious*) ways of influencing audio volume (such as *GameAuxSendVolume*, *UserAuxSendVolume0*, etc.), but the easiest (and recommended!) one to use is just called `Volume`. Values will typically range from `-15` for very loud noises to `-3` for very quiet ones. 

# Using modded sound files
Once the script is done (assuming `enable_write` was set, of course), a backup of the destination `soundbank.json` will be placed in the destination's *parent* folder (look for `<soundbank_name>_backup.json`). You will have to pack your soundbank again using *rewwise* by dropping the modified soundbank folder onto `bnk2json.exe`. Then place the *repacked* soundbank in your mod folder, in a path corresponding to where you got it from (e.g. if you got it from `sd/enus`, place it in `sd/enus`, etc.).

The official release of [Mod Engine 2](https://github.com/soulsmods/ModEngine2) does not support loading modified sound files right now. There is an inofficial release floating around which is packaged with some mods, e.g. [this one](https://www.nexusmods.com/eldenring/mods/6384) or [this one](https://www.nexusmods.com/eldenring/mods/6340). Don't forget to give an "endorse" to these kind people :) Also, you can keep your previous config files!
