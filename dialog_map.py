"""
Extract dialogue decision points and their consequences from the stage files.

Model (verified): a <SelDialogBattle> action presents a <DialogChoice> of <Choice>
options. Each <Choice> has Message (its text) and an <ActionList> of immediate
effects, usually <Action Type="UpdateStageVariable" Variable=.. Value=..>. Triggers
fire on <Condition Type="VariableTest"> against those variables and run consequences
(ChangeTeam, party changes, rewards, ...). So:

    choice  --sets-->  variable  --tested by-->  trigger  --runs-->  consequence

We resolve the direct (in the choice's own ActionList) and 1-hop (variable-gated
trigger) consequences, categorised as fight / join / third-party / party / reward /
mission outcome. Pure camera/cutscene directives (MissionDirect) are ignored.
"""
import os
import re
import glob
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict

from missions import build_mission_index

MARKUP_RE = re.compile(r"\[[^\[\]]*\]")   # CEGUI markup: [colour='..'], [font='..'], ...

TEAM_ACTIONS = {"ChangeTeam", "UpdateObjectPropertyTeam", "UpdateObjectInstantPropertyTeam"}
JOIN_TEAMS = {"player", "ally", "Ally", "Troubleshooter"}
THIRD_TEAMS = {"ThirdForce", "Third", "third"}
# action types that represent a consequence worth surfacing
INTEREST = TEAM_ACTIONS | {"UpdateUserMember", "TeamUpdateUserMember", "GiveItem",
                           "GainItem", "MissionComplete", "MissionFail", "MissionClear",
                           "Win", "UnitAddBuff", "TeamAddBuff", "UpdateDashboard"}
# buffs that are purely cosmetic / UI and not worth showing
IGNORE_BUFFS = {"Guide_Circle"}
# condition types that test a battle/objective outcome (enemies wiped, a unit down, a location
# reached, a timer). A Win/Fail gated by one — directly, or via a variable a trigger sets under one
# — is the *objective* being met, not the choice's doing, so it isn't credited to the choice.
OBJECTIVE_COND = {"DashboardEvaluator", "TeamDestroy", "TeamDestroyInstant", "UnitDead",
                  "UnitDeadEvent", "AnyUnitDeadEvent", "TeamDeadEvent", "UnitArrived",
                  "TeamArrived", "UnitArrivedToUnit", "TeamArrivedToUnit",
                  "UnitArrivedToPositionHolderGroup"}


def _txt(dic, key):
    """Resolve a dialog text key; '' if it has no dictionary entry (untranslated)."""
    s = dic.get(f"Sentence/{key}/Value")
    if s is None:
        s = dic.get(key)
    if s is None:
        return ""
    return MARKUP_RE.sub("", s).strip()


def _L(dic, en, ko):
    """Pick the language for a generated consequence-badge phrase. The dialogue data is baked
    per-language (like mastery descriptions / scenario names), so badge phrasing is localized here
    at extraction; embedded unit/buff names already resolve through `dic`. The full *script* view
    stays English (out of scope)."""
    return ko if (getattr(dic, "lang", "eng") != "eng") else en


def _unit_namer(stage_root, monster_name):
    """key -> readable name, via the unit's Object id."""
    key2obj = {}
    for tag in ("Enemy", "Neutral", "Ally", "Unit"):
        for e in stage_root.iter(tag):
            if e.get("Key") and e.get("Object"):
                key2obj[e.get("Key")] = e.get("Object")

    def name(key):
        # a placed unit resolves via its Object id; otherwise resolve the bare key directly
        # (monster_name also tries ObjectInfo, so an unplaced player char like "Albus" localizes).
        return monster_name(key2obj.get(key) or key) if key else ""
    return name


def _objective_change(a, dic):
    """The new objective text if `a` is an UpdateDashboard action that rewrites the mission
    objective (a `<Command Value="UpdateObjectiveMessage"/>` followed by the new message key),
    else None. This is how the game swaps the objective mid-mission — e.g. Crimson Crow's hack
    terminals change it to 'Put all enemies Out of Action'. Not the `VictoryCondition` form the
    script renderer already knows."""
    if a.get("Type") != "UpdateDashboard":
        return None
    cmds = [c.get("Value") for c in a.findall("Command")]
    if not cmds or cmds[0] != "UpdateObjectiveMessage":
        return None
    for c in cmds[1:]:                        # the message key resolves; enum slots (MainObjective) don't
        txt = _txt(dic, c)
        if txt:
            return txt
    return None


def _describe(action, namer, dic):
    """Return (category, localized text) for a consequence badge, or None to skip."""
    t = action.get("Type")
    if t == "UnitAddBuff":
        if action.get("Name") in IGNORE_BUFFS:
            return None
        units = [u for u in dict.fromkeys(namer(u.get("ObjectKey"))
                 for u in action.iter("Unit") if u.get("ObjectKey")) if u]
        if not units:
            return None
        who, buff = ", ".join(units), _buffname(dic, action.get("Name"))
        return ("buff", _L(dic, f"{who} gains {buff}", f"{who} {buff} 획득"))
    if t == "TeamAddBuff":
        if action.get("Name") in IGNORE_BUFFS:
            return None
        team, buff = action.get("Team"), _buffname(dic, action.get("Name"))
        return ("buff", _L(dic, f"{team} team gains {buff}", f"{team}팀 {buff} 획득"))
    if t in TEAM_ACTIONS:
        team = action.get("Team") or ""
        units = [namer(u.get("ObjectKey")) for u in action.iter("Unit") if u.get("ObjectKey")]
        units = [u for u in dict.fromkeys(units) if u]      # unique, drop blanks
        if not units:
            return None                                     # group/expression target — can't name
        who = ", ".join(units)
        if team.startswith("enemy"):
            return ("fight", _L(dic, f"{who} turns hostile", f"{who} 적대"))
        if team in JOIN_TEAMS:
            return ("join", _L(dic, f"{who} joins you", f"{who} 합류"))
        if team in THIRD_TEAMS:
            return ("third", _L(dic, f"{who} turns hostile as a third party (fights both sides)",
                                f"{who} 제3세력으로 적대 (양측과 교전)"))
        return None
    if t in ("UpdateUserMember", "TeamUpdateUserMember"):
        units = [u for u in dict.fromkeys(namer(u.get("ObjectKey"))
                 for u in action.iter("Unit") if u.get("ObjectKey")) if u]
        who = ", ".join(units)
        on = action.get("OnOff") != "Off"
        joins = on and (action.get("Team") in ("player", "ally", "Ally")
                        or action.get("Team2") == "player")
        if joins:
            return ("join", _L(dic, f"{who} joins you" if units else "allied units join you",
                               f"{who} 합류" if units else "아군 합류"))
        if not on and action.get("Team") == "player":
            return ("leave", _L(dic, f"{who} leaves the party" if units else "a member leaves the party",
                                f"{who} 이탈" if units else "아군 이탈"))
        return None
    if t in ("GiveItem", "GainItem"):
        # the item(s) are nested: OpenReward_GiveItem > ItemCollection > Slot > Item[ItemType, Count]
        items = []
        for it in action.iter("Item"):
            itype = it.get("ItemType")
            if not itype:
                continue
            nm = dic.get(f"Item/{itype}/Base_Title") or itype.replace("_", " ")
            cnt = it.get("Count")
            items.append(f"{nm} ×{cnt}" if cnt and cnt != "1" else nm)
        if not items:
            return ("reward", _L(dic, "Gives an item reward", "아이템 보상"))
        joined = ", ".join(dict.fromkeys(items))
        return ("reward", _L(dic, f"Gives {joined}", f"{joined} 지급"))
    if t in ("MissionComplete", "MissionClear"):
        return ("mission", _L(dic, "Win the mission", "미션 승리"))
    if t == "Win":
        # `Win` ends the battle for the named Team — an enemy Win is the player's defeat
        # (player/empty Team = the player's win). Cf. _action's "end battle (victory/defeat)".
        team = action.get("Team")
        if team and team != "player":
            return ("mission", _L(dic, "Fail the mission", "미션 실패"))
        return ("mission", _L(dic, "Win the mission", "미션 승리"))
    if t in ("MissionFail", "Lose"):
        return ("mission", _L(dic, "Fail the mission", "미션 실패"))
    if t == "UpdateDashboard":
        obj = _objective_change(action, dic)
        return ("objective", _L(dic, f"New objective: {obj}", f"새 목표: {obj}")) if obj else None
    return None


def _timer_outcomes(stage_root):
    """TimeLimiter Key -> 'survival' (running out = player win) or 'deadline' (running out
    = a loss), by what the timer-expiry trigger does. A countdown is used for both: hold-out
    (time-up wins) and deadline (time-up loses), so we can't assume survival. Ties / unknown
    timers are left out. (Expiry = a DashboardEvaluator on the timer testing ElapsedTime vs
    LimitTime; the same trigger's Win Team="player" => survival, Lose/MissionFail or an enemy
    Win => deadline.)"""
    surv, dead = set(), set()
    for t in stage_root.iter("Trigger"):
        keys = {c.get("DashboardKey") for c in t.iter("Condition")
                if c.get("Type") == "DashboardEvaluator" and c.get("DashboardKey")
                and "ElapsedTime" in (c.get("SuccessExpression") or "")}
        if not keys:
            continue
        signalled = False
        for a in t.iter("Action"):
            ty, team = a.get("Type"), a.get("Team")
            if ty in ("Win", "MissionComplete", "MissionClear") and team in (None, "", "player"):
                surv |= keys; signalled = True
            elif ty in ("Lose", "MissionFail") or (ty == "Win" and team and team != "player"):
                dead |= keys; signalled = True
        if not signalled:                         # fall back to the expiry trigger's role
            n = (t.get("Name") or "").lower()
            if any(w in n for w in ("lose", "fail", "defeat")):
                dead |= keys
            elif "win" in n:
                surv |= keys
    return {k: ("deadline" if k in dead else "survival") for k in surv | dead}


def _mastery_title(dic, mid):
    return (dic.get(f"Mastery/{mid}/Base_Title") if dic else None) or mid


