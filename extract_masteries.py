"""
Extract Masteries, their drop/acquisition sources, and Mastery Sets from
TROUBLESHOOTER: Abandoned Children.

Inputs (already-decrypted plaintext produced by the official TSAC Modding Tool's
PLDataPacker --mode unpack):
    Unpack/Data/xml/Mastery.xml      - mastery + mastery-type definitions
    Unpack/Data/xml/MasterySet.xml   - 4-mastery set bonuses
    Unpack/Data/xml/Monster.xml      - enemies and the masteries they carry
    Unpack/Data/xml/Job.xml          - jobs/classes and their basic mastery
    Unpack/Data/xml/MasteryCode.xml  - share-code lookup tables (web/codemap.js)
And the (already plaintext) localization dictionary shipped with the game:
    Dictionary/keymap.dkm            - logical key -> (numeric code, dic_type)
    Dictionary/eng/dic_keyword.dic   - code -> English  (dic_type "Keyword")
    Dictionary/eng/dic_text.dic      - code -> English  (dic_type "Text")

In-game mechanic: you learn a mastery by analysing an enemy that uses it, so a
mastery's "sources" are the monsters whose <Masteries> list contains it, plus the
job that starts with it. (Story/quest one-off unlocks live in quest scripts and
are not included here.)

Outputs (./output): JSON, CSV and human-readable Markdown for each of the three.
"""
import os
import re
import csv
import json
import argparse
import datetime
import collections
import xml.etree.ElementTree as ET

import resolve_desc
from resolve_desc import (resolve_description, stat_summary, describe_buff, immune_debuff_summary,
                          neutralize_field_summary, strip_refs, ref_markup, indent_block)
from missions import build_enemy_missions, missions_for, dialog_labels_for, TIER_RANK
from dialog_map import (build_dialog_map, mastery_grants, mastery_opens, parse_mission_opens,
                        company_opens)
from quests import build_quests, quest_missions


def _tier_rank(t):
    return TIER_RANK.get(t, 0)

# ----------------------------------------------------------------------------- paths
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--game", default=r"E:\SteamLibrary\steamapps\common\Troubleshooter",
                   help="game install dir (for Dictionary/)")
    p.add_argument("--data", default=os.path.join(os.path.dirname(__file__), "Unpack", "Data"),
                   help="unpacked Data dir (with xml/)")
    p.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output"))
    p.add_argument("--lang", default="eng")
    p.add_argument("--include-developing", action="store_true",
                   help="include unavailable content. Default: exclude it, matching the game — "
                        "(1) Developing=\"true\" entries (a Developing job fails IsEnableJob in "
                        "shared_job.lua, so its unlocks are unreachable; mastery sets sit behind "
                        "EnableDevelopingMasterySet=false), and (2) hidden classes "
                        "(Help.xml Checker=\"HelpChecker_Hidden\") that no unit uses as its Job "
                        "(Musician/Singer/Priest/…), plus their orphaned class-trait masteries.")
    p.add_argument("--report-unresolved", action="store_true",
                   help="print every dictionary miss (raw English id / literal $token$ that leaked "
                        "into resolved text) at the end — for finding untranslated strings.")
    return p.parse_args()


# ----------------------------------------------------------------------------- dictionary
TAG_RE = re.compile(r"^\[![^\]]*\]")  # strip leading [!Category] markup


class Dictionary:
    def __init__(self, game_dir, lang="eng"):
        self.lang = lang                 # used for the few strings the game has no dictionary entry for
        self.key2 = {}
        km = open(os.path.join(game_dir, "Dictionary", "keymap.dkm"), encoding="utf-8").read()
        for k, code, t in re.findall(r'key="([^"]+)"\s+code="(\d+)"\s+dic_type="([^"]+)"', km):
            self.key2[k] = (int(code), t)
        self.kw = self._load(os.path.join(game_dir, "Dictionary", lang, "dic_keyword.dic"))
        self.tx = self._load(os.path.join(game_dir, "Dictionary", lang, "dic_text.dic"))

    @staticmethod
    def _load(path):
        txt = open(path, encoding="utf-8").read()
        parts = re.split(r"(?m)^#(\d+)\t", txt)
        out = {}
        for code, body in zip(parts[1::2], parts[2::2]):
            cols = body.rstrip("\r\n").split("\t")
            out[int(code)] = cols[1] if len(cols) > 1 else cols[0]  # col1 = English
        return out

    def get(self, key, default=None):
        if key not in self.key2:
            return default
        code, t = self.key2[key]
        val = (self.kw if t == "Keyword" else self.tx).get(code, default)
        return TAG_RE.sub("", val) if isinstance(val, str) else val


# ----------------------------------------------------------------------------- xml helpers
def idspace(path, want):
    root = ET.parse(path).getroot()
    spaces = root.findall("idspace") if root.tag == "idspaces" else [root]
    for sp in spaces:
        if sp.get("id") == want:
            return sp
    raise KeyError(f"idspace {want} not in {path}")


def is_developing(c):
    """True if an XML <class> carries Developing="true" — the engine's "not in normal play"
    marker. The game enforces it (e.g. shared_job.lua rejects Developing jobs in IsEnableJob;
    masteries/sets are gated behind the EnableDevelopingMasterySet=false system constant), so
    by default we drop these from the extracted data. Only Job.xml and MasterySet.xml among the
    files this script reads carry the flag."""
    return (c.get("Developing") or "").lower() == "true"


def hidden_classes(xml):
    """Class names whose Help.xml encyclopedia entry is permanently hidden
    (Checker="HelpChecker_Hidden") — the game's marker for withheld/cut classes. Scoped to the
    `Class_<name>` topics (other Hidden topics are live contextual battle/quest popups)."""
    out = set()
    root = ET.parse(os.path.join(xml, "Help.xml")).getroot()
    for sp in (root.findall("idspace") if root.tag == "idspaces" else [root]):
        for c in sp.findall("class"):
            n = c.get("name") or ""
            if n.startswith("Class_") and c.get("Checker") == "HelpChecker_Hidden":
                out.add(n[len("Class_"):])
    return out


def compute_excluded_jobs(xml, job_cls, exclude_dev):
    """Job names to drop when exclude_dev: Developing="true" jobs (engine-disabled) PLUS hidden
    classes (see hidden_classes) that NO unit uses as its `Job` — i.e. cut classes with no
    in-game carrier (Musician/Singer/Priest/Bowman/Astrologian/Gambler/Arithmetician/Crusader/
    Bard). Hidden classes a real unit embodies are kept (Guardian → VHPD Defender enemy,
    Merchant → shop NPCs). Returns an empty set when exclude_dev is False, so the
    --include-developing flag keeps everything."""
    if not exclude_dev:
        return set()
    excl = {jid for jid, c in job_cls.items() if is_developing(c)}
    used = {o.get("Job") for o in idspace(os.path.join(xml, "object.xml"), "Object").findall("class")
            if o.get("Job")}
    for h in hidden_classes(xml):
        if h in job_cls and h not in used:
            excl.add(h)
    return excl


# drone/robot mastery categories — split out of the human masteries into the Modules tab
MODULE_CATEGORIES = {"FrameModule", "ComplementaryModule", "AIModule", "SaftyModule",
                     "SupportModule"}
# small odd buckets that share a catch-all Misc tab (SP / Race / Granting / Difficulty / Bond)
MISC_CATEGORIES = {"ESP", "Race", "Grant", "Difficulty", "BestFriend"}
# "innate/granted" mastery Types — not learnable (no enemy source) and not player-placeable
# (Type isn't in any character's accessible set). When such a mastery slips past the category
# branches into the catch-all, it's an enemy trait / item- or ability-granted effect, not a
# real board mastery, so it belongs in Misc rather than the main Masteries tab.
SYSTEM_TYPES = {"Body", "Equipment", "Gene"}     # "Gene" is shown as "Genetics"


# Masteries unlocked by an in-game *feat* (the "achievement" channel): xml/GuideTrigger.xml
# entries whose Director grants a Mastery when a Checker condition is met. The precise
# thresholds live in script/server/guide_trigger.lua (read off the checker functions), not in
# any dictionary string, so the condition text is authored here (en + kor — kor mined from the
# linked Steam achievement Desc in Achievement.xml where one exists). Keyed by the *granted*
# mastery id (the GuideTrigger `Mastery` attribute); the optional 3rd field is the linked
# Steam achievement id (Achievement.xml), surfaced as extra context. See DATAMINING.md
# "Achievement unlocks". Used by guidetrigger_grants().
ACHIEVEMENT_GRANTS = {
    # mastery id:               (English condition,                                              Korean condition,                                                      steam achievement id)
    "BeastHunter":              ("Defeat 100 Legendary-grade beasts",                            "전설 등급 이상의 야수 100마리 처치",                                   None),
    "MachineHunter":            ("Defeat 100 Legendary-grade machines",                          "전설 등급 이상의 기계 100기 처치",                                     None),
    "GiantKiller":              ("Defeat 100 giant enemies",                                     "거대 적 100기 처치",                                                  None),
    "MassExtract":              ("Extract 100 psionic stones",                                   "이능석 100회 추출",                                                   None),
    "MaterialCollector":        ("Collect material drops from 100 defeated enemies",             "처치한 적에게서 재료 100회 획득",                                       None),
    "InnerPeace":               ("Use Stand By 100 times",                                       "대기 100회 사용",                                                     None),
    "BeastMaster":              ("Tame 5 beasts",                                                "야수 5회 길들이기",                                                   None),
    "HideHide":                 ("Use Conceal 10 times",                                         "잠복 10회 사용",                                                      "SituationConceal10"),
    "RushReady":                ("Dodge 100 ranged attacks while in cover",                      "엄폐 중 원거리 공격 100회 회피",                                       None),
    "DeepseaEscape":            ("Raise a unit's next-turn wait time (Act) to 300 or more",      "다음 턴 대기 시간을 300 이상으로 만들기",                              "SituationActOver300"),
    "BeastTraining":            ("Win 3 missions with a tamed beast in your team",               "길들인 야수와 함께 미션 3회 승리",                                     None),
    "ShadowStep":               ("Trigger Shadow Sniping (confuse an enemy with a shadow shot)", "그림자 저격으로 적을 혼란 상태로 만들기",                              "SituationShadowSniperConfusion"),
    "PowerfulTrap":             ("Install 10 traps",                                             "덫 10회 설치",                                                        "AbilityTrapDesigner"),
    "TreasureHunter":           ("Open 100 chests",                                              "상자 100개 열기",                                                     "SituationOpenChest"),
    "Restart":                  ("As Hacker Kylie, exhaust the use count of every protocol ability except Overcharge", "해커 카일리로 과충전 어빌리티를 제외한 모든 프로토콜 어빌리티 사용횟수 모두 소진하기", "SituationHackerZeroUseCount"),
    "AutoProtocolRestore":      ("As Engineer Kylie, exhaust the use count of every protocol ability except Overcharge", "기술자 카일리로 과충전 어빌리티를 제외한 모든 프로토콜 어빌리티 사용횟수 모두 소진하기", "SituationEngineerZeroUseCount"),
    "CodeOptimization":         ("Use protocol abilities 50 times",                              "프로토콜 어빌리티 50회 사용",                                          "SituationProtocol50"),
    "ReverseEngineering":       ("Succeed at Hacking Protocol 3 times",                          "해킹 프로토콜 3회 성공",                                               None),
    "RisingStar":               ("Trigger a Great performance at least once",                    "[ 멋짐 ] 1회 이상 발동",                                              "SituationRisingStar"),
    "EnthusiasticPerformance":  ("Finish a performance with Great triggered 5+ times",           "[ 멋짐 ] 5회 이상 발동한 상태에서 [ 마무리 ] 하기",                     "SituationEnthusiasticPerformance"),
    "SubscriptionConcert":      ("Finish 10 performances",                                       "공연 10회 마무리",                                                    None),
    "Encore":                   ("Finish a performance with 8+ performance slots filled and Great triggered", "[ 멋짐 ] 발동 + 공연 슬롯 8개 이상 채운 상태에서 [ 마무리 ] 하기", "SituationEncore"),
    "ShowClose":                ("Finish a performance with no Great triggered",                 "[ 멋짐 ]이 없는 [ 마무리 ] 1회 사용",                                  "SituationShowClose"),
    "Gourmand":                 ("Gain a food-set effect 10 times",                              "음식 세트 효과 10회 얻기",                                             "SituationGourmand"),
    "Illusionist":              ("Succeed with Fake Eye 5 times",                                "[ 환술 ] 5회 이상 성공",                                              "SituationIllusionist"),
    "ReleaseChakra":            ("As Ninja Misty, exhaust the use count of every Ninjutsu ability", "닌자 미스티로 모든 술법 어빌리티 사용횟수 모두 소진하기",              "SituationReleaseChakra"),
    "HealJutsu":                ("As Ninja Misty, restore an ally (or self) to full HP with a Ninjutsu heal", "닌자 미스티로 회복 술법으로 아군 혹은 자신 체력 최대로 만들기",      "SituationHealJutsu"),
    "ImprovedJutsu":            ("As Ninja Misty, use healing and assisting Ninjutsu 3 times each", "닌자 미스티로 회복·지원 술법 어빌리티 각각 3회 사용",                  "SituationImprovedJutsu"),
    "ExplosiveMixture":         ("Use an enhanced Shake Shot 3 times",                           "강화 쉐이크샷 3회 사용",                                               "SituationExplosiveMixture"),
    "RebirthPotion":            ("Use up every charge of Spray Heal",                            "스프레이 힐 사용횟수 모두 소진",                                       "AbilityRay"),
    "SkillfulContorolMachine":  ("Win 3 missions with a machine (drone) in your team",           "기계와 함께 미션 3회 승리",                                            None),
    # hardcoded in system.lua (a BuffAdded='Luck' subscription counting company.Stats.LuckAdded),
    # NOT GuideTrigger.xml — emitted via SYSTEM_GRANT_IDS below.
    "LuckyNumberSeven":         ("Gain the Luck buff 7 times (cumulative)",                      "행운 버프를 누적 7회 획득",                                            None),
    "LuckyNumberEight":         ("Gain the Luck buff 8 times (cumulative)",                      "행운 버프를 누적 8회 획득",                                            None),
    "LuckyNumberNine":          ("Gain the Luck buff 9 times (cumulative)",                      "행운 버프를 누적 9회 획득",                                            None),
}

# Masteries granted by a hardcoded handler in `script/server/system.lua` rather than a data table
# (GuideTrigger.xml) — same feat-based shape, so surfaced through the **Achievement** channel using
# the authored conditions in ACHIEVEMENT_GRANTS. A full `dc:AcquireMastery` audit found these (the
# Lucky-number trio) are the only such case: every other grant call is GuideTrigger / story
# (missionResult_Custom) / office / research / mastery-extract (not a grant) / a dead reward branch.
SYSTEM_GRANT_IDS = ("LuckyNumberSeven", "LuckyNumberEight", "LuckyNumberNine")


