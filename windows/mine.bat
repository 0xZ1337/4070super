@echo off
REM Lance le minage PRL+NOCK. Clic droit > "Executer en tant qu'administrateur" pour que l'OC s'applique.
cd /d "%~dp0.."
if not exist config\miner.env (
  echo Copie config\miner.env.example vers config\miner.env et remplis tes wallets.
  pause & exit /b 1
)
where py >nul 2>nul && (py -3 prl.py check & py -3 prl.py run %* & goto :end)
python prl.py check
python prl.py run %*
:end
pause
