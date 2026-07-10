"""
Map enemies -> the missions they appear in, with case type, recommended level and
a minimum-difficulty tag — mirroring the in-game "Appearance Location" panel.

Sources:
- xml/mission.xml   : each Mission has Stage="X.stage", Lv (recommended level) and a
                      LocationTitle. Several missions can share a stage.
- xml/ZoneEventGen.xml : authoritative case type per mission — Type in
                      {Scenario, Raid, Normal}. We map Raid->Violent, Normal->Ordinary.
- stage/*.stage     : <Enemy Key Object="Mon_.."> placements (the Hard roster);
                      <Trigger Group="Difficulty"> ExcludeUnit actions drop units on
                      lower difficulties, giving each enemy's minimum difficulty.

Case colour: Scenario=red, Ordinary=blue, Violent=purple. Side-quest stages (`Quest_*`
ids) are pulled out into a synthetic Quest=green category of our own (not an in-game case)
so they read apart from the main story — see `_classify_case`.
Missions with no resolvable case (JointTraining / test / PvP maps) are dropped.
"""
import os
import re
import glob
import xml.etree.ElementTree as ET

TIER_RANK = {"All": 0, "Normal+": 1, "Hard+": 2, "Dialog": 3}  # lower = more available
TEAM_ACTIONS = {"ChangeTeam", "UpdateObjectPropertyTeam", "UpdateObjectInstantPropertyTeam"}
# teams that are hostile to the player. A "third party" fights both sides, so its
# units are still defeatable -> their masteries are obtainable.
THIRD_TEAMS = {"ThirdForce", "Third", "third"}


def _is_hostile_team(team):
    return bool(team) and (team.startswith("enemy") or team in THIRD_TEAMS)


def _is_enemy_placement(team):
    """A unit in the <Enemy> tag is a real (defeatable) enemy unless it's on the player's own
    side or a *directing* team. Excluded:
    - `ally`/`player` — the player's side.
    - `Direct*` (`DirectTeam`/`Direct`/`Directeam`) — cutscene/directing actors that play a scripted
      bit (talk, retreat) and never engage you, e.g. Danny & Sharky in Pugo Street. The engine treats
      directing units as non-combatants regardless of relation. A directing unit that *does* later turn
      hostile is re-added via the ChangeTeam flip path below, so excluding the base placement is safe.
    Kept: `neutral*` teams are *third factions that fight everyone* (e.g. Raid_Pascal's released
    test-subject beasts — "the beasts can also attack us"), and any unit that merely *starts* on a
    hostile team and later retreats/defects still had a real combat window (Orsay's Tima, Luna/Marco),
    so those stay. Civilians are authored in <Neutral> tags / `Mon_Civil*` ids and filtered elsewhere."""
    if team is None:
        return True
    if team in ("ally", "Ally", "player", "Player"):
        return False
    if (team or "").lower().startswith("direct"):
        return False
    return True
GRADEUP_RE = re.compile(r"_(Elite|Epic|Legend)\d?$")
ZEG_CASE = {"Scenario": "Scenario", "Raid": "Violent", "Normal": "Ordinary"}


