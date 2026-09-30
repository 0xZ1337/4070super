#!/usr/bin/env bash
# Télécharge le miner (WildRig 0.51.3 par défaut). Ex : ./install.sh  |  ./install.sh --miner bzminer
set -euo pipefail
cd "$(dirname "$0")"
exec python3 prl.py install "$@"