def _direct_mastery_grants(stage_root):
    """{MissionDirect Key -> set(mastery id)} for scenes that grant a mastery, via a
    <GameMessageForm Mastery=.. Type="MasteryAcquired"/> directive. Transitive: a scene that
    plays another granting scene (Action Type="MissionDirect") inherits its grants. This is the
    canonical 'this mission awards this mastery' marker — the gating trigger's choice variable
    then ties it to a dialogue choice (see mastery_grants / _gated_consequences)."""
    own, plays = {}, {}
    for md in stage_root.iter("MissionDirect"):
        k = md.get("Key")
        if not k:
            continue
        g = own.setdefault(k, set())
        for gm in md.iter("GameMessageForm"):
            # `MasteryAcquired` (the toast) and `MasteryAcquiredWho` (granted *to* a named char,
            # `Who=`, used by the Firefly Park opening tutorial's starting-mastery choices) both mark a grant.
            if gm.get("Type") in ("MasteryAcquired", "MasteryAcquiredWho") and gm.get("Mastery"):
                g.add(gm.get("Mastery"))
        for a in md.iter("Action"):
            if a.get("Type") == "MissionDirect" and a.get("DirectType"):
                plays.setdefault(k, set()).add(a.get("DirectType"))
    if not any(own.values()):
        return {}

    def reach(k, seen):
        if k in seen:
            return set()
        seen.add(k)
        out = set(own.get(k, ()))
        for c in plays.get(k, ()):
            out |= reach(c, seen)
        return out

    return {k: r for k in own if (r := reach(k, set()))}


def _choice_setters(stage_root, dic):
    """(choice_text, choice_vars): {(variable, value): choice message} for every dialogue choice
    that sets a stage variable, and the set of variables choices set. A variable that a standalone
    trigger action also sets is *progression state* (e.g. EventID, advanced after each battle), not
    a character selector — even if a choice nudges it too — so it is excluded here and left to the
    state-tracing path instead (otherwise a reward's EventID gate reads as a spurious choice)."""
    trigger_set = {a.get("Variable") for tr in stage_root.iter("Trigger")
                   for a in tr.iter("Action")
                   if a.get("Type") == "UpdateStageVariable" and a.get("Variable")}
    choice_text = {}
    for ch in stage_root.iter("Choice"):
        txt = _txt(dic, ch.get("Message")) if ch.get("Message") else ""
        if not txt:
            continue
        al = ch.find("ActionList")
        for a in (al.findall("Action") if al is not None else []):
            v = a.get("Variable")
            if a.get("Type") == "UpdateStageVariable" and v and v not in trigger_set:
                choice_text.setdefault((v, a.get("Value")), txt)
    return choice_text, {v for (v, _) in choice_text}


def _choice_var_producers(stage_root, choice_vars):
    """{stateVar: {choiceVar: {choiceVal, …}}}: for each *non-choice* variable a trigger sets, the
    choice condition(s) the setting trigger is gated on — aggregated across *all* of that variable's
    set-values and producing triggers. A variable that is only ever set under one choice value
    (e.g. Irene_Win, set solely when PlayerSelect==3) is character-specific and safe to trace; a
    shared progress counter (EventID, set under many choices) lists them all and is then ignored."""
    prod = {}
    for tr in stage_root.iter("Trigger"):
        cc = {}
        for c in tr.iter("Condition"):
            if c.get("Type") == "VariableTest" and c.get("Operation") == "Equal" \
                    and c.get("Variable") in choice_vars:
                cc.setdefault(c.get("Variable"), set()).add(c.get("Value"))
        if not cc:
            continue
        for a in tr.iter("Action"):
            if a.get("Type") == "UpdateStageVariable" and a.get("Variable") \
                    and a.get("Variable") not in choice_vars:
                agg = prod.setdefault(a.get("Variable"), {})
                for cv, vals in cc.items():
                    agg.setdefault(cv, set()).update(vals)
    return prod


def _trigger_choice_keys(tr, choice_text, choice_vars, producers):
    """The (choiceVar, choiceVal) keys that gate a trigger — the dialogue choice(s) it depends on.
    A *direct* choice condition counts; but a *reliable derived* one overrides it: if the trigger
    also requires a state variable that is **only ever set under one value** of a choice variable,
    that value wins over a conflicting direct condition. This corrects misleading direct conditions
    like Sky-wind park's Irene reward, which directly tests PlayerSelect==2 (Sion — looks copied
    from the Sion reward block) yet also requires Irene_Win, a state set only when PlayerSelect==3
    (Irene). The PlayerSelect==2 is a leftover bug — you pick one character from a paging menu, so
    once Irene is chosen PlayerSelect stays 3 and ==2 can never hold — but the reward is still earned
    on Irene in-game; the reliable *determinant* is Irene_Win, so we key on that. A shared counter
    (EventID, set under every character choice + by battle triggers) is *not* choice-bound, so it
    never overrides."""
    chosen, derived = {}, {}
    for c in tr.iter("Condition"):
        if c.get("Type") != "VariableTest" or c.get("Operation") != "Equal":
            continue
        var, val = c.get("Variable"), c.get("Value")
        if var in choice_vars:
            chosen.setdefault(var, val)                      # direct choice condition
        else:
            for cv, vals in (producers.get(var) or {}).items():
                if len(vals) == 1:                           # var is choice-bound → reliable
                    derived.setdefault(cv, set()).update(vals)
    for cv, vals in derived.items():
        if len(vals) == 1:                                   # one implied value → override direct
            chosen[cv] = next(iter(vals))
    return [(cv, v) for cv, v in chosen.items() if (cv, v) in choice_text]


def _gated_consequences(stage_root, namer, dic, grants=None, opens_entry=None):
    """Map (variable, value) -> [(category, text)] from variable-gated triggers.
    `grants` ({MissionDirect Key -> set(mastery id)}, from _direct_mastery_grants) lets a
    trigger that plays a mastery-granting scene surface as a 'mastery' consequence.
    `opens_entry` (this stage's parse_mission_opens record) adds an 'opened' consequence beside
    each grant — the branch's other group members, which become craftable on that choice without a
    copy. Only applied when the mission's branches are gated purely on dialogue-choice variables
    (branch_vars ⊆ choice_vars); outcome-gated missions (Sky-wind park) are left to their own tier."""
    # countdown timers a choice can switch on — survival (hold-out) vs deadline (time-up loses)
    timers = {d.get("Key"): (d.get("LimitTime"), _txt(dic, d.get("Message") or ""))
              for d in stage_root.iter("Dashboard") if d.get("Type") == "TimeLimiter"}
    timer_kind = _timer_outcomes(stage_root)
    # choice info for re-keying mastery grants to the *correct* choice (see _trigger_choice_keys)
    choice_text, choice_vars = _choice_setters(stage_root, dic)
    producers = _choice_var_producers(stage_root, choice_vars) if grants else {}
    # opened-for-research companions, but only for choice-gated missions (see docstring)
    companions = (opens_entry["companions"]
                  if opens_entry and opens_entry["branch_vars"] <= choice_vars else {})
    # objective-progress variables: a variable a trigger sets *while gated on an objective event* is
    # itself an objective flag (e.g. Road_111 sets Win=1 only when all fences are repaired / the team
    # retreats). A Win/Fail gated on one is the objective being met, not the choice — so it's not
    # credited (this is what stops "Win the mission" appearing on every choice that merely advances
    # toward a later victory). Choice selectors are excluded (a choice's own var isn't an objective).
    objective_vars = {a.get("Variable") for tr in stage_root.iter("Trigger")
                      if any(c.get("Type") in OBJECTIVE_COND for c in tr.iter("Condition"))
                      for a in tr.iter("Action")
                      if a.get("Type") in SET_VAR and a.get("Variable")} - choice_vars
    out = {}
    for t in stage_root.iter("Trigger"):
        cond_types = {c.get("Type") for c in t.iter("Condition")}
        # a Win gated by a battle/objective event (directly, or via an objective-progress variable)
        # is the objective, not the choice's doing — so don't credit the choice with the outcome
        objective_gated = bool(cond_types & OBJECTIVE_COND) or any(
            c.get("Type") == "VariableTest" and c.get("Variable") in objective_vars
            for c in t.iter("Condition"))
        descs = []
        mastery_descs = []        # keyed to the corrected gating choice, not raw conditions
        for a in t.iter("Action"):
            ty = a.get("Type")
            if ty in INTEREST:
                d = _describe(a, namer, dic)
                if d and not (d[0] == "mission" and objective_gated):
                    descs.append(d)
            elif ty == "MissionDirect" and grants and a.get("DirectType") in grants:
                for mid in sorted(grants[a.get("DirectType")]):
                    title = _mastery_title(dic, mid)   # app.js re-renders via dlg.grants; localize for search/fallback
                    mastery_descs.append(("mastery", _L(dic, f"Grants {title}", f"{title} 획득"), mid))
            elif ty == "UpdateDashboard" and a.get("DashboardKey") in timers:
                cmds = {c.get("Value") for c in a.iter("Command")}
                if cmds & {"Activate", "Show"}:
                    key = a.get("DashboardKey")
                    limit, msg = timers[key]
                    detail = f"{msg}: {limit}" if msg else _L(dic, f"limit {limit}", f"제한 {limit}")
                    kind = timer_kind.get(key)
                    if kind == "survival":
                        label = _L(dic, f"Switches objective to survival ({detail})",
                                   f"목표를 생존으로 전환 ({detail})")
                    elif kind == "deadline":
                        label = _L(dic, f"Adds a time limit — lose if it runs out ({detail})",
                                   f"제한 시간 추가 — 시간 초과 시 패배 ({detail})")
                    else:
                        label = _L(dic, f"Starts a timer ({detail})", f"타이머 시작 ({detail})")
                    descs.append(("mission", label))
        # non-mastery consequences key off every variable condition (existing behaviour);
        # mastery grants key off the corrected gating choice only, so a typo'd direct condition
        # (Sky-wind park's Irene reward) doesn't credit the wrong character's choice.
        if descs:
            for c in t.iter("Condition"):
                if c.get("Type") == "VariableTest" and c.get("Operation") == "Equal":
                    out.setdefault((c.get("Variable"), c.get("Value")), []).extend(descs)
        if mastery_descs:
            for key in _trigger_choice_keys(t, choice_text, choice_vars, producers):
                out.setdefault(key, []).extend(mastery_descs)
    # opened-for-research companions, added per choice once every grant is placed: the choice that
    # awards a group member opens the *other* members. Suppress any companion already granted under
    # the same choice — some stages credit two group members to one option (Hansol St's Heixing pick),
    # where "opens X" beside "grants X" would be redundant/misleading.
    if companions:
        for descs in out.values():
            granted = {d[2] for d in descs if d[0] == "mastery" and len(d) > 2}
            shown = set()
            for gmid in sorted(granted):
                for cid in sorted(companions.get(gmid, ())):
                    if cid in granted or cid in shown:
                        continue
                    shown.add(cid)
                    ct = _mastery_title(dic, cid)
                    descs.append(("opened", _L(dic, f"Opens {ct} for research", f"{ct} 연구 해금"), cid))
    return out