def guidetrigger_grants(xml, dic):
    """{mastery_id: source-dict} for masteries granted by an in-game feat (GuideTrigger.xml).
    Every <class> with a `Mastery` attribute is a grant; the human-readable condition + the
    optional linked Steam achievement come from ACHIEVEMENT_GRANTS (kor/eng by dic.lang)."""
    out = {}
    path = os.path.join(xml, "GuideTrigger.xml")
    if not os.path.exists(path):
        return out
    kor = dic.lang != "eng"
    for c in idspace(path, "GuideTrigger").findall("class"):
        mid = c.get("Mastery")
        if not mid or mid == "None":
            continue
        info = ACHIEVEMENT_GRANTS.get(mid)
        if info is None:                      # a new/uncovered grant — flag it, don't silently drop
            print(f"  [warn] GuideTrigger grant '{c.get('name')}' -> mastery '{mid}' "
                  f"has no ACHIEVEMENT_GRANTS condition entry")
            condition = None
            steam = c.get("Achievement")
        else:
            condition = info[1] if kor else info[0]
            steam = info[2] or c.get("Achievement")
        src = {"type": "Achievement", "condition": condition, "trigger": c.get("name")}
        if steam and steam != "None":
            ach = dic.get(f"Achievement/{steam}/Title")
            if ach:
                src["achievement"] = ach
        out[mid] = src                        # one grant per mastery (ids are unique in the table)
    return out


# Office/apartment onboarding scenes hand out a few masteries directly via a RefillMastery /
# AcquireMastery command (xml/Dialog/Dialog_Office*.xml) — a grant channel *outside* the stage
# `MasteryAcquired` markers and the dialogue-choice pipeline (dialog_system.lua's RefillMastery
# handler calls dc:AcquireMastery). Each file is a place; the label is authored (en, kor — Korean
# matches the in-game "알버스의 방" from the StoryOfficeAlbus achievement). See DATAMINING.md.
OFFICE_GRANT_FILES = {
    os.path.join("Dialog", "Dialog_Office_Albus.xml"): ("Albus's apartment", "알버스의 방"),
    os.path.join("Dialog", "Dialog_Office.xml"):       ("the office",        "사무실"),
}


def office_grants(xml, dic):
    """{mastery_id: source-dict} for masteries handed out by an office/apartment tutorial scene
    (RefillMastery / AcquireMastery action commands). Modelled as a Story `tutorial` grant whose
    `mission` is the place. e.g. Albus's apartment grants Yearning, Swordmaster, …"""
    out = {}
    kor = dic.lang != "eng"
    for rel, (en, ko) in OFFICE_GRANT_FILES.items():
        path = os.path.join(xml, rel)
        if not os.path.exists(path):
            continue
        place = ko if kor else en
        for p in ET.parse(path).getroot().iter("property"):
            if p.get("Type") == "Action" and p.get("Command") in ("RefillMastery", "AcquireMastery") \
                    and p.get("MasteryName"):
                out.setdefault(p.get("MasteryName"),
                               {"type": "Story", "mission": place, "tutorial": True})
    return out


def technique_initial(xml):
    """{mastery_id} for masteries **available from the start** — their `Technique.xml` entry is
    `Opened="true"` by default, i.e. pre-researched (no enemy-analysis / research needed). 32 such:
    Learning, the Resistance1 set, the basic drone Modules, the TrainingManuals. (This is the real
    source of Learning — the `Worldmap.xml` division `<Reward>` that also lists it is **vestigial**:
    `Company.xml` `ActivityReport/SpecialRewardIndex` defaults to 1 = the `None` reward and nothing
    ever sets it to the Mastery index, so that path never fires.)"""
    path = os.path.join(xml, "Technique.xml")
    if not os.path.exists(path):
        return set()
    return {c.get("name") for c in idspace(path, "Technique").findall("class")
            if c.get("Opened") == "true" and c.get("name")}


def company_initial(xml):
    """{company-mastery id} for **player** company policies available from the start — their
    `SetCompanyMastery.xml` entry is `Opened="true"` (`IsInitMastery="true"`): Scavenger, Expertise,
    CustomerSatisfaction, SenseOfBelonging, Individualism. The other 3 player policies (HardFight,
    FastWork, SafetyFirst) carry no init flag — they're unlocked by a story mission (see
    `dialog_map.company_opens`). (The `Organization`-category masteries in the same tab are NPC-static
    org traits, not player-obtainable, so they get no player source.)"""
    path = os.path.join(xml, "SetCompanyMastery.xml")
    if not os.path.exists(path):
        return set()
    return {c.get("name") for c in idspace(path, "SetCompanyMastery").findall("class")
            if c.get("Opened") == "true" and c.get("name")}


def technique_data(xml):
    """(unlock_by, developing) from Technique.xml.
    - `unlock_by` {mastery_id: [prereq_id, …]} — the **reverse** of `UnLockTechnique`: *crafting/
      researching* the prereq unlocks this mastery for research (verified in-game: the unlock is
      tied to the act of crafting the prereq, e.g. King's Wealth ← Treasure Island ← AliBaba ←
      Treasure Hunter). This is the unlock chain, **not** `RequireMasteries` (which is the recipe's
      ingredient list — what you consume to craft it).
    - `developing` {mastery_id} — entries flagged `Developing="true"` (unfinished/cut content)."""
    path = os.path.join(xml, "Technique.xml")
    unlock_by, developing = {}, set()
    if not os.path.exists(path):
        return unlock_by, developing
    for c in idspace(path, "Technique").findall("class"):
        name = c.get("name")
        if not name:
            continue
        if c.get("Developing") == "true":
            developing.add(name)
        for y in (c.get("UnLockTechnique") or "").split(","):
            if y.strip():
                unlock_by.setdefault(y.strip(), []).append(name)
    return unlock_by, developing


def classify_individual(xml, dic, mclass):
    """Return {mastery_id: (group, owner)} for the special mastery pages.

    group is one of: company | individual | npc | normal.
    - company:    Category=="Company"
    - individual: Category=="PC" owned by a playable character (its StartingMastery)
    - npc:        the remaining Category=="PC" masteries, owned by the enemy unit
                  that carries them; the ownerless grade-boost ones are "Promotion".
    """
    pc_sp = idspace(os.path.join(xml, "Pc.xml"), "Pc")
    playable_ids = {pc.get("name") for pc in pc_sp.findall("class")}
    starting_owner = {}
    for pc in pc_sp.findall("class"):
        disp = dic.get(f"ObjectInfo/{pc.get('name')}/Title") or pc.get("name")
        sm = pc.find("StartingMastery")
        for p in (sm.findall("property") if sm is not None else []):
            starting_owner[p.get("value")] = disp

    pc_masteries = {n for n, c in mclass.items() if c.get("Category") == "PC"}
    beast_masteries = {n for n, c in mclass.items() if c.get("Category") == "Beast"}

    # player-obtainable drone masteries = the OS choices + AI-upgrade pool picks + craft-rolled
    # uniques. Machine masteries outside this set are enemy-drone / internal-only (e.g. Automatic
    # Suppressive Counter Attack, Control Management) → routed to Misc rather than the Drone tab.
    machine_player = {n for n, c in mclass.items()
                      if c.get("Category") == "Machine" and (c.get("Type") or "").startswith("OperatingSystem")}
    mx = os.path.join(xml, "Machine.xml")
    for c in idspace(mx, "MachineAIUpgrade").findall("class"):
        up = c.find("AIUpgrade")
        for p in (up.findall("property") if up is not None else []):
            machine_player.add(p.get("Type"))
    for c in idspace(mx, "MachineCraftUniqueMastery").findall("class"):
        machine_player.update((c.get("Type"), c.get("name")))

    # which unit(s) carry each PC/Beast-category mastery (basic + selectable), and the
    # display names of each beast family (Info "Beast_<Family>_<Variant>") so a mastery
    # carried by a whole family collapses to the family name instead of every subtype.
    carriers = collections.defaultdict(list)
    fam_members = collections.defaultdict(set)
    mon_info = {}
    for c in idspace(os.path.join(xml, "Monster.xml"), "Monster").findall("class"):
        info = c.get("Info")
        if not info:
            continue
        mon_info[c.get("name")] = info
        if info.startswith("Beast_") and info.count("_") >= 2:
            fam_members[info.split("_")[1]].add(dic.get(f"ObjectInfo/{info}/Title") or info)
        ms = c.find("Masteries")
        srcs = [c.get("BasicMastery")] + ([p.get("name") for p in ms.findall("property")] if ms is not None else [])
        for s in set(srcs):
            if s in pc_masteries or s in beast_masteries:
                carriers[s].append(info)
    # a beast also owns the category-Beast masteries it gains via Beast.xml (its evolution/
    # fixed/level slots) — e.g. the unique "I Like Anne" on one specific bat.
    for c in idspace(os.path.join(xml, "Beast.xml"), "BeastType").findall("class"):
        info = mon_info.get(c.get("Monster"))
        if not info:
            continue
        for el in c.iter():
            if (el.get("name") or el.get("Name")) in beast_masteries:
                carriers[(el.get("name") or el.get("Name"))].append(info)
    fam_display = {}                                  # family id -> most common word in its names
    for fam, names in fam_members.items():
        words = collections.Counter(w for n in names for w in n.split())
        fam_display[fam] = words.most_common(1)[0][0] if words else fam

    out = {}
    for n, c in mclass.items():
        cat = c.get("Category")
        if cat == "Set":
            # the set-bonus mastery; already shown in full on the Mastery Sets tab
            out[n] = ("set", None)
        elif cat in MODULE_CATEGORIES:
            # robot/drone parts: the five module-board categories
            out[n] = ("module", None)
        elif cat == "Machine":
            # drone-personal masteries (OS / application enhancements) — the Individual-equivalent
            # for machines; enemy-drone / internal-only ones go to Misc instead of the Drone tab
            out[n] = ("individual" if n in machine_player else "misc", None)
        elif cat == "Job":
            # innate class/race traits (each is a job's BasicMastery) — their own tab
            out[n] = ("class", None)
        elif cat in MISC_CATEGORIES:
            out[n] = ("misc", None)             # small odd buckets share a Misc tab

        elif cat == "Beast":
            # the beast equivalent of Individual masteries — shown on the Individual tab,
            # owned by the carrying beast(s); a family carried wholesale collapses to its name
            byfam, loose = collections.defaultdict(set), set()
            for info in carriers.get(n, []):
                disp = dic.get(f"ObjectInfo/{info}/Title") or info
                if info.startswith("Beast_") and info.count("_") >= 2:
                    byfam[info.split("_")[1]].add(disp)
                else:
                    loose.add(disp)
            labels = set(loose)
            for fam, members in byfam.items():
                if len(members) > 3 and fam in fam_display:
                    labels.add(fam_display[fam])          # whole family → its name
                else:
                    labels.update(members)
            out[n] = ("individual", " / ".join(sorted(labels)) if labels else None)
        elif cat in ("Company", "Organization"):
            # player company perks + the NPC-faction equivalent (Organization)
            out[n] = ("company", None)
        elif cat == "PC":
            if n in starting_owner:
                out[n] = ("individual", starting_owner[n])
            else:
                names = sorted({dic.get(f"ObjectInfo/{i}/Title") or i
                                for i in carriers.get(n, []) if i not in playable_ids})
                if names:
                    out[n] = ("npc", " / ".join(names))
                else:
                    out[n] = ("npc", "Promotion")
        elif c.get("Type") in SYSTEM_TYPES:
            # innate/granted board mastery with no learnable source — surface it in Misc
            out[n] = ("misc", None)
        else:
            out[n] = ("normal", None)
    return out


def mastery_desc(dic, name):
    """Assemble the multi-part Desc_Base/<n>/Text fields in order."""
    lines = []
    i = 1
    while True:
        v = dic.get(f"Mastery/{name}/Desc_Base/{i}/Text")
        if v is None:
            break
        lines.append(v)
        i += 1
    return "\n".join(lines)


# ----------------------------------------------------------------------------- main
# Limit-modifier masteries — curated from shared_pc.lua. Each placed mastery adds its
# Base_ApplyAmount to the named target. Slots feed the cost cap via cap = 2*slots-1.
SLOT_MODS = {
    "Basic":   ["OpenMind", "Persuasion", "SelfExamination", "Application_EnhancedFrame",
                "HardBone", "Module_FrameEnhanced", "Module_FrameOptimaztion",
                "Module_ModuleOptimization", "BeastNormalTraining", "GrowthPotential_Normal"],
    "Support": ["Flexibility", "Principlism", "ReasonablySuspects", "Module_AuxiliarySupportModule",
                "WildNatureKnowledge", "Module_ModuleOptimization", "Module_SupportModuleOptimaztion",
                "BeastSubTraining", "GrowthPotential_Sub"],
    "Attack":  ["Hysterie", "Brazenface", "Module_AuxiliaryComplementaryModule",
                "Module_ComplementaryModuleOptimaztion", "TerritorialDisputes",
                "Module_ModuleOptimization", "BeastAttackTraining", "GrowthPotential_Attack"],
    "Defence": ["TacticalRetreat", "Sophistry", "Module_AuxiliarySaftyModule",
                "Module_SaftyModuleEnhanced", "PersistentLife", "Module_ModuleOptimization",
                "BeastDefenceTraining", "GrowthPotential_Defence"],
    "Ability": ["SocialLife", "Module_AuxiliaryAIModule", "Module_AuxiliaryAIModuleEnhanced",
                "BeastAbilityTraining", "GrowthPotential_Ability"],
}
SLOT_MODS_ALL = ["IndomitableHeart", "IndomitableHeart2"]          # +slots to every category
# +cost cap to every category (shared_pc.lua Get_MaxMasteryCost_Shared_PC — human / beast / machine)
COST_MODS_ALL = ["PangOfConscience", "Consideration", "GraciousRefusal", "ForthrightStatement",
                 "Sortilege", "Egoist", "Illuminati", "KeyboardWarrior",          # human
                 "AdaptiveTraining", "ParentalLove",                              # beast
                 "Application_PowerControl", "Module_AuxiliaryPowerControl",      # machine (drones)
                 "Module_PowerProvider", "Module_PowerDeliveryOptimization"]
TOTAL_MODS = ["Frankness", "ColdRefusal", "LoveHate", "SocialLife"]        # +total training points
# ESP element -> per-category slot bonus (ESP.xml Max<Cat>MasteryCount)
ESP_CATS = [("Basic", "MaxBasicMasteryCount"), ("Support", "MaxSubMasteryCount"),
            ("Attack", "MaxAttackMasteryCount"), ("Defence", "MaxDefenceMasteryCount"),
            ("Ability", "MaxAbilityMasteryCount")]
# board category -> MasteryUnlockLevel class (Mastery.xml) holding the per-slot unlock levels
UNLOCK_KEY = {"Basic": "Normal", "Support": "Sub", "Attack": "Attack",
              "Defence": "Defence", "Ability": "Ability"}
BEAST_ELEMENTS = {"Fire", "Ice", "Lightning", "Wind", "Earth", "Water", "Spirit"}