def _min_tier(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return a if TIER_RANK[a] <= TIER_RANK[b] else b


def _idspace(path, want):
    for sp in ET.parse(path).getroot().findall("idspace"):
        if sp.get("id") == want:
            return sp
    return None


def beast_hatching_pools(xml_dir):
    """{pool_name: [monster_id, ...]} from Beast.xml idspace `BeastHatching`.

    A stage `<DrakyEgg DrakyEggType="...">` generator (the *only* live off-roster spawn
    mechanism that introduces mastery-carriers — see DATAMINING.md "Enemies with no mission
    appearance") drops the pool's eggs (`<EggList>`), which hatch into one of the
    `<MonsterList>` forms (a random elemental Draki at its `Prob`, or the tameable hatchling).
    Neither the eggs nor the hatch results appear in the static `<Enemy>` roster, so a mission
    that places such a generator must source every pool member through here. `DrakyEggType`
    is optional and defaults to the plain `DrakyEgg` pool (e.g. Raid_DrakyNest2's 27 generators
    omit it). Members are base ids; grade-up folding happens in `missions_for`."""
    sp = _idspace(os.path.join(xml_dir, "Beast.xml"), "BeastHatching")
    pools = {}
    if sp is None:
        return pools
    for c in sp.findall("class"):
        ids = []
        for tag in ("EggList", "MonsterList"):
            el = c.find(tag)
            for p in (el.findall("property") if el is not None else []):
                v = p.get("value") or p.get("Type")
                if v:
                    ids.append(v)
        pools[c.get("name")] = ids
    return pools


def mission_summons(xml_dir):
    """{mission_id: [monster_id, ...]} for units a mission summons mid-battle — the per-mission
    `<Enemies>` manifest entries (the data behind the in-game "Appearance Location" panel) whose
    `BattleJoinType` is `SummonBeast`/`SummonMachine`. These join the fight but are NOT in the
    static stage `<Enemy>` roster, so the stage parser can't see them: e.g. Tutorial_Pascal's boss
    summons his three drones (SummonMachine), and several boss missions summon Legend beasts
    (Crimson Pig/Cloud/Fang, Blue Glutton/Flame/Teeth) via SummonBeast."""
    sp = _idspace(os.path.join(xml_dir, "mission.xml"), "Mission")
    out = {}
    if sp is None:
        return out
    for c in sp.findall("class"):
        en = c.find("Enemies")
        if en is None:
            continue
        ids = [p.get("Type") for p in en.findall("property")
               if (p.get("BattleJoinType") or "").startswith("Summon") and p.get("Type")]
        if ids:
            out[c.get("name")] = ids
    return out


def _classify_case(mid, objective_type, zeg_type):
    """Return Scenario | Quest | Ordinary | Violent | None for a mission."""
    # Side-quest battle stages (`Quest_*` ids — the NPC request chains) are their own category,
    # shown apart from the main story. This wins over every other signal, including a
    # ZoneEventGen tag or a `Hunting` objective, so *all* quest stages land in one bucket
    # regardless of the case colour the game would otherwise give them (e.g. Quest_Hundred01 /
    # Quest_Leo01 are `Type="Hunting"`, which used to fall through to Violent). In practice no
    # `Quest_` mission carries a ZoneEventGen entry, so ordering it first is safe either way.
    if mid.startswith("Quest_"):
        return "Quest"
    if zeg_type in ZEG_CASE:
        return ZEG_CASE[zeg_type]
    if objective_type == "Hunting":
        return "Violent"
    if mid.startswith("Raid_"):
        return "Violent"
    if mid.startswith("Common_"):
        return "Ordinary"
    if mid.startswith(("Tutorial_", "Scenario_")):
        return "Scenario"
    return None


def build_mission_index(xml_dir, dic):
    """Returns (mission_info, stage_to_missions) for classifiable missions.
       mission_info:     {mission_id: {"title","level","case"}}
       stage_to_missions: {"x.stage": [mission_id, ...]}
    """
    msp = _idspace(os.path.join(xml_dir, "mission.xml"), "Mission")
    zeg = _idspace(os.path.join(xml_dir, "ZoneEventGen.xml"), "ZoneEventGen")

    zeg_type = {}
    if zeg is not None:
        for c in zeg.findall("class"):
            if c.get("Mission"):
                zeg_type.setdefault(c.get("Mission"), c.get("Type"))

    mission_info = {}
    stage_to_missions = {}
    if msp is None:                              # mission.xml missing its Mission idspace — nothing to map
        return mission_info, stage_to_missions
    for c in msp.findall("class"):
        mid = c.get("name")
        case = _classify_case(mid, c.get("Type"), zeg_type.get(mid))
        if case is None:
            continue                                   # skip joint-training / test maps
        title = dic.get(f"Mission/{mid}/LocationTitle")
        if not title:
            continue                                   # unshipped dev/test stage (e.g. "nts" =
                                                       # new_test_stage) — no localized name
        try:
            level = int(c.get("Lv") or 0)
        except ValueError:
            level = 0
        mission_info[mid] = {
            "title": title,
            "level": level,
            "case": case,
        }
        stage = (c.get("Stage") or "").lower()
        if stage:
            stage_to_missions.setdefault(stage, []).append(mid)

    # scenario missions carry a story name + chapter (Troublebook), shown alongside the location —
    # you locate a replayable scenario by its name first. Other case types have only a location.
    scen = build_scenario_index(xml_dir, dic)
    for mid, info in mission_info.items():
        s = scen.get(mid)
        if s:
            info["scenario"] = s["scenario"]        # "Ch4 Scent of the Past" (chapter prefix + title)
            info["chapter"] = s["chapter"]
    return mission_info, stage_to_missions


def build_scenario_index(xml_dir, dic):
    """{mission_id: {"scenario","chapter"}} for Scenario missions, from Troublebook.xml — the
    in-game story log. Each <class> is a chapter (Order N, e.g. Trouble04); its <Stage> lists the
    scenario missions, whose localized name is `Troublebook/<chapter>/Stage/<1-based idx>/Title`.
    `scenario` is the compact "<chapter-prefix> <name>" (e.g. "Ch4 Scent of the Past" / kor
    "4장 …"); the location stays the mission's own LocationTitle."""
    sp = _idspace(os.path.join(xml_dir, "Troublebook.xml"), "Troublebook")
    out = {}
    if sp is None:
        return out
    pre = (lambda n: f"{n}장") if getattr(dic, "lang", "eng") != "eng" else (lambda n: f"Ch{n}")
    for ch in sp.findall("class"):
        order, chname, stg = ch.get("Order"), ch.get("name"), ch.find("Stage")
        if stg is None:
            continue
        for i, p in enumerate(stg.findall("property"), 1):
            mid = p.get("Mission") or p.get("Stage")
            title = dic.get(f"Troublebook/{chname}/Stage/{i}/Title")
            if mid and title:
                out[mid] = {"chapter": order, "scenario": f"{pre(order)} {title}"}
    return out


def build_enemy_missions(xml_dir, stage_dir, dic):
    """Returns (enemy_missions, mission_info, placed, enemy_dialog):
       enemy_missions: {monster_id: {mission_id: difficulty_tier}}
       mission_info:   {mission_id: {"title","level","case"}}  (only classifiable missions)
       placed:         set of monster_ids actually placed in a stage
       enemy_dialog:   {monster_id: {mission_id: set(trigger names)}}
    """
    mission_info, stage_to_missions = build_mission_index(xml_dir, dic)
    hatching_pools = beast_hatching_pools(xml_dir)

    enemy_missions = {}
    enemy_dialog = {}        # obj -> {mission_id: set(trigger names)}
    placed = set()
    for f in glob.glob(os.path.join(stage_dir, "*.stage")):
        missions = stage_to_missions.get(os.path.basename(f).lower())
        if not missions:
            continue
        try:
            r = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        parents = {c: p for p in r.iter() for c in p}
        enemy_keys = {e.get("Key"): e.get("Object")
                      for e in r.iter("Enemy")
                      if e.get("Key") and e.get("Object") and _is_enemy_placement(e.get("Team"))}
        # all placed units (any team) so we can resolve dialog-converted ones
        all_keys = {}
        for tag in ("Enemy", "Neutral", "Ally", "Unit"):
            for e in r.iter(tag):
                if e.get("Key") and e.get("Object"):
                    all_keys[e.get("Key")] = e.get("Object")
        # Draki egg generators: resolve each <DrakyEgg> through its BeastHatching pool — the
        # eggs and every hatch result appear here even though none are in the <Enemy> roster.
        drakyegg = set()
        for e in r.iter("DrakyEgg"):
            if _is_enemy_placement(e.get("Team")):
                drakyegg.update(hatching_pools.get(e.get("DrakyEggType") or "DrakyEgg", ()))
        if not enemy_keys and not all_keys and not drakyegg:
            continue
        # team-level conversions: a `TeamChangeTeam` action turns a *whole* team into another
        # (`Team`→`Team2`, unlike unit-level `ChangeTeam`). Compute the teams that become hostile to
        # the player — transitively converted into an enemy/Third team — so units placed on them, or
        # flipped onto them, count as real (if choice-gated) enemies. e.g. the training-room duel's
        # `DefenceTeam → enemy` makes Maximillion (flipped onto DefenceTeam) fightable; `→player`/
        # `→neutral` conversions never enter the set, so units that merely join you aren't counted.
        tct = {}
        for a in r.iter("Action"):
            if a.get("Type") == "TeamChangeTeam" and a.get("Team") and a.get("Team2"):
                tct.setdefault(a.get("Team"), set()).add(a.get("Team2"))
        hostile_teams = set()
        while True:
            grown = {s for s, ts in tct.items() if s not in hostile_teams
                     and any(_is_hostile_team(t) or t in hostile_teams for t in ts)}
            if not grown:
                break
            hostile_teams |= grown
        excl_easy, excl_normal = set(), set()
        for t in r.iter("Trigger"):
            if t.get("Group") != "Difficulty":
                continue
            dt = next((cond.get("DifficultyType") for cond in t.iter("Condition")
                       if cond.get("DifficultyType")), None)
            keys = [u.get("ObjectKey") for a in t.iter("Action")
                    if a.get("Type") == "ExcludeUnit" for u in a.findall("Unit")]
            if dt in ("Easy", "Safty"):
                excl_easy.update(keys)
            elif dt == "Normal":
                excl_normal.update(keys)

        stage_tier = {}
        for key, obj in enemy_keys.items():
            tier = "Hard+" if key in excl_normal else ("Normal+" if key in excl_easy else "All")
            stage_tier[obj] = _min_tier(stage_tier.get(obj), tier)
        for obj in drakyegg:            # egg/hatch-pool members appear at all difficulties
            stage_tier[obj] = _min_tier(stage_tier.get(obj), "All")
        for tag in ("Neutral", "Ally", "Unit"):   # units placed on a team that converts to hostile
            for e in r.iter(tag):                  # (<Enemy> placements on such teams are already in
                if e.get("Object") and e.get("Team") in hostile_teams:   # enemy_keys)
                    stage_tier[e.get("Object")] = _min_tier(stage_tier.get(e.get("Object")), "All")

        # dialog-conditional enemies: a non-<Enemy> unit flipped to a hostile team by a
        # ChangeTeam-style action (e.g. an NPC who fights you on a story choice). When the
        # enclosing trigger is gated by a variable a dialogue choice sets, we label it with
        # that choice's text; otherwise we fall back to the (humanized) trigger name.
        choice_index = _choice_index(r, dic)
        stage_dialog = {}        # obj -> set(labels)
        for a in r.iter("Action"):
            if a.get("Type") in TEAM_ACTIONS and (_is_hostile_team(a.get("Team"))
                                                  or a.get("Team") in hostile_teams):
                n = a
                while n is not None and n.tag != "Trigger":
                    n = parents.get(n)
                if n is None:
                    continue
                texts = set()
                for c in n.iter("Condition"):
                    if c.get("Type") == "VariableTest" and c.get("Operation") == "Equal" \
                            and c.get("Variable"):
                        texts |= choice_index.get((c.get("Variable"), c.get("Value")), set())
                label = "; ".join(sorted(texts)) if texts else _humanize(n.get("Name"))
                for u in a.iter("Unit"):
                    k = u.get("ObjectKey")
                    if k and k not in enemy_keys and k in all_keys:
                        obj = all_keys[k]
                        stage_tier[obj] = _min_tier(stage_tier.get(obj), "Dialog")
                        if label:
                            stage_dialog.setdefault(obj, set()).add(label)

        for obj, tier in stage_tier.items():
            placed.add(obj)
            slot = enemy_missions.setdefault(obj, {})
            for mid in missions:
                slot[mid] = _min_tier(slot.get(mid), tier)
        for obj, labels in stage_dialog.items():
            dslot = enemy_dialog.setdefault(obj, {})
            for mid in missions:
                dslot.setdefault(mid, set()).update(labels)

    # boss-summoned units (mission.xml <Enemies> BattleJoinType=Summon*) — they join mid-battle and
    # are absent from the static stage roster, so resolve them straight from the mission manifest.
    for mid, ids in mission_summons(xml_dir).items():
        if mid not in mission_info:
            continue
        for obj in ids:
            placed.add(obj)
            slot = enemy_missions.setdefault(obj, {})
            slot[mid] = _min_tier(slot.get(mid), "All")

    return enemy_missions, mission_info, placed, enemy_dialog


def missions_for(enemy_missions, placed, monster_id):
    """{mission_id: tier} for an id, folding grade-up variants onto their base."""
    if monster_id in enemy_missions:
        return enemy_missions[monster_id]
    base = GRADEUP_RE.sub("", monster_id)
    if base != monster_id and base in enemy_missions:
        return enemy_missions[base]
    return {}


def dialog_labels_for(enemy_dialog, monster_id, mid):
    """Readable dialog labels (choice text, or humanized trigger name) for an (enemy,
       mission), with grade-up fold. Labels are already resolved, so returned as-is."""
    slot = enemy_dialog.get(monster_id) or enemy_dialog.get(GRADEUP_RE.sub("", monster_id)) or {}
    return sorted(slot.get(mid, ()))


def _choice_index(root, dic):
    """{(variable, value): {choice text, ...}} — which dialogue choice sets each stage
       variable, so a variable-gated hostility trigger can name the choice that caused it."""
    idx = {}
    for dc in root.iter("DialogChoice"):
        for ch in dc.findall("Choice"):
            msg = ch.get("Message")
            text = dic.get(f"Sentence/{msg}/Value") if msg else None
            text = re.sub(r"\[[^\]]*\]", "", text).strip() if text else None
            al = ch.find("ActionList")
            for a in (al.findall("Action") if al is not None else []):
                if a.get("Type") == "UpdateStageVariable" and a.get("Variable") \
                        and a.get("Value") is not None:
                    idx.setdefault((a.get("Variable"), a.get("Value")), set())
                    if text:
                        idx[(a.get("Variable"), a.get("Value"))].add(text)
    return idx


def _humanize(name):
    name = re.sub(r"^(Mission_|Mission)", "", name)
    name = name.replace("_", " ")
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)   # split camelCase
    return name.strip()