OP = {"Equal": "==", "NotEqual": "!=", "GreaterThan": ">", "LessThan": "<",
      "GreaterThanOrEqual": ">=", "LessThanOrEqual": "<="}


def _ckunits(e, namer):
    """Names of the units a condition/action targets, deduped in order. Each <Unit> resolves via
    its ObjectKey (a placed unit) or, failing that, its GameObject type ref — e.g.
    GameObject="PC_Sion" (which carries no ObjectKey) -> the localized "Sion", so conditions like
    UnitAlive name the character instead of a bare "unit"."""
    out = []
    for u in e.iter("Unit"):
        if u.get("ObjectKey"):
            out.append(namer(u.get("ObjectKey")))
        elif u.get("GameObject"):
            out.append(namer(re.sub(r"^(Mon_PC_|PC_|Mon_)", "", u.get("GameObject"))))
    return ", ".join(dict.fromkeys(x for x in out if x)) or ""


def _buffname(dic, name):
    return dic.get(f"Buff/{name}/Title") or name or "?"


# insight/spot conditions: (who-spots, who-gets-spotted), each a child tag or @attr
INSIGHT_ROLES = {
    "UnitInsightToUnit": ("SearchUnit", "TargetUnit"),
    "TeamInsightToUnit": ("@Team", "Unit"),
    "UnitInsightToTeam": ("Unit", "@Team"),
    "TeamInsightToTeam": ("@Team", "@Team2"),
}


def _insight_who(c, role, namer):
    if role.startswith("@"):
        v = c.get(role[1:])
        return f"the {v} team" if v else "a team"
    e = c.find(role)
    if e is None:
        return "someone"
    if e.get("ObjectKey"):
        return namer(e.get("ObjectKey"))
    if e.get("GameObject"):
        return re.sub(r"^(Mon_PC_|PC_|Mon_)", "", e.get("GameObject")).replace("_", " ")
    return "a unit"


def _filter_desc(sf, dic):
    """A readable description of an insight SearchUnitFilter's targeted unit, or None.
       Affiliation codes are resolved to their organization title (Spoon -> Spoonism)."""
    if not sf or sf.strip() in ("", "true"):
        return None
    info_eq = re.findall(r"Info\.name\s*==\s*'([^']+)'", sf)
    info_neq = re.findall(r"Info\.name\s*~=\s*'([^']+)'", sf)
    aff_eq = re.findall(r"Affiliation\.name\s*==\s*'([^']+)'", sf)
    clean = lambda n: n.split("_")[-1]
    if aff_eq:
        org = (dic.get(f"Organization/{aff_eq[0]}/Title") if dic else None) or aff_eq[0]
        d = f"a {org} member"
        if info_neq:
            d += f" (not {', '.join(clean(x) for x in info_neq)})"
        return d
    if info_eq:
        return clean(info_eq[0])
    if info_neq:
        return f"a unit (not {', '.join(clean(x) for x in info_neq)})"
    return None


def _insight_parts(c, namer, dic):
    """(spotter, spotted) for an insight condition. The type name implies a default
       direction, but ConditionOutput is authoritative: for a *ToTeam check whose Finder
       is the searched team-member (Finder="Search") and which names a concrete <Unit>,
       the team-member is the spotter and that <Unit> is the one spotted (the type name
       has it backwards). The SearchUnitFilter describes the team-member."""
    srole, trole = INSIGHT_ROLES[c.get("Type")]
    spotter = _insight_who(c, srole, namer)
    spotted = _insight_who(c, trole, namer)
    co = c.find("ConditionOutput")
    u = c.find("Unit")
    if c.get("Type") == "UnitInsightToTeam" and co is not None and co.get("Finder") == "Search" \
            and u is not None and u.get("ObjectKey"):
        spotter = _filter_desc(c.get("SearchUnitFilter"), dic) \
            or (f"a {c.get('Team')} unit" if c.get("Team") else "a unit")
        spotted = namer(u.get("ObjectKey"))
    return spotter, spotted


# --- condition folding: collapse the verbose multi-unit AND/OR groups the game data
# emits (the same unit/predicate repeated per instance) into a compact, readable form.
def _unit_subject(ch, namer, dic):
    """The leading subject _cond renders for a unit condition (or None)."""
    t = ch.get("Type")
    if t in INSIGHT_ROLES:
        return _insight_parts(ch, namer, dic)[0]
    return _ckunits(ch, namer) or None


def _unit_atom(ch, namer, dic):
    """(subject, predicate, relation, object) if ch is one unit+predicate condition,
       else None. relation/object are filled for relational predicates (X spots Y /
       X moves next to Y) so the object side can be folded like the subject side."""
    t = ch.get("Type")
    if t in ("And", "Or", "VariableTest", "VariableToVariableTest"):
        return None
    name = _unit_subject(ch, namer, dic)
    if not name:
        return None
    s = _cond(ch, namer, dic)
    if not s.startswith(name):
        return None
    pred = s[len(name):].strip()
    rel = obj = None
    if t in INSIGHT_ROLES and pred.startswith("spots "):
        rel, obj = "spots", pred[len("spots "):].strip()
    elif t in ("UnitArrivedToUnit", "TeamArrivedToUnit") and pred.startswith("moves next to "):
        o = pred[len("moves next to "):].strip()
        if o and o != "target":
            rel, obj = "moves next to", o
    return (name, pred, rel, obj)


def _pure_unit_group(c, namer, dic):
    """The atoms of an And/Or whose children are all unit+predicate conditions, else None."""
    if c.get("Type") not in ("And", "Or"):
        return None
    atoms = []
    for ch in c:
        if ch.tag != "Condition":
            continue
        a = _unit_atom(ch, namer, dic)
        if not a:
            return None
        atoms.append(a)
    return atoms or None


def _fmt_units(names):
    return ", ".join(f"{n} x{c}" if c > 1 else n for n, c in names)


def _union_names(clauses):
    cnt = OrderedDict()
    for cl in clauses:
        for n, c in cl["names"]:
            cnt[n] = cnt.get(n, 0) + c
    return sorted(cnt.items())


def _fold_preds(plist, conn):
    """Fold one subject's predicates: unary ones kept as-is; relational ones sharing a
       relation collapse their objects into 'relation <all/any> of {objects}'."""
    quant = "any of" if conn == "Or" else "all of"
    unary, rel_objs = [], OrderedDict()
    for pred, rel, obj in plist:
        if rel and obj:
            rel_objs.setdefault(rel, []).append(obj)
        elif pred not in unary:
            unary.append(pred)
    out = list(unary)
    for rel, objs in rel_objs.items():
        # distinct objects only — object repetition here just mirrors subject instances
        # (already counted on the subject side), so it must not produce an object xN
        distinct = list(dict.fromkeys(objs))
        if len(distinct) > 1:
            out.append(f"{rel} {quant} {{{', '.join(sorted(distinct))}}}")
        else:
            out.append(f"{rel} {distinct[0]}")
    return out


def _unit_clauses(atoms, conn):
    """Group a pool of (subject, predicate, relation, object) atoms (under connective
       conn) by shared predicate set, folding each subject's predicates (incl. relational
       objects) and collapsing repeated instances to xN. -> list of clause dicts."""
    by = OrderedDict()
    for n, pred, rel, obj in atoms:
        by.setdefault(n, []).append((pred, rel, obj))
    info = OrderedDict()
    for n, plist in by.items():
        cnt = max(Counter(p for p, _, _ in plist).values())
        info[n] = (cnt, tuple(_fold_preds(plist, conn)))
    groups = OrderedDict()
    for n, (cnt, ps) in info.items():
        groups.setdefault(ps, []).append((n, cnt))
    # conn joins the predicates; uconn quantifies the units (may differ after a merge)
    return [{"names": sorted(nm), "preds": ps, "conn": conn, "uconn": conn}
            for ps, nm in groups.items()]


def _is_multiunit(cl):
    return len(cl["names"]) > 1 or cl["names"][0][1] > 1


def _fmt_clause(cl):
    join = " and " if cl["conn"] == "And" else " or "
    uword = "all of" if cl.get("uconn", cl["conn"]) == "And" else "any of"
    nstr, pstr = _fmt_units(cl["names"]), join.join(cl["preds"])
    if _is_multiunit(cl):
        return f"({uword} {{{nstr}}}: {pstr})"
    if len(cl["preds"]) > 1:
        return f"({nstr}: {pstr})"               # one unit, several predicates
    return f"{nstr} {pstr}"                       # one unit, one predicate


def _fmt_merged(names, clauses):
    parts = [f"{'all' if cl['conn'] == 'And' else 'any'} "
             f"({(' and ' if cl['conn'] == 'And' else ' or ').join(cl['preds'])})"
             for cl in clauses]
    return f"(of {{{_fmt_units(names)}}}: " + ", ".join(parts) + ")"


