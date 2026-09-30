#!/usr/bin/env bash
# Lance le minage PRL+NOCK avec le profil de config/miner.env (ou --profile eco|balanced|max|tuned).
# L'OC (verrou core, power limit) demande root : sudo ./mine.sh
set -euo pipefail
cd "$(dirname "$0")"
[ -f config/miner.env ] || { echo "Copie config/miner.env.example vers config/miner.env et remplis tes wallets."; exit 1; }
python3 prl.py check || true
exec python3 prl.py run "$@"