def beast_availability(xml, dic, type_title, mclass):
    """Classify each Category=='Beast' (evolution-pick) mastery by how a *player* beast gets it:
       global (any beast can roll it), element (only beasts of that ESP element), species (specific
       families offer it via FixedEvolutionMastery) or genetic (GrowthPotential, via genetic
       modification). Returns {mastery id: (scope, display label)}; see [[share-code-format]]'s
       sibling notes on the evolution-mastery pools."""
    bx = os.path.join(xml, "Beast.xml")
    genetic = {c.get("name") for c in idspace(bx, "BeastUniqueEvolutionMastery_Genetic").findall("class")}
    unique = {c.get("name") for c in idspace(bx, "BeastUniqueEvolutionMastery").findall("class")}
    fam_title = {c.get("name"): (dic.get(f"BeastManagerCategory/{c.get('name')}/Title") or c.get("name"))
                 for c in idspace(bx, "BeastManagerCategory").findall("class")}
    fam_of = collections.defaultdict(set)                  # mastery -> offering family titles
    for c in idspace(bx, "BeastType").findall("class"):
        fam = c.get("LobbySlot")
        if fam not in fam_title:
            continue
        fe = c.find("FixedEvolutionMastery")
        for p in (fe.findall("property") if fe is not None else []):
            if p.get("Name"):
                fam_of[p.get("Name")].add(fam_title[fam])
    out = {}
    for n, c in mclass.items():
        if c.get("Category") != "Beast":
            continue
        t = c.get("Type")
        if n in genetic:
            out[n] = ("genetic", "Any beast (genetic modification)", None)
        elif n in unique:
            fams = sorted(fam_of.get(n, []))
            out[n] = ("species", " / ".join(fams) if fams else "Specific species", fams)
        elif t in BEAST_ELEMENTS:
            out[n] = ("element", type_title.get(t, t), None)
        else:                                              # Training / Nature / Gene (shared pools)
            out[n] = ("global", "Any beast", None)
    return out


def build_builder_data(xml, dic, type_title, board_cat=None, excluded_jobs=()):
    """Selectable jobs and playable characters for the mastery-board builder.
       Per-category Max counts (slots/cost-limit), basic mastery, and the accessible
       mastery Types (the job + its recursive prerequisite jobs).
       `excluded_jobs` (see compute_excluded_jobs) lists job ids to drop — Developing="true"
       jobs (incl. every grade-3 "awakened" one) and unit-less hidden classes; empty when
       --include-developing. Availability keys off this set, not Grade."""
    excluded_jobs = set(excluded_jobs)
    CATS = [("Basic", "MaxBasicMasteryCount"), ("Support", "MaxSubMasteryCount"),
            ("Attack", "MaxAttackMasteryCount"), ("Defence", "MaxDefenceMasteryCount"),
            ("Ability", "MaxAbilityMasteryCount")]
    jclass = {c.get("name"): c for c in idspace(os.path.join(xml, "Job.xml"), "Job").findall("class")}
    prereqs = {}                                    # job -> prerequisite jobs (RequireClassLv)
    for jid, c in jclass.items():
        rc = c.find("RequireClassLv")
        names = []
        for p in (rc.findall("property") if rc is not None else []):
            cond = p.find("RequireConditions")
            for q in (cond.findall("property") if cond is not None else []):
                if q.get("name") in jclass:
                    names.append(q.get("name"))
        prereqs[jid] = names

    def closure(jid):                               # job + all (recursive) prerequisite jobs
        seen, stack = set(), [jid]
        while stack:
            j = stack.pop()
            if j in seen or j not in jclass:
                continue
            seen.add(j)
            stack += prereqs.get(j, [])
        return seen

    jobs = []
    for jid, c in jclass.items():
        if jid in excluded_jobs:                    # Developing / unit-less hidden class
            continue
        # accessible types = the job + its prerequisite job tree. (RequiredESP is only a
        # class-change prerequisite; element-mastery access is the character's own element.)
        jobs.append({
            "id": jid, "title": type_title.get(jid, jid), "grade": int(c.get("Grade") or 0),
            "requireLv": int(c.get("RequireLv") or 0),
            "max": {cat: int(c.get(attr) or 0) for cat, attr in CATS},
            "basic": c.get("BasicMastery") if c.get("BasicMastery") not in (None, "None") else None,
            # raw MasteryType ids (the builder matches accessibility on raw type, not display)
            "accessTypes": sorted(set(closure(jid))),
        })
    jobs.sort(key=lambda j: (j["grade"], j["requireLv"], j["title"]))

    # PC unit Object -> innate element / starting class (object.xml Object/<obj>.ESP & .Job;
    # the .Job is the class the character actually JOINS in — often an advanced class for
    # late joiners, e.g. PC_Giselle -> Sniper, not the Order-1 base Gunman)
    obj_classes = list(idspace(os.path.join(xml, "object.xml"), "Object").findall("class"))
    obj_esp = {o.get("name"): o.get("ESP") for o in obj_classes}
    obj_job = {o.get("name"): o.get("Job") for o in obj_classes}
    pcs = []
    for c in idspace(os.path.join(xml, "Pc.xml"), "Pc").findall("class"):
        pid = c.get("name")
        ej = c.find("EnableJobs")
        jobids = [p.get("name") for p in (ej.findall("property") if ej is not None else [])
                  if p.get("name") in jclass and p.get("name") not in excluded_jobs]
        if not jobids:
            continue
        elem = obj_esp.get(c.get("Object"))
        # inherent fixed masteries (auto-placed on the board, e.g. Leton's antifreezing kit)
        fm = c.find("FixedMastery")
        fixed = []
        for p in (fm.findall("property") if fm is not None else []):
            mid = p.get("value")
            if mid:
                fixed.append({"id": mid, "name": dic.get(f"Mastery/{mid}/Base_Title", mid),
                              "cat": (board_cat or {}).get(mid)})
        pcs.append({"id": pid, "name": dic.get(f"ObjectInfo/{pid}/Title") or pid,
                    "race": "Human", "pcType": pid, "jobs": jobids,
                    "element": elem if elem and elem != "None" else None, "fixed": fixed,
                    "order": int(c.get("SlotIndex") or 999),  # canonical roster order (Pc.xml SlotIndex, = MasteryCode Pc code)
                    "startLv": int(c.get("Lv") or 1),     # join level
                    # starting class = the unit Object's Job (advanced for late joiners),
                    # falling back to the Order-1 base if it's not a selectable grade-1/2 job
                    "startJob": obj_job.get(c.get("Object")) if obj_job.get(c.get("Object")) in jobids else jobids[0],
                    "baseMax": {cat: int(c.get("Base_" + attr) or 0) for cat, attr in CATS}})
    pcs.sort(key=lambda p: (p["order"], p["name"]))   # canonical roster order (drives the builder selector + lists)

    # ---- captured beast forms (player side) -----------------------------------
    # Beasts reuse the PC board engine: each *form*'s Object gives Race=Beast / Job=<family>
    # / ESP=<element>, so slots = unit Base_Max + family-job Max<Cat> + element ESP, and
    # accessible masteries are Type-based (family + Beast + All + element). Sourced from
    # Beast.xml (the *captured* form), NOT Monster.xml (the enemy spawn form) — the enemy
    # entry only supplies the unit Base_Max slot counts and the form's join level/title.
    mon_cls = {c.get("name"): c for c in idspace(os.path.join(xml, "Monster.xml"), "Monster").findall("class")}
    fam_meta = {}                                       # family id -> {title, order}
    for c in idspace(os.path.join(xml, "Beast.xml"), "BeastManagerCategory").findall("class"):
        fam_meta[c.get("name")] = {"title": dic.get(f"BeastManagerCategory/{c.get('name')}/Title") or c.get("name"),
                                   "order": int(c.get("Order") or 0)}
    beast_families = [{"id": k, "title": v["title"], "order": v["order"]}
                      for k, v in sorted(fam_meta.items(), key=lambda kv: kv[1]["order"])]

    # captured beasts have no innate per-unit slot bonus (verified against a live save) — all
    # their slots come from the family job + element ESP. (Monster.xml's Base_Max…MasteryCount
    # belongs to the *enemy* spawn form, so it must NOT be used here.)
    def beast_base_max(mon):
        return {cat: 0 for cat, _ in CATS}

    beasts = []
    for c in idspace(os.path.join(xml, "Beast.xml"), "BeastType").findall("class"):
        fam = c.get("LobbySlot")
        if not fam or fam == "None" or fam not in fam_meta:
            continue                                    # skip non-roster / helper entries
        mon = c.get("Monster") or c.get("name")
        job = obj_job.get(mon)                           # Object name coincides with the monster id
        if job not in jclass:
            continue                                     # only forms with a real family job
        elem = obj_esp.get(mon)
        info = mon_cls[mon].get("Info") if mon in mon_cls else None
        evo = c.find("Evolutions")
        evolves = sorted(({"id": p.get("name"), "order": int(p.get("Order") or 0),
                           "requireLv": int(p.get("RequireLv") or 0),
                           "item": p.get("RequireItem") if p.get("RequireItem") not in (None, "None", "") else None}
                          for p in (evo.findall("property") if evo is not None else [])),
                         key=lambda e: e["order"])
        # species-specific unique evolution masteries this form can be offered (FixedEvolutionMastery
        # table); the global Training/Nature/Gene/element pools are derived app-side from beastEvo
        fe = c.find("FixedEvolutionMastery")
        fixed_evo = []
        for p in (fe.findall("property") if fe is not None else []):
            nm = p.get("Name")
            if nm and nm not in fixed_evo:
                fixed_evo.append(nm)
        beasts.append({
            "id": mon, "name": (dic.get(f"ObjectInfo/{info}/Title") if info else None) or info or mon,
            "race": "Beast", "family": fam, "familyTitle": fam_meta[fam]["title"],
            "pcType": fam, "jobs": [job],
            "element": elem if elem and elem != "None" else None,
            "stage": int(c.get("EvolutionStage") or 1), "maxStage": int(c.get("EvolutionMaxStage") or 1),
            "evoType": c.get("EvolutionType") or "Normal",
            "baseMax": beast_base_max(mon),
            "startLv": int(mon_cls[mon].get("Lv") or 1) if mon in mon_cls else 1,
            "fixed": [], "evolvesTo": evolves, "fixedEvo": fixed_evo})
    # back-links for the "sort direct evolutions to the top" selector behaviour
    parents = collections.defaultdict(list)
    for b in beasts:
        for e in b["evolvesTo"]:
            parents[e["id"]].append(b["id"])
    fam_order = {f["id"]: f["order"] for f in beast_families}
    for b in beasts:
        b["evolvesFrom"] = sorted(parents.get(b["id"], []))
    beasts.sort(key=lambda b: (fam_order.get(b["family"], 99), b["stage"], b["name"]))
    have_forms = {b["family"] for b in beasts}             # drop meta families (e.g. "All") with no forms
    beast_families = [f for f in beast_families if f["id"] in have_forms]
    # localized evolution-stage names per EvolutionType, keyed by the form's EvolutionStage
    # (= 1-based property index). Normal = Growth/Adolescent/Mature; EggStart (Draki, egg-hatched)
    # has 4: Babyhood/Growth/Adolescent/Mature. Pulled via keymap so other languages work too.
    beast_stages = {}
    for cls in idspace(os.path.join(xml, "Beast.xml"), "BeastEvolutionType").findall("class"):
        tname = cls.get("name")
        beast_stages[tname] = {i: dic.get(f"BeastEvolutionType/{tname}/{i}/Title", p.get("Title"))
                               for i, p in enumerate(cls.findall("property"), 1)}

    # catalog of beast evolution masteries (the 1-of-3 picks per evolution). Global by Type
    # (Training/Nature/Gene) or element-gated (Type == a Fire/Ice/… element); `unique` ones are
    # excluded from the global pools and offered only via a form's fixedEvo. The app builds each
    # beast's pool from this + the beast's element + form.fixedEvo (so nothing is duplicated per beast).
    beast_unique = set()
    for idn in ("BeastUniqueEvolutionMastery", "BeastUniqueEvolutionMastery_Genetic"):
        for c in idspace(os.path.join(xml, "Beast.xml"), idn).findall("class"):
            beast_unique.add(c.get("name"))
    beast_evo = []
    for c in idspace(os.path.join(xml, "Mastery.xml"), "Mastery").findall("class"):
        if c.get("Category") == "Beast":
            n = c.get("name")
            beast_evo.append({"id": n, "name": dic.get(f"Mastery/{n}/Base_Title") or n,
                              "type": c.get("Type"), "unique": n in beast_unique})

    # ESP element -> per-category slot bonus
    esp_slots = {}
    for c in idspace(os.path.join(xml, "ESP.xml"), "ESP").findall("class"):
        d = {cat: int(c.get(a) or 0) for cat, a in ESP_CATS if int(c.get(a) or 0)}
        if d:
            esp_slots[c.get("name")] = d

    # per-category slot unlock schedule (Mastery.xml MasteryUnlockLevel/<key>.Unlock)
    mul = {}
    for c in idspace(os.path.join(xml, "Mastery.xml"), "MasteryUnlockLevel").findall("class"):
        u = c.find("Unlock")
        mul[c.get("name")] = [int(p.get("value") or 0) for p in (u.findall("property") if u is not None else [])]
    slot_unlock = {cat: mul.get(key, []) for cat, key in UNLOCK_KEY.items()}

    # board limit-modifier masteries: id -> [{kind, cat, amt}]
    mcls = {c.get("name"): c for c in idspace(os.path.join(xml, "Mastery.xml"), "Mastery").findall("class")}

    def applyamt(mid):
        v = mcls[mid].get("Base_ApplyAmount") if mid in mcls else None
        try:
            return int(float(v or 0))
        except (TypeError, ValueError):
            return 0
    mods = {}

    def addmod(mid, eff):
        if mid in mcls:
            mods.setdefault(mid, []).append(eff)
    for cat, names in SLOT_MODS.items():
        for n in names:
            addmod(n, {"kind": "slot", "cat": cat, "amt": applyamt(n)})
    for n in SLOT_MODS_ALL:
        addmod(n, {"kind": "slot", "cat": "all", "amt": applyamt(n)})
    for n in COST_MODS_ALL:
        addmod(n, {"kind": "cost", "cat": "all", "amt": applyamt(n)})
    for n in TOTAL_MODS:
        addmod(n, {"kind": "total", "amt": applyamt(n)})

    return jobs, pcs, esp_slots, mods, slot_unlock, beasts, beast_families, beast_stages, beast_evo


