@echo off
REM Regenerate everything the web tool serves: web\data.js + web\data.<lang>.js,
REM web\codemap.js (share-code tables), the output\ dumps, and the per-language pages.
REM Run this after unpacking new game data, or after changing the extractor.
REM
REM ---- First-time setup (only needed once, before this script can run) --------------
REM   1. Install "TSAC Modding Tool" from Steam (Library -^> Tools). It ships PLDataPacker.exe.
REM   2. Unpack the game's text data into .\Unpack by running unpack.bat (see README
REM      "Reproducing"). You need Unpack\Data\xml\*.xml, plus Unpack\Data\stage\*.stage
REM      for the enemy-^>mission data.
REM -----------------------------------------------------------------------------------
REM
REM Override any of these for your machine (set before calling, or edit the default here):
setlocal enabledelayedexpansion
if "%GAME%"==""   set "GAME=E:\SteamLibrary\steamapps\common\Troubleshooter"
if "%DATA%"==""   set "DATA=Unpack\Data"
if "%LANGS%"==""  set "LANGS=eng kor"
if "%PYTHON%"=="" set "PYTHON=python"

cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"

if not exist "%DATA%\xml" (
  echo ERROR: "%DATA%\xml" not found -- unpack the game data first ^(see README "Reproducing"^).
  exit /b 1
)

for %%L in (%LANGS%) do (
  REM English is the canonical dump in output\; other langs go to output-<lang>\ (see .gitignore)
  if "%%L"=="eng" ( set "OUT=output" ) else ( set "OUT=output-%%L" )
  echo ^>^> extract_masteries.py --lang %%L --out !OUT!
  "%PYTHON%" extract_masteries.py --game "%GAME%" --data "%DATA%" --lang %%L --out "!OUT!" || exit /b 1
)

echo ^>^> gen_pages.py ^(stamp each ^<lang^>\index.html from web\index.html^)
"%PYTHON%" gen_pages.py || exit /b 1

echo Done. Rebuilt web\data*.js, web\codemap.js, output*\ and the language pages.
endlocal
