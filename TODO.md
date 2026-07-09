# TODO

Open / deferred items for the extractor + web tool. (Status: ☐ open · ◐ partial · ✓ done-for-reference.)

## Items & crafting
- ☐ **Unique items + their sources.** Add data/UI for unique (high-grade/legendary) items and
  where each comes from. Sources are twofold: **boss/enemy drops** and **crafted at high
  mastery** (crafter expertise level). Likely data: `Item.xml` (grade/rarity), enemy loot/drop
  tables for the boss drops (same loot system the modding tool's "Modify Loot" edits), and
  crafting recipes for the high-expertise crafts. Show each unique item with its drop source(s)
  and/or its recipe.
- ☐ **Crafting.** Add the crafting system — recipes (materials + required crafter
  expertise/technique level + output), likely from `Craft.lua` / `Technique.xml` / recipe
  tables. Cross-links with the unique-items sources above and the drone-module creation cost
  (Modules tab) — module creation is part of this same system.
- ☐ **Individual equipment items.** Beyond Equipment *Sets*, add individual gear with stats —
  weapons / armor / accessories (`Item.xml` equipment entries, the same data the modding tool's
  Equipment Editor edits), with stats, grade/rarity, and slot. Overlaps the unique-items item
  above (uniques are a subset).
- ☐ **Show drone-module *creation* cost.** The Modules tab currently shows each module's board
  **Output** cost (`Mastery.xml` `Cost`). Also surface the cost to *create/craft* the module
  (materials / resources / research), which is distinct. Locate the source first — likely the
  crafting/`Technique` recipe or a module-production table — extract it, and add a column /
  row-detail line in the Modules tab.

## Class Traits tab
- ☐ **List characters per class.** Consider adding, to each Class trait's row detail, the
  characters that are that class — **both** playable characters (reverse of `Pc.xml →
  EnableJobs`, already mapped per character in the builder) **and** NPCs/enemies of that class
  (units whose `Object.Job` is the class, via `Monster.xml` / `ObjectInfo`).

## Statuses / buffs — dynamic descriptions
- ◐ **Render buff/status/ability effects — the exotic long tail.** The auto-tooltip generator is a
  **Lua port** (`shared/shared_tooltip.lua GetBuffSystemMessageText` → `GuideMessage.xml` templates,
  plus the `Status.xml Desc_Increase`/`…ByLevel` value path). The **common cases are done and
  surfaced** in the web tool: ability/buff `Desc_Base` resolution, the buff "stat core" (flat +
  level-scaled `Eval` deltas), auras, debuff-immunity, and the SubType category header — emitted as
  `DATA.buffs` and shown as inline buff/group references in mastery/ability/set descriptions.
  **Left:** the value-carrying / no-template branches — discharge & reflect damage, cost/SP eaters,
  untargetable, explosion, HP-over-time, `ImmuneRace`. Low value (exotic buffs, diminishing returns);
  port by frequency, incrementally.
- ☐ **Compute actual damage/heal for an ability given a unit (and optionally a target).** The
  `$DamageAmount$` token (488 abilities, currently shown as X where present) is
  **runtime, unit-stat dependent** — the game computes it from the caster's AttackPower/ESPPower,
  the ability's coefficient (`ApplyAmountChangeStep` & friends), hit-rate calculator, and the
  target's Armor/Resistance/etc. Port that formula so we can show a concrete number (or range)
  for a built unit — and a vs-target number when an enemy is supplied. Lets the board builder
  preview real damage and resolves the `$DamageAmount$`/heal placeholders in ability descriptions.
  Source: the damage path in `script/` (battle/ability calc) + `GetHitRateCalculator_*`; ties into
  the Abilities tab and Stage 2 of "Statuses / buffs". Scope the formula inputs before committing.

## Dialogue tab
- ☐ **Continue clarity passes.** Keep reviewing the rendered dialogue/script output for
  confusing or wrong labels and tighten the wording.

## Page polish & i18n
- ☐ **Lazy-load the dialogue data (split it out of `data.js`).** Dialogues are **~1.4 MB / 29%** of
  the data file per language (en: 1.38 MB of 4.73 MB; kor: 1.45 MB) but only 58 stages, and are used
  **only** in the Dialogue tab — the rest of the app never touches `DATA.dialogues`. Have the extractor
  emit a second file (`web/data.dialogue.js` / `data.dialogue.kor.js`) and load it lazily on first
  Dialogue-tab open. **Note:** `fetch()` of `file://` is blocked in Chrome, so lazy-load must inject a
  `<script src="data.dialogue.js">` tag on demand (not fetch), running the `_blob` indexing loop
  (`app.js:39`) in its `onload`. Consumers to gate: that load-time `_blob` loop, `renderDialogue()`
  (`app.js:829`), and the filter values (`app.js:486`). Search is already per-view scoped, so nothing
  else breaks. **Payoff:** ~29% smaller initial data file + deferred parse/indexing.
- ☐ **Responsive / mobile + accessibility pass.** The 5-column board builder + side panels
  likely don't fit small screens — review responsive layout. Also audit accessibility: keyboard
  navigation, ARIA labels, and the colour-contrast of the category colours.
- ☐ **Korean translation support for Dialog tab scripts.**
  the Dialogue tab's generated *full-script* rendering
  (WHEN/by/triggers/decisions/full-script — cohesive generated prose, localize as one unit; tracked
  under README "Still English (deferred)" and the app-wide-English audit below).
- ☐ **Source overlapping chrome terms from the extracted dictionary.** Some `ui.<lang>.js` chrome
  strings duplicate terms the game already defines and the extractor already emits into
  `data.<lang>.js` (category / type / race / element values, OS, promotion, module, class, …) —
  e.g. the **Category** / **Type** filter labels vs the category/type *values* shown beneath them,
  which could drift across a retranslation. Have the extractor emit a small generated map
  (`web/ui.gen.<lang>.js`, `window.TS_UI_GEN`) for that subset, keyed by our i18n keys → *pinned
  keymap keys* (not English-value matching — ambiguous: "Type" → 종류 / 아이템 종류 / 사건 유형). Then
  `applyI18n` merges `Object.assign({}, TS_UI_GEN, TS_UI)` so curated strings still override, and
  the curated `ui.<lang>.js` shrinks to only our own taxonomy/verbs (Board Builder, Class Traits,
  New / Import / Export, tooltips). ~12 of 44 keys overlap; the payoff scales with #languages (every
  new dictionary language gets those terms free), so most worthwhile once a 3rd language is added.

## Testing & maintenance
- ☐ **Automated test / regression suite.** The validated work is currently checked by one-off
  scripts. Add a small suite (pytest / node) that locks it in: share-code decode/encode
  round-trips against `data/exports/`, the board-limit model vs the known reference values,
  dialogue label sanity, and `(type,code)` codemap coverage. Catches regressions on code changes
  *and* game patches.