def build_machine_data(xml, dic, type_title, cat_title_en=None):
    """Drone board-builder data. A drone = Frame (slots) + SP Structure (element) + OS (reinforcement
       pool); the Object is Mon_DroneFrame_<Frame>_<SP> (Race=Machine, Job=Drone, ESP=<SP>). Drones
       reuse the PC slot/cost engine; the board's 5 columns are the *module* categories mapped to a
       standard slot category via EquipSlot (FrameModule→Basic, ComplementaryModule→Attack, …).
       OS / SP choices reference existing masteries (the OS-choice masteries; the ESP-category mastery
       per element) so the builder can show their cards instead of duplicating name/description."""
    mx = os.path.join(xml, "Machine.xml")
    objs = {o.get("name"): o for o in idspace(os.path.join(xml, "object.xml"), "Object").findall("class")}
    mons = {c.get("name"): c for c in idspace(os.path.join(xml, "Monster.xml"), "Monster").findall("class")}
    esp_of = {}                                         # SP element -> its ESP-category mastery (the card)
    for c in idspace(os.path.join(xml, "Mastery.xml"), "Mastery").findall("class"):
        if c.get("Category") == "ESP" and c.get("Type") in ("Heat", "Info", "Charge"):
            esp_of.setdefault(c.get("Type"), c.get("name"))
    CATS = [("Basic", "MaxBasicMasteryCount"), ("Support", "MaxSubMasteryCount"),
            ("Attack", "MaxAttackMasteryCount"), ("Defence", "MaxDefenceMasteryCount"),
            ("Ability", "MaxAbilityMasteryCount")]
    frames = []
    for c in idspace(mx, "MachineCategory").findall("class"):
        mon = c.get("Monster"); info = mons[mon].get("Info") if mon in mons else None
        frames.append({"id": c.get("name"), "order": int(c.get("Order") or 99),
                       "name": (dic.get(f"ObjectInfo/{info}/Title") if info else None) or c.get("name"),
                       "opened": c.get("Opened") == "true",
                       "slots": {cat: int(c.get(attr) or 0) for cat, attr in CATS}})
    frames.sort(key=lambda f: f["order"])
    sp = sorted(({"id": c.get("name"), "name": type_title.get(c.get("name"), c.get("name")),
                  "order": int(c.get("Order") or 99), "mastery": esp_of.get(c.get("name"))}
                 for c in idspace(mx, "MachineSPType").findall("class")), key=lambda s: s["order"])
    os_list = [{"id": n} for n in ("Windows", "Linux", "MacOS")]   # id == the OS-choice mastery id
    units = []                                          # a built drone = each Frame × SP combination
    for f in frames:
        for s in sp:
            oid = f"Mon_DroneFrame_{f['id']}_{s['id']}"
            o = objs.get(oid)
            if o is None:
                continue
            elem = o.get("ESP")
            units.append({"id": oid, "name": f["name"], "frame": f["id"], "sp": s["id"],
                          "race": "Machine", "jobs": ["Drone"], "opened": f["opened"],
                          # raw ESP id (e.g. "Heat") — keys espSlots AND matches the SP module raw type
                          "element": elem if elem and elem != "None" else None,
                          "baseMax": f["slots"]})
    reinf = {}                                          # reinforcement stage names (1..4)
    norm = next((c for c in idspace(mx, "MachineReinforcementType").findall("class") if c.get("name") == "Normal"), None)
    for i, p in enumerate(norm.findall("property") if norm is not None else [], 1):
        reinf[i] = dic.get(f"MachineReinforcementType/Normal/{i}/Title", p.get("Title"))
    es = {c.get("name"): c.get("EquipSlot") for c in idspace(os.path.join(xml, "Mastery.xml"), "MasteryCategory").findall("class")}
    slot_name = {"Sub": "Support"}                      # EquipSlot says 'Sub'; the board/engine cat is 'Support'
    mod_cats = [{"cat": n, "name": dic.get(f"MasteryCategory/{n}/Title") or n,
                 "nameEn": (cat_title_en or {}).get(n) or dic.get(f"MasteryCategory/{n}/Title") or n,
                 "slot": slot_name.get(es.get(n) or "", es.get(n))}
                for n in ("FrameModule", "ComplementaryModule", "SupportModule", "SaftyModule", "AIModule")]
    ai = {}                                             # OS -> reinforcement AI-upgrade pool [{id, lv}]
    for c in idspace(mx, "MachineAIUpgrade").findall("class"):
        up = c.find("AIUpgrade")
        ai[c.get("name")] = [{"id": p.get("Type"), "lv": int(p.get("Lv") or 1)}
                             for p in (up.findall("property") if up is not None else [])]
    # craft-unique pick: at construction a drone rolls ONE passive trait from the groups whose
    # Count > 0 (Power has Count 0 → excluded; only Compatibility + Performance reach the player).
    grp_count = {c.get("name"): int(c.get("Count") or 0)
                 for c in idspace(mx, "MachineCraftUniqueMasteryGroup").findall("class")}
    craft = [c.get("name") for c in idspace(mx, "MachineCraftUniqueMastery").findall("class")
             if grp_count.get(c.get("Group"), 0) > 0]
    return {"frames": frames, "sp": sp, "os": os_list, "units": units,
            "reinf": reinf, "moduleCats": mod_cats, "aiUpgrade": ai, "craft": craft}


# leading shape token of a TargetRange id (Sphere8_… / Box1_… / Dot) -> its radius
_RANGE_RE = re.compile(r"^[A-Za-z]+?(\d+)")


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _item_slot_label(item_type):
    """The Slot-column label for an ability granted by an item of this `Item.xml` Type, or None.
    These abilities have no real ability SlotType (Basic/Normal/Ultimate), so the otherwise-empty
    Slot column is reused to record *how* the ability is accessed (which consumable/gadget grants it)."""
    if item_type in ("Potion", "Intravenous"):  # Intravenous = an IV solution, a Potion_* ability
        return "Potion"
    if item_type == "Grenade":
        return "Grenade"
    if item_type == "Spray":
        return "Spray"
    if item_type and ("Device" in item_type or item_type in ("Gear", "Sneakers", "Bracelet")):
        return "Device"                          # gadget/equipment-granted (mostly DrivingDevice_*/OuterDevice_*)
    return None


def ability_item_sources(xml):
    """{ability-id: Slot label} for abilities granted by a consumable/gadget item — read from each
    `Item.xml` class's `Ability="<id>"` + its `Type` (mapped via _item_slot_label). A higher-rarity
    item can swap in an upgraded `<id>2` variant (e.g. Epic sprays → ShakeShot_Wind2 "Upgraded Shaking
    Shot", wired in shared_ability.lua, not Item.xml), so each spray-granted id also tags its `<id>2`
    sibling."""
    src = {}
    for c in ET.parse(os.path.join(xml, "Item.xml")).getroot().iter("class"):
        aid = c.get("Ability")
        label = _item_slot_label(c.get("Type"))
        if aid and aid not in (None, "None") and label:
            src.setdefault(aid, label)
    for aid, label in list(src.items()):         # fold in rarity-upgrade variants (Spray's ShakeShot_*2)
        if label == "Spray":
            src.setdefault(aid + "2", label)
    return src


# playable-character battle units: PC_<Name> (the roster's in-battle unit) and Mon_PC_<Name>
# (story/guest versions — controllable on some maps, and meaningful as named enemies). The captured
# group is the character base name; trailing variant suffixes (_Black/_Hologram/…) are ignored.
_PC_UNIT_RE = re.compile(r"(?:Mon_)?PC_([A-Za-z]+)")


def build_player_abilities(xml, dic, beasts, machine_units, item_sources, ability_cls):
    """The set of abilities a player can actually field, plus each one's owning unit(s).

    An ability is player-usable if it comes from any of:
      - a **character's class unlocks** — `Pc.xml` `<char>/EnableJobs/<job>/<Abilities>` (each
        `<property Name= RequireLv=>` is an ability learned by levelling that class — the authoritative,
        character-specific "granted by levelling" source);
      - a **character battle unit** — `PC_<Name>` / `Mon_PC_<Name>` in `object.xml` (its `Ability` list —
        adds the loadout's sub-abilities and the story/guest `Mon_PC_*` chars with no Pc.xml entry);
      - **equipment / any unit** — every `Item.xml` `Ability` (weapons, devices, potions, grenades, sprays),
        plus the spray `<id>2` rarity-upgrade siblings carried in `item_sources` (ability_item_sources);
      - a **mastery that grants** it — `Mastery.xml` `Ability` (NOT `ModifyAbility`/`ChainAbility`, whose
        targets are reinforced abilities already reachable elsewhere, or chained sub-effects);
      - a **player beast/drone** — the `Ability` list of every roster beast/drone unit;
      - an **`AutoActiveAbility` toggle-companion** of an already-kept ability — activating/holding X
        auto-grants Y (the toggle's other stance, e.g. Ferocious Tima → Nimble Tima; the `RemoveBuff`
        pair mutually disables). Followed transitively, inheriting the source's owner. The toggle-*off*
        `*_Disable` companions this also reaches are culled downstream by `build_abilities`.
    Everything else is enemy-/NPC-/effect-only and dropped from the Abilities tab. (Drone units carry
    no `Ability` of their own — their abilities come via equipped devices, i.e. the `Item.xml` path.)

    NB two *sibling* lists under each job are **NOT** sources, because they're job-templated and
    over-list (e.g. both tag Giselle's `BattlebreakerSnipe` onto Kylie, who never unlocks it): the
    `<ActiveAbility>` true/false flag set, and `<AbilityPriority>` — a per-job *AI targeting-priority*
    table with `<Normal>`/`<Danger>`/`<Rage>` rows (the priorities a unit uses normally / at low HP /
    while berserk-`Rage`-afflicted). Only `<Abilities>` is character-accurate; use it, not those.

    Returns (player_set, owners, auto_source). owners[aid] = sorted display labels — character names
    (`ObjectInfo/<Name>/Title`) and beast family titles — for the "which unit has it" badge. Item /
    mastery / weapon abilities carry no owner (the Slot column already conveys their source).
    auto_source[aid] = the `AutoActiveAbility` source that reached a captured companion (so it can
    inherit that source's Slot access-source)."""
    obj = {c.get("name"): c for c in ET.parse(os.path.join(xml, "object.xml")).getroot().iter("class")}

    def unit_abilities(uid):
        c = obj.get(uid)
        return [a for a in re.split(r"[,\s]+", (c.get("Ability") or "").strip()) if a] if c is not None else []

    player = set(item_sources)                       # spray <id>2 upgrades + slotless item abilities
    char_owner = collections.defaultdict(set)        # ability -> {character base name}
    beast_owner = collections.defaultdict(set)        # ability -> {beast family title}

    # class unlocks — the authoritative per-character source: `Pc.xml` `<char>/EnableJobs/<job>/
    # <Abilities>` (each `<property Name= RequireLv=>` is an ability the character learns by levelling
    # that class). Character-specific, so it correctly excludes Giselle's snipe from Kylie — unlike the
    # sibling `<ActiveAbility>` flag set and `<AbilityPriority>` AI table, which are job-templated and
    # over-list. Drives both reachability and owner attribution.
    for pc in idspace(os.path.join(xml, "Pc.xml"), "Pc").findall("class"):
        ej = pc.find("EnableJobs")
        for a in (ej.iter("Abilities") if ej is not None else ()):
            for p in a.findall("property"):
                if p.get("Name"):
                    player.add(p.get("Name")); char_owner[p.get("Name")].add(pc.get("name"))

    for uid in obj:                                  # battle units add their loadout's sub-abilities + the story/guest
        if not uid:                                  # object.xml <class> with no name attr (never happens; keeps types honest)
            continue
        m = _PC_UNIT_RE.match(uid)                   # chars (Mon_PC_*, which have no Pc.xml class-unlock entry)
        if m and uid.startswith(("PC_", "Mon_PC_")):
            for aid in unit_abilities(uid):
                player.add(aid); char_owner[aid].add(m.group(1))

    for c in ET.parse(os.path.join(xml, "Item.xml")).getroot().iter("class"):   # equipment / any unit
        if c.get("Ability") and c.get("Ability") != "None":
            player.add(c.get("Ability"))
    for c in idspace(os.path.join(xml, "Mastery.xml"), "Mastery").findall("class"):   # masteries that *grant*
        if c.get("Ability") and c.get("Ability") != "None" and c.get("Category") != "System":
            player.add(c.get("Ability"))                       # only `Ability` (grants) — NOT ModifyAbility/
    #                                                          # ChainAbility (reinforced/chained sub-effects,
    #                                                          # already reachable), and NOT `Category="System"`
    #                                                          # masteries — those are environmental hazards
    #                                                          # (a poison puddle, an obstacle), not player
    #                                                          # masteries, so their `Ability` (e.g. Toxic Leak)
    #                                                          # is a hazard attack the player can never field.

    for b in beasts:                                 # player beasts (owner = family)
        for aid in unit_abilities(b["id"]):
            player.add(aid); beast_owner[aid].add(b.get("familyTitle") or b.get("family"))
    for u in machine_units:                          # player drones (no own Ability; device-granted)
        for aid in unit_abilities(u["id"]):
            player.add(aid)

    # AutoActiveAbility closure — a kept ability's toggle-companion is itself player-usable. Follow
    # transitively, inheriting the source's owner so the companion shows the same unit badge, and
    # record `auto_source[tgt] = src` so build_abilities can inherit the source's Slot access-source too.
    auto_source = {}
    frontier = list(player)
    while frontier:
        src = frontier.pop()
        c = ability_cls.get(src)
        if c is None:
            continue
        for tgt in re.split(r"[,\s]+", (c.get("AutoActiveAbility") or "").strip()):
            if tgt and tgt != "None" and tgt not in player:
                player.add(tgt); frontier.append(tgt); auto_source[tgt] = src
                if char_owner.get(src):
                    char_owner[tgt] |= char_owner[src]
                if beast_owner.get(src):
                    beast_owner[tgt] |= beast_owner[src]

    owners = {}
    for aid in set(char_owner) | set(beast_owner):
        labels = {dic.get(f"ObjectInfo/{n}/Title") or n for n in char_owner.get(aid, ())}
        labels |= {fam for fam in beast_owner.get(aid, ()) if fam}
        owners[aid] = sorted(labels)
    return player, owners, auto_source


