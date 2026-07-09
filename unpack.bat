@echo off
REM Unpack the game's encrypted text data into .\Unpack using PLDataPacker (TSAC Modding Tool),
REM driven by this repo's package_list.xml. Run this to pull a fresh snapshot after a game patch,
REM THEN run regen.bat to rebuild the web data from it. (regen does NOT unpack.)
REM
REM ---- First-time setup (only needed once) -------------------------------------------
REM   Install "TSAC Modding Tool" from Steam (Library -^> Tools). It ships PLDataPacker.exe.
REM ------------------------------------------------------------------------------------
REM
REM Override any of these for your machine (set before calling, or edit the default here):
setlocal
if "%GAME%"=="" set "GAME=E:\SteamLibrary\steamapps\common\Troubleshooter"
if "%TOOL%"=="" set "TOOL=E:\SteamLibrary\steamapps\common\TSAC Modding Tool"

cd /d "%~dp0"
set "REPO=%CD%"
if "%LIST%"=="" set "LIST=%REPO%\package_list.xml"
if "%OUT%"==""  set "OUT=%REPO%\Unpack"

if not exist "%TOOL%\PLDataPacker.exe" (
  echo ERROR: PLDataPacker.exe not found in "%TOOL%" -- install the TSAC Modding Tool from Steam, or set TOOL.
  exit /b 1
)

echo ^>^> PLDataPacker unpack ^("%LIST%" -^> "%OUT%\Data"^)
REM Run from the tool dir so it finds its sibling DLLs and keeps its logs/caches there.
pushd "%TOOL%"
PLDataPacker.exe --full --mode unpack --package_list_path "%LIST%" --target_root "%GAME%" --source_root "%OUT%"
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" ( echo Unpack FAILED ^(exit %RC%^). & exit /b %RC% )

echo Done. Unpacked into "%OUT%\Data" ^(xml\, script\, stage\^). Now run regen.bat to rebuild the web data.
endlocal
