"""
Resolve the $Placeholder$ tokens in mastery descriptions into real text.

The game stores, per mastery, a <Desc_Base> with one <property Text="..."> per line
plus a <FormatKeyword> list. Each <property FormatKey=K ...> says how to fill $K$:
  - Value="$ApplyAmountValueN$"  -> mastery.Base_ApplyAmount[N] (+"%" if ApplyAmountType[N]=="Percent")
  - Value="$MasteryBuff$" etc.   -> a named buff/group/field/mastery on the mastery, resolved to its Title
  - Idspace=X Key=Y Value=sub    -> dictionary lookup  X/Y/sub   (sub is usually "Title")
  - otherwise (literal)          -> the translated string in dict Mastery/<id>/Desc_Base/<n>/FormatKeyword/<k>/Value
The English line text itself is dict  Mastery/<id>/Desc_Base/<n>/Text.
"""
import re
from collections import Counter

# Diagnostic: when REPORT_UNRESOLVED is on, every dictionary miss that falls back to a raw English
# id — or leaves a $token$ literal — is recorded here, so `extract_masteries.py --report-unresolved`
# can surface untranslated leaks (the Slashing/Melee class of bug) at their source instead of
# grepping the output. Off by default (zero overhead on normal runs).
REPORT_UNRESOLVED = False
_UNRESOLVED = Counter()


def note_unresolved(kind, space, key):
    """Record one fallback-to-raw event ((kind, idspace/context, key)) when reporting is enabled."""
    if REPORT_UNRESOLVED:
        _UNRESOLVED[(kind, space, key)] += 1


def unresolved_report():
    """(kind, space, key, count) rows for everything that leaked, most frequent first."""
    return [(k, s, key, c) for (k, s, key), c in
            sorted(_UNRESOLVED.items(), key=lambda kv: (-kv[1], kv[0]))]


# $Mastery<X>$ token -> (mastery attribute, idspace used to resolve its Title)
MASTERY_REF = {
    "$MasteryBuff$": ("Buff", "Buff"),
    "$MasterySubBuff$": ("SubBuff", "Buff"),
    "$MasteryThirdBuff$": ("ThirdBuff", "Buff"),
    "$MasteryForthBuff$": ("ForthBuff", "Buff"),
    "$MasteryBuffGroup$": ("BuffGroup", "BuffGroup"),
    "$MasterySubBuffGroup$": ("SubBuffGroup", "BuffGroup"),
    "$MasteryThirdBuffGroup$": ("ThirdBuffGroup", "BuffGroup"),
    "$MasteryFieldEffect$": ("FieldEffect", "FieldEffect"),
    "$MasteryMastery$": ("Mastery", "Mastery"),
    "$MasteryPerformanceEffectList$": ("PerformanceEffect", "PerformanceEffect"),
}
APPLY_RE = re.compile(r"^\$ApplyAmountValue(\d*)\$$")
PLACE_RE = re.compile(r"\$(\w+)\$")
_MULTISPACE_RE = re.compile(r" {2,}")   # collapse space runs (template typos + empty-token gaps)

# ---- Inline reference markup ------------------------------------------------------------------
# A resolved buff/mastery/ability/group reference is emitted as an inline, positioned, typed marker
# so the web tool renders a per-occurrence chip — two same-named refs (a module-mastery header vs an
# ability token) then link to the right target. The marker is a control-char *sentinel* triple
# `\x01kind\x02label\x03`: control chars never occur in game text (no delimiter collision, incl. the
# `[…]` the source uses for josa), and Korean josa — which attaches to the char immediately before it
# — still sees the label's last syllable because nothing sits between it and a following `[을]`
# except the transparent `\x03` (handled in `apply_josa`). The web parses the same triple; `strip_refs`
# reduces it to the bare label for plain-text exports.
_REF_S, _REF_SEP, _REF_E = "\x01", "\x02", "\x03"

# Block-indent marker. A line beginning with this control char is a continuation/effect line that the
# web renders inside a padded container, so the *whole* wrapping line stays indented — not just its
# first row, which is all the old leading-spaces hack achieved once descriptions became rich text.
# Line-start only, single char: it never collides with game text or the ref sentinels, and apply_josa
# ignores it (the char after it is always ordinary text, never a josa bracket). `strip_refs` turns it
# back into the historical 4 leading spaces for the plain-text (non-web) dumps.
INDENT = "\x11"


def indent_block(s):
    """Prefix every line of `s` with the block-indent marker (see `INDENT`)."""
    return "\n".join(INDENT + ln for ln in s.split("\n"))


def _ref(kind, title):
    """Sentinel-wrapped positioned reference of `kind` (or "" for an empty/None title)."""
    return f"{_REF_S}{kind}{_REF_SEP}{title}{_REF_E}" if title else ""


_REF_RE = re.compile(_REF_S + r"([a-z]+)" + _REF_SEP + "([^" + _REF_E + "]*)" + _REF_E)


