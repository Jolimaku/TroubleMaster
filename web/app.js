"use strict";
(function () {
  const DATA = window.TS_DATA || { masteries: [], sets: [], counts: {} };
  const UI = window.TS_UI || {};
  const t = (key, fallback) => (UI[key] != null ? UI[key] : fallback);   // localized chrome string, with English fallback
  // localized string with {placeholder} substitution, e.g. tf("count.shown","{n} of {total}",{n,total})
  const tf = (key, fallback, vars) => {
    let s = t(key, fallback);
    for (const k in (vars || {})) s = s.split("{" + k + "}").join(vars[k]);
    return s;
  };
  // mission case type (Scenario / Quest / Ordinary / Violent): localized short name for a filter
  // option, and the "{name} Case" badge tooltip. Korean uses the in-game 사건 terms (시나리오/의뢰/
  // 일반/강력 사건). Case values are a stable English enum in the data, so translate at display time.
  const caseName = c => t("case." + c, c);
  const caseTip = c => tf("case.tooltip", "{case} Case", { case: caseName(c) });
  // (the static-shell localization sweep runs earlier, in i18n.js, so the chrome is translated
  // before the large data file parses — see the <script> order in index.html. app.js only handles
  // dynamic strings via t()/tf().)

  // owner-column sentinels the extractor emits as English (the rest are proper names, already
  // localized in the data). Translated wherever owner is shown *and* in the search blob, so the
  // index matches the display. Composite owners ("A / B") are translated per component.
  const OWNER_I18N = { "Promotion": "owner.promotion", "Any beast": "owner.anyBeast",
                       "Any beast (genetic modification)": "owner.anyBeastGenetic" };
  const ownerLabel = o => (o || "").split(" / ").map(p => t(OWNER_I18N[p], p)).join(" / ");

  // Inline reference markup (control-char sentinel triple \x01kind\x02label\x03) — parsed to chips
  // in detail/card views (parseMarkup) and flattened to the bare label for plain-text contexts
  // (search blobs, tooltips, compact previews). Declared up here so the blob pass below can use it.
  const REF_RE = /\x01([a-z]+)\x02([^\x03]*)\x03/g;
  // Block-indent marker (\x11): a line beginning with it is a continuation/effect line rendered inside
  // an indented container (parseMarkup) so the whole wrapping line stays indented — replaces an older
  // leading-spaces hack that only indented the first row. For plain-text contexts we flatten it back
  // to the historical 4 leading spaces (harmless: inline previews collapse whitespace, tooltips pre-wrap).
  const IND = "\x11";
  function stripMarkup(text) {
    return (text || "").replace(REF_RE, (_, k, label) => label).split(IND).join("    ");
  }

  // precompute lowercase search blobs
  DATA.masteries.forEach(m => {
    m._sourceCount = m.enemies.length + m.characters.length + m.jobs.length
      + (m.beasts || []).length + (m.drones || []).length
      + (m.achievements || []).length + (m.story || []).length + (m.initial ? 1 : 0)
      + (m.research || []).length;
    m._blob = [
      m.name, m.type, m.grade, m.category, ownerLabel(m.owner), stripMarkup(m.description), m.flavor,
      m.sets.join(" "),
      // enemy names and their mission appearances are intentionally NOT indexed — too many misleading hits
      m.characters.map(c => c.character + " " + c.job).join(" "),
      m.jobs.join(" "),
      (m.beasts || []).map(b => b.beast).join(" "),
      (m.drones || []).map(b => b.drone).join(" "),
      (m.achievements || []).length
        ? t("src.achievement", "achievement") + " "    // index the localized word so a search on it finds these
          + m.achievements.map(a => (a.condition || "") + " " + (a.achievement || "")).join(" ")
        : "",
      (m.story || []).map(s => (s.scenario || "") + " " + s.mission + " " + (s.choice || "")).join(" "),
      m.initial ? t("src.initial", "from start") : "",
      (m.research || []).join(" ")
    ].join("  ").toLowerCase();
  });
  DATA.sets.forEach(s => {
    s._blob = [s.name, s.type, s.dlc, stripMarkup(s.bonus),
      s.components.map(c => c.name).join(" ")].join("  ").toLowerCase();
  });

  (DATA.equipmentSets || []).forEach(e => {
    e._blob = [e.name, e.type, stripMarkup(e.thresholds.map(t => t.desc).join(" "))].join("  ").toLowerCase();
  });

  (DATA.abilities || []).forEach(a => {
    // index the *displayed* (localized) slot label, not the raw English key — search-what-you-see
    const slot = a.slot ? tf("ability.slot." + a.slot.toLowerCase(), a.slot, {}) : "";
    a._blob = [a.name, a.type, a.element, slot, stripMarkup(a.description), (a.owners || []).join(" "),
      (a.grantedBy || []).join(" "), (a.modifiedBy || []).join(" ")].join("  ").toLowerCase();
  });

  (DATA.dialogues || []).forEach(s => {
    s._blob = [s.title, s.scenario || "", s.questName || "", s.case, s.stage,
      s.decisions.map(d => d.prompt).join(" "),
      s.decisions.flatMap(d => d.options.map(o => o.text)).join(" "),
      s.decisions.flatMap(d => d.options.flatMap(o => o.consequences.map(c => c.text))).join(" "),
      (s.script || []).flatMap(r => [r.name, r.when,
        ...r.do.map(d => d.text), ...r.do.flatMap(d => d.lines || [])]).join(" ")
    ].join("  ").toLowerCase();
  });

  (DATA.quests || []).forEach(q => {
    q._blob = [q.title, q.objective, q.npcName, q.typeLabel, (q.unlockMission || {}).name || "",
      q.locations.join(" "), q.dropFrom.join(" "),
      q.rewards.map(r => r.name || r.pool || "").join(" ")].join("  ").toLowerCase();
  });
  // quest id -> {npcName, chainIndex} so a prerequisite can be shown as "NPC #N"
  const QUEST_BY_ID = {}; (DATA.quests || []).forEach(q => { QUEST_BY_ID[q.id] = q; });

  const state = { view: "masteries", group: "normal", indivCat: "Individual", q: "", type: "", category: "", starred: false, sortKey: "name", sortDir: 1,
    // per-column filter row (Masteries / Abilities tables): colFilters[key] = active value
    // (lowercased substring for text columns, exact value for dropdowns); colModes[key] = "text"|"select"
    colFilters: {}, colModes: {} };
  // the star ("to acquire" tracking list) applies only on the two board-mastery tabs
  const starGroup = () => state.group === "normal" || state.group === "module";
  const GROUP_LABEL = { normal: t("group.normal", "masteries"), individual: t("group.individual", "individual masteries"),
    npc: t("group.npc", "NPC / promotion masteries"), company: t("group.company", "company masteries"), module: t("group.module", "drone modules"),
    class: t("group.class", "class traits"), misc: t("group.misc", "misc masteries") };
  // language-independent category id (categoryRaw) -> localized display name, harvested from the
  // data — used to localize board-column headers / sidebar labels that key on the English categoryRaw
  const catDisplay = {}; (DATA.masteries || []).forEach(m => { if (m.categoryRaw) catDisplay[m.categoryRaw] = m.category; });
  // raw type/element id (typeRaw) -> localized display name — covers character/beast/drone ESP
  // elements (Fire→화염, Heat→발열, …) shown in the builder summary, which key on the raw id
  const typeDisplay = {}; (DATA.masteries || []).forEach(m => { if (m.typeRaw) typeDisplay[m.typeRaw] = m.type; });
  // in-game board category colours (masteries & drone modules share the role colours)
  const CAT_COLOR = {
    Basic: "cat-green", Frame: "cat-green",
    Attack: "cat-red", Reinforcement: "cat-red",
    Ability: "cat-yellow", AI: "cat-yellow",
    Defence: "cat-blue", Security: "cat-blue",
    Support: "cat-support",
  };
  const table = document.getElementById("mastery-table");
  const abilityTable = document.getElementById("ability-table");

  const $ = sel => document.querySelector(sel);
  const els = {
    search: $("#search"), type: $("#type-filter"), count: $("#count"),
    typeLabel: $("#type-filter-label"),
    cat: $("#cat-filter"), catLabel: $("#cat-filter-label"),
    body: $("#mastery-body"), grid: $("#set-grid"), footer: $("#footer"),
    dialogue: $("#dialogue-list"), equipGrid: $("#equipset-grid"),
    indivSubtabs: $("#indiv-subtabs"), abilityBody: $("#ability-body"),
    starFilter: $("#star-filter"), starCount: $("#star-count"),
    questsList: $("#quests-list"),
  };
  // set once the search clear (×) button is wired up (see init); safe no-op until then
  let syncSearchClear = () => {};

  // ---- helpers ----
  // el("tag.cls.cls", props?, ...children) → HTMLElement.
  //   props: `text` (textContent), `html` (innerHTML), `class` (extra classes appended),
  //   `dataset` ({k:v}), `on` ({event:fn}), `onClick`/`onMouseenter`/… handlers; any other key →
  //   setAttribute. props whose value is null/false are skipped (so `cond && value` reads cleanly).
  //   children: strings (→ text nodes) or nodes; arrays are flattened; null/false/undefined skipped.
  function el(tag, props, ...kids) {
    const parts = tag.split(".");
    const e = document.createElement(parts[0] || "div");
    if (parts.length > 1) e.className = parts.slice(1).join(" ");
    for (const k in props) {
      const v = props[k];
      if (v == null || v === false) continue;
      if (k === "text") e.textContent = v;
      else if (k === "html") e.innerHTML = v;
      else if (k === "class") { if (v) e.className += (e.className ? " " : "") + v; }
      else if (k === "dataset") Object.assign(e.dataset, v);
      else if (k === "on") for (const ev in v) e.addEventListener(ev, v[ev]);
      else if (k.startsWith("on") && k.length > 2 && k[2] === k[2].toUpperCase())
        e.addEventListener(k.slice(2).toLowerCase(), v);
      else e.setAttribute(k, v);
    }
    return add(e, kids);
  }
  // append children to a parent: strings → text nodes, arrays flattened, null/false/undefined skipped.
  function add(parent, ...kids) {
    for (const c of kids.flat(Infinity)) if (c != null && c !== false) parent.append(c);
    return parent;
  }
  function chip(label, onClick) {
    return el("span.chip", { text: label, onClick: e => { e.stopPropagation(); onClick(); } });
  }
  // funnel glyph marking a clickable board-column header as a sidebar filter (dimmed at rest,
  // full-opacity on hover / when active — see renderBuilder + .bld-col-funnel in style.css)
  const FUNNEL_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M4.25 5.61C6.27 8.2 10 13 10 13v6c0 .55.45 1 1 1h2c.55 0 1-.45 1-1v-6s3.72-4.8 5.74-7.39c.51-.66.04-1.61-.79-1.61H5.04c-.83 0-1.3.95-.79 1.61z"/></svg>';

  // shared hover tooltip: set chips preview the set card; mastery (component) chips
  // preview that mastery's info
  const setByName = {};
  DATA.sets.forEach(s => { setByName[s.name] = s; });
  const masteryByName = {};
  DATA.masteries.forEach(m => { masteryByName[m.name] = m; });
  const masteryById = {};
  DATA.masteries.forEach(m => { masteryById[m.id] = m; });

  // ---- tracking ("to acquire") list: starred mastery/module ids, persisted, file://-safe ----
  // A single id-set covers both the Masteries and Modules tabs (modules share the mastery id
  // space) and the Board Builder. Stale ids (a game patch dropped a mastery) are pruned on load.
  const STAR_KEY = "ts:starred";
  let starred;
  try { starred = new Set((JSON.parse(localStorage.getItem(STAR_KEY) || "[]") || []).filter(id => masteryById[id])); }
  catch (e) { starred = new Set(); }
  const saveStarred = () => { try { localStorage.setItem(STAR_KEY, JSON.stringify([...starred])); } catch (e) { /* blocked/full */ } };
  function toggleStar(id) {
    const wasFiltering = state.starred && starGroup() && state.view === "masteries";
    if (starred.has(id)) starred.delete(id); else starred.add(id);
    saveStarred();
    if (state.view === "builder") { updateStarFilterUI(); renderBuilder(); }  // sync the star across board + sidebar
    else if (wasFiltering) render();                                          // filtered membership changed; render() refreshes the pill too
    else updateStarFilterUI();                                               // filter off → the caller's paint() flips the one star in place
  }
  // a star toggle for a mastery id; paints in place on click (so an open row-detail / scroll survives)
  function starToggle(id, cls) {
    const s = el("span.star" + (cls ? "." + cls : ""), { role: "button", tabindex: "0" });
    const paint = () => {
      const on = starred.has(id);
      s.textContent = on ? "★" : "☆";
      s.classList.toggle("on", on);
    };
    paint();
    const fire = e => { e.stopPropagation(); toggleStar(id); paint(); };
    s.addEventListener("click", fire);
    s.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fire(e); } });
    return s;
  }
  // the "★ Starred" filter pill: visible only on the two board-mastery tabs and only once something
  // is starred; auto-clears the filter when the list empties.
  function updateStarFilterUI() {
    // pill shows on both board-mastery tabs regardless of whether anything is starred yet
    // (discoverability); clicking it with an empty list just shows the empty state.
    const onTab = state.view === "masteries" && starGroup();
    const show = onTab;
    if (!els.starFilter) return;
    els.starFilter.hidden = !show;
    els.starFilter.classList.toggle("active", show && state.starred);
    // count = starred items matching the table's current filters (group + search + type + category),
    // independent of the star toggle itself — so it previews what the filter would show on this tab
    let n = 0;
    if (onTab) n = DATA.masteries.reduce((acc, m) => acc + (
      m.group === state.group && starred.has(m.id) && matches(m._blob)
      && colFilterMatch(m, MASTERY_FILTER_COLS) ? 1 : 0), 0);
    els.starCount.textContent = n ? String(n) : "";
  }
  const abilityByName = {};
  (DATA.abilities || []).forEach(a => { abilityByName[a.name] = a; });
  // buff effect lookup (name → text), keyed by the label a `buff` inline-ref carries — so a chip's
  // hover shows the effect, mirroring the in-game nested tooltip.
  const buffDesc = DATA.buffs || {};
  const buffById = DATA.buffsById || {};      // buff id → {t: Title, e: effect} for dialogue "gains X"
  const buffGroups = DATA.buffGroups || {};   // group title → member buff titles (group cards)
  const tip = el("div.hover-tip");
  document.body.appendChild(tip);
  let tipAnchor = null;
  function showTip(anchor, build, placement) {
    tipAnchor = anchor;
    tip.replaceChildren();
    build(tip);
    tip.classList.add("show");
    const r = anchor.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    const th = tip.offsetHeight;
    // keep the tip within the viewport vertically so it never stretches the page past its bottom
    // edge (the tip is pointer-events:none, so any page scrollbar it caused couldn't be used anyway)
    const clampTop = t => Math.max(8, Math.min(t, vh - th - 8));
    if (placement === "right") {                          // beside the anchor (for dropdown options)
      tip.style.top = (window.scrollY + clampTop(r.top)) + "px";
      let left = r.right + 8;
      if (left + tip.offsetWidth > vw - 8) left = r.left - tip.offsetWidth - 8;   // flip if no room
      tip.style.left = (window.scrollX + Math.max(8, left)) + "px";
    } else {
      // default below the anchor; flip above when it would overflow the bottom and there's room up top
      let top = r.bottom + 6;
      if (top + th > vh - 8 && r.top - 6 - th >= 8) top = r.top - 6 - th;
      tip.style.top = (window.scrollY + clampTop(top)) + "px";
      const maxLeft = window.scrollX + vw - tip.offsetWidth - 8;
      tip.style.left = Math.max(window.scrollX + 8, Math.min(window.scrollX + r.left, maxLeft)) + "px";
    }
  }
  function hideTip(anchor) {
    if (anchor && anchor !== tipAnchor) return;   // moved straight onto another chip
    tip.classList.remove("show");
    tipAnchor = null;
  }
  function buildSetTip(s, box) {
    add(box,
      el("div.tip-name", { text: s.name }, s.dlc && el("span.badge.dlc", { text: s.dlc })),
      s.type && el("div.tip-meta", { text: s.type }),
      el("div.tip-comp", {}, s.components.map(c => el("div", { text: "• " + c.name }))),
      s.bonus && descBlock("div.tip-bonus", s.bonus));
  }
  // cost is a board stat — show it (incl. a genuine 0) only for board-placeable masteries (those
  // routed to the Masteries / Modules tab = group normal/module). For non-board masteries a 0 is
  // meaningless, so blank it; a non-board cost>0 (the Elite/Epic/Legend rank effects) still shows.
  const costText = m => (m.cost == null
    || (m.cost === 0 && m.group !== "normal" && m.group !== "module")) ? "" : m.cost;
  function buildMasteryTip(m, box) {
    const meta = el("div.tip-meta");
    if (m.type) meta.append(m.type);
    if (m.category) {
      if (m.type) meta.append(" · ");
      meta.append(el("span", { class: CAT_COLOR[m.categoryRaw] || "", text: m.category }));
    }
    if (costText(m) !== "") meta.append(` · Cost ${m.cost}`);
    add(box,
      el("div.tip-name", { text: m.name }),
      meta.childNodes.length ? meta : null,
      m.description && descBlock("div.tip-desc", m.description),
      m.grantsAbility && el("div.tip-grants",
        { text: t("detail.grantsAbility", "Grants ability:") + " " + m.grantsAbility }),
      m.flavor && el("div.tip-flavor", { text: "“" + m.flavor + "”" }));
  }
  function hoverChip(label, onClick, build) {
    const c = chip(label, onClick);
    if (build) {
      c.addEventListener("mouseenter", () => showTip(c, build));
      c.addEventListener("mouseleave", () => hideTip(c));
    }
    return c;
  }
  function setChip(sn) {
    const s = setByName[sn];
    return hoverChip(sn, () => gotoSet(sn), s ? (box => buildSetTip(s, box)) : null);
  }
  function masteryChip(name) {
    const m = masteryByName[name];
    return hoverChip(name, () => gotoMastery(name), m ? (box => buildMasteryTip(m, box)) : null);
  }
  // ability meta line shared by the row, tooltip and detail: "Type · Element · Slot"
  function abilityMetaText(a) {
    return [a.type, a.element, a.slot && tf("ability.slot." + a.slot.toLowerCase(), a.slot, {})]
      .filter(Boolean).join(" · ");
  }
  // append the effect text of each buff `text` references — so a hover card for a buff-enabling
  // ability (e.g. "Trigger Ferocious Tima") shows what the buff does without a click-through and a
  // second hover. Deduped, first-seen order; only buffs whose effect we have.
  function appendBuffEffects(box, text) {
    const seen = new Set();
    let m; REF_RE.lastIndex = 0;
    while ((m = REF_RE.exec(text || ""))) {
      const label = m[2];
      if (m[1] !== "buff" || seen.has(label) || !buffDesc[label]) continue;
      seen.add(label);
      box.append(el("div.tip-buffeffect", {},
        el("div.tip-buffname", { text: label }),
        descBlock("div.tip-buffbody", buffDesc[label])));
    }
  }
  function buildAbilityTip(a, box) {
    const meta = abilityMetaText(a);
    add(box,
      el("div.tip-name", { text: a.name }),
      meta && el("div.tip-meta", { text: meta }),
      a.description && descBlock("div.tip-desc", a.description));
    appendBuffEffects(box, a.description);
  }
  function abilityChip(name) {
    const a = abilityByName[name];
    if (!a) return name;   // granted ability isn't in the tab (e.g. an Interaction ability) — plain text, no dead link
    return hoverChip(name, () => gotoAbility(name), box => buildAbilityTip(a, box));
  }
  function buildBuffTip(name, box) {
    add(box,
      el("div.tip-name", { text: name }),
      descBlock("div.tip-desc", buffDesc[name]));
  }
  // id-resolved buff (dialogue "gains X") — {t: Title, e: effect}; keyed by id so colliding Titles
  // resolve to the exact buff (the enemy "Anger" state vs the Excitement stat buff both read "Rage")
  function buildBuffTipData(b, box) {
    add(box,
      el("div.tip-name", { text: b.t }),
      descBlock("div.tip-desc", b.e));
  }
  // a buff *group* card: the family name + its member buffs (each with the core line of its effect)
  function buildGroupTip(title, box) {
    const members = buffGroups[title] || [];
    const MAX = 8;
    add(box,
      el("div.tip-name", { text: title }),
      members.slice(0, MAX).map(mn =>       // core line of each member's effect (one line per member)
        el("div.tip-grpmember", {},
          el("span.tip-grpname", { text: mn }),
          buffDesc[mn] && el("span.tip-grpdesc", { text: " — " + stripMarkup(buffDesc[mn]).split("\n")[0] }))),
      members.length > MAX &&
        el("div.tip-meta", { text: tf("buff.moreMembers", "+{n} more", { n: members.length - MAX }) }));
  }
  // one inline chip for a (kind, label) ref — a clickable/hoverable pill when the target is in the
  // dataset, else plain text. mastery/ability jump on click; buff/group hover-preview their effect.
  function refNode(kind, label) {
    const linkSpan = (cls, tip, onClick) => {
      const span = el(cls, { text: label });
      span.addEventListener("mouseenter", () => showTip(span, tip));
      span.addEventListener("mouseleave", () => hideTip(span));
      if (onClick) span.addEventListener("click", e => { e.stopPropagation(); hideTip(span); onClick(); });
      return span;
    };
    if (kind === "mastery" && masteryByName[label])
      return linkSpan("span.masterylink", box => buildMasteryTip(masteryByName[label], box), () => gotoMastery(label));
    if (kind === "ability" && abilityByName[label])
      return linkSpan("span.abilitylink", box => buildAbilityTip(abilityByName[label], box), () => gotoAbility(label));
    if (kind === "group" && buffGroups[label])
      return linkSpan("span.bufflink", box => buildGroupTip(label, box));
    if (kind === "buff" && buffDesc[label])
      return linkSpan("span.bufflink", box => buildBuffTip(label, box));
    return document.createTextNode(label);              // unknown/absent target → plain text
  }
  // render `text` (with inline ref markup) into `box`, emitting a chip per ref and preserving the
  // surrounding prose / newlines. Replaces the old name-matching linker.
  // parse the inline ref markup within one logical line into `node` (text nodes + ref chips)
  function appendLine(node, text) {
    let last = 0, m; REF_RE.lastIndex = 0;
    while ((m = REF_RE.exec(text))) {
      if (m.index > last) node.append(text.slice(last, m.index));
      node.append(refNode(m[1], m[2]));
      last = m.index + m[0].length;
    }
    if (last < text.length) node.append(text.slice(last));
  }
  // render a description as one block per source line. A line flagged with the block-indent marker
  // (IND) gets a padded container so *every* wrapped row stays indented, not just the first; a blank
  // source line renders as a one-line paragraph gap (see .desc-line:empty in style.css). `rich` chips
  // inline refs (detail/cards); plain flattens them to labels (hover tooltips, non-interactive).
  function renderDesc(box, text, rich) {
    box.textContent = "";
    if (!text) return;
    for (const raw of text.split("\n")) {
      const indented = raw.startsWith(IND);
      const content = indented ? raw.slice(IND.length) : raw;
      const line = el(indented ? "div.desc-line.desc-indent" : "div.desc-line");
      if (rich) appendLine(line, content);
      else line.textContent = stripMarkup(content);
      box.append(line);
    }
  }
  function parseMarkup(box, text) { renderDesc(box, text, true); }
  // block-indented plain text (refs → bare labels) in a fresh element of `tag` — for hover tooltips
  function descBlock(tag, text) { const e = el(tag); renderDesc(e, text, false); return e; }
  function matches(blob) {
    if (!state.q) return true;
    return state.q.split(/\s+/).every(t => blob.includes(t)); // AND of terms
  }

  // ---- per-column filter row (Masteries / Abilities tables) ----
  // A filter row sits under the sortable header. Each column with a `key` gets a control whose type
  // is chosen automatically: a dropdown when the column has few distinct values (enumerable), a free-
  // text box when there are too many to enumerate. `mode` forces one ("text"/"select"); `cls` mirrors
  // the header cell's column class so the filter cell hides/shows with its column (col-cat, col-aux…).
  const DROPDOWN_MAX = 15;   // auto mode: ≤ this many distinct values → dropdown; more → free-text
  // source kinds for the Masteries "Sources" column dropdown: a value, a (localized) label, and a
  // "does this mastery have this source?" predicate — mirrors the aggregated parts built in masteryRow
  const SOURCE_KINDS = [
    { value: "enemy",       label: () => t("srcfilter.enemies", "Enemies"),         has: m => m.enemies.length > 0 },
    { value: "character",   label: () => t("srcfilter.characters", "Characters"),   has: m => m.characters.length > 0 },
    { value: "job",         label: () => t("srcfilter.jobs", "Jobs"),               has: m => m.jobs.length > 0 },
    { value: "beast",       label: () => t("srcfilter.beasts", "Beasts"),           has: m => (m.beasts || []).length > 0 },
    { value: "drone",       label: () => t("srcfilter.drones", "Drones"),           has: m => (m.drones || []).length > 0 },
    { value: "achievement", label: () => t("srcfilter.achievement", "Achievement"), has: m => (m.achievements || []).length > 0 },
    { value: "story",       label: () => t("srcfilter.story", "Story"),             has: m => (m.story || []).length > 0 },
    { value: "initial",     label: () => t("srcfilter.initial", "From start"),      has: m => !!m.initial },
    { value: "research",    label: () => t("srcfilter.research", "Research"),        has: m => (m.research || []).length > 0 },
  ];
  const MASTERY_FILTER_COLS = [
    { cls: "name",      key: "m_name", mode: "text", get: m => (m.formGroup ? m.formGroup + " " : "") + m.name },
    { cls: "col-desc",  key: "m_desc", mode: "text", get: m => stripMarkup(m.description) || m.grantsAbility || "" },
    { cls: "col-owner", key: "m_owner", mode: "text", get: m => ownerLabel(m.owner) },
    { cls: "col-type",  key: "m_type", mode: "select", get: m => m.type || "" },
    { cls: "col-cat",   key: "m_cat",  get: m => m.category || "" },
    { cls: "col-aux",   key: "m_cost", mode: "select", allShort: true,     // Cost — numeric dropdown
      get: m => { const c = costText(m); return c === "" ? "" : String(c); },
      options: rows => [...new Set(rows.map(costText).filter(v => v !== ""))]
        .sort((a, b) => a - b).map(v => ({ value: String(v), label: String(v) })) },
    { cls: "col-aux",   key: "m_sets", mode: "text", get: m => m.sets.join(" ") },
    { cls: "col-aux",   key: "m_src",  mode: "select",                     // Sources — by source kind
      options: rows => SOURCE_KINDS.filter(k => rows.some(k.has)).map(k => ({ value: k.value, label: k.label() })),
      match: (m, v) => { const k = SOURCE_KINDS.find(x => x.value === v); return !k || k.has(m); } },
  ];
  const ABILITY_FILTER_COLS = [
    { cls: "",         key: "a_name",  mode: "text", get: a => a.name },
    { cls: "",         key: "a_slot",  get: a => a.slot ? tf("ability.slot." + a.slot.toLowerCase(), a.slot, {}) : "" },
    { cls: "",         key: "a_type",  mode: "select", get: a => a.type || "" },
    { cls: "",         key: "a_elem",  get: a => a.element || "" },
    { cls: "" }, { cls: "" }, { cls: "" },                                  // Cost / CD / Cast — not filtered
    { cls: "col-desc", key: "a_desc",  mode: "text", get: a => stripMarkup(a.description) },
  ];
  // does a row satisfy every active column filter? (select → exact match, text → substring)
  function colFilterMatch(row, cols) {
    for (const c of cols) {
      if (!c.key) continue;
      const v = state.colFilters[c.key];
      if (!v) continue;
      if (state.colModes[c.key] === "select") {
        // custom predicate (Sources: membership) or exact equality on the cell value
        if (c.match ? !c.match(row, v) : (c.get(row) || "") !== v) return false;
      } else if (!(c.get(row) || "").toLowerCase().includes(v)) return false;
    }
    return true;
  }
  // build (or rebuild) a table's filter row for the current tab, sourcing dropdown options from
  // `optionRows` (the tab's full row set, so options stay stable as other filters narrow the view)
  function buildColFilterRow(tbl, cols, optionRows) {
    const thead = tbl.tHead;
    const old = thead.querySelector("tr.col-filter");
    if (old) old.remove();
    const headRow = thead.querySelector("tr.col-head");
    const headCells = headRow.children;
    const tr = el("tr.col-filter");
    cols.forEach((c, i) => {
      const th = el("th", c.cls ? { class: c.cls } : {});
      if (c.key) th.append(colFilterControl(c, optionRows, headCells[i] ? headCells[i].textContent.trim() : ""));
      tr.append(th);
    });
    thead.append(tr);
    // stick the filter row flush beneath the (sticky) header row — measured, so it holds in any locale.
    // Overlap the header by 1px: the header (higher z-index, opaque bg) paints over the seam, so
    // sub-pixel rounding during scroll can't leave a transparent hairline that reveals the rows behind.
    const base = parseFloat(getComputedStyle(headCells[0]).top) || 124;
    const top = base + headRow.offsetHeight - 1;
    tr.querySelectorAll("th").forEach(th => (th.style.top = top + "px"));
  }
  function colFilterControl(c, rows, label) {
    let mode = c.mode;
    if (!mode) {   // auto: dropdown when few distinct values (enumerable), free-text when too many
      const n = new Set(rows.map(c.get).filter(Boolean)).size;
      mode = (n >= 2 && n <= DROPDOWN_MAX) ? "select" : "text";
    }
    return mode === "select" ? selectFilter(c, rows, label) : textFilter(c, label);
  }
  // dropdown filter. Options come from c.options(rows) → [{value,label}] when given (numeric Cost, the
  // fixed source kinds); otherwise the column's distinct get() values, sorted alphabetically. Matching
  // is c.match when given, else exact equality on get() (see colFilterMatch).
  function selectFilter(c, rows, label) {
    state.colModes[c.key] = "select";
    const opts = c.options ? c.options(rows)
      : [...new Set(rows.map(c.get).filter(Boolean))].sort().map(v => ({ value: v, label: v }));
    const sel = el("select", { "aria-label": label, dataset: { colkey: c.key } });
    // narrow columns (Cost) use a compact "*" for the all-option so it isn't clipped to "A"
    sel.append(new Option(c.allShort ? t("filter.allShort", "*") : t("filter.all", "All"), ""));
    opts.forEach(o => sel.append(new Option(o.label, o.value)));
    sel.addEventListener("change", () => {
      if (sel.value) state.colFilters[c.key] = sel.value; else delete state.colFilters[c.key];
      render();
    });
    return sel;
  }
  function textFilter(c, label) {
    state.colModes[c.key] = "text";
    const inp = el("input", { type: "search", placeholder: t("colfilter.placeholder", "Filter…"),
      "aria-label": label, autocomplete: "off", spellcheck: "false", dataset: { colkey: c.key } });
    // wrap + custom × clear, mirroring the global search (native type=search cancel is hidden in CSS)
    const wrap = el("span.colf-wrap", {}, inp);
    const clear = el("button.colf-clear", { type: "button", text: "×",
      title: t("colfilter.clear", "Clear filter"), "aria-label": t("colfilter.clear", "Clear filter") });
    wrap.append(clear);
    const sync = () => wrap.classList.toggle("has-text", !!inp.value);
    let deb;
    inp.addEventListener("input", () => {
      sync();
      clearTimeout(deb);
      const v = inp.value.trim().toLowerCase();
      deb = setTimeout(() => {
        if (v) state.colFilters[c.key] = v; else delete state.colFilters[c.key];
        render();
      }, 120);
    });
    clear.addEventListener("click", e => {
      e.stopPropagation();
      inp.value = ""; sync();
      delete state.colFilters[c.key];
      render();
      inp.focus();
    });
    return wrap;
  }
  // rebuild the active table's filter row (called on tab / sub-tab change); clears prior selections
  function setupColFilters() {
    state.colFilters = {};
    state.colModes = {};
    if (state.view === "masteries") {
      let rows = DATA.masteries.filter(m => m.group === state.group);
      if (state.group === "individual") rows = rows.filter(m => m.categoryRaw === state.indivCat);
      buildColFilterRow(table, MASTERY_FILTER_COLS, rows);
    } else if (state.view === "abilities") {
      buildColFilterRow(abilityTable, ABILITY_FILTER_COLS, DATA.abilities || []);
    }
  }
  // set a column filter to `value` and reflect it in its control — used by cross-links so they land
  // scoped to the exact column (e.g. a set component → the Name column) rather than the global search.
  // Call after selectTab (the filter row must already be rebuilt for the target tab). No-op if that
  // column has no control on this tab (e.g. hidden). Caller renders.
  function setColFilter(tbl, key, value) {
    const ctrl = tbl.tHead.querySelector(`.col-filter [data-colkey="${key}"]`);
    if (!ctrl) return;
    ctrl.value = value;
    const wrap = ctrl.closest(".colf-wrap");   // reveal the × on a text filter set programmatically
    if (wrap) wrap.classList.toggle("has-text", !!value);
    state.colFilters[key] = state.colModes[key] === "select" ? value : value.toLowerCase();
  }

  // ---- masteries table ----
  function sortedMasteries(list) {
    const k = state.sortKey, d = state.sortDir;
    return list.slice().sort((a, b) => {
      let av, bv;
      if (k === "sets") { av = a.sets.length; bv = b.sets.length; }
      else if (k === "sourceCount") { av = a._sourceCount; bv = b._sourceCount; }
      else if (k === "cost") { av = a.cost == null ? -1 : a.cost; bv = b.cost == null ? -1 : b.cost; }
      else { av = (a[k] || "").toString().toLowerCase(); bv = (b[k] || "").toString().toLowerCase(); }  // incl. owner
      if (av < bv) return -1 * d;
      if (av > bv) return 1 * d;
      return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1;
    });
  }

  function renderMasteries() {
    // the Individual tab is split into Troubleshooter / Beast / Drone sub-tabs
    const isIndiv = state.group === "individual";
    els.indivSubtabs.hidden = !isIndiv;
    if (isIndiv) els.indivSubtabs.querySelectorAll(".subtab").forEach(b =>
      b.classList.toggle("active", b.dataset.cat === state.indivCat));
    let inGroup = DATA.masteries.filter(m => m.group === state.group);
    if (isIndiv) inGroup = inGroup.filter(m => m.categoryRaw === state.indivCat);
    // owner shown only on NPC now; each Individual sub-tab groups via sub-headers (Troubleshooter by
    // character, Beast by availability, Drone by Type) so owner/category — and Type on Drone — drop.
    const showOwner = state.group === "npc";
    table.classList.toggle("show-owner", showOwner);
    // Individual sub-tabs share one Category; Class Traits are all "Class" — drop the column for both
    table.classList.toggle("hide-cat", isIndiv || state.group === "class");
    table.classList.toggle("hide-type", isIndiv && state.indivCat === "Machine");
    // Cost/Sets/Sources are empty or not-meaningfully-acquired for these personal groups
    table.classList.toggle("hide-aux", ["individual", "npc", "company", "class", "misc"].includes(state.group));
    const starOn = state.starred && starGroup();   // tracking-list filter, scoped to the two board tabs
    const list = sortedMasteries(
      inGroup.filter(m => matches(m._blob) && colFilterMatch(m, MASTERY_FILTER_COLS)
        && (!starOn || starred.has(m.id))));
    els.count.textContent = tf("count.shown", "{n} of {total} {label}", { n: list.length, total: inGroup.length, label: GROUP_LABEL[state.group] });
    const frag = document.createDocumentFragment();
    // each Individual sub-tab groups differently: Beast by availability, Troubleshooter by character,
    // Drone by mastery Type (≈ how it's acquired: OS choice / reinforcement picks / craft traits)
    const sectioner = isIndiv && state.indivCat === "Beasts" ? beastAvail
                    : isIndiv && state.indivCat === "Individual" ? pcSection
                    : isIndiv && state.indivCat === "Machine" ? machineSection : null;
    if (sectioner) {
      const withSec = list.map(m => ({ m, s: sectioner(m) }));
      withSec.sort((a, b) => a.s.order[0] - b.s.order[0] || a.s.order[1] - b.s.order[1]);  // stable: keeps user sort within
      const counts = {}; withSec.forEach(r => counts[r.s.key] = (counts[r.s.key] || 0) + 1);
      let curKey = null;
      for (const { m, s } of withSec) {
        if (s.key !== curKey) { curKey = s.key; frag.appendChild(sectionHeaderRow(s.label, counts[s.key])); }
        frag.appendChild(masteryRow(m));
      }
    } else {
      for (const m of list) frag.appendChild(masteryRow(m));
    }
    els.body.replaceChildren(frag);
    if (!list.length) {
      const msg = starOn ? t("empty.starred", "No starred masteries — click ☆ on a row to track one.")
                         : t("empty.masteries", "No masteries match.");
      els.body.innerHTML = `<tr><td colspan="8" class="empty">${msg}</td></tr>`;
    }
  }

  // a section-header row spanning the table (groups the Beast sub-tab by availability)
  function sectionHeaderRow(label, n) {
    return el("tr.mast-section", {},
      el("td", { colspan: 8 }, label, el("span.mast-section-n", { text: n })));
  }
  // a beast mastery's availability group; species masteries split per offering family (cross-family
  // ones — Luminescence/Fragrance — land in one "several families" group), ordered by family order.
  const BEAST_FAM_ORDER = {}; (DATA.beastFamilies || []).forEach(f => { BEAST_FAM_ORDER[f.title] = f.order; });
  function beastAvail(m) {
    if (m.availScope === "species") {
      const fams = m.availFamilies || [];
      if (fams.length === 1)
        return { order: [2, BEAST_FAM_ORDER[fams[0]] || 99], key: "species:" + fams[0],
                 label: tf("avail.species", "Species — {family}", { family: fams[0] }) };
      return { order: [2, 999], key: "species:multi", label: t("avail.speciesMulti", "Species — several families") };
    }
    // [order, i18n-key, English fallback] — keyed off the language-neutral availScope
    const sc = { global: [0, "avail.global", "Any beast"], element: [1, "avail.element", "Element-locked"],
                 genetic: [3, "avail.genetic", "Genetic modification"] }[m.availScope] || [9, "avail.other", "Other"];
    return { order: [sc[0], 0], key: m.availScope || "other", label: t(sc[1], sc[2]) };
  }
  // Troubleshooter sub-tab section: the owning character, in canonical roster order (Pc.xml Index)
  const PC_ORDER = {}; (DATA.pcs || []).forEach(p => { PC_ORDER[p.name] = p.order != null ? p.order : 999; });
  function pcSection(m) {
    const owner = m.owner || "—";
    return { order: [PC_ORDER[owner] != null ? PC_ORDER[owner] : 999, 0], key: owner, label: ownerLabel(owner) };
  }
  // Drone sub-tab section: by mastery Type (the OS-pool reinforcement picks also carry a prefix).
  // Keyed off the language-neutral typeRaw (OperatingSystem_* variants collapse to one group) so the
  // ordering holds in every locale; the visible label is the localized m.type.
  const MACHINE_TYPE_ORDER = { OperatingSystem: 0, Application_Enhancement: 1, Application_Control: 2,
                               Power: 3, Performance: 4, Compatibility: 5 };
  function machineSection(m) {
    const tr = (m.typeRaw || "").startsWith("OperatingSystem") ? "OperatingSystem" : m.typeRaw;
    const o = MACHINE_TYPE_ORDER[tr];
    return { order: [o != null ? o : 9, 0], key: m.type || "other", label: m.type || t("section.other", "Other") };
  }
  function masteryRow(m) {
    const tr = el("tr.row", { dataset: { id: m.id } });
    const tdName = el("td.name");
    if (starGroup()) tdName.append(starToggle(m.id, "row-star"));   // tracking-list star (Masteries + Modules)
    if (m.formGroup)                                 // species mastery: prefix the offering form/group
      tdName.append(el("span.name-prefix", { text: m.formGroup }), " " + m.name);
    else tdName.append(m.name);
    const descText = stripMarkup(m.description).replace(/\s+/g, " ").trim();   // linebreaks → spaces
    const tdDesc = el("td.col-desc.desc-inline");
    if (descText) tdDesc.textContent = descText;
    else if (m.grantsAbility)   // no description of its own → surface the granted ability inline
      tdDesc.append(t("detail.grantsAbility", "Grants ability:") + " ", abilityChip(m.grantsAbility));
    // title tooltip is set lazily on hover, only when actually ellipsised (see els.body listener)
    const owners = (m.owner || "—").split(" / ").map(o => t(OWNER_I18N[o], o));
    const tdOwner = owners.length > 3
      ? el("td.col-owner.owner", { text: owners.slice(0, 3).join(" / ") + ` …(+${owners.length - 3})`, title: owners.join(" / ") })
      : el("td.col-owner.owner", { text: owners.join(" / ") || "—" });
    const tdType = el("td.col-type", { text: m.type || "" });
    const tdCat = el("td.cat.col-cat", { class: CAT_COLOR[m.categoryRaw] || "", text: m.category || "—" });
    const tdCost = el("td.cost.col-aux", { text: costText(m) });   // 0 shown only for board-placeable
    const tdSets = el("td.col-aux", {}, m.sets.map(setChip));
    const tdSrc = el("td.src.col-aux");
    const parts = [];
    if (m.enemies.length) parts.push(tf(m.enemies.length === 1 ? "src.enemy" : "src.enemies", m.enemies.length === 1 ? "{n} enemy" : "{n} enemies", { n: m.enemies.length }));
    if (m.characters.length) parts.push(tf("src.characters", "{n} character", { n: m.characters.length }));
    if (m.jobs.length) parts.push(tf("src.jobs", "{n} job", { n: m.jobs.length }));
    if ((m.beasts || []).length) parts.push(tf("src.beasts", "{n} beast", { n: m.beasts.length }));
    if ((m.drones || []).length) parts.push(tf("src.drones", "{n} drone", { n: m.drones.length }));
    if ((m.achievements || []).length) parts.push(t("src.achievement", "achievement"));
    if ((m.story || []).length) parts.push(t("src.story", "story"));
    if (m.initial) parts.push(t("src.initial", "from start"));
    if ((m.research || []).length) parts.push(t("src.research", "research"));
    tdSrc.textContent = parts.join(" · ") || "—";
    tr.append(tdName, tdDesc, tdOwner, tdType, tdCat, tdCost, tdSets, tdSrc);
    tr.addEventListener("click", () => toggleDetail(tr, m));
    return tr;
  }

  function toggleDetail(row, m) {
    const next = row.nextElementSibling;
    if (next && next.classList.contains("detail")) { next.remove(); return; }
    const td = el("td", { colspan: 8 });

    if (m.description) {
      const d = el("div.desc");
      parseMarkup(d, m.description);
      td.append(d);
    }
    if (m.flavor) td.append(el("div.flavor", { text: "“" + m.flavor + "”" }));
    if (m.grantsAbility && abilityByName[m.grantsAbility])   // cross-link to the Abilities tab
      td.append(el("div.chiplist.ability-link", {}, t("detail.grantsAbility", "Grants ability:") + " ", abilityChip(m.grantsAbility)));
    if (m.categoryRaw === "Beasts") {                 // evolution-pick availability + demoted enemy carriers
      const wrap = el("div.srclist");
      const addLine = (txt, cls) => wrap.append(el("div", { class: cls, text: txt }));
      if (m.availScope === "species") {
        const forms = m.offeredBy || [];
        const grp = (m.formGroup && forms.length > 1) ? ` (${m.formGroup})` : "";
        addLine(tf("src.offeredBy", "Offered by {n} form(s){group}:", { n: forms.length, group: grp }));
        forms.forEach(f => addLine(tf("src.formStage", "• {name} (stage {stage})", { name: f.name, stage: f.stage }), "src-form"));
      } else {
        addLine(m.availScope === "global" ? t("src.beastGlobal", "Any captured beast can roll this at an evolution.")
          : m.availScope === "element" ? tf("src.beastElement", "Only {element}-element beasts can roll this.", { element: m.owner })
          : m.availScope === "genetic" ? t("src.beastGenetic", "Available to any beast via genetic modification.")
          : t("src.beastEvoPick", "An evolution-stage pick (1 of 3 offered)."));
      }
      // Training & Nature (shown as "Instinct") are the changeable pools — re-rollable later
      if (m.typeRaw === "Training" || m.typeRaw === "Nature") addLine(t("src.beastRepick", "Re-pickable later (changeable pool)."), "src-note");
      td.append(heading(t("detail.beastEvo", "Beast evolution mastery")), wrap);
    }
    if ((m.achievements || []).length) {
      const wrap = el("div.srclist.achievement-src");
      m.achievements.forEach(a => {
        const div = el("div", {}, el("span.ach-cond", { text: a.condition || t("detail.achGeneric", "An in-game feat") }));
        if (a.achievement) div.append(el("span.ach-steam", { text: " 🏆 " + a.achievement }));
        wrap.append(div);
      });
      td.append(heading(t("detail.achievement", "Unlocked by an achievement (in-game feat)")), wrap);
    }
    if ((m.story || []).length) {
      const wrap = el("div.srclist.story-src");
      m.story.forEach(s => {
        const where = s.scenario ? s.scenario + " · " + s.mission : s.mission;   // "Ch4 Scent… · Sky-wind park"
        const div = el("div", { text: s.tutorial
          ? tf("detail.storyTutorial", "{mission} (tutorial)", { mission: where })
          : s.choice
          ? tf("detail.storyChoice", "{mission} — choose “{choice}”", { mission: where, choice: s.choice })
          : tf("detail.storyReward", "{mission} (mission reward)", { mission: where }) });
        // opened-for-research group: the choice picks which you're awarded, but all become craftable
        if (s.opened)
          div.append(el("span.story-opened", { text: " " +
            t("detail.storyOpened", "(still opened for research even if not awarded)") }));
        wrap.append(div);
      });
      td.append(heading(t("detail.story", "Unlocked by a story mission")), wrap);
    }
    if (m.initial)
      td.append(heading(t("detail.initial", "Available from the start")),
        el("div.src-note", { text: t("detail.initialLine", "Unlocked by default — no research or analysis needed.") }));
    if ((m.research || []).length) {
      const wrap = el("div.srclist.story-src");
      m.research.forEach(p => wrap.append(el("div", {},
        t("detail.researchLine", "Researched after crafting") + " ", masteryChip(p))));
      td.append(heading(t("detail.research", "Unlocked by mastery research")), wrap);
    }
    const charLevel = m.characters.filter(c => !c.classBasic);   // level-gated job unlocks
    const charBasic = m.characters.filter(c => c.classBasic);     // granted with the class (no level)
    if (charLevel.length)
      srcSection(td, t("detail.charUnlock", "Unlocked by levelling a character in a job"), charLevel,
        c => tf("detail.charLine", "{character} as {job} — Lv {lv}", { character: c.character, job: c.job, lv: c.lv }));
    if (charBasic.length)
      srcSection(td, t("detail.charBasic", "Granted with a character’s class"), charBasic,
        c => tf("detail.charBasicLine", "{character} as {job}", { character: c.character, job: c.job }));
    if ((m.beasts || []).length)
      srcSection(td, t("detail.beastUnlock", "Unlocked by capturing & levelling a beast"), m.beasts,
        b => tf("detail.lvLine", "{name} — Lv {lv}", { name: b.beast, lv: b.lv }));
    if ((m.drones || []).length)
      srcSection(td, t("detail.droneUnlock", "Unlocked by building & levelling a drone"), m.drones,
        b => tf("detail.lvLine", "{name} — Lv {lv}", { name: b.drone, lv: b.lv }));
    if (m.enemies.length && m.categoryRaw !== "Beasts" && m.categoryRaw !== "Machine") {   // beast/drone are picks, not enemy-learned
      const wrap = el("div.srclist.enemy-src");
      const renderEnemy = e => {
        const lv = e.lv ? (e.lv[0] === e.lv[1] ? ` (Lv ${e.lv[0]})` : ` (Lv ${e.lv[0]}–${e.lv[1]})`) : "";
        const div = el("div", {}, el("span.enemy-name", { text: e.name + lv }));
        if (e.missions && e.missions.length) {
          const ms = el("div.missions");
          e.missions.forEach(mm => {
            if (mm.training) {                       // fightable in the Joint Drill mode
              let txt = t("detail.jointTraining", "⚔ Joint Drill");
              if (mm.teams && mm.teams.length) txt += " — " + mm.teams.join(", ");
              ms.append(el("span.mission.training", { text: txt }));
              return;
            }
            const item = el("span.mission", {},
              el("span", { class: "lvl-badge case-" + mm.case, text: mm.level || "?", title: caseTip(mm.case) }));
            let suffix = "";
            if (mm.dialog) suffix = typeof mm.dialog === "string" ? ` (dialog: ${mm.dialog})` : " (dialog)";
            else if (mm.diff) suffix = ` [${mm.diff}]`;
            item.append(mm.name + suffix);
            if (mm.dialog) item.classList.add("dialog");
            ms.append(item);
          });
          div.append(ms);
        }
        return div;
      };
      // sort by the lowest-level mission an enemy appears in (so the earliest-reachable carrier
      // leads); Joint-Training-only / level-less enemies sort last, then alphabetical as tiebreak.
      const minLv = e => {
        const ls = (e.missions || []).filter(mm => !mm.training && mm.level != null).map(mm => mm.level);
        return ls.length ? Math.min(...ls) : Infinity;
      };
      const ordered = m.enemies.slice().sort((a, b) => minLv(a) - minLv(b) || a.name.localeCompare(b.name));
      wrap.append(renderEnemy(ordered[0]));
      if (ordered.length > 1) {                       // fold every carrier after the first
        const rest = ordered.slice(1);
        const fold = el("div.enemy-fold");
        const tg = el("span.enemy-toggle", { text: tf("detail.moreEnemies", "+{n} more", { n: rest.length }) });
        tg.addEventListener("click", () => fold.classList.toggle("open"));
        const body = el("div.enemy-more");
        rest.forEach(e => body.append(renderEnemy(e)));
        fold.append(tg, body);
        wrap.append(fold);
      }
      td.append(heading(tf("detail.enemies", "Learnable from enemies ({n})", { n: m.enemies.length })), wrap);
    }
    if (m.jobs.length)
      srcSection(td, t("detail.job", "Job"), m.jobs, j => j);
    if (!m.description && !m.characters.length && !m.enemies.length && !m.jobs.length && !(m.beasts || []).length
        && !(m.drones || []).length && !(m.achievements || []).length && !(m.story || []).length && !m.initial
        && !(m.research || []).length)
      td.append(el("div.flavor", { text: t("detail.noSource", "No source data (learned via story, quest or class change).") }));
    row.after(el("tr.detail", {}, td));
  }
  function heading(text) { return el("h4", { text }); }
  // a detail block: a heading followed by a `.srclist` with one <div> per item (text from lineFn)
  function srcSection(parent, headingText, items, lineFn) {
    add(parent, heading(headingText),
      el("div.srclist", {}, items.map(it => el("div", { text: lineFn(it) }))));
  }

  // ---- set cards ----
  function renderSets() {
    const list = DATA.sets
      .filter(s => matches(s._blob) && (!state.type || s.type === state.type))
      .sort((a, b) => a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1);
    els.count.textContent = tf("count.sets", "{n} of {total} sets", { n: list.length, total: DATA.sets.length });
    const frag = document.createDocumentFragment();
    for (const s of list) {
      const card = el("div.card", {},
        el("h3", { text: s.name }, s.dlc && el("span.badge.dlc", { text: s.dlc })),
        el("div.meta", { text: s.type || "" }),
        el("div.components", {}, s.components.map(c => masteryChip(c.name))));
      if (s.bonus) {
        const b = el("div.bonus");
        parseMarkup(b, s.bonus);
        card.append(b);
      }
      frag.appendChild(card);
    }
    els.grid.replaceChildren(frag);
    if (!list.length) els.grid.innerHTML = `<div class="empty">${t("empty.sets", "No sets match.")}</div>`;
  }

  function renderEquipSets() {
    const all = DATA.equipmentSets || [];
    const list = all
      .filter(e => matches(e._blob) && (!state.type || e.type === state.type))
      .sort((a, b) => a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1);
    els.count.textContent = tf("count.equipsets", "{n} of {total} equipment sets", { n: list.length, total: all.length });
    const frag = document.createDocumentFragment();
    for (const e of list) {
      const card = el("div.card", {},
        el("h3", { text: e.name }),
        e.type && el("div.meta", { text: e.type }),
        el("div.equip-thresholds", {}, e.thresholds.map(th =>
          el("div.equip-th", {},
            el("span.equip-n", { text: tf("equip.pieces", "{n} pc", { n: th.n }) }),
            el("span.equip-desc", { text: th.desc })))));
      frag.appendChild(card);
    }
    els.equipGrid.replaceChildren(frag);
    if (!list.length) els.equipGrid.innerHTML = `<div class="empty">${t("empty.equipsets", "No equipment sets match.")}</div>`;
  }

  // ---- abilities table ----
  const ABIL_NUM = new Set(["cost", "sp", "cooldown", "castDelay", "range", "targets"]);
  function sortedAbilities(list) {
    const k = state.sortKey, d = state.sortDir;
    return list.slice().sort((a, b) => {
      let av, bv;
      if (ABIL_NUM.has(k)) { av = a[k] == null ? -1 : a[k]; bv = b[k] == null ? -1 : b[k]; }
      else { av = (a[k] || "").toString().toLowerCase(); bv = (b[k] || "").toString().toLowerCase(); }
      if (av < bv) return -1 * d;
      if (av > bv) return 1 * d;
      return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1;
    });
  }
  function renderAbilities() {
    const all = DATA.abilities || [];
    const list = sortedAbilities(all.filter(a => matches(a._blob) && colFilterMatch(a, ABILITY_FILTER_COLS)));
    els.count.textContent = tf("count.abilities", "{n} of {total} abilities", { n: list.length, total: all.length });
    const frag = document.createDocumentFragment();
    for (const a of list) frag.appendChild(abilityRow(a));
    els.abilityBody.replaceChildren(frag);
    if (!list.length) els.abilityBody.innerHTML = `<tr><td colspan="8" class="empty">${t("empty.abilities", "No abilities match.")}</td></tr>`;
  }
  function abilityRow(a) {
    const tr = el("tr.row", { dataset: { id: a.id } });
    const cell = (cls, txt) => el("td", { class: cls, text: txt == null ? "" : txt });
    const slot = a.slot ? tf("ability.slot." + a.slot.toLowerCase(), a.slot, {}) : "";
    const tdDesc = cell("col-desc desc-inline", stripMarkup(a.description).replace(/\s+/g, " ").trim());
    const tdName = el("td.name", { text: a.name });   // owner badge(s): the character(s)/beast that field it
    (a.owners || []).forEach(o => tdName.append(el("span.owner-badge", { text: o })));
    tr.append(
      tdName, cell("col-slot", slot), cell("col-type", a.type || ""),
      cell("col-elem", a.element || ""), cell("num", a.cost == null ? "" : a.cost),
      cell("num", a.cooldown == null ? "" : a.cooldown), cell("num", a.castDelay == null ? "" : a.castDelay),
      tdDesc);
    tr.addEventListener("click", () => toggleAbilityDetail(tr, a));
    return tr;
  }
  function toggleAbilityDetail(row, a) {
    const next = row.nextElementSibling;
    if (next && next.classList.contains("detail")) { next.remove(); return; }
    const td = el("td", { colspan: 8 });
    if (a.description) {
      const d = el("div.desc");
      parseMarkup(d, a.description);
      td.append(d);
    }
    // stat line: range / targets / hit-rate / SP, whatever's present
    const stats = [];
    if (a.range != null) stats.push(a.range === 0 ? t("ability.self", "Self") : tf("ability.range", "Range {n}", { n: a.range }));
    if (a.targets) stats.push(tf("ability.targets", "{n} targets", { n: a.targets }));
    if (a.sp) stats.push(tf("ability.sp", "{n} SP", { n: a.sp }));
    if (a.hitRate) stats.push(tf("ability.hit", "{type} hit", { type: a.hitRate }));
    if (stats.length) td.append(el("div.ability-stats", { text: stats.join(" · ") }));
    if (a.owners && a.owners.length)   // which character(s) / beast family field it
      td.append(el("div.ability-owners", { text: t("detail.abilityOwners", "Available to") + ": " + a.owners.join(", ") }));
    if (a.flavor) td.append(el("div.flavor", { text: "“" + a.flavor + "”" }));
    if (a.grantedBy && a.grantedBy.length)
      td.append(heading(t("detail.abilityGranted", "Granted by masteries")),
        el("div.chiplist", {}, a.grantedBy.map(masteryChip)));
    if (a.modifiedBy && a.modifiedBy.length)
      td.append(heading(t("detail.abilityModified", "Modified by masteries")),
        el("div.chiplist", {}, a.modifiedBy.map(masteryChip)));
    row.after(el("tr.detail", {}, td));
  }

  // ---- quests (Shooter Street NPC request chains) ----
  function questReward(r) {
    if (r.kind === "recipe")
      return el("li", { text: tf("reward.recipe", "Recipe: {name}", { name: r.name }) + (r.amount > 1 ? " ×" + r.amount : "") });
    if (r.kind === "troublemaker")
      return el("li", { text: tf("reward.troublemaker", "{n}× {pool} Troublemaker info", { n: r.amount, pool: r.pool }) });
    if (r.kind === "workmanship")
      return el("li", { text: tf("reward.workmanship", "{pool} workmanship +{n}", { pool: r.pool, n: r.amount }) });
    return el("li", { text: (r.amount > 1 ? r.amount + "× " : "") + r.name });
  }
  // prerequisites worth flagging: anything other than the immediately-preceding quest in the
  // same NPC chain (i.e. cross-NPC unlocks like Roberto←Maximillion, and same-chain merges)
  function questRequires(q) {
    const reqs = q.prereqs.map(id => QUEST_BY_ID[id])
      .filter(p => p && !(p.npc === q.npc && p.chainIndex === q.chainIndex - 1));
    if (!reqs.length) return null;
    const labels = reqs.map(p => p.npcName + " #" + p.chainIndex);
    return el("div.quest-req", {},
      el("span.quest-req-ico", { text: "⚠" }),
      el("span", { text: t("quest.requires", "Requires") + ": " + labels.join(", ") }));
  }
  function questCard(q) {
    const card = el("div.card.quest-card", {},
      el("div.quest-head", {},
        el("span.quest-idx", { text: "#" + q.chainIndex }),
        el("span.quest-title", { text: q.title })),
      el("div.quest-meta", {},
        el("span", { text: q.typeLabel })));
    if (q.unlockMission)
      card.append(el("div.quest-unlock", {},
        el("span.quest-unlock-lbl", { text: t("quest.unlockAfter", "Unlocks after") + ": " }),
        q.unlockMission.level != null && el("span", { class: "lvl-badge case-" + q.unlockMission.case,
          text: q.unlockMission.level, title: caseTip(q.unlockMission.case) }),
        el("span", { text: q.unlockMission.name })));
    if (q.locations.length) card.append(el("div.quest-loc", { text: "📍 " + q.locations.join(" · ") }));
    if (q.objective) card.append(el("div.quest-obj", { text: q.objective }));
    if (q.dropFrom.length)
      card.append(el("div.quest-drop", { text: t("quest.dropFrom", "Drops from") + ": " + q.dropFrom.join(", ") }));
    const req = questRequires(q);
    if (req) card.append(req);
    if (q.rewards.length) {
      if (q.rewards.length > 1) card.append(el("div.quest-reward-h", { text: t("quest.chooseOne", "Choose one of:") }));
      card.append(el("ul.quest-rewards", {}, q.rewards.map(questReward)));
    }
    if (q.friendship) card.append(el("div.quest-friend", { text: tf("quest.friendship", "+{n} Friendship", { n: q.friendship }) }));
    if (q.unlocksJointDrill)
      card.append(el("div.quest-unlock-reward", { text: "🔓 " + t("quest.unlocksJointDrill", "Unlocks Joint Drill") }));
    return card;
  }
  function renderQuests() {
    const all = DATA.quests || [];
    const list = all.filter(q => matches(q._blob) && (!state.type || q.typeLabel === state.type));
    els.count.textContent = tf("count.quests", "{n} of {total} quests", { n: list.length, total: all.length });
    // group by NPC; order NPCs by the earliest quest in their chain (roughly story order)
    const groups = new Map();
    for (const q of list) { if (!groups.has(q.npc)) groups.set(q.npc, []); groups.get(q.npc).push(q); }
    const order = [...groups.values()].map(qs => ({
      qs, name: qs[0].npcName, lead: Math.min(...qs.map(q => q.stageLv || 999)),
    })).sort((a, b) => a.lead - b.lead || (a.name < b.name ? -1 : 1));
    const frag = document.createDocumentFragment();
    for (const g of order) {
      g.qs.sort((a, b) => a.chainIndex - b.chainIndex);
      frag.append(el("h2.quest-npc", {},
        el("span", { text: g.name }),
        el("span.quest-npc-count", { text: g.qs.length })));
      frag.append(el("div.quest-grid", {}, g.qs.map(questCard)));
    }
    els.questsList.replaceChildren(frag);
    if (!list.length) els.questsList.innerHTML = `<div class="empty">${t("empty.quests", "No quests match.")}</div>`;
  }

  // ---- cross-links ----
  // A cross-link jump is the *only* navigation pushed onto browser history, so Back returns to exactly
  // where you were before following the link. Manual tab/filter changes stay non-back-able on purpose —
  // that keeps Back meaningful ("undo my last link-follow") instead of a maze. historyJump pins the
  // live pre-jump state into the current entry (so Back lands there even after manual work since the
  // last jump), performs the navigation, then pushes the post-jump state as a new back-able entry.
  function historyJump(navigate) {
    history.replaceState(captureState(), "");
    navigate();
    history.pushState(captureState(), "");
  }
  function gotoSet(name) {
    historyJump(() => { setView("sets"); els.search.value = name; state.q = name.toLowerCase(); syncSearchClear(); render(); });
  }
  function gotoAbility(name) {
    historyJump(() => {
      selectTab(document.querySelector('.tab[data-view="abilities"]'));
      els.search.value = name; state.q = name.toLowerCase(); syncSearchClear(); render();
    });
  }
  function gotoMastery(name) {
    historyJump(() => {
      const m = DATA.masteries.find(x => x.name === name);
      const grp = m ? m.group : "normal";
      selectTab(document.querySelector(`.tab[data-group="${grp}"]`));   // rebuilds the target tab's filter row
      els.search.value = ""; state.q = ""; syncSearchClear();           // scope by the Name column, not the global search
      setColFilter(table, "m_name", name);
      render();
    });
  }
  // ---- browser-history restore (Back / Forward) ----
  // A serialisable snapshot of everything the views render from (the builder keeps its own state in
  // bldStore/localStorage, so view:"builder" just restores the tab).
  function captureState() {
    return { view: state.view, group: state.group, indivCat: state.indivCat,
      q: state.q, type: state.type, colFilters: { ...state.colFilters },
      sortKey: state.sortKey, sortDir: state.sortDir, starred: state.starred };
  }
  // the tab button for a snapshot. Only the masteries view has multiple tabs (by group); every other
  // view is a single tab, so fall back to the first match on view (the stored group is a stale
  // masteries group there and must not block the lookup).
  const tabFor = (view, group) => {
    const tabs = [...document.querySelectorAll(".tab")].filter(x => x.dataset.view === view);
    return tabs.find(x => (x.dataset.group || "") === (group || "")) || tabs[0] || null;
  };
  // paint the sort arrow on a table's header to match state. Name-ascending is the table's natural
  // order, so it reads as "unsorted" — no arrow (and clicking Name from any other sort returns to it).
  function syncSortArrows(tbl) {
    const showArrow = !(state.sortKey === "name" && state.sortDir === 1);
    tbl.querySelectorAll("th[data-sort]").forEach(h => {
      h.classList.remove("sorted-asc", "sorted-desc");
      if (showArrow && h.dataset.sort === state.sortKey)
        h.classList.add(state.sortDir === 1 ? "sorted-asc" : "sorted-desc");
    });
  }
  // rebuild the UI to match a snapshot (on popstate). selectTab reruns populateTypes → setupColFilters,
  // which rebuilds the filter row and resets colFilters, so restore the saved filters *after* it.
  function applyState(s) {
    if (!s) return;
    els.search.value = s.q || ""; state.q = s.q || ""; syncSearchClear();
    state.indivCat = s.indivCat || "Individual";
    state.sortKey = s.sortKey || "name"; state.sortDir = s.sortDir || 1;
    state.starred = !!s.starred;
    const tab = tabFor(s.view, s.group);
    if (tab) selectTab(tab);
    if (s.type) { els.type.value = s.type; state.type = els.type.value; }   // card-view Type dropdown
    const tbl = s.view === "masteries" ? table : s.view === "abilities" ? abilityTable : null;
    if (tbl) {
      for (const k in (s.colFilters || {})) setColFilter(tbl, k, s.colFilters[k]);
      syncSortArrows(tbl);
    }
    render();
  }

  // ---- view / filter wiring ----
  function populateTypes() {
    // Masteries & Abilities are tables → their Type/Category (and more) live in the in-table filter
    // row; the shared top-bar Type/Category dropdowns are only for the card/list views (no columns).
    els.catLabel.style.display = "none";   // Category only ever applied to masteries → now a column filter
    els.cat.value = ""; state.category = "";
    if (state.view === "masteries" || state.view === "abilities") {
      els.typeLabel.style.display = "none";
      els.type.value = ""; state.type = "";
      setupColFilters();
      return;
    }
    els.typeLabel.style.display = "";
    let values;
    if (state.view === "dialogue") {
      values = [...new Set((DATA.dialogues || []).map(s => s.case).filter(Boolean))].sort();
    } else if (state.view === "quests") {
      values = [...new Set((DATA.quests || []).map(q => q.typeLabel).filter(Boolean))].sort();
    } else {
      const src = state.view === "sets" ? DATA.sets : (DATA.equipmentSets || []);
      values = [...new Set(src.map(x => x.type).filter(Boolean))].sort();
    }
    els.type.replaceChildren(new Option(t("filter.all", "All"), ""));
    // dialogue values are raw case-type keys (Scenario/Quest/…) — show a localized label but keep
    // the key as the option value; quest/set values are already localized in the data, so pass through
    const optLabel = state.view === "dialogue" ? caseName : (v => v);
    values.forEach(v => els.type.appendChild(new Option(optLabel(v), v)));
    els.type.value = "";
    state.type = "";
  }
  const TAB_KEY = "ts:activeTab";   // NOTE: i18n.js restores this tab's visual state pre-paint — keep the key + the .tab/.view/.active/.controls contract in sync
  function selectTab(tab) {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    state.view = tab.dataset.view;
    if (tab.dataset.group) state.group = tab.dataset.group;
    document.querySelectorAll(".view").forEach(v =>
      v.classList.toggle("active", v.id === "view-" + state.view));
    // the builder has its own toolbar; hide the shared search/type/category bar
    document.querySelector(".controls").style.display = state.view === "builder" ? "none" : "";
    try { localStorage.setItem(TAB_KEY, JSON.stringify({ view: tab.dataset.view, group: tab.dataset.group || "" })); } catch (e) { /* storage blocked */ }
    populateTypes();
  }
  // the tab button matching a saved {view, group}, or null
  function savedTab() {
    let t;
    try { t = JSON.parse(localStorage.getItem(TAB_KEY) || "null"); } catch (e) { t = null; }
    if (!t) return null;
    return [...document.querySelectorAll(".tab")]
      .find(x => x.dataset.view === t.view && (x.dataset.group || "") === (t.group || "")) || null;
  }
  function setView(view) {  // used by cross-links; pick the matching tab
    const sel = view === "sets" ? '.tab[data-view="sets"]' : '.tab[data-group="normal"]';
    selectTab(document.querySelector(sel));
  }
  function render() {
    updateStarFilterUI();
    if (state.view === "masteries") renderMasteries();
    else if (state.view === "sets") renderSets();
    else if (state.view === "equipsets") renderEquipSets();
    else if (state.view === "abilities") renderAbilities();
    else if (state.view === "builder") renderBuilder();
    else if (state.view === "quests") renderQuests();
    else renderDialogue();
  }

  // ---- dialogue ----
  function sceneLineEl(ln) {
    const depth = (ln.match(/^\t+/) || [""])[0].length;
    const text = ln.slice(depth);
    const li = el("div.dlg-line", { text });
    if (text.startsWith("▷ presents choice:")) li.classList.add("choice-line");
    if (text.startsWith("⎇ switch")) li.classList.add("switch-line");
    if (text.startsWith("❖ ")) li.classList.add("title-line");
    if (text.startsWith("◎ ")) li.classList.add("objective-line");
    if (depth) li.style.marginLeft = (depth * 14) + "px";
    return li;
  }

  // append a rule's DO actions (each played scene's lines behind a sub-toggle, except
  // a single-line scene which shows that line directly in place of "play scene")
  function appendDoItems(container, rule) {
    rule.do.forEach(dd => {
      if (dd.lines && dd.lines.length === 1) {
        container.appendChild(sceneLineEl(dd.lines[0]));
        return;
      }
      const dn = el("div.do", { text: dd.text });
      container.appendChild(dn);
      if (dd.lines && dd.lines.length) {
        dn.classList.add("has-lines");
        const box = el("div.dlg-lines", {}, dd.lines.map(sceneLineEl));
        dn.addEventListener("click", e => {
          e.stopPropagation();
          dn.classList.toggle("open");
          box.classList.toggle("open");
        });
        container.appendChild(box);
      }
    });
  }

  // one trigger rule → DOM (name + WHEN + actions). whenOverride: a string to show
  // instead of rule.when ("" = omit the WHEN line, e.g. when a group header covers it).
  function renderRule(rule, whenOverride) {
    const rl = el("div.rule");
    if (rule.name) rl.append(el("span.rule-name", { text: rule.name }));
    const cond = whenOverride !== undefined ? whenOverride : (rule.when || "");
    rl.append(el("div.when", { text: cond ? (rule.repeat ? "WHENEVER " : "ONCE WHEN ") + cond
      : (rule.repeat ? "EACH TIME" : "ONCE") }));
    appendDoItems(rl, rule);
    return rl;
  }

  // render a stage's rules, grouping a run of consecutive rules that share a condition
  // term (factored into a "WHEN <shared>:" header) and/or test the same variable with
  // differing values (an EventID-style sequence, surfaced as "by <var>:")
  const GROUP_LEVELS = 2;                                   // primary + secondary variable grouping

  // {label, sort} describing how rule r constrains variable v (for bucketing/sorting)
  function varKey(r, v) {
    let label = null;
    if (r.terms) { const t = r.terms.find(x => x.v === v); if (t) label = t.t; }
    if (label === null && r.vt && r.vt[v]) label = r.vt[v];   // non-flat rule's rendered test
    if (label === null) label = v + " == ?";
    let value = (r.vv && r.vv[v] !== undefined) ? r.vv[v] : null;
    if (value === null) { const m = label.match(/(-?\d+)\s*$/); if (m) value = m[1]; }
    const n = value !== null ? parseFloat(value) : NaN;
    return { label, sort: isNaN(n) ? null : n };
  }

  // residual WHEN for a leaf, dropping variables already shown by ancestor headers
  function residualWhen(r, used) {
    if (!r.terms) return undefined;                         // non-flat → show full WHEN
    return r.terms.filter(x => !(x.v && used.has(x.v))).map(x => x.t).join(" and ");
  }

  // recursively group a set of rules: each rule joins its dominant variable's group
  // (≥2 members), buckets by that variable's value, then recurses one level deeper
  function layout(container, rules, used, depth) {
    // assign each rule to its dominant groupable variable
    const freq = {};
    if (depth < GROUP_LEVELS)
      for (const r of rules)
        for (const v of (r.vars || [])) if (!used.has(v)) freq[v] = (freq[v] || 0) + 1;
    const domOf = r => {
      let best = null, bn = 1;
      for (const v of (r.vars || [])) if (!used.has(v) && freq[v] > bn) { best = v; bn = freq[v]; }
      return best;
    };
    const members = {};                                     // var -> [{r, idx}]
    rules.forEach((r, idx) => {
      const v = domOf(r);
      if (v) (members[v] = members[v] || []).push({ r, idx });
    });
    // a group needs ≥2 members; otherwise its rules fall back to loose
    const groups = Object.keys(members).filter(v => members[v].length >= 2);
    const grouped = new Set();
    groups.forEach(v => members[v].forEach(o => grouped.add(o.idx)));

    // render groups (anchored at first member) and loose rules, in document order
    const items = groups.map(v => ({ idx: Math.min(...members[v].map(o => o.idx)), v }));
    rules.forEach((r, idx) => { if (!grouped.has(idx)) items.push({ idx, r }); });
    items.sort((a, b) => a.idx - b.idx);

    for (const it of items) {
      if (it.r) { container.appendChild(renderRule(it.r, residualWhen(it.r, used))); continue; }
      const v = it.v;
      const rs = members[v].map(o => o.r);
      const buckets = {};                                   // label -> {rules, sort}
      rs.forEach(r => {
        const k = varKey(r, v);
        (buckets[k.label] = buckets[k.label] || { rules: [], sort: k.sort }).rules.push(r);
      });
      const labels = Object.keys(buckets).sort((a, b) => {
        const sa = buckets[a].sort, sb = buckets[b].sort;
        if (sa != null && sb != null) return sa - sb;
        return a.localeCompare(b);
      });
      const used2 = new Set(used); used2.add(v);
      const grp = el("div.rule-group");
      if (labels.length === 1) {                            // single value → fold into header
        grp.append(el("div.rule-group-when", { text: "WHEN " + labels[0] + ":" }));
        layout(grp, buckets[labels[0]].rules, used2, depth + 1);
      } else {
        grp.append(el("div.rule-group-when", { text: "by " + v + ":" }));
        for (const lab of labels) {
          grp.append(el("div.rule-group-when.bucket", { text: lab + ":" }));
          const sub = el("div.rule-subgroup");
          layout(sub, buckets[lab].rules, used2, depth + 1);
          grp.appendChild(sub);
        }
      }
      container.appendChild(grp);
    }
  }

  function renderScriptInto(container, rules) {
    layout(container, rules, new Set(), 0);
  }

  // "triggers N [additional] rule(s)" foldable list of full rules
  function triggersToggle(indices, s, additional) {
    const tw = el("div.opt-triggers");
    const n = indices.length;
    const tg = el("span.trig-toggle", { text: `triggers ${n} ${additional ? "additional " : ""}rule${n === 1 ? "" : "s"}` });
    tg.addEventListener("click", () => tw.classList.toggle("open"));
    const tb = el("div.trig-body");
    indices.forEach(idx => { const r = s.script[idx]; if (r) tb.appendChild(renderRule(r)); });
    tw.append(tg, tb);
    return tw;
  }

  // one decision → DOM, nesting any follow-up decisions a choice leads to
  function renderDecision(di, s, asChild, visited, rendered) {
    rendered.add(di);
    const dec = s.decisions[di];
    const d = el("div.decision", { class: asChild && "child" });
    if (dec.prompt) d.append(el("div.prompt", { text: dec.prompt }));
    if (!asChild && dec.shown_by && dec.shown_by.length) {   // children show via their parent
      dec.shown_by.forEach(idx => {
        const rule = s.script[idx];
        if (!rule) return;
        const av = el("div.appears-via");
        const cond = el("div.av-cond", { text: "when " + (rule.when || "(always)") });
        cond.addEventListener("click", () => av.classList.toggle("open"));
        const detail = el("div.av-detail");
        appendDoItems(detail, rule);
        av.append(cond, detail);
        d.appendChild(av);
      });
    }
    const ul = el("ul.options");
    // group options by shared consequence; else by shared trigger set
    const groups = [];
    const idxByKey = {};
    dec.options.forEach(o => {
      // group only options that are fully equivalent: same consequences AND same
      // triggered rules. Two choices with identical consequence text but different
      // scripts (e.g. Irene Rush vs Irene Cover) must stay separate.
      const hasInfo = o.consequences.length || (o.triggers && o.triggers.length);
      const ckey = o.consequences.map(c => c.text).slice().sort().join("||");
      const tkey = (o.triggers || []).slice().sort((a, b) => a - b).join(",");
      const key = hasInfo ? ckey + "##" + tkey : null;
      if (key !== null && key in idxByKey) groups[idxByKey[key]].opts.push(o);
      else { if (key !== null) idxByKey[key] = groups.length; groups.push({ opts: [o] }); }
    });
    groups.forEach(g => {
      const li = el("li");
      if (g.opts.length > 1) li.classList.add("grouped");

      const trigs = [...new Set(g.opts.flatMap(o => o.triggers || []))].sort((a, b) => a - b);
      // a triggered rule gated *only* by the variable(s) this choice sets is the choice's
      // direct consequence (script shown inline under the option); rules with extra ambient
      // conditions (e.g. a phase variable) are kept under "triggers N additional rules"
      const setVars = new Set(g.opts.flatMap(o => (o.sets || []).map(sv => sv.split("=")[0])));
      const direct = [], additional = [];
      trigs.forEach(idx => {
        const r = s.script[idx];
        if (!r) return;
        const pure = r.terms && r.terms.length && r.terms.every(t => t.v && setVars.has(t.v));
        (pure ? direct : additional).push(idx);
      });
      const foldable = direct.length > 0;

      g.opts.forEach(o => {
        const ot = el("div.opt-text", { class: foldable && "foldable", text: o.text });
        if (foldable) ot.addEventListener("click", () => li.classList.toggle("open"));
        li.appendChild(ot);
      });

      const cons = g.opts[0].consequences;
      if (cons.length)                            // extracted top-level consequences stay above
        li.append(el("div.conseqs", {}, cons.map(c => {
          // "mastery" grants a copy, "opened" only unlocks it for research — both link to the row
          const linked = (c.kind === "mastery" || c.kind === "opened") && c.mastery && masteryById[c.mastery];
          // the data text ("Grants X" / "Opens X …") is English (dialogue text is English-deferred);
          // when we know the mastery, relabel it localized from its (localized) name + a clickable link
          const label = !linked ? c.text
            : c.kind === "opened"
            ? tf("dlg.opens", "Opens {name} for research", { name: masteryById[c.mastery].name })
            : tf("dlg.grants", "Grants {name}", { name: masteryById[c.mastery].name });
          const span = el("span.conseq", { class: "kind-" + c.kind, text: label });
          if (linked) {
            const gm = masteryById[c.mastery];
            span.classList.add("clickable");       // jump to the granted/opened mastery's row
            span.addEventListener("click", e => { e.stopPropagation(); gotoMastery(gm.name); });
            span.addEventListener("mouseenter", () => showTip(span, box => buildMasteryTip(gm, box)));
            span.addEventListener("mouseleave", () => hideTip(span));
          } else if (c.kind === "buff" && buffById[c.buff]) {
            const b = buffById[c.buff];            // id-keyed → exact buff even when Titles collide
            span.classList.add("hoverable");       // hover-preview the buff's effect (no jump target)
            span.addEventListener("mouseenter", () => showTip(span, box => buildBuffTipData(b, box)));
            span.addEventListener("mouseleave", () => hideTip(span));
          }
          return span;
        })));

      if (foldable) {
        // expanding the option reveals the direct consequence script (no rule chrome),
        // with any extra-condition rules below as "triggers N additional rules"
        const body = el("div.opt-script");
        direct.forEach(idx => { const r = s.script[idx]; if (r) appendDoItems(body, r); });
        if (additional.length) body.appendChild(triggersToggle(additional, s, true));
        li.appendChild(body);
      } else if (trigs.length) {
        li.appendChild(triggersToggle(trigs, s, false));
      }

      // nested follow-up conversation(s) — render each decision once (spanning tree),
      // so branching/permutation chains don't blow up combinatorially
      const leads = [...new Set(g.opts.flatMap(o => o.leads_to || []))]
        .filter(ci => !visited.has(ci) && !rendered.has(ci));
      leads.forEach(ci => {
        const nv = new Set(visited);
        nv.add(ci);
        li.appendChild(renderDecision(ci, s, true, nv, rendered));
      });
      ul.appendChild(li);
    });
    d.appendChild(ul);
    return d;
  }

  function renderDialogue() {
    const all = DATA.dialogues || [];
    const list = all.filter(s => matches(s._blob) && (!state.type || s.case === state.type));
    els.count.textContent = tf("count.dialogues", "{n} of {total} dialogue stages", { n: list.length, total: all.length });
    const frag = document.createDocumentFragment();
    for (const s of list) {
      const row = el("div.dlg-stage");
      const dn = s.decisions.length;   // separate singular/plural keys so EN reads grammatically (Korean ignores plural)
      const head = el("div.dlg-head", {},
        el("span", { class: "lvl-badge case-" + s.case, text: s.level || "?", title: caseTip(s.case) }),
        // scenario missions: lead with the story name + chapter (you find them by name first),
        // then the location; side-quest stages lead with the quest name instead (they share a
        // location with no chapter name to tell them apart); other case types have only a location
        s.scenario && el("span.dlg-scenario", { text: s.scenario }),
        s.questName && el("span.dlg-quest", { text: s.questName }),
        el("span.dlg-title", { text: s.title }),
        el("span.dlg-meta", { text: tf(dn === 1 ? "dlg.decisions1" : "dlg.decisionsN", dn === 1 ? "{n} decision" : "{n} decisions", { n: dn }) }));
      head.addEventListener("click", () => row.classList.toggle("open"));
      row.appendChild(head);

      const body = el("div.dlg-body");
      const rendered = new Set();
      s.decisions.forEach((dec, di) => {        // roots first; chains nest under them
        if (!dec.is_child) body.appendChild(renderDecision(di, s, false, new Set([di]), rendered));
      });
      s.decisions.forEach((dec, di) => {        // any child never reached from a root
        if (!rendered.has(di)) body.appendChild(renderDecision(di, s, false, new Set([di]), rendered));
      });
      if (s.script && s.script.length) {
        // omit choice-consequence rules — they're already shown under the options above;
        // what remains is the stage machinery not driven by player choices. EXCEPT rules that
        // award a mastery: the option above only shows a "★ Grants" chip, not the grant action,
        // so keep those here so the grant is findable in the script (esp. when a buggy gating
        // value files the reward rule under the wrong option, e.g. Sky-wind park's Irene reward).
        const grantRule = r => (r.do || []).some(it => (it.text || "").startsWith("grant mastery: "));
        const trig = new Set();
        s.decisions.forEach(dec => dec.options.forEach(o => (o.triggers || []).forEach(i => trig.add(i))));
        const kept = s.script.filter((r, i) => !trig.has(i) || grantRule(r));
        const omitted = s.script.length - kept.length;
        const sec = el("div.script-section");
        const tog = el("div.script-toggle", { text: `Full script (${kept.length} rule${kept.length === 1 ? "" : "s"}`
          + (omitted ? `; ${omitted} choice-consequence rule${omitted === 1 ? "" : "s"} shown above omitted` : "")
          + ")" });
        tog.addEventListener("click", () => sec.classList.toggle("open"));
        const sb = el("div.script-body");
        renderScriptInto(sb, kept);
        sec.append(tog, sb);
        body.appendChild(sec);
      }
      row.appendChild(body);
      frag.appendChild(row);
    }
    els.dialogue.replaceChildren(frag);
    if (!list.length) els.dialogue.innerHTML = `<div class="empty">${t("empty.dialogues", "No dialogue stages match.")}</div>`;
  }

  // ---- board builder ----
  const BOARD_CATS = ["Basic", "Attack", "Ability", "Support", "Defence"];
  const jobById = {}; (DATA.jobs || []).forEach(j => { jobById[j.id] = j; });
  const pcById = {}; (DATA.pcs || []).forEach(p => { pcById[p.id] = p; });
  // a build's "character" is either a Troubleshooter (PC) or a captured-beast *form*. Beasts
  // reuse the PC engine: bld.pcId = the form id (the unit — element/baseMax), bld.jobId = the
  // family job (e.g. "Tima" — the per-category slot counts). unitById resolves either kind.
  const beastById = {}; (DATA.beasts || []).forEach(b => { beastById[b.id] = b; });
  const unitById = Object.assign({}, pcById, beastById);
  const beastFamById = {}; (DATA.beastFamilies || []).forEach(f => { beastFamById[f.id] = f; });
  const beastsByFamily = {};
  (DATA.beasts || []).forEach(b => (beastsByFamily[b.family] = beastsByFamily[b.family] || []).push(b));
  const beastBaseForm = fam => (beastsByFamily[fam] || []).find(b => b.stage === 1) || (beastsByFamily[fam] || [])[0];
  const isBeast = u => !!(u && u.race === "Beast");
  // ---- drones (Machine): a unit = Frame (slots) × SP (element); OS picks the reinforcement pool.
  // The board's 5 columns are the *module* categories, each mapped to a standard slot category. ----
  const MACH = DATA.machine || {};
  const DRONE_CRAFT_LV = 40;                            // engine-set craft level (progression-scaled); see pc-change
  const droneById = {}; (MACH.units || []).forEach(u => { droneById[u.id] = u; });
  Object.assign(unitById, droneById);                 // drones resolve through unitById too
  const droneByFrameSp = {}; (MACH.units || []).forEach(u => { droneByFrameSp[u.frame + "/" + u.sp] = u; });
  const isDrone = u => !!(u && u.race === "Machine");
  const MODULE_SLOT = {}; (MACH.moduleCats || []).forEach(c => { MODULE_SLOT[c.nameEn] = c.slot; });   // module cat (English) -> slot
  // a mastery's board slot category: its own category for PC/beast, the mapped slot for a drone module.
  // Keyed on the language-independent English category (categoryRaw), since BOARD_CATS / module-cat
  // keys are English engine names.
  const slotOf = m => m && (MODULE_SLOT[m.categoryRaw] || (BOARD_CATS.includes(m.categoryRaw) ? m.categoryRaw : null));
  // board columns for the current unit: standard 5 for PC/beast, the 5 module categories for a drone.
  // cat = English key (matches categoryRaw for sidebar filtering); label = localized display name.
  const boardColumns = unit => isDrone(unit)
    ? (MACH.moduleCats || []).map(c => ({ cat: c.nameEn, slot: c.slot, label: c.name }))
    : BOARD_CATS.map(c => ({ cat: c, slot: c, label: c }));
  // beast evolution-mastery picks (1-of-3 per evolution level). The pool is global by Type
  // (Training/Nature/Gene) + the beast's own element, plus that form's species-unique `fixedEvo`.
  const beastEvoById = {}; (DATA.beastEvo || []).forEach(m => { beastEvoById[m.id] = m; });
  const BEAST_GLOBAL_TYPES = new Set(["Training", "Nature", "Gene"]);
  function beastEvoPool(u) {
    if (!isBeast(u)) return [];
    const out = [];
    (DATA.beastEvo || []).forEach(m => {
      if (!m.unique && (BEAST_GLOBAL_TYPES.has(m.type) || m.type === u.element)) out.push(m.id);
    });
    (u.fixedEvo || []).forEach(id => { if (beastEvoById[id] && !out.includes(id)) out.push(id); });
    return out;
  }
  // `evo` = chosen pick id per evolution level (index 0 = level 1); empty string = no pick yet.
  // drones add `os` (reinforcement-pool selector) and `reinf` (reinforcement stage 1..4)
  const bld = { id: null, pcId: null, jobId: null, level: 1, placed: {}, evo: [], os: null, reinf: 1, craft: null, sideTab: "masteries", q: "", catFilter: null };
  // which "Missing N" set-panel sections are expanded — remembered for the session (so adding a
  // set from an expanded group doesn't re-collapse it); resets to just "Missing 1" on reload.
  const bldSetFolds = new Set([1]);
  const bldEls = {
    bar: $("#builder-bar"), pcCsel: $("#bld-pc-csel"),
    jobLabelText: $("#bld-job-labeltext"), jobCsel: $("#bld-job-csel"),
    osLabel: $("#bld-os-label"), osCsel: $("#bld-os-csel"),
    reinfLabel: $("#bld-reinf-label"), reinfCsel: $("#bld-reinf-csel"),
    level: $("#bld-level"), summary: $("#bld-summary"),
    broken: $("#builder-broken"), board: $("#builder-board"), side: $("#builder-side"),
    importBtn: $("#bld-import"), exportBtn: $("#bld-export"), exportAllBtn: $("#bld-exportall"), io: $("#bld-io"),
    undoBtn: $("#bld-undo"), redoBtn: $("#bld-redo"),
    listCsel: $("#bld-list-csel"), newBtn: $("#bld-new"), dupBtn: $("#bld-dup"),
    renameBtn: $("#bld-rename"), deleteBtn: $("#bld-delete"),
  };
  bld.io = null;   // open import/export panel: null | "import" | "export"

  // accessible mastery Types for the current character + class: universal, the character's
  // personal type, race & innate element, and the job tree.
  // pc/job default to the active build; pass them explicitly to compute access for another build
  // (e.g. validating an incoming share code without touching the global bld state). Only an *omitted*
  // arg defaults to global — passing an explicit pc with no/undefined job means "this unit, no class"
  // (so we never leak the active build's job into another unit's type set).
  function bldAccessTypes(pc, job) {
    if (pc === undefined) { pc = unitById[bld.pcId]; if (job === undefined) job = jobById[bld.jobId]; }
    const s = new Set(["Common", "All", "Normal"]);
    if (pc) {
      if (pc.pcType) s.add(pc.pcType); if (pc.race) s.add(pc.race);
      if (pc.element) s.add(pc.element);              // raw MasteryType id (e.g. drone SP "Heat")
      // Control/Reinforcement Program modules (typeRaw Application_Control/_Enhancement) aren't
      // gated by the drone's SP or a job — they're board-placeable on every drone. Expose them so
      // bldAccessible lets those module-group masteries (and their sets) through.
      if (pc.race === "Machine") { s.add("Application_Control"); s.add("Application_Enhancement"); }
      // Mungo Mimic: each form grants a fixed class tree by its equipped weapon (the adolescent's
      // battle glove → Fighter; each evolved form its own weapon's class).
      if (pc.mimicAccess) pc.mimicAccess.forEach(t => s.add(t));
    }
    if (job) (job.accessTypes || []).forEach(t => s.add(t));
    return s;
  }
  // accessibility is matched on the raw MasteryType id (m.typeRaw), not the display name.
  // drones place modules only — their access set also matches plain Common/Normal masteries,
  // so restrict to the module group (regular masteries aren't valid on a drone board).
  // pc defaults to the active build's unit; pass it to check access for another unit.
  function bldAccessible(m, types, pc) {
    pc = pc || unitById[bld.pcId];
    if (!m || !slotOf(m) || !types.has(m.typeRaw)) return false;
    if (isDrone(pc) && m.group !== "module") return false;
    return true;
  }

  // the mastery sets fully assembled on the board — every component in `placedIds` (the set of
  // user-placed mastery ids). A componentless set never counts. Single source of truth for
  // set completion, shared by the limit bonuses, the set panel, and the board's active-set marker.
  const completedSets = placedIds =>
    DATA.sets.filter(s => s.components.length && s.components.every(c => placedIds.has(c.id)));

  // sum the limit bonuses from every placed mastery (shared_pc.lua Get_ExtraMax*/Get_Max*Cost):
  // slot bonuses per category (+ "all"), a cost-cap bonus applied to every category, and total TP.
  // A completed mastery set also grants its set-bonus mastery (Category=="Set", id == set id),
  // several of which raise limits (Egoist/Keyboard Warrior/The Seeker → +cost all; Social Life
  // → +Ability slots & total) — applied here so completing a set bumps the limits in-game-style.
  function bldBonuses() {
    const b = { slot: { Basic: 0, Attack: 0, Ability: 0, Support: 0, Defence: 0 }, costAll: 0, total: 0 };
    const mods = DATA.boardMods || {};
    const apply = effs => (effs || []).forEach(e => {
      if (e.kind === "slot") {
        if (e.cat === "all") BOARD_CATS.forEach(c => b.slot[c] += e.amt);
        else b.slot[e.cat] += e.amt;
      } else if (e.kind === "cost") b.costAll += e.amt;
      else if (e.kind === "total") b.total += e.amt;
    });
    BOARD_CATS.forEach(cat => (bld.placed[cat] || []).forEach(id => apply(mods[id])));
    (bld.evo || []).forEach(id => { if (id) apply(mods[id]); });   // beast evo / drone AI-upgrade picks
    if (bld.craft) apply(mods[bld.craft]);                          // drone construction craft-unique pick
    const placedIds = new Set(BOARD_CATS.flatMap(c => bld.placed[c] || []));
    completedSets(placedIds).forEach(s => { if (mods[s.id]) apply(mods[s.id]); });   // completed-set bonus masteries
    return b;
  }

  // full computed limits for the current character/class/level (shared_pc.lua formulas).
  // natural slots (PC base + job Max + element ESP) unlock progressively by the
  // MasteryUnlockLevel schedule; mastery-bonus slots are always on top of that.
  //   slots   = full capacity (natural + bonus)            — drives the cost cap
  //   unlocked= level-gated natural + bonus                — fillable right now
  //   locked  = unlock levels for the not-yet-open natural slots
  //   cost    = 2*slots - 1 + placed cost bonuses;  total = level + placed total bonuses.
  function bldLimits() {
    const pc = unitById[bld.pcId], job = jobById[bld.jobId];
    const elem = (pc && pc.element && (DATA.espSlots || {})[pc.element]) || {};
    const bonus = bldBonuses();
    const sched = DATA.slotUnlock || {};
    // inherent fixed masteries: each brings a dedicated (always-on) slot and -2 cost cap to
    // its category — net-neutral on cost, +1 free slot (shared_pc.lua FixedMasteryTest…).
    const fixSlot = { Basic: 0, Attack: 0, Ability: 0, Support: 0, Defence: 0 }, fixCost = { ...fixSlot };
    (pc && pc.fixed || []).forEach(f => { if (BOARD_CATS.includes(f.cat)) { fixSlot[f.cat]++; fixCost[f.cat] -= 2; } });
    const cats = {};
    BOARD_CATS.forEach(c => {
      const natural = (pc ? pc.baseMax[c] || 0 : 0) + (job ? job.max[c] || 0 : 0) + (elem[c] || 0);
      const alwaysOn = bonus.slot[c] + fixSlot[c];                     // not level-gated
      const slots = natural + alwaysOn;                                // full capacity
      const levels = sched[c] || [];
      const openNatural = Math.min(levels.filter(lv => lv <= bld.level).length, natural);
      cats[c] = {
        slots,
        unlocked: openNatural + alwaysOn,
        locked: levels.slice(openNatural, natural),                    // unlock level per locked slot
        cost: (slots > 0 ? 2 * slots - 1 : 0) + bonus.costAll + fixCost[c],
      };
    });
    return { cats, total: bld.level + bonus.total };
  }
  function bldPlace(id) {
    const m = masteryById[id];
    const slot = slotOf(m);
    if (!slot) return;
    const arr = bld.placed[slot] || (bld.placed[slot] = []);
    if (!arr.includes(id)) arr.push(id);
  }

  // ---- saved builds (multiple, named) — localStorage, file://-safe, namespaced ----
  // Store: { active:<id>, builds:[{id,name,pcId,jobId,level,placed}, …] }. The *active*
  // build autosaves continuously, so switching never loses work. Silently no-ops if storage
  // is unavailable (e.g. Safari on file://).
  const BLD_KEY = "tsbuilder:builds";
  let bldStore = { active: null, builds: [] };
  const bldUid = () => "b" + Math.random().toString(36).slice(2, 9);
  // the persisted per-build state (everything but id/name and transient UI). This is the single list
  // of build fields: add one here and bldBlank/bldSave/bldDuplicate pick it up automatically. Mutable
  // fields are cloned so two builds (or the live vs stored copy) never share state.
  function bldStateOf(src) {
    src = src || {};
    return { pcId: src.pcId || null, jobId: src.jobId || null, level: src.level || 1,
             placed: JSON.parse(JSON.stringify(src.placed || {})), evo: (src.evo || []).slice(),
             os: src.os || null, reinf: src.reinf || 1, craft: src.craft || null };
  }
  const bldBlank = name => ({ id: bldUid(), name, ...bldStateOf() });
  function bldStorePersist() {
    try { localStorage.setItem(BLD_KEY, JSON.stringify(bldStore)); } catch (e) { /* blocked/full */ }
  }
  // write the working build (`bld`) back into its slot, then persist
  function bldSave() {
    const b = bldStore.builds.find(x => x.id === bld.id);
    if (b) Object.assign(b, bldStateOf(bld));
    bldStorePersist();
    bldTrack();                                        // fold any state change into the undo history
  }

  // ---- undo / redo (session-only, a separate stack per build id) ----
  // Snapshot model: bldSave() is the universal persistence choke point, so every real edit funnels
  // through bldTrack() below. It diffs the serialized build state against the last one it recorded;
  // only an actual change pushes the *previous* snapshot onto that build's undo stack — so the many
  // redundant renderBuilder()/bldSave() calls (side-tab switches, category filters, export flushes)
  // add nothing. Stacks are keyed by bld.id, so switching builds is navigation and never recorded.
  const BLD_UNDO_MAX = 100;
  const bldUndo = {}, bldRedo = {};   // id → array of serialized bldStateOf() snapshots
  const bldSnap = {};                 // id → last snapshot bldTrack() recorded (change detector)
  let bldRestoring = false;           // true while applying an undo/redo (suppresses re-recording)
  function bldTrack() {
    const id = bld.id;
    if (!id) return;
    const cur = JSON.stringify(bldStateOf(bld));
    const prev = bldSnap[id];
    if (prev === undefined) { bldSnap[id] = cur; return; }   // seed on first activation — nothing to record
    if (cur === prev) return;                                // no change
    bldSnap[id] = cur;
    if (bldRestoring) return;                                // the change *is* an undo/redo — don't re-record it
    const stack = bldUndo[id] || (bldUndo[id] = []);
    stack.push(prev);
    if (stack.length > BLD_UNDO_MAX) stack.shift();
    bldRedo[id] = [];                                        // a fresh edit invalidates the redo branch
  }
  // apply a serialized snapshot onto the live build (id/transient fields untouched)
  function bldApplySnap(json) {
    Object.assign(bld, bldStateOf(JSON.parse(json)));
    bld.catFilter = null;             // a mastery the filter pointed at may no longer be placed
  }
  // move one step between stacks: from `undo` (or `redo`) onto the opposite stack, then apply + render
  function bldStep(from, to) {
    const id = bld.id;
    const stack = from[id];
    if (!stack || !stack.length) return;
    (to[id] || (to[id] = [])).push(JSON.stringify(bldStateOf(bld)));  // current state → opposite branch
    const snap = stack.pop();
    bldRestoring = true;
    bldApplySnap(snap);
    renderBuilder();                  // its trailing bldSave/bldTrack records the restore as the new snapshot
    bldRestoring = false;
  }
  const bldUndoStep = () => bldStep(bldUndo, bldRedo);
  const bldRedoStep = () => bldStep(bldRedo, bldUndo);
  function bldSyncUndoBtns() {
    if (bldEls.undoBtn) bldEls.undoBtn.disabled = !(bldUndo[bld.id] || []).length;
    if (bldEls.redoBtn) bldEls.redoBtn.disabled = !(bldRedo[bld.id] || []).length;
  }
  // normalize a stored/raw build's variable fields against current data (pure — shared by bldUse and
  // bldCanonCode, so a freshly-imported build and an already-stored one canonicalize identically)
  function bldNormalized(b) {
    const pc = unitById[b.pcId];
    const out = { pcId: pc ? b.pcId : null, level: Math.max(1, +b.level || 1),
      jobId: pc ? (pc.jobs.includes(b.jobId) ? b.jobId : pc.jobs[0]) : null,
      placed: {}, os: null, reinf: Math.min(4, Math.max(1, +b.reinf || 1)), craft: null, evo: [] };
    // gate placed masteries the same way as the sidebar/interactive switch: they must still exist,
    // still map to their column, AND be accessible to the current form/class (bldAccessTypes). This
    // is the single enforcement point every load/import/canon path shares, so a build stored with a
    // now-incompatible mastery (e.g. from an older data set or a share code) heals on load.
    const ntypes = pc ? bldAccessTypes(pc, jobById[out.jobId]) : null;
    BOARD_CATS.forEach(c => {                           // drop anything that no longer exists / fits / isn't accessible
      const arr = (b.placed && b.placed[c] || []).filter(id => {
        const m = masteryById[id];
        return m && slotOf(m) === c && (!ntypes || bldAccessible(m, ntypes, pc));
      });
      if (arr.length) out.placed[c] = arr;
    });
    out.os = (isDrone(pc) && (MACH.os || []).some(o => o.id === b.os)) ? b.os : (isDrone(pc) ? (MACH.os[0] || {}).id : null);
    out.craft = (isDrone(pc) && (MACH.craft || []).includes(b.craft)) ? b.craft : null;   // construction pick
    // keep only the per-stage picks valid for this unit: a beast's evolution pool, or (for a drone)
    // its OS AI-upgrade pool, trimmed to the picks the current reinforcement stage actually unlocks.
    const pool = new Set(isDrone(pc) ? ((MACH.aiUpgrade || {})[out.os] || []).map(u => u.id) : beastEvoPool(pc));
    out.evo = (Array.isArray(b.evo) ? b.evo : []).map(id => (id && pool.has(id)) ? id : "");
    if (isDrone(pc)) out.evo = out.evo.slice(0, Math.max(0, out.reinf - 1));
    return out;
  }
  // load a stored build's fields into the working state (validated against current data)
  function bldUse(b) {
    bld.id = b.id;
    Object.assign(bld, bldNormalized(b));
    bld.catFilter = null;
  }
  function bldLoad() {                                 // init: restore the store + active build
    let s;
    try { s = JSON.parse(localStorage.getItem(BLD_KEY) || "null"); } catch (e) { s = null; }
    if (!s || !Array.isArray(s.builds) || !s.builds.length) {
      s = { active: null, builds: [bldBlank(t("bld.defaultName", "My build"))] };
    }
    bldStore = s;
    const act = bldStore.builds.find(x => x.id === bldStore.active) || bldStore.builds[0];
    bldStore.active = act.id;
    bldUse(act);
    bldStorePersist();
  }
  // populate the Builds dropdown (called from renderBuilder)
  function bldRenderList() {
    if (!bldEls.listCsel) return;
    const jobTitle = id => (jobById[id] && jobById[id].title) || "";
    const label = b => {
      const u = unitById[b.pcId];
      // PCs show "Name · Class"; beast forms already read like "Black Tima", so just the form name
      const who = u ? (pcById[b.pcId] && jobTitle(b.jobId) ? u.name + " · " + jobTitle(b.jobId) : u.name)
                    : "(no character)";
      return `${who} — ${b.name}`;
    };
    // sort by the displayed label so the dropdown reads WYSIWYG (drone/machine builds resolve a
    // character via unitById, so they interleave alphabetically instead of being lumped with the
    // empties). `numeric` gives natural order (Build 2 < Build 10); character-less builds sink last.
    const sorted = [...bldStore.builds].sort((a, b) => {
      const ea = !unitById[a.pcId], eb = !unitById[b.pcId];
      if (ea !== eb) return ea ? 1 : -1;
      return label(a).localeCompare(label(b), undefined, { numeric: true, sensitivity: "base" });
    });
    const labelById = {}; sorted.forEach(b => { labelById[b.id] = label(b); });
    bldEls.listCsel.replaceChildren();
    bldEls.listCsel.appendChild(makeCsel(bld.id, [{ label: "", ids: sorted.map(b => b.id) }],
      id => bldSwitch(id), { labelOf: id => labelById[id] || id, cardOf: () => null, noNone: true }));
  }
  function bldActivate(b) { bldStore.active = b.id; bldUse(b); bldStorePersist(); renderBuilder(); }
  function bldSwitch(id) {
    if (id === bld.id) return;
    bldSave();                                         // persist current before leaving
    const b = bldStore.builds.find(x => x.id === id);
    if (b) bldActivate(b);
  }
  function bldNew() {
    bldSave();
    const b = bldBlank(tf("bld.newName", "Build {n}", { n: bldStore.builds.length + 1 }));
    bldStore.builds.push(b); bldActivate(b);
  }
  function bldDuplicate() {
    bldSave();
    const cur = bldStore.builds.find(x => x.id === bld.id);
    const b = bldBlank((cur ? cur.name : t("bld.buildWord", "Build")) + t("bld.copySuffix", " copy"));
    Object.assign(b, bldStateOf(bld));
    bldStore.builds.push(b); bldActivate(b);
  }
  function bldRename() {
    const cur = bldStore.builds.find(x => x.id === bld.id);
    if (!cur) return;
    const name = prompt(t("bld.renamePrompt", "Build name:"), cur.name);
    if (name == null) return;
    cur.name = name.trim() || cur.name;
    bldStorePersist(); renderBuilder();
  }
  function bldDelete() {
    const cur = bldStore.builds.find(x => x.id === bld.id);
    if (!cur || !confirm(tf("bld.deleteConfirm", 'Delete build "{name}"?', { name: cur.name }))) return;
    delete bldUndo[cur.id]; delete bldRedo[cur.id]; delete bldSnap[cur.id];   // drop its undo history
    bldStore.builds = bldStore.builds.filter(x => x.id !== cur.id);
    if (!bldStore.builds.length) bldStore.builds.push(bldBlank(t("bld.defaultName", "My build")));
    bldActivate(bldStore.builds[0]);
  }

  // ---- shareable links (#build=<code>&name=<…>&<picks>) ----
  // The build state the in-game share CODE can't carry, as compact link params (mastery ids; reinf a
  // small int). Roster-gated: a beast carries its evolution-mastery picks; a drone its OS / reinforce /
  // craft / AI-upgrade picks. So a link of ours restores the FULL build, while the bare code still
  // imports into the game. (Evolution/craft masteries have no MasteryCode entry — that's exactly why
  // they're not in the code — so we key them by stable mastery id, not by a codemap code.)
  function bldPicks(b) {
    const pc = unitById[b.pcId]; if (!pc) return {};
    const p = {}, evo = (b.evo || []).map(x => x || "");
    while (evo.length && !evo[evo.length - 1]) evo.pop();   // trim trailing blanks (keep interior order)
    if (isDrone(pc)) {
      if (b.os) p.os = b.os;
      if (+b.reinf && +b.reinf !== 1) p.reinf = b.reinf;
      if (b.craft) p.craft = b.craft;
      if (evo.length) p.evo = evo.join(",");
    } else if (isBeast(pc)) {
      if (evo.length) p.evo = evo.join(",");
    }
    return p;
  }
  // restore link-carried picks onto a fresh build, roster-gated (bldUse re-validates on activation)
  function bldApplyPicks(b, picks) {
    if (!picks) return;
    const pc = unitById[b.pcId]; if (!pc) return;
    const evo = () => String(picks.evo || "").split(",").map(s => s.trim());
    if (isDrone(pc)) {
      if (picks.os) b.os = picks.os;
      if (picks.reinf != null) b.reinf = Math.min(4, Math.max(1, +picks.reinf || 1));
      if (picks.craft) b.craft = picks.craft;
      if (picks.evo != null) b.evo = evo();
    } else if (isBeast(pc)) {
      if (picks.evo != null) b.evo = evo();
    }
  }
  // pull the pick params (os/reinf/craft/evo) out of a URLSearchParams into a plain object
  function bldPicksFromParams(params) {
    const p = {};
    ["os", "reinf", "craft", "evo"].forEach(k => { if (params.has(k)) p[k] = params.get(k); });
    return p;
  }
  // canonical key for a stored build — board share code plus the picks the code can't carry, so two
  // builds differing only in a pick aren't treated as identical. Normalized first so an imported build
  // and an already-stored one key the same.
  function bldCanonCode(b) {
    const n = bldNormalized(b);
    if (!n.pcId) return null;
    const code = encodeShareCode(n.pcId, n.jobId, n.level, BOARD_CATS.flatMap(c => n.placed[c] || []));
    if (!code) return null;
    const p = bldPicks(n), keys = Object.keys(p).sort();
    return keys.length ? code + "|" + keys.map(k => k + "=" + p[k]).join("&") : code;
  }
  // the #-fragment that fully restores a build: board code + name + the un-encoded picks
  function bldShareFragment(b, code) {
    const params = new URLSearchParams();
    params.set("build", code);
    if (b.name) params.set("name", b.name);
    const p = bldPicks(b); Object.keys(p).forEach(k => params.set(k, p[k]));
    return params.toString();
  }
  // full share URL for a build (works under file:// where location.origin is "null")
  function bldShareLink(b, code) {
    const base = (location.origin && location.origin !== "null")
      ? location.origin + location.pathname : location.href.split("#")[0];
    return base + "#" + bldShareFragment(b, code);
  }
  // Decode a share code (+ optional link picks) into a build, with a sanity gate (a random base32-ish
  // string can base32-decode to a few stray masteries — see TODO "Stricter import validation"). Returns
  //   null                          — not a coherent board (bad roster/char/level, nothing recognized,
  //                                    a mastery that isn't placeable on the unit, or mostly-unknown)
  //   { build, known, unknown }     — a valid board; `unknown` > 0 means some group entries didn't map to
  //                                    a mastery this tool knows (e.g. a newer game version) and were
  //                                    dropped — the caller can offer a partial import.
  function bldAnalyzeShareCode(code, name, picks) {
    const dec = decodeShareCode(code);
    if (!dec) return null;
    if (![1, 2, 3].includes(dec.rosterType)) return null;   // roster must be PC(1) / Beast(2) / Machine(3)
    if (!(dec.level >= 1 && dec.level <= 99)) return null;   // plausible level
    if (!dec.keyCount || !dec.ids.length) return null;       // no mastery groups / none recognized
    const pcId = (dec.pcType && unitById[dec.pcType]) ? dec.pcType : null;
    if (!pcId) return null;                              // unknown / unresolvable char id
    const pc = unitById[pcId];
    // the roster tag must match the resolved unit's kind (guards a charId that maps in the wrong table)
    const kindOk = dec.rosterType === 2 ? isBeast(pc) : dec.rosterType === 3 ? isDrone(pc) : !!pcById[pcId];
    if (!kindOk) return null;
    const jobId = (dec.jobName && pc.jobs.includes(dec.jobName)) ? dec.jobName : pc.jobs[0];
    // Every *recognized* mastery must be placeable on this unit. This is only a tolerant plausibility
    // filter (is the code garbage, or a real build for this character?) — it checks against the whole
    // type universe (union over all the character's classes) so a code isn't rejected outright over one
    // cross-class mastery. Strict per-mastery eligibility for the *current* class is enforced later, in
    // bldNormalized (which every import flows through via bldUse/bldCanonCode), pruning what doesn't fit.
    const types = new Set();
    const jobIds = (pc.jobs || []).filter(jid => jobById[jid]);
    if (jobIds.length) jobIds.forEach(jid => bldAccessTypes(pc, jobById[jid]).forEach(t => types.add(t)));
    else bldAccessTypes(pc).forEach(t => types.add(t));      // no resolvable class → unit-only types
    if (!dec.ids.every(id => bldAccessible(masteryById[id], types, pc))) return null;
    // some entries didn't map. A *few* unknowns read as a version gap (offer a partial import); mostly-
    // unknown reads as garbage that happened to land one valid mastery — reject it.
    const unknown = dec.keyCount - dec.ids.length;
    if (unknown > dec.ids.length) return null;
    const placed = {};
    dec.ids.forEach(id => {
      const m = masteryById[id];
      if (slotOf(m)) (placed[slotOf(m)] = placed[slotOf(m)] || []).push(id);
    });
    const b = Object.assign(bldBlank(name || tf("bld.sharedName", "{name} (shared)", { name: pc.name })), {
      pcId, jobId, level: dec.level, placed });
    bldApplyPicks(b, picks);
    return { build: b, known: dec.ids.length, unknown };
  }
  // strict decode → build, or null: rejects a partial (any unknown mastery). Used by the paths that
  // can't prompt (backup merge, #build= link); the interactive Import offers the partial instead.
  function bldFromShareCode(code, name, picks) {
    const r = bldAnalyzeShareCode(code, name, picks);
    return r && !r.unknown ? r.build : null;
  }
  // process a #build=<code> share link: switch to a matching loaded build or add it as a new
  // one, switch to the builder tab, then strip the hash so F5 won't re-trigger
  function bldLoadFromHash() {
    const hash = location.hash.slice(1);
    if (!/(?:^|&)build=/.test(hash)) return;
    const params = new URLSearchParams(hash);
    const code = (params.get("build") || "").trim(), name = params.get("name");
    history.replaceState(null, "", location.pathname + location.search);   // clear the hash
    const incoming = bldFromShareCode(code, name, bldPicksFromParams(params));
    if (!incoming) return;                               // invalid / unknown unit — ignore
    const target = bldCanonCode(incoming);
    const existing = bldStore.builds.find(b => bldCanonCode(b) === target);
    if (existing) { bldStore.active = existing.id; bldUse(existing); }
    else { bldStore.builds.push(incoming); bldStore.active = incoming.id; bldUse(incoming); }
    bldStorePersist();
    selectTab(document.querySelector('.tab[data-view="builder"]'));
    render();
  }

  // ---- export all / import (our own backup format; import also accepts one share code/link) ----
  // back up the whole collection as { tsbuilder, builds:[#-fragment], starred:[id] }. Each build is a
  // full-fidelity share fragment (board code + name + picks), so importing one restores it completely.
  function bldExportAll() {
    bldSave();                                          // flush the active build's live edits first
    const builds = [];
    bldStore.builds.forEach(b => {
      const n = bldNormalized(b);
      if (!n.pcId) return;                              // skip empty (character-less) builds
      const code = encodeShareCode(n.pcId, n.jobId, n.level, BOARD_CATS.flatMap(c => n.placed[c] || []));
      if (code) builds.push(bldShareFragment({ ...n, name: b.name }, code));
    });
    return JSON.stringify({ tsbuilder: 1, builds, starred: [...starred] }, null, 2);
  }
  // merge a full backup: append builds whose canonical key isn't already present, union the starred set
  function bldImportAll(data) {
    bldSave();
    const have = new Set(bldStore.builds.map(b => bldCanonCode(b)).filter(Boolean));
    let added = 0, dup = 0;
    (Array.isArray(data.builds) ? data.builds : []).forEach(frag => {
      const params = new URLSearchParams(frag);
      const code = (params.get("build") || "").trim(); if (!code) return;
      const b = bldFromShareCode(code, params.get("name"), bldPicksFromParams(params));
      if (!b) return;
      const canon = bldCanonCode(b);
      if (canon && have.has(canon)) { dup++; return; }
      if (canon) have.add(canon);
      bldStore.builds.push(b); added++;
    });
    let stars = 0;
    (Array.isArray(data.starred) ? data.starred : []).forEach(id => {
      if (masteryById[id] && !starred.has(id)) { starred.add(id); stars++; }
    });
    if (stars) saveStarred();
    bldStorePersist();
    return { added, dup, stars };
  }
  // add one decoded share-code/link build as a new build and switch to it
  function bldImportOne(b) {
    bldSave();
    bldStore.builds.push(b);
    bldActivate(b);                                     // selects + validates + renders
  }

  // --- in-game mastery board SHARE CODE decoder (base32 + period-5 unscramble) ---
  const SC_ALPH = "23456789ABCDEFGHIJKMNPQRSTUVWXYZ";
  const SC_VAL = {}; for (let i = 0; i < SC_ALPH.length; i++) SC_VAL[SC_ALPH[i]] = i;
  function scBits(code) {
    const b = [];
    for (const ch of code) { const v = SC_VAL[ch]; if (v === undefined) return null; for (let i = 4; i >= 0; i--) b.push((v >> i) & 1); }
    return b;
  }
  function scUnscramble(s) {                 // invert [a,b,c,d,e] -> [a, b^c, ~c, d, e]
    const r = s.slice();
    for (let k = 0; k < s.length; k += 5) {
      if (k + 2 < s.length) r[k + 2] = 1 ^ s[k + 2];
      if (k + 1 < s.length) r[k + 1] = s[k + 1] ^ (k + 2 < s.length ? r[k + 2] : 0);
    }
    return r;
  }
  function scScramble(raw) {                 // forward [a,b,c,d,e] -> [a, b^c, ~c, d, e]
    const s = raw.slice();
    for (let k = 0; k < raw.length; k += 5) {
      if (k + 2 < raw.length) s[k + 2] = 1 ^ raw[k + 2];
      if (k + 1 < raw.length) s[k + 1] = raw[k + 1] ^ (k + 2 < raw.length ? raw[k + 2] : 0);
    }
    return s;
  }
  function scInt(b, p, n) { let v = 0; for (let i = 0; i < n; i++) v = (v << 1) | b[p + i]; return v; }
  function scVarint(raw, p) {
    let byte = scInt(raw, p, 8); p += 8; let v = byte & 0x7f;
    if (byte & 0x80) { const hi = scInt(raw, p, 8); p += 8; v |= (hi & 0x7f) << 7; }
    return [v, p];
  }
  function scPut(arr, val, w) { for (let i = 0; i < w; i++) arr.push((val >> (w - 1 - i)) & 1); }
  // Decode a board share code -> { ids, level, rosterType, pcType, jobName }, or null.
  // Whole code is one period-5 scrambled stream (groups aligned so bit49 is a boundary);
  // un-scramble from bit 4 -> plain header then mastery groups. Header: rosterType (1=PC/2=Beast/
  // 3=Machine) @16, charId @20 (8 bits — the Pc/Beast/Machine code; PCs only fill the low nibble),
  // level @29, jobId @37 (PC job, or for a beast its family job). `pcType` is the resolved unit id.
  function decodeShareCode(text) {
    const code = text.trim().toUpperCase().replace(/[^0-9A-Z]/g, "");
    const bits = scBits(code);
    if (!bits || bits.length < 64) return null;
    const raw = scUnscramble(bits.slice(4));         // raw[i] == absolute bit (i+4)
    const rosterType = scInt(raw, 16, 4);
    const charId = scInt(raw, 20, 8), level = scInt(raw, 29, 7), jobId = scInt(raw, 37, 7);
    const keys = []; let p = 45, first = true;       // mastery list starts at raw[45] (bit 49)
    while (p + 7 <= raw.length) {
      if (!first) p += 1;
      const t = scInt(raw, p, 7);
      if (t < 1 || t > 85) break;
      p += 7; if (p + 8 > raw.length) break;
      const n = scInt(raw, p, 8); p += 8;
      for (let i = 0; i < n; i++) { if (p + 8 > raw.length) break; let v; [v, p] = scVarint(raw, p); keys.push(t + ":" + v); }
      first = false;
    }
    const CM = window.TS_CODEMAP || {}, JM = window.TS_JOB || {};
    const unit = (rosterType === 2 ? window.TS_BEAST : rosterType === 3 ? window.TS_MACHINE : window.TS_PC) || {};
    const ids = keys.map(k => CM[k]).filter(Boolean);
    // keyCount vs ids.length lets the import gate reject a stream whose "masteries" don't all map
    return { ids, keyCount: keys.length, level, rosterType, pcType: unit[charId] || null, jobName: JM[jobId] || null };
  }
  // Build a share code from the current build (inverse of decodeShareCode). `unitId` is a PC id
  // or a beast form id; the header template & non-roster bits are shared across roster types.
  function encodeShareCode(unitId, jobId, level, placedIds) {
    const tmpl = scBits(window.TS_PCTEMPLATE || "KSAACAJQEE");
    if (!tmpl) return null;
    const raw = scUnscramble(tmpl.slice(4)).slice(0, 45);   // constant header template
    const set = (o, w, v) => { for (let i = 0; i < w; i++) raw[o + i] = (v >> (w - 1 - i)) & 1; };
    const BI = window.TS_BEASTINV || {}, KI = window.TS_MACHINEINV || {};
    let roster = 1, charId = (window.TS_PCINV || {})[unitId] | 0;             // rosterType + 8-bit code
    if (unitId in BI) { roster = 2; charId = BI[unitId]; }                    // beast form
    else if (unitId in KI) { roster = 3; charId = KI[unitId]; }              // drone frame
    set(16, 4, roster);
    set(20, 8, charId | 0);
    set(29, 7, Math.max(1, level | 0));
    set(37, 7, (window.TS_JOBINV || {})[jobId] | 0);
    // gather (type#, code) from placed mastery ids, group by type#
    const MI = window.TS_MASTINV || {}, groups = {};
    placedIds.forEach(id => {
      const tc = MI[id]; if (!tc) return;
      const [t, c] = tc.split(":").map(Number);
      (groups[t] = groups[t] || []).push(c);
    });
    let first = true;
    Object.keys(groups).map(Number).sort((a, b) => a - b).forEach(t => {
      const codes = groups[t].sort((a, b) => a - b);
      if (!first) raw.push(0); first = false;                // 1-bit separator
      scPut(raw, t, 7); scPut(raw, codes.length, 8);
      codes.forEach(c => {
        if (c < 128) scPut(raw, c, 8);
        else { scPut(raw, (c & 0x7f) | 0x80, 8); scPut(raw, c >> 7, 8); }
      });
    });
    while ((4 + raw.length) % 5) raw.push(0);                 // pad in raw frame, then scramble
    const stored = tmpl.slice(0, 4).concat(scScramble(raw));
    let out = "";
    for (let i = 0; i < stored.length; i += 5) out += SC_ALPH[scInt(stored, i, 5)];
    return out;
  }

  // parse pasted/loaded text into an import action: a full-collection backup (→ merge), or a single
  // board from a share code / share link / #-fragment (→ one new build). Accepts any roster
  // (PC / beast / drone). The old game mastery_export.json path is intentionally dropped.
  function bldParseImport(text) {
    text = (text || "").trim();
    if (!text) return { error: t("io.errEmpty", "Nothing to import — paste a code, link, or export.") };
    // a full export backup?
    if (/^[\[{]/.test(text)) {
      let data; try { data = JSON.parse(text); } catch (e) { return { error: t("io.errJson", "Invalid JSON: ") + e.message }; }
      if (!data || !Array.isArray(data.builds)) return { error: t("io.errFormat", "Unrecognized format — expected a TroubleMaster export (use “Backup”).") };
      return { kind: "all", data };
    }
    // otherwise a single build: a share link, a bare #-fragment, or a bare code
    const frag = text.includes("#") ? text.slice(text.lastIndexOf("#") + 1) : text;
    const params = new URLSearchParams(frag);
    const code = (params.get("build") || frag).trim();   // a bare code has no build= → use the whole string
    const r = bldAnalyzeShareCode(code, params.get("name"), bldPicksFromParams(params));
    if (!r) return { error: t("io.errDecode", "Couldn't decode that as a board share code or link.") };
    // a few unrecognized masteries (likely a newer game version) → let the user opt into a partial import
    if (r.unknown) return { kind: "one-partial", build: r.build, known: r.known, unknown: r.unknown };
    return { kind: "one", build: r.build };
  }

  function renderIoPanel(mode) {
    const io = bldEls.io;
    if (bld.io === mode) { bld.io = null; io.replaceChildren(); io.classList.remove("open"); return; }
    bld.io = mode; io.replaceChildren(); io.classList.add("open");
    (mode === "import" ? buildImportPanel : buildExportPanel)(io);
  }
  // keep an open EXPORT panel in sync with the active build — its code/link are build-specific, so a
  // build switch or an edit made with the panel open must refresh them. (The import panel has an
  // editable textarea, so it's left intact and only (re)built when toggled.)
  function bldSyncExportPanel() {
    if (bld.io === "export") { bldEls.io.replaceChildren(); buildExportPanel(bldEls.io); }
  }

  function buildImportPanel(io) {
    // one line tall by default (most formats are single-line; the multi-line "Backup" export is
    // normally restored via file import) — vertically resizable for pasting a longer blob
    const ta = el("textarea.bld-io-text", { rows: "1", placeholder: t("io.placeholder", "Paste a board share code (e.g. KSAAC…), a share link, or a TroubleMaster export here…") });
    const file = el("input.bld-io-file", { type: "file", accept: ".json,application/json,.txt" });
    const loadBtn = el("button.bld-iobtn", { text: t("io.import", "Import") });
    const msg = el("div.bld-io-msg");
    const finishOne = b => { bldImportOne(b); bld.io = null; io.replaceChildren(); io.classList.remove("open"); };
    const doImport = () => {
      const r = bldParseImport(ta.value);
      if (r.error) { msg.textContent = r.error; msg.className = "bld-io-msg err"; return; }
      if (r.kind === "all") {                            // full backup → merge into the collection
        const res = bldImportAll(r.data);
        renderBuilder();                                 // refresh the Builds list (the panel survives)
        msg.textContent = tf("io.merged", "Imported {added} build(s); {dup} duplicate(s) skipped; {stars} starred added.",
          { added: res.added, dup: res.dup, stars: res.stars });
        msg.className = "bld-io-msg";
        ta.value = "";
      } else if (r.kind === "one-partial") {             // some masteries unrecognized → confirm partial
        msg.className = "bld-io-msg warn";
        msg.replaceChildren(
          el("span", { text: tf("io.partialWarn",
            "{unknown} of {total} masteries weren’t recognized (a newer game version?) and will be left off.",
            { unknown: r.unknown, total: r.known + r.unknown }) }),
          el("button.bld-iobtn.bld-io-partial", { text: tf("io.importPartial", "Import the {known} recognized", { known: r.known }),
            onClick: () => finishOne(r.build) }));
      } else {                                           // single code/link → add as a new build + switch
        finishOne(r.build);
      }
    };
    file.addEventListener("change", () => {
      const f = file.files[0]; if (!f) return;
      const fr = new FileReader();
      fr.onload = () => { ta.value = fr.result; doImport(); };
      fr.readAsText(f);
    });
    loadBtn.addEventListener("click", doImport);
    // Enter imports (the field is effectively single-line); Shift+Enter still inserts a newline
    ta.addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doImport(); }
    });
    const row = el("div.bld-io-row");
    row.append(file, loadBtn);
    io.append(ta, row, msg);
  }

  function copyTo(btn, text) {
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
      .then(() => { btn.textContent = t("io.copied", "Copied ✓"); }, () => { btn.textContent = t("io.copyFailed", "Copy failed"); });
  }

  function buildExportPanel(io) {
    const msg = el("div.bld-io-msg");
    const pc = unitById[bld.pcId];
    if (!pc) { msg.textContent = t("io.pickFirst", "Pick a character first."); io.append(msg); return; }
    bldSave();                                          // flush live edits so the link carries them
    const cur = bldStore.builds.find(x => x.id === bld.id) || bld;
    // Share code (in-game importable) — built from the current build (PC, beast or drone)
    const placed = [];
    BOARD_CATS.forEach(cat => (bld.placed[cat] || []).forEach(n => placed.push(n)));
    let code = null;
    try { code = encodeShareCode(bld.pcId, bld.jobId, bld.level, placed); } catch (e) { code = null; }
    if (!code) { msg.textContent = t("io.pickFirst", "Pick a character first."); io.append(msg); return; }
    const lbl = el("label.bld-io-msg", { text: t("io.shareCode", "In-game share code:") });
    const ci = el("input.bld-io-code", { readonly: "" }); ci.value = code;
    const ccopy = el("button.bld-iobtn", { text: t("io.copyCode", "Copy code") });
    ccopy.addEventListener("click", () => { ci.select(); copyTo(ccopy, code); });
    const lcopy = el("button.bld-iobtn", { text: t("io.copyLink", "Copy link"),
      title: t("io.copyLinkTitle", "A shareable URL that opens this build in the builder") });
    lcopy.addEventListener("click", () => copyTo(lcopy, bldShareLink(cur, code)));
    const crow = el("div.bld-io-row"); crow.append(ci, ccopy, lcopy);
    io.append(lbl, crow);
    // beasts & drones drop their picks from the CODE (so it stays game-importable); our LINK restores them
    if (isBeast(pc) || isDrone(pc))
      io.append(el("div.bld-io-msg.src-note", { text: t("io.linkRestoresNote",
        "The in-game code is board-only, so it imports into the game; the share link also restores the picks "
        + "it can't carry — a beast's evolution masteries, or a drone's OS / reinforcement / craft & AI-upgrade picks.") }));
  }

  // fill the 2nd selector with a beast family's forms, grouped by evolution stage. After a form
  // is picked, its direct evolutions (evolvesTo) are floated to a "Evolves into →" group on top.
  // Stage names are the game's own localized strings, per EvolutionType (Normal = Growth/
  // Adolescent/Mature; EggStart/Draki adds Babyhood as a 4th), keyed by the form's stage.
  function beastStageName(evoType, stage) {
    const tbl = (DATA.beastStages || {});
    const m = tbl[evoType] || tbl.Normal || {};
    return m[stage] || ("Stage " + stage);
  }
  // groups for the beast Form custom dropdown: the current form's direct evolutions on top (with the
  // unlock level), then every stage present (Draki reach 4). Mirrors the old native layout.
  function beastFormGroups(fam, currentFormId) {
    const forms = beastsByFamily[fam] || [];
    const evoType = (forms[0] || {}).evoType;
    const maxSt = forms.reduce((mx, f) => Math.max(mx, f.stage || 1), 1);
    const cur = beastById[currentFormId];
    const groups = [];
    if (cur && (cur.evolvesTo || []).length) {
      const ids = cur.evolvesTo.filter(e => beastById[e.id])
        .map(e => ({ id: e.id, label: beastById[e.id].name + (e.requireLv ? ` (Lv ${e.requireLv})` : "") }));
      if (ids.length) groups.push({ label: "Evolves into →", ids });
    }
    for (let st = 1; st <= maxSt; st++) {              // iterate every stage present (Draki reach 4)
      const inStage = forms.filter(f => f.stage === st);
      if (inStage.length) groups.push({ label: beastStageName(evoType, st), ids: inStage.map(f => f.id) });
    }
    return groups;
  }

  // ---- custom dropdown for the evolution-mastery pickers: a button + a div menu, so each
  // option (and the closed trigger's current pick) can show the mastery card on hover, which a
  // native <select> can't do. Only one menu is open at a time; clicks outside / Esc close it. ----
  let openCsel = null;
  function closeAllCsel() {
    // close every open dropdown found in the DOM, not just the one tracked in `openCsel` — a
    // re-render (renderBuilder) nulls `openCsel` while leaving a freshly-built menu's state alone,
    // so relying on the tracked handle alone left some menus visibly open after a pick.
    document.querySelectorAll(".csel.open").forEach(c => {
      c.classList.remove("open");
      const m = c.querySelector(".csel-menu"); if (m) m.hidden = true;
    });
    openCsel = null;
  }
  const evoLabel = id =>
    (id && ((masteryById[id] && masteryById[id].name) || (beastEvoById[id] && beastEvoById[id].name))) || t("bld.none", "— none —");
  // opts (optional): labelOf(id)->text, cardOf(id)->mastery for the hover card, noNone to drop the
  // "— none —" option (for mandatory pickers like the drone SP / OS). Defaults treat id as a mastery.
  function makeCsel(currentId, groups, onPick, opts) {
    opts = opts || {};
    const labelOf = opts.labelOf || evoLabel;
    const cardOf = opts.cardOf || (id => masteryById[id]);
    const csel = el("div.csel");
    const trig = el("button.csel-trigger", { type: "button" });
    const lab = el("span.csel-lab", { text: labelOf(currentId) });
    const car = el("span.csel-car", { text: "▾" });
    trig.append(lab, car);
    const menu = el("div.csel-menu"); menu.hidden = true;
    // hovering the *closed* picker shows the currently-selected mastery's card
    trig.addEventListener("mouseenter", () => {
      const m = currentId && cardOf(currentId);
      if (m && menu.hidden) showTip(trig, box => buildMasteryTip(m, box));
    });
    trig.addEventListener("mouseleave", () => hideTip(trig));
    const opt = (id, text) => {
      const o = el("div.csel-opt", { text });
      if (id === currentId) o.classList.add("sel");
      const m = id && cardOf(id);
      if (m) {                                          // hovering an open option shows its card (beside)
        o.addEventListener("mouseenter", () => showTip(o, box => buildMasteryTip(m, box), "right"));
        o.addEventListener("mouseleave", () => hideTip(o));
      }
      // preventDefault: these dropdowns live inside a <label>, and clicking a non-control part of a
      // label re-fires the click on the label's first control (the trigger button) — which would
      // immediately re-open the menu we're closing. preventDefault suppresses that label activation.
      o.addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); hideTip(o); closeAllCsel(); onPick(id); });
      return o;
    };
    if (!opts.noNone) menu.appendChild(opt("", opts.noneLabel || t("bld.none", "— none —")));
    groups.forEach(g => {
      if (!g.ids.length) return;
      // a group with no label is rendered flat (no header) — used for single-group selectors
      if (g.label) menu.appendChild(el("div.csel-group", { text: g.label }));
      // items may be a plain id (label via labelOf) or {id, label} to override the option text
      g.ids.forEach(it => { const id = typeof it === "object" ? it.id : it; menu.appendChild(opt(id, (typeof it === "object" && it.label) || labelOf(id))); });
    });
    trig.addEventListener("click", e => {
      e.preventDefault();                 // the trigger sits in a <label>; avoid label re-activation
      e.stopPropagation();
      const wasOpen = !menu.hidden;
      closeAllCsel(); hideTip(trig);
      if (!wasOpen) { menu.hidden = false; csel.classList.add("open"); openCsel = csel; }
    });
    csel.append(trig, menu);
    return csel;
  }

  // a row of evolution-mastery pickers (one per evolution level reached) shown above a beast's
  // board; each is the beast's pool grouped by type, excluding picks chosen at other levels.
  function renderEvoRow(unit) {
    const pool = beastEvoPool(unit);
    if (!pool.length) return null;
    const grpOf = m => m.unique ? "Species" : (BEAST_GLOBAL_TYPES.has(m.type) ? m.type : "Element");
    // group labels are localized via typeDisplay (raw type id -> in-game name): the global pools
    // Training/Nature/Gene and the beast's ESP element (e.g. Heat -> 발열 / "Heating") — the same map
    // the build summary uses. Element falls back to a key when the beast has none; Species is a
    // synthetic group (m.unique masteries, no game type) so it carries its own translation key.
    const elemLabel = unit.element ? (typeDisplay[unit.element] || unit.element) : t("bld.evoElement", "Element");
    const order = [["Training", typeDisplay.Training || "Training"], ["Nature", typeDisplay.Nature || "Nature"],
                   ["Gene", typeDisplay.Gene || "Genetics"], ["Element", elemLabel],
                   ["Species", t("bld.evoSpecies", "Species")]];
    const row = el("div.bld-evo-row", {},
      el("span.bld-evo-head", { text: t("bld.evoMasteries", "Evolution masteries") }));
    const stages = unit.stage || 1;
    for (let lvl = 0; lvl < stages; lvl++) {
      const pick = el("div.bld-evo-pick", {},
        el("span.bld-evo-lvl", { text: beastStageName(unit.evoType, lvl + 1) }));
      const taken = new Set((bld.evo || []).filter((_, i) => i !== lvl).filter(Boolean));
      const buckets = { Training: [], Nature: [], Gene: [], Element: [], Species: [] };
      pool.forEach(id => { if (!taken.has(id)) buckets[grpOf(beastEvoById[id])].push(id); });
      const groups = order.map(([k, label]) => ({ label, ids: buckets[k] }));
      const lvlIdx = lvl;
      pick.appendChild(makeCsel((bld.evo || [])[lvl] || "", groups, id => {
        if (!bld.evo) bld.evo = [];
        bld.evo[lvlIdx] = id || "";
        renderBuilder();                  // recompute limits + refresh the other pickers' exclusions
      }));
      row.appendChild(pick);
    }
    return row;
  }

  // a row of a drone's chosen masteries: the construction craft-unique pick (always, from the
  // Performance/Compatibility pool) followed by one AI-upgrade pick per reinforcement step reached
  // (Remodeled/Reinforced/Complete). Each AI step picks from the OS pool whose required Lv is below
  // the step (shared_machine.lua GetMachineAIUpgradeMasteryCandidate: cls.Lv < stage), excluding
  // upgrades taken at another step. We model the eligible set; in-game you pick 1 of 3 drawn from it.
  function renderReinfRow(unit) {
    const pool = (MACH.aiUpgrade || {})[bld.os] || [];
    const craftPool = MACH.craft || [];
    if (!craftPool.length && (!pool.length || (bld.reinf || 1) < 2)) return null;
    const stages = MACH.reinf || {};
    const row = el("div.bld-evo-row", {},
      el("span.bld-evo-head", { text: t("bld.craftAi", "Craft & AI upgrades") }));
    // construction craft-unique pick — present at every stage, grouped by trait type
    if (craftPool.length) {
      const pick = el("div.bld-evo-pick", {},
        el("span.bld-evo-lvl", { text: t("bld.machineCrafting", "Machine Crafting") }));
      const buckets = {};
      craftPool.forEach(id => { const t = (masteryById[id] || {}).type || "Other"; (buckets[t] = buckets[t] || []).push(id); });
      const groups = Object.keys(buckets).sort().map(t => ({ label: t, ids: buckets[t] }));
      pick.appendChild(makeCsel(bld.craft || "", groups, id => { bld.craft = id || null; renderBuilder(); }));
      row.appendChild(pick);
    }
    for (let i = 0; i < bld.reinf - 1; i++) {
      const stage = i + 2;                                       // pick i is made on reaching this stage
      const pick = el("div.bld-evo-pick", {},
        el("span.bld-evo-lvl", { text: stages[stage] || ("Stage " + stage) }));
      const taken = new Set((bld.evo || []).filter((_, j) => j !== i).filter(Boolean));
      const buckets = {};                                        // eligible (Lv < stage), grouped by unlock step
      pool.forEach(u => { if (u.lv < stage && !taken.has(u.id)) (buckets[u.lv] = buckets[u.lv] || []).push(u.id); });
      const groups = Object.keys(buckets).sort((a, b) => a - b)
        .map(lv => ({ label: stages[+lv + 1] || ("Lv " + lv), ids: buckets[lv] }));
      const idx = i;
      pick.appendChild(makeCsel((bld.evo || [])[i] || "", groups, id => {
        if (!bld.evo) bld.evo = [];
        bld.evo[idx] = id || "";
        renderBuilder();                  // recompute limits + refresh the other pickers' exclusions
      }));
      row.appendChild(pick);
    }
    return row;
  }

  function renderBuilder() {
    hideTip();                       // a re-render rebuilds the DOM, orphaning any hovered tip
    openCsel = null;                 // any open custom dropdown is about to be replaced
    const pc = unitById[bld.pcId];
    const beast = isBeast(pc), drone = isDrone(pc);

    // ---- Character selector (custom dropdown): Troubleshooters / Beasts / Drones, grouped ----
    // value is the PC id, beast family id, or "frame:<id>"; shows the family/frame for beasts/drones.
    const charVal = beast ? pc.family : drone ? ("frame:" + pc.frame) : (bld.pcId || "");
    const charLabel = v => {
      if (!v) return t("bld.pickCharacterShort", "— pick a character —");
      if (v.startsWith("frame:")) { const f = (MACH.frames || []).find(x => x.id === v.slice(6)); return f ? f.name : v; }
      if (pcById[v]) return pcById[v].name;
      if (beastFamById[v]) return beastFamById[v].title;
      return v;
    };
    bldEls.pcCsel.replaceChildren();
    bldEls.pcCsel.appendChild(makeCsel(charVal, [
      { label: "Troubleshooters", ids: (DATA.pcs || []).map(p => p.id) },
      { label: "Beasts", ids: (DATA.beastFamilies || []).map(f => f.id) },
      { label: "Drones", ids: (MACH.frames || []).filter(f => f.opened).map(f => "frame:" + f.id) },
    ], v => bldSelectCharacter(v || null), { labelOf: charLabel, cardOf: () => null, noneLabel: t("bld.pickCharacterShort", "— pick a character —") }));

    // ---- 2nd selector: Class (PC jobs) / Form (beast) / SP (drone) ----
    // SP options show their structure's base-mastery card on hover; class/form carry no card.
    bldEls.jobLabelText.textContent = beast ? t("bld.form", "Form") : drone ? t("bld.sp", "SP") : t("bld.class", "Class");
    bldEls.jobCsel.replaceChildren();
    if (beast) {
      const formLabel = id => (beastById[id] || {}).name || id;
      bldEls.jobCsel.appendChild(makeCsel(bld.pcId, beastFormGroups(pc.family, bld.pcId),
        v => bldSelectForm(v),
        { labelOf: formLabel, cardOf: () => null, noNone: true }));
    } else if (drone) {
      const spName = id => { const s = (MACH.sp || []).find(x => x.id === id); return s ? s.name : id; };
      const spCard = id => { const s = (MACH.sp || []).find(x => x.id === id); return s && masteryById[s.mastery]; };
      bldEls.jobCsel.appendChild(makeCsel(pc.sp,
        [{ label: "SP Structure", ids: (MACH.sp || []).map(s => s.id) }],
        spId => bldSelectForm(spId), { labelOf: spName, cardOf: spCard, noNone: true }));
    } else if (pc) {
      if (!bld.jobId || !pc.jobs.includes(bld.jobId)) bld.jobId = pc.jobs[0];
      const jobLabel = id => { const j = jobById[id]; return j ? `${j.title} (Lv ${j.requireLv})` : id; };
      bldEls.jobCsel.appendChild(makeCsel(bld.jobId,
        [{ label: "", ids: pc.jobs.filter(jid => jobById[jid]) }],
        v => bldSelectForm(v), { labelOf: jobLabel, cardOf: () => null, noNone: true }));
    }

    // ---- OS selector — drones only (picks the reinforcement pool) ----
    // the OS id IS its mastery id, so the default label/card lookups show each OS's mastery card on hover
    bldEls.osLabel.hidden = !drone;
    bldEls.osCsel.replaceChildren();
    if (drone) {
      if (!bld.os || !(MACH.os || []).some(o => o.id === bld.os)) bld.os = (MACH.os[0] || {}).id;
      bldEls.osCsel.appendChild(makeCsel(bld.os,
        [{ label: t("bld.osGroup", "Operating System"), ids: (MACH.os || []).map(o => o.id) }],
        osId => { bld.os = osId; bld.evo = []; renderBuilder(); }, { noNone: true }));
    }

    // ---- Reinforcement stage — drones only (Normal→Remodeled→Reinforced→Complete; gates AI picks) ----
    bldEls.reinfLabel.hidden = !drone;
    bldEls.reinfCsel.replaceChildren();
    if (drone) {
      const stages = MACH.reinf || {};
      bld.reinf = Math.min(4, Math.max(1, +bld.reinf || 1));
      bldEls.reinfCsel.appendChild(makeCsel(String(bld.reinf),
        [{ label: "", ids: Object.keys(stages) }],
        n => { bld.reinf = +n || 1; bld.evo = (bld.evo || []).slice(0, Math.max(0, bld.reinf - 1)); renderBuilder(); },
        { labelOf: n => stages[n] || n, cardOf: () => null, noNone: true }));
    }
    bldEls.level.value = bld.level;
    bldSave();                       // persist the (resolved) current build
    bldRenderList();                 // refresh the Builds dropdown from the just-saved state (not stale)
    bldSyncUndoBtns();               // enable/disable Undo/Redo for the active build's stacks

    if (!pc) {
      bldEls.summary.textContent = "";
      bldEls.broken.classList.remove("show");
      bldEls.board.replaceChildren();
      bldEls.side.replaceChildren();
      bldEls.board.appendChild(el("div.empty", { text: t("bld.pickCharacter", "Pick a character to start building a mastery board.") }));
      bldSyncExportPanel();
      return;
    }

    const types = bldAccessTypes();
    const job = jobById[bld.jobId];

    // ---- board columns (limits fully computed from the game formulas) ----
    const limits = bldLimits();
    bldEls.board.replaceChildren();
    if (beast) { const er = renderEvoRow(pc); if (er) bldEls.board.appendChild(er); }
    if (drone) { const rr = renderReinfRow(pc); if (rr) bldEls.board.appendChild(rr); }
    let totalUsed = 0;
    const broken = [];
    const slotById = {};                // placed mastery id -> its slot element (for set highlight)
    const setIconsById = {};            // placed mastery id -> its set-panel diamonds (reverse highlight)
    // components of every completed set — their board slots get a violet left border marking them
    // as part of an active set.
    const inSetIds = new Set();
    completedSets(new Set(BOARD_CATS.flatMap(c => bld.placed[c] || [])))
      .forEach(s => s.components.forEach(c => inSetIds.add(c.id)));
    // light up the set-panel diamonds for a mastery while hovering it on the board (mirrors the game)
    const setHi = (id, on) => (setIconsById[id] || []).forEach(ic => ic.classList.toggle("mastery-hi", on));
    const pcFixed = pc.fixed || [];
    boardColumns(pc).forEach(column => {
      const cat = column.slot;                         // the engine's slot category (Basic/Attack/…)
      const placed = (bld.placed[cat] || []).map(id => masteryById[id]).filter(Boolean);
      // inherent fixed masteries pinned in this category (always present, non-removable)
      const fixedHere = pcFixed.filter(f => f.cat === cat)
        .map(f => masteryById[f.id] || { name: f.name, id: f.id, cost: 0 });
      const lim = limits.cats[cat];
      const usedCount = fixedHere.length + placed.length;
      const usedCost = fixedHere.reduce((a, m) => a + (m.cost || 0), 0)
        + placed.reduce((a, m) => a + (m.cost || 0), 0);
      totalUsed += usedCost;
      const colLabel = catDisplay[column.cat] || column.label;
      if (usedCount > lim.unlocked) broken.push(tf("bld.brokenSlots", "{label}: {n} masteries over {cap} unlocked slots", { label: colLabel, n: usedCount, cap: lim.unlocked }));
      if (usedCost > lim.cost) broken.push(tf("bld.brokenCost", "{label}: {n} points over {cap} cost cap", { label: colLabel, n: usedCost, cap: lim.cost }));

      const col = el("div.bld-col", { class: CAT_COLOR[cat] || "" });
      // clicking the column title filters the sidebar to this column's masteries (toggle)
      const head = el("div.bld-col-head.clickable", { class: bld.catFilter === column.cat ? "filtered" : "",
        title: tf("bld.colFilterTitle", "Filter the sidebar to {label} masteries", { label: colLabel }) });
      head.addEventListener("click", () => {
        bld.catFilter = bld.catFilter === column.cat ? null : column.cat;
        if (bld.catFilter) bld.sideTab = "masteries";
        renderBuilder();
      });
      head.append(
        // name + funnel share the left; the funnel marks the header as a sidebar filter
        el("span.bld-col-head-l", {},
          el("span.bld-cat-name", { text: colLabel }),
          el("span.bld-col-funnel", { html: FUNNEL_SVG })),
        // top-corner shows cost usage (slot usage is obvious from the slots below)
        el("span.bld-cat-count", { class: usedCost > lim.cost ? "over" : "", text: `${usedCost}/${lim.cost}`,
          title: t("bld.costTitle", "Training-point cost used / cap") }));
      col.appendChild(head);

      // user-placed masteries → empty open slots → level-locked slots → pinned fixed
      // masteries last (matching the in-game layout). Fixed masteries occupy their own
      // appended always-on slots, so the user can fill (unlocked - fixed) slots.
      const body = el("div.bld-slots");
      const userOpen = Math.max(0, lim.unlocked - fixedHere.length);
      placed.forEach((m, i) => {
        const slot = el("div.bld-slot.filled" + (i >= userOpen ? ".over" : "") + (inSetIds.has(m.id) ? ".in-set" : ""),
          { title: t("bld.slotRemove", "Remove") },
          starToggle(m.id, "bld-star"),
          el("span.bld-slot-name", { text: m.name }),
          el("span.bld-slot-cost", { text: m.cost || 0 }));
        slotById[m.id] = slot;
        slot.addEventListener("mouseenter", () => { showTip(slot, box => buildMasteryTip(m, box)); setHi(m.id, true); });
        slot.addEventListener("mouseleave", () => { hideTip(slot); setHi(m.id, false); });
        slot.addEventListener("click", () => {
          bld.placed[cat] = bld.placed[cat].filter(id => id !== m.id);
          hideTip(slot); renderBuilder();
        });
        body.appendChild(slot);
      });
      for (let i = placed.length; i < userOpen; i++)             // open, empty slots
        body.appendChild(el("div.bld-slot.empty", { text: t("bld.slotEmpty", "Empty") }));
      lim.locked.forEach(lv =>                                   // not yet unlocked by level
        body.appendChild(el("div.bld-slot.locked", { text: tf("bld.slotLocked", "Unlock Lv {lv}", { lv }) })));
      fixedHere.forEach(m => {                                   // inherent, pinned, ★ (no cost)
        const slot = el("div.bld-slot.filled.fixed",
          { title: t("bld.slotFixed", "Inherent to this character — always present") },
          el("span.bld-slot-name", { text: m.name }),
          el("span.bld-slot-cost", { text: "★" }));
        slotById[m.id] = slot;
        if (masteryById[m.id]) {
          slot.addEventListener("mouseenter", () => { showTip(slot, box => buildMasteryTip(masteryById[m.id], box)); setHi(m.id, true); });
          slot.addEventListener("mouseleave", () => { hideTip(slot); setHi(m.id, false); });
        }
        body.appendChild(slot);
      });
      col.appendChild(body);
      bldEls.board.appendChild(col);
    });

    // ---- 6th panel: completed + partial mastery sets ----
    bldEls.board.appendChild(renderSetPanel(slotById, setIconsById, types));

    // data-driven mutual exclusion (Mastery.xml ExclusiveMastery): flag any placed pair the game
    // forbids together (e.g. Warrior's <-> Guardian's Descendant — the only such pair in the game).
    const placedFlat = BOARD_CATS.flatMap(c => bld.placed[c] || []);
    const placedSet = new Set(placedFlat);
    const seenExcl = new Set();
    placedFlat.forEach(id => {
      const m = masteryById[id];
      ((m && m.exclusive) || []).forEach(other => {
        if (!placedSet.has(other)) return;
        const key = [id, other].sort().join("|");
        if (seenExcl.has(key)) return;
        seenExcl.add(key);
        broken.push(tf("bld.brokenExclusive", "{a} and {b} can't be equipped together",
          { a: m.name, b: (masteryById[other] || {}).name || other }));
      });
    });

    if (totalUsed > limits.total) broken.unshift(tf("bld.brokenTotal", "Total: {n} points over {cap} cap", { n: totalUsed, cap: limits.total }));
    bldEls.summary.textContent = tf("bld.summary", "Training Point {used}/{total}", { used: totalUsed, total: limits.total })
      + (pc.element ? ` · ${typeDisplay[pc.element] || pc.element}` : "") + ` · ${job ? job.title : ""}`;
    if (broken.length) {
      bldEls.broken.classList.add("show");
      bldEls.broken.replaceChildren();
      bldEls.broken.append(el("strong", { text: t("bld.broken", "⚠ Build broken — ") }), broken.join("; "));
    } else {
      bldEls.broken.classList.remove("show");
    }

    renderBuilderSide(types);
    bldSyncExportPanel();
  }

  // the "Mastery Set" panel: completed sets (all components on the board) first, then a
  // separator and the completable partial sets (≥1 component placed, the rest accessible),
  // sorted by how many you already have. Each component is a hoverable icon (mastery card);
  // missing components are dimmed; hovering a row lights up the placed ones on the board;
  // clicking a partial row adds its missing masteries to the build.
  function renderSetPanel(slotById, setIconsById, types) {
    const placedIds = new Set(BOARD_CATS.flatMap(c => bld.placed[c] || []));
    const scored = DATA.sets
      .filter(s => s.components.length)
      .map(s => ({ s, matched: s.components.filter(c => placedIds.has(c.id)).length }))
      .filter(x => x.matched > 0);
    const full = completedSets(placedIds);
    // partials sorted by how many you're still MISSING (so "1 away" sets group together
    // regardless of how many components the set has), then by matched / name as tiebreak
    const partial = scored
      .filter(x => x.matched < x.s.components.length
        && x.s.components.every(c => bldAccessible(masteryById[c.id], types)))
      .map(x => ({ ...x, missing: x.s.components.length - x.matched }))
      .sort((a, b) => a.missing - b.missing || b.matched - a.matched || a.s.name.localeCompare(b.s.name));

    const col = el("div.bld-col.bld-setcol");
    col.appendChild(el("div.bld-col-head", {},
      el("span.bld-cat-name", { text: t("bld.masterySet", "Mastery Set") }),
      el("span.bld-cat-count", { text: full.length })));

    const body = el("div.bld-slots");

    // one set row; addable=true makes it dim missing icons and add them on click
    const setRow = (s, addable) => {
      const row = el("div.bld-set-row", { class: addable ? "addable" : "" });
      const label = el("span.bld-set-name", { text: s.name });
      // hovering the name shows the set card; the icons (right side) show per-mastery
      // cards instead, so they deliberately skip the set tip to avoid flicker
      label.addEventListener("mouseenter", () => showTip(label, box => buildSetTip(s, box)));
      label.addEventListener("mouseleave", () => hideTip(label));
      const icons = el("span.bld-set-icons");
      s.components.forEach(c => {
        const m = masteryById[c.id];
        const ic = el("span.bld-setmastery", { class: (m && CAT_COLOR[m.categoryRaw] || "")
          + (placedIds.has(c.id) ? "" : " missing") });
        ic.title = "";   // suppress the row's "add missing" tooltip from bubbling onto icons
        (setIconsById[c.id] || (setIconsById[c.id] = [])).push(ic);   // for board→panel reverse highlight
        // hovering a diamond lights up every diamond for the same mastery — across all sets,
        // including dimmed (missing) ones — mirroring the board-slot hover
        const hiSame = on => (setIconsById[c.id] || []).forEach(x => x.classList.toggle("mastery-hi", on));
        ic.addEventListener("mouseenter", () => hiSame(true));
        ic.addEventListener("mouseleave", () => hiSame(false));
        if (m) {
          ic.addEventListener("mouseenter", () => showTip(ic, box => buildMasteryTip(m, box)));
          ic.addEventListener("mouseleave", () => hideTip(ic));
        }
        icons.appendChild(ic);
      });
      row.append(label, icons);
      // light up the placed component masteries on the board while hovering the row
      const hi = on => s.components.forEach(c => {
        const node = slotById[c.id]; if (node) node.classList.toggle("set-hi", on);
      });
      row.addEventListener("mouseenter", () => hi(true));
      row.addEventListener("mouseleave", () => hi(false));
      if (addable) {
        row.title = t("bld.addMissing", "Add the missing masteries to the build");
        row.addEventListener("click", () => { s.components.forEach(c => bldPlace(c.id)); renderBuilder(); });
      }
      return row;
    };

    if (!full.length && !partial.length)
      body.appendChild(el("div.bld-slot.empty", { text: t("bld.noSets", "No matching sets") }));
    full.forEach(s => body.appendChild(setRow(s, false)));
    // partials grouped by how many you're missing — each group foldable; the 1-away group open by default
    [...new Set(partial.map(x => x.missing))].sort((a, b) => a - b).forEach((k, i) => {
      const rows = partial.filter(x => x.missing === k);
      const det = el("details.bld-set-fold");
      det.open = bldSetFolds.has(k);                          // restore session expand state
      det.addEventListener("toggle", () => det.open ? bldSetFolds.add(k) : bldSetFolds.delete(k));
      det.appendChild(el("summary", { text: tf("bld.missing", "Missing {k}", { k }) + (i === 0 ? t("bld.missingComplete", " — click to complete") : "") + ` (${rows.length})` }));
      rows.forEach(x => det.appendChild(setRow(x.s, true)));
      body.appendChild(det);
    });
    col.appendChild(body);
    return col;
  }

  function renderBuilderSide(types) {
    bldEls.side.replaceChildren();
    const head = el("div.bld-side-head");
    ["masteries", "sets"].forEach(tab => {
      const b = el("button.bld-side-tab", { class: bld.sideTab === tab ? "active" : "",
        text: tab === "masteries" ? tf("bld.sideMasteries", "Masteries") : tf("bld.sideSets", "Sets") });
      b.addEventListener("click", () => { bld.sideTab = tab; renderBuilder(); });
      head.appendChild(b);
    });
    const search = el("input.bld-side-search", { type: "search", placeholder: t("bld.sideFilter", "Filter…") });
    search.value = bld.q;
    search.addEventListener("input", () => {
      bld.q = search.value.trim().toLowerCase();
      renderBuilderSideList(types, list);
    });
    bldEls.side.append(head, search);
    const list = el("div.bld-side-list");
    bldEls.side.appendChild(list);
    renderBuilderSideList(types, list);
    // keep focus while typing
    if (document.activeElement === search) search.focus();
  }

  function renderBuilderSideList(types, list) {
    list.replaceChildren();
    const q = bld.q;
    if (bld.sideTab === "masteries") {
      // masteries already on the board (user-placed + inherent fixed) drop out of the picker
      const pc = unitById[bld.pcId];
      const onBoard = new Set(BOARD_CATS.flatMap(c => bld.placed[c] || []));
      (pc && pc.fixed || []).forEach(f => onBoard.add(f.id));
      const items = DATA.masteries.filter(m => bldAccessible(m, types)
        && !onBoard.has(m.id)
        && (!bld.catFilter || m.categoryRaw === bld.catFilter)
        && (!q || m._blob.includes(q)));
      items.sort((a, b) => a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1);
      if (bld.catFilter) {
        const cl = countLine(tf("bld.catFiltered", "{n} {label} masteries · show all ✕", { n: items.length, label: catDisplay[bld.catFilter] || bld.catFilter }));
        cl.classList.add("bld-side-clear");
        cl.addEventListener("click", () => { bld.catFilter = null; renderBuilder(); });
        list.appendChild(cl);
      } else {
        list.appendChild(countLine(tf("bld.accessibleMasteries", "{n} accessible masteries", { n: items.length })));
      }
      items.forEach(m => {
        const it = el("div.bld-item", { class: CAT_COLOR[m.categoryRaw] || "" },
          starToggle(m.id, "bld-star"),
          el("span.bld-item-name", { text: m.name }),
          el("span.bld-item-meta", { text: tf("bld.itemMeta", "{cat} · {cost}", { cat: m.category, cost: m.cost || 0 }) }));
        it.addEventListener("mouseenter", () => showTip(it, box => buildMasteryTip(m, box)));
        it.addEventListener("mouseleave", () => hideTip(it));
        it.addEventListener("click", () => { bldPlace(m.id); renderBuilder(); });
        list.appendChild(it);
      });
    } else {
      const items = DATA.sets.filter(s => types.has(s.type)
        && s.components.some(c => bldAccessible(masteryById[c.id], types))
        && (!q || s._blob.includes(q)));
      items.sort((a, b) => a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1);
      list.appendChild(countLine(tf("bld.accessibleSets", "{n} accessible sets", { n: items.length })));
      items.forEach(s => {
        const it = el("div.bld-item.bld-item-set", {},
          el("span.bld-item-name", { text: s.name }),
          el("span.bld-item-meta", { text: `${s.components.length} pc` }));
        it.addEventListener("mouseenter", () => showTip(it, box => buildSetTip(s, box)));
        it.addEventListener("mouseleave", () => hideTip(it));
        it.addEventListener("click", () => {
          s.components.forEach(c => { if (bldAccessible(masteryById[c.id], types)) bldPlace(c.id); });
          renderBuilder();
        });
        list.appendChild(it);
      });
    }
  }
  function countLine(text) { return el("div.bld-side-count", { text }); }

  // picking a character (PC / beast family / drone frame) starts a fresh board at its join state
  function bldSelectCharacter(v) {
    bld.placed = {}; bld.evo = []; bld.craft = null;   // a different character starts a fresh board
    if (v && v.startsWith("frame:")) {                 // picked a drone frame → default SP/OS, fresh
      const u = droneByFrameSp[v.slice(6) + "/" + (MACH.sp[0] || {}).id];
      bld.pcId = u ? u.id : null;
      // drones are crafted mid-game, not at Lv1 — the engine sets a progression-scaled level
      // (observed ~40, unlocking most slots); default to that, the Level field stays adjustable
      bld.jobId = "Drone"; bld.os = (MACH.os[0] || {}).id; bld.reinf = 1; bld.level = DRONE_CRAFT_LV;
    } else if (beastFamById[v]) {                      // picked a beast family → start at its base form
      const base = beastBaseForm(v);
      bld.pcId = base ? base.id : null;
      bld.jobId = v;                                   // the family is the "job"
      bld.level = base ? (base.startLv || 1) : 1;
    } else {
      // start fresh at the character's actual join level + class (the class they join in,
      // which is an advanced class for late joiners — e.g. Giselle joins as Sniper, not Gunman)
      bld.pcId = v;
      const pc = pcById[bld.pcId];
      bld.jobId = pc ? (pc.jobs.includes(pc.startJob) ? pc.startJob : pc.jobs[0]) : null;
      if (pc) bld.level = pc.startLv || 1;
    }
    renderBuilder();
  }
  // picking the 2nd selector: a PC class, a beast form (evolve), or a drone SP structure
  function bldSelectForm(v) {
    const u = unitById[bld.pcId];
    // resolve the form/class the switch would produce, but don't commit until we know whether it
    // would strip masteries/picks the new form/class can't hold (then we ask for confirmation first).
    let nextPc = bld.pcId, nextJob = bld.jobId;
    if (isDrone(u)) { const nu = droneByFrameSp[u.frame + "/" + v]; if (nu) nextPc = nu.id; }  // switch SP structure
    else if (isBeast(u)) nextPc = v;                   // switch form (evolve)
    else nextJob = v;                                  // switch class
    const npc = unitById[nextPc];
    // same eligibility gate as the sidebar/board (bldAccessTypes with no args = current form/class):
    // an advanced class's accessTypes already include the basic class it came from, so no separate
    // "prior classes" union is needed. evo picks use the new unit's evolution / AI-upgrade pool.
    const types = bldAccessTypes(npc, jobById[nextJob]);
    const evoPool = new Set(isDrone(npc) ? ((MACH.aiUpgrade || {})[bld.os] || []).map(x => x.id) : beastEvoPool(npc));
    const dropped = [];
    BOARD_CATS.forEach(c => (bld.placed[c] || []).forEach(id => {
      const m = masteryById[id];
      if (m && !bldAccessible(m, types, npc)) dropped.push(m.name || id);
    }));
    (bld.evo || []).forEach(id => {
      if (id && !evoPool.has(id)) dropped.push((beastEvoById[id] || masteryById[id] || {}).name || id);
    });
    if (dropped.length && !confirm(tf("bld.switchConfirm",
        "Switching will remove {n} mastery/masteries the new form or class can't equip:\n\n{list}\n\nSwitch anyway?",
        { n: dropped.length, list: dropped.join("\n") }))) {
      renderBuilder();                                 // cancelled — re-render resets the picker to the current form/class
      return;
    }
    bld.pcId = nextPc; bld.jobId = nextJob;   // commit, then prune what the new form/class can't keep
    BOARD_CATS.forEach(c => {
      if (!bld.placed[c]) return;
      bld.placed[c] = bld.placed[c].filter(id => bldAccessible(masteryById[id], types, npc));
      if (!bld.placed[c].length) delete bld.placed[c];
    });
    bld.evo = (bld.evo || []).map(id => (id && evoPool.has(id)) ? id : "");
    renderBuilder();
  }
  // close any open custom dropdown on an outside click or Esc
  document.addEventListener("click", () => closeAllCsel());
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeAllCsel(); });
  bldEls.level.addEventListener("change", () => {
    bld.level = Math.max(1, +bldEls.level.value || 1); renderBuilder();
  });
  bldEls.importBtn.addEventListener("click", () => renderIoPanel("import"));
  bldEls.exportBtn.addEventListener("click", () => renderIoPanel("export"));
  bldEls.exportAllBtn.addEventListener("click", () => {           // download the whole collection as a backup
    const url = URL.createObjectURL(new Blob([bldExportAll()], { type: "application/json" }));
    const a = el("a", { href: url, download: "troublemaster-builds.json" });
    a.click(); URL.revokeObjectURL(url);
  });
  bldEls.undoBtn.addEventListener("click", bldUndoStep);
  bldEls.redoBtn.addEventListener("click", bldRedoStep);
  // Ctrl/Cmd+Z to undo, Ctrl/Cmd+Shift+Z (or Ctrl+Y) to redo — only on the builder tab, and never
  // while typing in a field (so the search box, level input and import textarea keep native undo).
  document.addEventListener("keydown", e => {
    if (state.view !== "builder" || !(e.ctrlKey || e.metaKey) || e.altKey) return;
    const tgt = e.target;
    if (tgt && (tgt.tagName === "INPUT" || tgt.tagName === "TEXTAREA" || tgt.isContentEditable)) return;
    const k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); bldUndoStep(); }
    else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); bldRedoStep(); }
  });
  bldEls.newBtn.addEventListener("click", bldNew);
  bldEls.dupBtn.addEventListener("click", bldDuplicate);
  bldEls.renameBtn.addEventListener("click", bldRename);
  bldEls.deleteBtn.addEventListener("click", bldDelete);

  document.querySelectorAll(".tab").forEach(tab =>
    tab.addEventListener("click", () => { selectTab(tab); render(); }));
  els.indivSubtabs.querySelectorAll(".subtab").forEach(b =>
    b.addEventListener("click", () => { state.indivCat = b.dataset.cat; populateTypes(); render(); }));
  // lazily add a title tooltip to an inline description cell only when it's actually ellipsised
  // (measured at hover time → cheap, and stays correct across window resizes; no CSS way to do this)
  const ellipsisTitle = e => {
    const cell = e.target.closest && e.target.closest(".desc-inline");
    if (!cell) return;
    if (cell.scrollWidth > cell.clientWidth + 1) cell.title = cell.textContent;
    else cell.removeAttribute("title");
  };
  els.body.addEventListener("mouseover", ellipsisTitle);
  els.abilityBody.addEventListener("mouseover", ellipsisTitle);

  // custom clear (×) button on the main search — consistent across browsers, unlike the
  // native type=search cancel button (which Firefox lacks and WebKit hides until hover)
  const searchWrap = el("span.search-wrap");
  els.search.parentNode.insertBefore(searchWrap, els.search);
  const searchClear = el("button.search-clear", { type: "button", text: "×",
    title: t("search.clear", "Clear search"), "aria-label": t("search.clear", "Clear search") });
  searchWrap.append(els.search, searchClear);
  syncSearchClear = () => searchWrap.classList.toggle("has-text", !!els.search.value);
  searchClear.addEventListener("click", () => {
    els.search.value = ""; state.q = ""; syncSearchClear(); render(); els.search.focus();
  });
  syncSearchClear();

  let deb;
  els.search.addEventListener("input", () => {
    syncSearchClear();
    clearTimeout(deb);
    deb = setTimeout(() => { state.q = els.search.value.trim().toLowerCase(); render(); }, 120);
  });
  els.type.addEventListener("change", () => { state.type = els.type.value; render(); });
  els.cat.addEventListener("change", () => { state.category = els.cat.value; render(); });
  els.starFilter.addEventListener("click", () => { state.starred = !state.starred; render(); });

  document.querySelectorAll("th[data-sort]").forEach(th =>
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (state.sortKey === k) state.sortDir *= -1; else { state.sortKey = k; state.sortDir = 1; }
      syncSortArrows(th.closest("table"));
      render();
    }));

  els.footer.textContent = t("footer.credit",
    "All game data is extracted from TROUBLESHOOTER: Abandoned Children and remains the property of Dandylion. " +
    "Thank you for making such a wonderful game and supporting the community.");

  // init
  bldLoad();              // restore the autosaved builds before first render
  const tab = savedTab(); // restore the last active tab (selectTab runs populateTypes)
  if (tab) selectTab(tab); else populateTypes();
  render();
  bldLoadFromHash();                                   // a #build=… link overrides the tab/build
  window.addEventListener("hashchange", bldLoadFromHash);  // also handle links opened while running
  // browser Back/Forward: restore the snapshot a cross-link pushed. Seed the current entry so Back
  // right after the first jump lands on this initial state (rather than leaving the page).
  window.addEventListener("popstate", e => { if (e.state) applyState(e.state); });
  history.replaceState(captureState(), "");
})();
