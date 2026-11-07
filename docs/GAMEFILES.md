# Game files: what exists in the current game

`python -m builder.data.gamefiles` reads the installed game directly, read-only, and writes
`data/game-<Steam build id>/` (gitignored):

- `tables/*.json`: whole data tables (every column the community dat-schema names for PoE2, English).
  Each table's schema width is checked against the file's rows; a mismatch is reported, never misread
  (`export-report.json`).
- `files/`: the tree graph (`Metadata/PassiveSkillGraph.psg`) and every stat description file
  (`data/statdescriptions/**/*.csd`, as UTF-8).
- `live.json`: what the current game can reach, the rules used (below), and the cross-check against PoB's export
  and poe2db's Meta list.

`--analyse-only` redoes `live.json` from an existing export. The export takes about 20 seconds.

**Tools**, all inside the project, approved 7 Nov 2026: a portable Node.js (nodejs.org, checksum verified) under
`tools/node/`; SnosMe's `pathofexile-dat` 16.0.1 (npm, pinned in `tools/datamine/package.json`), driven by
`tools/datamine/export.mjs`; the dat-schema from poe-tool-dev's latest release, pinned per build as
`schema.min.json`. The loader opens bundle files with `fs.readFile` only. Nothing runs while Steam is updating the game
(the app manifest's StateFlags must be 4). The install is `C:\Program Files (x86)\Steam\steamapps\common\Path of Exile 2`
(`POE2_PATH` overrides it). The old October 2025 copy under `C:\PROJECTS\SteamLibrary` is not used.

## Liveness rules

The files keep removed content, and they also hold content that isn't released yet. The rules, with the tables they
read, are in `builder/data/liveness.py`'s docstring and are copied into `live.json`. In short:

- **Passives** are live if they're nodes of the current tree graph (`PassiveSkillGraph.psg`, parsed to its last byte).
- **Gems** are live if something reaches them:
  - they drop: they have an uncut-gem tier, which is the schema's `SkillGems.CraftingLevel` (the `Tier` column reads 0
    for every gem);
  - a base item grants them (`ItemInherentSkills`);
  - they're a weapon's or a shapeshift form's basic skill;
  - an ascendancy or tree node in the graph grants them;
  - a rollable modifier grants them;
  - a unique in the collection grants them. That unique's text comes from PoB's export, because which unique carries a
    unique mod is server-side;
  - another skill grants them (`GemEffects.AdditionalGrantedEffects`: Shred grants Ice Fragments).

  A support that's live only by its `SupportGems` weight is "live (inferred)".
- **Nothing is removed on the files' word alone**, because a rule that misses a source would silently drop a real
  skill. A gem nothing reaches is:
  - "deprecated" only when a second current source agrees (`GONE` in liveness.py: poe2db's patch notes, the user's
    in-game check), or GGG marks it ([DNT], placeholders);
  - "unresolved", and kept usable, when PoB's export carries it;
  - "dead in files" when no source we use has it, so there's nothing to remove.
- **Uniques** are live if they're in the unique collection layout.
- **Mods** are rollable if they're a prefix or suffix, or have a spawn weight above 0. Unique mods are counted apart.

## Cross-check, build 26476662 (7 Nov 2026)

| | Count |
|---|---|
| Passives in the graph (of 9,731 rows) | 5,152 |
| Gems live | 909, plus 74 inferred |
| Gems unresolved (kept usable) | 37 |
| Gems dead in files (in no source we use) | 28 |
| Metas live | 25 |
| Uniques in the collection | 441 |

- **PoB's tree** has no node outside the graph. The graph has 238 nodes PoB lacks.
- **Deprecated, the files and a second source agreeing**: Curse on Block, Cast on Melee Stun, Cast on Melee Kill,
  and Cast on Shock, Freeze and Ignite.
- **The 37 unresolved gems** stay usable. They're 0.5 league content (Runic, Verisium and Kalguur skills, Remnants,
  Detonate Living, Explosive Transmutation and others), plus Abyssal Apparition, Gelid Palm, Grenade, Requiem,
  Rite of Restoration, Smash to Smithereens and Soaring Midnight. Their sources aren't read yet.
- **Live gems PoB lacks**: ascendancy-granted skills (Navira's, Kelari's and Ruzhan's skills, Queen's Procession) and
  Kulemak's unique skills. Also 40 supports that are live only by the inferred weight, which may be unreleased
  content (Ricochet III, Dazing Cry and others).
- **Uniques**: 7 names differ between the collection and PoB, mostly spellings (Brynabas/Byrnabas,
  Splinter of Loratta/Lorrata). Eshtera's Path, Reverie, Safrin's Resolve, The Road Warrior and Zaida's Longevity are
  in PoB but not in the collection.
- **poe2db's Meta list (39)**:
  - 6 are deprecated (above);
  - 7 are dead in files and in no source we use: Cast on Death, Cast when Stunned, Cast when Damage Taken,
    Cast while Channelling, Demon Magus, Hydra Familiar and Summon Companion;
  - Animus Splinters is unresolved;
  - 25 are live.
- **The dodge roll**: `DodgeRollPlayer`'s `dodge_roll_base_travel_distance` is 46 units, which the game's own
  description shows divided by ten: 4.6 m. Cast on Dodge's Energy stat (200 "centienergy per unit") reads in game as
  "Gain 2 Energy per metre"; the model follows the in-game text.
