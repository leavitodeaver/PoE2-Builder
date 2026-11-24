# The 1.0 preview layer: plan (PREVIEW1, 23 Nov 2026)

The user, 23 Nov 2026 ~08:20: include announced 1.0 content "with asterisks that it's subject to change pending 1.0
release"; ~08:30 (card): OFF BY DEFAULT, a request option turns it on. Source catalogue: RESEARCH53 R53-1
(scratch/manager/research/poe2-1-0-preview-r53.md). 1.0 releases 11 Dec 2026 (PST); full patch notes ~4 Dec.

## Rules this layer keeps
- Nothing here reaches a search unless `BuildRequest.preview_1_0` is true (default false). With it off, every number,
  list and rule is 0.5.5's exactly (tests/test_preview_1_0.py checks it).
- Every preview row carries its source (poe2db page / GGG post), the date read, a confidence, and what is left out.
- Every result row a preview gem touches gets "*1.0 preview, subject to change" (the checklist rows,
  builder/preview/__init__.py checklist_items).
- Current-game numbers are never changed. '1.0 changes pending' is a NOTE (flags.py), never a number.
- No skill or mechanic is dropped on an unverified claim (CLAUDE.md): Fire / Cold / Lightning Penetration stay beside
  Elemental Penetration (they share families, so they never stack).