def strip_refs(s):
    """Reduce sentinel refs to their bare label and block-indent markers to 4 leading spaces —
    for plain-text (non-web) outputs."""
    return _REF_RE.sub(lambda m: m.group(2), s).replace(INDENT, "    ") if s else s


def ref_markup(kind, title):
    """Public: wrap a resolved title as inline positioned ref markup — for descriptions synthesized
    outside `resolve_description` (e.g. the sub-ability list of a subcommand ability)."""
    return _ref(kind, title)


# idspace/refspace/CaseType -> the web chip kind. Only these four name linkable entities; every other
# idspace (element/damage-type/target/field-effect words) resolves to plain text.
_REF_KIND = {"Buff": "buff", "BuffGroup": "group", "Mastery": "mastery", "Ability": "ability"}


# Tokens not tied to a per-mastery FormatKeyword are global words. Try these
# dictionary locations in order.
GLOBAL_FALLBACK = [
    "WordCollection/{}/Text",
    "Reaction/{}/Title",
    "Status/{}/Title",
    "MasteryType/{}/Title",
    "Buff/{}/Title",
]


def resolve_global(dic, token):
    for tmpl in GLOBAL_FALLBACK:
        v = dic.get(tmpl.format(token))
        if v is not None:
            return v
    return None


# ---- FormatMessage: $token$ substitution + Korean josa (postposition) selection -------------
# Game templates carry josa markers like "체력[이]" — the particle depends on whether the
# preceding syllable has a final consonant (받침). English templates have no markers (no-op).
JOSA = {"이": ("이", "가"), "은": ("은", "는"), "을": ("을", "를"),
        "과": ("과", "와"), "와": ("과", "와"), "는": ("은", "는"), "가": ("이", "가")}
# A josa marker can sit directly after a ref sentinel end (\x03); the regex captures that \x03 in
# group 2 (re-emitted intact) so the particle still keys off the label's final syllable, not `\x03`.
JOSA_RE = re.compile(r"([^\s\x01\x02\x03])(\x03?)\[([이은을과와는가로])\]")


def _has_batchim(ch):
    o = ord(ch)
    return (o - 0xAC00) % 28 if 0xAC00 <= o <= 0xD7A3 else None   # 0 = no 받침, 8 = ㄹ, else other


def apply_josa(text):
    def repl(m):
        prev, end, mark = m.group(1), m.group(2), m.group(3)
        jong = _has_batchim(prev)
        if jong is None:                       # non-Korean preceding char — drop the marker
            return prev + end
        if mark == "로":                        # 로/으로: 로 after no-받침 or ㄹ, else 으로
            return prev + end + ("로" if jong in (0, 8) else "으로")
        pair = JOSA.get(mark)
        if not pair:
            return prev + end
        return prev + end + (pair[0] if jong else pair[1])
    return JOSA_RE.sub(repl, text)


def format_message(dic, guide_key, vars):
    """Resolve a GuideMessage/<key>/Title_Base (or any template string) — substitute $tokens$
    from vars (dropping colour-markup tokens), then apply Korean josa. `guide_key` may be a key
    (looked up) or a literal template."""
    tmpl = dic.get(f"GuideMessage/{guide_key}/Title_Base") or guide_key
    if not tmpl:
        return ""

    def sub(m):
        tok = m.group(1)
        if tok in COLOR_WORDS or tok.endswith("_ON") or tok.endswith("_OFF"):
            return ""                              # colour markup ($White$ / $Blue_ON$ …)
        return str(vars.get(tok, m.group(0)))
    out = apply_josa(PLACE_RE.sub(sub, tmpl))
    return re.sub(r" {2,}", " ", out).strip()      # tidy spaces left by stripped colour tokens


# Aura relation noun (Ally/Enemy/Any) — the game ships no dictionary entry, so translate the
# 3-value enum directly; unknown languages fall back to the raw id.
AURA_RELATION = {
    "eng": {"Enemy": "an enemy", "Ally": "an ally", "Any": "a unit"},
    "kor": {"Enemy": "적", "Ally": "아군", "Any": "대상"},
}


def _relation_word(dic, rel):
    if rel in (None, "None", ""):
        return None
    return AURA_RELATION.get(getattr(dic, "lang", "eng"), {}).get(rel, rel)


# Base_<field>s that are buff *parameters*, not Status stat deltas
_BUFF_PARAM = {"Base_MaxStack", "Base_ApplyAmount", "Base_ApplyAmount2", "Base_ApplyAmount3",
               "Base_ApplyAmount4", "Base_ApplyAmount5", "Base_HPChangeValue"}
_RADIUS_RE = re.compile(r"(\d+)")


