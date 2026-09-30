# HiveOS — flight sheet PRL+NOCK (WildRig, RTX 4070 SUPER)

Sur HiveOS on n'utilise pas `prl.py` : on décrit le même réglage dans une flight sheet **Custom**.

## 1. Miner
- **Miner** : Custom
- **Miner name** : `wildrig`
- **Installation URL** :
  `https://github.com/andru-kun/wildrig-multi/releases/download/0.51.3/wildrig-multi-hiveos-0.51.3.tar.gz`
- **Hash algorithm** : `pearlhash`
- **Wallet and worker template** : `%WAL%.%WORKER_NAME%`
  Dans le wallet HiveOS mets **`PRL_ADRESSE+NOCK_ADRESSE`** (le `+` fait le merge mining ; le template ajoute `.worker`).
- **Pool URL** : `pearl-eu1.luckypool.io:3360` (ou `fr.pearl.herominers.com:1200`)
- **Pass** : `x`
- **Extra config arguments** (profil *balanced*) :
  ```
  --pearlhash-kernel 2 --gpu-temp-limit 80 --gpu-temp-resume 65
  ```
  Ne mets pas `--api-port` ni `--log-file` : le paquet HiveOS les gère.

## 2. Overclock (onglet OC de la carte, pas dans le miner)
| Profil | Core (lock) | Mem offset | Power limit | Fan |
|---|---|---|---|---|
| eco | 1905 | -500 | 165 W | 65 % |
| balanced | 2100 | -500 | 185 W | 70 % |
| max | 2475 | 0 | 220 W | 80 % |

Dans HiveOS, une valeur de **Core clock ≥ 500** est un *verrou* (lock), c'est ce qu'on veut sur pearlhash
(charge tensor cores très stable, un lock évite les oscillations de boost et fait gagner des watts).
Le kernel `--pearlhash-kernel 2` est celui que le changelog WildRig annonce comme plus économe sur 4060 / 4070 Super / 4070 Ti ;
compare avec et sans sur 15 min de hashrate **côté pool**.

## 3. Vérifier
- Onglet *Miner* : la ligne `speed 10s/60s/15m … TH/s` doit tourner autour de 105–120 TH/s en *balanced*.
- Sur LuckyPool : `pearl.luckypool.io` avec ton adresse PRL, et `nock.luckypool.io` avec ton adresse NOCK pour la partie merge.