def build_abilities(dic, ability_cls, mclass, item_sources, player_set, owners, auto_source):
    """Reference rows for the Abilities tab: each named ability's slot/type/element, action cost,
    SP, cooldown, cast delay, range, targets, resolved effect text (resolve_description with the
    Ability idspace), and the masteries that grant or modify it. Skipped: unnamed internal abilities
    (no `Ability/<id>/Title`), `Type="Interaction"` / `Type="Move"` abilities (context-sensitive
    object interactions and generic movement), `*_Disable` abilities (the
    "Remove/Deactivate <aura>" toggle-off companions), and `*TrapActivate` abilities (the detonation
    an already-placed `*Trap` fires when triggered) — none are real, player-placeable abilities.
    Also dropped: anything **not in `player_set`** (see build_player_abilities) — i.e. enemy-/NPC-/
    effect-only abilities the player can never field. Each kept row gets an `owners` list (the
    character(s) / beast family that have it) for the "which unit has it" badge.

    Item-/mastery-granted abilities have no real SlotType (Basic/Normal/Ultimate), so the otherwise
    -empty Slot column records *how* they're accessed instead: Potion / Grenade / Spray / Device from
    `item_sources` (ability_item_sources), else "Mastery" if a mastery grants it. Item source wins."""
    # cross-link: mastery -> ability it grants (Ability) or modifies (ModifyAbility/ChainAbility)
    grants, mods = collections.defaultdict(set), collections.defaultdict(set)
    for mid, c in mclass.items():
        nm = dic.get(f"Mastery/{mid}/Base_Title") or mid
        for attr, bucket in (("Ability", grants), ("ModifyAbility", mods), ("ChainAbility", mods)):
            a = c.get(attr)
            if a and a != "None":
                bucket[a].add(nm)

    def parse_range(tr):
        if not tr or tr in ("None", ""):
            return None
        if tr.startswith(("Dot", "Self")):
            return 0                                    # self-targeted
        m = _RANGE_RE.match(tr)
        return int(m.group(1)) if m else None

    def title_or_raw(idspace_, key):                    # localized Title, else the raw id, else None
        if key in (None, "None", ""):
            return None
        hit = dic.get(f"{idspace_}/{key}/Title")
        if not hit:
            resolve_desc.note_unresolved("title", idspace_, key)
        return hit or key

    abilities = []
    for aid, c in ability_cls.items():
        title = dic.get(f"Ability/{aid}/Title")
        if not title:                                   # unnamed internal ability — not player-facing
            continue
        if c.get("Type") in ("Interaction", "Move"):    # context-sensitive object interaction / generic movement
            continue                                     # — not a combat ability. Even the player-gated
        #                                                # interactions (Extractor's Energy Extraction, the
        #                                                # Fuel/Unlocker-granted Fuel Filling/Disarm Trap,
        #                                                # Kylie's Hacking/Repair) are `Sphere1_Interaction`
        #                                                # map-object actions with name-only descriptions, so
        #                                                # all 47 are dropped. A mastery's "Grants ability"
        #                                                # chip to one degrades to plain text (abilityChip).
        if aid.endswith("_Disable"):                    # "Remove/Deactivate <aura>" toggle-off companion, not a real ability
            continue
        if aid.endswith("TrapActivate"):                # the detonation an already-placed `*Trap` fires when triggered, not a placeable ability
            continue
        if aid not in player_set:                       # enemy-/NPC-/effect-only — the player can never field it
            continue
        slot = c.get("SlotType")
        granted_src = aid                               # whose granting masteries this row's grantedBy shows
        if slot in (None, "None", ""):                  # no real slot — record the access source instead
            slot = item_sources.get(aid) or ("Mastery" if aid in grants else None)
            src = aid                                    # AutoActiveAbility companions (Nimble Tima ←
            while slot is None and src in auto_source:   # Ferocious Tima) inherit their source's access
                src = auto_source[src]                   # source (e.g. "Mastery") + granting masteries,
                slot = item_sources.get(src) or ("Mastery" if src in grants else None)
            if aid not in grants:                        # a companion → inherit the source's grantedBy too
                granted_src = src
        desc = resolve_description(dic, c, "Ability")    # refs are inline sentinel markup in desc
        if not desc.strip():                             # subcommand abilities (e.g. Call of Fire) carry
            subs = [t.strip() for t in (c.get("AutoActiveAbility") or "").split(",")]  # only a runtime
            subs = [t for t in subs if t and not t.endswith("_Disable") and t in player_set]  # $SubAbility
            if subs:                                     # Message$ — list the sub-abilities they open, as chips
                header = dic.get("WordCollection/AbilitySubMenu/Text", "Ability Submenu")
                desc = header + "\n" + indent_block("\n".join(
                    ref_markup("ability", dic.get(f"Ability/{t}/Title") or t) for t in subs))
        abilities.append({
            "id": aid, "name": title,
            "type": dic.get(f"AbilityType/{c.get('Type')}/Title") or c.get("Type"),
            "typeRaw": c.get("Type") if c.get("Type") not in (None, "None", "") else None,
            "element": title_or_raw("AbilitySubType", c.get("SubType")),  # element / physical class — AbilitySubType
            # covers both the elements (Fire/Ice/…) and the physical classes (Slashing/Blunt/Piercing/EMP); the
            # MasteryType idspace only has the elements, so physical classes leaked as raw English ids.
            "slot": slot if slot not in (None, "None", "") else None,
            "cost": _num(c.get("Cost")),
            "sp": _num(c.get("SP")) or None,
            "cooldown": _num(c.get("CoolTime")) or None,
            "castDelay": _num(c.get("CastDelay")) or None,
            "range": parse_range(c.get("TargetRange")),
            "targets": _num(c.get("MaximumTargetCount")) or None,
            "hitRate": title_or_raw("AbilityHitRateType", c.get("HitRateType")),  # localize Melee/Ranged/…
            "description": desc,
            "flavor": dic.get(f"Ability/{aid}/FlavorText"),
            "owners": owners.get(aid, []),                  # character(s)/beast family that field it (badge)
            "grantedBy": sorted(grants.get(granted_src, ())),
            "modifiedBy": sorted(mods.get(aid, ())),
        })
    for a in abilities:                 # drop empty owner list to keep the payload small
        if not a["owners"]:
            del a["owners"]
    abilities.sort(key=lambda a: a["name"].lower())
    return abilities


def build_buffs(dic, buff_cls, status_fmt):
    """Name → effect-text lookup for the buffs named in mastery/ability/set descriptions, so the
    web tool can show the in-game nested tooltip on hover. Keyed by the buff's display Title (the
    exact string that appears in a description); only buffs whose `describe_buff` yields effect
    text are included. A few buffs share a Title — keep the first non-empty."""
    out = {}
    for bid, c in buff_cls.items():
        title = dic.get(f"Buff/{bid}/Title")
        if not title or title in out:
            continue
        # require a substantive effect (a bare duration is not a useful tooltip)
        if not describe_buff(dic, c, status_fmt, with_duration=False).strip():
            continue
        out[title] = describe_buff(dic, c, status_fmt)
    return out


def build_dialogue_buffs(dialogues, dic, buff_cls, status_fmt):
    """{buff id → {t: Title, e: effect text}} for the buffs a dialogue "gains <Buff>" consequence
    references. Keyed by *id* (not Title) so colliding Titles resolve to the exact buff — the enemy
    "Anger" state and the Excitement stat buff both read "Rage"/분노, and Title-keying picked the
    wrong one. Only buffs with substantive effect text are kept; the rest fall back to plain text."""
    ids = {c["buff"] for st in dialogues for d in st.get("decisions", [])
           for o in d["options"] for c in o.get("consequences", [])
           if c.get("kind") == "buff" and c.get("buff")}
    out = {}
    for bid in sorted(ids):
        c = buff_cls.get(bid)
        if c is None:
            continue
        eff = describe_buff(dic, c, status_fmt)
        if eff.strip():
            out[bid] = {"t": dic.get(f"Buff/{bid}/Title") or bid, "e": eff}
    return out


def build_buff_groups(dic, buff_cls, group_cls):
    """Title → member-buff-titles for each buff *group* (e.g. Bleeding → [Bleeding, Severe Bleeding,
    Rupture]). A description that references a group (`$MasteryBuffGroup$`) links to a group card
    listing these members (the web tool pulls each member's effect from the buff lookup). Keyed by
    the group's display Title — the exact string that appears in the description."""
    members = collections.defaultdict(list)
    for bid, c in buff_cls.items():
        g = c.get("Group")
        if g and g != "None":
            mt = dic.get(f"Buff/{bid}/Title")
            if mt and mt not in members[g]:
                members[g].append(mt)
    out = {}
    for gc in group_cls:
        gt = dic.get(f"BuffGroup/{gc.get('name')}/Title")
        ms = members.get(gc.get("name"), [])
        if gt and ms:
            out[gt] = ms
    return out


def joint_training_teams(xml, dic, exclude_dev):
    """{clone_id: [localized faction-team titles]} for `Mon_JointTraining_*` clones that belong to a
    *live* team — one whose `MatchingRule` is not Developing. `JointTrainingBotMatchingRule` flags
    **`Beast`** as `Developing="true"` (the cut beast-arena packs: `RandomNamed_Beast`, the Iron-Forest
    Tima/Neguri/Draki/Dorori packs, `Crammy_GoldSand`, `Beast_Underway`); `Regular` (Story — train vs
    Valhalla PD, e.g. `VHPD`/`VHPD2`), `Extra` (the faction squads) and `Player` are live. The generic
    pools (`RandomNamed*`) keep a clone live but add no faction tag. A clone *absent* from this map is
    reachable only via a Developing-rule team, so it's not a real Joint Training appearance and gets
    dropped. With `--include-developing`, every matching rule counts as live."""
    jt = os.path.join(xml, "JointTraining.xml")
    dev_rules = {c.get("name") for c in idspace(jt, "JointTrainingBotMatchingRule").findall("class")
                 if exclude_dev and is_developing(c)}
    out = {}
    for c in idspace(jt, "JointTrainingBotTeam").findall("class"):
        if c.get("MatchingRule") in dev_rules:
            continue                                   # team only reachable via a Developing rule
        title = dic.get(f"JointTrainingBotTeam/{c.get('name')}/Title") or c.get("Title")
        faction = not (c.get("name") or "").startswith("RandomNamed")
        mp = c.find("MemberPreset")
        for grp in (mp.findall("property") if mp is not None else []):
            mem = grp.find("Members")
            for p in (mem.findall("property") if mem is not None else []):
                if p.get("Mon"):
                    titles = out.setdefault(p.get("Mon"), [])
                    if faction and title not in titles:
                        titles.append(title)
    for titles in out.values():
        titles.sort()
    return out


