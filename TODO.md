# TODO

Open / deferred items for the extractor + web tool. (Status: ☐ open · ◐ partial · ✓ done-for-reference.)

## Items & crafting
- ✓ *(done)* **Unique items + individual equipment + crafting.** Built as the **Items tab**
  (`extract_items.py` → `web/items.js`; see README "Items" and `DATAMINING.md` "Items, gear, and the
  identify system"). Every obtainable item (~1,460) with stats, rarity, slot/type, the identify-suffix
  system, and **full acquisition sources**: enemy drops (resolved to the missions each enemy appears
  in), crafting (recipe materials + the **familiarity-tree** unlock — starter / master-a-predecessor /
  quest / Pascal-raid-gated / no-unlock), shops (price + currency), starting equipment, quest &
  civilian-rescue rewards, story/tutorial gifts, stage loot (→ mission), loot boxes (→ `Box_Lv<N>`
  tier), and Thief pocket-steal. Uniques are just the high-rarity subset; not-obtainable/cut items are
  dropped at extraction. (Originally three separate items: unique-items, crafting, individual-equipment.)
- ✓ *(done)* **Mission mail rewards (`GiveSystemMail*` / `SystemMail.xml` `AttachItem`).** Investigated
  and **deliberately not surfaced** — no value. The `AttachItem` values are almost all `Vill` (money),
  `Statement_Mastery`/`Statement_Module` (training/module points), and per-character costumes (we drop
  those); the only real items (a few materials, `Extractor`, `ToyBox_Rare`) are **already sourced** via
  craft/shop/drop/rescue, so a mail channel would gain zero new sources.
- ✓ *(done)* **Show drone-module *creation* cost in the Modules tab.** Each board module is crafted
  (after it unlocks) from a fixed material list — a `Type="Module"` **Technique** (`Technique.xml`)
  whose class name == the module mastery name, with `<RequireItems> → property[Item, Count]`.
  `extract_masteries.py` attaches it as `craftCost` on the module masteries; the Modules-tab row detail
  shows a **"Crafting materials: …"** line (alongside the existing board Output cost). 112 modules.
  (Character masteries have no such cost — the 842 `Type="Mastery"` techniques carry no `RequireItems`.)

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
- ◐ **Compute actual damage/heal for an ability given a unit (and optionally a target).**
  **Done — the `$DamageAmount$` *formula* is now rendered** in ability descriptions (base +
  `(+pct% stat)` scaling terms, e.g. "100 (+75% Attack Power)(+25% Speed)"), replacing the old `X`
  placeholder. Base = `ApplyAmountChangeStep`; stats = the `<AdditionalApplyAmount>` list; the
  unit-dependent `Cost` term resolves to the owner's resource (Vigor/Rage/Fuel). See DATAMINING
  "`$DamageAmount$` — the per-hit damage formula" (`resolve_damage_amount`).
  **Left:** the *concrete number* for a specific built unit (and a vs-target number) — the game
  computes it from the caster's AttackPower/ESPPower × the coefficient, hit-rate calculator, and the
  target's Armor/Resistance. Port that from the damage path in `script/` (battle/ability calc) +
  `GetHitRateCalculator_*` to let the board builder preview real damage. Scope the inputs first.

## Dialogue tab
- ☐ **Continue clarity passes.** Keep reviewing the rendered dialogue/script output for
  confusing or wrong labels and tighten the wording.
- ✓ *(done)* **Phase 2: outcome-tier view for Sky-wind Park + Silverlining.** Two opened-group
  missions decide the award by **outcome**, not a dialogue menu, so they now get **synthetic
  "outcome" decisions** (`dialog_map._inject_outcome_decisions`, driven by the authored `OUTCOME_GROUPS`
  table — en/kor labels lifted from the mission objective text where one exists; grants/opens from
  `parse_mission_opens`).
  - **Sky-wind park**: outcomes **nested under** the `PlayerSelect` character pick — Sion → *Defeat
    Delivery brother* (Hysterie / opens Tactical Retreat) | *Leave the park* (Tactical Retreat / opens
    Hysterie); Irene → *Defeat Luna* (Hero's Responsibility / opens A Hero Never Gives Up) | *Don't
    give up* (A Hero Never Gives Up / opens Hero's Responsibility). The traced grant chips that were
    lumped on the Sion/Irene picks are **stripped** and shown split under the outcomes; Anne/Ray keep
    their single granted-only masteries (Waiting/ColdRefusal).
  - **Silverlining**: a **top-level** outcome decision (the mission surfaces no `<Choice>`) — *Don
    contacts the VHPD* (Supporter) | *Albus contacts the VHPD* (Alacrity) | *Destroy the jammers*
    (HighSpeed), each opening the other two.
  - The authored labels also replace the stage-traced pick / bare "mission reward" as the
    **Masteries-tab** Story choice (`outcome_labels`).
- ☐ **Mine `missionResult_Custom.lua` for other surfaceable content.** The per-mission post-battle
  result handlers held the "opened for research" channel and the authoritative grant→choice map.
  Survey the rest for other player-meaningful outcomes worth surfacing: `Progress/Character/*`
  advances (relationship/story flags), menu/feature unlocks (`OfficeMenu`/`WorkshopMenu`/
  `JointTrainingMenu` `Opened`), one-off item/resource rewards, `UpdateAchievement` toggles, and any
  other `AcquireMastery`/`UpdateCompanyProperty` effects. Catalogue, decide what's useful vs internal
  bookkeeping, and propose where each surfaces (mastery source, dialogue consequence, mission reward).

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