def _parse_eval(expr):
    """Parse an `Eval_<Stat>` value — a runtime expression that is, in practice, either a bare
    constant or a level-scaled `coef * Lv` form (also `Lv * coef`, `Lv`, `-Lv*coef`, `Lv * -coef`).
    Returns `(coef, per_level)` (coef is the per-level rate when per_level), or None if unrecognised."""
    expr = (expr or "").strip()
    if not expr:
        return None
    try:
        return float(expr), False          # bare constant -> flat delta
    except ValueError:
        pass
    if "Lv" not in expr:
        return None
    coef = 1.0                              # product of the numeric factors flanking Lv
    for part in expr.split("Lv", 1):
        part = part.strip().strip("*").strip()
        if part in ("", "+"):
            continue
        if part == "-":
            coef = -coef
            continue
        try:
            coef *= float(part)
        except ValueError:
            return None
    return coef, True


def _stat_delta_line(dic, stat, coef, status_fmt, per_level=False):
    """One stat-bonus line via the game's `Status/<stat>/Desc_Increase|Decrease[ByLevel]` template
    ($Status$=Title, $Value$=|coef|, +% for Percent stats). Returns "" if coef is 0 or untitled."""
    if not coef:
        return ""
    suffix = "ByLevel" if per_level else ""
    tmpl = dic.get(f"Status/{stat}/Desc_{'Increase' if coef > 0 else 'Decrease'}{suffix}")
    title = dic.get(f"Status/{stat}/Title")
    if not tmpl or not title:
        return ""
    num = abs(coef)
    s = str(int(num)) if num == int(num) else ("%g" % num)
    if status_fmt.get(stat) == "Percent":
        s += "%"
    out = PLACE_RE.sub(lambda m: {"Status": title, "Value": s}.get(m.group(1), m.group(0)), tmpl)
    return apply_josa(out)


def describe_buff(dic, buff, status_fmt, with_duration=True):
    """Generate a buff's effect text the way the engine's auto-tooltip does, for the common
    'stat core': its authored Desc_Base, flat Base_<Stat> deltas + level-scaled Eval_<Stat> deltas
    (both via Status.xml Desc_Increase/Decrease[ByLevel]), an aura line, and (unless
    with_duration=False) a duration line. `status_fmt` maps a Status id to its Format (Int/Percent).
    with_duration=False yields effect-only text — used when a buff stands in for a mastery's
    description (a bare duration isn't an effect). The long tail (discharge / reflect / HP-over-time)
    is left to a later phase — those carry computed values (see Damage calculation)."""
    lines = []
    # 0. SubType category header (`BuffSubType/<SubType>/Title_Buff|Debuff` — "Positive Aura" /
    #    "Physical Debuff" / "Mental Buff" …, keyed on SubType × positive(Buff)/negative(Debuff)).
    #    It's the tooltip's category line (see in-game aura tooltips), shown only in the full buff
    #    tooltip — omitted when with_duration=False (the buff standing in for a mastery description,
    #    where a category label isn't an effect). State/System-typed buffs carry no +/- label.
    if with_duration:
        st, typ = buff.get("SubType"), buff.get("Type")
        if st and st != "None" and typ in ("Buff", "Debuff"):
            label = dic.get(f"BuffSubType/{st}/Title_{'Buff' if typ == 'Buff' else 'Debuff'}")
            if label:
                lines.append(label)
    # 1. authored description (206 buffs) — same Desc_Base/FormatKeyword shape as masteries
    db = resolve_description(dic, buff, "Buff")
    if db.strip():
        lines.append(db)
    # 2. flat stat deltas (Base_<Stat>) and level-scaled deltas (Eval_<Stat> = "coef * Lv" → the
    #    per-level Desc template). Both render via the game's Status Desc_Increase/Decrease[ByLevel].
    for k, raw in buff.attrib.items():
        if k.startswith("Base_") and k not in _BUFF_PARAM:
            # a level-indexed list ("100, 200, 300") gives the base (Lv1) value, matching the
            # in-game tooltip for the freshly-applied buff — e.g. Faith/Brave (Excitement) show 100
            try:
                val = float((raw or "").split(",")[0])
            except (TypeError, ValueError):
                continue
            line = _stat_delta_line(dic, k[5:], val, status_fmt)
        elif k.startswith("Eval_"):
            parsed = _parse_eval(raw)
            if parsed is None:
                continue
            line = _stat_delta_line(dic, k[5:], parsed[0], status_fmt, per_level=parsed[1])
        else:
            continue
        if line:
            lines.append(line)
    # 3. action restrictions (Stun/Bind/Silence…) — flags default true; false = that action is barred
    barred = [dic.get(f"BuffUnableType/{f}/Title")
              for f in ("Movable", "Attackable", "Assistable", "Healable") if buff.get(f) == "false"]
    barred = [b for b in barred if b]
    if barred:
        tmpl = dic.get("GuideMessage/UnableAbilityMessage/Title_Base") or ""
        lines.append(format_message(dic, tmpl.replace("%s", ", ".join(barred)), {}))
    # 4. aura (52 buffs) — pick the message variant the engine uses:
    #    cover landmark (Conceal) / sight / adjacent (Near) / standard "approaches within N".
    #    None-relation, non-cover auras (continuous fortress/shield) carry their own Desc_Base, so
    #    the "approaches" line doesn't apply — skip it for them.
    aura = buff.get("AuraBuff")
    if aura and aura != "None":
        bt = _title(dic, "Buff", aura)
        rng = buff.get("AuraRange") or ""
        if buff.get("IsCoverableObject") == "true":
            lines.append(format_message(dic, "AuraBuffMessage_Conceal_For_Aura", {"Buff": bt}))
        else:
            rel = _relation_word(dic, buff.get("AuraRelation"))
            if rel:
                key = ("AuraBuffMessage_Sight" if rng == "Sight"
                       else "AuraBuffMessage_Near" if "ExSelf" in rng or rng.startswith("Box1")
                       else "AuraBuffMessage")
                rad = _RADIUS_RE.search(rng)
                lines.append(format_message(dic, key,
                             {"Buff": bt, "Relation": rel, "Dist": rad.group(1) if rad else ""}))
    # 4. duration (meta, not an effect — omitted when the buff stands in for a mastery description)
    if with_duration and buff.get("IsTurnShow") == "true" and buff.get("Turn") not in (None, "None", "", "-1"):
        lines.append(format_message(dic, "Buff_Turn", {"Turn": buff.get("Turn")}))
    return "\n".join(lines)