def main():
    a = parse_args()
    resolve_desc.REPORT_UNRESOLVED = a.report_unresolved
    xml = os.path.join(a.data, "xml")
    dic = Dictionary(a.game, a.lang)
    os.makedirs(a.out, exist_ok=True)
    # drop Developing="true" content (unfinished / engine-disabled) unless asked to keep it.
    # Only Job.xml and MasterySet.xml among the files read here carry the flag.
    exclude_dev = not a.include_developing
    dev_skips = collections.Counter()        # tally of what got dropped, for the run summary

    # --- mastery types & categories (in-game display names) ------------------
    type_title = {}
    for c in idspace(os.path.join(xml, "Mastery.xml"), "MasteryType").findall("class"):
        n = c.get("name")
        type_title[n] = dic.get(f"MasteryType/{n}/Title", n)

    def type_display(tid):
        # the per-policy company types (Company_CustomerSatisfaction, …) ship no MasteryType title
        # and would render as raw ids in every language; the policy is named on the mastery itself,
        # so collapse the type to the parent "Company" (localized via MasteryType/Company/Title).
        if tid and tid.startswith("Company_"):
            return type_title.get("Company", tid)
        return type_title.get(tid, tid)

    def dlc_name(dlc_id):
        # a set's raw DLC attribute is a CamelCase id (CrimsonCrow / WhiteLionAndBlackWitch);
        # localize it via DLC/<id>/Title, which wraps the short name in a franchise prefix
        # ("Troubleshooter: … Extra Story - <name> -"), so take the last " - " segment. Falls
        # back to the raw id if there's no dictionary entry.
        if not dlc_id or dlc_id == "None":
            return dlc_id
        full = dic.get(f"DLC/{dlc_id}/Title")
        if not full:
            return dlc_id
        name = full.split(" - ", 1)[1] if " - " in full else full   # drop the franchise prefix
        name = re.sub(r"^[\s\-]+|[\s\-]+$", "", name)               # trim surrounding dashes/space
        return name or full

    # category id -> in-game name (e.g. Sub -> Support, Normal -> Basic, Job -> Class)
    # The *English* category title is the stable key the web app filters/colours by (sub-tabs,
    # board categories, CAT_COLOR), so resolve it from the eng dictionary even in a non-English
    # run — emitted as `categoryRaw` — while `category` carries the localized display name.
    dic_en = dic if a.lang == "eng" else Dictionary(a.game, "eng")
    cat_title = {}
    cat_title_en = {}
    for c in idspace(os.path.join(xml, "Mastery.xml"), "MasteryCategory").findall("class"):
        n = c.get("name")
        cat_title[n] = dic.get(f"MasteryCategory/{n}/Title", n)
        cat_title_en[n] = dic_en.get(f"MasteryCategory/{n}/Title", n)

    # --- monster display names + monster -> masteries ------------------------
    mon_sp = idspace(os.path.join(xml, "Monster.xml"), "Monster")

    def monster_name(c):
        info = c.get("Info")
        title = dic.get(f"ObjectInfo/{info}/Title") if info else None
        return title or info or c.get("name")

    mon_name_by_id = {c.get("name"): monster_name(c) for c in mon_sp.findall("class")}

    sources = collections.defaultdict(list)   # mastery name -> [source dict]
    for c in mon_sp.findall("class"):
        if c.get("name") == "_DUMMY_":
            continue
        # civilians (Civil_*/Mon_Civil*) are rescue NPCs / neutrals — never a hostile enemy you can
        # analyse, so they're not a real mastery source (e.g. Civil_Dembel "carries" AliBaba but you
        # only ever meet him as an ally/neutral; AliBaba's real source is the research chain). Every
        # mastery a civilian carries also has a real enemy carrier or another channel, so dropping
        # them orphans nothing.
        if (c.get("name") or "").startswith(("Civil_", "Mon_Civil")):
            continue
        ms = c.find("Masteries")
        if ms is None:
            continue
        nm = monster_name(c)
        lv = c.get("Lv")
        grade = c.get("Grade")
        for p in ms.findall("property"):
            sources[p.get("name")].append(
                {"type": "Enemy", "name": nm, "id": c.get("name"), "lv": lv, "grade": grade})

    # job basic masteries as an additional source
    def job_title(jid):
        return (dic.get(f"MasteryType/{jid}/Title") or dic.get(f"Job/{jid}/Title") or jid)

    job_cls = {c.get("name"): c for c in idspace(os.path.join(xml, "Job.xml"), "Job").findall("class")}
    # jobs to drop when excluding unavailable content: Developing + unit-less hidden classes
    excluded_jobs = compute_excluded_jobs(xml, job_cls, exclude_dev)

    for jid, c in job_cls.items():
        if jid in excluded_jobs:
            dev_skips["job basic-mastery sources"] += 1
            continue
        bm = c.get("BasicMastery")
        if bm and bm != "None":
            sources[bm].append({"type": "Job", "name": f"{job_title(jid)} (basic mastery)",
                                 "id": jid, "lv": c.get("RequireLv"), "grade": None})

    # character job-level unlocks: levelling a specific character in a specific job
    # globally unlocks the listed masteries for selection.
    # Pc -> EnableJobs -> property[job] -> Masteries -> property[Name, RequireLv]
    for pc in idspace(os.path.join(xml, "Pc.xml"), "Pc").findall("class"):
        pid = pc.get("name")
        cname = dic.get(f"ObjectInfo/{pid}/Title") or pid
        ej = pc.find("EnableJobs")
        if ej is None:
            continue
        for job in ej.findall("property"):
            if job.get("name") in excluded_jobs:     # engine-disabled / cut class: unlocks unreachable
                dev_skips["character job-level unlocks"] += 1
                continue
            jt = job_title(job.get("name"))
            ms = job.find("Masteries")
            for m in (ms.findall("property") if ms is not None else []):
                lv = m.get("RequireLv")
                sources[m.get("Name")].append(
                    {"type": "Character", "name": f"{cname} as {jt} (Lv {lv})",
                     "character": cname, "job": jt, "lv": lv, "grade": None})
            # class basic masteries: a job's <BasicMasteries> (no RequireLv) is granted when the
            # character takes that class — shared_job.lua GetRewardMasteriesByJobLevel returns them
            # with no level gate (≠ the level-locked <Masteries> above). Wording stays neutral on
            # *when*: it's at recruitment for the character's join class (e.g. Kylie joins as
            # Engineer) but on switching for a later class (e.g. Kylie→Hacker grants Algorithm).
            bms = job.find("BasicMasteries")
            for m in (bms.findall("property") if bms is not None else []):
                sources[m.get("Name")].append(
                    {"type": "Character", "name": f"{cname} as {jt} (class basic mastery)",
                     "character": cname, "job": jt, "lv": None, "classBasic": True, "grade": None})

    # captured-beast unlocks: capturing a beast and levelling it in its class unlocks the
    # listed masteries. Beast.xml BeastType -> Masteries -> property[Name, RequireLv]
    for c in idspace(os.path.join(xml, "Beast.xml"), "BeastType").findall("class"):
        bname = mon_name_by_id.get(c.get("Monster")) or c.get("Monster")
        ms = c.find("Masteries")
        if ms is None:
            continue
        for m in ms.findall("property"):
            lv = m.get("RequireLv")
            sources[m.get("Name")].append(
                {"type": "Beast", "name": f"{bname} (Lv {lv})",
                 "beast": bname, "lv": lv, "grade": None})

    # built-drone module unlocks: building a drone and levelling it in its class unlocks the
    # listed modules. Machine.xml MachineType -> Masteries -> nested property[Name, RequireLv].
    # Each unit is a Frame×SP combo; a grant is determined either by the FRAME (granted across all
    # that frame's SP variants — RequireLv 1/16) or by the SP structure (granted across all frames
    # sharing that SP — RequireLv 8). Detect which axis is responsible structurally and attribute
    # the source to it, so an SP grant reads "Heat SP" instead of every frame's name (the unit Title
    # is just the frame name, e.g. "Scout Drone", and otherwise drops the SP it came from).
    # The Masteries table's outer <property> groups are the reinforcement stages (Normal/Remodeled/
    # Reinforced/Complete) — the same mastery slot grants a *different* module at each stage, so the
    # stage is part of a grant's identity (e.g. Fire-Fighter Lv1 = Water Resistance at Normal but
    # Auxiliary Support Module at Reinforced). Stage names from MachineReinforcementType/Normal.
    reinf_names = {}
    rnorm = next((c for c in idspace(os.path.join(xml, "Machine.xml"), "MachineReinforcementType")
                  .findall("class") if c.get("name") == "Normal"), None)
    if rnorm is not None:
        for i, p in enumerate(rnorm.findall("property"), 1):
            reinf_names[i] = dic.get(f"MachineReinforcementType/Normal/{i}/Title", p.get("Title"))
    drone_grants = {}                                   # (mastery, lv, stage) -> {frames, sps, frame_title}
    for c in idspace(os.path.join(xml, "Machine.xml"), "MachineType").findall("class"):
        mm = re.match(r"Mon_DroneFrame_([A-Za-z]+)_([A-Za-z]+)$", c.get("name") or "")
        ms = c.find("Masteries")
        if not mm or ms is None:
            continue
        frame, sp = mm.group(1), mm.group(2)
        dname = mon_name_by_id.get(c.get("Monster")) or c.get("Monster")
        for stage, grp in enumerate(ms.findall("property"), 1):     # outer group = reinforcement stage
            for p in grp.findall("property"):
                if p.get("Name") and p.get("RequireLv") is not None:
                    g = drone_grants.setdefault((p.get("Name"), p.get("RequireLv"), stage),
                                                {"frames": set(), "sps": set(), "frame_title": {}})
                    g["frames"].add(frame); g["sps"].add(sp); g["frame_title"][frame] = dname
    for (mid, lv, stage), g in drone_grants.items():
        if len(g["frames"]) > 1 and len(g["sps"]) == 1:     # SP-determined (all frames, one SP)
            label = f"{next(iter(g['sps']))} SP"
        else:                                               # frame-determined
            label = g["frame_title"][sorted(g["frames"])[0]]
        if stage > 1:                                       # tag non-base stages; Normal stays unadorned
            label = f"{label} · {reinf_names.get(stage, stage)}"
        sources[mid].append({"type": "Drone", "name": f"{label} (Lv {lv})",
                             "drone": label, "lv": lv, "stage": stage, "grade": None})

    # achievement unlocks: masteries granted by an in-game feat (GuideTrigger.xml — defeat N
    # enemies, conceal 10×, run a class out of protocols, …). An *additional* source: many of
    # these are also enemy-learnable, but the feat is the intended/primary route.
    for mid, src in guidetrigger_grants(xml, dic).items():
        sources[mid].append(src)

    # system.lua hardcoded feat grants (Lucky 7/8/9 — gain the Luck buff N times); same Achievement
    # channel, condition from ACHIEVEMENT_GRANTS.
    kor = dic.lang != "eng"
    for mid in SYSTEM_GRANT_IDS:
        en, ko, _ = ACHIEVEMENT_GRANTS[mid]
        sources[mid].append({"type": "Achievement", "condition": ko if kor else en, "trigger": "system.lua"})

    # story / dialogue-choice unlocks: masteries a story mission awards, optionally gated by a
    # dialogue choice (stage MasteryAcquired markers). Same grants are highlighted in the
    # Dialogue tab (build_dialog_map). Also an *additional* source alongside any enemy route.
    stage_dir = os.path.join(a.data, "stage")
    # 'opened for research' channel (missionResult_Custom.lua): masteries a mission opens (craftable,
    # no copy) regardless of choice — always the mutually-exclusive siblings of a grant. Flags the
    # matching Story source so the tab can note "still opened…"; also fed to build_dialog_map below.
    mission_opens = parse_mission_opens(
        os.path.join(a.data, "script", "server", "missionResult_Custom.lua"))
    opens_titles = mastery_opens(mission_opens, xml, dic)
    for mid, grants in mastery_grants(xml, stage_dir, dic, mission_opens).items():
        for g in grants:
            src = {"type": "Story", "mission": g["mission"], "choice": g["choice"],
                   "scenario": g.get("scenario")}
            if g["mission"] in opens_titles.get(mid, ()):
                src["opened"] = True
            sources[mid].append(src)

    # office/apartment tutorial grants (RefillMastery / AcquireMastery in Dialog_Office*.xml) —
    # the only mastery-grant channel outside stages; e.g. Yearning in Albus's apartment.
    for mid, src in office_grants(xml, dic).items():
        sources[mid].append(src)

    # available from the start: Technique Opened="true" (pre-researched) — e.g. Learning.
    for mid in technique_initial(xml):
        sources[mid].append({"type": "Initial"})

    # player company policies (Company-category, the adoptable ones — not the NPC-static Organization
    # traits in the same tab): 5 available from the start (SetCompanyMastery Opened="true"), 3 unlocked
    # by a story mission (missionResult_Custom CompanyMasteries/<id>/Opened). Fills their orphan gap.
    for mid in company_initial(xml):
        sources[mid].append({"type": "Initial"})
    for mid, mission in company_opens(
            os.path.join(a.data, "script", "server", "missionResult_Custom.lua"), xml, dic).items():
        sources[mid].append({"type": "Story", "mission": mission})

    # research-unlock chain: a mastery is unlocked by *crafting* (researching) the mastery whose
    # UnLockTechnique points to it (e.g. King's Wealth ← Treasure Island ← AliBaba ← Treasure
    # Hunter). An additional source — shown even when the mastery is also enemy-learnable, since
    # researching the prereq is a genuine alternate way to unlock it.
    tech_unlock, tech_developing = technique_data(xml)
    for mid, prereqs in tech_unlock.items():
        if mid not in tech_developing:
            sources[mid].append({"type": "Research", "via": prereqs})

    # index every mastery <class> so descriptions can be resolved from their
    # FormatKeyword recipes (and set bonuses, which are same-named masteries).
    mclass = {c.get("name"): c
              for c in idspace(os.path.join(xml, "Mastery.xml"), "Mastery").findall("class")}
    # ability <class>es, for resolving the descriptions of abilities masteries grant/modify
    ability_cls = {c.get("name"): c
                   for c in idspace(os.path.join(xml, "Ability.xml"), "Ability").findall("class")}
    # buff <class>es + Status formats, for generating a buff's effect text (stat-core auto-tooltip)
    buff_cls = {c.get("name"): c
                for c in idspace(os.path.join(xml, "Buff.xml"), "Buff").findall("class")}
    group_cls = idspace(os.path.join(xml, "Buff.xml"), "BuffGroup").findall("class")  # buff families
    status_fmt = {c.get("name"): c.get("Format")
                  for c in idspace(os.path.join(xml, "Status.xml"), "Status").findall("class")}
    # PerformanceType -> its ordered granted PerformanceEffect ids, for `$MasteryPerformanceEffectList$`
    # (the Clown *Amazing Trick* → "Amazing Acrobatic Move, …"). Keyed off the mastery's PerformanceType.
    resolve_desc.PERFORMANCE_EFFECTS = {
        c.get("name"): [p.get("Type") for p in eff.findall("property")]
        for c in idspace(os.path.join(xml, "Performance.xml"), "Performance").findall("class")
        if (eff := c.find("Effect")) is not None}

    def describe(name):
        c = mclass.get(name)
        if c is None:
            return mastery_desc(dic, name)
        # Description mirrors the game tooltip's line order (shared_tooltip.lua GetMasterySystem
        # MessageText): authored Desc_Base, then debuff-immunity (ImmuneDebuff_BuffGroup) and terrain
        # field-effect immunity (NeutralizeFieldEffect) as consecutive lines, then the flat
        # Base_<Stat> deltas ($StatusMessage$) under a gold "Extra Effect"
        # (WordCollection/AdditionalEffect) header when any content precedes them — else the stats are
        # the description itself. These can co-occur (e.g. the elemental-skin monster masteries:
        # reinforcement text + element immunity + stat deltas; Flight: authored + field immunity).
        authored = resolve_description(dic, c)
        primary = "\n".join(p for p in (authored, immune_debuff_summary(dic, c),
                                        neutralize_field_summary(dic, c)) if p)
        stats = stat_summary(dic, c, status_fmt)
        if stats:
            if primary:
                header = dic.get("WordCollection/AdditionalEffect/Text", "Extra Effect")
                primary = f"{primary}\n\n{header}\n{stats}"
            else:
                primary = stats
        if primary:
            return primary
        # no description of its own: if the effect lives on a linked buff, generate its stat-core
        # tooltip. (A *granted ability* isn't surfaced here — the "Grants ability" cross-link in the
        # row detail already names it and hovers to its effect, so a description line would dupe it.)
        buff = c.get("Buff")
        if buff and buff in buff_cls:
            bd = describe_buff(dic, buff_cls[buff], status_fmt, with_duration=False)
            if bd.strip():
                return bd
        return primary

    group_owner = classify_individual(xml, dic, mclass)
    beast_avail = beast_availability(xml, dic, type_title, mclass)   # how a player beast gets each Beast mastery

    # --- mastery sets --------------------------------------------------------
    sets = []
    mastery_to_sets = collections.defaultdict(list)
    for c in idspace(os.path.join(xml, "MasterySet.xml"), "MasterySet").findall("class"):
        if exclude_dev and is_developing(c):     # unfinished set (EnableDevelopingMasterySet=false in-game)
            dev_skips["mastery sets"] += 1
            continue
        sid = c.get("name")
        comps = [c.get(f"Mastery{i}") for i in range(1, 5)]
        comps = [m for m in comps if m and m != "None"]
        rec_bonus = describe(sid)                        # refs are inline sentinel markup in rec_bonus
        rec = {
            "id": sid,
            "name": dic.get(f"Mastery/{sid}/Base_Title", sid),
            "bonus_desc": rec_bonus,
            "type": c.get("Type"),
            "type_name": type_title.get(c.get("Type"), c.get("Type")),
            "dlc": dlc_name(c.get("DLC")) or "None",
            "developing": c.get("Developing") == "true",
            "priority": c.get("Priority"),
            "components": [{"id": m, "name": dic.get(f"Mastery/{m}/Base_Title", m)} for m in comps],
        }
        sets.append(rec)
        for m in comps:
            mastery_to_sets[m].append(sid)

    # orphaned class-trait masteries: the BasicMastery of an excluded job, when EVERY job that
    # uses it as a basic mastery is excluded and no enemy carries it — so it's truly unreachable
    # (Passionate Performance / Beautiful Voice / Piety / Holy War). Shared traits whose owners
    # include a kept class (Accounting via Merchant, Firearm Training via Gunman, Martial Art via
    # Martial Artist) stay. Empty when --include-developing (excluded_jobs is empty).
    basic_owners = collections.defaultdict(set)
    for jid, c in job_cls.items():
        bm = c.get("BasicMastery")
        if bm and bm != "None":
            basic_owners[bm].add(jid)
    drop_traits = {bm for bm, owners in basic_owners.items()
                   if owners <= excluded_jobs
                   and not any(s["type"] == "Enemy" for s in sources.get(bm, []))}

    # --- masteries -----------------------------------------------------------
    masteries = []
    for c in idspace(os.path.join(xml, "Mastery.xml"), "Mastery").findall("class"):
        n = c.get("name")
        if n in ("Dummy", "_DUMMY_"):
            continue
        if n in drop_traits:                # unique trait of a cut/engine-disabled class
            dev_skips["orphaned class traits"] += 1
            continue
        title = dic.get(f"Mastery/{n}/Base_Title")
        enemy_owner = group_owner.get(n, ("normal", None))[1]   # beasts that carry it (enemy/learned)
        avail = beast_avail.get(n)                              # for Beast masteries: player availability
        mdesc = describe(n)                                     # refs are inline sentinel markup in mdesc
        rec = {
            "id": n,
            "name": title or n,
            "has_localized_name": title is not None,
            "type": c.get("Type"),
            "type_name": type_display(c.get("Type")),
            "grade": c.get("Grade") if c.get("Grade") not in (None, "None") else None,
            "cost": int(cost) if (cost := c.get("Cost")) and cost.isdigit() else None,
            "category": c.get("Category"),
            "category_name": cat_title.get(c.get("Category"), c.get("Category")),
            "category_en": cat_title_en.get(c.get("Category"), c.get("Category")),
            "group": group_owner.get(n, ("normal", None))[0],
            # Beast masteries show *player availability* in the owner column; the enemy-carrier list
            # is demoted to `enemyCarriers` (shown only in the row detail).
            "owner": avail[1] if avail else enemy_owner,
            "availScope": avail[0] if avail else None,
            "availFamilies": avail[2] if avail else None,
            "enemyCarriers": enemy_owner if avail else None,
            "description": mdesc,
            "flavor": dic.get(f"Mastery/{n}/FlavorText"),
            "in_sets": mastery_to_sets.get(n, []),
            "sources": sources.get(n, []),
            # the ability this mastery grants/modifies (display name), for the Abilities cross-link
            "grantsAbility": dic.get(f"Ability/{c.get('Ability')}/Title")
            if c.get("Ability") not in (None, "None") else None,
            # buff/group/mastery/ability refs are inline sentinel markup in `description` — the web
            # renders positioned chips from those.
        }
        if exclude_dev and n in tech_developing:   # cut/unfinished: its only unlock is a Developing
            rec["developing"] = True               # technique (no real in-game source) — drop from web
        masteries.append(rec)

    by_id = {m["id"]: m for m in masteries}

    # enemy -> missions (case type, recommended level, min-difficulty), for the web view
    # (stage_dir defined above for the story-grant sources)
    enemy_missions, mission_info, placed, enemy_dialog = build_enemy_missions(xml, stage_dir, dic)

    # dialogue decision/consequence maps per stage
    # id -> display name: a Monster class name, else an ObjectInfo title (so a unit referenced by a
    # bare ObjectKey that isn't placed — e.g. the player char "Albus" in a buff action — still
    # localizes, instead of falling back to the raw English key), else the id.
    dialogues = build_dialog_map(xml, stage_dir, dic,
                                 lambda i: mon_name_by_id.get(i) or dic.get(f"ObjectInfo/{i}/Title") or i,
                                 quest_names=quest_missions(xml, dic), mission_opens=mission_opens)

    # board-builder data: jobs (grade 1-2) + playable characters + limit modifiers
    board_cat = {m["id"]: m["category_en"] for m in masteries}   # English engine cat (matches board columns)
    jobs, pcs, esp_slots, board_mods, slot_unlock, beasts, beast_families, beast_stages, beast_evo = \
        build_builder_data(xml, dic, type_title, board_cat, excluded_jobs)
    machine = build_machine_data(xml, dic, type_title, cat_title_en)   # drone frames / SP / OS / units / reinforcement / modules
    item_sources = ability_item_sources(xml)                          # ability -> Slot source (Potion/Grenade/Spray/Device)
    player_set, ability_owners, auto_source = build_player_abilities(xml, dic, beasts, machine.get("units", []), item_sources, ability_cls)  # player-usable filter + owners
    abilities = build_abilities(dic, ability_cls, mclass, item_sources, player_set, ability_owners, auto_source)  # Abilities reference tab
    buffs = build_buffs(dic, buff_cls, status_fmt)                     # buff effect lookup (hover tooltips)
    buff_groups = build_buff_groups(dic, buff_cls, group_cls)         # buff-group → members (group cards)
    dialogue_buffs = build_dialogue_buffs(dialogues, dic, buff_cls, status_fmt)  # id-keyed, for "gains X" hovers

    # ---- beast species masteries: form-specific heading + the forms that offer it -----------
    # A species evolution-mastery offered by a single form is labelled with that form's name; one
    # whose offering-form set exactly matches a hand-named group (beast_groups.json) gets the group
    # name. Used as a name prefix in the Individual tab; the row detail lists every offering form.
    bgroups = {}
    try:
        raw = json.load(open(os.path.join(os.path.dirname(__file__), "beast_groups.json"), encoding="utf-8"))
        bgroups = {frozenset(v): k for k, v in raw.items() if not k.startswith("_")}
    except (OSError, ValueError):
        pass
    forms_of = collections.defaultdict(list)
    for b in beasts:
        for mid in b.get("fixedEvo", []):
            forms_of[mid].append(b)
    for m in masteries:
        if m.get("availScope") != "species":
            continue
        fs = sorted(forms_of.get(m["id"], []), key=lambda b: (b["stage"], b["name"]))
        m["offeredBy"] = [{"name": b["name"], "stage": b["stage"]} for b in fs]
        m["formGroup"] = fs[0]["name"] if len(fs) == 1 else bgroups.get(frozenset(b["id"] for b in fs))

    # ---- drone reinforcement masteries: OS-pool prefix (which OS offers the AI-upgrade pick) ----
    # The Drone tab groups by Type; the reinforcement (AI-upgrade) picks additionally carry a prefix
    # naming the OS pool(s) that offer them — "Any OS" when in all three, else the OS display names.
    os_pools = collections.defaultdict(set)            # mastery id -> set of OS pool names
    for c in idspace(os.path.join(xml, "Machine.xml"), "MachineAIUpgrade").findall("class"):
        up = c.find("AIUpgrade")
        for p in (up.findall("property") if up is not None else []):
            os_pools[p.get("Type")].add(c.get("name"))
    OS_ORDER = ["Windows", "Linux", "MacOS"]            # = Multi Processing / Open Type / Independent
    os_name = {n: (dic.get(f"Mastery/{n}/Base_Title") or n).removesuffix(" OS") for n in OS_ORDER}
    for m in masteries:
        oss = os_pools.get(m["id"])
        if not oss:
            continue
        m["formGroup"] = "Any OS" if len(oss) >= len(OS_ORDER) else " / ".join(os_name[o] for o in OS_ORDER if o in oss)

    # ------------------------------------------------------------------- write
    write_outputs(a.out, masteries, sets, by_id, dic)
    # English is the default page (data.js); other languages get a suffixed file (data.<lang>.js)
    web_name = "data.js" if a.lang == "eng" else f"data.{a.lang}.js"
    jt_teams = joint_training_teams(xml, dic, exclude_dev)   # JT clone -> live faction-team titles
    quests = build_quests(xml, dic, mon_name_by_id)          # Shooter Street NPC quest chains
    write_web_data(os.path.join(os.path.dirname(__file__), "web", web_name),
                   masteries, sets, enemy_missions, mission_info, placed, enemy_dialog,
                   dialogues, jobs, pcs, esp_slots, board_mods, slot_unlock,
                   beasts, beast_families, beast_stages, beast_evo, machine, abilities, buffs, buff_groups,
                   jt_teams=jt_teams, quests=quests, dialogue_buffs=dialogue_buffs,
                   high_risk_label=dic.get("Help/GameDifficultyAdditionalSetting_HighRiskReturn/Base_Title", "High Risk") or "High Risk",
                   generated=source_date(xml))
    if a.lang == "eng":   # share-code lookup tables are language-independent — emit once
        emit_codemap(xml, os.path.join(os.path.dirname(__file__), "web", "codemap.js"))
    print(f"dialogue stages: {len(dialogues)} "
          f"({sum(len(d['decisions']) for d in dialogues)} decision points)")
    print(f"masteries: {len(masteries)} ({sum(1 for m in masteries if m['sources'])} with sources)")
    print(f"mastery sets: {len(sets)}")
    print(f"abilities: {len(abilities)} "
          f"({sum(1 for x in abilities if x['grantedBy'] or x['modifiedBy'])} linked to masteries)")
    print(f"buff effect tooltips: {len(buffs)} | buff groups: {len(buff_groups)}")
    if exclude_dev:
        dropped = ", ".join(f"{v} {k}" for k, v in sorted(dev_skips.items()))
        print(f"excluded unavailable content (Developing + unit-less hidden classes): "
              f"{dropped or 'none'} (pass --include-developing to keep)")
    else:
        print("kept unavailable content (--include-developing)")
    print(f"distinct masteries dropped by enemies: "
          f"{sum(1 for m in masteries if any(s['type']=='Enemy' for s in m['sources']))}")
    print(f"distinct masteries from character job-level unlocks: "
          f"{sum(1 for m in masteries if any(s['type']=='Character' for s in m['sources']))}")
    print(f"distinct masteries from captured-beast levelling: "
          f"{sum(1 for m in masteries if any(s['type']=='Beast' for s in m['sources']))}")
    print(f"distinct modules from built-drone levelling: "
          f"{sum(1 for m in masteries if any(s['type']=='Drone' for s in m['sources']))}")
    print(f"output written to {a.out}")

    if a.report_unresolved:
        rows = resolve_desc.unresolved_report()
        print(f"\n=== unresolved lookups ({a.lang}): {len(rows)} distinct "
              f"idspace/key pairs fell back to a raw English id / literal token ===")
        for kind, space, key, count in rows:
            print(f"{count:6}  [{kind}] {space}/{key}")


