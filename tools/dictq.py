#!/usr/bin/env python
"""dictq — resolve game Dictionary keys to their localized strings.

The one game-data query yq/jq can't do: the dictionary lives in the game's custom
`Dictionary/*.dic` + `keymap.dkm` format, decoded only by `extract_masteries.Dictionary`.
This wraps that so a lookup is a plain, allowlistable command instead of an inline-python heredoc.

Usage:
    python tools/dictq.py Status/AttackPower/Title
    python tools/dictq.py CostType/Rage/Title --lang kor
    python tools/dictq.py Status/MaxHP/Title --lang both        # eng + kor, side by side
    python tools/dictq.py Status/AttackPower/Title Status/Speed/Title_HPChangeFunctionArg

--game defaults to $GAME or the usual install dir (same default as extract_masteries.py).
Prints `<lang>\t<key>\t<value>` (value empty when the key has no entry).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root, for the import
from extract_masteries import Dictionary   # noqa: E402  (path set up above)

DEFAULT_GAME = os.environ.get("GAME") or r"E:\SteamLibrary\steamapps\common\Troubleshooter"


def main():
    p = argparse.ArgumentParser(description="Resolve game Dictionary keys to localized strings.")
    p.add_argument("keys", nargs="+", help="dictionary keys, e.g. Status/AttackPower/Title")
    p.add_argument("--lang", default="eng", help="eng | kor | both (default eng)")
    p.add_argument("--game", default=DEFAULT_GAME, help="game install dir (has Dictionary/)")
    a = p.parse_args()

    langs = ("eng", "kor") if a.lang == "both" else (a.lang,)
    for lang in langs:
        dic = Dictionary(a.game, lang)
        for key in a.keys:
            val = dic.get(key)
            print(f"{lang}\t{key}\t{'' if val is None else val}")


if __name__ == "__main__":
    main()