# Bare $Token$s that appear directly in ability Desc_Base *text* (no FormatKeyword property),
# read straight off the owning Ability's attributes. (kind: "Buff"/"BuffGroup" -> resolve the
# referenced id to its Title; "%" -> numeric value + "%"; None -> numeric value as-is.)
OWNER_REF = {
    "ApplyBuff": ("ApplyTargetBuff", "Buff"),
    "ApplySubBuff": ("ApplyTargetSubBuff", "Buff"),
    "CancelBuff": ("CancelTargetBuff", "Buff"),
    "RemoveBuff": ("RemoveBuff", "Buff"),
    "BuffGroup": ("BuffGroup", "BuffGroup"),
    "ApplyBuffDuration": ("ApplyTargetBuffDuration", None),
    "ApplyBuffChance": ("ApplyTargetBuffChance", "%"),
    "ApplyTargetBuffLv": ("ApplyTargetBuffLv", None),
    "ApplyCost": ("ApplyCost", None),
    "KnockbackPower": ("KnockbackPower", None),
    "ApplyAct": ("ApplyAct", None),
    "Grade": ("Grade", None),
    "DamageType": ("SubType", "AbilitySubType"),  # the ability's damage element (Earth/Fire/Piercing…).
    # AbilitySubType covers both elements AND physical classes (Slashing/Blunt/Piercing/EMP); MasteryType
    # has only the elements, so physical classes leaked as raw English ids (same trap as the Element column).
    "Target": ("Target", "TargetType"),           # who the ability can hit (Enemy→적 / Ally→아군 / Any→모든 대상)
    # buff Desc_Base tokens (resolved from the owning Buff's attributes)
    "AddBuffName": ("AddBuff", "Buff"),
    "AddBuffName2": ("AddBuff2", "Buff"),
    "AddBuffName3": ("AddBuff3", "Buff"),
    "Explosion": ("ExplosionType", "Ability"),
    "MaxStack": ("Base_MaxStack", None),
}
# in-text colour-markup control tokens ($White$, $Blue_ON$/$Blue_OFF$, …) — presentation, not
# content, and absent from the dictionaries, so drop them from the resolved text.
COLOR_WORDS = {"White", "Black", "Red", "Green", "Blue", "Yellow", "Orange", "Cyan",
               "Gray", "Grey", "Cream", "Perano", "LimeGold", "Gold", "Pink", "Purple"}


def resolve_owner_token(dic, owner, tok, cost_label=None):
    """Resolve a bare ability-text $tok$ from the owning class's attributes, or "" to drop a
    colour-markup token. Returns None if `tok` isn't an owner token (caller falls back).
    `$DamageAmount$` builds the game's per-hit damage formula (see resolve_damage_amount);
    `cost_label` is the owner unit's action-resource title (Vigor/Rage/Fuel) for its `Cost` term.
    Buff/ability refs come back as inline sentinel markup."""
    if tok in COLOR_WORDS or tok.endswith("_ON") or tok.endswith("_OFF"):
        return ""
    if tok == "DamageAmount":
        return resolve_damage_amount(dic, owner, cost_label)
    spec = OWNER_REF.get(tok)
    if spec is None:
        return None
    attr, kind = spec
    raw = owner.get(attr)
    plain = False
    if raw in (None, "None", "") and tok == "BuffGroup":
        raw = owner.get("Group")            # a buff's `Group` is its *damage element* (Enchant* →
        plain = True                        # "Fire"/"Ice"…) — a plain word, not the Fire *buff* family,
        #                                     so don't chip it (abilities' `BuffGroup`, e.g. Stealth, is).
    if raw in (None, "None", "") and tok == "Grade":
        # $Grade$ is used only by the Tame ability, which carries no `Grade` attribute — the taming cap
        # is the **Epic** beast rank by default (Legendary with the Monster Taming mastery, whose own
        # text says so). Fill the Epic *promotion mastery* title (the rank name, localized) as plain text.
        return dic.get("Mastery/Epic/Base_Title") or "Epic"
    if raw in (None, "None", ""):
        return ""
    if kind in ("Buff", "BuffGroup", "Ability", "MasteryType", "AbilitySubType", "TargetType"):
        title = _title(dic, kind, raw)
        rkind = {"Buff": "buff", "BuffGroup": "group", "Ability": "ability"}.get(kind)
        if rkind and title and not plain:          # linkable ref → positioned chip; else plain word
            return _ref(rkind, title)
        return title
    if kind == "%":
        return _fmt_num(raw) + "%"
    return _fmt_num(raw)