def _extract_adjacency(atoms):
    """Collapse mutual 'A moves next to B' + 'B moves next to A' pairs (the trigger fires
       when either unit reaches the other) into a single 'A is next to B'. Returns
       (phrases, remaining_atoms)."""
    phrases, used = [], set()
    for i, a in enumerate(atoms):
        if i in used or a[2] != "moves next to":
            continue
        for j in range(i + 1, len(atoms)):
            b = atoms[j]
            if j not in used and b[2] == "moves next to" \
                    and b[0] == a[3] and b[3] == a[0] and a[0] != a[3]:
                lo, hi = sorted((a[0], a[3]))
                phrases.append(f"{lo} is next to {hi}")
                used |= {i, j}
                break
    return phrases, [a for k, a in enumerate(atoms) if k not in used]


def _fold_group(c, t, namer, dic):
    direct, mergeable, leaves = [], [], []
    for ch in c:
        if ch.tag != "Condition":
            continue
        ug = _pure_unit_group(ch, namer, dic)
        if ug is not None:
            if ch.get("Type") == t:
                direct.extend(ug)                 # same quantifier -> pool the atoms
            else:
                # a different-connective subgroup: it may cross-merge with siblings only
                # if it folds to a SINGLE clause (its quantifier then carries the
                # connective). If it folds to several clauses, flattening would turn its
                # inner or/and into the parent's, so keep it wrapped as its own term.
                gc = ch.get("Type")
                adj, rest = _extract_adjacency(ug)
                sub = _unit_clauses(rest, gc)
                terms = adj + [_fmt_clause(x) for x in sub]
                if len(terms) == 1 and not adj and len(sub) == 1:
                    mergeable.append(sub[0])      # lone foldable clause -> can cross-merge
                elif len(terms) == 1:
                    leaves.append(terms[0])       # lone term (e.g. an adjacency phrase)
                else:
                    join = " and " if gc == "And" else " or "
                    leaves.append("(" + join.join(terms) + ")")
            continue
        a = _unit_atom(ch, namer, dic)
        if a:
            direct.append(a)
            continue
        s = _cond(ch, namer, dic)                 # nested mixed group / non-unit term
        if s:
            leaves.append(s)
    adj_direct, direct = _extract_adjacency(direct)   # mutual adjacency among pooled atoms
    leaves = adj_direct + leaves
    clauses = _unit_clauses(direct, t) + mergeable
    # predicate-set merge: sibling clauses with the same predicates but different units
    # combine into one, with the units quantified by the parent connective t
    porder, bypred = [], OrderedDict()
    for cl in clauses:
        key = (cl["preds"], cl["conn"])
        if key not in bypred:
            porder.append(key)
        bypred.setdefault(key, []).append(cl)
    clauses = []
    for key in porder:
        grp = bypred[key]
        if len(grp) > 1:
            clauses.append({"names": _union_names(grp), "preds": grp[0]["preds"],
                            "conn": grp[0]["conn"], "uconn": t})
        else:
            clauses.append(grp[0])
    # group clauses by unit set; merge a recurring multi-unit set into "of {..}: all (..), any (..)"
    order, byset = [], OrderedDict()
    for cl in clauses:
        key = tuple(cl["names"])
        if key not in byset:
            order.append(key)
        byset.setdefault(key, []).append(cl)
    rendered = []
    for key in order:
        grp = byset[key]
        if len(grp) > 1 and _is_multiunit(grp[0]):
            rendered.append(_fmt_merged(grp[0]["names"], grp))
        else:
            rendered.extend(_fmt_clause(cl) for cl in grp)
    parts = rendered + leaves
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]                          # single self-contained term — no extra parens
    return "(" + (f" {t.lower()} ").join(parts) + ")"


def _factor_or(c, namer, dic):
    """Distributive factoring: when every branch of an OR is an AND sharing a common
       conjunct, pull it out — (A and C) or (B and C)  ->  C and (A or B). Returns the
       rendered string, or None if it doesn't apply."""
    kids = [k for k in c if k.tag == "Condition"]
    if len(kids) < 2 or any(k.get("Type") != "And" for k in kids):
        return None
    branches = [[(_cond(ch, namer, dic), ch) for ch in k if ch.tag == "Condition"] for k in kids]
    common = set.intersection(*[{txt for txt, _ in b} for b in branches])
    if not common:
        return None
    common_elems, seen = [], set()
    for txt, ch in branches[0]:                          # ordered, de-duped common conjuncts
        if txt in common and txt not in seen:
            seen.add(txt)
            common_elems.append(ch)
    residuals = [[ch for txt, ch in b if txt not in common] for b in branches]
    if len(common_elems) == 1:
        common_str = _cond(common_elems[0], namer, dic)
    else:
        ae = ET.Element("Condition", {"Type": "And"})
        ae.extend(common_elems)
        common_str = _cond(ae, namer, dic)
        if common_str.startswith("(") and common_str.endswith(")"):
            common_str = common_str[1:-1]                # strip outer parens; re-wrapped below
    parts = [common_str]
    if all(residuals):                                   # a branch equal to the common makes
        uniq, seent = [], set()                          # the OR redundant -> common alone
        for res in residuals:
            key = tuple(sorted(_cond(x, namer, dic) for x in res))
            if key not in seent:
                seent.add(key)
                uniq.append(res)
        or_el = ET.Element("Condition", {"Type": "Or"})
        for res in uniq:
            if len(res) == 1:
                or_el.append(res[0])
            else:
                ae = ET.Element("Condition", {"Type": "And"})
                ae.extend(res)
                or_el.append(ae)
        parts.append(_cond(or_el, namer, dic))
    return parts[0] if len(parts) == 1 else "(" + " and ".join(parts) + ")"


# Condition types whose own branch already accounts for `_Reverse` by changing the wording
# (not a plain "not (…)" wrap), so the top-level negation in _cond must skip them.
_REVERSE_SELFHANDLED = {"CompanyEvaluatorAll", "CompanyEvaluatorCount"}


def _cond(c, namer, dic):
    """Render a condition. `_Reverse="true"` negates it — most branches don't encode that, so
    apply it as a top-level "not (…)" here (a single fallback), except for the few types that
    already fold their own polarity into the wording (see _REVERSE_SELFHANDLED)."""
    s = _cond_body(c, namer, dic)
    if s and c.get("_Reverse") == "true" and c.get("Type") not in _REVERSE_SELFHANDLED:
        # a folded group is already fully parenthesised — don't double-wrap it
        s = f"not {s}" if s[:1] == "(" and s[-1:] == ")" else f"not ({s})"
    return s


