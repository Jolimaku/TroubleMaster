# TroubleMaster — A build planner and reference for TROUBLESHOOTER: Abandoned Children

A static web tool (`web/`) plus the Python extractor that feeds it.
The extractor reads, directly from the installed game, the up-to-date list of:

1. **Masteries** — name, type/class, description, flavor text
2. **What unlocks each mastery** — the enemies you learn it from, the job that
   starts with it, and the **character + job + level** that globally unlocks it
3. **Mastery Sets** — the 4 component masteries of each set and the set bonus

> **Reverse-engineering & data-model reference** — how the game's data is decrypted, stored,
> and modeled (encryption, saves, the full data model, dialogue wiring, the mastery-board
> share-code format, Quests, Joint Training) lives in **[`DATAMINING.md`](DATAMINING.md)**.

## Reproducing

*(For how the `Package/` blobs are encrypted and the dictionary layout they resolve
against, see [`DATAMINING.md` → How the data is stored](DATAMINING.md#how-the-data-is-stored).)*

1. Install **TSAC Modding Tool** from Steam (Library → Tools). It ships `PLDataPacker.exe`,
   which decrypts the game's `Package/` into plain files.

2. Unpack the text data with **`unpack.sh`** (Git Bash / macOS / Linux) or **`unpack.bat`**
   (Windows `cmd`). It runs `PLDataPacker.exe` against this repo's own **`package_list.xml`** —
   our trimmed list (xml + script + stage). We keep the list in-repo rather than in the tool
   folder so it survives tool updates; the shipped `--full`/`package_list_mod.xml` run also
   unpacks gigabytes of UI/loading-screen assets we don't use. Edit the `GAME` / `TOOL`
   variables at the top of the script, or override per-run via env vars:

   ```
   ./unpack.sh                                    # defaults (game + tool on E:\SteamLibrary\…)
   TOOL="D:/Steam/…/TSAC Modding Tool" ./unpack.sh   # or override a path
   ```

   Produces `Unpack/Data/xml/*.xml` (489 files), `Unpack/Data/script/*.lua`, and
   `Unpack/Data/stage/*.stage` (256 files, ~27 MB — the enemy→mission data). The build reads
   `xml/` + `stage/`; `script/` is kept as reverse-engineering reference (the `*.lua` files
   cited across the extractor and `DATAMINING.md`).

3. Regenerate everything with one command — **`regen.sh`** (Git Bash / macOS / Linux) or
   **`regen.bat`** (Windows `cmd`). This rebuilds `web/data.js`, `web/data.kor.js`,
   `web/codemap.js` and the per-language pages (it runs `extract_masteries.py` for each
   language then `gen_pages.py`). Edit the `GAME` / `DATA` / `LANGS` variables at the top
   of the script, or override per-run via env vars:

   ```
   ./regen.sh                                  # defaults (game on E:\SteamLibrary\…, Unpack/ beside the script)
   GAME="D:/Steam/…/Troubleshooter" ./regen.sh   # or override a path
   ```

   To run a single step by hand instead (defaults assume the game is on `E:\SteamLibrary\...`
   and `Unpack/` sits next to the script — override with `--game` / `--data`):

   ```
   python extract_masteries.py                 # English → web/data.js + web/codemap.js + output/
   python extract_masteries.py --lang kor --out output-kor   # Korean → web/data.kor.js
   python gen_pages.py                          # stamp web/<lang>/index.html from web/index.html
   ```

   By default the extractor **drops content unavailable in normal play** — the 30 grade-3
   "awakened" jobs, a few unreleased/hidden classes, and cut (`Developing`) masteries/sets,
   along with their unlock sources. Pass `--include-developing` to keep everything. The exact
   rules are in
   [`DATAMINING.md` → Content exclusion](DATAMINING.md#content-exclusion-default-drop).

## Outputs (`output/`)

| file | contents |
|------|----------|
| `masteries.json` | every mastery (~2,500, incl. internal) with full structured sources |
| `masteries.csv` / `masteries.md` | the ~2,000 named masteries with sources (readable) |
| `mastery_sets.json` / `.csv` / `.md` | 611 sets with components + bonus text |
| `web/data.js` | denormalized data for the interactive page (named masteries only), plus the Board Builder tables (`jobs`, `pcs`, `espSlots`, `boardMods`, `slotUnlock`). Its `generated` field is the **data-extraction date** (modtime of the unpacked `Mastery.xml`, via `source_date()`), *not* the build date — so rebuilding an unchanged snapshot yields no diff; it only advances when the game is re-unpacked. |

## Interactive webpage (`web/`)

A dependency-free static site which can be opened directly or served through a static site host.

Tabs:

- **Masteries** — the regular masteries: searchable, sortable table with per-column
  **Type** and **Category** filters. The **Category**
  column is colour-coded to match the in-game board (green Basic/Frame, red
  Attack/Reinforcement, yellow Ability/AI, blue Defence/Security, light Support), and a
  **Cost** column shows each mastery's training-point / Output cost. Each row
  expands to the resolved description, flavor, the character/job/level unlocks, and
  the full enemy list with levels and the missions each enemy appears in,
  shown like the in-game "Appearance Location" panel: a recommended-level badge
  coloured by case type (**red** = Scenario, **green** = Requested (side-quest stages),
  **blue** = Ordinary, **purple** = Violent) next to each mission name. A mission is also tagged `[Normal+]` /
  `[Hard+]` when the enemy only appears at that difficulty or higher (untagged =
  all difficulties). Set chips cross-link to the Sets tab, and hovering one previews that
  set's card (components + bonus) in a tooltip. Where a description **names another mastery**
  (e.g. Beast Binding → Loyal Beast), that name renders as an inline pill — hover previews the
  target's card, click jumps to it — from **positioned inline markup** baked into the resolved text
  (see [`DATAMINING.md` → Inline references](DATAMINING.md#data-model-notes));
  the same applies to buff, ability and set-bonus descriptions. Following **any** cross-link (mastery pill, set /
  ability / component chip) pushes a browser-history entry, so the **Back button returns you** to
  the tab + filters you were on before the jump. Only these link-follows are back-able — manual tab
  and filter changes are not, so Back stays a predictable "undo my last jump" rather than a maze.
  (Wiring: `historyJump` / `captureState` / `applyState` in `app.js`; state lives in `history.state`,
  session-only — it isn't a shareable/reloadable deep link.)

  How case type, recommended level, the difficulty tags, and the hostile-NPC `(dialog: …)`
  labels are resolved from the game data is in
  [`DATAMINING.md` → Enemy appearances and mission case type](DATAMINING.md#enemy-appearances-and-mission-case-type).
- **Individual** — character-personal masteries (`Category=="PC"`), each tagged with
  the playable character that owns it (from the character's `StartingMastery`); also
  includes the beast equivalent (`Category=="Beast"`), tagged with the owning beast(s) —
  resolved from the beasts that carry it (`Monster.xml`) and the beasts that gain it via
  `Beast.xml` evolution/level slots. A mastery carried by a whole beast family collapses to
  the family name (e.g. Flight → "Draki" rather than all 52 Dragon subtypes), and the owner
  cell caps at 3 names (full list on hover). Also includes the **machine-personal** `Machine`
  category (the drone equivalent — OS choices, application enhancements; no specific owner).
  Use the Category column filter to separate Individual vs Beasts vs Machine.
- **NPC / Promotion** — the remaining `Category=="PC"` masteries, tagged with the
  enemy/NPC that carries them; the grade-boost masteries any enemy can be given
  (Elite/Epic/Legend, and their stronger `…2` tiers) are tagged **Promotion**.
- **Company** — company masteries (`Category=="Company"`), which work differently
  from normal masteries, plus the NPC-faction equivalent (`Category=="Organization"`).
  Use the Category column filter to separate Company vs Organization.
- **Modules** — robot/drone parts (split out of the human masteries). Covers the five
  module-board categories (`FrameModule`/`ComplementaryModule`/`AIModule`/`SaftyModule`/
  `SupportModule` → Frame/Reinforcement/AI/Security/Support). Uses the same table — the
  Cost column is the module's **Output** cost, and the category colours/filter apply.
- **Class Traits** — innate class/race traits (`Category=="Job"`): each is a job's
  `BasicMastery` (always-on, not a board mastery; many belong to enemy/NPC/beast/robot
  classes), with the class shown in the Type column and the effect in the row detail. The
  **Category** column is dropped here — every trait shares the one value (`Class`).
- **Misc** — small odd buckets that don't warrant their own tab: `ESP` (SP), `Race`,
  `Grant` (Granting), `Difficulty`, `BestFriend` (Bond). The Category column filter separates them.
- **Mastery Sets** — searchable cards; component chips cross-link to the right
  Masteries tab and **hover** to preview that mastery (type, colour-coded category, cost,
  description, flavor). The jump lands scoped by the **Name column filter** (not the global
  search), so you see just that mastery. (The reverse — a mastery's Set chips → the Sets tab —
  uses the global search, as the card view has no columns.)
  Search matches names, descriptions, set/component names and sources.
- **Equipment Sets** — the gear-set bonuses (`EquipmentSet` category, split out of the
  Masteries table into their own tab). One searchable card per set, grouped from the
  per-threshold masteries (named either `"<Set> - Set <N>"` or `"<Set> - <N>-piece Set
  Bonus"` in English, or `"<Set> - <N> 세트"` in Korean), listing each piece-count
  threshold's bonus (e.g. Arms Alchemy: 2pc / 3pc / 5pc). Grouping keys off the localized
  name suffix, so `_parse_eqset` in `extract_masteries.py` needs a regex per language —
  add one when localizing, or every threshold becomes its own ungrouped card.
- **Abilities** — the **player-usable** combat abilities: a searchable/sortable table with
  **Slot** · **Type** (Attack/Support/Heal/…) · **Element** · action **Cost** · **CD** · **Cast** ·
  resolved **effect**. Slot / Type / Element get per-column dropdown filters, Name / effect a
  free-text filter (see the filter row above). For item- or mastery-granted abilities (which have no real combat slot) the
  Slot column instead shows how you access them — **Potion / Grenade / Spray / Device / Mastery**.
  The row detail adds range / targets / hit-rate / SP, flavor, the masteries that **grant** or
  **modify** the ability (chips → Masteries tab), and **owner badges** naming the character(s) or
  beast family that field it. Enemy-/NPC-/effect-only abilities are filtered out. How
  player-usability, owners, and the slot labels are derived from the game data is in
  [`DATAMINING.md` → Abilities tab](DATAMINING.md#abilities-tab).
- **Dialogue** — the story stages that have branching choices, listed in level order with a
  case-coloured level badge (Raid/Common combat-only stages are excluded). Expand a stage to see
  each **decision** — its prompt and options — with each option's consequence tagged (fight /
  join / leave / third-party / buff / reward / mission win-fail / grants mastery / opens mastery
  for research / timer). A `○ Opens X for research` badge marks a mutually-exclusive grant group:
  the choice awards one member but opens the rest for crafting (see
  [`DATAMINING.md` → Opened for research](DATAMINING.md#data-model-notes); the awarding Story
  source on the Masteries tab carries a matching "still opened…" note). Mastery grant/open badges
  link to the row; a `gains <Buff>` badge hover-previews that buff's effect (resolved by buff **id**,
  so colliding Titles like *Rage* show the right effect).
  Multi-step conversations **chain**: a follow-up decision nests under the option that leads to
  it, and each option can fold open to the scripted rules it triggers. Every stage also has a
  collapsible **Full script** — its trigger graph rendered as readable pseudo-code, with spoken
  cutscene lines inlined — covering the machinery not driven by player choices. Script text
  (variables, buffs, units) is searchable. How the event-graph is parsed and rendered is in
  [`DATAMINING.md` → Dialogue tab](DATAMINING.md#dialogue-tab).
- **Quests** — the Shooter Street NPC request chains (84 quests), grouped under a
  subheading per NPC (ordered by the chain's earliest stage requirement) with the quests laid
  out as cards in a responsive grid. Each card shows its chain index (#N), title,
  quest type, an **"Unlocks after: &lt;story
  mission&gt;"** line (the `RequireStageLv` gate resolved to the gating scenario mission,
  prefixed with that mission's recommended-level badge, case-coloured like the
  masteries tab; these gates are always Scenario, so **red**), the fixed stage location (or the
  enemy it drops from), the objective, the
  **pick-one-of-three** reward list plus the always-granted friendship, and a **"Requires NPC
  #N"** badge for any cross-chain / non-sequential prerequisite (e.g. Roberto #2 requires
  Maximillion #1). The **Type** dropdown filters by quest type; search matches title /
  objective / NPC / location / drop / reward / unlock-mission names. Built by `quests.py` — see
  [`DATAMINING.md` → Quests](DATAMINING.md#quests-shooter-street-npc-requests) for the data model.
- **Board Builder** — assemble a character's actual mastery board. Pick a **character**,
  **class**, and **level**; the five category columns (Basic / Attack / Ability / Support /
  Defence, colour-coded as in-game) show the computed slots and a per-category
  training-point **cost cap**, plus a `Training Point used/total` readout. A right-hand
  sidebar (**Masteries** / **Sets**, filtered to the types that character can actually use)
  adds a single mastery or a whole set on click; placed masteries can be removed by clicking
  them, and each shows its card on hover. The **Masteries** tab hides anything already on the
  board and has a **Fit** toggle (next to the filter box) that further narrows the list to masteries
  that still fit — a free unlocked slot in their category plus cost headroom in the category and
  total caps; the **Sets** tab lists the sets you haven't completed as component **diamonds** (placed
  solid, missing dimmed) sorted **closest-to-complete first** — sets you have none of sink to the
  bottom — and **clicking one adds its missing masteries**. Placed masteries in a completed set get
  a violet right border; hovering a set (in the panel or sidebar) highlights its components on the
  board, and hovering a component diamond lights up that mastery's diamonds everywhere. Slots
  **unlock by character level** (locked ones read `Unlock Lv N`); going over any slot, cost, or
  total cap is allowed but raises a **"build broken"** banner. A sixth **Mastery Set** panel lists
  the sets your board **already completes** (the completable partial sets live in the Sets sidebar).
  A **Builds** row keeps **multiple named
  builds** (new / duplicate / rename / delete + a switcher); the active build **autosaves** to
  `localStorage` continuously (and the active tab is remembered), so switching never loses work —
  all works from `file://` in Chrome/Firefox.
  **Undo / redo** (bottom bar, after Export; `Ctrl/Cmd+Z` and `Ctrl/Cmd+Shift+Z` or `Ctrl+Y`, off
  when a text field has focus) covers every board edit — placing/removing masteries, adding a whole
  set, and the character / class / form / level / OS / reinforcement / evolution / craft selectors.
  Each build has its **own history**, so switching builds is navigation, not an undoable step; the
  history is **session-only** (in-memory, not persisted) and clears on reload. Internally it snapshots
  the build at the single `bldSave()` persistence point and diffs, so redundant re-renders don't
  record spurious steps.
  The same board works for **beasts** (the Character picker offers families; the 2nd selector is
  the **Form**, and an extra row picks the 1-of-3 **evolution masteries** per stage) and **drones**
  (Character = **Frame**, 2nd selector = **SP** structure, plus **OS** and **Reinforce** stage
  selectors; the five columns relabel to the module categories Frame / Reinforcement / Support /
  Security / AI, and a **AI upgrades** row picks one upgrade per reinforcement step from the OS pool
  — the eligible set is cumulative `Lv < stage`). Drone **share codes / links** work too (rosterType
  3, charId = the full `Frame_SP` unit so the SP round-trips) — the **code** is **board only**, like
  beasts: the OS, reinforcement stage, and craft / AI-upgrade picks aren't in it (so the bare code
  still imports into the game), but a **link we generate restores them** (see below).
  All limits are computed from the game's own formulas — see
  [`DATAMINING.md` → Data model notes](DATAMINING.md#data-model-notes).
  **Import / Export** (per build + whole collection):
  - **Export** (bottom bar, current build): an in-game **board share code** (`KSAAC…`) plus a
    **full-fidelity shareable link** `…#build=<code>&name=<name>[&os=…&reinf=…&craft=…&evo=…]`. The
    link appends the picks the share code can't carry (a beast's evolution masteries, a drone's OS /
    reinforcement / craft / AI-upgrades) as **mastery-id params** — evolution/craft masteries have no
    `MasteryCode` entry (that's why they're absent from the code), so they're keyed by stable id, not a
    codemap code. A link without those params still decodes fine. The share-code format is fully
    reverse-engineered (decode + encode) — see
    *[`DATAMINING.md` → Importing a build from the game](DATAMINING.md#importing-a-build-from-the-game)*.
  - **Backup** (Builds row): downloads `troublemaster-builds.json` =
    `{ tsbuilder:1, builds:[<#-fragment>…], starred:[id…] }` — every build as a full-fidelity fragment
    plus the starred-mastery wishlist. A backup/restore of the whole collection.
  - **Import** (Builds row): auto-detects. A pasted **share code / share link / `#`-fragment** (any
    roster — PC / beast / drone) is added as **one new build** and selected (it never overwrites the
    active build). A pasted-or-uploaded **export file** is **merged** — builds whose canonical key isn't
    already loaded are appended, exact duplicates skipped, and the starred set is unioned (reports
    `N imported / M skipped / K starred`). The canonical key (`bldCanonCode`) folds the picks in (over a
    normalized build, so an imported build and a stored one key identically), so two builds differing
    only in a pick aren't deduped together.
  - Opening a `#build=…` link switches to the builder and loads that build (with its picks) —
    selecting it if you already have it, else adding it as new — then strips the hash so refresh
    doesn't re-load it. *(Import always adds a build as new; to update a build, import the new
    version and delete the old.)*

The Individual and NPC tabs add a **Character** column. Masteries are partitioned by
a `group` field (normal / individual / npc / company) computed by the extractor.

Design and data are separate: `index.html` / `style.css` / `app.js` are
hand-authored and stable; the extractor regenerates only `web/data.js`
(`window.TS_DATA`, loaded via a `<script>` tag so it works from `file://`).

### Localization (per-language pages)

The page is built for multiple languages without a build step or runtime language
switching — each language is its own static page that differs only by which scripts
it loads (clean URLs for GitHub Pages, and works the same from `file://`):

- **Data** — `python extract_masteries.py --lang <dir>` resolves every string through
  `Dictionary/<dir>/` and writes `web/data.<lang>.js` (English `--lang eng` stays
  `web/data.js`, the default page). `<dir>` is the game's dictionary folder name
  (`eng`, `kor`, `jpn`, `chn`, `deu`, …). The payload is structurally identical across
  languages (same ids/relationships/counts); only the text differs.
- **UI chrome** — the static shell strings (tab names, column headers, buttons, filter
  labels) live in `web/ui.<lang>.js` (`window.TS_UI`, an id→string map), **not** in the
  markup. The shell is language-neutral: every chrome element carries a `data-i18n` /
  `data-i18n-ph` (placeholder) / `data-i18n-title` (tooltip) hook, and `web/i18n.js` fills
  them from `TS_UI` (missing keys fall back to the markup's English default). The dynamic
  strings built in `app.js` use `t(key, fallback)` / `tf(key, fallback, {vars})`. `ui.en.js`
  is the canonical key list; a translated page ships a same-keyed copy. Unlike `data.<lang>.js`,
  the UI map is **hand-curated** (tab names like "Board Builder" are our own taxonomy, not game text).
- **Load order matters (no flash):** `ui.<lang>.js` then `i18n.js` load **before** the multi-MB
  `data.<lang>.js`, so they run before first paint. `i18n.js` does two data-independent pre-paint
  jobs — (a) the shell-localization sweep, and (b) restoring the last-active **tab** (mirroring the
  visual part of `selectTab`, keyed off `ts:activeTab`) — so the chrome paints already-translated
  *and* on the saved tab. If either ran only in `app.js` (which loads after the big parse) you'd see
  a flash of English / of the default Masteries tab first. `app.js` re-applies the full tab setup
  (`populateTypes`, etc.) idempotently once data is available, and renders the data-driven content.
- **A language page** is `<lang>/index.html` — the same shell, loading `../ui.<lang>.js` +
  `../i18n.js` + `../data.<lang>.js` (instead of the English `ui.en.js` + `data.js`) and setting
  `<html lang>`. The
  language switch is a plain `<a href>` link between pages, kept as a pretty directory URL
  (`ko/`, `../`) tagged with the `dir-index` marker class. A server resolves those to
  `index.html`; under `file://` (where a bare `ko/` would open a directory listing) `i18n.js`
  re-appends `index.html` to every `a.dir-index` at load — so `.dir-index` hrefs must end in `/`.
  **Generated, not hand-edited:** `python gen_pages.py` stamps each `<lang>/index.html` from the
  canonical `web/index.html`, applying exactly those differences (page lang, `../`-prefixed
  asset/script srcs, `ui.en.js`→`ui.<suffix>.js`, `data.js`→`data.<suffix>.js`, switch-back
  anchor) from its `LANGS` table. So edit the tabs/views **only** in `web/index.html`, then re-run
  `gen_pages.py` (add a `LANGS` entry to introduce a new language).

A **Korean** page is provided: `web/ko/index.html` + `web/ui.kor.js` +
`web/data.kor.js` (run `--lang kor`). `ui.kor.js` uses in-game Korean terms where the game
has one (특성 = mastery, 특성판 = mastery board, 추가 특성 = mastery set, 모듈 = module,
클래스 = class, 코스트 = cost, 운영체제 = OS, 승격 = promotion, 대사 = dialogue) — mined by
matching the `eng`/`kor` dictionary pairs by shared code.

Masteries with **no authored description** (e.g. the Elite/Epic/Legend promotion buffs) get a
synthesized one from their flat `Base_<Stat>` deltas via `stat_summary` in `resolve_desc.py`. It
uses the **game's own per-stat template** `Status/<stat>/Desc_Increase` / `Desc_Decrease`
(`"Increases your $Status$ by $Value$."` / Korean `"$Status$[이] $Value$ 증가합니다."`), filling
`$Status$` with the stat Title and `$Value$` with the delta (abs value — the template carries the
increase/decrease sense), then running `apply_josa`. This is faithful for free: Max-stats say
"Maximum …", and boolean/immunity stats are self-contained sentences (`Immune to mental debuff.`,
no `$Value$`). `STAT_TMPL` (English `"Increases {title} by {num}"`; Korean `"{title}을 {N} 증가"`,
josa-marked) is only a **fallback** for the rare stat with a Title but no Desc template. The
SP-gauge stats (`SP_GAUGE_STATS`) have no Title but do have a Desc template, so they're given the
generic `"SP"` title and render their `"… Maximum SP …"` template (e.g. *LimitBreak*
`Base_MaxAddSP=50` → "Increases your Maximum SP by 50."). Only `ApplyAmount*` (no Title *and* no
Desc template — a context-only magnitude) is skipped; those masteries carry their meaning in a
linked buff/ability instead (the `describe()` path), not a flat-stat line.

Most in-app dynamic chrome built in `app.js` is localized via `t(key, fallback)` (plain
strings) and `tf(key, fallback, {vars})` (with `{placeholder}` substitution): tab/result counts,
empty states, footer, the mastery row-detail headings, the whole Board Builder (board columns,
slots, summary, broken-build messages, set panel, sidebar, build prompts/default names), and the
import/export panel. Board-category labels that key on the English `categoryRaw` are localized via
a `catDisplay` map harvested from the data (`categoryRaw → category`). Likewise the beast
evolution-picker group headers (Training / Nature / Gene / the beast's element) key on the raw
type id and are localized through the `typeDisplay` map (`typeRaw → type`, in-game names — so e.g.
Nature shows "Instinct", Heat shows "Heating"/발열); the synthetic Species group (the `m.unique`
pool, which has no game type) uses its own `bld.evoSpecies` key (종족).

**Still English (deferred):**
- **The Dialogue tab's generated *full-script* rendering** — "WHEN …:", "by …:", "triggers N rules",
  "when (always)", "Full script (N rules; … omitted)", and the per-action lines ("set X = N",
  "end battle (victory)", …) (`app.js` ~660–880 + `dialog_map._action`/`_cond`). This is cohesive
  generated prose; localize as one unit. *(The per-stage "N decisions" count, and the top-level
  **consequence badges** under each choice — join / fight / buff / reward / mission win-fail / grants
  / timer — **are** localized: phrasing authored per-language in `dialog_map._describe` /
  `_gated_consequences` via `_L(dic, en, ko)`, with embedded unit/buff names resolved through the
  dictionary. The only English left in a badge is a structural id with no localizable title — a team
  id like `enemy팀` or an unnamed map object like `WatchTower01`.)*
- **The import board-picker line** — "… — board N (active) · M masteries".
- Hover **mastery/set cards** are essentially already localized (they render translated data — name,
  type, category, description, flavor); only the rare extractor-composed description fragments aren't.

## License

The original code and documentation in this repository are released under the
[MIT License](LICENSE).

TroubleMaster is an **unofficial, fan-made** tool and is not affiliated with, endorsed by, or
sponsored by Dandylion. *TROUBLESHOOTER: Abandoned Children*, its data, text, names, and
trademarks are the property of their respective owners. Game data reproduced here (e.g. the
generated `web/data.js`, `web/data.kor.js`, and `web/codemap.js`) is included for reference and
interoperability and remains the property of its rights holders — it is **not** covered by the
MIT license.