def _fmt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else ("%g" % f)


def _damage_stat_title(dic, name, cost_label):
    """Display name of one `AdditionalApplyAmount` stat in a $DamageAmount$ formula. Mirrors
    shared_tooltip.lua GetDamageAmountText: normal stats use Status/<name>/Title_HPChangeFunctionArg
    (its `pairs` loop), while the specials are hand-mapped. Returns None to drop the term (a
    unit-dependent Cost with no known owner resource)."""
    if name == "SP":                    # element-specific "<Element> SP" needs a unit → generic "SP"
        return _title(dic, "Status", "MaxSP")
    if name == "HP":
        return dic.get("Status/MaxHP/Title")
    if name == "EnemyHP":
        return (dic.get("WordCollection/Enemy/Text") or "Enemy") + " " + (dic.get("Status/MaxHP/Title") or "")
    if name == "Cost":                  # action resource (Vigor/Rage/Fuel) — from the owner unit, or drop
        return cost_label
    return dic.get(f"Status/{name}/Title_HPChangeFunctionArg") or dic.get(f"Status/{name}/Title") or name


# The game appends these four to the stat list AFTER the `pairs(statusList)` normal stats, in this
# fixed order (shared_tooltip.lua) — none is a `Status` class, so each enters via a hand-coded insert.
_DAMAGE_SPECIALS = ("SP", "HP", "EnemyHP", "Cost")


def resolve_damage_amount(dic, owner, cost_label=None):
    """The game's $DamageAmount$ per-hit damage formula (shared_tooltip.lua GetDamageAmountText):
    the base number (`ability.ApplyAmount` = `ApplyAmountChangeStep[Lv]`, the Lv-1 value; omitted
    when 0) followed by each `AdditionalApplyAmount` stat as `(+<pct>% <stat>)` — e.g. Surge of
    Blades → "100 (+75% Attack Power)(+25% Speed)". The first term is bare when there's no base.
    Rendered unit-agnostically like the game's own no-target tooltip: SP is the generic "SP" (no
    element), and the unit-dependent `Cost` term takes the owner's resource title (`cost_label`)
    when known, else is dropped (as the no-target tooltip does).

    Stat order mirrors the game: the normal stats first, then the specials (`_DAMAGE_SPECIALS`) in
    their fixed order. We keep the normals in XML authoring order (Lua's `pairs` hash order over
    statusList isn't reproducible, but authoring order matches the in-game tooltips checked)."""
    step = _fmt_num(owner.get("ApplyAmountChangeStep") or 0)
    result = step if step not in ("0", "") else ""     # base number; ApplyAmount==0 shows no base
    aa = owner.find("AdditionalApplyAmount")
    props = [p for p in (aa.findall("property") if aa is not None else ())
             if p.get("value") and _fmt_num(p.get("value")) != "0"]
    normals = [p for p in props if p.get("name") not in _DAMAGE_SPECIALS]
    specials = sorted((p for p in props if p.get("name") in _DAMAGE_SPECIALS),
                      key=lambda p: _DAMAGE_SPECIALS.index(p.get("name")))
    additional = ""
    for p in normals + specials:
        title = _damage_stat_title(dic, p.get("name"), cost_label)
        if not title:                   # unit-dependent term with no resolvable label → drop it
            continue
        seg = f"{_fmt_num(p.get('value'))}% {title}"
        additional += f"(+{seg})" if (result or additional) else seg
    return result + " " + additional if (result and additional) else result + additional


# The SP-gauge stats have no static Title: in-game the name is the unit's "<element> SP",
# resolved at runtime from its ESP (Status/Max<ESP>Point/Title — e.g. 화염 SP / 발열 SP). In this
# unit-agnostic library no single element applies, so we render the generic "SP" (the common
# suffix of every variant; same string in English and Korean). See DATAMINING.md. Without this the raw
# key "MaxSP" leaked into ~48 mastery descriptions (Sincerity, Crammy, …).
SP_GAUGE_STATS = {"MaxSP", "MaxAddSP"}