def _cond_body(c, namer, dic):
    t = c.get("Type")
    if t == "VariableTest":
        return f"{c.get('Variable')} {OP.get(c.get('Operation'), c.get('Operation') or '?')} {c.get('Value')}"
    if t == "VariableToVariableTest":
        return f"{c.get('Variable')} {OP.get(c.get('Operation'), '?')} {c.get('TargetVariable') or c.get('Variable2')}"
    if t == "Or":
        factored = _factor_or(c, namer, dic)
        if factored is not None:
            return factored
    if t in ("And", "Or"):
        return _fold_group(c, t, namer, dic)
    if t == "TeamDestroy":
        return f"all {c.get('Team')} defeated"
    if t == "UnitDead":
        return f"{_ckunits(c, namer) or 'unit'} is dead"
    if t == "UnitDeadEvent":
        return f"{_ckunits(c, namer) or 'unit'} dies"
    if t == "UnitAlive":
        return f"{_ckunits(c, namer) or 'unit'} alive"
    if t in ("UnitArrivedToUnit", "TeamArrivedToUnit"):
        who = _ckunits(c, namer) or c.get("Team") or "unit"
        tgt_el = c.find("Unit2") if c.find("Unit2") is not None else c.find("TargetUnit")
        tgt = namer(tgt_el.get("ObjectKey")) if tgt_el is not None and tgt_el.get("ObjectKey") else None
        return f"{who} moves next to {tgt}" if tgt else f"{who} moves next to target"
    if t == "TeamArrived":
        # a *team*-scoped arrival: the Team is the subject. The <Unit> is a positional anchor,
        # not the arriving unit (e.g. Sky-wind park's 8 Lose_Away escape checks all anchor on
        # "Sion" while testing whether an `enemy` beast reaches the edge), so don't name it.
        return f"{c.get('Team') or 'a'} team reaches a location"
    if t == "UnitArrived":
        return f"{_ckunits(c, namer) or c.get('Team') or 'a unit'} reaches a location"
    if t in INSIGHT_ROLES:
        spotter, spotted = _insight_parts(c, namer, dic)
        return f"{spotter} spots {spotted}"
    if t == "DifficultyTestEx":
        return f"difficulty {OP.get(c.get('Operation'), '')} {c.get('DifficultyType')}"
    if t == "DashboardEvaluator":
        return c.get("SuccessExpression") or "objective/timer met"
    if t == "MissionBegin":
        return "mission start"
    if t in ("UnitTurnStart", "TeamTurnStart"):
        return f"{_ckunits(c, namer) or c.get('Team') or ''} turn start".strip()
    if t in ("UnitBuffState", "UnitBattleStateTest"):
        who = _ckunits(c, namer) or c.get("Team") or "unit"
        bn = c.get("BuffName")
        if bn:
            return f"{who} {'lacks' if c.get('OnOff') == 'Off' else 'has'} {_buffname(dic, bn)}"
        if c.get("BattleState") is not None:
            in_battle = (c.get("BattleState") != "false") != (c.get("OnOff") == "Off")
            return f"{who} {'in' if in_battle else 'out of'} battle"
        return f"{who} state"
    # variable test variant (instantaneous) — same surface form as VariableTest
    if t == "VariableTestInstant":
        return f"{c.get('Variable')} {OP.get(c.get('Operation'), c.get('Operation') or '?')} {c.get('Value')}"
    # arrival / position
    if t in ("UnitArrived2",):
        return f"{_ckunits(c, namer) or 'a unit'} reaches a location"
    if t == "TeamArrived2":
        return f"{c.get('Team')} team reaches a location"
    if t == "UnitArrivedToPositionHolderGroup":
        return f"{_ckunits(c, namer) or 'a unit'} reaches {c.get('PosHolderGroup')}"
    # deaths / destruction
    if t == "AnyUnitDeadEvent":
        return "any unit dies"
    if t == "TeamDeadEvent":
        return f"a {c.get('Team')} unit dies"
    if t == "TeamDestroyInstant":
        return f"all {c.get('Team')} defeated"
    # attacks
    if t == "UnitAttacked":
        return f"{_ckunits(c, namer) or ('a ' + c.get('Relation') if c.get('Relation') else 'a unit')} is attacked"
    if t == "TeamAttacked":
        return f"{c.get('Team')} team is attacked"
    if t == "TeamAttackedToUnit":
        return f"{c.get('Team')} team attacks"
    # HP / buffs
    if t == "UnitHPTest":
        return f"{_ckunits(c, namer) or 'unit'} HP {OP.get(c.get('Operation'), c.get('Operation') or '?')} {c.get('Value')}%"
    if t == "UnitBuffStateEvent":
        return f"{_ckunits(c, namer) or 'unit'} {'gains' if c.get('OnOff') != 'Off' else 'loses'} {_buffname(dic, c.get('BuffName'))}"
    if t == "TeamBuffAdded":
        return f"{c.get('Team')} team gains {_buffname(dic, c.get('BuffName'))}"
    # spotting / insight variants not in INSIGHT_ROLES
    if t == "UnitInsightEachOther":
        return f"{_ckunits(c, namer) or 'a unit'} and a unit notice each other"
    if t == "AreaInsightToUnit":
        return f"an area reveals {_ckunits(c, namer) or 'a unit'}"
    if t == "TeamInsightToTeamEx":
        return f"{c.get('Team')} team spots {c.get('Team2')} team"
    # spatial count: count of <Relation> units within <Range> of <unit> <op> <Value>
    if t == "NearUnitCountTest":
        return (f"{c.get('Relation')} units within {c.get('Range')} of "
                f"{_ckunits(c, namer) or 'a unit'} "
                f"{OP.get(c.get('Operation'), c.get('Operation') or '?')} {c.get('Value')}")
    if t == "NoEnemyToTeam":
        return f"no enemy near {c.get('Team')} team"
    # interactions / misc
    if t in ("ObjectInteractionEvent", "ObjectInteractionOccured"):
        return f"{c.get('Interaction')} interaction"
    if t == "AbilityUse":
        return "an ability is used"
    if t == "UnitTurnReached":
        return f"{_ckunits(c, namer) or 'a unit'} reaches turn {c.get('TurnCount')}"
    if t == "UnitLastKill":
        return "final blow"
    if t == "BeastTamedAny":
        return f"{(c.get('BeastKey') or 'a beast').replace('_', ' ')} tamed"
    if t == "ChallengerTest":
        return "a challenger is present"
    if t == "MissionEnd":
        return "mission ends"
    # items / company / quests
    if t == "TeamItemAcquired":
        it = c.get("ItemType") or "item"
        return f"{c.get('Team')} obtains {dic.get(f'Item/{it}/Title') or it.replace('_', ' ')}"
    if t in ("CompanyEvaluatorAll", "CompanyEvaluatorCount"):
        expr = c.get("SuccessExpression") or ""
        rev = c.get("_Reverse") == "true"
        # `company.{Technique,CompanyMasteries}.<id>.Opened` is the company's acquired-state flag
        # for that (company) mastery — almost always used (reversed) as a "don't grant it twice"
        # guard. Render it with the mastery's name and the polarity applied, not the raw expression.
        m = re.match(r"company\.(?:Technique|CompanyMasteries)\.(\w+)\.Opened$", expr)
        if m:
            return f"{_mastery_title(dic, m.group(1))} {'not yet' if rev else 'already'} acquired"
        if not expr:
            return "company condition met"
        # resolve a cryptic `company.MissionCleared.<stage>` token to the mission's name in place
        # (the rest of a compound expression is left as-is — already legible comparisons).
        expr = re.sub(r"company\.MissionCleared\.(\w+)",
                      lambda mm: f"'{dic.get('Mission/' + mm.group(1) + '/LocationTitle') or mm.group(1)}' cleared",
                      expr)
        # `company.Progress.Character.<name>` is a per-character *story-progress counter* (a chapter
        # index, Albus runs past 65), NOT a recruited flag — recruitment is separate, and no value
        # maps to a label. So just name it readably ("<name> story progress <op> N") without claiming
        # a meaning. <name> is usually a character (resolve its title) but can be a sub-quest key.
        expr = re.sub(r"company\.Progress\.Character\.(\w+)",
                      lambda mm: (dic.get(f"ObjectInfo/{mm.group(1)}/Title")
                                  or re.sub(r"(?<=[a-z])(?=[A-Z])", " ", mm.group(1))) + " story progress",
                      expr)
        return f"not ({expr})" if rev else expr
    if t == "CompanyQuestProgressTest":
        return f"quest {c.get('Quest')} in progress"
    # engine sequencing marker — carries no readable condition, so drop it
    if t == "ActionDelimiter":
        return ""
    return t or ""


def _action(a, namer, dic):
    """Return a readable string for a meaningful action, or None for noise."""
    t = a.get("Type")
    if t in ("UpdateStageVariable", "UpdateStageVariableEx"):
        return f"set {a.get('Variable')} = {a.get('Value')}"
    if t == "AddStageVariable":
        return f"{a.get('Variable')} += {a.get('Value')}"
    if t == "RandomUpdateStageVariable":
        return f"set {a.get('Variable')} = (random)"
    if t in ("ChangeTeam", "UpdateObjectPropertyTeam", "UpdateObjectInstantPropertyTeam"):
        return f"{_ckunits(a, namer) or a.get('Team')} → {a.get('Team')} team"
    if t == "TeamChangeTeam":
        # team-level conversion: the *whole* `Team` becomes `Team2` (≠ unit-level ChangeTeam, where
        # `Team` is the target). e.g. Team="DefenceTeam" Team2="enemy" → "DefenceTeam team → enemy team".
        return f"{a.get('Team')} team → {a.get('Team2')} team"
    if t == "SpawnObject":
        return f"spawn {_ckunits(a, namer)}"
    if t == "ExcludeUnit":
        return f"remove {_ckunits(a, namer)}"
    if t == "Win":
        # `Win` ends the battle for the named Team — so an *enemy* Win is the player's defeat
        # (e.g. a beast escaping). Team player/empty = the player's victory.
        return "end battle (defeat)" if (a.get("Team") and a.get("Team") != "player") \
            else "end battle (victory)"
    if t in ("MissionComplete", "MissionClear"):
        return "complete mission"
    if t in ("MissionFail", "Lose"):
        return "end battle (defeat)"
    if t in ("GiveItem", "GainItem"):
        return "give item reward"
    if t in ("UpdateUserMember", "TeamUpdateUserMember", "AddUserMember"):
        who = _ckunits(a, namer) or a.get("Team") or ""
        verb = "remove from party" if a.get("OnOff") == "Off" else "add to party"
        return f"{verb}: {who}" if who else verb
    if t in ("UnitAddBuff", "TeamAddBuff", "UnitRemoveBuff", "TeamRemoveBuff") \
            and a.get("Name") in IGNORE_BUFFS:
        return None                              # cosmetic/UI buff — not worth showing
    if t == "UnitAddBuff":
        v = a.get("Value")
        return f"buff {_ckunits(a, namer)}: +{_buffname(dic, a.get('Name'))}" + (f" x{v}" if v and v != "1" else "")
    if t == "TeamAddBuff":
        return f"buff team {a.get('Team')}: +{_buffname(dic, a.get('Name'))}"
    if t == "UnitRemoveBuff":
        return f"unbuff {_ckunits(a, namer)}: -{_buffname(dic, a.get('Name'))}"
    if t == "TeamRemoveBuff":
        return f"unbuff team {a.get('Team')}: -{_buffname(dic, a.get('Name'))}"
    if t == "UnitRemoveBuffAll":
        return f"clear buffs: {_ckunits(a, namer)}"
    if t == "TeamRemoveBuffAll":
        return f"clear buffs: team {a.get('Team')}"
    if t == "GiveAbility":
        return f"give ability to {_ckunits(a, namer)}"
    if t == "UpdateAchievement":
        name = dic.get(f"Achievement/{a.get('Achievement')}/Title") or a.get("Achievement")
        return f"unlock achievement: {name}" if name else "unlock achievement"
    if t == "ToggleTrigger" and a.get("Trigger"):
        on = (a.get("OffOn") or a.get("OnOff")) == "On"
        return f"{'enable' if on else 'disable'} trigger: {a.get('Trigger')}"
    if t == "ToggleTriggerGroup" and a.get("TriggerGroup"):
        on = (a.get("OffOn") or a.get("OnOff")) == "On"
        return f"{'enable' if on else 'disable'} group: {a.get('TriggerGroup')}"
    if t == "UpdateDashboard":
        obj = _objective_change(a, dic)
        return f"set objective: {obj}" if obj else None
    return None


SCENE_NOISE = {"DirectOn", "DirectOFF", "DirectOff", "DirectInit", "Init"}


def _terms(tr, namer, dic):
    """Top-level AND condition terms as {t: text, v: variable}, or None if the
       condition isn't a flat AND (contains OR or nested groups). `v` lets the UI group
       a run of rules that test the same variable with different values."""
    conds = tr.findall("Condition")
    if len(conds) == 1 and conds[0].get("Type") == "And":
        conds = [c for c in conds[0] if c.tag == "Condition"]
    out = []
    for c in conds:
        if c.get("Type") in ("And", "Or"):
            return None
        s = _cond(c, namer, dic)
        if s:
            v = c.get("Variable") if c.get("Type") in ("VariableTest", "VariableToVariableTest") else None
            out.append({"t": s, "v": v})
    return out


def _scene_directtype(a):
    """The scene a play-action invokes: a MissionDirect action, or a CheckPoint that
       wraps an <ActionInstance ActionType="MissionDirect">."""
    if a.get("Type") == "MissionDirect":
        return a.get("DirectType")
    if a.get("Type") == "CheckPoint":
        inst = a.find("ActionInstance")
        if inst is not None and inst.get("ActionType") == "MissionDirect":
            return inst.get("DirectType")
    return None


def _scene_label(dt):
    return "play scene: " + re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", dt.replace("_", " ")).strip()


