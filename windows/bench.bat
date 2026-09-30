@echo off
REM Auto-tuning (admin requis) : balaye verrou core x kernel et ecrit le profil "tuned".
cd /d "%~dp0.."
where py >nul 2>nul && (py -3 prl.py bench %* & goto :end)
python prl.py bench %*
:end
pause