# --- share-code lookup tables (web/codemap.js) --------------------------------
# A constant PC header — the 10-char magic prefix of a PC board share code (per-character
# fields are overwritten on encode). See decode_board.py for the share-code format.
_PC_TEMPLATE = "KSAACAJQEE"


def emit_codemap(xml, path):
    """Emit web/codemap.js — the code<->id/name lookup tables the Board Builder uses to
    encode/decode share codes, read from MasteryCode.xml. Language-independent (pure code
    tables, no dictionary strings), so it's written once, on the English build."""
    sp = idspace(os.path.join(xml, "MasteryCode.xml"), "MasteryCode")
    C = {}          # class name -> {property name -> attr dict}, from the <Codes> wrapper
    for cl in sp.findall("class"):
        codes = cl.find("Codes")
        C[cl.get("name")] = {p.get("name"): p.attrib
                             for p in (codes.findall("property") if codes is not None else [])}
    code = lambda cls: {n: int(a["Code"]) for n, a in C[cls].items()}
    mtype, pc, job = code("MasteryType"), code("Pc"), code("Job")
    beast, machine = code("Beast"), code("Machine")   # beast form / drone frame id -> 8-bit charId
    codemap = {f"{mtype[a['Type']]}:{int(a['Code'])}": n for n, a in C["Mastery"].items()}   # "t:c" -> id
    mast_inv = {n: f"{mtype[a['Type']]}:{int(a['Code'])}" for n, a in C["Mastery"].items()}  # id -> "t:c"
    j = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    inv = lambda d: {str(v): k for k, v in d.items()}
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            f"window.TS_CODEMAP={j(codemap)};\n"          # "t:c" -> id  (decode)
            f"window.TS_MASTINV={j(mast_inv)};\n"         # id -> "t:c"  (encode)
            f"window.TS_PC={j(inv(pc))};\n"               # charId -> pc name
            f"window.TS_PCINV={j(pc)};\n"                 # pc name -> charId
            f"window.TS_JOB={j(inv(job))};\n"             # jobId  -> job name
            f"window.TS_JOBINV={j(job)};\n"               # job name -> jobId
            f"window.TS_BEAST={j(inv(beast))};\n"         # charId -> beast form id
            f"window.TS_BEASTINV={j(beast)};\n"           # beast form id -> charId
            f"window.TS_MACHINE={j(inv(machine))};\n"     # charId -> drone frame id
            f"window.TS_MACHINEINV={j(machine)};\n"       # drone frame id -> charId
            f"window.TS_MTYPES={j(inv(mtype))};\n"
            f'window.TS_PCTEMPLATE="{_PC_TEMPLATE}";\n')
    print(f"wrote {path} ({len(codemap)} code entries)")


def source_date(xml):
    """Date the game data was extracted, for data.js's `generated` stamp. Anchored on a core
    unpacked source file's modtime (set when extract_files.py unpacks the game) rather than the
    build date, so rebuilding an unchanged snapshot produces no spurious diff — it only advances
    when the game is re-unpacked. Mastery.xml is a central input and won't be hand-edited; max-of-all
    modtimes is deliberately avoided (a single touched file, e.g. CheatCommand.xml, would poison it).
    Falls back to today if the anchor is missing."""
    try:
        return datetime.date.fromtimestamp(os.path.getmtime(os.path.join(xml, "Mastery.xml"))).isoformat()
    except OSError:
        return datetime.date.today().isoformat()


