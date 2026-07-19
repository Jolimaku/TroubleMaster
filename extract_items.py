"""
Extract equipable/craftable Items, their stats, identify (suffix) system and
acquisition sources from TROUBLESHOOTER: Abandoned Children.

Inputs (already-decrypted plaintext produced by the official TSAC Modding Tool's
PLDataPacker --mode unpack):
    Unpack/Data/xml/Item.xml         - items + the ItemRank/ItemType/ItemCategory enums
    Unpack/Data/xml/ItemIdentify.xml - suffix pools (ItemIdentifyType) + suffix defs (ItemIdentify)
    Unpack/Data/xml/Monster.xml      - enemies and their <Rewards> drop tables
    Unpack/Data/xml/ItemCraft.xml    - crafting recipes (Recipe.name == produced item id)
    Unpack/Data/xml/Shop.xml         - the 11 category storefronts and their stock pools
    Unpack/Data/xml/Status.xml       - stat labels + Int/Percent format
And the (already plaintext) localization dictionary shipped with the game.

The identify model (see DATAMINING.md "Items, gear, and the identify system"):
identifying an unidentified gear drop rolls exactly one suffix from a pool keyed by
the item's *Type*; the rank filters which suffixes are eligible by their stat-line
count. Set/Unique/Poor/Quest gear is not identifiable (fixed stats). This script
emits the pools and per-rank eligibility so the web tool can show an item's possible
rolls without re-deriving the rule.

Output: ./output/items.json  (+ items.csv for quick inspection). Wiring the data into
web/data.js (with enemy->mission encounter resolution, as masteries get) is a follow-up.
"""
import os
import re
import csv
import glob
import json
import argparse
import datetime
import collections
import xml.etree.ElementTree as ET

from extract_masteries import Dictionary, idspace, is_developing, joint_training_teams
from missions import build_enemy_missions, collapse_enemy_encounters
from resolve_desc import resolve_description

# Item ids that are engine scaffolding, not real items.
SKIP_ITEMS = {"Dummy", "_DUMMY_"}
# Base_<field>s to treat as displayable stats are those whose stripped key is a real Status
# entry (built at runtime from Status.xml); this drops machine-only params (MaxFuel, Load, …)
# that are always 0 on the weapon/armor/accessory gear the tool surfaces.


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--game", default=r"E:\SteamLibrary\steamapps\common\Troubleshooter",
                   help="game install dir (for Dictionary/)")
    p.add_argument("--data", default=os.path.join(os.path.dirname(__file__), "Unpack", "Data"),
                   help="unpacked Data dir (with xml/)")
    p.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output"))
    p.add_argument("--lang", default="eng")
    return p.parse_args()


def _num(v):
    """'160' -> 160, '1.23' -> 1.23, '' / None -> 0."""
    if v is None or v == "":
        return 0
    f = float(v)
    return int(f) if f.is_integer() else f


def load_enum(item_xml, enum_id):
    """{class name: attrib dict} for one of Item.xml's sibling enum idspaces."""
    return {c.get("name"): dict(c.attrib) for c in idspace(item_xml, enum_id).findall("class")}


