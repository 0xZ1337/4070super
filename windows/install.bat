@echo off
REM Telecharge le miner (WildRig 0.51.3 par defaut). Ex : install.bat  |  install.bat --miner bzminer
cd /d "%~dp0.."
where py >nul 2>nul && (py -3 prl.py install %* & goto :end)
python prl.py install %*
:end
pause
