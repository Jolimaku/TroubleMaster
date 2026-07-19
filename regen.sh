#!/usr/bin/env bash
# Regenerate everything the web tool serves: web/data.js + web/data.<lang>.js,
# web/codemap.js (share-code tables), the output/ dumps, and the per-language pages.
# Run this after unpacking new game data, or after changing the extractor.
#
# ---- First-time setup (only needed once, before this script can run) --------------
#   1. Install "TSAC Modding Tool" from Steam (Library -> Tools). It ships PLDataPacker.exe.
#   2. Unpack the game's text data into ./Unpack by running ./unpack.sh (see README
#      "Reproducing"). You need Unpack/Data/xml/*.xml, plus Unpack/Data/stage/*.stage
#      for the enemy->mission data.
# -----------------------------------------------------------------------------------
#
# Override any of these for your machine (env var or edit the default here):
GAME="${GAME:-E:/SteamLibrary/steamapps/common/Troubleshooter}"   # dir containing Dictionary/
DATA="${DATA:-Unpack/Data}"                                       # unpacked game data (has xml/, stage/)
LANGS="${LANGS:-eng kor}"                                         # dictionary folders to build (eng stays web/data.js)
PYTHON="${PYTHON:-python}"

set -euo pipefail
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8      # keep Korean console output from choking on cp1252

if [ ! -d "$DATA/xml" ]; then
  echo "ERROR: '$DATA/xml' not found -- unpack the game data first (see README 'Reproducing')." >&2
  exit 1
fi

for lang in $LANGS; do
  # English is the canonical dump in output/; other langs go to output-<lang>/ (see .gitignore)
  if [ "$lang" = eng ]; then out="output"; else out="output-$lang"; fi
  echo ">> extract_masteries.py --lang $lang --out $out"
  "$PYTHON" extract_masteries.py --game "$GAME" --data "$DATA" --lang "$lang" --out "$out"
  echo ">> extract_items.py --lang $lang --out $out"
  "$PYTHON" extract_items.py --game "$GAME" --data "$DATA" --lang "$lang" --out "$out"
done

echo ">> gen_pages.py (stamp each <lang>/index.html from web/index.html)"
"$PYTHON" gen_pages.py

echo "Done. Rebuilt web/data*.js, web/codemap.js, output*/ and the language pages."