def main():
    a = parse_args()
    xml = os.path.join(a.data, "xml")
    dic = Dictionary(a.game, a.lang)

    item_xml = os.path.join(xml, "Item.xml")
    ranks = load_enum(item_xml, "ItemRank")
    types = load_enum(item_xml, "ItemType")
    categories = load_enum(item_xml, "ItemCategory")

    # ---- stat metadata (label + Int/Percent format) from Status.xml -------------------------
    status = {c.get("name"): dict(c.attrib)
              for c in idspace(os.path.join(xml, "Status.xml"), "Status").findall("class")}
    status_keys = set(status)          # which Base_<key> fields count as real stats
    used_stats = set()                 # stat keys actually referenced (items + suffixes)

    def item_stats(cls):
        out = {}
        for k, v in cls.attrib.items():
            if not k.startswith("Base_"):
                continue
            key = k[len("Base_"):]
            if key in status_keys and _num(v) != 0:
                out[key] = _num(v)
                used_stats.add(key)
        return out

    # ---- granted-mastery effect (device masteries) ------------------------------------------
    # An item's `Mastery` grant is a device-internal mastery with no display name and no entry in
    # the masteries tab, but its effect *is* real player info (what the device does). Resolve it to
    # inline text: prefer the mastery's authored description; if it has none (a bare stat modifier,
    # e.g. Destron gear = +100% melee damage), synthesize the line from its flat stat mods.
    mastery_cls = {c.get("name"): c
                   for c in idspace(os.path.join(xml, "Mastery.xml"), "Mastery").findall("class")}
    _effect_cache = {}

    def mastery_effect(mid):
        if mid in _effect_cache:
            return _effect_cache[mid]
        cls = mastery_cls.get(mid)
        eff = None
        if cls is not None:
            desc = resolve_description(dic, cls, idprefix="Mastery")
            if desc and desc.strip():
                eff = desc                       # authored effect text (may carry ref markup)
            else:
                mods = []
                for k, v in cls.attrib.items():
                    if k.startswith("Base_") and k[len("Base_"):] in status_keys and _num(v) != 0:
                        key = k[len("Base_"):]
                        unit = "%" if status.get(key, {}).get("Format") == "Percent" else ""
                        mods.append(f"{dic.get('Status/' + key + '/Title') or key} "
                                    f"{'+' if _num(v) > 0 else ''}{_num(v)}{unit}")
                eff = ", ".join(mods) or None
        _effect_cache[mid] = eff
        return eff

    # ---- suffix system (ItemIdentify.xml) ---------------------------------------------------
    id_xml = os.path.join(xml, "ItemIdentify.xml")
    suffix_cls = {c.get("name"): c for c in idspace(id_xml, "ItemIdentify").findall("class")}

    suffixes = {}
    suffix_lines = {}                  # id -> stat-line count (drives per-rank eligibility)
    for sid, c in suffix_cls.items():
        opts = c.find("IdentifyOptions")
        lines = []
        for p in (opts.findall("property") if opts is not None else []):
            lines.append({"stat": p.get("Type"),
                          "min": _num(p.get("Min")), "max": _num(p.get("Max"))})
            used_stats.add(p.get("Type"))
        suffix_lines[sid] = len(lines)
        suffixes[sid] = {
            "name": dic.get(f"ItemIdentify/{sid}/Title2") or sid,
            "name_template": dic.get(f"ItemIdentify/{sid}/Title"),   # "%s of Peak"
            "prob": _num(c.get("Prob")),
            "lines": lines,
        }

    # identifiable ranks, with their eligible stat-line band [OptionMinCount, OptionMaxCount]
    ident_ranks = {r: (int(_num(v.get("OptionMinCount"))), int(_num(v.get("OptionMaxCount"))))
                   for r, v in ranks.items()
                   if (v.get("Identifiable") or "").lower() == "true"}

    suffix_pools = {}                  # Type -> {suffixes:[...], eligible_by_rank:{rank:[...]}}
    for c in idspace(id_xml, "ItemIdentifyType").findall("class"):
        pool_ids = []
        opts = c.find("Options")
        for p in (opts.findall("property") if opts is not None else []):
            sid = p.get("name")
            # skip Developing pool entries (the game filters them; matches repo convention) and
            # any option with no suffix definition (the engine logs these as data errors).
            if (p.get("Developing") or "").lower() == "true" or sid not in suffix_cls:
                continue
            pool_ids.append(sid)
        eligible = {}
        for rank, (lo, hi) in ident_ranks.items():
            eligible[rank] = [s for s in pool_ids if lo <= suffix_lines[s] <= hi]
        suffix_pools[c.get("name")] = {"suffixes": pool_ids, "eligible_by_rank": eligible}

    def is_identifiable(cls):
        cat = categories.get(cls.get("Category"), {})
        rank = ranks.get(cls.get("Rank"), {})
        return ((cat.get("IsIdentify") or "").lower() == "true"
                and (rank.get("Identifiable") or "").lower() == "true"
                and cls.get("Type") in suffix_pools)

    # ---- sources: enemy drops (Monster.xml <Rewards>) ---------------------------------------
    sources = collections.defaultdict(list)     # item id -> [source dict]
    mon_sp = idspace(os.path.join(xml, "Monster.xml"), "Monster")

    def monster_name(c):
        info = c.get("Info")
        return (dic.get(f"ObjectInfo/{info}/Title") if info else None) or info or c.get("name")

    for c in mon_sp.findall("class"):
        name = c.get("name")
        # civilians are rescue NPCs / neutrals you never fight — not a real source (same rule the
        # mastery extractor applies to <Masteries> carriers).
        if name in SKIP_ITEMS or name.startswith(("Civil_", "Mon_Civil")):
            continue
        rewards = c.find("Rewards")
        if rewards is None:
            continue
        nm, lv, grade = monster_name(c), c.get("Lv"), c.get("Grade")
        for p in rewards.findall("property"):
            iid = p.get("Item")
            if not iid:
                continue
            src = {"type": "Enemy", "name": nm, "id": name, "lv": lv, "grade": grade}
            lo, hi = int(_num(p.get("Min") or 1)), int(_num(p.get("Max") or 1))
            if (lo, hi) != (1, 1):                          # [1,1] is the common case; omit it
                src["count"] = [lo, hi]
            sources[iid].append(src)

    # ---- sources: crafting (ItemCraft.xml Recipe; name == produced item id) ------------------
    # Recipes form a familiarity tree: crafting a recipe to mastery (Exp>=MaxExp) opens the recipes
    # it lists in UnLockRecipe (lobby_enter.lua CheckRecipeUnlock). Roots are Opened="true". It's a
    # clean single-parent forest, so record each recipe's one predecessor. The recipe's RequireLv
    # (1-3) is NOT a player gate in normal play (only internal exp-spillover tiering), so it's dropped.
    recipe_cls = {c.get("name"): c
                  for c in idspace(os.path.join(xml, "ItemCraft.xml"), "Recipe").findall("class")}
    pred = {}                                    # recipe -> the recipe you master to unlock it
    for name, c in recipe_cls.items():
        for u in (c.get("UnLockRecipe") or "").split(","):
            u = u.strip()
            if u and u != "None":
                pred[u] = name
    # quest rewards (Quest.xml Reward): a recipe (Type="Recipe" — the orphan set-piece recipes) or the
    # item itself (Type="Item" — e.g. the Flash/Camera pens, whose recipe is a dead orphan).
    quest_recipe, quest_item = set(), set()
    for c in idspace(os.path.join(xml, "Quest.xml"), "Quest").findall("class"):
        rw = c.find("Reward")
        for p in (rw.findall("property") if rw is not None else []):
            if p.get("Value") and p.get("Type") == "Recipe":
                quest_recipe.add(p.get("Value"))
            elif p.get("Value") and p.get("Type") == "Item":
                quest_item.add(p.get("Value"))
    craft_categories = {}                        # raw craft category id -> display name
    for iid, c in recipe_cls.items():
        if is_developing(c):
            continue
        cat = c.get("Category")
        mats = c.find("RequireMaterials")
        materials = [{"id": p.get("Item"), "amount": int(_num(p.get("Amount") or 1))}
                     for p in (mats.findall("property") if mats is not None else [])
                     if p.get("Item")]
        src = {"type": "Craft"}
        if cat:
            src["category"] = cat
            if cat not in craft_categories:
                # craft categories are a mix of profession names (Leather, Clothes, …) and
                # weapon-type names (Sword, Spray, …); resolve whichever dictionary has it.
                craft_categories[cat] = (dic.get(f"Profession/{cat}/Title")
                                         or dic.get(f"ItemType/{cat}/Title") or cat)
        # how the recipe is obtained: a starter (root), unlocked by mastering its predecessor,
        # a direct quest reward, or an orphan with no craft-unlock at all (the item is loot-only —
        # e.g. the elemental Seal amulets that drop, or the "Pascal's" drone-Legend gear).
        if (c.get("Opened") or "").lower() == "true":
            src["root"] = True
        elif iid in pred:
            p = pred[iid]
            src["unlocked_by"] = {"id": p, "name": dic.get(f"Item/{p}/Base_Title") or p}
        elif iid in quest_recipe:
            src["unlocked_by_quest"] = True
        else:
            src["no_unlock"] = True
        # AutoUnLock="false" recipes stay locked even once their predecessor is mastered until the
        # Pascal's-Base raid flips them on (missionResult_Custom.lua MissionResult_Custom_Raid_Pascal
        # → SpecialUnlockRecipe "PascalRecipe"). These are exactly the 40 raid-gated recipes.
        if (c.get("AutoUnLock") or "true").lower() == "false":
            src["raid_gated"] = True
        if materials:
            src["materials"] = materials
        sources[iid].append(src)

    # ---- sources: shops (Shop.xml); collapse duplicate stock entries to one {shop, price} ----
    shops = {}
    seen_shop = set()                            # (item, shop) already recorded
    for c in idspace(os.path.join(xml, "Shop.xml"), "Shop").findall("class"):
        sid = c.get("name")
        shops[sid] = {"name": dic.get(f"Shop/{sid}/Title") or c.get("Title") or sid,
                      "currency": c.get("Currency") or "Vill"}
        il = c.find("ItemList")
        for p in (il.findall("property") if il is not None else []):
            iid = p.get("Item")
            if not iid or (iid, sid) in seen_shop:
                continue
            seen_shop.add((iid, sid))
            src = {"type": "Shop", "shop": sid, "price": int(_num(p.get("Price")))}
            fr = p.get("Friendship")
            if fr and fr != "None":                          # friendship-gated stock
                src["friendship"] = fr
            sources[iid].append(src)

    # ---- sources: starting equipment (Pc.xml <BundleEquipment>) ------------------------------
    # A recruited character brings a fixed loadout — the only reliable source for the roster
    # uniques (e.g. Giselle's Wallenstein Sniper Rifle). Name via ObjectInfo/<Info>/Title.
    for c in idspace(os.path.join(xml, "Pc.xml"), "Pc").findall("class"):
        be = c.find("BundleEquipment")
        if be is None:
            continue
        info = c.get("Info") or c.get("name")
        cname = dic.get(f"ObjectInfo/{info}/Title") or info
        for p in be.findall("property"):
            iid = p.get("Item")
            if iid:
                sources[iid].append({"type": "Starting", "character": cname, "slot": p.get("name")})

    # ---- sources: quest item rewards (Quest.xml Reward Type="Item") --------------------------
    for iid in quest_item:
        sources[iid].append({"type": "Quest"})

    # ---- sources: civilian rescue rewards (CivilRescueReward.xml) -----------------------------
    # Rescuing a civilian mails rewards — Vill plus junk toy/doll boxes and fake-gold spoons you
    # mostly sell. These are the only source for those "story prop" items.
    rescue_items = set()
    for c in idspace(os.path.join(xml, "CivilRescueReward.xml"), "CivilRescueReward").findall("class"):
        rw = c.find("Rewards")
        for p in (rw.findall("property") if rw is not None else []):
            if p.get("Item") and p.get("Item") != "Vill":
                rescue_items.add(p.get("Item"))
    for iid in rescue_items:
        sources[iid].append({"type": "Rescue"})

    # ---- sources from a hostile's <Equipments> (Monster.xml) ---------------------------------
    # The Steal mastery (Misty/Thief) takes a **non-Gear** item from an *enemy human's* pockets/bags:
    # TopPocket/BottomPocket (= Inventory1/Inventory2), AlchemyBag, GrenadeBag, NinjaToolkit — and
    # explicitly "cannot steal Gear". Only humans have these (a drone's Inventory1/2 hold its Sensor/
    # Fuel), so gate on the carrier object's Race == "Human". Everything else in <Equipments> — worn
    # gear (Weapon/Body/Hand/Leg/Module…) or an unstealable Gear item in a pocket — isn't obtainable,
    # so it's NPC-only.
    obj_race = {c.get("name"): c.get("Race")
                for c in idspace(os.path.join(xml, "object.xml"), "Object").findall("class")}
    item_type = {c.get("name"): c.get("Type")
                 for c in idspace(item_xml, "Item").findall("class")}
    STEAL_SLOTS = {"Inventory1", "Inventory2", "AlchemyBag", "GrenadeBag", "NinjaToolkit"}
    steal_from, npc_equip = {}, {}
    for c in mon_sp.findall("class"):
        nm = c.get("name")
        if nm.startswith(("Civil_", "Mon_Civil")):
            continue
        human = obj_race.get(c.get("Object")) == "Human"
        eq = c.find("Equipments")
        for p in (eq.findall("property") if eq is not None else []):
            iid = p.get("Item")
            if not iid:
                continue
            stealable = human and p.get("name") in STEAL_SLOTS and item_type.get(iid) != "Gear"
            (steal_from if stealable else npc_equip).setdefault(iid, set()).add(monster_name(c))

    def has_real_source(srcs):
        return any(s["type"] in ("Enemy", "Shop", "Starting", "Quest", "Dialogue", "Steal", "Rescue",
                                 "StageLoot", "Box")
                   or (s["type"] == "Craft" and not s.get("no_unlock")) for s in srcs)

    # ---- sources: dialogue GiveItem (tutorial/story gifts) -----------------------------------
    # Kylie's drone-workshop tutorial (Dialog_Office.xml) hands out the base drone devices (weapons,
    # PowerDevice, the three Composite armors, Hover, Sensor — Common+Uncommon each); other dialogues
    # gift story items. A last-resort source for otherwise-orphan gear. (These aren't "built into" the
    # drone — you're given them, then pick from what you have when building; the base set feels
    # unlimited because unequipping a drone device recovers it as parts.)
    given = set()
    for f in glob.glob(os.path.join(xml, "Dialog", "*.xml")) + glob.glob(os.path.join(xml, "Dialog_*.xml")):
        try:
            droot = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        for p in droot.iter("property"):
            # GiveItem = a plain gift; RefillItem = a one-time gift used for uniques so replaying the
            # scene can't dupe them (e.g. Albus's 'Black Pearl' apprenticeship sneakers).
            if p.get("Command") in ("GiveItem", "RefillItem") and p.get("ItemName"):
                given.add(p.get("ItemName"))
    for iid in given:
        if not has_real_source(sources.get(iid, [])):
            sources[iid].append({"type": "Dialogue"})

    # ---- sources: stage-placed loot + loot boxes --------------------------------------------
    # Items placed as <ItemCollection> loot in a .stage file (e.g. the Troubleshooter Jacket), and
    # items in an ItemBox.xml loot table (chests, variant Box_Lv<N>_<Easy|Normal|Hard|Rare> picked by
    # stage level+difficulty at runtime). Fallback sources for otherwise-orphan loot.
    stage_loot = {}                              # item -> {stage basenames}
    for f in glob.glob(os.path.join(a.data, "stage", "*.stage")):
        try:
            sroot = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        base = os.path.basename(f)
        for coll in sroot.iter("ItemCollection"):
            for it in coll.iter("Item"):
                if it.get("ItemType"):
                    stage_loot.setdefault(it.get("ItemType"), set()).add(base)
    box_items = {}                               # item -> {Box_Lv<N>_<tier> names}
    for b in idspace(os.path.join(xml, "ItemBox.xml"), "ItemBox").findall("class"):
        for it in b.iter("Item"):
            if it.get("ItemType"):
                box_items.setdefault(it.get("ItemType"), set()).add(b.get("name"))
    stage_missions = {}                          # stage basename -> {mission ids} (mission.xml Stage=)
    for c in ET.parse(os.path.join(xml, "mission.xml")).getroot().iter("class"):
        if c.get("Stage"):
            stage_missions.setdefault(c.get("Stage"), set()).add(c.get("name"))
    for iid, stages in stage_loot.items():
        if not has_real_source(sources.get(iid, [])):
            sources[iid].append({"type": "StageLoot", "stages": sorted(stages)})
    for iid, boxes in box_items.items():
        if not has_real_source(sources.get(iid, [])):
            sources[iid].append({"type": "Box", "boxes": sorted(boxes)})

    # Steal is only worth surfacing when it's the *only* way to get the item — once Gear is excluded
    # the other stealable pocket items (common potions/grenades) also drop, so gating on "no other
    # source" leaves just the steal-exclusive ones (the Administrator Card keys).
    for iid, carriers in steal_from.items():
        if not has_real_source(sources.get(iid, [])):
            sources[iid].append({"type": "Steal", "carriers": sorted(carriers)})

    for iid, carriers in npc_equip.items():
        if not has_real_source(sources.get(iid, [])):
            sources[iid].append({"type": "NPC", "carriers": sorted(carriers)})

    # enemy→mission resolution (also reused for the web drops below); needed here so obtainability
    # matches what the web actually shows — an enemy that resolves to no fightable mission (phantom
    # carrier, dropped by collapse_enemy_encounters) doesn't count as a source.
    stage_dir = os.path.join(a.data, "stage")
    enemy_missions, mission_info, placed, enemy_dialog = build_enemy_missions(xml, stage_dir, dic)
    jt_teams = joint_training_teams(xml, dic, True)

    def obtainable(iid):
        srcs = sources.get(iid, [])
        if any(s["type"] in ("Shop", "Starting", "Quest", "Dialogue", "Steal", "Rescue", "StageLoot",
                             "Box", "NPC") or (s["type"] == "Craft" and not s.get("no_unlock"))
               for s in srcs):
            return True
        enemy = [s for s in srcs if s["type"] == "Enemy"]
        if enemy:                                 # only counts if it resolves to a real encounter
            drops, _ = collapse_enemy_encounters(enemy, enemy_missions, mission_info, placed,
                                                 enemy_dialog, jt_teams)
            return bool(drops)
        return False

    # ---- items ------------------------------------------------------------------------------
    items = []
    for c in idspace(item_xml, "Item").findall("class"):
        iid = c.get("name")
        # skip scaffolding, the Etc category (cosmetic costumes / coins / documents), and Ghost_*
        # (the engine's fallback weapon when a unit is unarmed — not a real item).
        if iid in SKIP_ITEMS or iid.startswith("Ghost_") or c.get("Category") == "Etc":
            continue
        # drop items nothing can grant and no NPC even carries — cut/unused data, combat-logic-only
        # type stubs, and civilian-worn flavor (verified by a full id grep across every lua/xml/stage).
        if not obtainable(iid):
            continue
        main_status = c.get("MainStatus")
        ability = c.get("Ability")
        mastery = c.get("Mastery")
        # a granted ability is real player info (what a device/potion does) — resolve its name so
        # the web pill reads properly even for the few utility abilities not in the Abilities tab.
        grants_ability = ({"id": ability, "name": dic.get(f"Ability/{ability}/Title") or ability}
                          if ability and ability != "None" else None)
        # granted (device) mastery: inline its resolved effect text rather than a dead id/pill
        grants_mastery = None
        if mastery and mastery != "None":
            eff = mastery_effect(mastery)
            if eff:
                grants_mastery = {"id": mastery, "effect": eff}
        set_id = None                             # ItemSet membership is filled in below
        items.append({
            "id": iid,
            "name": dic.get(f"Item/{iid}/Base_Title") or iid,
            "category": c.get("Category"),
            "type": c.get("Type"),
            "rank": c.get("Rank"),
            "require_lv": int(_num(c.get("RequireLv"))),
            "main_status": main_status if main_status and main_status != "None" else None,
            "stats": item_stats(c),
            "flavor": dic.get(f"Item/{iid}/Desc_Suffix") or None,
            "grants_ability": grants_ability,
            "grants_mastery": grants_mastery,     # {id, effect} — inlined device-mastery effect
            "set": set_id,
            "identifiable": is_identifiable(c),
            "sources": sources.get(iid, []),
        })

    # ItemSet membership (0-1 per item). The item's `set` is the equipment-set *name* the way the
    # mastery pipeline keys the Equipment Sets tab — the set-bonus mastery's Base_Title minus the
    # " - Set N" suffix (patterns mirror write_web_data._parse_eqset, incl. the Korean " - N 세트").
    # This links to the exact tab card across languages, unlike the ItemSet Title (2 differ by
    # typo/case). Falls back to the ItemSet Title/id if a set somehow has no bonus mastery.
    eqset_suffix = (r"^(.*) - Set (\d+)$", r"^(.*) - (\d+)-piece Set Bonus$", r"^(.*) - (\d+) ?세트$")

    def eqset_base(name):
        for rx in eqset_suffix:
            m = re.match(rx, name)
            if m:
                return m.group(1)
        return name

    item_by_id = {it["id"]: it for it in items}
    for c in idspace(os.path.join(xml, "ItemSet.xml"), "ItemSet").findall("class"):
        sid = c.get("name")
        set_name = None
        for n in range(1, 6):
            m = c.get(f"Mastery{n}")
            mt = dic.get(f"Mastery/{m}/Base_Title") if m and m != "None" else None
            if mt:
                set_name = eqset_base(mt)
                break
        set_name = set_name or dic.get(f"ItemSet/{sid}/Title") or sid
        for n in range(1, 6):
            comp = c.get(f"Item{n}")
            if comp and comp != "None" and comp in item_by_id:
                item_by_id[comp]["set"] = set_name

    # ---- lookups (resolved display names, emitted once) -------------------------------------
    type_meta = {t: {"name": dic.get(f"ItemType/{t}/Title") or t,
                     "category": v.get("Parent"),
                     "equip_position": v.get("EquipmentPosition"),
                     "two_handed": (v.get("IsTwoHandMeleeWeapon") or "").lower() == "true"}
                 for t, v in types.items()}
    rank_meta = {r: {"name": dic.get(f"ItemRank/{r}/Title") or r,
                     "weight": int(_num(v.get("Weight"))),               # rarity order/tier
                     "identifiable": (v.get("Identifiable") or "").lower() == "true"}
                 for r, v in ranks.items()}
    stat_meta = {s: {"label": dic.get(f"Status/{s}/Title") or s,
                     "format": (status.get(s, {}).get("Format") or "Int")}
                 for s in sorted(used_stats)}

    # weapon Types a playable character can equip (object.xml PC_* EnableEquipWeapon); the rest are
    # NPC-only, which the web tool splits into its own subtab.
    pc_weapon_types = set()
    for c in idspace(os.path.join(xml, "object.xml"), "Object").findall("class"):
        if (c.get("name") or "").startswith("PC_"):
            for w in (c.get("EnableEquipWeapon") or "").split(","):
                if w.strip():
                    pc_weapon_types.add(w.strip())

    out = {
        "generated": datetime.date.today().isoformat(),
        "items": items,
        "types": type_meta,
        "ranks": rank_meta,
        "shops": shops,
        "craft_categories": craft_categories,
        "stat_meta": stat_meta,
        "suffix_pools": suffix_pools,
        "suffixes": suffixes,
        "pc_weapon_types": sorted(pc_weapon_types),
    }

    os.makedirs(a.out, exist_ok=True)
    json.dump(out, open(os.path.join(a.out, "items.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # ---- web/items.js: denormalized for the web tool, with enemy drops resolved to the same
    # mission/Joint-Training encounters the mastery tab shows (shared collapse helper; the encounter
    # data was built above for the obtainability filter). Raw output/items.json keeps flat `sources`;
    # the web form groups them by channel. --------------------------------------------------------
    KEEP = ("id", "name", "category", "type", "rank", "require_lv", "main_status", "stats",
            "flavor", "grants_ability", "grants_mastery", "set", "identifiable")

    def stage_loot_missions(stages):
        seen, out_m = set(), []
        for st in stages:
            for mid in stage_missions.get(st, ()):
                info = mission_info.get(mid)
                if info and info["title"] not in seen:
                    seen.add(info["title"])
                    out_m.append({"name": info["title"], "level": info["level"], "case": info["case"]})
        if not out_m:                             # unmapped stage → humanized file name as a location
            out_m = [{"name": re.sub(r"(?<=[a-z])(?=[A-Z])", " ", st[:-6].replace("_", " ")).strip()}
                     for st in stages]
        return sorted(out_m, key=lambda x: (x.get("level", 999), x["name"]))

    def box_tiers(boxes):
        tiers = set()
        for b in boxes:
            m = re.match(r"Box_Lv(\d+)_(\w+)", b)
            if m:
                tiers.add((int(m.group(1)), m.group(2)))
        return [{"level": lv, "tier": tr} for lv, tr in sorted(tiers)]

    def web_item(it):
        w = {k: it[k] for k in KEEP}
        drops, _ = collapse_enemy_encounters(
            [s for s in it["sources"] if s["type"] == "Enemy"],
            enemy_missions, mission_info, placed, enemy_dialog, jt_teams)
        craft = [{k: v for k, v in s.items() if k != "type"}
                 for s in it["sources"] if s["type"] == "Craft"]
        sold_at = [{k: v for k, v in s.items() if k != "type"}
                   for s in it["sources"] if s["type"] == "Shop"]
        starts = sorted({s["character"] for s in it["sources"] if s["type"] == "Starting"})
        npc = next((s["carriers"] for s in it["sources"] if s["type"] == "NPC"), None)
        if drops:
            w["drops"] = drops                            # [{name, lv:[min,max], missions:[...]}]
        if craft:
            w["craft"] = craft                            # [{require_lv, category, materials}]
        if sold_at:
            w["sold_at"] = sold_at                        # [{shop, price[, friendship]}]
        if starts:
            w["starts_with"] = starts                     # [character name, …] recruited-with
        if any(s["type"] == "Quest" for s in it["sources"]):
            w["quest_reward"] = True                      # given by a quest (see Quests tab)
        if any(s["type"] == "Rescue" for s in it["sources"]):
            w["rescue_reward"] = True                     # mailed for rescuing a civilian
        if any(s["type"] == "Dialogue" for s in it["sources"]):
            w["given_by_event"] = True                    # handed out by a tutorial/story dialogue
        stages = sorted({st for s in it["sources"] if s["type"] == "StageLoot" for st in s["stages"]})
        if stages:
            w["stage_loot"] = stage_loot_missions(stages)  # [{name[, level, case]}] mission locations
        boxes = sorted({b for s in it["sources"] if s["type"] == "Box" for b in s["boxes"]})
        if boxes:
            w["loot_box"] = box_tiers(boxes)               # [{level, tier}] loot-box variants
        steal = next((s["carriers"] for s in it["sources"] if s["type"] == "Steal"), None)
        if steal:
            w["steal_from"] = steal                       # pocket item a Thief can steal — from whom
        if npc:
            w["npc_carriers"] = npc                       # NPC-only gear — who wears it (not lootable)
        return w

    web = {**out, "items": [web_item(it) for it in items]}
    web_name = "items.js" if a.lang == "eng" else f"items.{a.lang}.js"
    web_path = os.path.join(os.path.dirname(__file__), "web", web_name)
    with open(web_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("window.TS_ITEMS = " + json.dumps(web, ensure_ascii=False, separators=(",", ":")) + ";")

    # quick-inspection CSV (equippable gear only, one row per item)
    equip = [it for it in items if it["category"] in ("Weapon", "Armor", "Accessory")]
    with open(os.path.join(a.out, "items.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "category", "type", "rank", "require_lv", "main_status",
                    "identifiable", "set", "sources"])
        for it in sorted(equip, key=lambda x: (x["category"], x["type"], x["name"].lower())):
            src = "; ".join(sorted({s["type"] for s in it["sources"]}))
            w.writerow([it["id"], it["name"], it["category"], it["type"], it["rank"],
                        it["require_lv"], it["main_status"] or "", it["identifiable"],
                        it["set"] or "", src])

    ident = sum(1 for it in items if it["identifiable"])
    with_drops = sum(1 for it in web["items"] if it.get("drops"))
    print(f"items: {len(items)} ({len(equip)} equippable, {ident} identifiable, "
          f"{with_drops} with resolved drops), suffixes: {len(suffixes)}, "
          f"pools: {len(suffix_pools)}, shops: {len(shops)}, stats: {len(stat_meta)} -> "
          f"{os.path.join(a.out, 'items.json')}, {web_path}")


if __name__ == "__main__":
    main()
