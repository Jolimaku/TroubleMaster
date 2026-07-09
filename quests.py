"""
Extract the Shooter Street side-quests (the NPC request chains) from Quest.xml.

Every player-facing side quest is an `EP1_Quest*` class in `xml/Quest.xml`'s `Quest`
idspace, handed out by one of the Shooter Street lobby NPCs (`Client`). Each quest has:
- a localized Title / Objective (`Quest/<id>/Title_Base` / `.../Objective_Base` in the
  dictionary — the inline xml attrs are Korean), which may embed `$QuestType$`,
  `$TargetItem$`, `$TargetCivilName$` placeholders we expand here;
- a `Type` (QuestType id — Hunting / CollectItem / CivilRescue / …) whose display name is
  `QuestType/<type>/Title`;
- prerequisites (`PriorQuest`, comma/space separated — can be several, and can point at a
  *different* NPC's chain, e.g. Roberto02 needs Maximillion01);
- a reward block: up to three selectable items (the player picks one) plus an always-granted
  NPC friendship amount (`Base_RewardNPCFriendship`).

NPC display names: `ObjectInfo/<client>/Title` (only Hundred→"Maximillion" and Sabana→
"Savana" differ from their ids). Reward / target item names: `Item/<id>/Base_Title`;
collect targets cascade by quest type (kill-skin → `QuestType_CollectItem_Kill/<id>/Title`,
cargo/property → `CollectItemSet/<id>/Title`, plain → `Item/<id>/Base_Title`); rescue targets
→ `CitizenGenSet/<id>/Title`. Mission location (for quests tied to a fixed stage) →
`Mission/<id>/LocationTitle`.

`build_quests` returns a flat list of quest dicts (one per quest, with `npc`/`npcName`/
`chainIndex` so the web tool can lay them out per-NPC and resolve "Requires X #N" badges).
"""
import os
import re
import xml.etree.ElementTree as ET

from missions import build_mission_index

QUEST_PREFIX = "EP1_Quest"


def _idspace(path, want):
    for sp in ET.parse(path).getroot().findall("idspace"):
        if sp.get("id") == want:
            return sp
    return None


def _split_prereq(s):
    return [x for x in re.split(r"[,\s]+", s or "") if x and x != "None"]


# --- quest title resolution (shared by build_quests + quest_missions) --------------------
def _qtype_label(dic, t):
    return dic.get(f"QuestType/{t}/Title") or t


def _item_name(dic, iid):
    return dic.get(f"Item/{iid}/Base_Title") or iid


def _target_name(dic, c):
    t = c.get("Target")
    typ = c.get("Type") or ""
    if not t:
        return ""
    if typ == "CollectItem_Kill":
        return dic.get(f"QuestType_CollectItem_Kill/{t}/Title") or _item_name(dic, t)
    if typ == "CollectItem_Property":
        return dic.get(f"CollectItemSet/{t}/Title") or _item_name(dic, t)
    if typ.startswith("CollectItem"):
        return _item_name(dic, t)
    if typ.startswith("CivilRescue"):
        return (dic.get(f"CitizenGenSet/{t}/Title")
                or dic.get(f"ObjectInfo/{t}/Title") or t)
    if typ.startswith("Arrest"):
        return (dic.get(f"ObjectInfo/{t}/Title")
                or dic.get(f"CitizenGenSet/{t}/Title") or t)
    return t


def _expand(dic, text, c):
    """Expand a quest's $QuestType$/$TargetItem$/$TargetCivilName$ placeholders and strip
    [!tags] / Korean [을]/[를] josa markers."""
    if not text:
        return text
    text = text.replace("$QuestType$", _qtype_label(dic, c.get("Type")))
    tn = _target_name(dic, c)
    text = text.replace("$TargetItem$", tn).replace("$TargetCivilName$", tn)
    text = re.sub(r"\[[^\]]*\]", "", text)            # drop [!tags] and korean [을]/[를] josa
    return re.sub(r"\s+", " ", text).strip()


def _quest_title(dic, c):
    """A quest's localized display title: the dictionary `Quest/<id>/Title_Base`, or the inline
    placeholder template when there's no dictionary entry, with placeholders expanded / tags
    stripped. Falls back to the raw id."""
    raw = dic.get(f"Quest/{c.get('name')}/Title_Base")
    if not raw and "$" in (c.get("Title_Base") or ""):
        raw = c.get("Title_Base")
    return _expand(dic, raw, c) if raw else c.get("name")


def _quest_mission_ids(c):
    """Mission ids a quest launches as its battle — via Target (MissionClear_*/Talk),
    <DirectMission Mission=>, or <Missions> children."""
    mids = []
    typ = c.get("Type") or ""
    if (typ.startswith("MissionClear") or typ == "Talk") and c.get("Target"):
        mids.append(c.get("Target"))
    dm = c.find("DirectMission")
    if dm is not None and dm.get("Mission"):
        mids.append(dm.get("Mission"))
    ms = c.find("Missions")
    for p in (ms.findall("property") if ms is not None else []):
        if p.get("name"):
            mids.append(p.get("name"))
    return mids


def quest_missions(xml_dir, dic):
    """{mission_id: quest_title} — the side-quest that launches each mission, so the Dialogue
    tab can label a quest stage (which carries no scenario/chapter name) with its quest name.
    First quest wins on the rare shared mission; Repeat dev-placeholder quests are skipped."""
    qsp = _idspace(os.path.join(xml_dir, "Quest.xml"), "Quest")
    out = {}
    for c in (qsp.findall("class") if qsp is not None else []):
        qid = c.get("name") or ""
        if not qid.startswith(QUEST_PREFIX) or c.get("Group") == "Repeat":
            continue
        title = _quest_title(dic, c)
        for m in _quest_mission_ids(c):
            out.setdefault(m, title)
    return out


