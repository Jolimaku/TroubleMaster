#!/usr/bin/env bash
# Unpack the game's encrypted text data into ./Unpack using PLDataPacker (TSAC Modding Tool),
# driven by this repo's package_list.xml. Run this to pull a fresh snapshot after a game patch,
# THEN run ./regen.sh to rebuild the web data from it. (regen.sh does NOT unpack.)
#
# ---- First-time setup (only needed once) -------------------------------------------
#   Install "TSAC Modding Tool" from Steam (Library -> Tools). It ships PLDataPacker.exe.
# ------------------------------------------------------------------------------------
#
# Override any of these for your machine (env var or edit the default here):
GAME="${GAME:-E:/SteamLibrary/steamapps/common/Troubleshooter}"      # game install (has Package/ + Dictionary/)
TOOL="${TOOL:-E:/SteamLibrary/steamapps/common/TSAC Modding Tool}"   # dir holding PLDataPacker.exe

set -euo pipefail
cd "$(dirname "$0")"
REPO="$PWD"
LIST="${LIST:-$REPO/package_list.xml}"   # what to unpack (this repo's list: xml + script + stage)
OUT="${OUT:-$REPO/Unpack}"               # unpacked data lands in $OUT/Data

if [ ! -f "$TOOL/PLDataPacker.exe" ]; then
  echo "ERROR: PLDataPacker.exe not found in '$TOOL' -- install the TSAC Modding Tool from Steam, or set TOOL=." >&2
  exit 1
fi

echo ">> PLDataPacker unpack ($LIST -> $OUT/Data)"
# Run from the tool dir so it finds its sibling DLLs and keeps its logs/caches there; point --source_root
# at our Unpack and --package_list_path at our list. --target_root is the game (the packed source).
( cd "$TOOL" && ./PLDataPacker.exe --full --mode unpack \
    --package_list_path "$LIST" --target_root "$GAME" --source_root "$OUT" )

echo "Done. Unpacked into $OUT/Data (xml/, script/, stage/). Now run ./regen.sh to rebuild the web data."