def _scene_lines(md_entry, dic, namer, key2entry, visited):
    """The spoken content of a MissionDirect sequence — DialogBattle, BalloonChat
       (speech bubbles) and choice prompts — following invoked sub-scenes and showing
       Switch logic (variable + per-case). Lines are prefixed with tabs for depth."""
    out = []

    def walk(elem, depth):
        for a in elem:
            if a.tag != "Action":
                walk(a, depth)
                continue
            t = a.get("Type")
            if t in ("DialogBattle", "SelDialogBattle") and a.get("Message"):
                text = _txt(dic, a.get("Message"))
                if not text:
                    continue
                if t == "SelDialogBattle":
                    out.append("\t" * depth + f"▷ presents choice: “{text}”")
                    continue
                sp = a.find("Speaker")
                info = sp.get("Info") if sp is not None else None
                who = dic.get(f"ObjectInfo/{info}/Title") if info and info != "Empty" else None
                out.append("\t" * depth + (f"{who}: " if who else "") + f"“{text}”")
            elif t == "BalloonChat" and a.get("Message"):
                text = _txt(dic, a.get("Message"))
                if not text:
                    continue
                u = a.find("Unit")
                who = namer(u.get("ObjectKey")) if u is not None and u.get("ObjectKey") else None
                thinks = (a.get("BalloonType") or "").startswith("Think")
                out.append("\t" * depth + (f"{who}: " if who else "")
                           + ("(thinks) " if thinks else "") + f"“{text}”")
            elif t == "Subtitle" and a.get("Message"):
                text = _txt(dic, a.get("Message"))
                if text:
                    out.append("\t" * depth + f"“{text}”")           # narration subtitle
            elif t == "TitleMessage":
                label = " — ".join(x for x in (_txt(dic, a.get("Title") or ""),
                                               _txt(dic, a.get("Message") or "")) if x)
                if label:
                    out.append("\t" * depth + f"❖ {label}")          # location/chapter banner
            elif t == "VictoryCondition":                            # (re)sets the objective
                win = [w for w in (_txt(dic, e.get("Title") or "")
                                   for e in a.findall("VictoryCondition/Entry")) if w]
                lose = [l for l in (_txt(dic, e.get("Title") or "")
                                    for e in a.findall("DefeatCondition/Entry")) if l]
                parts = ([f"win: {' / '.join(win)}"] if win else []) \
                    + ([f"lose: {' / '.join(lose)}"] if lose else [])
                if parts:
                    out.append("\t" * depth + "◎ objective — " + "  ·  ".join(parts))
            elif t == "Switch":
                var = a.get("TestExpression") or "?"
                cd = a.find("CaseDefinition")
                cases = cd.findall("Case") if cd is not None else []
                boolean = bool(cases) and all((c.get("CaseValue") or "").lower() in ("true", "false")
                                              for c in cases)
                # emit each branch's header only if its body has content, and drop the whole switch
                # if every branch is empty (mirrors _switch_lines) — a bare "switch on X / X == 0…"
                # with no lines under any case is noise.
                sw_start = len(out)
                if not boolean:                          # switch on a value: one header over the cases
                    out.append("\t" * depth + f"⎇ switch on {var}:")
                for case in cases:
                    if boolean:                          # TestExpression is itself a condition
                        hdr = (f"⎇ if {var}:" if (case.get("CaseValue") or "").lower() == "true"
                               else "⎇ otherwise:")
                        hdr, sub_depth = "\t" * depth + hdr, depth + 1
                    else:
                        hdr, sub_depth = "\t" * (depth + 1) + f"{var} == {case.get('CaseValue')}:", depth + 2
                    at = len(out)
                    out.append(hdr)
                    walk(case, sub_depth)
                    if len(out) == at + 1:               # branch produced no content — drop its header
                        out.pop()
                if not boolean and len(out) == sw_start + 1:   # every branch empty — drop the switch
                    out.pop()
            else:
                dt = _scene_directtype(a)
                if dt is not None:
                    if dt not in SCENE_NOISE and dt in key2entry and dt not in visited:
                        visited.add(dt)
                        walk(key2entry[dt], depth)
                else:
                    walk(a, depth)          # generic action — descend for nested content

    walk(md_entry, 0)
    return out


def _scene_index(stage_root, dic, namer):
    """{MissionDirect Key -> [spoken lines]} for the stage's directing sequences."""
    key2entry = {}
    for cont in stage_root.iter("MissionDirects"):
        for md in cont.findall("MissionDirect"):
            if md.get("Key"):
                key2entry[md.get("Key")] = md
    idx = {}
    for key, md in key2entry.items():
        ln = _scene_lines(md, dic, namer, key2entry, {key})
        if ln:
            idx[key] = ln
    return idx


def _unit_list_split(text):
    """Split a unit-list action into (prefix, names-csv), or None if it isn't one.
       Mergeable actions are unit removals ('remove <unit>, <unit>'), party removals
       ('remove from party: <unit>, ...') and party adds ('add to party: <unit>, ...').
       The 'from party: ' / 'add to party: ' part is a label, not a name, so it's kept
       in the prefix and only same-prefix lines merge together."""
    if text.startswith("remove "):
        body = text[len("remove "):]
        m = re.match(r".*?: ", body)
        if m:
            return "remove " + m.group(0), body[m.end():]
        return "remove ", body
    if text.startswith("add to party: "):
        return "add to party: ", text[len("add to party: "):]
    return None


def _merge_removes(do):
    """Collapse consecutive unit-list items into one counted line, e.g.
       'remove 4 Spoonist Guerilla, 2 Spoonist Pyromancer'. Lines are only merged
       with others sharing the same label-prefix, so party removals/adds stay grouped as
       'remove from party: Albus, Sion, ...' rather than repeating the label."""
    out, i = [], 0
    while i < len(do):
        item = do[i]
        split = None if "lines" in item else _unit_list_split(item["text"])
        if split is not None:
            prefix = split[0]
            counts, order, j = {}, [], i
            while j < len(do) and "lines" not in do[j] \
                    and (sj := _unit_list_split(do[j]["text"])) is not None \
                    and sj[0] == prefix:
                for nm in (n.strip() for n in sj[1].split(",")):
                    if nm:
                        if nm not in counts:
                            order.append(nm)
                        counts[nm] = counts.get(nm, 0) + 1
                j += 1
            if order:
                parts = [f"{counts[n]} {n}" if counts[n] > 1 else n for n in order]
                out.append({"text": prefix + ", ".join(parts)})
            i = j
        else:
            out.append(item)
            i += 1
    return out


SET_VAR = {"UpdateStageVariable", "AddStageVariable", "UpdateStageVariableEx",
           "RandomUpdateStageVariable"}


def _read_vars(stage_root):
    """Variables read anywhere — condition tests, data bindings, or expressions.
       A set-action writing to a variable NOT in here has no observable effect and is
       hidden as dead bookkeeping."""
    read = set()
    expr = []
    for e in stage_root.iter():
        v = e.get("Variable")
        if v and not (e.tag == "Action" and e.get("Type") in SET_VAR):
            read.add(v)                              # condition / StageDataBinding read
        for k in ("TargetVariable", "Variable2"):
            if e.get(k):
                read.add(e.get(k))
        for k in ("TestExpression", "SuccessExpression", "FailExpression", "Expression"):
            if e.get(k):
                expr.append(e.get(k))
    return read, " ".join(expr)


def _switch_lines(sw, dic, namer, scenes, read_vars, expr_blob, depth):
    """A trigger-body Switch as tab-prefixed lines: per-case label + that case's actions
       (scene plays, buffs, var sets, nested switches), so only-one-branch-runs logic is
       visible instead of every branch's actions shown flat."""
    expr = sw.get("TestExpression") or "?"
    cd = sw.find("CaseDefinition")
    cases = cd.findall("Case") if cd is not None else []
    boolean = bool(cases) and all((c.get("CaseValue") or "").lower() in ("true", "false")
                                  for c in cases)

    def emit(elem, d, acc):
        for a in elem:
            if a.tag != "Action":
                emit(a, d, acc)
                continue
            if a.get("Type") == "Switch":
                acc.extend(_switch_lines(a, dic, namer, scenes, read_vars, expr_blob, d))
                continue
            dt = _scene_directtype(a)
            if dt is not None:
                if dt not in SCENE_NOISE and scenes.get(dt):   # hide content-less scenes
                    acc.append("\t" * d + _scene_label(dt))
                continue
            if a.get("Type") in SET_VAR and a.get("Variable") \
                    and a.get("Variable") not in read_vars and a.get("Variable") not in expr_blob:
                continue
            s = _action(a, namer, dic)
            if s:
                acc.append("\t" * d + s)

    body = []
    for case in cases:
        cv = case.get("CaseValue") or ""
        clines = []
        emit(case, depth + (1 if boolean else 2), clines)
        if not clines:
            continue                            # branch with no displayable effect — skip
        if boolean:
            body.append("\t" * depth + (f"⎇ if {expr}:" if cv.lower() == "true" else "⎇ otherwise:"))
        else:
            body.append("\t" * (depth + 1) + f"{expr} == {cv}:")
        body.extend(clines)
    if not body:                                # every branch was noise — drop the switch
        return []
    return body if boolean else ["\t" * depth + f"⎇ switch on {expr}:"] + body


