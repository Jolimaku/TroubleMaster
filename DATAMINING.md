# TroubleMaster — Datamining & reverse-engineering reference

Companion to **[`README.md`](README.md)** (the app + usage guide). This file records how
*TROUBLESHOOTER: Abandoned Children* stores its data and how that data was reverse-engineered
to feed the Python extractor and the web tool.

**Before working out any game-data mechanism** — file formats, encryption, the share-code
scheme, the translation/dictionary lookup, board/slot formulas, dialogue wiring, beast/
evolution data — **search this file first**; it very likely was figured out and documented
already. The **Data model notes** and the top **How the data is stored** section are the densest.

Covers: how the `Package/` assets and player saves are stored · the full data model (mastery
sources, Board Builder limits, description resolution, the dialogue graph) · the in-game **Help
corpus** (`Help.xml`) · Quests · Joint Training · the mastery-board **share-code format** · helper
scripts. The build/extraction
pipeline (`Reproducing`, `Outputs`) and the web tool itself are in **[`README.md`](README.md)**.

## How the data is stored

The game keeps everything in `Package/` as AES-encrypted, zipped blobs with
hashed names, indexed by an encrypted `Package/index`. The encryption key shipped
in the old community `TroubleTool` no longer matches the current build, so we use
the **official Dandylion modding tool** (`TSAC Modding Tool` on Steam) to decrypt
and unpack — it always tracks the current format.

Alternate (scriptable) decrypt path: the **DateMine** project (cloned at
`TROUBLESHOOTER-mine/`) ships a precompiled `lib/troublecrypt.dll`/`.exe` whose key
**does** match the current build. `extract_files.py` decrypts `Package/index` (→ a 22.5 MB
XML of ~78k `{pack, original, method, size}` entries) then decrypts/unzips each blob to a
`Data/` tree. Verified: extracted `xml/{Option,Mastery,Machine,Beast}.xml` are byte-identical
to our `Unpack/Data`. Set `lib/paths.py` `GAME` to the install path first. Useful for
re-extracting after a patch without the Dandylion UI. (Its `apply_mod.py` re-packs and
**overwrites** `Package/` — the destructive write-back direction; leave it alone (same hazard as
the `PLDataPacker --mode pack` repack warned about below).)

Localized strings live separately and are already plaintext, in the **game install
directory** (`…/Troubleshooter/Dictionary/`), *not* in the unpacked `Unpack/Data/` —
so searching the extracted xml/script for an English string (e.g. "Mature Phase")
finds nothing; it only exists in the dictionary. `Dictionary/keymap.dkm` maps a logical
key (e.g. `Mastery/BodyTraining/Base_Title`) to a numeric code + dictionary type, and
`Dictionary/eng/dic_*.dic` (`keyword` / `text`) map that code to the English string
(`#code \t Korean \t English`); pass another `--lang` for other languages. `Dictionary.get`
in `extract_masteries.py` resolves a logical key straight through.

Many xml fields hold an **inline Korean** `Title` with no obvious key (e.g.
`BeastEvolutionType/Normal` stages `성장기/성숙기/완전체`). They still have a keymap key —
find it by reverse-lookup: grep the English string in `dic_keyword.dic` to get its code,
then find which `keymap.dkm` key points to that code (here `BeastEvolutionType/Normal/<n>/Title`
→ "Growth / Adolescent / Mature Phase"; the `EggStart` variant adds a 4th, "Babyhood", for
egg-hatched beasts like Draki — so beasts have 3 *or* 4 evolution stages by `EvolutionType`).
Translate via that key rather than hardcoding.

## How saves are stored

**The player saves are plain, unencrypted SQLite 3 databases** — *not* encrypted, *not*
share-code-scrambled, and **troublecrypt is not involved** (that key is only for the `Package/`
asset blobs). They live in the install at `Release/bin/saves/`:
- `game.sav` — the full player state (the one that matters; ~2 MB).
- `resource.sav` — account/uuid bookkeeping (mostly empty in offline play).
- `log.sav` — an append-only event log (logins, property updates, mission results).
- plus `*_backup` and dated `*.bak` rolling backups, and `steam_autocloud.vdf`.

Open them read-only with stdlib `sqlite3` (no CLI/dll needed); the header is literally
`SQLite format 3\0`. **Steam Cloud syncs these exact plain files** — `userdata/<id>/470310/
remotecache.vdf` lists `Release/bin/saves/{game,resource,log}.sav` with `root=1` and a `sha`/`size`
matching the on-disk plain DB. There is **no separate `version;base64(encrypted)` remote blob** for
this game; the earlier "encrypted online save, ~7.99 bits/byte entropy" assumption was wrong (it
was reading the wrong artefact). Both the troublecrypt path and the period-5 share-code unscramble
are irrelevant here.

`game.sav` data model — an **EAV (entity / attribute-master / value) pattern** repeated per scope
(`account` / `company` / `roster` / `item` / `world` / `npc` / `quest` / `mail` / `mission`). Each
scope has a `*PropertyMaster(masterIndex, masterName)` name table + a `*Property(id, masterIndex,
value TEXT)` value table; `worldProperty` is a flat `(wpName, wpValue)`. Key tables:
- `company(companyID, CompanyName, Vill)` — Vill = money.
- `roster(rosterID, rosterClass, rosterName, rosterLv, rosterExp)` — one row per owned unit: PCs by
  name (`Albus`…), beasts as `Beast_N`, drones as `Machine_N`.
- `mastery(rosterID, masterIndex, masteryLv, boardIndex)` + `masteryMaster(masterIndex, masterName)`
  — the **actual placed mastery board** per unit. `masterName` joins **1:1 to our extracted mastery
  ids** (`Alacrity`, `Swordmaster`, `Module_Rifle`…). `boardIndex` 0/1 = the multiple board presets
  (extra boards from awakened jobs). `masteryLv` is 0/1 (placement flag, not a rank).
- `rosterProperty` (via `rosterPropertyMaster`) — the rest of the build as `path/like/keys`:
  `Object/Job` + `EnableJobs/<Job>/{Lv,Exp,...}` (class & job levels), `AbilityPreset/...`,
  `MasteryBoard/ExtraCount`, `Stats/*`, and crucially **the picks the share code can't carry**:
  beasts have `EvolutionMastery1/2/3`; drones have `MachineType` (= the `Mon_DroneFrame_<F>_<SP>`
  unit), `OSType`, `AIUpgradeStage` + `AIUpgradeMastery1/2/3`, `CraftKey` + `CraftMastery`.
- `item(itemID, companyID, itemType, itemCount, itemStatus)` + `itemProperty` + `itemEquippedInfo`
  (which roster has it equipped), and `mailbox`/`mailProperty`.

So a save is a **complete superset of a share code** — it holds the full board *and* every pick the
board-only code drops. This makes "import a real save to reconstruct full builds" (and a roster/
inventory view) directly feasible; the read path is trivial (read-only sqlite). Don't write to the
live DB while the game is running.

**Online saves are server-side, not a local file.** The plain `game.sav` above only exists after the
in-game *export online→offline* step (which is also what lets mods run). An online save itself lives
on the dev server and travels over an **encrypted network protocol** (ProtoLion.exe links Crypto++
`AuthenticatedEncryptionFilter` / `EncryptMessage` + Steam `EncryptedAppTicket` auth) — there is **no
persistent local online-save file** to read. A from-scratch scan of the whole game install + all
Steam `userdata` (2003 files) found **no** `version;base64(...)`-style blob; the encrypted-looking
local data seen in an earlier online session was transient / online-mode-only and isn't on disk now.
Bottom line: the only practical route to online-save data is the export-to-offline step (→ plain
`game.sav`); skipping it would mean intercepting/decrypting the native server protocol (not worth it).

The game's **in-game Help** is a usable corpus too: `xml/Help.xml` (`HelpCategory` →
`HelpSubCategory` → `Help`, each with `Base_Title` / `Base_Content` / `Content_Mission` /
`Content_End`, a `Checker` unlock-gate and `Order`), with the English in the same dictionaries
(verified: *"…modules that are randomly added when you craft a machine."*, *"Drone Realignment"*).
Bodies are either static `Base_Content` (direct dictionary lookup) or generated at runtime via
`Base_Content="" AutoScript="GetHelpContent_*"` in `script/shared/shared_help.lua` (intro prose from
`WordCollection` dict entries, then a data-assembled mastery list). Useful for cross-checking
reverse-engineered mechanics — see TODO "Mine the in-game Help texts". Not mined yet.

## Data model notes

- A mastery's `Type` is its class/character/ESP group (e.g. *Battle Mage*, *Ice*).
- **Enemy source**: a monster whose `<Masteries>` includes it — i.e. you can learn
  it by analysing that enemy. (`Monster.xml`, names resolved via `ObjectInfo`.)
  **Civilians** (`Civil_*` / `Mon_Civil*`) are skipped — they're rescue NPCs / neutrals you only
  ever meet as allies, never a hostile you can analyse, so they're not a real source (e.g.
  `Civil_Dembel` "carries" AliBaba but is only ever an ally/neutral; AliBaba's real source is the
  research chain). Every mastery a civilian carries also has a real enemy carrier or another
  channel, so the skip orphans nothing.
  `Mon_JointTraining_*` monsters are leveled training-mode clones of real enemies
  (`TroublemakerForJointTraining` points to the original) with fuller mastery sets; they
  aren't placed in any stage but are fought in the unlockable **Joint Drill** mode (the
  in-game display name; the XML idspace is `JointTraining`), so their masteries show a **Joint
  Drill** appearance (`⚔`) instead of a mission.
  (See the "Joint Training (Joint Drill)" section for the mode, its teams, and rewards.)
- **Character unlock**: `Pc.xml → EnableJobs → <job> → Masteries → property[Name,
  RequireLv]`. Levelling that character in that job to `RequireLv` globally unlocks
  the mastery for selection (it does not consume a board slot). Example — Sion as
  Battle Mage: Lv1 Magic Absorption, Lv4 Magic Duplication, Lv8 Magic Withdrawal,
  Lv12 Magic of Opportunity, Lv16 Supporting Magic Circuit.
- **Class basic masteries**: `Pc.xml → EnableJobs → <job> → BasicMasteries → property[Name]`
  (note: **no** `RequireLv`). Granted as a reward simply for *taking* that class —
  `shared_job.lua` `GetRewardMasteriesByJobLevel` returns the `BasicMasteries` with no level gate
  (unlike the level-locked `<Masteries>` block above). Only **3 blocks** exist, all on
  special character-classes: **Kylie/Engineer** (Thermodynamics, Dynamics, Solid/FluidMechanics),
  **Kylie/Hacker** (Abstraction, **Algorithm**, DataStructure), **Misty/Ninja**
  (HiddenWeaponThrowing, EnhancedTaijutsu, ReinforcedJutsu). *When* you get them depends on the
  class: at **recruitment** for the character's join class (Kylie joins as Engineer) but on the
  **class switch** for a later class (Hacker is `RequireLv 15`) — so the tool's wording stays
  neutral ("Granted with a character's class", line "<char> as <job>"). Emitted as a `Character`
  source flagged `classBasic` (no `lv`), shown in its own detail section. This is the only source
  for the 6 Hacker/Ninja basics (the 4 Engineer ones are also enemy-learnable).
- **Beast unlock**: `Beast.xml → BeastType → Masteries → property[Name, RequireLv]`.
  Capturing a beast and levelling it in its class unlocks the listed masteries (each
  `BeastType`/evolution form grants 3, typically at Lv 1/8/16); names resolve via the
  beast's `Monster`/`ObjectInfo`. 156 masteries come from this. Shown as "<Beast> — Lv N".
- **Drone unlock**: `Machine.xml → MachineType → Masteries → (nested) property[Name,
  RequireLv]`. Building a drone and levelling it in its class unlocks the listed modules
  (the `Module_*` masteries); names resolve via the drone's `Monster`/`ObjectInfo`. 59
  modules come from this. Shown on the Modules tab as "<Drone> — Lv N".