def write_web_data(path, masteries, sets, enemy_missions=None, mission_info=None,
                   placed=None, enemy_dialog=None, dialogues=None, builder_jobs=None, pcs=None,
                   esp_slots=None, board_mods=None, slot_unlock=None, builder_beasts=None, beast_families=None,
                   beast_stages=None, beast_evo=None, machine=None, abilities=None, buffs=None,
                   buff_groups=None, jt_teams=None, quests=None, high_risk_label="High Risk",
                   generated=None, dialogue_buffs=None):
    """Emit web/data.js (window.TS_DATA) — denormalized, named masteries only."""
    enemy_missions = enemy_missions or {}
    mission_info = mission_info or {}
    enemy_dialog = enemy_dialog or {}
    jt_teams = jt_teams or {}                      # JT clone id -> live faction-team titles

    # equipment-set bonuses (category EquipmentSet) get their own tab: one entry per set,
    # with the bonus at each piece-count threshold. Names use one of these patterns:
    # English "<Set> - Set <N>" / "<Set> - <N>-piece Set Bonus"; Korean "<Set> - <N> 세트".
    # The threshold count <N> is embedded in the localized name, so each language needs its
    # own suffix pattern — otherwise grouping fails and every threshold becomes its own card.
    def _parse_eqset(name):
        for rx in (r"^(.*) - Set (\d+)$", r"^(.*) - (\d+)-piece Set Bonus$",
                   r"^(.*) - (\d+) ?세트$"):
            mt = re.match(rx, name)
            if mt:
                return mt.group(1), int(mt.group(2))
        return name, None
    eq = collections.OrderedDict()
    for m in masteries:
        if m["category"] != "EquipmentSet" or not m["has_localized_name"]:
            continue
        base, n = _parse_eqset(m["name"])
        e = eq.setdefault(base, {"name": base, "type": m["type_name"], "thresholds": []})
        e["thresholds"].append({"n": n if n is not None else "?", "desc": m["description"] or ""})
    equipment_sets = []
    for e in eq.values():
        e["thresholds"].sort(key=lambda t: t["n"] if isinstance(t["n"], int) else 99)
        equipment_sets.append(e)
    equipment_sets.sort(key=lambda e: e["name"].lower())

    name_by_id = {m["id"]: (m["name"] or m["id"]) for m in masteries}   # for Research prereq names
    # diagnostics for the "drop enemies with no resolvable encounter" rule (see below).
    # keyed by monster id (collision-free: a *display name* can be dropped for one mastery yet
    # legitimately appear for another via a same-named encounterable variant, but an id's encounter
    # status is global) -> display name, for a stable diffable artifact.
    dropped_carriers = {}            # {monster_id: display_name} with no mission, no JT
    orphan_board = []                # board (normal/module) masteries left with NO source at all
    web_masteries = []
    for m in masteries:
        if not m["has_localized_name"]:
            continue
        if m["category"] == "EquipmentSet":
            continue                      # shown on the Equipment Sets tab instead
        if m["category"] == "System":
            continue                      # internal/object metadata — not a player mastery
        if m.get("developing"):
            continue                      # cut/unfinished (Developing technique, no real source)
        # collapse enemy sources to unique names with a level range + missions
        enemy_lv = collections.defaultdict(list)
        enemy_ids = collections.defaultdict(set)
        for s in m["sources"]:
            if s["type"] == "Enemy":
                enemy_ids[s["name"]].add(s["id"])
                try:
                    enemy_lv[s["name"]].append(int(s["lv"]))
                except (TypeError, ValueError):
                    pass
        enemies = []
        for n in sorted(enemy_ids):
            v = enemy_lv.get(n, [])
            # union missions (by mission id) across all monster ids sharing this name,
            # keeping the most-available difficulty tier per mission
            mis = {}
            mis_dialog = {}
            training = False
            jt_titles = set()           # localized Joint Training team(s) this enemy belongs to
            for eid in enemy_ids[n]:
                if eid in jt_teams:     # a clone in a *live* Joint Training team (dev-only Beast
                    training = True     # packs are absent from jt_teams, so they don't count)
                    jt_titles.update(jt_teams[eid])
                for mid, tier in missions_for(enemy_missions, placed, eid).items():
                    cur = mis.get(mid)
                    mis[mid] = tier if cur is None or _tier_rank(tier) < _tier_rank(cur) else cur
                    labels = dialog_labels_for(enemy_dialog, eid, mid)
                    if labels:
                        mis_dialog.setdefault(mid, set()).update(labels)
            missions = []
            for mid, tier in mis.items():
                info = mission_info.get(mid)
                if not info:
                    continue
                rec = {"name": info["title"], "level": info["level"], "case": info["case"]}
                if tier == "Dialog":
                    label = ", ".join(sorted(mis_dialog.get(mid, [])))
                    rec["dialog"] = label or True
                elif tier != "All":
                    rec["diff"] = tier
                missions.append(rec)
            # dedupe identical (title, level, case, diff/dialog) and sort by level then name
            seen, uniq = set(), []
            for r in sorted(missions, key=lambda x: (x["level"], x["name"])):
                k = (r["name"], r["level"], r["case"], r.get("diff"), r.get("dialog"))
                if k not in seen:
                    seen.add(k)
                    uniq.append(r)
            if training:                  # fightable in the Joint Training (Joint Drill) mode
                rec = {"name": "Joint Training", "training": True}
                if jt_titles:             # tag with the team(s) whose roster this clone is in
                    rec["teams"] = sorted(jt_titles)
                uniq.append(rec)
            # Blanket rule: an enemy that resolves to NO encounter (no mission, no Joint Training)
            # after every appearance mechanism (static/neutral placement, DrakyEgg hatching, boss
            # summons, dialog flips) is not a real source — you can never fight it — so drop it
            # rather than list a phantom carrier. The build-time diagnostic below reports the dropped
            # set and flags any board mastery this orphans, so a new game-update spawn mechanism
            # surfaces as a warning instead of silently vanishing. (See DATAMINING.md "Enemies
            # with no mission appearance".)
            if not uniq:
                for eid in enemy_ids[n]:
                    dropped_carriers[eid] = n
                continue
            enemies.append({"name": n, "lv": [min(v), max(v)] if v else None, "missions": uniq})
        chars = sorted(({"character": s["character"], "job": s["job"], "lv": int(s["lv"] or 0),
                         **({"classBasic": True} if s.get("classBasic") else {})}
                        for s in m["sources"] if s["type"] == "Character"),
                       key=lambda c: (not c.get("classBasic"), c["lv"], c["character"], c["job"]))
        # de-dupe character rows (a class basic mastery and a level unlock are distinct rows)
        seen, uchars = set(), []
        for c in chars:
            k = (c["character"], c["job"], c["lv"], c.get("classBasic", False))
            if k not in seen:
                seen.add(k)
                uchars.append(c)
        jobs = sorted({s["name"] for s in m["sources"] if s["type"] == "Job"})
        # captured beasts: {beast: lowest level that unlocks it}
        bmap = {}
        for s in m["sources"]:
            if s["type"] == "Beast":
                lv = int(s["lv"] or 0)
                if s["beast"] not in bmap or lv < bmap[s["beast"]]:
                    bmap[s["beast"]] = lv
        beasts = [{"beast": b, "lv": lv} for b, lv in
                  sorted(bmap.items(), key=lambda x: (x[1], x[0]))]
        # built drones: {drone: lowest level that unlocks the module}
        dmap = {}
        for s in m["sources"]:
            if s["type"] == "Drone":
                lv = int(s["lv"] or 0)
                if s["drone"] not in dmap or lv < dmap[s["drone"]]:
                    dmap[s["drone"]] = lv
        drones = [{"drone": dr, "lv": lv} for dr, lv in
                  sorted(dmap.items(), key=lambda x: (x[1], x[0]))]
        # achievement (feat) unlocks — one per mastery: {condition, achievement?}
        achievements = [{k: v for k, v in s.items() if k in ("condition", "achievement")}
                        for s in m["sources"] if s["type"] == "Achievement"]
        # available from the start (default-open Technique)
        initial = any(s["type"] == "Initial" for s in m["sources"])
        # research-unlock: the prerequisite mastery(ies) whose crafting unlocks this one (names)
        research = sorted({name_by_id.get(v, v) for s in m["sources"]
                           if s["type"] == "Research" for v in s.get("via", [])})
        # story / dialogue-choice unlocks: {mission, choice?} — dedupe identical entries
        story, seen_story = [], set()
        for s in m["sources"]:
            if s["type"] != "Story":
                continue
            key = (s["mission"], s.get("choice"), s.get("tutorial", False))
            if key in seen_story:
                continue
            seen_story.add(key)
            rec = {"mission": s["mission"]}
            if s.get("choice"):
                rec["choice"] = s["choice"]
            if s.get("tutorial"):
                rec["tutorial"] = True
            if s.get("scenario"):              # scenario missions: "Ch4 Scent of the Past"
                rec["scenario"] = s["scenario"]
            if s.get("opened"):                # also opened for research whichever choice you make
                rec["opened"] = True
            story.append(rec)
        story.sort(key=lambda r: (not r.get("tutorial"), r["mission"], r.get("choice") or ""))
        set_names = [next((s["name"] for s in sets if s["id"] == sid), sid) for sid in m["in_sets"]]
        wm = {
            "id": m["id"], "name": m["name"], "type": m["type_name"], "typeRaw": m["type"],
            "category": m["category_name"], "categoryRaw": m["category_en"], "cost": m["cost"],
            "group": m["group"], "owner": m["owner"],
            "availScope": m.get("availScope"), "availFamilies": m.get("availFamilies"),
            "formGroup": m.get("formGroup"), "offeredBy": m.get("offeredBy"),
            "enemyCarriers": m.get("enemyCarriers"),
            "description": m["description"], "flavor": m["flavor"],
            "sets": set_names, "enemies": enemies, "characters": uchars, "jobs": jobs,
            "beasts": beasts, "drones": drones,
        }
        if achievements:                           # feat-based unlocks (GuideTrigger.xml)
            wm["achievements"] = achievements
        if story:                                  # story-mission / dialogue-choice unlocks
            wm["story"] = story
        if initial:                                # available from the start (Technique Opened=true)
            wm["initial"] = True
        if research:                               # unlocked only by researching these prereq masteries
            wm["research"] = research
        if m.get("grantsAbility"):                 # omit when null (only ~53 of ~1900 carry one)
            wm["grantsAbility"] = m["grantsAbility"]
        # orphan tripwire: a board-placeable mastery (normal/module) with no remaining source of any
        # kind. Expected to be empty; a non-empty list after a game update means a new acquisition
        # mechanism (likely a spawn the parser misses) needs investigating — not a silent drop.
        if m["group"] in ("normal", "module") and not (
                enemies or uchars or jobs or beasts or drones
                or achievements or initial or research or story):
            orphan_board.append((m["id"], m["name"]))
        web_masteries.append(wm)

    # Elite2/Epic2/Legend2 are the enhanced rank effects applied by the "High-Risk, High-Return"
    # custom-difficulty setting (same in-game name as the base rank effect) — suffix them with that
    # setting's own localized name so they're distinguishable in every language (the game titles it
    # under Help/GameDifficultyAdditionalSetting_HighRiskReturn — see the difficulty "Second Wind"
    # additional options), instead of a hardcoded English half-name.
    rank_upgrade = {"Elite2", "Epic2", "Legend2"}
    for m in web_masteries:
        if m["id"] in rank_upgrade:
            m["name"] = f"{m['name']} ({high_risk_label})"

    # disambiguate any other display names that still collide within a group by appending a
    # numeric counter (language-neutral, unlike the internal id which reads oddly mixed into
    # a localized name). Order follows XML order, so it's stable across runs and languages.
    name_counts = collections.Counter((m["group"], m["name"]) for m in web_masteries)
    name_seen = collections.Counter()
    for m in web_masteries:
        key = (m["group"], m["name"])
        if name_counts[key] > 1:
            name_seen[key] += 1
            m["name"] = f"{m['name']} ({name_seen[key]})"

    web_sets = []
    for s in sets:
        ws = {"id": s["id"], "name": s["name"], "type": s["type_name"],
              "dlc": "" if s["dlc"] == "None" else s["dlc"],
              "components": s["components"], "bonus": s["bonus_desc"]}
        web_sets.append(ws)

    payload = {"generated": generated or datetime.date.today().isoformat(),
               "masteries": web_masteries, "sets": web_sets,
               "equipmentSets": equipment_sets, "dialogues": dialogues or [],
               "jobs": builder_jobs or [], "pcs": pcs or [],
               "espSlots": esp_slots or {}, "boardMods": board_mods or {},
               "slotUnlock": slot_unlock or {},
               "beasts": builder_beasts or [], "beastFamilies": beast_families or [],
               "beastStages": beast_stages or {}, "beastEvo": beast_evo or [],
               "machine": machine or {}, "abilities": abilities or [], "buffs": buffs or {},
               "buffsById": dialogue_buffs or {},
               "buffGroups": buff_groups or {}, "quests": quests or [],
               "counts": {"masteries": len(web_masteries), "sets": len(web_sets)}}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.TS_DATA = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    # --- no-encounter drop diagnostic (English build only, to avoid double-printing) ---
    if os.path.basename(path) == "data.js":
        print(f"dropped {len(dropped_carriers)} enemy-carrier ids with no resolvable encounter "
              f"(not listed as sources)")
        # A diffable artifact: a game update that changes the dropped set shows up in git. One
        # "<name>\t<id>" per line, sorted by name then id for a stable ordering.
        with open(os.path.join(os.path.dirname(path), "dropped_no_encounter.txt"),
                  "w", encoding="utf-8") as f:
            header = (
                "# Enemy mastery-carrier ids dropped for having no resolvable encounter.\n"
                "# These enemies were not listed as a source for any mastery, so they are\n"
                "# omitted from the web data. Regenerated by extract_masteries.py; committed\n"
                "# as a diffable artifact so a game update that changes the set shows up in git.\n"
                "# One \"<name>\\t<id>\" per line, sorted by name then id.\n\n"
            )
            lines = sorted(f"{nm}\t{eid}" for eid, nm in dropped_carriers.items())
            f.write(header + "\n".join(lines) + ("\n" if lines else ""))
        if orphan_board:
            print(f"  ⚠ {len(orphan_board)} board (normal/module) masteries now have NO source — "
                  f"investigate (likely a new spawn mechanism the parser misses):")
            for mid, nm in sorted(orphan_board, key=lambda x: x[1]):
                print(f"      {nm} ({mid})")


def write_outputs(out, masteries, sets, by_id, dic):
    # JSON keeps every mastery (incl. internal); human-readable views skip the
    # nameless internal/dummy entries (no localized title).
    named = [m for m in masteries if m["has_localized_name"]]

    # These outputs are plain-text/human — flatten the inline ref sentinels to bare labels (the web
    # data keeps the markup). `_plain(items, field)` returns copies with `field` stripped.
    def _plain(items, field):
        return [{**it, field: strip_refs(it[field])} if it.get(field) else it for it in items]

    # JSON
    json.dump(_plain(masteries, "description"), open(os.path.join(out, "masteries.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump(_plain(sets, "bonus_desc"), open(os.path.join(out, "mastery_sets.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # CSV - masteries (one row per mastery; sources flattened)
    with open(os.path.join(out, "masteries.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "type", "category", "group", "owner", "in_sets",
                    "enemy_sources", "character_unlocks", "description"])
        for m in sorted(named, key=lambda x: x["name"].lower()):
            enemies = "; ".join(sorted({s["name"] for s in m["sources"] if s["type"] == "Enemy"}))
            chars = "; ".join(sorted({s["name"] for s in m["sources"] if s["type"] == "Character"}))
            w.writerow([m["id"], m["name"], m["type_name"], m["category_name"] or "",
                        m["group"], m["owner"] or "",
                        "; ".join(m["in_sets"]), enemies, chars,
                        strip_refs(m["description"] or "").replace("\n", " ")])

    # CSV - sets
    with open(os.path.join(out, "mastery_sets.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "type", "dlc", "mastery1", "mastery2", "mastery3", "mastery4", "bonus"])
        for s in sorted(sets, key=lambda x: x["name"].lower()):
            comps = [c["name"] for c in s["components"]] + [""] * 4
            w.writerow([s["id"], s["name"], s["type_name"], s["dlc"],
                        comps[0], comps[1], comps[2], comps[3],
                        strip_refs(s["bonus_desc"] or "").replace("\n", " ")])

    # Markdown - masteries with sources (like the community drop guide)
    with open(os.path.join(out, "masteries.md"), "w", encoding="utf-8") as f:
        f.write("# TROUBLESHOOTER — Masteries and where to get them\n\n")
        f.write(f"_{len(masteries)} masteries extracted directly from the game data._\n\n")
        for m in sorted(named, key=lambda x: x["name"].lower()):
            f.write(f"## {m['name']}")
            meta = [m["type_name"], m["category_name"]] + ([m["owner"]] if m["owner"] else [])
            f.write(f"  _({' · '.join(t for t in meta if t)})_\n\n")
            if m["description"]:
                f.write(strip_refs(m["description"]).strip() + "\n\n")
            if m["in_sets"]:
                names = [next((s["name"] for s in sets if s["id"] == sid), sid) for sid in m["in_sets"]]
                f.write("**Part of sets:** " + ", ".join(names) + "\n\n")
            enemies = sorted({(s["name"], s["lv"]) for s in m["sources"] if s["type"] == "Enemy"})
            jobs = [s["name"] for s in m["sources"] if s["type"] == "Job"]
            chars = sorted({(s["character"], s["job"], int(s["lv"] or 0), s["name"])
                            for s in m["sources"] if s["type"] == "Character"},
                           key=lambda x: (x[2], x[0], x[1]))
            if chars:
                f.write("**Unlocked by levelling a character in a job:**\n\n")
                for _, _, _, label in chars:
                    f.write(f"- {label}\n")
                f.write("\n")
            beasts = sorted({(s["beast"], int(s["lv"] or 0)) for s in m["sources"]
                             if s["type"] == "Beast"}, key=lambda x: (x[1], x[0]))
            if beasts:
                f.write("**Unlocked by capturing & levelling a beast:**\n\n")
                for b, lv in beasts:
                    f.write(f"- {b} (Lv {lv})\n")
                f.write("\n")
            if enemies or jobs:
                f.write("**Learnable from enemies / jobs:**\n\n")
                for nm, lv in enemies:
                    f.write(f"- {nm}" + (f" (Lv {lv})" if lv else "") + "\n")
                for j in jobs:
                    f.write(f"- {j}\n")
                f.write("\n")
            if not (enemies or jobs or chars or beasts):
                f.write("_No enemy/job/character/beast source (learned via story, quest or class change)._\n\n")

    # Markdown - mastery sets (like the community set guide)
    with open(os.path.join(out, "mastery_sets.md"), "w", encoding="utf-8") as f:
        f.write("# TROUBLESHOOTER — Mastery Sets\n\n")
        f.write(f"_{len(sets)} sets extracted directly from the game data. "
                "Each set bonus activates when all listed masteries are equipped together._\n\n")
        f.write("| Set | Type | Mastery 1 | Mastery 2 | Mastery 3 | Mastery 4 | DLC |\n")
        f.write("|-----|------|-----------|-----------|-----------|-----------|-----|\n")
        for s in sorted(sets, key=lambda x: (x["type_name"] or "", x["name"].lower())):
            comps = [c["name"] for c in s["components"]] + [""] * 4
            dlc = "" if s["dlc"] == "None" else s["dlc"]
            f.write(f"| {s['name']} | {s['type_name']} | {comps[0]} | {comps[1]} | "
                    f"{comps[2]} | {comps[3]} | {dlc} |\n")
        f.write("\n## Set bonuses\n\n")
        for s in sorted(sets, key=lambda x: x["name"].lower()):
            f.write(f"### {s['name']}  _({s['type_name']})_\n\n")
            f.write("Requires: " + ", ".join(c["name"] for c in s["components"]) + "\n\n")
            if s["bonus_desc"]:
                f.write(strip_refs(s["bonus_desc"]).strip() + "\n\n")


if __name__ == "__main__":
    main()
