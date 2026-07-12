# Project instructions

## Two docs: README.md (app) and DATAMINING.md (reverse-engineering)
`README.md` covers the app + usage: the web tool, the extract/build pipeline
(`Reproducing`, `Outputs`), and `Localization`. **`DATAMINING.md`** covers how the game
data is decrypted/stored/modeled: `How the data is stored`, `How saves are stored`, the
`Data model notes`, `Quests`, `Joint Training`, and the share-code format.
Note the seam: **per-tab rendering mechanics (Dialogue, Abilities) are in README's
`Interactive webpage` section** (they describe how a UI tab is populated), while
cross-cutting data-model/sourcing is in `DATAMINING.md`.

## Check DATAMINING.md first
Before reverse-engineering or working out any game-data mechanism (file formats,
encryption, the share-code scheme, the translation/dictionary lookup, board/slot
formulas, dialogue wiring, beast/evolution data, etc.), **search `DATAMINING.md` first** —
it very likely was figured out and documented already. The "Data model notes" and the
top "How the data is stored" sections are the densest. Don't re-derive what's recorded.
(For anything about the web tool or the build pipeline itself, check `README.md`.)

## Querying game data
Read-only queries over the data files — reach for these before writing a script:
- **JSON** (`output/*.json`, `mastery_export.json`): `jq`.
- **`web/data.js` / `web/data.kor.js`** are a `window.TS_DATA = {…};` wrapper, not pure JSON — strip it
  first: `jq -Rs 'ltrimstr("window.TS_DATA = ") | rtrimstr("\n") | rtrimstr("\r") | rtrimstr(";") | fromjson | <query>' web/data.js`
- **Game XML** (`Unpack/Data/xml/*.xml`): `yq -p xml -oy '<expr>'` (mikefarah/yq — the Go build, needs
  `-p xml`). Attributes are `+@name` keys (`.["+@name"]`); a lone child parses as a map and repeated
  children as a sequence, so wrap child access as `[.X.property] | flatten`. Handles class dumps,
  `group_by` distributions, and compound `select(...)` predicates.
- **Dictionary lookups** (`Status/<x>/Title`, EN/KO): the `.dic`/`.dkm` format isn't JSON/XML, so use
  the committed helper — `python tools/dictq.py <key> [--lang kor|both]` (wraps `extract_masteries.Dictionary`).

## Keep the docs current
When you work out something new about how the game data or this tool works — a new
mechanism, a non-obvious key/path, a gotcha, a corrected assumption — **add it to the
relevant doc** (`DATAMINING.md` for a game-data/RE mechanism; `README.md` for the web
tool or build pipeline) so the next session doesn't have to rediscover it.
Record *where* a thing lives (file + key/path) and any trap that wasted time.

## Keep translations in sync
The web tool is localized (English + Korean — see README "Localization"). User-facing strings live
in two places: **static page chrome** in `web/index.html` via `data-i18n` / `-i18n-ph` / `-i18n-title`
hooks, and **dynamic strings** built in `web/app.js` via `t(key, fallback)` / `tf(key, fallback, {vars})`.
Both resolve keys from `window.TS_UI` — `web/ui.en.js` (English) and `web/ui.kor.js` (Korean).
- **Any new user-facing string must be translation-ready:** never hard-code display text. Add a key
  (via a `data-i18n*` hook in HTML, or `t()`/`tf()` in app.js) and add that key to **both** `ui.en.js`
  **and** `ui.kor.js`. Use `tf` with `{placeholders}` for interpolated values so word order stays
  translatable (don't concatenate translated fragments).
- **When you change an English string, update its Korean entry too** — edit the matching key in
  `ui.kor.js` (and the `data-i18n`/`t()` fallback should mirror the English value). Don't let the two
  drift. For Korean, prefer the in-game term where one exists (mine the `eng`/`kor` dictionary pairs).
- If a string genuinely can't be translated yet, leave it out of scope deliberately and note it under
  README "Localization → Still English (deferred)".

## Keep TODO.md current
`TODO.md` tracks open / deferred work (status markers: ☐ open · ◐ partial · ✓ done-for-reference).
- When you **finish** an item it covers, **tick it off** — change `☐`/`◐` to `✓`, prefix the title
  with *(done)*, and rewrite the body to describe what was actually built (not what was planned).
- **Do not add new items on your own.** Only add a TODO after the **user explicitly requests or
  confirms** it. If you spot something worth tracking, suggest it and wait for the go-ahead before
  writing it in.