- **Achievement unlock** (feat-based): `xml/GuideTrigger.xml` — a `<class>` with a `Mastery`
  attribute grants that mastery when its `Checker` condition is met (Director
  `GuideTriggerDirector_AchievementMastery*` / Register-based). **31 masteries** (BeastHunter =
  defeat 100 Legendary beasts, HideHide = conceal 10×, the Engineer/Hacker "run a class out of
  protocols" pair, the performer/ninja feats, …). The precise threshold lives in
  `script/server/guide_trigger.lua` (read off each `GuideTriggerChecker_*` — **not** in any
  dictionary string), so the human-readable condition is **authored** in
  `extract_masteries.py` `ACHIEVEMENT_GRANTS` (en + kor; Korean mined from the linked Steam
  achievement `Desc` in `Achievement.xml` where one exists — that link is the optional 3rd
  field, surfaced as `🏆` context). Emitted per mastery as `achievements:[{condition,
  achievement?}]`. `guidetrigger_grants()` warns on any new grant missing a condition entry.
  *Note: `Achievement.xml` itself has **no** mastery-reward field — Steam achievements and
  mastery grants are separate systems that only sometimes share a trigger.*
  - **+3 hardcoded in lua, not GuideTrigger.xml** (`SYSTEM_GRANT_IDS`): **Lucky 7/8/9**
    (`LuckyNumberSeven/Eight/Nine`) are granted by `script/server/system.lua` — a `BuffAdded='Luck'`
    subscription counting `company.Stats.LuckAdded`; the 7th/8th/9th cumulative Luck buff grants
    each. Same feat shape, so emitted through this Achievement channel with conditions authored in
    `ACHIEVEMENT_GRANTS`. Found via a full `dc:AcquireMastery` call-site audit — these are the **only**
    grant hardcoded outside the data tables (every other call is GuideTrigger / story
    (`missionResult_Custom`) / office / research / the no-op mastery-*extract* path / a dead
    Quest+Troublebook reward branch with zero `Type="Mastery"` entries).
- **Story / dialogue unlock**: a story mission awards the mastery, often gated by a **dialogue
  choice**. Marked in the `.stage` file by `<GameMessageForm Mastery=.. Type="MasteryAcquired"/>`
  (or the `…AcquiredWho` variant, granted *to* a named character — the **Firefly Park** Ch1 opening
  tutorial's "starting masteries" use it: Patience vs PangOfConscience, PositiveMind vs Ambush, +
  Deftness) inside a `MissionDirect` scene; the trigger that plays that scene carries the gating
  `VariableTest`, and a `DialogChoice/Choice` sets that variable — so the choice text resolves
  (`dialog_map.mastery_grants()` / `_direct_mastery_grants()`, transitive across scene→scene).
  **~30 masteries** across ~10 EP1 story missions (Market Street → Principlism on "Intervene" /
  Flexibility on "Wait…"; the Sky-wind park Sion/Anne/Ray branches; the Training Room
  team-split). Emitted as `story:[{mission, choice?, scenario?}]` (`choice` omitted = unconditional mission
  reward). The **same** grants are highlighted in the **Dialogue tab** as a gold `★ Grants <m>`
  consequence under the choice (links to the mastery row), and surface in that tab's **full-script**
  view as a `grant mastery: <m>` action line — `render_script` would otherwise drop a grant scene
  as "content-less" (it holds only the `MasteryAcquired` toast, no spoken lines), so it's added
  back explicitly. All three views read the one `_direct_mastery_grants` parse.
  - **Scenario name + chapter** (`missions.build_scenario_index`). A **Scenario** mission is found
    in-game by its story name first (you pick it before the location/level even show), so the
    Story source and the Dialogue-tab heading lead with it: e.g. *Ch4 Scent of the Past* · Sky-wind
    park. From `Troublebook.xml` (the in-game story log): each `<class>` is a chapter (`Order` N);
    its `<Stage>` lists the scenario missions, whose localized name is
    `Troublebook/<chapter>/Stage/<1-based idx>/Title`. The compact chapter prefix is composed per
    language (`Ch4` / kor `4장`); the location stays the mission's own `LocationTitle`. Merged onto
    `mission_info` as `scenario`/`chapter`; only Scenario missions have one (Normal/Violent cases
    carry only a location). Indexed in search so a scenario can be found by name.
  - **Quest name** (`quests.quest_missions` → dialogue record `questName`). Side-quest stages have
    **no** scenario/chapter name, yet several sibling quests reuse one battlefield — e.g. Roberto's
    *Capacity Test* / *Favor and grudge of the past* / *A Request from a Girl* all run on the
    `Slot="SilverCloud_Street"` map (LocationTitle *Shooter Street Park*), and Maximillion's two
    *Iron Forest Disassembly Workshop* quests likewise. Each quest→mission→stage is a **separate**
    `.stage` file (not one stage multiplexed by a variable), so the Dialogue tab renders one entry
    per quest; to tell them apart the heading leads with the quest name (blue, matching the Quests
    tab) the way a scenario name leads for story stages. `quest_missions` builds `{mission_id:
    quest_title}` from every `EP1_Quest*` that names a battle via `Target` (MissionClear_*/Talk),
    `<DirectMission Mission=>`, or `<Missions>` — reusing the same title resolution as `build_quests`
    (factored into the shared `_quest_title` / `_quest_mission_ids`). 9 dialogue stages carry a
    `questName`; a stage never has both `scenario` and `questName` (quests aren't in Troublebook).
  - **Choice attribution traces state variables** (`_trigger_choice_keys`). A reward trigger's
    direct choice condition can be misleading: Sky-wind park's Irene reward tests `PlayerSelect==2`
    (Sion's value — apparently copied from the Sion reward block) yet also requires `Irene_Win`, a
    state set *only* when `PlayerSelect==3` (Irene). That `==2` is a **leftover bug** — the mission's
    character select is a *paging* menu (Albus/Sion/Irene/Another ⇄ Anne/Heissing/Ray/Previous, the
    page links setting `SelectLoop`) where you pick **one**, so once Irene is chosen `PlayerSelect`
    stays 3 and `==2` can never hold — but the reward is still earned on Irene. So a non-choice
    variable that is **choice-bound** (only ever set under one choice value, like `Irene_Win`)
    overrides a conflicting direct condition → correctly Irene's. Progression counters (`EventID`,
    set by *every* character choice **and** by battle triggers, hence excluded from `choice_vars`)
    are *not* choice-bound and never override. Without this, HeroResponsibility/HeroDontGiveUp
    mis-read as "Sion" and Waiting picked up a spurious "Albus".
  - **Per-option consequences *and* follow-up chaining use only the selector variable.** A `<Choice>`
    is credited with the consequences — and the `leads_to` follow-up decisions — of the variables it
    *selects* on (`choice_vars`), **not** the progression counters it also nudges. Sky-wind park's
    character picks each set `EventID=2` alongside `PlayerSelect`, and `EventID` gates dozens of later
    triggers — so without this every option was both flooded with the same unrelated effects *and*
    chained to every later sub-dialog (all six characters' follow-ups piling under the first option).
    With it, each pick chains only its own follow-up (Irene → the child-labour beat, Anne → her
    taming sub-choices). (The full team joining under *every* character is genuine, though — the pick
    chooses the mission's first half; the second half is shared and assembles everyone, keyed per
    `PlayerSelect` path.)
  - **Two follow-up wiring styles — variable-gated *and* direct scene-play.** `leads_to` nesting is
    resolved two ways, unioned. **(a) Indirect** (base game): an option sets a selector variable that
    gates a `<Trigger>` which plays the scene showing the follow-up (`shown_by` → `rule_to_dec`).
    **(b) Direct** (~25 stages across the base game *and* both DLCs, e.g. *Crimson Crow*'s *Shadow of
    the Past* `Tutorial_RedMineCrimeBase`): the `<Choice>`'s `<ActionList>` sets **no** variable — it plays the
    follow-up scene straight away with a `MissionDirect` action, and that scene hosts the next
    `DialogChoice`. So each option also records the scenes it plays, and `decs_played` maps those
    (chasing scene→scene via the forward `scene_plays` map, **stopping at the nearest
    decision-hosting scene** so an option links to its immediate follow-up, not the whole downstream
    chain) to the decisions whose `scene_key` they host. Without (b) these DLC missions rendered every
    sub-choice flat (the "security system" menu's four searches all detached from it). A stage can use
    both styles at once (Sky-wind park does), which is why the two link sets are unioned, not chosen
    between.
  - **Office / apartment tutorial grants** are a sub-channel of Story, emitted with `tutorial:true`
    (shown as "*place* (tutorial)"). The early onboarding scenes hand out masteries via a
    `<property Type="Action" Command="RefillMastery|AcquireMastery" MasteryName=..>` in
    `xml/Dialog/Dialog_Office*.xml` (`dialog_system.lua`'s `RefillMastery` → `dc:AcquireMastery`) —
    **not** a `.stage` file, so the stage scan misses them. 8 masteries: Albus's apartment
    (`Dialog_Office_Albus.xml`) grants **Yearning**, Swordmaster, CounterAttack, Forestallment,
    FairWind, BodyTraining; the office (`Dialog_Office.xml`) grants Robust + FineTuning. These two
    files are the *only* place these commands appear (stages grant via the `MasteryAcquired` marker
    above), so this closes the non-stage grant channel. Place labels are authored
    (`OFFICE_GRANT_FILES`, en/kor — `알버스의 방` matches the `StoryOfficeAlbus` achievement). Only
    Yearning + FineTuning were otherwise sourceless; the other 6 are also enemy-learnable.
  - **Opened for research (mutual-open groups)** — a story mission's dialogue choice awards **one**
    mastery of a group but **opens the whole group for research** regardless, so the others are
    craftable even though you weren't handed a copy. The opens live **only** in
    `script/server/missionResult_Custom.lua` (the `.stage` carries just the `MasteryAcquired` grant
    marker): each `MissionResult_Custom_<Stage>` handler branches on the choice, calling
    `dc:AcquireMastery(company,'X',1)` for the awarded one and
    `dc:UpdateCompanyProperty(company,'Technique/Y/Opened',true)` for the rest (`dialog_map.parse_mission_opens`
    parses these per branch → `{granted → opened-companions}` + the union `opened` set + the gating
    `branch_vars`). **Key finding:** across the game *every* opened mastery is also **granted by the
    same mission on another branch** — there are **no** purely-opened masteries; "opened" precisely
    marks the losing members of an either/or grant group. **6 EP1 story missions:** Firefly Park
    (two either/or pairs — PositiveMind↔Ambush, Patience↔PangOfConscience; Deftness is always
    granted, never opened), Silverlining (Alacrity/HighSpeed/Supporter), Pugo Street
    (Challenger/Consideration), Stop and Look Back (ForthrightStatement/GraciousRefusal/Frankness),
    Hansol Street (Wanderer/Breakthrough), Sky-wind park (Hysterie/TacticalRetreat +
    HeroResponsibility/HeroDontGiveUp; Waiting & ColdRefusal are granted-only, never opened).
    - **Two surfaces.** (1) *Masteries tab* — the awarding **Story** source is flagged
      `opened:true` (via `mastery_opens`, matched on mission title), rendered with a "(still opened
      for research even if not awarded)" note. Applies to **all 6** missions. (2) *Dialogue tab* —
      an `○ Opens X for research` consequence beside the `★ Grants` on each choice (`_gated_consequences`
      rides the existing grant attribution: the choice that grants a member opens its companions).
    - **Choice-gated vs outcome-gated → the outcome tier (`OUTCOME_GROUPS`).** The Dialogue-tab opens
      that ride a real `<Choice>` are emitted only when `branch_vars ⊆ choice_vars`. Two missions
      instead decide the award by **outcome**, not a menu, so they get **synthetic "outcome"
      decisions** (`_inject_outcome_decisions`): **Sky-wind park** — battle outcome
      (`Sion_Allelimination`/`Sion_Escape` = defeat-all-vs-leave-park, `Irene_Win` = defeat-Luna-vs-
      don't-give-up) **nested under** the `PlayerSelect` character pick (the traced grant chips it
      lumped on the Sion/Irene picks are stripped and shown split under the outcomes); **Silverlining**
      — positioning (who reaches the office phone to contact the VHPD, or destroying all jammers;
      `SelectionPlayType`), a **top-level** decision since the mission surfaces no `<Choice>` at all.
      Grants/opens come from `parse_mission_opens` (`grants_by_choice`/`companions`); the outcome
      **labels are authored** (`OUTCOME_GROUPS`, en/kor, lifted from the mission's objective text where
      one exists — "Defeat Delivery brother" / "Leave the park" / "Defeat Luna" / "Don't give up") and
      also replace the stage-traced pick / bare "mission reward" as the **Masteries-tab** Story choice
      (`outcome_labels`).
    - **Lua choice→grant override (`grants_by_choice`).** The `.stage` `MasteryAcquired` markers are
      only a **toast** (wrapped in `EnableIf TestCompanyTechniqueNotOpened`); the real grant is the Lua
      `dc:AcquireMastery`. Where a stage's award scenes don't cleanly separate by choice, the Lua does:
      **Hansol Street**'s win cutscenes fire on *who dies* (each ORing over both `TeamID` values), so
      `_trigger_choice_keys` alone pinned **both** team masteries to the one "Heixing" option. So
      `parse_mission_opens` also records each branch gated on **exactly one** `var==val` as
      `grants_by_choice[(var,val)] → granted_mid` (Hansol: `TeamID` 1→Breakthrough, 2→Wanderer); both
      `mastery_grants` (Masteries-tab choice) and `_gated_consequences` (Dialogue-tab grant key) let
      that map **override** the stage attribution for those masteries — correcting Hansol Street on
      both surfaces. Multi-var branches (Stop and Look Back's `SionPhase01 AND AlbusPhase01`) and
      outcome-var branches (Sky-wind park) are **not** recorded, so they stay stage-driven. A
      **post-pass** still suppresses `Opens X` for any companion already `Grants X` under the same
      choice, as a general safety net.
- **Available from the start** (`technique_initial`, `initial:true`): a mastery whose `Technique.xml`
  entry is `Opened="true"` is pre-researched — usable from the start with no enemy-analysis/research.
  **~30** such (Learning, the `Resistance1` set, the basic drone `Module_*`, the `TrainingManual`s);
  only **Learning** was otherwise sourceless. ⚠️ *Not* a world-map reward: `Worldmap.xml` lists
  Learning under the Wind Wall / Northern District division `<Reward>`, but that **Activity-Report
  special-reward** path is **vestigial** — `Company.xml` `ActivityReport/SpecialRewardIndex` defaults
  to `1` (= the `None` reward) and nothing ever sets it to the Mastery index, so `lobby.lua`'s
  `dc:AcquireMastery` for it never runs. (The division `<Reward>`/`<Section>` data drives the live
  area-**reputation** Vill bonuses, not this dead mastery grant.)
- **Company policies** (`group:"company"` — the tab merges two categories). The **8 `Company`**
  masteries are the *player*-adoptable company policies; the **~20 `Organization`** masteries in the
  same tab are **NPC-static** (the policy an enemy org gives its members) and get **no player source** —
  their **`Type` already names the owning org** (Street Thug, Smuggler, VHPD, White Tiger, Skull, …),
  so "used by whom" is inherent, nothing to source.
  The 8 = exactly `SetCompanyMastery.xml`, sourced two ways: **5 available from the start**
  (`company_initial` — `SetCompanyMastery` `Opened="true"`/`IsInitMastery="true"`: Scavenger,
  Expertise, CustomerSatisfaction, SenseOfBelonging, Individualism) → the same `Initial` source as
  above; **3 unlocked by a story mission** (`dialog_map.company_opens` — `missionResult_Custom.lua`
  `dc:UpdateCompanyProperty(company, 'CompanyMasteries/<id>/Opened', true)`, a **different** path from
  the unit `Technique/<id>/Opened`): HardFight ← *Magenta Street* (`Tutorial_PurpleStreet`),
  FastWork & SafetyFirst ← *Iron Forest Resource Management* (`Tutorial_Road_111`, a `PhaseStart`
  choice pair — but that multi-setter var isn't cleanly attributable, so only the mission is shown).
  These are **purely opened** (adoptable policy — no `AcquireCompanyMastery`/grant/copy exists), but
  reuse the plain `Story` source for display. All 8 were otherwise **sourceless** (and slipped the
  orphan tripwire, which only checks `normal`/`module` groups).
- **Mastery-research unlock** (`technique_data` → `research:[prereq names]`): a mastery is unlocked
  by **crafting/researching** another mastery — `Technique.xml` `UnLockTechnique` links X → Y
  (researching X unlocks Y for research). The unlock is tied to the **act of crafting** the prereq
  (verified in-game: a free copy of the prereq isn't enough — you must craft it), so this is the
  `UnLockTechnique` **forward-link reversed**, *not* `RequireMasteries` (which is the recipe's
  *ingredient* list). Crucially these are different scales: **720** techniques have `RequireMasteries`
  (the tree mirrors nearly the whole roster — a blanket "researchable" line off *that* would be
  noise), but only **23** have `UnLockTechnique` — i.e. only 23 masteries are actually "unlocked by
  crafting X." So it's an **additional source on all 23** (shown even when the mastery is also
  enemy-learnable). The chains: the nine Resistance ladders (Blunt/Piercing/Slashing +
  Fire/Ice/Lightning/Earth/Water/Wind, each II ← I, III ← II), Understanding ← Learning, Insight ←
  Understanding, and King's Wealth ← Treasure Island ← AliBaba ← Treasure Hunter.
  Renders "Unlocks after crafting <prereq>" with the prereq as a cross-link.
- **Developing (cut) masteries dropped** (`technique_data` developing set): a mastery whose
  `Technique.xml` entry is `Developing="true"` is unfinished/cut — not obtainable in the live game.
  5 such (Mental Break, Sherlock, Detective, Immersion, Vanquished); their only "sources" were a
  Developing research path and/or a no-mission-appearance enemy carrier. Dropped from the web tool
  under the default `exclude_dev` (kept with `--include-developing`), like Developing jobs/sets.
  This (with the channels above) brings **board-category masteries with no source to zero**.
- **Enemies with no mission appearance — audit.** The original audit found **375** of **861**
  mastery-carrying monsters placed in no mission, categorized as (pre–fixes below, which revise the
  total to **317**): Joint Training clones **156** (fought in that mode — tagged ⚔ with their team;
  but **57** are Beast-arena packs reachable only via the Developing `Beast` matching rule, so they're
  dropped — see "Enemy compositions" below — leaving **99** live),
  Civilian rescue NPCs **93** (now skipped as sources entirely — see "Enemy source" above), captured
  **Beasts 68** (now fewer — the Draki egg/elemental/hatchling forms in this bucket resolve via the
  egg-hatching system below), VHPD/police ally & difficulty-modifier variants **27**, character/guest
  battle units (`Mon_Albus`, …) **13**, drones **6**, story NPCs **4**, named/boss variants **4**,
  dev/test **2**, ability-spawned objects **2**. None use a `Developing` flag — Monster.xml has
  **no** such attribute, so a "cut" enemy is simply one never placed (no flag to miss).
- **Off-roster spawns (Draki eggs) — the one live exception.** An earlier pass assumed no
  off-roster spawn introduces a carrier; that was wrong. The **Draki egg-hatching** system places
  mastery-carriers into a mission *without* a static `<Enemy>` entry: a stage `<DrakyEgg
  DrakyEggType="…">` generator (the `DrakyEggType` attribute is optional and defaults to the plain
  `DrakyEgg` pool) drops eggs that hatch into a random elemental Draki (at its `Prob`) or the
  tameable hatchling. The pools live in `Beast.xml` idspace **`BeastHatching`** (`<EggList>` +
  `<MonsterList>`); `missions.beast_hatching_pools` resolves them so the eggs **and** every hatch
  result count as appearing. Used in **Raid_DrakyNest2** ("Draki's Nest", 27 generators, plain
  pool — elemental stage-1 Draki + `Draki Hatchling`), **Raid_CaveBase** ("Iron Forest Secret
  Base", `DrakyEggBlack` → Mutant/`Dot` + *Draki Hatchling from the Black Eggshell*), and
  **Tutorial_BeastTrafficking** ("Iron Forest Poaching Camp", plain + `DrakyEggGold`). This is the
  *only* spawn vector that surfaces a carrier — vetted alternatives that do **not**: monster
  `InstantProperty` `DestroyedMonsterType`/`HatchedObject` is just the egg lifecycle + the Yasha
  spider-egg + `Fortress` objective team-flips (non-carrier `Object_` units); the Yasha buff-hatch's
  product (`BlackWidow1`) is statically placed anyway; `GameObject=` dynamic refs add only the Draki
  eggs. The genuinely no-appearance Draki that remain are the **Mutant (`Dot`) and Golden (`Gold`)
  special-egg lines' evolved adults** (`Dot2/Dot3`, `Gold2/Gold3` + `_Ground`/`_Elite`) — obtained
  by hatching the black/gold egg then *evolving*, never placed as wild enemies; since evolution
  doesn't grant a form's starting masteries, their evolved-exclusive masteries aren't player-
  obtainable (beast-innate residue). This resolution drops the no-static-appearance carrier count
  from 375 to **344**.
- **Neutral-team third factions count (Raid_Pascal).** `_is_enemy_placement` used to discard any
  `<Enemy>`-tag unit on a `neutral*` team, on the assumption they were authored allies. That was
  wrong: a `neutral*` team is a *third faction that fights everyone* — Raid_Pascal ("Pascal's
  Hideout") releases its **12 test-subject beasts** on teams `neutral1`–`neutral6` (the stage has
  no `neutralN↔player` relation, so the engine default is hostile; the script dialogue spells it out
  — *"the beasts can also attack us"*). They're defeatable/tameable there, so they now count
  (a full sweep found neutral `<Enemy>` units only in Raid_Pascal — the genuine gap — and
  Tutorial_Silverlining, whose gangsters already appear as normal enemies elsewhere). This sources
  several previously-residue beast moves (Munggo's Icicle/Snowball, the `_Pascal_Boss` variants'
  kit) and drops the no-appearance carrier count **344 → 326**.
- **Directing-team actors don't count (Pugo Street).** `_is_enemy_placement` also excludes `<Enemy>`
  units on a **`Direct*`** team (`DirectTeam`/`Direct`/`Directeam`) — the engine's *directing*
  (cutscene) team. These play a scripted bit and **never engage** you, e.g. **Danny** & **Sharky**
  in Pugo Street, who retreat before you can reach them. The signal is the team name, not the
  relation: in Pugo the `DirectTeam↔player` relation is undeclared (so a relation check would
  default it hostile and miss it), yet they still don't fight — the engine treats directing units as
  non-combatants. Safe because: a sweep of 31 `Direct*` placements found 27 are also real enemies
  elsewhere (keep those appearances), 9 are flipped to a hostile team mid-mission (re-added by the
  ChangeTeam flip path), and only **Danny** is uniquely affected — and all 34 of his board masteries
  have other appearing carriers, so dropping him orphans nothing. *Not* dropped: units that merely
  start hostile then retreat/defect (they had a real combat window) — Orsay's Tima (you fight them
  until the Silver-meets-Anne cutscene), Luna/Marco in Silverlining-After. Relations are **static**
  (no runtime relation-switch actions exist); only team *membership* changes at runtime.
- **Team-level conversions count (`TeamChangeTeam`).** Besides unit-level `ChangeTeam`, a
  `TeamChangeTeam` action converts a *whole* team into another (`Team`→`Team2`). `build_enemy_missions`
  computes, per stage, the set of teams that become hostile (transitively converted into an
  `enemy*`/`Third` team) and counts units placed on — or unit-flipped onto — them. This is how the
  training-room **duel** resolves: you control one of two mirror teams (`AttackTeam`/`DefenceTeam`,
  both `ally` by relation) and the other is `TeamChangeTeam`'d to `enemy`. The closure correctly
  ignores `→player`/`→neutral` conversions (units that join/neutralise you), and since you can pick
  either side, both mirror teams' rosters are fightable across playthroughs. Net new across the whole
  game: **Maximillion** (`Mon_Troubleshooter_Hundred`) in the Drill Hall — flipped onto `DefenceTeam`,
  which converts to `enemy`. Everything else the mechanism touches was already counted (the source
  teams are `enemy*`-named, or the units appear as real enemies elsewhere). 0 new masteries, 0 orphans.
- **Boss-summoned units count (mission.xml `<Enemies>` manifest).** Each mission carries an
  `<Enemies>` roster (the data behind the in-game "Appearance Location" panel) of `<property
  Type="Mon_…" BattleJoinType="…">` entries. Most types (`None`/`Normal`/`Hard`/`Random*`) mirror the
  static stage placement, but **`SummonBeast`/`SummonMachine`** entries are units a boss summons
  mid-battle that are absent from the stage `<Enemy>` roster — so the stage parser can't see them.
  `missions.mission_summons` resolves these. Only **7** missions use it: **Tutorial_Pascal** ("Wind
  Wall District VHPD") summons Pascal's three drones (Terminator/Camouflage/First Aid →
  `Mon_Drone_*_Pascal`, `SummonMachine`); and Legend beasts are summoned via `SummonBeast` — Crimson
  Pig/Cloud/Fang in five missions (Crow's Ruin, Shadow Fog Marketplace, Metrodium Residence,
  Dust Wind Highway Garage, Blue Fog Distribution Center G) and Blue Glutton/Flame/Teeth in
  Tutorial_BeastTrafficking. Resolves **9** carriers (incl. Sleep Poison's carrier Crimson Cloud and
  Conceal in Bush's Blue Teeth), dropping the count **326 → 317**. (`<Enemies>` covers only 114/233
  missions, so it augments rather than replaces the stage parser.)
- **Blanket "no-encounter → not a source" drop + diagnostic.** After every appearance mechanism
  above, `write_web_data` drops any `Enemy`-type source that still resolves to **no encounter** (no
  mission, no Joint Training) — you can never fight it, so listing it is a phantom. This replaces the
  earlier per-category skips (only civilians were hand-skipped; the rest were validated one-off) with
  one rule, and is future-proof: it self-cleans whatever a game update leaves unencounterable. The
  safety net against silently hiding a *new* spawn mechanism is two build-time diagnostics:
  (1) it prints the dropped-carrier count and writes the full list to `web/dropped_no_encounter.txt`
  (a git-diffable artifact — a content update that changes it shows up), and (2) an **orphan
  tripwire** warns if any board (normal/module) mastery is left with **no** source of any kind
  (enemy/character/job/beast/drone/achievement/story/initial/research). Current run: **102**
  carrier ids dropped, **0** board masteries orphaned — i.e. every no-encounter carrier's masteries
  are obtainable from a fightable enemy or another channel, so the player-facing source list is
  complete. (The dropped ids are the validated non-fightable set: the **57** Beast-arena Joint
  Training packs (Developing `Beast` matching rule), Mutant/Golden evolution-only Draki adults,
  ally-only character/VHPD/NPC guests, unused/cut enemy-drone defs, and ability-spawned-object
  markers.) *(The one real gap this whole audit surfaced — Lucky 7/8/9's
  buff-trigger-count unlock — has since been handled via the Achievement channel; see "+3 hardcoded
  in lua, not GuideTrigger.xml" above.)*
- Achievement / Story are **additional** sources: most of these masteries are *also*
  enemy-learnable, but the feat or story choice is the intended/primary route, so the grant is
  added alongside any enemy source rather than replacing it.
- The mastery **board** itself only governs how many masteries of each category you
  can have *active* at once; it grants no masteries, so it is not a source.
- **Board Builder limits** are reproduced from the game's own Lua (`script/shared/shared_pc.lua`,
  `script/server/Unit.lua`) and validated against in-game screenshots. The extractor emits
  `jobs` (grade 1–2 only; grade-3 *awakened* jobs are excluded — unimplemented, all
  `Max…Count` = 0), `pcs` (with innate `element`, `baseMax`, and the character's join
  defaults `startLv` / `startJob` — the level and class they actually join in, which is an
  *advanced* class for late joiners, e.g. Giselle joins Lv31 as Sniper, not Gunman; from
  `Pc.xml` `Lv` and the unit `Object`'s `Job`), `espSlots`, `boardMods`, and
  `slotUnlock` into `window.TS_DATA`:
  - **Total cost cap** = character **level** (`Get_MaxTrainingPoint` = `Lv + BonusTP +
    MaxPower`, plus a few total-bonus masteries — Frankness/ColdRefusal/LoveHate/SocialLife).
  - **Slots per category** = PC `Base_Max<Cat>MasteryCount` (`Pc.xml`; an innate per-character
    bonus — e.g. Albus = `Base_MaxBasicMasteryCount=1` → +1 Basic, which the game shows on his
    individual-mastery (WindSword) tooltip rather than on the mastery itself) + job
    `Max<Cat>MasteryCount` (`Job.xml`) + the character's **innate element** (`object.xml`
    `Object/<pc>.ESP` → `ESP.xml` `Max<Cat>MasteryCount`; e.g. Albus = Wind → +1 Support) +
    any limit-modifier masteries placed on the board.
  - **Per-category cost cap** = `2 × slots − 1` + cost-only modifier masteries
    (`Get_Max<Cat>MasteryCost_PC`). The cap always uses *full* capacity, even when fewer
    slots are unlocked (Albus Lv19: Basic cost cap 13 with only 4 of 7 slots open).
  - **Slot unlock by level**: the natural (base + job + element) slots open progressively per
    `Mastery.xml` idspace `MasteryUnlockLevel` (Basic 1/6/11/16/22/32/42/52, Support
    2/7/12/17/24…, Attack 3/8/13/18/26…, Defence 4/9/14/19/28…, Ability 5/10/15/20/30…);
    mastery-granted slots ignore level and stack on top.
  - **Accessible masteries/sets** = `Common`/`All` + the character's personal type, race
    (Human), innate element, and the job's prerequisite tree (`Job.xml` `RequireClassLv` →
    `RequireConditions`). A job's `RequiredESP` is only a class-change prerequisite, **not**
    element access. The match is on the **raw `MasteryType` id** (`m.typeRaw`), not the display
    `Title` — so a drone's raw element `Heat` matches a module's raw type `Heat` directly (the
    display `Heating`/`Genetics`/… only differs from the id for a handful of types, which is why
    matching on display names silently worked for PCs/beasts but broke for drones). Accordingly
    `pcs[].pcType`, `jobs[].accessTypes`, and `beasts[].pcType` are emitted as **raw ids**.
    A job's `accessTypes` is the **prereq closure** (job + all recursive `RequireClassLv` classes),
    so an advanced class already carries its basic class(es) — e.g. Martial Artist →
    `[Dancer, Fighter, MartialArtist]`, Sniper → `[Gunman, Sniper]`. This is why there is **no
    separate "prior classes" concept**: gating on the *current* class's `accessTypes` already keeps
    the basics it descends from. The builder applies this one gate everywhere — the sidebar list, the
    interactive form/class switch (`bldSelectForm`, which asks before dropping now-incompatible
    masteries/evo picks), and `bldNormalized` (the single point every load/import/canon path shares,
    so a stored/shared build with an incompatible mastery heals on load). The share-code *import gate*
    is intentionally looser — a tolerant union-over-all-classes plausibility check that accepts the
    code, then defers strict per-class pruning to `bldNormalized`.
  - **Category** has the same split for the same reason (i18n): each mastery carries `category`
    (localized display, e.g. `개인`) **and** `categoryRaw` — the *English* category title (e.g.
    `Individual` / `Basic` / `Frame`), resolved from the `eng` dictionary even in a non-English
    run. All category *logic* (the Individual sub-tabs' `data-cat`, `CAT_COLOR`, `BOARD_CATS`,
    `MODULE_SLOT`, drone module `moduleCats[].nameEn`, slot mapping, `categoryRaw === "Beasts"`)
    keys on the English `categoryRaw`; only rendered text uses `category`. Note the English title
    is *not* the raw id (`Basic`←`Normal`, `Support`←`Sub`, `Class`←`Job`), so the engine key is
    the English title, not `c.get("Category")`.
  - **Drone access** uses the same engine but is additionally restricted to the **`module`
    group**: a drone's access set also matches plain `Common`/`All`/`Normal` masteries (174 of
    them), which a drone can't actually equip, so the builder filters those out — only the 112
    module masteries (minus the other-SP ones) reach a drone's sidebar. **Two module types are
    SP-agnostic** and not covered by the element/race/job access set: `Application_Control`
    (Control Program) and `Application_Enhancement` (Reinforcement Program) — every drone can place
    them regardless of SP, so `bldAccessTypes` adds both when `race==="Machine"`. (Trap: the raw
    types `Application_Control`/`_Enhancement` are *also* worn by `group:"individual"` OS
    reinforcement picks — a different mechanic; the `module`-group filter keeps those off the board.)
  - **Mutually-exclusive masteries** — `Mastery.xml` has an `ExclusiveMastery` rule
    (`type="table" subtype="string"`) naming masteries that can't sit on the same board. Only one
    pair uses it: `DescendantOfWarrior` ⇄ `DescendantOfGuardian` (each lists the other). The
    extractor emits it as `exclusive: [ids]` (omitted when empty); the builder flags a placed pair
    via the `broken[]` warning list (it warns, matching the over-cap handling — it doesn't hard-block
    the click).
  - **Mungo "Mimic" class access** — Mungos (family `Munggo`) equip a class's masteries via the
    Mimic mechanic, which isn't a `Type`/`accessTypes` on the unit — it lives in the `Desc_Base` of
    the `Munggo*` masteries as a grant sentence whose `FormatKeyword` refs are **all** `Idspace="Job"`
    (the paired substitution sentence mixes in `Idspace="Mastery"` refs — skip it). Two shapes:
    the base `Munggo` mastery (Category `Job`) has one grant property per weapon, gated by
    `CaseType="ItemType"` (`BattleGlove→Fighter, LongSword→Swordsman, Dagger→Thief, Pistol→Gunman,
    TwoHandClub→Warrior, Axe→Marauder, Spray→Thrower, Bangle→Mage`); a named `Munggo_<class>`
    (Category `Beast`, in a form's `FixedEvolutionMastery`) grants an advanced class + its basics
    (e.g. `Munggo_Barbarian` → Barbarian/Warrior/Marauder). The extractor resolves each grant's jobs
    to the union of their `accessTypes` closures and emits per-form:
    - **advanced** forms (`Mon_Beast_Munggo_<weapon>`, e.g. Black Eyepatch): `mimicAccess` = the
      `Munggo_<class>` grant;
    - **basic** mature forms (`Mon_Beast_Munggo_Base_<weapon>`, e.g. Swordsman Mungo): `mimicAccess`
      = the weapon's basic class (weapon from the id suffix — the only glue not in the data; every
      suffix equals its `ItemType` except `MartialArtist`→`BattleGlove`);
    - the weapon-agnostic **adolescent** `Mon_Beast_Munggo_Base`: `mimicChoice: true` (no fixed
      class). The builder splits it into one Form-dropdown entry per weapon (from the global
      `mimicWeapons` table) backed by a persisted `mimic` pick, so a board commits to a single class
      (you can't mix two classes' masteries). `bldAccessTypes` adds `mimicAccess`, or the chosen
      weapon's class for a `mimicChoice` form; `bldNormalized` prunes anything outside it.
    Every Mungo form is its own coded roster unit (`MasteryCode.xml` `Beast`), so all are shareable;
    the `mimic` pick rides the link/localStorage like `evo`, leaving the bare in-game code untouched.
  - **Limit-modifier masteries** (curated from `shared_pc.lua`'s `Get_ExtraMax*MasteryCount_PC`
    / `Get_MaxMasteryCost_Shared_PC` lists × each mastery's `Base_ApplyAmount`): slot bonuses
    feed the cost cap via the `2×slots−1` formula — e.g. Self-Examination +1 Basic slot,
    Hysteria / Brazenface +1 Attack slot, Social Life +Ability slots & +total; cost-only
    bonuses (PangOfConscience, Consideration, …) raise every category's cap.
  - **Individual masteries** (`Category=="Individual"`) are *not* board-placed — they're a
    free single pick — so the builder excludes them from the five columns.
  - **Beast evolution masteries** (`Category=="Beast"`, 61 of them): at each evolution a beast
    picks 1-of-3 from a pool decided by the mastery's **`Type`** (logic in
    `script/shared/shared_beast.lua` `GetBeastUniqueMasteryCandidate` / `PickBeastUniqueMasteryCandidate`):
    `Training` (15, the only "changeable"/re-pickable), `Nature` (18) and `Gene` (22) are **global**
    pools (any species); the `ESP` type is **element-limited** — only masteries whose `Type` equals
    the beast's own element (`Object.ESP`: Fire/Ice/Lightning/Wind/Earth/Water, one each) are eligible.
    The `BeastUniqueEvolutionMastery` list (`Flight`, `GoldScale`, `Illumination`, the 8
    `Munggo_<class>`…) is **excluded** from all global pools and granted only to specific forms
    via each form's `<FixedEvolutionMastery>` table (`Name`/`Slot`/`Rate`) — these are the `unique`
    ones in the extractor. **Trap:** the separate `BeastUniqueEvolutionMastery_Genetic` list (the 5
    `GrowthPotential_*` +2-slot picks: Giant Skeleton / Flexible Muscle / Engraved Fighting Spirit /
    Strong Desire to Survive / Expanded Neuron) is **NOT** unique/form-gated — they're `Type=Gene`,
    available to *any* beast (the "genetic modification" scope), so they live in the **global Gene
    pool**, not a form's fixedEvo. `extract_masteries.py` must **not** fold `_Genetic` into
    `beast_unique` or they vanish from every growth-stage picker. NB these are picks, not
    board placements, and several affect board limits (slots/cost) like human Individual masteries —
    fed into `boardMods` (the `BeastXTraining`/`GrowthPotential`/`AdaptiveTraining`-type ones).
    `beast_availability()` tags each with a player-availability scope (global / element / species /
    genetic) for the Individual tab. **Species** masteries are offered only by *specific forms*, not a
    whole family: a 1-form mastery is labelled with that form's name; a multi-form one whose offering
    set matches a **hand-named group** in **`beast_groups.json`** (a curated `{group → [form ids]}`
    file — e.g. "Melee Draki", "Mature Crabmit", "Golden Beasts") gets the group name. That label
    prefixes the mastery name in the tab; the row detail lists every offering form.
  - **Drone (Machine) masteries** (`Category=="Machine"`, 46): the same shape one axis over. A drone
    is crafted from a **Frame** (`MachineCategory` Rifle/Flamethrower/… — carries the board slots),
    an **SP Structure** (Heat/Info/Charge) and an **OS** (`MachineAIUpgrade` Windows/Linux/MacOS =
    Multi-Processing/Open/Independent), then **Reinforced** (Normal→Remodeled→Reinforced→Complete),
    each stage offering a 1-of-3 pick from the OS pool (`script/shared/shared_machine.lua`
    `GetMachineAIUpgradeMasteryCandidate`, gated by `Lv < stage`). The mastery `Type` ≈ how it's
    acquired: `Operating System` (the OS choice) / `Reinforcement Program`+`Control Program` (the
    OS-pool reinforcement picks) / `Output`+`Performance`+`Compatibility` (craft-rolled stat traits,
    `MachineCraftUniqueMastery`). The Drone tab groups by `Type`; reinforcement picks also get an
    **OS-pool prefix** ("Any OS" / a single OS / a 2-of-3 combo) reusing the `formGroup` field.
  - **Drone level-grants** (`Machine.xml` `MachineType/<unit>/Masteries`): levelling a drone's class
    auto-unlocks modules. There are 18 units (6 frames × 3 SPs); each unit's `Masteries` has **four
    outer `<property>` groups = the reinforcement stages** (Normal/Remodeled/Reinforced/Complete),
    and within each, grants at `RequireLv` 1/8/16. So a grant's identity is (mastery, lv, **stage**):
    e.g. Fire-Fighter Lv 1 = Water Resistance at Normal but Auxiliary Support Module at Reinforced.
    Two axes determine each grant, **both verified clean**: **frame-determined** (granted across all
    3 SPs of a frame — the Lv 1/16 tiers) vs **SP-determined** (granted across all 6 frames sharing
    an SP — the Lv 8 tier). The unit `Title` is just the frame name ("Scout Drone"), dropping both
    the SP and the stage, so the extractor re-derives them: an SP grant reads **"Heat SP (Lv 8)"**
    and a non-base stage is tagged **"… · Reinforced"**. (`iter()`-flattening the groups, as the
    first cut did, silently merges all four stages onto the base label.)
  - **Crafted-drone level** is set by the engine's `dc:NewMachine` (not in the readable data —
    `LobbyAction_CraftMachine` never overrides `Lv`, and the `Machine` root class's `Lv="1"` isn't
    what's applied); it's progression-scaled (observed ~40). The builder defaults a new drone to
    **Lv 40** (`DRONE_CRAFT_LV`) since Lv 1 would show almost no slots unlocked — adjustable.
  - **Craft-unique pick** (`MachineCraftUniqueMasteryGroup` + `MachineCraftUniqueMastery`): at
    construction a drone rolls passive stat traits (`MachineUnique_*`, `Type` Performance /
    Compatibility / Power) — group `Count`s are Power 0 / Compatibility 1 / PerformanceA 2 /
    PerformanceB 1 / PerformanceC 1, picked by `PickMachineUniqueMasteryCandidate` (replaceable
    later with a paid 1-of-3 reroll). **Not yet modelled in the builder.**
- Not included: one-off story/quest unlocks (e.g. "change Kylie's class to Hacker"),
  which live in quest scripts rather than a data table; and the `Category=="System"`
  masteries (Dummy/Obstacle/Explosives/object metadata), dropped entirely.
- Descriptions are fully resolved: `resolve_desc.py` reproduces the game's text
  formatter, replacing every `$Placeholder$` with its real value — numbers from
  `Base_ApplyAmountN` (`Percent` → `%`), stat/buff/type names via idspace `Title`
  lookups, translated literals from the per-line FormatKeyword dictionary entries,
  and global words via `WordCollection`. All ~7600 tokens across 2269 descriptions
  resolve (0 left unresolved).
  - **`ValueType="table"` keyword** — the `Key` is a *comma-separated list* of ids (e.g.
    `Idspace="FieldEffect" Key="IceMist, PoisonGas, AcidGas, CorrosionGas, PlagueMist"`, the
    field effects a mastery is immune to). `resolve_one_keyword` splits on `,` and resolves
    each id under the idspace, then rejoins. Looking the whole list up as one key always misses,
    so before this the raw ids leaked through in **both** languages (caught via the Korean page).
  - **Some ids have no static `Title` because the name is resolved at runtime from unit context.**
    `MaxSP` is the prime case: `Status/MaxSP` has only `Desc_*`, no `/Title`. In-game the SP-gauge
    name is **ESP-element-dependent** — `CalculatedProperty_Status_Customized_MaxSP` reads the
    unit's `Max<ESP>Point`, and `shared_tooltip.lua` titles it `Status/Max<ESP.name>Point/Title` =
    `"<Element> SP"` (화염 SP / 발열 SP / 물 SP / …), with the *empty* `MaxSP` Title as the no-unit
    fallback. (Distinct from the **action-cost** resource `Base_CostType`, which *is* a flat
    human/beast/drone split — Vigor / Rage / Fuel — and a different stat from the SP gauge.) A
    unit-agnostic mastery library has no single right element, so `_title` maps the title-less
    SP-gauge stats (`SP_GAUGE_STATS` = `MaxSP`, `MaxAddSP`) to the **generic "SP"** (the common
    suffix of every variant; same string in English and Korean). Without it the raw `MaxSP` leaked
    into ~48 mastery descriptions (*Sincerity*, *Deathblow*, *Crammy*, …).
  - **`Vill` is faithful** — `CurrencyType/Vill/Title` is literally `[!재화]Vill` (the `[!재화]`/
    currency markup stripped), so the game keeps "Vill" even in Korean. Don't "fix" it.
- The resolver is **reusable for abilities** (same `Desc_Base`/FormatKeyword shape):
  `resolve_description(dic, cls, idprefix="Ability")` resolves `Ability/<id>/Desc_Base`. Bare
  ability-text tokens (`$ApplyBuff$`→its `ApplyTargetBuff` title, `$RemoveBuff$`, `$BuffGroup$`,
  `$ApplyBuffChance$`→`%`, …) come from `OWNER_REF`; colour-markup (`$White$`/`*_ON`/`*_OFF`) is
  stripped. `$BuffGroup$` resolves via the `BuffGroup` idspace, but the attribute differs by owner
  type: **abilities store an actual buff family in `BuffGroup`** (Stealth) → a linkable **group chip**;
  **buffs store their *damage element* in `Group`** (the `Enchant*` buffs → "Fire"/"Ice"/…, matching
  the game's `GetBuffGroupText` → `buff.Group`) → rendered as **plain text**, since it names the element
  not the Fire buff family. `resolve_owner_token` reads `BuffGroup`, falling back to `Group` (never both
  set) and suppressing the chip on that fallback. (This also cleans up the six *Call of &lt;Element&gt;*
  board masteries, whose description is that buff's effect via the `describe_buff` fallback.) All
  ability tokens now resolve, including **`$DamageAmount$`** (the per-hit damage formula — see below).
  Mastery output is unchanged (the `idprefix` defaults to `Mastery`).
- **`$DamageAmount$` — the per-hit damage formula** (`resolve_damage_amount`, a port of
  `shared_tooltip.lua GetDamageAmountText`). The game renders it as a **base number** followed by each
  scaling stat as `(+<pct>% <stat>)` — e.g. Surge of Blades (`WaveSlash`) → "100 (+75% Attack Power)
  (+25% Speed)", Raindrop Slash → "200 (+100% Attack Power)(+100% Speed)(+100% SP)". Two non-obvious
  sources: (1) the **base number is `ApplyAmountChangeStep`**, *not* an `ApplyAmount` attribute — no
  ability sets `ApplyAmount` in the XML; the engine computes it as `CalculatedProperty_Ability_ApplyAmount`
  = `ApplyAmountChangeStep[Lv]` (`shared_status.lua`), a per-level array, and we take the Lv-1 (single)
  value. Base `0` → no number shown (only the stats). (2) the **stat percentages are the child
  `<AdditionalApplyAmount>` `<property name= value=>` list**, rendered in XML authoring order (which
  matches the game's display order). Stat **titles** follow the Lua: normal stats use
  `Status/<stat>/Title_HPChangeFunctionArg`; the specials are hand-mapped (`HP`→`Status/MaxHP/Title`,
  `EnemyHP`→"Enemy "+that, `SP`→the title-less SP gauge's generic **"SP"** — the game shows the unit's
  *"<Element> SP"*, but the library is unit-agnostic, so generic like the no-target tooltip). The first
  term is bare (no parens) only when there's no base; otherwise every stat is parenthesised.
  - **`Cost` is the unit's action resource** (Vigor/Rage/Fuel), so it's unit-dependent — the no-target
    game tooltip omits it. We instead resolve it from the ability's **owner units' `Base_CostType`**
    (Human→Vigor / Beast→Rage / Machine→Fuel, per `object.xml`): `build_player_abilities` returns a
    `cost_type` map (aid → the distinct owner resources), and `build_abilities` passes the localized
    `CostType/<res>/Title` as `cost_label` to `resolve_description`. All ~137 Cost-scaling abilities in
    the default build are beast abilities → **Rage** (e.g. Fireball → "100 (+75% ESP Power)(+50% Rage)").
    Owners that disagree, or an unknown owner, **join every candidate** (`COST_RESOURCE_ORDER`
    "Vigor/Rage/Fuel") rather than drop the term — defensive; no ability in the current data hits it.
- `describe()` builds a mastery's description in the game tooltip's line order
  (`script/shared/shared_tooltip.lua` `GetMasterySystemMessageText`): **authored `Desc_Base`**, then
  **debuff-immunity line(s)** and **terrain field-effect immunity** as consecutive lines, then the
  **flat-stat deltas** (`$StatusMessage$`) under a gold **"Extra Effect"** header
  (`WordCollection/AdditionalEffect` → 추가 효과) **when any content precedes them** (`not isEmpty`),
  else as the description itself. These co-occur — e.g. the elemental-skin monster masteries
  (`HotMonster`: reinforcement text + "Immune to Fire type debuffs." + an ESP-damage "Extra Effect"),
  `Legend2` (+30% combat effect + HP/Vigor/Speed/Block block), or `Flight`/`Hovering` (authored text +
  field-effect immunity). If **none** apply, fall back to the linked **buff** effect text. (Earlier
  versions dropped the immunity and the stats whenever authored text existed.)
  - **Debuff immunity** (`immune_debuff_summary`): a mastery with `ImmuneDebuff_BuffGroup="true"`
    grants immunity to the groups in `BuffGroup`/`SubBuffGroup`, rendered from the GuideMessage
    template `Mastery_ImmuneDebuff_BuffGroup` (one group) / `…_BuffGroup2` (two — no mastery lists
    more). E.g. *Bluffer* (`Braggart`) → "Immune to Panic and Silence type debuffs." Sits right after
    the authored text and combines with stat lines (*ToadSkin*: poison immunity + Vigor regen).
  - **Field-effect immunity** (`neutralize_field_summary`): a mastery whose `NeutralizeFieldEffect`
    lists terrain effects it ignores (`Hovering`/`Flight` → Swamp, Water, Ice, Lava…) — one line
    joining each `FieldEffect/<id>/Title` into the `NeutralizeFieldEffect` GuideMessage
    (`$FieldEffectList$`), e.g. "You are immune to Swamp, Water, … field effects."
  - **Flat-stat deltas** (`stat_summary(dic, mastery, status_fmt)`): each non-`Base_Title` `Base_<Stat>`
    via `Status/<stat>/Desc_Increase|Decrease`. `status_fmt` supplies the trailing **`%` for Percent
    stats** (Block, Hit Chance, Attack damage… — the template omits it, the engine appends it from the
    stat's `Format`); flat stats (HP, Vigor, Armor, Resistance) stay bare.
  - **Linked buff**: `describe_buff` on the mastery's `Buff` (below).
  A mastery that only *grants an ability* gets no description line — the web tool surfaces the
  granted ability via a "Grants ability: *X*" cross-link chip (`grantsAbility`), shown inline in
  the description column and the row detail, so a text line would just duplicate it.
  - **Genuinely description-less** (4, all `ApplyAmount`-only with no buff/ability/template):
    `Vanquished`, `BloodThickerThanWater`, `DeathScent`, `MentalBreak`. These also have no normal
    source (e.g. Vanquished is carried only by the enemy 디미트리/Dimitri, who appears in **no
    mission** — Dimitri is a quest/shop NPC, not a fightable unit), so they're likely unobtainable.
    Left in for now; not filtered.
- **Desc_Base "case" prefixes** — each `Desc_Base` `<property>` can carry a `CaseType`/`CaseValue`
  the game renders **before** the line's `Text` (`shared_tooltip.lua` `MakeMasteryDescBaseOneline`):
  a section header (`Custom` — a localized literal in `Desc_Base/<n>/CaseValue`), a condition list
  (`FieldEffect`/`MissionWeather`/`MissionTemperature`/`ItemRank`/`CoverCondition`…), or a reference
  to another `Mastery`/`Ability`/`Buff`/`Status`/`MasteryType`/`Race`/`ESP`/… The `CaseType` **is**
  the idspace; ids resolve to `Title`s (a comma-list when `CaseValueType="table"`). The caseTitle
  joins its `Text` with a newline (`CaseLineBreak="true"`, where the `Text` is then **block-indented**
  as it's "within" that case) or `": "` — so `Neutralization` → "**Physical attack:**
  Ignores enemy's Armor by 25%", `WildLife` → its Field/Weather/Temperature blocks with indented
  effects, `Legend2` → "**When attacking:** …/**When attacked:** …". A caseTitle with **empty `Text`**
  is a bare *section header* whose effects arrive on the *following* standalone (`None`-case) lines
  (e.g. `Module_FrameEnhanced` → "Reinforced Machine" then three effect lines); those get indented
  too via an `in_section` flag — set by an empty-`Text` header, cleared by a self-contained caseTitle
  (so a general trailing note like DarkHunter's "While Revealed…" stays flush) or a paragraph break.
  Indentation is **not** literal leading spaces: each indented line is prefixed with a **block-indent
  marker** (`\x11`, `resolve_desc.INDENT` / `indent_block`) that the web renders inside a padded
  container (`.desc-indent`) so the *whole* wrapping line stays indented, not just its first row (spaces
  only indent the first row once descriptions became rich text). `strip_refs` flattens the marker back
  to 4 leading spaces for the plain-text (`output/`) dumps. `parseMarkup` renders one block per source
  line (blank lines → a one-line paragraph gap via `.desc-line:empty`). A property with
  `LineBreak="true"` ends a paragraph — the game appends an extra newline (`GetMasteryMasteryDescBase
  Text` joins with `\n`), so the resolver emits a **blank-line separator** there (the gap above each
  `WildLife` section header). `resolve_desc._case_title` handles all of this; **`Mastery`/`Ability`/
  `Buff` case refs are emitted as positioned inline markup** (the sentinel triple — see "Inline
  references" below) so the web chips them — which is why the ability-reinforcement masteries (e.g.
  Irene's *A Million Years of Training* → 9 Red Panda forms + secret/ultimate) need **no** special
  `shared_ability.lua` parsing: the ability id is right there in `CaseValue`. Predicate-scope
  modifiers (`ModifyAbility="Custom"` with a `Checker` category, e.g. *Seasoned Hunter* → all
  support/item abilities) carry no case list and name their scope in the authored text already.
- **Buff effect text** — `describe_buff(dic, buff, status_fmt)` reproduces the engine's
  buff auto-tooltip "stat core": authored `Desc_Base` + flat `Base_<Stat>` deltas **and level-scaled
  `Eval_<Stat>` deltas** (both via `Status.xml` `Desc_Increase`/`Desc_Decrease`\[`ByLevel`], per-stat
  `Format`, through the shared `_stat_delta_line`) + action restrictions + an aura line + duration.
  `Eval_<Stat>` values are runtime expressions; `_parse_eval` handles the `coef * Lv` family (also
  `Lv`, `-Lv*coef`, bare constant) → the "… per level" template (≈23 buffs, e.g. Boosts_Speed /
  Break\* / Frozen Blood). A `Base_<Stat>` can also be a **level-indexed list** (`"100, 200, 300"` =
  the value at buff Lv 1/2/3); the **first (Lv 1)** value is taken, matching the in-game tooltip for
  the freshly-applied buff — without this the whole stat line was dropped (`float("100, 200, 300")`
  throws), so e.g. **Faith / Brave** (Excitement family) rendered *no* effect at all. 320 buffs now
  resolve; the remaining tail (HP-over-time, discharge/reflect, in-range fortress auras, `ImmuneRace`)
  is deferred (TODO "Statuses / buffs" Stage 4). ⚠️ **Titles collide** — several distinct buffs share
  a `Title` (the enemy **Anger** state and the Excitement **stat** buff both read *Rage* / 분노), so the
  Title-keyed `DATA.buffs` (built by `build_buffs`, first-non-empty per Title, for the *inline* buff
  refs in descriptions which only carry a Title) can hold the wrong one. Dialogue **"gains X"**
  consequences instead carry the buff **id** and resolve via a separate **id-keyed** `DATA.buffsById`
  (`build_dialogue_buffs`, only the ids a dialogue actually references — 16 buffs), so *Rage* there
  correctly shows +100 Attack/ESP, not "can attack civilians." (The inline-ref path is still
  Title-keyed and can mis-resolve a collided Title — a separate, lower-value gap.) Mastery
  *debuff-immunity* (`ImmuneDebuff_BuffGroup`) is a separate `immune_debuff_summary` in the mastery
  `describe()` fallback, not part of `describe_buff`. The shared `format_message` does `GuideMessage`
  `$token$` substitution, colour-markup stripping, and **Korean josa** selection (`apply_josa` —
  `[이]/[은]/[을]/[과]/[로]` chosen by the preceding syllable's 받침); josa is also applied inside
  `resolve_description` (a no-op for mastery/ability text, which carries no markers). The aura
  `Ally/Enemy/Any` relation noun has no game dictionary entry, so it's translated directly
  (`AURA_RELATION`, keyed off `Dictionary.lang`).
- **Inline references (buff / buff-group / mastery / ability chips)** — every chip-able reference is
  emitted **inline and positioned** in the resolved text, as a control-char *sentinel triple*
  `\x01kind\x02label\x03` (`_ref` in `resolve_desc.py`, `kind ∈ buff|group|mastery|ability`). It is
  written **exactly where the game renders that reference** — an `Idspace="Buff"/"Mastery"/"Ability"`
  FormatKeyword substituted into the text, a `$MasteryMastery$`/`$MasteryBuff$` `MASTERY_REF` token,
  an `$Explosion$` `OWNER_REF`, or a `CaseType` prefix (`_case_title`). Two same-named refs therefore
  keep distinct kinds — a module-mastery header `⟦mastery|Emergency Fuel⟧` vs the ability token
  `⟦ability|Emergency Fuel⟧` on the same line link to the right target, which name-substring matching
  (the old approach) could never do. Sentinels also dodge two traps: control chars never collide with
  real text (unlike `[...]`, which the source overloads for josa), and Korean josa still attaches to
  the label's last syllable because only the transparent `\x03` sits between it and a `[을]` marker
  (`apply_josa` skips it). **The web** (`app.js`) parses the triple: `parseMarkup` renders a chip per
  ref (mastery/ability → clickable pill + card hover; buff/group → effect/`DATA.buffGroups` card
  hover; absent target → plain text), and `stripMarkup` flattens to the bare label for plain-text
  contexts (tooltips, search blobs, compact previews, filters). Plain-text exports
  (`masteries.json/.csv/.md`) are stripped via `strip_refs`; only `web/data.js` keeps the markup.
  Buff effect text lives in `DATA.buffs` (name → text, `describe_buff`) and group members in
  `DATA.buffGroups` (`build_buff_groups`), both looked up by the label at hover time. An ability's
  hover card also **expands the effect of each buff it references** (`appendBuffEffects`) — so a
  buff-enabling ability ("Trigger Ferocious Tima") shows what the buff does without a click-through.
  A ref whose display name carries a disambiguation suffix (`(2)`, `(High-Risk, High-Return)`) may
  not resolve against `masteryByName`/`abilityByName` and falls back to plain text — the sentinel
  `label` is the raw `Base_Title`.
- **Declared-but-unpositioned refs are dropped.** A `FormatKeyword` can *declare* a reference whose
  `$token$` never appears in the rendered `Text` (so no sentinel is positioned — e.g. ForbiddenBook
  declares `Mastery=MagicResonance` with no `$Mastery$` in its text). The game doesn't render these
  either (its tooltip builds from the same data), so they're simply not surfaced — an earlier
  "Related:" line that listed them was removed after confirming all ~8 cases are unused data quirks.

## The in-game Help corpus (`xml/Help.xml`)

`Help.xml` is the game's **encyclopedia/tooltip text corpus** — 400 entries in idspace `Help`,
grouped `HelpCategory` → `HelpSubCategory` → `Help`. Each entry resolves to English through the
same dictionaries the rest of the pipeline uses: `Help/<name>/Base_Title`, `Help/<name>/Base_Content`
(also `Content_Mission`, `Content_End`). The inline `Base_Title=`/`Base_Content=` attributes in the
XML are the *Korean source*; the dict key `Help/<name>/<prop>` gives the localized value. Each entry
also has a `Checker` (unlock gate — `HelpChecker_Hidden` marks cut/withheld topics, already used by
`extract_masteries.py` `hidden_classes()`) and an `Order`.

**Two body styles, and they yield very different things** (this is the key finding — don't re-derive):

- **Static `Base_Content` (101 entries with real prose)** — authoritative one-paragraph blurbs on
  individual abilities/masteries/buffs/mechanics (Tame, Overwatch, Iron Wall, Headshot, …). The
  **only part of the corpus with novel, surface-able text.** Bodies carry `$Token$` placeholders in
  the same family `resolve_desc.py` already handles: `$Idspace_Key$` → `Idspace/Key/Title` (e.g.
  `$Ability_Tame$`, `$Job_Hunter$`, `$MonGrade_Epic$`); `$Word_X$` → `WordCollection/X/Text`;
  literal attribute tokens read off the `Help` class itself (`$Amount$`, `$Percent$`, `$KeyText$`,
  `$ColorText$`, `$Note$`); colour markup (`$White$`, `$Blue_ON$`) drops. A naive resolver already
  cleans ~54/101; the rest need only a handful more token rules (a few hours, same class of work as
  the description resolver) — not a rabbit hole.
- **Generated (`AutoScript="GetHelpContent_*"`, ~297 entries, empty `Base_Content`)** — assembled at
  runtime by `script/shared/shared_help.lua`. **Mostly NOT new content — it re-lists data we already
  extract**, so reproducing the Lua buys little for *content*. Its value is as a **cross-check**:
  - `GetHelpContent_OSMastery` / `GetHelpContent_MachineBonusMastery` / `GetHelpContent_EvolutionMastery`
    just emit the **mastery list** for a category/type (icon + title, no prose). `OSMastery` sources
    the OS→mastery mapping from `MachineAIUpgrade.AIUpgrade` (`xml/MachineAIUpgrade.xml`) — an
    authoritative source to validate our OS-mastery grouping against.
  - `GetHelpContent_Class` builds the class panel **purely from `Job.xml` fields**: ability slots
    (`Basic`/`Normal`/`Ultimate`) and per-category board slots (`MaxBasicMasteryCount`,
    `MaxSubMasteryCount`, `MaxAttackMasteryCount`, `MaxDefenceMasteryCount`, `MaxAbilityMasteryCount`)
    + the class-mastery list. This **confirms the board-slot model** documented under "Board Builder
    limits" above: the game's own class encyclopedia shows the per-category slots as exactly the five
    Job `Max<Cat>MasteryCount` fields (verified Hunter = 5/6/5/3/6). No correction needed — validation.
  - `GetHelpContent_Race` emits `curRace.Desc`, but `Race.xml` has **no** `Desc` field, so race prose
    is empty (a dead end — some other generators like `GameMode` do have a real `.Desc`).

**Bottom line:** the static-prose half is the surface-able payoff; the generated half is our own data
re-listed, useful only for cross-checking (and the board-slot cross-check already passed).

## Content exclusion (default drop)

By default `extract_masteries.py` **drops content unavailable in normal play** (pass
`--include-developing` to keep it all). Two engine-authored signals drive this
(`compute_excluded_jobs`):

1. **`Developing="true"`** — the engine's "not in normal play" marker (a `Developing` job
   fails the `IsEnableJob` check in `script/shared/shared_job.lua`; masteries/sets sit behind
   the `EnableDevelopingMasterySet` = `false` system constant). Of the files the extractor
   reads, only `Job.xml` and `MasterySet.xml` carry it. Removes the 30 grade-3 "awakened"
   jobs **and** 4 unreleased grade-1/2 jobs (Merchant/Arithmetician/Gambler/Astrologian).
   Availability keys off `Developing`, not `Grade` (every grade-3 job is `Developing`, so the
   two agree on grade-3 while `Developing` also catches the grade-1/2 strays).
2. **Hidden classes** — `Help.xml` `Class_<name>` topics flagged `Checker="HelpChecker_Hidden"`
   (the permanent-hide marker; other Hidden topics are live contextual popups, so we scope to
   classes). A hidden class is dropped **only if no unit uses it as its `Job`** — so
   `Musician`, `Singer`, `Priest`, `Bowman`, `Crusader`, `Bard` go, while `Guardian` stays
   (the VHPD Defender enemy is a Guardian). `Merchant` is also a hidden-with-units class but is
   already removed by rule 1 (it's `Developing`).

Both rules feed one `excluded_jobs` set that drops those jobs from the board-builder list and
their unlock *sources* (character job-level + basic-mastery) from every mastery. Additionally,
a class's **basic-mastery trait** is dropped iff *every* job that uses it as a basic mastery is
excluded and no enemy carries it — so the genuinely orphaned traits go (Passionate Performance,
Beautiful Voice, Holy War, Accounting) while shared ones stay (Piety via Bishop, Firearm
Training via Gunman, Martial Art via Martial Artist). This fills the last blank-description gap
in the Class Traits tab (Musician's "Passionate Performance" etc.).

## Enemy appearances and mission case type

The Masteries tab's per-enemy "Appearance Location" panel resolves each enemy's missions and
their case-coloured level badges from the stage/mission data.

Case type: side-quest stages (`Quest_*` mission ids) are pulled out first into a synthetic
case we key as **`Quest`** (green) — shown in the tool as the game's own term **"Requested"**
(의뢰 사건; the other three, Scenario/Ordinary/Violent, already match the in-game case names).
This wins over everything, so all quest battles read apart from the main story regardless of
the case colour the game would give them (e.g.
`Quest_Hundred01` / `Quest_Leo01` are `Type="Hunting"`, which would otherwise be Violent). The
rest come from `ZoneEventGen.xml` (`Scenario`/`Raid`→Violent/`Normal`→Ordinary), falling back
to `Hunting`→Violent and the `Tutorial_`/`Scenario_` id prefix (→Scenario); recommended level
is the mission's `Lv`. Maps with no resolvable case (JointTraining/PvP/test) are omitted.
**Dev/test scaffolding stages are dropped**: `build_mission_index` skips any mission whose
localized `Mission/<id>/LocationTitle` is empty — these are dev scaffolding maps (e.g. mission
`nts` = `new_test_stage.stage`, `LocationTitle=""`, `Lv="0"`, `Type="Hunting"`) that otherwise
leak in via the `Hunting`→Violent fallback and show up as a bogus "Violent ?" appearance
(rendered from the raw id + level-0 badge) on every enemy placed in them (Jeff, the Crabmits, …).
Every real mission has a localized title, so this only removes the junk.

NPCs that a story choice turns hostile (pre-placed `<Neutral>` units flipped to a hostile
team by a `ChangeTeam` action) are shown as `(dialog: <branch>)`. When the enclosing
trigger is gated by a stage variable that a `DialogChoice` sets, the branch is that
**choice's text** (e.g. Alisa at Ramji Plaza → "Did you do all of this?!", Jason → "Fight.");
triggers with no such choice link fall back to the humanized trigger name.

## Abilities tab

The **Abilities** tab (built from `Ability.xml`) is a table of the **player-usable** combat
abilities (~600 rows), emitted as `DATA.abilities` by `build_abilities()`.

Non-combat rows are skipped first: `Type="Interaction"` (47, context-sensitive actions next to
interactable objects), `Type="Move"` (3, generic movement), `*_Disable` ids (41, the
"Remove/Deactivate <aura>" toggle-off companions), and `*TrapActivate` ids (5, the detonation an
already-placed `*Trap` fires when triggered — the placeable `*Trap`/`Type="Trap"` ability is kept).
The rest is filtered to **player-usable only** (`build_player_abilities()`, below), dropping the
enemy-/NPC-/effect-only abilities.

Column sources: **Slot** (Basic/Normal/Ultimate) · **Type** (Attack/Support/Heal/…, `AbilityType`)
· **Element** (`SubType`, resolved via the **`AbilitySubType`** idspace — which covers both the
elemental types and the physical classes Slashing/Blunt/Piercing/EMP, where `MasteryType` carries
only the elemental ones) · action **Cost** · **CD** (`CoolTime`) · **Cast** (`CastDelay`) ·
resolved **effect**. The **hit-rate** (`HitRateType`, e.g. `Melee`/`Ranged`) resolves via the
**`AbilityHitRateType`** idspace (근접/원거리…) — it has *no* entry in `MasteryType`/`AbilitySubType`,
so it needs its own idspace or the raw English id leaks. The row detail adds range / targets /
hit-rate / SP, flavor, and the masteries that **grant** or **modify** it (chips → Masteries tab).
Descriptions resolve via `resolve_desc.py` (`idprefix="Ability"`); the `$DamageAmount$` per-hit
damage formula is resolved at build time into the description text (base + `(+pct% stat)` terms — see
"`$DamageAmount$` — the per-hit damage formula" above), so the web tool no longer needs its old `X`
placeholder swap. **The same physical-class trap bites the in-text
`$DamageType$` token** (`OWNER_REF["DamageType"]` in `resolve_desc.py`): it must resolve `SubType`
via **`AbilitySubType`**, not `MasteryType` — otherwise Slashing/Blunt/Piercing/EMP leak as raw
English inside the effect text even though the Element column (which already uses `AbilitySubType`)
shows 참격 correctly. A mastery that grants an ability shows a "Grants ability:" chip in its detail
(`grantsAbility`), linking to the Abilities tab. **Subcommand abilities** (`ApplyScp="ABL_SUBCOMMAND"`,
e.g. the six *Call of &lt;Element&gt;* controls) carry only a runtime `$SubAbilityMessage$` that
resolves to nothing, so `build_abilities` **synthesizes** their description — a
`WordCollection/AbilitySubMenu` header ("Ability Submenu" / 어빌리티 하위 메뉴) over the sub-abilities
they open, taken from the non-`_Disable` `AutoActiveAbility` targets and emitted as inline ability
chips (`ref_markup`), so Call of Fire → Ignite / Extinguish.

**Finding untranslated leaks (`--report-unresolved`).** `extract_masteries.py --report-unresolved`
prints every dictionary miss where a lookup fell back to a raw English id or left a `$token$`
literal — the tool for hunting the Slashing/Melee class of bug at its source (grepping the output
JS drowns in dialogue/quest EventIDs and character names). It is gated behind
`resolve_desc.REPORT_UNRESOLVED` (zero overhead when off), instrumented at the two fallback points
`_title()` and the extractor's `title_or_raw()`, plus the literal-token passthrough in
`resolve_description`. Two gotchas it surfaced: (1) **Item names live under `Base_Title`, not
`Title`** (amulets, potions — e.g. `Item/Amulet_Collector_Set`, `Item/Potion_Scourge`), so `_title`
now falls back Title→Base_Title (same as the Mastery branch) or item names referenced in
descriptions leak as raw ids in every language. (2) **`$Target$`** reads the owning ability's
`Target` attribute (a relation enum) and resolves via the **`TargetType`** idspace (`Enemy`→적,
`Ally`→아군, `Any`→모든 대상) — `OWNER_REF["Target"]`, mirroring the game's own "…deal X damage to
Enemy" text. (3) The rest are **not** translation bugs and none reach the UI: `$Overcharge$` sits in the unreferenced
`충성적`/Loyal beast-loyalty buff (0 `buffRefs`, never hovered); and `1/Height` is a **typo in the
game's own `Mastery.xml`** (`Idspace="1"` on a `Height` FormatKeyword — real entry is
`Help/Height/Base_Title`) whose mastery (`CoverPredator`) shows its `Conceal` buff text instead, so
the resolved-then-discarded `Height` never surfaces.

**Player-usable filter + "which unit" badge** (`build_player_abilities()`). The game gives every
unit its *own* ability id for the same move (enemy `Slash` on `Mon_Gangster_*`, android
`Android_Slash`, beast `Slash_Munggo`, player `CalmSlash`), so listing all named abilities mixes
player and enemy copies. An ability is **kept** iff it's reachable by the player, via any of:
a character's **class unlocks** (`Pc.xml` `<char>/EnableJobs/<job>/<Abilities>` — each
`<property Name= RequireLv=>` is an ability learned by levelling that class; the authoritative,
character-specific "granted by levelling" source); a **character battle unit** `PC_<Name>` /
`Mon_PC_<Name>` in `object.xml` (`Ability` list — `PC_*` = the playable 12, `Mon_PC_*` = story/guest
chars, controllable on some maps & meaningful as named enemies, so kept — adds loadout sub-abilities
and the guest chars that have no Pc.xml entry); every **`Item.xml` `Ability`** (weapons/devices/
potions/grenades/sprays — "any unit can equip", + the spray `<id>2` upgrades via `item_sources`); a
**mastery that *grants*** it (`Mastery.xml` `Ability` only — *not* `ModifyAbility`/`ChainAbility`,
whose targets are reinforced abilities already reachable, or chained sub-effects like the cloud/
explosion a player ability triggers; and **excluding `Category="System"`** masteries — environmental
hazards like the poison-puddle *Poison* / *Obstacle*, whose `Ability` (*Toxic Leak*) is a hazard
attack no player fields); a **roster beast/drone unit's** `Ability` (`DATA.beasts`/
`DATA.machine.units`; drones carry none of their own — theirs come via equipped devices, i.e. the
item path); or an **`AutoActiveAbility` toggle-companion** of an already-kept ability — activating/
holding X auto-grants its other stance Y (Ferocious Tima→Nimble Tima, AttackStance→DefenceStance, the
Enchant/Release element halves, …; the `RemoveBuff` pair mutually disables). Followed transitively and
inheriting the source's owner; the toggle-**off** `*_Disable` companions it also reaches are still
culled by the `*_Disable` name rule below. This recovers the ~38 real toggle/stance/sub-mode abilities
that no `Ability=` grant or unit list reaches (they'd otherwise be dead inline chips). 765→630. Each kept row carries an **`owners`** list — the character(s)
(`ObjectInfo/<Name>/Title`, e.g. *CalmSlash*→Albus, *NinjutsuMist*→Misty) and/or beast **family**
title (*Neguri_TongueStorm*→Negoori) that field it — shown as inline `owner-badge`s and an
"Available to" detail line, and indexed in search. Item/mastery/weapon abilities carry no owner
(Slot conveys the source).

Class unlocks come from `EnableJobs/<job>/<Abilities>` (the `RequireLv` unlock list), unioned with
the character's battle-unit loadout (which adds sub-abilities and guest chars). The sibling
`<ActiveAbility>` / `<AbilityPriority>` lists are job-templated AI tables that over-list abilities
onto characters who never unlock them (e.g. both tag `BattlebreakerSnipe` onto Kylie though only
Giselle's Sniper unlocks it), so they aren't used. Battle-unit loadout alone *misses* owners
(Irene's `FlashAura`/`RippleKick`, Kylie's `FireTrap`/`Tame` are class-unlocks absent from the unit
loadout), which is why the two are unioned. `PC_<Name>` (playable) vs `Mon_PC_<Name>` (story) marks
the player/enemy split; the *unprefixed* `<id>` is usually an enemy's (`Slash`→gangsters,
`WindSlash`/`HurricaneSlash`→enemy/beast).

**Slot doubles as the access source.** Item-/mastery-granted abilities have no real ability
`SlotType`, so the otherwise-empty Slot column records *how* you access them instead:
**Potion** (30) / **Grenade** (20) / **Spray** (8) / **Device** (6) — from each `Item.xml`
class's `Ability="<id>"` keyed by item `Type` (`ability_item_sources()` → `_item_slot_label()`;
`Intravenous` folds into Potion, gadget item types Gear/Sneakers/Bracelet + `*Device` into
Device) — else **Mastery** (42) if a mastery grants it (item source wins ties). Higher-rarity
sprays swap in an upgraded `<id>2` ability ("Upgraded Shaking Shot", wired in
`shared_ability.lua`, *not* `Item.xml`), so each spray id also tags its `<id>2` sibling. The
Slot values are translatable (`ability.slot.{potion,grenade,spray,device,mastery}`).

## Dialogue tab

The **Dialogue** tab shows the stages that have branching choices, built by `dialog_map.py` from
each stage's event-graph. **Raid (Violent) and Common (Ordinary) missions are excluded**
(`build_dialog_map` drops any stage whose missions all have `Raid_`/`Common_` ids): their only
choices are deploy/entry-route picks and "boss defeated / done — continue or retreat?" prompts,
which carry no story decision. Kept are the story-scripted stages — the **Scenario** (story/
tutorial) and **Quest** (side-quest, `Quest_*` ids) cases — even when a stage surfaces no choice,
so their full script still renders (`is_story` in `build_dialog_map`). Quest stages carry `Quest_`
ids, so the Raid_/Common_ drop never touches them regardless.

Each decision's consequence is tagged (fight / join / leave / third-party / buff / reward /
mission outcome / **objective**). "Joins you" is driven mainly by the party-membership action (`UpdateUserMember`
On/player) — the reliable signal — with `ChangeTeam→player` as a secondary; "leave" is
`UpdateUserMember` Off/player. A choice that ends the battle shows "Win the mission" or "Fail the
mission" — read from the `Win` action's Team (the engine's `Win` ends the battle *for a Team*:
player/empty = the player's win, an enemy `Win` = the player's defeat; `MissionFail`/`Lose` are
also "Fail the mission"; same split the full-script view shows as "end battle (victory)" /
"(defeat)"); one that starts a countdown timer is classified by what the timer's expiry does —
running out = **win** shows "Switches objective to survival (<timer text>: <limit>)" (e.g. a raid
"Retreat" → "Time Left Until Return: 150"); running out = **loss** shows "Adds a time limit — lose
if it runs out (…)"; otherwise (a reinforcement/event countdown — an ally arrives, a wave spawns)
the neutral "Starts a timer (…)". The expiry outcome is read from the trigger gated by the timer
reaching its `LimitTime` (its `Win Team="player"` / `Lose` / enemy-`Win` action, falling back to
the trigger's name). Wins gated by a survival timer, by destroying all enemies, or by unit deaths
are not credited to the choice (they're the objective/loss). Consequences are built from the stage
event-graph: a choice sets a stage variable, and a `VariableTest`-gated trigger runs the
consequence (`choice → variable → trigger → consequence`). Direct and 1-hop consequences are
resolved; deeper multi-hop chains aren't traced. Choice/prompt text has CEGUI markup stripped.

A choice that **rewrites the mission objective** shows a `New objective: <text>` consequence
(kind `objective`) — read from an `UpdateDashboard` action whose commands are
`UpdateObjectiveMessage` + the new objective's message key (`_objective_change`). This is the
command form the DLC missions use (e.g. Crimson Crow's "Shadow of the Past" terminal choice →
"Put all enemies Out of Action"); it's **distinct** from the `VictoryCondition` action the
full-script view already renders as "◎ objective —". The same `UpdateObjectiveMessage` actions
occurring inside triggers now also render in the full script as `set objective: <text>`. (Without
this, a choice whose only effect was an inline objective swap looked empty — no consequence and no
"triggers N rules" line.)

Each stage also has a collapsible **Full script**: its trigger graph rendered as
pseudo-code, with the choice-consequence rules omitted (they're already shown under the
options above), so it reads as the stage machinery *not* driven by player choices — the
toggle notes how many were omitted. Each rule reads `ONCE WHEN <conditions>` (one-shot trigger) or
`WHENEVER <conditions>` (a `Repeat="true"` trigger that fires every time). A trigger that
starts `Active="false"` and is never switched on by any `ToggleTrigger`/`ToggleTriggerGroup`
is dead and dropped (e.g. a superseded per-character version left beside the combined one).
`ToggleTrigger`/`ToggleTriggerGroup` actions render as `enable/disable trigger: <X>` (the
common case is mutual exclusion — the first of a set to fire disables its siblings); a
trigger that *starts* inactive carries its activation as a leading `enabled by <X>`
condition (citing the **group** when it's switched on via its group, e.g. `enabled by
group JoinEvent_01`, so it lines up with the `enable group` / `disable group` actions and
the sibling triggers that share it), so both ends of the enable show. Writes to a variable that nothing ever reads
(no condition, binding, or expression) are dropped as dead bookkeeping. Rules are then **grouped by the variable they test**: each rule joins
the group of its most-used variable in the stage (so a story flow gated by `EventID` forms
one `by EventID:` group), bucketed by that variable's value and sorted numerically
(`EventID == 1 / 2 / 3`). Within a value bucket the same grouping recurses one level
(e.g. `EventID == 2` → `by SionSelect:` / `by Sion:`), and each leaf rule drops the
variables already shown in its header chain. Rules whose value sits inside a nested OR
keep their full `WHEN`. Body actions: (variable sets/tests, team changes,
spawns, removals, buffs, rewards, objective/battle-end), with camera/UI noise
filtered out. An action-body `Switch` (where only the matching branch runs — e.g. a
different scene per spotter, or a `math.random` outcome) is shown as a foldable
`⎇ switch on <expr>:` with each case's actions under it, rather than every branch flat;
cases with no displayable actions are skipped, and a switch whose every branch is noise
(e.g. per-value camera framing) is dropped entirely.

Dialogue isn't stored in triggers — triggers invoke `MissionDirect`
cutscene sequences, so a "play scene: <name>" action is shown and the scene's actual
spoken lines (`Speaker: "…"`, choice prompts) are resolved from the `MissionDirects`
container and inlined beneath it (behind a per-scene "lines" sub-toggle). Condition
`<unit> state` checks render the real state ("in/out of battle", "has/lacks <buff>").
Verbose multi-unit `AND`/`OR` groups (the game repeats a unit-predicate per instance)
are **folded**: units that share a predicate set are listed once as `all of {…}` (AND) /
`any of {…}` (OR), repeated instances collapse to `xN`, a lone unit reads `Unit pred` /
`Unit: p1 and p2`, and the object side of relational predicates folds too
(`Giselle spots any of {…}`). A mutual `A moves next to B` + `B moves next to A` pair
collapses to `A is next to B`. For an insight/spot check, the spotting **direction** comes
from `ConditionOutput` (not the type name): a `UnitInsightToTeam` whose finder is the
searched team-member and which names a concrete unit reads `<searched> spots <unit>`
(e.g. `a Spoon unit (not Sharky) spots Albus`), with the `SearchUnitFilter` rendered. Sibling clauses are merged on either axis: same unit set →
`of {…}: all (…), any (…)`; same predicate set, different units → quantified by the parent
connective (`any of {…}: dies and lacks <buff>`). Non-unit terms (variables, timers) pass
through. A shared conjunct is **distributively factored** out of an `OR` whose branches are
all `AND`s — `(A and C) or (B and C)` → `C and (A or B)` — which both shortens the condition
and lets the residual `(A or B)` fold (e.g. a 6-branch boss check `1117 → 199` chars).

Conversations **chain**: when a choice triggers a scene that presents the next
choice, that follow-up decision is nested under the option (recursively), so a
multi-step exchange reads as one tree instead of separate decisions. Only the root
decision shows at top level; follow-ups appear under the choice that leads to them.
A parent option's consequence badges are **de-duplicated against its nested subtree**
(`_dedup_nested_consequences`): the option's flattened list drops any badge that a
decision rendered below it already shows per sub-choice (identity = kind + text +
mastery/buff id), so e.g. Sky-wind park's Anne pick no longer repeats the Dorori/Anne
effects its "What should I do…" sub-choices detail — nothing is lost, only un-repeated.
Within a decision, options that are **fully equivalent** — the same consequence *and*
the same triggered rules — are grouped into a single entry (bulleted, accent-bordered)
showing that consequence/script once; options that share a consequence but trigger
different scripts (e.g. Irene Rush vs Irene Cover) stay separate, as do options with
different or no consequences. Each choice option also shows **what it triggers**: the rules gated by the
variable that option sets (`choice → variable=value → matching WHEN/DO rules`) are nested
under the option, so you can follow a choice straight to its scripted outcome. When a
triggered rule is gated *only* by the variable the choice set, the option row itself folds
open to its consequence script directly (no redundant rule-title / `WHEN <that choice>` /
"triggers 1 rule" lines); any rules with extra ambient conditions (e.g. a phase variable)
appear below it as "triggers N additional rules". The extracted top-level consequence tags
stay above the script.

Each decision shows **what makes it appear** ("appears via N rules"): a choice lives
inside a `MissionDirect` scene, so the rule(s) that play that scene are surfaced,
chasing scene→scene invocations up to the triggering rule (resolved for ~82% of
decisions; the rest are invoked by scene-chaining/opening flow that isn't traceable). The engine ends a battle with a `Win` action scoped to a
Team, so it's read by team: a player/empty `Win` is "end battle (victory)", an enemy `Win`
"end battle (defeat)" (matching `Lose`/`MissionFail`).
Script text (variables, buffs, units) is searchable. Covers the stages that have branching choices.

## Quests (Shooter Street NPC requests)

The **Quests** tab lists the side-quests handed out by the Shooter Street lobby NPCs. All
of them are `EP1_Quest*` classes in `xml/Quest.xml`'s `Quest` idspace, extracted by
**`quests.py`** (`build_quests`, called from `extract_masteries.py`; baked per-language into
`web/data.js` under the `quests` key, like everything else). 84 quests across 12 clients
(`Client` attr): the intro `EP1_Quest01` from **Jean**, then chains from Al, Oleg, Dembel,
Sam, Savana, Camila, Dimitri, Leo, Bruna, Roberto, and **Maximillion** (whose NPC id is
`Hundred`). (Two `Group="Repeat"` quests are excluded as unreachable dev placeholders — see
below.)

Key resolution (all via the dictionary — the inline xml attrs are Korean):
- **Title / Objective**: `Quest/<id>/Title_Base` / `.../Objective_Base`. Many quests have only
  a template `$QuestType$ - $TargetItem$` with **no dictionary entry** — fall back to the
  inline `Title_Base` attr *only when it contains a `$` placeholder*. Placeholders `$QuestType$`
  / `$TargetItem$` / `$TargetCivilName$` are expanded here; `[!tags]` and Korean `[을]/[를]`
  josa markers are stripped.
- **NPC name**: `ObjectInfo/<client>/Title` (only `Hundred`→"Maximillion" and `Sabana`→
  "Savana" differ from their ids).
- **Quest type label**: `QuestType/<type>/Title` (Hunting / Secure Object / Contest / …).
- **Target name** (`$TargetItem$`) cascades by type: kill-drop →
  `QuestType_CollectItem_Kill/<t>/Title`, cargo/property → `CollectItemSet/<t>/Title`, plain
  collect → `Item/<t>/Base_Title`; rescue target (`$TargetCivilName$`) →
  `CitizenGenSet/<t>/Title`.
- **Location**: `Mission/<missionId>/LocationTitle`, for quests tied to a fixed stage
  (`MissionClear_*`/`Talk` Target, `<DirectMission Mission=>`, or `<Missions>` children).
  Pure collect/kill quests have no fixed stage — the enemy they drop from (`<TargetMonster>`
  → monster names) is shown instead.
- **Rewards**: the `<Reward>` block is the **pick-one-of-three** set (the schema's separate
  `RewardSelectableItems` is unused), plus an always-granted `Base_RewardNPCFriendship`.
  Reward `Type`s: `Item`/`Recipe` → `Item/<id>/Base_Title` (`Statement_Mastery` = "Training
  Manual"); `RandomTroublemaker Value=Criminal|Beast|Wanted` → info on a random troublemaker
  of that category (`TroublemakerCategory/<v>/Title`); `RandomRecipe Value=<profession>` →
  craftable-item **workmanship** in that profession (`Profession/<v>/Title`, e.g. Clothes =
  "Costume Making").
- **Prerequisites**: `PriorQuest` is comma/space-separated and can point at a *different*
  NPC's chain — e.g. `EP1_Quest_Roberto02` needs `EP1_Quest_Hundred01` (Maximillion). The web
  tool shows a "Requires NPC #N" badge for any prereq that isn't the immediately-preceding
  quest in the same NPC chain (catches cross-NPC unlocks and same-chain merges like Camila's
  06+07 → 08). `chainIndex` is the 1-based position within the NPC's chain (by `ProgressOrder`).
- **`RequireStageLv` → gating mission** (`unlockMission`): `RequireStageLv` is **not** a
  character level — it's a *story-progress* gate. The server (`Quest_NPC.lua`) blocks accepting
  the quest until `GetScenarioProgressGrade(company)` (the **highest `ProgressOrder` among the
  company's cleared missions** — `shared_mission.lua`) reaches it. Since only main-scenario
  missions carry a `ProgressOrder` (1..65) and it runs *parallel to but offset from* the
  recommended level (the mission at `ProgressOrder` 20 is `Lv` 18), the raw number reads
  misleadingly as a level — so `quests.py` resolves it to the **earliest story mission whose
  `ProgressOrder ≥ RequireStageLv`** and emits `unlockMission = {name, level, case}` — the
  mission's scenario name (chapter + title, via `build_mission_index`; falls back to
  `LocationTitle`) plus its recommended `level` and `case` (for the colour-coded `lvl-badge`;
  always `Scenario`). `RequireStageLv` = 0 (no gate) and the repeat sentinel 99 (past the last
  mission) resolve to nothing.
- **Repeat quests dropped**: the two `Group="Repeat"` quests (Maximillion) are **disabled dev
  placeholders** — `RequireStageLv=99` exceeds the highest reachable scenario `ProgressOrder`
  (65), so `TestQuestStart` (`server/Quest_NPC.lua`) always fails their stage-progress gate and
  they can never be accepted in game (also flagged by their `Lv=999` and `[!Ignore]` titles).
  `build_quests` filters them out, like other unshipped content.
- **Joint Drill unlock**: a quest with a `<JointTraining>` child (only `EP1_Quest_Roberto05`,
  the final Roberto quest) permanently opens the **Joint Drill** mode — flagged
  `unlocksJointDrill` and shown as an extra "🔓 Unlocks Joint Drill" reward on the card,
  alongside the always-granted friendship.

## Joint Training (Joint Drill)

**Joint Training** (합동훈련 / "Joint Drill") is a single-player practice-battle hub.
All its data lives in `xml/JointTraining.xml` (one idspace per choice axis); the runtime
logic is `script/server/jointtraining.lua` (+ `ai_jointtraining.lua`,
`script/shared/shared_jointtraining.lua`). It is **not** DLC-gated — base-game EP1.

- **Unlock**: quest `EP1_Quest_Roberto05` ("합동훈련", client Roberto) — needs the prior
  `EP1_Quest_Roberto04`, **Stage Lv ≥ 51**, **Company Lv ≥ 1** (`Quest.xml`, the
  `EP1_Quest_Roberto05` class), and clearing the `JointTraining_Occupation_Police_Tutorial`
  mission. Menu gate: `company.JointTrainingMenu.Opened and Progress.Tutorial.JointTraining >= 5`
  (`lobby_enter.lua`). The tutorial flow sets `Progress/Tutorial/JointTraining` and flips
  `JointTrainingMode/Duelling/Opened = true` on completing the Duelling tutorial
  (`missionResult_Custom.lua`). (Earlier guess that this was DLC2 was wrong — the
  `Mon_JointTraining_*` clones' fuller mastery sets misled it.)

- **A match = three player choices** resolved in `jointtraining.lua`
  `BuildJointTrainingBotSetting`: **Mode** × **BotMatchingRule** × **TeamSelectID** (an index
  into `AvailableBotTeamsByCompany(company, rule)` — the per-company-unlocked team list).

- **Modes** (`idspace JointTrainingMode`) — only two are live (`Developing="false"`):
  - **Occupation / 점령전** (`Opened="true"`) — capture-point. Win by wiping the enemy, holding
    every fortress, or reaching **1500 occupation points** (points tick per turn-wait per fortress
    held). Fortress buffs `Fortress_Obey/Brilliance/Love/Protection`; a `CallRejoin` tactical
    ability; `Tame` + `Potion_Immortality` disabled (`idspace JointTrainingDisableAbility`).
  - **Duelling / 결투 대항전** — 2-on-2 elimination relay; the winning pair carries
    HP/EP/cooldowns into the next bout, the losing team gets a stacking `FightHard` buff.
    Ships `Opened="false"`; opened by the Duelling tutorial.
  - `BeastBattle` (야수 투기장), `Seige` (공방전), `Strategy` (전술전) exist but are
    `Developing="true"` (unfinished).

- **Opponent source** (`idspace JointTrainingBotMatchingRule`) — picks *what* you fight and
  *what you earn*:

  | Rule | Exp/Traits | Loot | Activity-report bonus |
  |---|---|---|---|
  | **Regular / 스토리** (VHPD teams) | ✅ | ❌ | ✅ — +1 case resolved, **3,000 Vill + 100× `Statement_Mastery`** per win on report claim |
  | **Extra / 가상** (enemies from across the game) | ✅ | ✅ | ❌ |
  | **Player / 플레이어** (snapshots of other players' winning rosters) | ✅ | ✅ | ❌ |

  **Extra (가상) is the item-farm mode** — full loot drops, no report stipend. Drops come from
  the defeated `Mon_JointTraining_*` units' own tables, not a special JT loot table. (`Beast` rule
  exists but is `Developing`.)

- **Enemy compositions** (`idspace JointTrainingBotTeam`) — hand-authored fixed rosters you
  select from. Each pins a **map** (`Area → Mission`) and a `MemberPreset` (members are `Fix`ed
  or drawn `Random`ly from a pool; presets can be weighted by `Ratio`). Members are the
  `Mon_JointTraining_*` clones (see Data model notes). The teams: `RandomNamed` (무작위 팀,
  whole-pool weighted draw) and `RandomNamed_Player` (ghost team); the faction squads
  `VHPD`/`VHPD2` (바람장벽 경찰청 기동대), `AngryBull` (성난 황소), `Smuggler` (밀수업자),
  `Street` (거리 불량배), `WhiteTiger` (백호), `Skull` (스컬), `Spoon` (스푼교),
  `TwilightMercenary` (땅거미 용병단), `Contractor` (청부업자), `VendettaAndNineDragon`
  (벤데타와 구룡회), `WanderClown` (유랑광대단), `HuntingGroup` (밀렵단), `RedMineGangs`
  (붉은광산지구 폭력배), `RedSandMercenary` (붉은모래 용병단), `PascalAndSerpent`
  (파스칼과 뱀주인 삼인방); plus the beast packs (무작위 야수 무리, 철의 숲
  Tima/Neguri/Draki/Dorori, 황금모래 Crammy, 지하수로).
  - **Live vs Developing is the team's `MatchingRule`, not the team or its `AvailableMode`.**
    `JointTrainingBotMatchingRule` flags **`Beast` as `Developing="true"`** → every beast pack
    (all `MatchingRule="Beast"`) is cut. `Regular` (스토리/Story — "train vs Valhalla PD teams", e.g.
    `VHPD`/`VHPD2`), `Extra` (가상 — the faction squads) and `Player` are live. So `VHPD` is live via
    `Regular` even though its `AvailableMode` is only the `*_Tutorial` (Developing) modes — `MatchingRule`
    is the authoritative gate. `extract_masteries.joint_training_teams` maps each clone → its live
    faction-team title(s); a clone reachable *only* via a `Beast` team is **not** a real Joint Training
    appearance and is dropped (default exclude-dev). Of 156 clones, **99** are live and **57**
    (the Beast-only packs) drop. The web tool tags each ⚔ Joint Drill row with its team(s)
    (localized), e.g. "⚔ Joint Drill — Spoonism" (in-game display name is "Joint Drill" /
    "합동훈련"; `WordCollection/JointTraining/Text`).

- **Other axes**: ban/pick rules `Blind` (free) / `Draft` / `Draft2` / `NoBanDraft`
  (`idspace JointTrainingBanPickRule`); turn timers `Basic` 60s / `SlightlyFast` 30s / `Fast`
  15s / `Unlimited` (`JointTrainingWaitingRule`); extra modifiers `ConcealCharacterInfo`,
  `DisabledRejoin` (`JointTrainingExtraRule`). Maps: 22 Occupation + 10 Duelling areas with a
  per-area `Ladder` flag (`idspace JointTrainingArea`).

- **Help corpus**: 10 `JointTraining_*` classes in `Help.xml` (Mode, MatchingRule —
  these redirect to the `Desc` of the JointTraining.xml classes — and direct-content
  OccupationBoard / Rejoin / Fortress / FortressOccupationEffect / DuelingBoard). Korean inline;
  English resolves via the `keymap.dkm` + `dic_*.dic` path (see [How the data is stored](#how-the-data-is-stored)).

- **Not yet surfaced in the tool**: which **team** each Joint-Training-tagged enemy belongs to
  (the `MemberPreset` membership), and any in-app reference for the mode itself. Deferred until
  the mode can be unlocked and seen first-hand (see TODO).

## Importing a build from the game

Two ways to get an actual in-game board into the Board Builder's **Import**:

### 1. Paste a board share code (no mod needed)

The game's mastery-board **share code** (the `KSAAC…` string the in-game share button
produces) is **fully reverse-engineered — both decode *and* encode**. Paste one into Import
and it decodes the character, level, and every mastery; Export emits a share code for the
current build that the game imports back. Works for any code, including other players'.
`decodeShareCode()` / `encodeShareCode()` live in `web/app.js`; the lookup tables are
`web/codemap.js` (emitted by `extract_masteries.py` from `MasteryCode.xml`, via `emit_codemap()`, on the English build);
`decode_board.py` is the reference implementation + test harness.

**Format** (validated 100% against 24 of our own boards + independent online codes; encode
**round-trips byte-for-byte**, so there's no checksum):

- **base32**, custom alphabet `23456789ABCDEFGHIJKMNPQRSTUVWXYZ` (drops the ambiguous
  `0 1 L O`), 5 bits/symbol, MSB-first.
- **The whole code is one period-5 bit scramble** — each raw 5-bit group `[a,b,c,d,e]` is
  stored as `[a, b^c, ~c, d, e]`, with groups aligned so bit 49 (the start of the mastery
  list) is a group boundary (un-scramble from bit 4). Un-scramble and the entire stream is
  plain:
  - **Header** (bit positions in the un-scrambled stream): a constant magic prefix, **roster
    type** `[16:20]` (1 = PC / 2 = Beast / 3 = Machine, also the 5th symbol `C`/`E`/`G`),
    **character** `[24:28]` (`MasteryCode` `Pc` code), **level** `[29:36]`, **job** `[37:44]`
    (`MasteryCode` `Job` code).
  - **Mastery list** (from bit 49): a sequence of **type-groups**, ascending by `MasteryCode`
    type number:

    ```
    group = [type#:7 bits][count:8 bits][ code ]×count
    code  = LEB128-style varint, MSB-first byte, high bit = "another byte follows"
            (values ≥128 use 2 bytes: low 7 bits, then high 7 bits)
    ```

    Groups are joined by a **1-bit separator**; the stream is zero-padded (in the raw frame,
    *before* scrambling) to the next base32 character. `MasteryCode.xml` maps each mastery to
    its `(Type, per-type Code)` — types keep the per-mastery numbers small (the Lua comment:
    *"to reduce the size of the code value"*) and the varint keeps the common case to one byte.

  The per-5-bit scramble has no functional purpose (it's not a valid Gray code, checksum, or
  alignment aid) — it's light obfuscation so codes can't be hand-crafted; the *structure*
  (type-grouping + varint) is the genuine size optimisation.

- **Excluded from codes**: a character's fixed/innate masteries (e.g. Leton's
  `FrozenBlood` — not in `MasteryCode.xml`) and equipment-granted masteries; the builder
  auto-pins the fixed ones anyway.

- **Import sanity gate** (there's no checksum, so a random base32-ish string can base32-decode to
  a few stray masteries). Before `bldFromShareCode` accepts a decode it requires: **rosterType ∈
  {1,2,3}**, **level ∈ [1,99]**, **every type-group entry maps to a known mastery** (`decodeShareCode`
  returns `keyCount`; reject if `ids.length !== keyCount`), the **char id resolves to a real unit**
  whose **kind matches the roster tag**, and **every decoded mastery is accessible to that character**
  (checked against the union of all its classes' access types, so a board keeping a mastery from
  another class passes but a different character's mastery doesn't). Valid codes always satisfy this
  because the encode round-trips byte-for-byte. `bldAnalyzeShareCode` returns `{ build, known, unknown }`
  where `unknown` counts entries that didn't map to a mastery we know; the strict `bldFromShareCode`
  wrapper (link + backup-merge paths) rejects any `unknown`. **Partial import:** when a code is otherwise
  coherent but a *few* entries are unknown (`unknown ≤ known`, e.g. a **newer game version** — not
  garbage that landed one lucky mastery), the interactive Import flags it ("*N of M masteries weren’t
  recognized*") and offers an explicit "import the recognized ones" button rather than dropping them
  silently or failing outright. Mostly-unknown (`unknown > known`) is rejected as garbage.

### 2. Export your whole roster with the in-game mod

`mod/` is a small **read-only Lua mod** that dumps every roster's boards (and each board's
share code) to JSON from inside the game, where the data is already decoded. Output:
`…/Release/bin/mastery_export.json` (a copy is kept in `data/exports/`). It remains useful for
RE / bulk reference, but **the web tool no longer imports this format** — the builder's Import now
takes share codes / links + the tool's own *Backup* export (the per-board share codes the mod
emits can be pasted into Import individually). The mod isn't shipping with the first release.

Running it:

- **Offline only** — mods are disabled in online mode. Use the game's *download save for
  offline play*, then play offline.
- Install via the **TSAC Modding Tool → Local Save** (creates `…/Release/bin/localmod.xml`
  pointing at `mod/`); loose `.lua`/`.xml` in the mod folder load this way.
- Trigger: type the cheat phrase **`export mastery`** in the lobby chat (added to
  `CheatCommand.xml`; routes via `OnUserCommand` → `Command_exportmastery`). *Not* `/export…` —
  a leading `/` is just posted as chat.
- Editing the mod's Lua needs a **full game restart** to reload (returning to the menu keeps
  the local server resident).
- ⚠️ The mod is read-only and snapshots/restores anything it touches, but in-game **board
  mutation** desyncs the session — sampling is done mutation-free via `GetMasteryCode`. Don't
  repack the live `Package` from the mod folder; `PLDataPacker --mode pack` is a destructive
  sync that will delete the game's files.