def build_quests(xml_dir, dic, mon_name=None):
    """[quest_dict, ...] for every EP1_Quest* side quest, in per-NPC chain order.

    mon_name: optional {monster_id: display_name} so collect-by-kill quests can name the
    enemy they drop from (the caller already builds this from Monster.xml)."""
    qsp = _idspace(os.path.join(xml_dir, "Quest.xml"), "Quest")
    if qsp is None:
        return []
    mon_name = mon_name or {}
    # `Group="Repeat"` quests are disabled dev placeholders: RequireStageLv=99 exceeds the
    # highest reachable scenario ProgressOrder (65), so TestQuestStart (server/Quest_NPC.lua)
    # always fails their stage-progress gate — they can never be accepted in game. (Also flagged
    # by their [!Ignore] titles and Lv=999.) So they're dropped, like other unshipped content.
    classes = [c for c in qsp.findall("class")
               if (c.get("name") or "").startswith(QUEST_PREFIX) and c.get("Group") != "Repeat"]

    def rewards(c):
        out = []
        r = c.find("Reward")
        for p in (r.findall("property") if r is not None else []):
            typ, val = p.get("Type"), p.get("Value")
            amt = int(p.get("Amount") or 1)
            if typ == "Item":
                out.append({"kind": "item", "name": _item_name(dic, val), "amount": amt})
            elif typ == "Recipe":
                out.append({"kind": "recipe", "name": _item_name(dic, val), "amount": amt})
            elif typ == "RandomTroublemaker":
                out.append({"kind": "troublemaker", "amount": amt,
                            "pool": dic.get(f"TroublemakerCategory/{val}/Title") or val})
            elif typ == "RandomRecipe":
                out.append({"kind": "workmanship", "amount": amt,
                            "pool": dic.get(f"Profession/{val}/Title") or val})
        return out

    def locations(c):
        """Localized stage name(s) for a quest tied to a fixed mission ([] if it's a
        'collect anywhere these enemies spawn' quest)."""
        locs = []
        for m in _quest_mission_ids(c):
            t = dic.get(f"Mission/{m}/LocationTitle")
            if t and t not in locs:
                locs.append(t)
        return locs

    def drop_from(c):
        """Enemy names a collect-by-kill quest drops from."""
        tm = c.find("TargetMonster")
        out = []
        for p in (tm.findall("property") if tm is not None else []):
            nm = mon_name.get(p.get("Target")) or p.get("Target")
            if nm and nm not in out:
                out.append(nm)
        return out

    def as_int(v, d=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return d

    # RequireStageLv is a story-progress gate: the quest unlocks once the company's highest
    # cleared main-mission ProgressOrder reaches it (server/Quest_NPC.lua →
    # GetScenarioProgressGrade). Resolve it to the actual gating mission — the earliest story
    # mission whose ProgressOrder meets the threshold — and show its name instead of the raw
    # number (which reads misleadingly like a character level). 0 = no gate; the repeat-quest
    # sentinel (99, past the last mission) resolves to nothing.
    mission_info, _ = build_mission_index(xml_dir, dic)
    prog = []
    msp = _idspace(os.path.join(xml_dir, "mission.xml"), "Mission")
    for c in (msp.findall("class") if msp is not None else []):
        v = as_int(c.get("ProgressOrder"))
        if v > 0:
            prog.append((v, c.get("name")))
    prog.sort()

    def unlock_mission(stagelv):
        if stagelv <= 0:
            return None
        for v, mid in prog:
            if v >= stagelv:
                info = mission_info.get(mid) or {}
                name = (info.get("scenario") or info.get("title")
                        or dic.get(f"Mission/{mid}/LocationTitle"))
                if not name:
                    return None
                # level + case type drive the same colour-coded badge the masteries tab uses
                return {"name": name, "level": info.get("level"),
                        "case": info.get("case")}
        return None

    quests = []
    for c in classes:
        qid = c.get("name")
        quests.append({
            "id": qid,
            "npc": c.get("Client"),
            "type": c.get("Type"),
            "typeLabel": _qtype_label(dic, c.get("Type")),
            "rank": (c.get("Rank") if c.get("Rank") != "None" else None),
            "lv": as_int(c.get("Lv")),
            "stageLv": as_int(c.get("RequireStageLv")),
            "unlockMission": unlock_mission(as_int(c.get("RequireStageLv"))),
            "title": _quest_title(dic, c),
            "objective": _expand(dic, dic.get(f"Quest/{qid}/Objective_Base"), c),
            "prereqs": _split_prereq(c.get("PriorQuest")),
            "order": as_int(c.get("ProgressOrder")),
            "friendship": as_int(c.get("Base_RewardNPCFriendship")),
            "rewards": rewards(c),
            "locations": locations(c),
            "dropFrom": drop_from(c),
            # a `<JointTraining>` child marks the quest that permanently opens the Joint Drill
            # mode (only EP1_Quest_Roberto05) — surfaced as an extra "unlock" reward
            "unlocksJointDrill": c.find("JointTraining") is not None,
        })

    # NPC display names + per-NPC chain index (1-based, by progress order then level)
    npc_names = {q["npc"]: (dic.get(f"ObjectInfo/{q['npc']}/Title") or q["npc"])
                 for q in quests}
    for q in quests:
        q["npcName"] = npc_names[q["npc"]]
    by_npc = {}
    for q in quests:
        by_npc.setdefault(q["npc"], []).append(q)
    for chain in by_npc.values():
        chain.sort(key=lambda q: (q["order"], q["lv"], q["id"]))
        for i, q in enumerate(chain, 1):
            q["chainIndex"] = i

    quests.sort(key=lambda q: (q["npcName"], q["chainIndex"]))
    return quests