def render_script(stage_root, namer, dic):
    """Render each trigger as {name, when, do[...], repeat}; each do item is {text,
       lines?}. Repeating triggers (Repeat="true") are marked; dead variable writes
       (set to a variable nothing ever reads) are dropped."""
    scenes = _scene_index(stage_root, dic, namer)
    grants = _direct_mastery_grants(stage_root)   # scene -> masteries it awards (toast-only, no lines)
    read_vars, expr_blob = _read_vars(stage_root)
    # triggers/groups that some action can switch on at runtime
    toggled_names = {a.get("Trigger") for a in stage_root.iter("Action")
                     if a.get("Type") == "ToggleTrigger" and a.get("Trigger")}
    toggled_groups = {a.get("TriggerGroup") for a in stage_root.iter("Action")
                      if a.get("Type") == "ToggleTriggerGroup" and a.get("TriggerGroup")}
    # which trigger switches each trigger/group *on* — to mark an initially-inactive
    # trigger's condition with "enabled by <X>" (its activation, shown as a precondition)
    parents = {c: p for p in stage_root.iter() for c in p}

    def _enclosing(a):
        n = a
        while n is not None and n.tag != "Trigger":
            n = parents.get(n)
        return n.get("Name") if n is not None else None

    enable_by = {}             # trigger name -> triggers that switch it on by name
    groups_enabled_on = set()  # groups switched on (a member trigger cites its group)
    for a in stage_root.iter("Action"):
        if (a.get("OffOn") or a.get("OnOff")) != "On":
            continue
        if a.get("Type") == "ToggleTrigger" and a.get("Trigger"):
            enable_by.setdefault(a.get("Trigger"), set()).add(_enclosing(a))
        elif a.get("Type") == "ToggleTriggerGroup" and a.get("TriggerGroup"):
            groups_enabled_on.add(a.get("TriggerGroup"))
    out = []
    for tr in stage_root.iter("Trigger"):
        # a trigger that starts inactive and is never activated is dead — skip it
        if (tr.get("Active") or "").lower() == "false" \
                and tr.get("Name") not in toggled_names and tr.get("Group") not in toggled_groups:
            continue
        do = []
        plays = []
        consumed = set()
        granted = set()                             # masteries surfaced this trigger (dedupe)
        for a in tr.iter("Action"):
            if id(a) in consumed:
                continue
            if a.get("Type") == "Switch":           # branch — only the matching case runs
                swlines = _switch_lines(a, dic, namer, scenes, read_vars, expr_blob, 0)
                for sub in a.iter("Action"):        # don't re-emit the flattened case actions
                    consumed.add(id(sub))
                    sdt = _scene_directtype(sub)
                    if sdt and sdt not in SCENE_NOISE:
                        plays.append(sdt)           # keep scene→rule linkage for chaining
                if swlines:
                    if swlines[0].startswith("⎇ switch on"):
                        do.append({"text": swlines[0], "lines": swlines[1:]})
                    else:
                        do.append({"text": "⎇ branch:", "lines": swlines})
                continue
            dt = _scene_directtype(a)
            if dt is not None:
                if dt in SCENE_NOISE:
                    continue                        # camera/UI directing toggles — noise
                plays.append(dt)
                lines = scenes.get(dt)
                if lines:                           # content-less scenes (pure cutscene
                    do.append({"text": _scene_label(dt), "lines": lines})  # mechanics) are hidden
                for mid in sorted(grants.get(dt, ())):   # but a mastery-grant scene must show
                    if mid not in granted:
                        granted.add(mid)
                        do.append({"text": "grant mastery: " + _mastery_title(dic, mid)})
                continue
            if a.get("Type") in SET_VAR and a.get("Variable") \
                    and a.get("Variable") not in read_vars and a.get("Variable") not in expr_blob:
                continue                            # dead write — variable never read
            s = _action(a, namer, dic)
            if s:
                do.append({"text": s})
        if not do:
            continue
        do = _merge_removes(do)
        conds = [c for c in (_cond(c, namer, dic) for c in tr.findall("Condition")) if c]
        gates = [[c.get("Variable"), c.get("Value")] for c in tr.iter("Condition")
                 if c.get("Type") == "VariableTest" and c.get("Operation") == "Equal"]
        cond_vars = sorted({c.get("Variable") for c in tr.iter("Condition")
                            if c.get("Type") in ("VariableTest", "VariableToVariableTest")
                            and c.get("Variable")})
        vv = {}                                 # variable -> equality value (incl. nested), for bucketing
        for c in tr.iter("Condition"):
            if c.get("Type") == "VariableTest" and c.get("Operation") == "Equal" \
                    and c.get("Variable") and c.get("Value") is not None:
                vv.setdefault(c.get("Variable"), c.get("Value"))
        terms = _terms(tr, namer, dic)
        vt = {}                                 # non-flat rules: each variable's rendered test, for bucket labels
        if terms is None:
            for c in tr.iter("Condition"):
                if c.get("Type") in ("VariableTest", "VariableToVariableTest") and c.get("Variable"):
                    txt = _cond(c, namer, dic)
                    if txt:
                        vt.setdefault(c.get("Variable"), txt)
        if (tr.get("Active") or "").lower() == "false":   # starts off — show what switches it on
            sources = sorted(e for e in (enable_by.get(tr.get("Name")) or set()) if e)
            if tr.get("Group") in groups_enabled_on:       # enabled via its group, not by name
                sources.append(f"group {tr.get('Group')}")
            marker = ("enabled by " + ", ".join(sources)) if sources else "enabled"
            conds = [marker] + conds
            if terms is not None:
                terms = [{"t": marker, "v": None}] + terms
        rule = {"name": tr.get("Name") or tr.get("Group") or "",
                "when": " and ".join(conds), "do": do, "gates": gates, "plays": plays,
                "terms": terms, "vars": cond_vars, "vv": vv}
        if vt:
            rule["vt"] = vt
        if tr.get("Repeat") == "true":
            rule["repeat"] = True
        out.append(rule)
    return out


def mastery_grants(xml_dir, stage_dir, dic):
    """{mastery id -> [{mission, choice}]}: masteries a story mission awards, with the dialogue
    choice (text) that gates the award — or choice=None for an unconditional mission reward.
    This is the 'Story' source channel for the masteries tab; the *same* grants are highlighted
    in the Dialogue tab by build_dialog_map (both read _direct_mastery_grants). Only stages that
    map to a real mission are considered, so the duplicate *Test maps are ignored."""
    mission_info, stage_to_missions = build_mission_index(xml_dir, dic)
    out = {}
    for f in glob.glob(os.path.join(stage_dir, "*.stage")):
        missions = stage_to_missions.get(os.path.basename(f).lower())
        if not missions:
            continue
        try:
            r = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        grants = _direct_mastery_grants(r)
        if not grants:
            continue
        choice_text, choice_vars = _choice_setters(r, dic)
        producers = _choice_var_producers(r, choice_vars)
        rep = min((mission_info[m] for m in missions), key=lambda mi: (mi["level"] or 999))
        title = rep["title"]
        # a stage can map to >1 mission (e.g. Firefly Park + …_Roster) with the scenario on only one
        scenario = next((mission_info[m].get("scenario") for m in missions
                         if mission_info[m].get("scenario")), None)
        seen = set()                          # (mastery, choice) dedupe within this stage
        for tr in r.iter("Trigger"):
            mids = set()
            for a in tr.iter("Action"):
                if a.get("Type") == "MissionDirect" and a.get("DirectType") in grants:
                    mids |= grants[a.get("DirectType")]
            if not mids:
                continue
            choices = [choice_text[k] for k in
                       _trigger_choice_keys(tr, choice_text, choice_vars, producers)]
            for mid in mids:
                for choice in (choices or [None]):
                    if (mid, choice) in seen:
                        continue
                    seen.add((mid, choice))
                    out.setdefault(mid, []).append(
                        {"mission": title, "choice": choice, "scenario": scenario})
    return out


