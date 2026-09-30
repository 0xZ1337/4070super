# Kit PRL + NOCK pour RTX 4070 SUPER

Reproduit le setup de la vidéo *« MINING ULTRA PROFITABLE EN MERGE PEARL+NOCK »* (Pearl `pearlhash` + Nockchain en merge mining avec WildRig), mais **spécialisé pour une seule RTX 4070 SUPER** et piloté par un script open source :

- installation automatique de **WildRig-Multi 0.51.3** (0 % de dev fee sur pearlhash, contre 2 % chez SRBMiner / BzMiner / PeakMiner) ;
- **login merge mining** PRL+NOCK construit et vérifié pour toi (format `PRL+NOCK.worker`) ;
- **3 profils d'overclock** pensés pour la 4070 SUPER (eco / balanced / max) appliqués par le miner lui-même ;
- **auto-tuning** : `bench` balaye les verrous de fréquence core et les kernels pearlhash, mesure TH/s **et** watts réels, et garde le meilleur TH/s par watt ;
- watchdog + redémarrage automatique, logs, API de stats ;
- Linux, Windows, et une flight sheet **HiveOS**.

> Pourquoi pas un « nouveau miner » ? Les kernels pearlhash de WildRig sont fermés et déjà les plus efficaces sur Ada (0 % de fee, kernel 2 optimisé 4070 Super/Ti dans le changelog 0.51.x). Réécrire un kernel tensor-core équivalent n'apporterait rien ; l'efficacité de la vidéo vient du miner + des bons réglages + du merge. Ce kit automatise exactement ça et garde BzMiner / SRBMiner comme roues de secours.

---

## 1. Démarrage rapide

Prérequis : Python 3.8+, driver NVIDIA ≥ 452 (Windows) / 450 (Linux), une adresse **Pearl** (`prl1…`) et une adresse **Nockchain native** (base58, pas une adresse `0x…` de wNOCK).

### Windows
1. Installe Python depuis python.org (coche *Add to PATH*).
2. Copie `config\miner.env.example` → `config\miner.env`, remplis `PRL_WALLET`, `NOCK_WALLET`, `WORKER`.
3. Double-clique `windows\install.bat` (télécharge WildRig 0.51.3).
4. Clic droit sur `windows\mine.bat` → **Exécuter en tant qu'administrateur** (sans admin, le verrou core et le power limit ne s'appliquent pas).

### Linux
```bash
cp config/miner.env.example config/miner.env   # puis édite tes adresses
./install.sh
sudo ./mine.sh                                  # root requis pour l'OC via NVML
```

### Vérifier avant de lancer
```bash
python3 prl.py check      # adresses, pool, profil, GPU, driver, droits admin
python3 prl.py pools      # pools connus + login exact qui sera envoyé
python3 prl.py run --dry-run   # affiche la ligne de commande sans miner
python3 prl.py stats      # hashrate (API) + watts/température (nvidia-smi)
```

---

## 2. Configuration (`config/miner.env`)

| Clé | Rôle |
|---|---|
| `PRL_WALLET` | adresse Pearl, commence par `prl1` |
| `NOCK_WALLET` | adresse Nockchain **native** (base58). Vide = PRL seul |
| `WORKER` | nom du rig sur le pool |
| `POOL` | `luckypool-eu1` (défaut), `luckypool-eu2`, `herominers-fr`, `herominers-de`, `kryptex-eu`, `custom` |
| `USERNAME_TEMPLATE` | `{prl}+{nock}.{worker}` : le `+` déclenche le merge mining côté pool |
| `MINER` | `wildrig` (recommandé), `bzminer`, `srbminer` |
| `PROFILE` | `eco`, `balanced`, `max`, `stock`, `tuned` (créé par `bench`) |
| `GPU_ID` | index de la carte (0 si une seule) |
| `KWH_PRICE`, `REVENUE_PER_TH_DAY` | facultatif, pour le classement « net €/jour » du bench |