## Where it plugs in (all hooks small; new code in builder/preview/)
| Piece | Where | How |
|---|---|---|
| The switch | builder/request/schema.py `preview_1_0` | default False |
| The form | builder/web/static index.html / app.js / live.js; app.py | a checkbox; preview names in the lists marked "*1.0 preview"; the server checks a request against `preview.data_for(request, data)` |
| Gem lists | builder/search/context.py `preview_data` (Context.data) | `preview.add_gems`: GameData.gems + the preview gems (shape of PoB's dump, `preview: True`), so Context.gems / all_gems / any_gems / gems_by_skill_id and the request compiler see them |
| Weapon classes | builder/data/rules.py `set_preview` | Dagger, One Hand Sword, Two Hand Sword released; their off hands as One Hand Mace's (LOW). Context sets it for each request, so a request without the switch puts 0.5.5 back |
| PoB | builder/search/gems.py Gems.__init__ -> `preview.install(engine, request)` | builder/preview/lua.py writes each gem into PoB's data as bridge.lua writes Untether (setUp as Data.lua 929-965, setupGem 980-1052); once per engine (a Lua guard); through engine.lua.run, so replay records it |
| Result marks | builder/report/checklist.py -> `preview.checklist_items` | a "1.0 preview *" row per preview gem used; "1.0 changes pending" rows (switch on) |

## Gem levels (LOW)
poe2db shows each 1.0 gem at ONE level (13, or 20 = max). Damage and cost at other levels follow a 0.5.5 attack's
curve (gems.json curve_reference: Lightning Spear; 20/13 = 1.406, the median of PoB's 109 attacks; other families
1.54 and 1.64, i.e. +9% / +16% at level 20 for a gem shown at 13). Replaced on release day.

## Order of work (DPS first)
1. DONE (PREVIEW1 turn 1): the layer; 6 plain-hit skills (Charged Slash, Engage, Haemorrhage, Blade Conduit,
   Shadow Assault, Viper Strike) as their main hit; Elemental Penetration; the pending flags.
2. DONE (PREVIEW2): Perforate (strikes; the drill, waves and disks still left out), Sand Scour / Bloodied Blade (the
   added attack time after speed as the headline, the scaled reading as a row: readings.py), Worldcutter (held to the
   payoff stage its own data gives, 3; 2.67 s a stage as the headline, the whole-hold reading as a row).
   HELD: the 8 traps + Detonate. Their 'Attack Damage N%' has no published damage to scale (no WeaponTypes row for any
   trap base; PoB's bases have no weapon data; poe2db's trap pages show none: UNVERIFIED until 1.0's files). A request
   naming one is told why; none is scored from a guessed number. When the damage is known they need a
   TrapTool weapon type too (below).
   DONE: Blade Conduit's Shocked Ground, through MECH-A's shocked_ground (three table rows in conditions.py, inert
   unless it is the main skill). Haemorrhage's Rupture (PREVIEW3, rupture.py; the manager's ruling, 23 Nov 14:53):
   a labelled model on R55-10's reading, the same total dealt sooner (MEDIUM-LOW): Bleeds don't stack, so the rest of a
   replaced Bleed is recovered, up to the strikes' limit; rows at 0 and the upper case (each strike adds its limit).
   NEXT: Molten Forge, the traps once unblocked (needs a
   TrapTool weapon type in PoB's weaponTypeInfo and a throw / cooldown cycle), Ethereal Knives / Whirling Blades
   (shotgun overlap like R51-4).
3. Supports: Overchannel, Impaling Blade II / Rending Blade (a periodic extra hit), Galvanising Blades, Sandstorm,
   Abundant Remnants I (Remnant sources), Conceal / Volatile Shades with the mechanics.
4. Mechanics. DONE (PREVIEW2): the Stealth cycle (stealth.py: Shroud's own use rate -> Ambushes; Conceal's 10 Darkness;
   Shades in range, LOW, with a row without them; no source = 0 with a note). Conceal supports only skills that enter
   Stealth (PREVIEW3: a preview skill type, PreviewStealth, that only Shroud carries; lua.py adds it to PoB's SkillType). Stances: their published parts are rows,
   NOT counted, because 1.0 publishes neither when a stance is active nor its buff values. The plan as first written:
   (with the MECH lanes' patterns): Stances (the search picks the stance as a play strategy, per skill);
   Darkness (x(1 + 0.03 D) on Ambush hits, D from the build's sources per Stealth cycle); Shades (1 + 0.5 x Shades in
   range on an Ambush); Stealth (a cycle from Shroud's cooldown / Duplicity / kills, no flat rate); Engulf in Darkness
   (fed by MECH-A's Immobilise cycles); Spectral Impale (LOW).
5. Uniques (as item data, PoB-parseable lines first; Oro's Sacrifice, Agnar, Ashcaller DONE on hidden bases derived
   from their cards): Windripper, Blueflame Bracers (changed), Oro's Sacrifice,
   Molten Tenet, Agnar, Riptide ... Each needs its base; granted skills have no data yet. Sacrificial Flame (PREVIEW3,
   flames.py): counted 0 Flames with a per-Flame table at 12% and 4% (poe2db's two blocks); its Frenzy Charge at maximum 0.
6. Classes / ascendancies: the Knight's 10 nodes (text published); Shadow / Duelist starts wait for the 1.0 tree.

## Release day (~11 Dec 2026; patch notes ~4 Dec)
1. ~4 Dec: read the full patch notes into the patch-notes archive (NOTES lane) and check every preview row and every
   flag against them; mark rows that changed; turn flags into tasks for the lanes that own the code (supports: GEM;
   jewel sockets / craft: JEWEL; ascendancies: MECH lanes; tree: SEARCH-B).
   Re-check first (the manager, 23 Nov 14:53): Rupture's definition (rupture.py's reading: same total sooner, or added);
   Sacrificial Flame's maximum, duration and removal, and which per-Flame block (12% / 4%) is current (flames.py); and
   trap damage. For traps, check first poe2db's Traps page's two unlabelled weapon-style entries, Miracle Apparatus
   (Physical 32-54, Fire 7-10, crit 10%, 1.85 a second) and Rune Gear (Physical 19-32, crit 10%, 1.70), not in
   BaseItemTypes (RESEARCH55 R55-9): if they are trap weapons at release, trap skills likely scale from them.
2. Release: re-export the game files (`python -m builder.data.gamefiles`), update the PoB checkout (vendor\) to its
   1.0 release and re-dump `data/pob-*` (`uv run python -m builder.data dump`).
3. For each row in builder/preview/gems.json: if PoB 1.0 has the gem, delete the row (PoB's real data wins; lua.py
   skips any id PoB has). If PoB lacks it, rebuild the row from the game files (real per-level tables) and keep it
   as a tool gem (builder/data/tool_gems.json + bridge.lua, the Untether pattern), no longer "preview".
   Re-export the real gem text from the game files (11 Dec): this layer's stat lines are paraphrased, numbers exact.
   Replace uniques.json's derived 'bases' with the real ones, and give traps their real weapon data.
   Settle readings.py's open readings from the real stats: which stat is "Added Attack Time", and Worldcutter's time per
   stage; then delete the row for the reading that lost.
4. Remove PREVIEW_WEAPON_CLASSES from rules (game_rules.yaml's `released` gets the 1.0 classes, patch "1.0.0").
5. Retire flags.py's entries one by one as the owning lanes land the 1.0 changes.
6. When builder/preview/gems.json is empty and no flag is left, the switch does nothing: the manager decides whether
   to keep it for the next preview cycle (0.x leagues after 1.0) or remove it.