def parse_mission_opens(lua_path):
    """The 'opened for research' channel, parsed from `script/server/missionResult_Custom.lua`.
    Each `MissionResult_Custom_<Stage>` handler awards one mastery per branch (`dc:AcquireMastery`)
    and *opens the branch's other group members for research* — `dc:UpdateCompanyProperty(company,
    'Technique/<id>/Opened', true)` — i.e. craftable, but without handing over a copy. This is the
    only place these opens live (the `.stage` carries just the `MasteryAcquired` grant marker), so
    it's read straight from the Lua. In every case the opened masteries form a mutually-exclusive
    grant group ("your choice awards one, the rest become craftable"). Returns {stage_base: {
        'opened':      set(mid),                       # every mastery the mission opens
        'companions':  {granted_mid: set(opened_mid)}, # per branch: opens to show beside a grant
        'branch_vars': set(stage-variable name),       # the stage vars gating this mission's branches
    }}. Obfuscated string literals (all rendered as 'l') are skipped."""
    try:
        with open(lua_path, encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
    except OSError:
        return {}
    out = {}
    # split into `function MissionResult_Custom_<Name>( … ) <body>` chunks
    parts = re.split(r"\nfunction (MissionResult_Custom_\w+)", txt)
    for i in range(1, len(parts), 2):
        stage, body = parts[i][len("MissionResult_Custom_"):], parts[i + 1]
        if "/Opened'" not in body:
            continue
        # branch conditions test `missionValue_X`, each aliased from a stage variable; keep only the
        # ones actually sourced that way (drops locals like tutorialProgress / company.* checks) so
        # branch_vars ⊆ choice_vars cleanly separates choice-gated missions from outcome-gated ones
        # (Sky-wind park branches on battle outcome — Sion_Escape/…, not a dialogue choice).
        stage_vars = set(re.findall(r"GetStageVariable\(mission,\s*'(\w+)'\)", body))
        branch_vars = set(re.findall(r"missionValue_(\w+)\s*==", body)) & stage_vars
        # walk statements, grouping the consecutive Acquire/Open calls that sit inside one branch
        # block (delimited by if/elseif/else/end) — the grant and the opens it pairs with
        companions, opened = {}, set()
        grp_grant, grp_open = [], []

        def flush():
            for m in grp_grant:
                companions.setdefault(m, set()).update(grp_open)
            opened.update(grp_open)

        for line in body.splitlines():
            s = line.strip()
            if re.match(r"(if|elseif|else|end)\b", s):
                flush()
                grp_grant, grp_open = [], []
            mg = re.search(r"AcquireMastery\(company,\s*'(\w+)'", s)
            if mg and mg.group(1) != "l":
                grp_grant.append(mg.group(1))
            mo = re.search(r"Technique/(\w+)/Opened'\s*,\s*true", s)
            if mo and mo.group(1) != "l":
                grp_open.append(mo.group(1))
        flush()
        if opened:
            out[stage] = {"opened": opened, "companions": companions, "branch_vars": branch_vars}
    return out


def mastery_opens(mission_opens, xml_dir, dic):
    """{mastery id -> set(mission title)}: masteries a story mission *opens* for research (from
    parse_mission_opens), joined to the mission index for the display title — the same min-level
    resolution mastery_grants uses. Powers the 'still opened…' note on the Masteries tab."""
    if not mission_opens:
        return {}
    mission_info, stage_to_missions = build_mission_index(xml_dir, dic)
    out = {}
    for stage, info in mission_opens.items():
        missions = stage_to_missions.get(f"{stage.lower()}.stage")
        if not missions:
            continue
        rep = min((mission_info[m] for m in missions), key=lambda mi: (mi["level"] or 999))
        for mid in info["opened"]:
            out.setdefault(mid, set()).add(rep["title"])
    return out


def build_dialog_map(xml_dir, stage_dir, dic, monster_name, quest_names=None, mission_opens=None):
    """Return a list of stage dialogue records — stages with a real decision, plus every
       story-scripted stage (Scenario or Quest case) even when it has no surfaced choice, for its
       full-script rendering. Each: {stage, title, level, case, decisions:[{prompt,
       options:[{text, consequences}]}], script}. Scenario stages may have decisions:[].
       `quest_names` ({mission_id: quest_title}) labels a quest stage with its quest name (it
       has no scenario/chapter name to disambiguate a shared location), shown like `scenario`.
    """
    quest_names = quest_names or {}
    mission_info, stage_to_missions = build_mission_index(xml_dir, dic)
    opens_by_stage = {k.lower(): v for k, v in (mission_opens or {}).items()}
    records = []

    for f in glob.glob(os.path.join(stage_dir, "*.stage")):
        missions = stage_to_missions.get(os.path.basename(f).lower())
        if not missions:
            continue
        rep = min((mission_info[m] for m in missions), key=lambda mi: (mi["level"] or 999))
        # Story-scripted stages — Scenario (story/tutorial) and Quest (side-quest) cases, the ones
        # with the interesting scripting — are kept even without a surfaced decision, for their
        # full-script rendering. Raid (Violent) and Common (Ordinary) stages are otherwise dropped:
        # their only choices are deploy/entry-route picks and "boss defeated / done — continue or
        # retreat?" prompts, none a story decision worth surfacing. (Chubong Island's entry pick does
        # grant a team buff, but it's dropped with the rest.) Side-quest stages carry `Quest_` ids
        # (case "Quest"), so they're never matched by the Raid_/Common_ drop below anyway.
        is_story = any(mission_info[m].get("case") in ("Scenario", "Quest") for m in missions)
        if not is_story and all(m.startswith(("Raid_", "Common_")) for m in missions):
            continue
        try:
            r = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        choices_present = next(r.iter("DialogChoice"), None)
        if choices_present is None and not is_story:
            continue

        namer = _unit_namer(r, monster_name)
        grants = _direct_mastery_grants(r)        # mastery-awarding scenes (for choice consequences)
        opens_entry = opens_by_stage.get(os.path.basename(f)[:-len(".stage")].lower())
        gated = _gated_consequences(r, namer, dic, grants, opens_entry)
        # a choice's own consequences come only from the variables it *selects* on — not the
        # progression counters it also nudges (e.g. every Sky-wind character pick sets EventID=2,
        # which gates dozens of unrelated triggers; crediting each pick with all of them floods
        # every option with the same list). _choice_setters already drops trigger-set vars.
        _, choice_vars = _choice_setters(r, dic)
        parents = {c: p for p in r.iter() for c in p}

        # full stage script + indices of which rules each (variable,value) gates
        # and which rules play each scene (to find what shows a choice)
        script = render_script(r, namer, dic)
        gate_index, scene_to_rules = {}, {}
        for i, rule in enumerate(script):
            for g in rule.get("gates", ()):
                gate_index.setdefault((g[0], g[1]), []).append(i)
            for dt in rule.get("plays", ()):
                scene_to_rules.setdefault(dt, []).append(i)
        # scene -> scenes that invoke it (a MissionDirect playing another scene), and the forward
        # map scene -> scenes it plays (used for direct choice→scene follow-up chaining below)
        scene_parents, scene_plays = {}, {}
        for cont in r.iter("MissionDirects"):
            for md in cont.findall("MissionDirect"):
                if not md.get("Key"):
                    continue
                for a in md.iter("Action"):
                    if a.get("Type") == "MissionDirect" and a.get("DirectType"):
                        scene_parents.setdefault(a.get("DirectType"), []).append(md.get("Key"))
                        scene_plays.setdefault(md.get("Key"), set()).add(a.get("DirectType"))

        def shown_by(scene, seen=None):
            """Rule indices that cause a scene to play, chasing scene→scene up to a trigger."""
            seen = seen or set()
            if scene in scene_to_rules:
                return list(scene_to_rules[scene])
            found = []
            for p in scene_parents.get(scene, []):
                if p not in seen:
                    seen.add(p)
                    found += shown_by(p, seen)
            return found

        decisions = []
        seen_decisions = set()
        for dc in r.iter("DialogChoice"):
            pa = parents.get(dc)
            prompt = _txt(dic, pa.get("Message")) if pa is not None and pa.get("Message") else ""
            scene_key = None
            n = parents.get(dc)
            while n is not None:
                if n.tag == "MissionDirect" and n.get("Key"):
                    scene_key = n.get("Key")
                    break
                n = parents.get(n)
            options = []
            for ch in dc.findall("Choice"):
                text = _txt(dic, ch.get("Message")) if ch.get("Message") else ""
                cons = []
                setvars = []
                al = ch.find("ActionList")
                plays_scenes = []
                for a in (al.findall("Action") if al is not None else []):
                    if a.get("Type") == "UpdateStageVariable":
                        var, val = a.get("Variable"), a.get("Value")
                        setvars.append((var, val))
                        if var in choice_vars:        # selector var only — skip progression counters
                            cons.extend(gated.get((var, val), []))
                    elif a.get("Type") == "MissionDirect" and a.get("DirectType"):
                        plays_scenes.append(a.get("DirectType"))   # choice plays a follow-up scene directly
                    elif a.get("Type") in INTEREST:
                        d = _describe(a, namer, dic)
                        if d:
                            cons.append(d)
                # unique consequences, preserve order
                uniq = list(dict.fromkeys(cons))
                if not text and not uniq:
                    continue                            # untranslated, no effect — skip
                if not text:
                    text = "(unnamed choice)"
                # link follow-up decisions only through the selector variable, not the progression
                # counters a choice also nudges (EventID) — else every option chains to every later
                # sub-dialog (all of Sky-wind park's per-character follow-ups piled under the first).
                triggers = sorted({i for (var, val) in setvars if var in choice_vars
                                   for i in gate_index.get((var, val), ())})
                options.append({"text": text, "sets": [f"{v}={val}" for v, val in setvars],
                                "consequences": [dict(kind=c[0], text=c[1],
                                                      **({"mastery": c[2]} if len(c) > 2 else {}))
                                                 for c in uniq],
                                "triggers": triggers, "plays": plays_scenes})
            # drop decisions with nothing meaningful left; dedupe difficulty-variant copies
            if not options:
                continue
            key = (prompt, tuple(o["text"] for o in options))
            if key in seen_decisions:
                continue
            seen_decisions.add(key)
            decisions.append({"prompt": prompt, "options": options, "scene_key": scene_key,
                              "shown_by": sorted(set(shown_by(scene_key))) if scene_key else []})

        if not decisions and not is_story:
            continue
        # conversation chaining: an option "leads to" a follow-up decision two ways —
        #  (a) indirect: the option sets a StageVariable that gates a trigger which plays the scene
        #      showing that decision (via shown_by → rule_to_dec); and
        #  (b) direct: the option's ActionList plays that decision's scene straight away (via `plays`
        #      → scene_plays → the scene that hosts the decision's DialogChoice). Common in the DLC
        #      missions (e.g. Crimson Crow's "Shadow of the Past") but also used in the base game.
        rule_to_dec = {}
        for di, dec in enumerate(decisions):
            for ri in dec.get("shown_by", []):
                rule_to_dec.setdefault(ri, set()).add(di)
        dec_scene = {}                            # scene Key -> decisions whose DialogChoice lives there
        for di, dec in enumerate(decisions):
            if dec.get("scene_key"):
                dec_scene.setdefault(dec["scene_key"], set()).add(di)

        def decs_played(scenes, seen=None):
            """Decisions reached by playing `scenes`, chasing scene→scene but stopping at the
            nearest decision-hosting scene (so an option links to its immediate follow-up, not the
            whole downstream chain)."""
            seen = set() if seen is None else seen
            out = set()
            for s in scenes:
                if s in seen:
                    continue
                seen.add(s)
                if s in dec_scene:
                    out |= dec_scene[s]
                else:
                    out |= decs_played(scene_plays.get(s, ()), seen)
            return out

        child = set()
        for di, dec in enumerate(decisions):
            for o in dec["options"]:
                leads = set()
                for ri in (o.get("triggers") or []):
                    leads |= {t for t in rule_to_dec.get(ri, ()) if t != di}
                leads |= {t for t in decs_played(o.get("plays") or ()) if t != di}
                o["leads_to"] = sorted(leads)
                child |= leads
        for di, dec in enumerate(decisions):
            dec["is_child"] = di in child
        for dec in decisions:                     # internal-only fields, not for the web payload
            dec.pop("scene_key", None)
            for o in dec["options"]:
                o.pop("plays", None)

        for rule in script:                      # internal indices, not for output
            rule.pop("gates", None)
            rule.pop("plays", None)
        rec = {
            "stage": os.path.splitext(os.path.basename(f))[0],
            "title": rep["title"], "level": rep["level"], "case": rep["case"],
            "decisions": decisions,
            "script": script,
        }
        scenario = next((mission_info[m].get("scenario") for m in missions
                         if mission_info[m].get("scenario")), None)
        if scenario:                          # scenario missions: story name + chapter (shown in heading)
            rec["scenario"] = scenario
        quest_name = next((quest_names[m] for m in missions if m in quest_names), None)
        if quest_name:                        # side-quest stages: the quest name (no scenario name)
            rec["questName"] = quest_name
        records.append(rec)

    records.sort(key=lambda s: (s["level"] or 999, s["title"]))
    return records
