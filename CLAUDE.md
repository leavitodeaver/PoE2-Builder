# Rules for every Claude session in this project

## Check every "only / never / none" claim before you write it (the user's standing rule, 11 Nov 2026)

The user has caught claims like these false again and again, and can't check every message for them:
- "only two meta gems are socketable" (there are many);
- "Into the Breach is the only Remnant source" (five more exist);
- "each support gem once per character" (revoked long ago);
- "Shocked Ground's damage-taken bonus only exists on monster skills" (only one stat is monster-only; players make Shocked Ground through supports and items, and it Shocks the boss).

A false "no" claim hides an undercount, and a false "always" claim hides an overcount. So these are rules, not suggestions.

**Which claims this covers:** any claim, in code, a comment, a label, the mechanics catalog, a ledger entry, a report or a message, of the form:
- "X is the only Y", "no Y exists", or "nothing grants / sets / uses X";
- "X never / always happens", or "X can't / doesn't apply to players (or bosses)";
- "X is monster-only / deprecated / unused / removed";
- "PoB already handles X", or "X moves no build".

**Before you write one:**
1. **Search for the mechanic by its meaning, not by one id.**
   - Read its game definition (KeywordPopups).
   - Find every stat whose id holds its keywords.
   - Find every place those stats are used:
     - skill and support stat sets, both player and monster;
     - Mods (item, unique, passive, jewel, rune, soul core, idol);
     - the passive tree and ascendancies;
     - PoB's ModCache and ModParser.
2. **A finding about one stat, one table or one source tells you about that thing only, not about the mechanic.** Example: `shocked_ground_base_magnitude_override` is monster-only, but player skills make Shocked Ground without it.
3. **Check a second source**: poe2db (current entries only; it keeps unmarked deprecated ones) or the patch notes. Where they disagree with the game files, write down both.
4. **Write the claim with its evidence**: "(checked: <what you searched>; <second source>)". If you couldn't do all of the above, write "(UNVERIFIED)" and treat it as an open question.
5. **Never zero, drop or exclude a mechanic on an unverified claim.** Model it with a labelled assumption instead, or leave it as it is and say so.

**Managers:** before passing any such claim to the user, check its evidence tag, and check the claim yourself if the tag is missing or thin. Workers: the manager will reject a report whose "only / never / none" claims carry no evidence tag.