def _title(dic, idspace, key):
    """Resolve a <idspace>/<key>/Title-style name, with the Mastery special case."""
    if key in (None, "None", ""):
        return None
    if idspace == "Mastery":
        hit = dic.get(f"Mastery/{key}/Base_Title") or dic.get(f"Mastery/{key}/Title")
        if not hit:
            note_unresolved("title", idspace, key)
        return hit or key
    fallback = "SP" if (idspace == "Status" and key in SP_GAUGE_STATS) else key
    # Title, then Base_Title (Item names — amulets, potions — live under Base_Title, not Title, so
    # they leaked as raw ids; same authored-name fallback the Mastery branch above already uses).
    hit = dic.get(f"{idspace}/{key}/Title") or dic.get(f"{idspace}/{key}/Base_Title")
    if not hit and fallback == key:          # genuine miss (not the SP-gauge special case)
        note_unresolved("title", idspace, key)
    return hit or fallback


# PerformanceType id -> the ordered PerformanceEffect ids it grants (from Performance.xml `<Effect>`).
# Populated by the extractor; resolves `$MasteryPerformanceEffectList$` (the Clown's Amazing Trick →
# "Amazing Acrobatic Move, Surprising Acrobatic Move, …") which the game builds from the mastery's
# `PerformanceType`, not a per-mastery attribute.
PERFORMANCE_EFFECTS = {}


def resolve_one_keyword(dic, owner, line_no, pos, prop, idprefix="Mastery"):
    """Return the display string for a single <FormatKeyword> property. `owner` is the source
    <class> (a Mastery, or an Ability when idprefix="Ability"); `idprefix` is the dictionary
    idspace its Desc_Base text lives under. Buff/mastery/ability refs come back as inline markup."""
    value = prop.get("Value") or ""
    idspace = prop.get("Idspace")
    mid = owner.get("name")

    if value == "$MasteryPerformanceEffectList$":     # the effects a mastery's PerformanceType grants
        eff = PERFORMANCE_EFFECTS.get(owner.get("PerformanceType") or "", ())
        return ", ".join(t for t in (_title(dic, "PerformanceEffect", e) for e in eff) if t)

    m = APPLY_RE.match(value)
    if m:
        n = m.group(1)  # "", "2", "3", ...
        raw = owner.get("Base_ApplyAmount" + n)
        out = _fmt_num(raw)
        if owner.get("ApplyAmountType" + n) == "Percent":
            out += "%"
        return out

    if value in MASTERY_REF:
        attr, refspace = MASTERY_REF[value]
        ref = owner.get(attr)
        if ref in (None, "None"):
            return ""
        title = _title(dic, refspace, ref)
        rkind = _REF_KIND.get(refspace)
        if rkind and title:                                 # positioned chip; else plain (FieldEffect…)
            return _ref(rkind, title)
        return title

    if idspace not in (None, "None"):
        key = prop.get("Key")
        rkind = _REF_KIND.get(idspace)
        # a comma-separated list of ids resolves each under the idspace and rejoins (e.g. the
        # FieldEffect names a mastery is immune to) — looking the whole list up as one key misses.
        is_list = prop.get("ValueType") == "table" and key and "," in key
        keys = [k.strip() for k in key.split(",")] if is_list else [key]
        parts = []
        for k in keys:
            title = _title(dic, idspace, k)
            if not is_list and not title:                   # single-key literal fallback
                title = value
            if rkind and title and k not in (None, "None", ""):
                parts.append(_ref(rkind, title))
            elif title is not None:
                parts.append(title)
        return ", ".join(parts)

    # a bare $Token$ Value (no Idspace) on an ability/buff recipe — resolve from owner attributes
    # ($AddBuffName$ → the buff's AddBuff title, etc.) before the literal dictionary fallback
    if idprefix != "Mastery" and value.startswith("$") and value.endswith("$"):
        ov = resolve_owner_token(dic, owner, value[1:-1])
        if ov is not None:
            return ov

    # literal: translated value lives in the dictionary, indexed by absolute position
    return dic.get(f"{idprefix}/{mid}/Desc_Base/{line_no}/FormatKeyword/{pos}/Value", value)


# Fallback stat-bonus wording, used only when a stat has a Title but no game Desc template
# (see stat_summary). `{title}` carries a josa marker so apply_josa picks 을/를. Add a language
# here when localizing; missing languages fall back to English.
STAT_TMPL = {
    "eng": ("Increases {title} by {num}", "Decreases {title} by {num}"),
    "kor": ("{title}[을] {num} 증가", "{title}[을] {num} 감소"),
}