### Merge mining : ce qui compte
- **LuckyPool** documente le format `PRL_ADRESSE+NOCK_ADRESSE.WORKER` et paie le NOCK sur Nockchain. Tes stats : `pearl.luckypool.io` (adresse PRL) et `nock.luckypool.io` (adresse NOCK).
- Le miner doit **supporter le merge** : WildRig oui (c'est lui dans la vidéo). PeakMiner refuse le double wallet (issue GitHub #16). BzMiner/SRBMiner acceptent le login mais vérifie que du NOCK arrive bien.
- HeroMiners documente le merge PRL+MDL avec le même `+` ; pour NOCK, vérifie leur page « how to mine ». Kryptex : pas de merge NOCK connu et WildRig y a des soucis (issue #356).

---

## 3. Profils d'overclock 4070 SUPER (`config/profiles.json`)

pearlhash = multiplications de matrices sur tensor cores : **borné par le core, pas par la mémoire**. La recette : verrouiller le core (pas un simple offset), baisser la mémoire pour gratter des watts, et plafonner la puissance.

| Profil | Core (lock) | Mem offset | PL | Fan | Kernel | Attendu* |
|---|---|---|---|---|---|---|
| `eco` | 1905 MHz | -500 | 165 W | 65 % | 2 | ~100–108 TH/s @ ~160 W (≈0,66 TH/s/W) |
| `balanced` | 2100 MHz | -500 | 185 W | 70 % | 2 | ~115–120 TH/s @ ~180 W |
| `max` | 2475 MHz | 0 | 220 W | 80 % | 0 | ~128–135 TH/s @ ~210 W |
| `stock` | — | — | — | — | 0 | référence constructeur |

\* Ordres de grandeur relevés publiquement pour la 4070 SUPER (108 TH/s @ 162 W sous WildRig, 132 TH/s @ 201 W sous SRBMiner, 136 @ 220 W sous Golden Miner). Ta carte, ton boîtier et ta version de driver bougent ces chiffres : c'est pour ça qu'il y a `bench`.

`--pearlhash-kernel 2` est le kernel que le changelog WildRig décrit comme plus économe sur 4060 / 4070 Super / 4070 Ti. Il perd parfois 2–3 % de hashrate brut pour 8–10 % de watts : c'est le bon choix si tu cherches l'**efficacité** comme dans la vidéo.

---

## 4. Auto-tuning (`bench`)

```bash
sudo ./bench.sh                                   # grille par défaut : core 1800→2400 × kernel 0/2, ≈ 25 min
sudo ./bench.sh --clocks 1950,2100,2250 --kernels 2 --window 120
sudo ./bench.sh --objective max                   # hashrate brut plutôt que TH/s par watt
sudo ./bench.sh --objective net                   # €/jour après électricité (KWH_PRICE + REVENUE_PER_TH_DAY)
sudo ./bench.sh --online                          # sur le vrai pool au lieu du mode --benchmark hors ligne
```
Pour chaque point le script lance WildRig en mode `--benchmark-timeout` (aucune connexion, aucune dev fee), ignore 45 s de chauffe, puis prend la **médiane du hashrate** sur 90 s et la **moyenne des watts** lus par `nvidia-smi`. Résultat : un CSV dans `results/` et un profil `tuned` dans `config/profiles.tuned.json`. Mets `PROFILE=tuned` et relance `mine.sh`.

Windows : `windows\bench.bat` en administrateur.

---

## 5. Miners comparés sur pearlhash (état sept. 2026)

| Miner | Dev fee pearlhash | Merge NOCK | 4070 SUPER (relevés publics) | Remarque |
|---|---|---|---|---|
| **WildRig-Multi 0.51.3** | **0 %** | oui | 108 TH/s @ 162 W | celui de la vidéo ; kernel 2 optimisé Ada ; 0.51.3 = +10 % d'efficacité NVIDIA |
| SRBMiner-Multi | 2 % | à vérifier | 132 TH/s @ 201 W | `--pearl-k2` = kernel 2 ; pas de bench hors ligne |
| BzMiner | 2 % | à vérifier | n/d | OC via `--oc-*`, peut aussi servir un LLM en parallèle |
| PeakMiner | 2 % | **non** (issue #16) | n/d | |
| tw-pearl-miner | 1,5 % | non documenté | 4070 : 114 TH/s @ 200 W | binaire fermé, driver ≥ 570 |
| Golden Miner | n/d | oui (pool propriétaire) | 136 « MH/s » @ 220 W | unités propres, ferme sur leur pool |

À 0 % de fee et avec le kernel 2, WildRig est celui qui rapporte le plus **net** sur cette carte ; le kit garde BzMiner et SRBMiner en `MINER=` de secours (`./install.sh --miner bzminer`).

---

## 6. Structure du dépôt

```
prl.py                 outil unique (check / install / pools / run / bench / stats), stdlib Python uniquement
install.sh mine.sh bench.sh      wrappers Linux
windows/*.bat          wrappers Windows (mine.bat et bench.bat en administrateur)
config/miner.env.example         ta config (à copier en miner.env, ignoré par git)
config/profiles.json   profils d'OC 4070 SUPER ; profiles.tuned.json est produit par bench
hiveos/FLIGHT_SHEET.md flight sheet HiveOS équivalente
tests/                 python3 -m unittest discover -s tests
```

---

## 7. Dépannage

| Symptôme | Piste |
|---|---|
| `speed … n/a TH/s` qui ne démarre jamais | mauvais pool pour WildRig (Kryptex) → passe sur LuckyPool/HeroMiners ; driver trop vieux ; essaie `--pearlhash-kernel 0` (`PROFILE=max`) |
| OC non appliqué (`clock lock failed` dans le log) | pas admin/root. Windows : lancer en administrateur ; Linux : `sudo` |
| Login rejeté | adresse NOCK `0x…` (wNOCK) au lieu d'une adresse Nockchain native ; ou le pool ne fait pas de merge → `NOCK_WALLET=` vide |
| Hashrate ok mais 0 NOCK sur `nock.luckypool.io` | attends 1–2 h (payouts NOCK plus lents) ; vérifie que le `+` est bien dans le login (`prl.py pools`) |
| La carte chauffe / throttle | `PROFILE=eco`, ou augmente `fan` dans `profiles.json` ; le miner met en pause à 80 °C et reprend à 65 °C |
| Redémarrages en boucle | lis `logs/wildrig-AAAAMMJJ.log` ; `run --max-restarts 5` pour ne pas tourner dans le vide |

---

## 8. Sources
- WildRig-Multi : https://github.com/andru-kun/wildrig-multi (README, releases 0.50.1 → 0.51.3, issues #351/#356)
- WildRig-NG (README des options, même syntaxe) : https://github.com/andru-kun/wildrig-ng
- LuckyPool Pearl + Nock : https://pearl.luckypool.io/how-to-mine — format `PRL+NOCK.WORKER`
- HeroMiners Pearl : https://pearl.herominers.com (port 1200, 0 %, PPS+)
- BzMiner (README pearl, `--oc-*`) : https://github.com/bzminer/bzminer
- SRBMiner-Multi (fichier `Parameters`) : https://github.com/doktor83/SRBMiner-Multi
- PeakMiner issue merge NOCK : https://github.com/peakminer/peakminer/issues/16
- Relevés 4070 SUPER pearlhash : hashrate.no/gpus/4070s/PRL, kryptex.com, README Golden Miner et tw-pearl-miner
- Vidéo d'origine : https://www.youtube.com/watch?v=3q_IbIMr2P4
