#!/usr/bin/env bash
# Auto-tuning : balaye verrou core × kernel, garde le meilleur TH/s par watt -> profil "tuned".
# Ex : sudo ./bench.sh                      (grille par défaut, ~25 min)
#      sudo ./bench.sh --clocks 1950,2100,2250 --kernels 2 --window 120
#      sudo ./bench.sh --objective net      (nécessite REVENUE_PER_TH_DAY et KWH_PRICE)
set -euo pipefail
cd "$(dirname "$0")"
exec python3 prl.py bench "$@"
