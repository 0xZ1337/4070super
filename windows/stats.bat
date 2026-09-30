@echo off
cd /d "%~dp0.."
where py >nul 2>nul && (py -3 prl.py stats & goto :end)
python prl.py stats
:end
pause