def stat_summary(dic, mastery, status_fmt=None):
    """Build a description from a mastery's flat Base_<Stat> bonuses (the game tooltip's
    $StatusMessage$ — a mastery's "Extra Effect" section, or its whole description when it has no
    authored text, e.g. the Elite/Epic/Legend promotion buffs).

    Prefer the game's own per-stat template `Status/<stat>/Desc_Increase|Desc_Decrease`
    (e.g. "Increases your $Status$ by $Value$" / Korean "$Status$[이] $Value$ 증가합니다.") —
    it carries the right wording per stat: Max-stats say "Maximum …", and boolean/immunity
    stats are self-contained sentences ("Immune to mental debuff." with no $Value$). Only fall
    back to STAT_TMPL for the rare stat with a Title but no Desc template. `status_fmt` (Status id
    → Format) supplies the trailing "%" for Percent stats (Block, Resistance…) — the template omits
    it, the engine appends it from the Format (same as `_stat_delta_line` for buffs)."""
    status_fmt = status_fmt or {}
    inc, dec = STAT_TMPL.get(getattr(dic, "lang", "eng"), STAT_TMPL["eng"])
    parts = []
    for k, v in mastery.attrib.items():
        if not k.startswith("Base_") or k == "Base_Title":
            continue
        stat = k[5:]
        # SP_GAUGE_STATS (MaxAddSP, MaxSP) have no Title but do have a Desc_Increase/Decrease
        # template ("Increases your Maximum $Status$ by $Value$") — give them the generic "SP"
        # so e.g. LimitBreak (Base_MaxAddSP=50) renders the in-game "Increases your Maximum SP by 50"
        title = dic.get(f"Status/{stat}/Title") or ("SP" if stat in SP_GAUGE_STATS else None)
        if not title:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f == 0:
            continue
        num = str(int(f)) if f == int(f) else ("%g" % f)
        num = num.lstrip("-")                       # Desc_Decrease already carries the "decreases" sense
        if status_fmt.get(stat) == "Percent":       # Block/Resistance/… — template omits the "%"
            num += "%"
        tmpl = dic.get(f"Status/{stat}/{'Desc_Increase' if f > 0 else 'Desc_Decrease'}")
        if tmpl:
            line = PLACE_RE.sub(lambda m: {"Status": title, "Value": num}.get(m.group(1), m.group(0)), tmpl)
        else:
            line = (inc if f > 0 else dec).format(title=title, num=num)
        parts.append(apply_josa(line))
    return "\n".join(parts)


def immune_debuff_summary(dic, mastery):
    """For masteries with `ImmuneDebuff_BuffGroup="true"` but no authored text — they grant
    immunity to the debuff groups listed in `BuffGroup`/`SubBuffGroup`. The game renders this from
    a GuideMessage template (one group, or a two-group variant); no mastery lists more than two."""
    if mastery.get("ImmuneDebuff_BuffGroup") != "true":
        return ""
    titles = [_title(dic, "BuffGroup", mastery.get(a)) for a in ("BuffGroup", "SubBuffGroup")
              if mastery.get(a) and mastery.get(a) != "None"]
    if not titles:
        return ""
    if len(titles) >= 2:
        return format_message(dic, "Mastery_ImmuneDebuff_BuffGroup2",
                              {"MasteryBuffGroup": titles[0], "MasterySubBuffGroup": titles[1]})
    return format_message(dic, "Mastery_ImmuneDebuff_BuffGroup", {"MasteryBuffGroup": titles[0]})


def neutralize_field_summary(dic, mastery):
    """For masteries whose `NeutralizeFieldEffect` lists terrain field effects they ignore
    (Hovering/Flight → Swamp, Water, Ice, Lava…). One line joining each `FieldEffect/<id>/Title`
    into the `NeutralizeFieldEffect` GuideMessage ($FieldEffectList$), like the game tooltip."""
    raw = mastery.get("NeutralizeFieldEffect")
    if not raw or raw == "None":
        return ""
    names = [n for fx in raw.split(",") if fx.strip()
             and (n := _title(dic, "FieldEffect", fx.strip())) is not None]
    if not names:
        return ""
    return format_message(dic, "NeutralizeFieldEffect", {"FieldEffectList": ", ".join(names)})


# A Desc_Base <property>'s "case" prefix — the game renders it before the line's Text
# (shared_tooltip.lua `MakeMasteryDescBaseOneline`): a section header (`Custom`), a condition list
# (`FieldEffect`/`MissionWeather`/`MissionTemperature`…), or a referenced `Mastery`/`Ability`/`Buff`.
# `Custom` values are localized literals (dictionary `Desc_Base/<n>/CaseValue`); every other CaseType
# names an idspace whose ids resolve to Titles (a comma-list when `CaseValueType="table"`). Mastery/
# Ability/Buff refs come back as inline sentinel markup so the web tool chips them — which is why the
# ability-reinforcement masteries (e.g. *A Million Years of Training*) need no special
# `shared_ability.lua` parsing: the ability id is right here in `CaseValue`.


