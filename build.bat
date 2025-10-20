@echo off
CALL "%userprofile%\miniforge3\Scripts\activate.bat"
CALL conda activate er_soundbank_helper

REM "=== RUNNING PYINSTALLER ==="
IF EXIST dist RMDIR /S /Q dist
pyinstaller main.py --onefile

REM "=== COPYING ADDITIONAL FILES ==="
REN dist\main.exe ERSoundbankHelper.exe
COPY LICENSE dist\
COPY README.md dist\