def _case_title(dic, prop, idprefix, mid, n):
    """The resolved case-prefix string for one Desc_Base property ("" if CaseType is None)."""
    ct = prop.get("CaseType")
    if ct in (None, "None"):
        return ""
    if ct in ("Custom", "CustomText"):          # localized literal header, raw attr as last resort
        return dic.get(f"{idprefix}/{mid}/Desc_Base/{n}/CaseValue") or prop.get("CaseValue") or ""
    cv = prop.get("CaseValue") or ""
    keys = cv.split(",") if prop.get("CaseValueType") == "table" else [cv]
    kind = _REF_KIND.get(ct)
    parts = []
    for key in keys:
        key = key.strip()
        if not key:
            continue
        title = _title(dic, ct, key)
        if kind and title:
            parts.append(_ref(kind, title))
        elif title:
            parts.append(title)
    return ", ".join(parts)


def resolve_description(dic, owner, idprefix="Mastery", cost_label=None):
    """owner: a Mastery.xml <class> Element (default), or an Ability.xml <class> when
    idprefix="Ability" — both share the same Desc_Base/FormatKeyword shape. Returns resolved
    multi-line text (with inline sentinel ref markup). `idprefix` is the dictionary idspace its
    Desc_Base lines live under. `cost_label` (abilities only) is the owner unit's action-resource
    title (Vigor/Rage/Fuel), used for the `Cost` term of a $DamageAmount$ formula."""
    mid = owner.get("name")
    db = owner.find("Desc_Base")
    if db is None:
        return ""
    props = db.findall("property")

    # Per-line resolved keyword maps. Most owners repeat the full recipe on
    # every line, but a few define it once and reuse it across lines, so we also
    # keep an owner-wide pool as a fallback (line-local always takes precedence).
    per_line = []
    pool = {}
    for n, prop in enumerate(props, 1):
        resolved = {}
        fk = prop.find("FormatKeyword")
        if fk is not None:
            for pos, p in enumerate(fk.findall("property"), 1):
                resolved[p.get("FormatKey")] = resolve_one_keyword(dic, owner, n, pos, p, idprefix)
        per_line.append(resolved)
        for k, v in resolved.items():
            pool.setdefault(k, v)

    indent = indent_block   # block-indent marker per line (web indents the whole wrapping block)

    lines = []
    in_section = False          # inside a bare section header's body → indent its plain effect lines
    for n, (prop, resolved) in enumerate(zip(props, per_line), 1):
        text = dic.get(f"{idprefix}/{mid}/Desc_Base/{n}/Text")

        def sub(mt):
            tok = mt.group(1)
            if tok in resolved:
                return resolved[tok]
            if tok in pool:
                return pool[tok]
            if idprefix != "Mastery":          # ability-text owner tokens ($ApplyBuff$, colours…)
                ov = resolve_owner_token(dic, owner, tok, cost_label)
                if ov is not None:
                    return ov
            g = resolve_global(dic, tok)
            if g is not None:
                return g
            note_unresolved("token", idprefix, tok)   # $token$ left literal in the resolved text
            return mt.group(0)

        # collapse runs of spaces the game's rich-text renderer would (source-template double spaces
        # e.g. "For every 1  $Type$", and gaps left by runtime tokens that resolve empty — $Grade$,
        # $MasteryPerformanceEffectList$…). Done per line, before the block-indent marker is added.
        main = _MULTISPACE_RE.sub(" ", PLACE_RE.sub(sub, text)) if text else ""
        case = _case_title(dic, prop, idprefix, mid, n)
        if case:                        # a Custom caseTitle can carry $tokens$ (its own FormatKeyword)
            case = _MULTISPACE_RE.sub(" ", PLACE_RE.sub(sub, case))
        lb = str(prop.get("LineBreak")).lower() == "true"
        # game layout: caseTitle then Text. When the caseTitle is on its own line
        # (CaseLineBreak="true") the Text belongs "within" that case, so mark it for block-indent
        # readability; otherwise it's an inline "label: text" line. A caseTitle with **no** Text is a
        # bare section header — its effects come on the following standalone lines (e.g. "Reinforced
        # Machine" then three effect lines), so those get indented via `in_section`. A Text with no
        # case is a plain line — indented iff it's inside such a section.
        if case and main:
            line = case + "\n" + indent(main) if str(prop.get("CaseLineBreak")).lower() == "true" \
                else case + ": " + main
            in_section = False              # self-contained caseTitle (carries its own effect)
        elif case:
            line = case
            in_section = True               # bare header — following plain lines belong to it
        elif main:
            line = indent(main) if in_section else main
            if lb:                          # a paragraph break ends the section's body
                in_section = False
        else:
            continue
        # LineBreak="true" ends a paragraph — the game appends an extra newline after the line
        # (GetMasteryMasteryDescBaseText joins with "\n"), so it renders as a blank-line separator
        # above the next section header (Field/Weather/Temperature, etc.).
        if lb:
            line += "\n"
        lines.append(line)
    # apply Korean josa (no-op for English, and for mastery/ability text which carries no markers —
    # only buff Desc_Base uses them); idempotent once markers are resolved
    return apply_josa("\n".join(lines).rstrip("\n"))
