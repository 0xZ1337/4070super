#!/usr/bin/env python3
"""
prl.py — kit de minage PRL + NOCK (merge mining, algo pearlhash) pour RTX 4070 SUPER.

Sous-commandes :
  check    vérifie l'environnement (config, adresses, GPU, driver, droits admin)
  install  télécharge le miner (WildRig 0.51.3 par défaut, BzMiner/SRBMiner en secours)
  pools    liste les pools connus et le login qui sera envoyé
  run      lance le miner avec le profil d'OC choisi + watchdog/redémarrage auto
  bench    balaye verrou core × kernel et choisit le meilleur TH/s par watt -> profil "tuned"
  stats    affiche hashrate (API du miner) + puissance/température (nvidia-smi)

Aucune dépendance hors bibliothèque standard. Fonctionne sur Linux et Windows (Python 3.8+).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
ENV_FILE = CONFIG_DIR / "miner.env"
PROFILES_FILE = CONFIG_DIR / "profiles.json"
TUNED_FILE = CONFIG_DIR / "profiles.tuned.json"
MINERS_DIR = ROOT / "miners"
LOGS_DIR = ROOT / "logs"
RESULTS_DIR = ROOT / "results"

IS_WINDOWS = platform.system() == "Windows"

WILDRIG_VERSION = "0.51.3"
WILDRIG_BASE = "https://github.com/andru-kun/wildrig-multi/releases/download/{v}/"
WILDRIG_ASSETS = {
    "Windows": "wildrig-multi-windows-{v}.zip",
    "Linux": "wildrig-multi-linux-{v}.tar.gz",
}

# Miners de secours : on résout la dernière release via l'API GitHub et on
# choisit l'asset qui correspond à l'OS.
FALLBACK_MINERS = {
    "bzminer": {
        "repo": "bzminer/bzminer",
        "asset": {"Windows": r"(?i)win.*\.zip$", "Linux": r"(?i)linux.*\.tar\.gz$"},
        "binary": {"Windows": "bzminer.exe", "Linux": "bzminer"},
        "fee": "2 %",
    },
    "srbminer": {
        "repo": "doktor83/SRBMiner-Multi",
        "asset": {"Windows": r"(?i)win.*\.zip$", "Linux": r"(?i)linux.*\.tar\.gz$"},
        "binary": {"Windows": "SRBMiner-MULTI.exe", "Linux": "SRBMiner-MULTI"},
        "fee": "2 %",
    },
}

# Pools connus. "merge": True = le pool documente le login PRL+NOCK.
POOLS = {
    "luckypool-eu1": {
        "url": "stratum+tcp://pearl-eu1.luckypool.io:3360",
        "merge": True,
        "note": "PROP, merge PRL+NOCK natif. Ports 3360 (diff 2M), 3361 (4M), 3362 (8M).",
    },
    "luckypool-eu2": {
        "url": "stratum+tcp://pearl-eu2.luckypool.io:3360",
        "merge": True,
        "note": "Second serveur EU de LuckyPool, mêmes règles que eu1.",
    },
    "herominers-fr": {
        "url": "stratum+tcp://fr.pearl.herominers.com:1200",
        "merge": None,
        "note": "0 % fee, PPS+. Merge PRL+MDL documenté ; vérifie sur pearl.herominers.com si NOCK est accepté.",
    },
    "herominers-de": {
        "url": "stratum+tcp://de.pearl.herominers.com:1200",
        "merge": None,
        "note": "Idem herominers-fr, serveur Allemagne.",
    },
    "kryptex-eu": {
        "url": "stratum+tcp://prl-eu.kryptex.network:7048",
        "merge": False,
        "note": "Kryptex recommande SRBMiner/BzMiner plutôt que WildRig (issue #356). Pas de merge NOCK connu.",
    },
}

# Préfixe bech32 "prl1" ; on reste tolérant sur le charset pour ne jamais bloquer une adresse valide.
PRL_RE = re.compile(r"^prl1[a-z0-9]{20,110}$")
NOCK_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{30,130}$")

UNIT_TO_TH = {"H": 1e-12, "KH": 1e-9, "MH": 1e-6, "GH": 1e-3, "TH": 1.0, "PH": 1e3}


# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[prl] {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"[prl] ERREUR : {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def parse_env(text: str) -> dict:
    """Parse un fichier KEY=VALUE (commentaires # et lignes vides ignorés)."""
    env = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def load_env(path: Path = ENV_FILE) -> dict:
    if not path.exists():
        die(f"{path} introuvable. Copie config/miner.env.example vers config/miner.env et remplis tes adresses.")
    env = parse_env(path.read_text(encoding="utf-8"))
    env.setdefault("WORKER", "4070s")
    env.setdefault("POOL", "luckypool-eu1")
    env.setdefault("POOL_PASS", "x")
    env.setdefault("USERNAME_TEMPLATE", "{prl}+{nock}.{worker}")
    env.setdefault("MINER", "wildrig")
    env.setdefault("PROFILE", "balanced")
    env.setdefault("API_PORT", "4068")
    return env


def validate_prl(addr: str) -> bool:
    return bool(PRL_RE.match(addr or ""))


def validate_nock(addr: str) -> bool:
    return bool(NOCK_RE.match(addr or ""))


def build_username(prl: str, nock: str, worker: str, template: str = "{prl}+{nock}.{worker}") -> str:
    """Construit le login pool. Sans NOCK, retombe sur {prl}.{worker}."""
    if not nock:
        template = template.replace("+{nock}", "").replace("{nock}+", "").replace("{nock}", "")
    user = template.format(prl=prl, nock=nock, worker=worker)
    if not worker:
        user = user.rstrip(".")
    return user


def resolve_pool(env: dict) -> tuple[str, dict]:
    name = env.get("POOL", "luckypool-eu1")
    if name == "custom":
        url = env.get("POOL_URL", "").strip()
        if not url:
            die("POOL=custom mais POOL_URL est vide.")
        return url, {"url": url, "merge": None, "note": "pool personnalisé"}
    if name not in POOLS:
        die(f"POOL inconnu : {name}. Choix : {', '.join(POOLS)} ou custom.")
    return POOLS[name]["url"], POOLS[name]


def pool_host_port(url: str) -> str:
    return re.sub(r"^[a-z0-9+]+://", "", url)


def load_profiles() -> dict:
    profiles = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    profiles.pop("_doc", None)
    if TUNED_FILE.exists():
        profiles.update(json.loads(TUNED_FILE.read_text(encoding="utf-8")))
    return profiles


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            import ctypes  # noqa: WPS433

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # pragma: no cover - dépend de Windows
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def find_binary(miner: str) -> Path | None:
    """Cherche le binaire du miner dans miners/ (ou dans le PATH)."""
    names = {
        "wildrig": ["wildrig.exe", "wildrig"],
        "bzminer": ["bzminer.exe", "bzminer"],
        "srbminer": ["SRBMiner-MULTI.exe", "SRBMiner-MULTI"],
    }[miner]
    if MINERS_DIR.exists():
        candidates = []
        for name in names:
            candidates += list(MINERS_DIR.rglob(name))
        candidates = [c for c in candidates if c.is_file()]
        if candidates:
            # version la plus récente = dossier modifié le plus récemment
            return sorted(candidates, key=lambda p: p.stat().st_mtime)[-1]
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


# ----------------------------------------------------------------------------
# nvidia-smi
# ----------------------------------------------------------------------------
NVSMI_FIELDS = "name,driver_version,power.draw,clocks.sm,clocks.mem,temperature.gpu,fan.speed,utilization.gpu"


def nvidia_smi(gpu: str | None) -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe and IS_WINDOWS:
        for cand in (r"C:\Windows\System32\nvidia-smi.exe",
                     r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            if Path(cand).exists():
                exe = cand
                break
    if not exe:
        return None
    cmd = [exe, f"--query-gpu={NVSMI_FIELDS}", "--format=csv,noheader,nounits"]
    if gpu:
        cmd += ["-i", str(gpu)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return None
    if not out:
        return None
    parts = [p.strip() for p in out.splitlines()[0].split(",")]
    keys = NVSMI_FIELDS.split(",")
    data = dict(zip(keys, parts))

    def num(v):
        try:
            return float(v)
        except ValueError:
            return None

    for k in ("power.draw", "clocks.sm", "clocks.mem", "temperature.gpu", "fan.speed", "utilization.gpu"):
        data[k] = num(data.get(k, ""))
    return data


# ----------------------------------------------------------------------------
# Construction de la ligne de commande du miner
# ----------------------------------------------------------------------------
def oc_args(miner: str, profile: dict) -> list[str]:
    a: list[str] = []
    cc = int(profile.get("core_clock") or 0)
    co = int(profile.get("core_offset") or 0)
    mo = int(profile.get("mem_offset") or 0)
    pl = int(profile.get("powerlimit") or 0)
    fan = int(profile.get("fan") or 0)
    if miner == "wildrig":
        a += ["--gpu-reset-oc"]
        if cc:
            a += ["--gpu-core-clock", str(cc)]
        if co:
            a += ["--gpu-core-offset", str(co)]
        if mo:
            a += ["--gpu-memory-offset", str(mo)]
        if pl:
            a += ["--gpu-powerlimit", str(pl)]
        if fan:
            a += ["--gpu-fan-speed", str(fan)]
    elif miner == "bzminer":
        if cc:
            a += ["--oc-lock-core-clock", str(cc)]
        if co:
            a += ["--oc-core-clock-offset", str(co)]
        if mo:
            a += ["--oc-memory-clock-offset", str(mo)]
        if pl:
            a += ["--oc-power-limit", str(pl)]
        if fan:
            a += ["--oc-fan-speed", str(fan)]
    elif miner == "srbminer":
        # suffixe 0 = premier algorithme (A0) dans la syntaxe SRBMiner
        a += ["--gpu-reset-oc"]
        if cc:
            a += ["--gpu-cclock0", str(cc)]
        if co:
            a += ["--gpu-coffset0", str(co)]
        if mo:
            a += ["--gpu-moffset0", str(mo)]
        if pl:
            a += ["--gpu-plimit0", str(pl)]
        if fan:
            a += ["--gpu-fan0", str(fan)]
    return a


def build_cmd(miner: str, binary: str, url: str, user: str, password: str, profile: dict,
              gpu: str | None, api_port: int, logfile: Path | None, benchmark_seconds: int = 0) -> list[str]:
    kernel = int(profile.get("pearlhash_kernel") or 0)
    intensity = profile.get("intensity")
    if miner == "wildrig":
        cmd = [binary, "-a", "pearlhash"]
        if benchmark_seconds:
            cmd += ["--benchmark-timeout", str(benchmark_seconds)]
        else:
            cmd += ["-o", url, "-u", user, "-p", password]
        cmd += ["--print-time", "20", "--no-color",
                "--gpu-temp-limit", "80", "--gpu-temp-resume", "65"]
        if not benchmark_seconds:
            cmd += ["--api-port", str(api_port), "--watchdog"]
        if kernel:
            cmd += ["--pearlhash-kernel", str(kernel)]
        if gpu not in (None, ""):
            cmd += ["-d", str(gpu)]
        if intensity:
            cmd += ["-i", str(intensity)]
        if logfile:
            cmd += ["-l", str(logfile)]
        cmd += oc_args(miner, profile)
    elif miner == "bzminer":
        cmd = [binary, "-a", "pearl"]
        if benchmark_seconds:
            cmd += ["--bench"]
        else:
            cmd += ["-p", url, "-w", user, "--pass", password]
        cmd += ["--no-color", "-o", "log"]  # sortie texte lisible par le bench
        # BzMiner : sélection GPU via son config.txt ("devices"), pas gérée ici (mono-GPU).
        cmd += oc_args(miner, profile)
    elif miner == "srbminer":
        cmd = [binary, "--algorithm", "pearlhash", "--disable-cpu"]
        if not benchmark_seconds:
            cmd += ["--pool", pool_host_port(url), "--wallet", user, "--password", password,
                    "--api-enable", "--api-port", str(api_port)]
        gpu_ids = str(gpu) if gpu not in (None, "") else "0"
        cmd += ["--gpu-id", gpu_ids]
        if kernel == 2:
            cmd += ["--pearl-k2", gpu_ids]  # équivalent SRBMiner du kernel 2
        if intensity:
            cmd += ["--gpu-intensity", str(intensity)]
        if logfile:
            cmd += ["--log-file", str(logfile)]
        cmd += oc_args(miner, profile)
    else:
        die(f"miner inconnu : {miner}")
    return cmd


# ----------------------------------------------------------------------------
# Lecture du hashrate
# ----------------------------------------------------------------------------
SPEED_RE = re.compile(
    r"speed\s+10s/60s/15m\s+([\d.]+|n/a)\s+([\d.]+|n/a)\s+([\d.]+|n/a)\s+([KMGTP]?H)/s", re.I)
GPU_RE = re.compile(r"GPU\s*#?\s*\d+[^\n]*?([\d.]+)\s*([KMGTP]?H)/s", re.I)
GENERIC_RE = re.compile(r"(?<![\w.])([\d]+(?:\.\d+)?)\s*([KMGTP]?H)/s", re.I)


def parse_hashrate_line(line: str) -> float | None:
    """Retourne le hashrate en TH/s trouvé dans une ligne de log, sinon None."""
    m = SPEED_RE.search(line)
    if m:
        val = m.group(1) if m.group(1) != "n/a" else m.group(2)
        if val == "n/a":
            return None
        return float(val) * UNIT_TO_TH[m.group(4).upper()]
    m = GPU_RE.search(line)
    if m:
        return float(m.group(1)) * UNIT_TO_TH[m.group(2).upper()]
    if re.search(r"\b(hashrate|speed|bench)\b", line, re.I):
        m = GENERIC_RE.search(line)
        if m:
            return float(m.group(1)) * UNIT_TO_TH[m.group(2).upper()]
    return None


def api_hashrate(port: int) -> float | None:
    """Interroge l'API JSON locale (style xmrig : hashrate.total[0] en H/s)."""
    for path in ("/2/summary", "/", "/api.json"):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            continue
        hr = data.get("hashrate") if isinstance(data, dict) else None
        if not isinstance(hr, dict):
            continue
        total = hr.get("total")
        if isinstance(total, list) and total and total[0] is not None:
            return float(total[0]) / 1e12
        threads = hr.get("threads")
        if isinstance(threads, list):
            vals = [t[0] for t in threads if isinstance(t, list) and t and t[0] is not None]
            if vals:
                return sum(float(v) for v in vals) / 1e12
    return None


# ----------------------------------------------------------------------------
# Téléchargement / installation
# ----------------------------------------------------------------------------
def download(url: str, dest: Path) -> None:
    log(f"téléchargement {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "prl-kit/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done * 100 // total:3d} % ({done >> 20} Mo)", end="", flush=True)
    print()


def extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
    else:
        with tarfile.open(archive) as t:
            t.extractall(target)
    if not IS_WINDOWS:
        for p in target.rglob("*"):
            if p.is_file() and p.suffix == "" and p.name.lower().startswith(("wildrig", "bzminer", "srbminer")):
                p.chmod(p.stat().st_mode | 0o111)


def github_latest_asset(repo: str, pattern: str) -> tuple[str, str]:
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(api, headers={"User-Agent": "prl-kit/1.0", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rel = json.loads(r.read().decode())
    for asset in rel.get("assets", []):
        if re.search(pattern, asset["name"]):
            return rel["tag_name"], asset["browser_download_url"]
    die(f"aucun asset ne correspond à {pattern} dans la release {rel.get('tag_name')} de {repo}")


def cmd_install(args) -> None:
    osname = platform.system()
    if osname not in ("Windows", "Linux"):
        die(f"OS non géré : {osname}")
    MINERS_DIR.mkdir(exist_ok=True)
    miner = args.miner
    if miner == "wildrig":
        version = args.version or WILDRIG_VERSION
        asset = WILDRIG_ASSETS[osname].format(v=version)
        url = WILDRIG_BASE.format(v=version) + asset
        target = MINERS_DIR / f"wildrig-multi-{version}"
    else:
        spec = FALLBACK_MINERS[miner]
        tag, url = github_latest_asset(spec["repo"], spec["asset"][osname])
        asset = url.rsplit("/", 1)[-1]
        target = MINERS_DIR / f"{miner}-{tag}"
    if target.exists() and not args.force:
        log(f"{target} existe déjà (utilise --force pour retélécharger)")
    else:
        archive = MINERS_DIR / asset
        try:
            download(url, archive)
        except urllib.error.HTTPError as e:
            die(f"téléchargement impossible ({e}). Télécharge {url} à la main et dézippe dans {target}")
        if target.exists():
            shutil.rmtree(target)
        extract(archive, target)
        archive.unlink(missing_ok=True)
    binary = find_binary(miner)
    if not binary:
        die(f"binaire {miner} introuvable après extraction dans {target}")
    log(f"OK : {binary}")


# ----------------------------------------------------------------------------
# check / pools / stats
# ----------------------------------------------------------------------------
def cmd_check(args) -> int:
    problems = 0
    log(f"Python {platform.python_version()} sur {platform.system()} {platform.release()}")
    env = load_env()
    prl, nock = env.get("PRL_WALLET", ""), env.get("NOCK_WALLET", "")
    if validate_prl(prl):
        log(f"PRL_WALLET ok ({prl[:10]}…)")
    else:
        log("PRL_WALLET invalide : doit commencer par prl1 suivi de lettres minuscules/chiffres")
        problems += 1
    if nock:
        if nock.startswith("0x"):
            log("NOCK_WALLET commence par 0x : c'est du wNOCK (Base), le pool paie en NOCK natif. Mets une adresse Nockchain base58.")
            problems += 1
        elif validate_nock(nock):
            log(f"NOCK_WALLET ok ({nock[:8]}…)")
        else:
            log("NOCK_WALLET ne ressemble pas à une adresse base58 Nockchain")
            problems += 1
    else:
        log("NOCK_WALLET vide : minage PRL seul (pas de merge NOCK)")
    url, pool = resolve_pool(env)
    user = build_username(prl, nock, env["WORKER"], env["USERNAME_TEMPLATE"])
    log(f"pool  : {url}")
    log(f"login : {user}")
    if nock and pool.get("merge") is False:
        log("ce pool n'annonce pas le merge NOCK : la part NOCK ne sera pas payée")
        problems += 1
    elif nock and pool.get("merge") is None:
        log("merge NOCK non confirmé sur ce pool : vérifie sa page 'how to mine'")
    profiles = load_profiles()
    if env["PROFILE"] not in profiles:
        log(f"PROFILE={env['PROFILE']} inconnu (dispo : {', '.join(profiles)})")
        problems += 1
    else:
        log(f"profil {env['PROFILE']} : {json.dumps(profiles[env['PROFILE']], ensure_ascii=False)}")
    binary = find_binary(env["MINER"])
    if binary:
        log(f"miner {env['MINER']} : {binary}")
    else:
        log(f"miner {env['MINER']} absent : lance `python3 prl.py install --miner {env['MINER']}`")
        problems += 1
    smi = nvidia_smi(env.get("GPU_ID") or None)
    if smi:
        log(f"GPU   : {smi['name']} | driver {smi['driver_version']} | {smi['power.draw']} W | {smi['temperature.gpu']} °C")
        if "4070 SUPER" not in smi["name"].upper():
            log("attention : les profils sont calibrés pour une RTX 4070 SUPER, lance `bench` pour ta carte")
        try:
            major = int(smi["driver_version"].split(".")[0])
            if major < 452:
                log("driver trop ancien pour WildRig (>= 452.39 Windows / 450.80 Linux)")
                problems += 1
        except ValueError:
            pass
    else:
        log("nvidia-smi introuvable : pas de lecture puissance/température (le bench sera moins précis)")
    if not is_admin():
        log("pas admin/root : le miner ne pourra pas appliquer le verrou core / power limit. Lance en admin (Windows) ou sudo (Linux).")
    log("aucun problème bloquant" if problems == 0 else f"{problems} problème(s) à corriger")
    return problems


def cmd_pools(args) -> None:
    env = load_env() if ENV_FILE.exists() else {}
    prl = env.get("PRL_WALLET", "prl1…")
    nock = env.get("NOCK_WALLET", "NOCK…")
    worker = env.get("WORKER", "4070s")
    tmpl = env.get("USERNAME_TEMPLATE", "{prl}+{nock}.{worker}")
    for name, p in POOLS.items():
        merge = {True: "merge NOCK : oui", False: "merge NOCK : non", None: "merge NOCK : à vérifier"}[p["merge"]]
        print(f"{name:15s} {p['url']:50s} {merge}")
        print(f"{'':15s} {p['note']}")
    print(f"\nlogin envoyé : {build_username(prl, nock, worker, tmpl)}")


def cmd_stats(args) -> None:
    env = load_env()
    port = int(env.get("API_PORT") or 4068)
    hr = api_hashrate(port)
    smi = nvidia_smi(env.get("GPU_ID") or None)
    print(f"hashrate (API :{port}) : {hr:.2f} TH/s" if hr is not None else f"hashrate : API :{port} injoignable (miner lancé ? --api-port ?)")
    if smi:
        eff = f" | {hr / smi['power.draw']:.3f} TH/s/W" if (hr and smi["power.draw"]) else ""
        print(f"{smi['name']} : {smi['power.draw']} W | core {smi['clocks.sm']} MHz | mem {smi['clocks.mem']} MHz | "
              f"{smi['temperature.gpu']} °C | fan {smi['fan.speed']} %{eff}")


# ----------------------------------------------------------------------------
# run : lancement + watchdog
# ----------------------------------------------------------------------------
def cmd_run(args) -> None:
    env = load_env()
    miner = args.miner or env["MINER"]
    profile_name = args.profile or env["PROFILE"]
    profiles = load_profiles()
    if profile_name not in profiles:
        die(f"profil {profile_name} inconnu (dispo : {', '.join(profiles)})")
    profile = profiles[profile_name]
    binary = find_binary(miner)
    if not binary:
        die(f"binaire {miner} introuvable : `python3 prl.py install --miner {miner}`")
    prl, nock = env.get("PRL_WALLET", ""), env.get("NOCK_WALLET", "")
    if not prl.startswith("prl1"):
        die("PRL_WALLET doit commencer par prl1 (voir `check`)")
    if not validate_prl(prl):
        log("attention : PRL_WALLET a un format inhabituel, vérifie-le sur le pool")
    url, _ = resolve_pool(env)
    user = build_username(prl, nock, env["WORKER"], env["USERNAME_TEMPLATE"])
    LOGS_DIR.mkdir(exist_ok=True)
    logfile = LOGS_DIR / f"{miner}-{time.strftime('%Y%m%d')}.log"
    cmd = build_cmd(miner, str(binary), url, user, env["POOL_PASS"], profile,
                    env.get("GPU_ID") or None, int(env["API_PORT"]), logfile)
    log(f"profil {profile_name} | {miner} | {url} | login {user}")
    log("commande : " + " ".join(cmd))
    if args.dry_run:
        return
    if not is_admin():
        log("attention : sans admin/root les réglages d'OC seront ignorés par le driver")
    restarts = 0
    stop = {"flag": False}

    def on_sigint(*_):
        stop["flag"] = True

    signal.signal(signal.SIGINT, on_sigint)
    while not stop["flag"]:
        started = time.time()
        proc = subprocess.Popen(cmd, cwd=str(binary.parent))
        try:
            while proc.poll() is None and not stop["flag"]:
                time.sleep(1)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if stop["flag"]:
            log("arrêt demandé")
            break
        uptime = time.time() - started
        restarts += 1
        if restarts > args.max_restarts:
            die(f"{restarts} redémarrages, j'abandonne (regarde {logfile})")
        delay = 10 if uptime > 120 else min(300, 30 * restarts)
        log(f"miner terminé (code {proc.returncode}) après {uptime:.0f}s, redémarrage dans {delay}s ({restarts}/{args.max_restarts})")
        time.sleep(delay)


# ----------------------------------------------------------------------------
# bench : balayage et sélection
# ----------------------------------------------------------------------------
def measure_point(cmd: list[str], cwd: Path, gpu: str | None, warmup: int, window: int) -> dict:
    """Lance le miner, ignore `warmup` s, puis moyenne hashrate (log) et puissance (nvidia-smi) sur `window` s."""
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1)
    hashrates: list[float] = []
    powers: list[float] = []
    temps: list[float] = []
    clocks: list[float] = []
    start = time.time()
    last_smi = 0.0
    import threading

    def reader():
        for line in proc.stdout:  # type: ignore[union-attr]
            hr = parse_hashrate_line(line)
            if hr is not None and time.time() - start > warmup:
                hashrates.append(hr)

    threading.Thread(target=reader, daemon=True).start()
    try:
        while time.time() - start < warmup + window and proc.poll() is None:
            now = time.time()
            if now - start > warmup and now - last_smi >= 2:
                smi = nvidia_smi(gpu)
                if smi:
                    if smi["power.draw"]:
                        powers.append(smi["power.draw"])
                    if smi["temperature.gpu"] is not None:
                        temps.append(smi["temperature.gpu"])
                    if smi["clocks.sm"]:
                        clocks.append(smi["clocks.sm"])
                last_smi = now
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
    return {
        "hashrate_th": statistics.median(hashrates) if hashrates else None,
        "power_w": statistics.mean(powers) if powers else None,
        "temp_c": max(temps) if temps else None,
        "core_mhz": statistics.median(clocks) if clocks else None,
        "samples": len(hashrates),
    }


def score_rows(rows: list[dict], objective: str, kwh_price: float, revenue_per_th: float | None) -> list[dict]:
    for r in rows:
        hr, pw = r.get("hashrate_th"), r.get("power_w")
        r["eff_th_per_w"] = (hr / pw) if (hr and pw) else None
        if hr and pw and revenue_per_th:
            r["net_eur_day"] = hr * revenue_per_th - pw / 1000 * 24 * kwh_price
        else:
            r["net_eur_day"] = None
    key = {"max": "hashrate_th", "eff": "eff_th_per_w", "net": "net_eur_day"}[objective]
    valid = [r for r in rows if r.get(key) is not None]
    return sorted(valid, key=lambda r: r[key], reverse=True)


def cmd_bench(args) -> None:
    env = load_env()
    miner = args.miner or env["MINER"]
    binary = find_binary(miner)
    if not binary:
        die(f"binaire {miner} introuvable : `python3 prl.py install --miner {miner}`")
    if not is_admin():
        log("attention : sans admin/root les verrous de clock ne seront pas appliqués, le bench n'a pas de sens")
    gpu = env.get("GPU_ID") or None
    kernels = [int(k) for k in args.kernels.split(",")]
    clocks = [int(c) for c in args.clocks.split(",")]
    base = dict(load_profiles()[args.base_profile])
    objective = args.objective
    revenue = float(env["REVENUE_PER_TH_DAY"]) if env.get("REVENUE_PER_TH_DAY") else None
    if objective == "net" and not revenue:
        die("objective=net demande REVENUE_PER_TH_DAY dans miner.env")
    kwh = float(env.get("KWH_PRICE") or 0.25)
    RESULTS_DIR.mkdir(exist_ok=True)
    out_csv = RESULTS_DIR / f"bench-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    rows: list[dict] = []
    total = len(kernels) * len(clocks)
    log(f"{total} points × {args.warmup + args.window}s ≈ {total * (args.warmup + args.window) / 60:.0f} min")
    if miner == "bzminer":
        log("note : BzMiner n'a pas de kernel 2, les valeurs --kernels sont ignorées")
        kernels = [0]
    if miner == "srbminer" and not args.online:
        log("SRBMiner n'a pas de mode hors ligne : bench sur le pool (--online forcé)")
        args.online = True
    for kernel in kernels:
        for clock in clocks:
            profile = dict(base, core_clock=clock, pearlhash_kernel=kernel, powerlimit=args.powerlimit)
            if args.online:
                url, _ = resolve_pool(env)
                user = build_username(env["PRL_WALLET"], env.get("NOCK_WALLET", ""), env["WORKER"] + "-bench",
                                      env["USERNAME_TEMPLATE"])
                cmd = build_cmd(miner, str(binary), url, user, env["POOL_PASS"], profile, gpu,
                                int(env["API_PORT"]), None)
            else:
                cmd = build_cmd(miner, str(binary), "", "", "", profile, gpu, int(env["API_PORT"]), None,
                                benchmark_seconds=args.warmup + args.window + 15)
            log(f"kernel {kernel} | core {clock} MHz | PL {args.powerlimit} W …")
            m = measure_point(cmd, binary.parent, gpu, args.warmup, args.window)
            row = {"kernel": kernel, "core_clock": clock, "powerlimit": args.powerlimit, **m}
            rows.append(row)
            hr = f"{m['hashrate_th']:.1f} TH/s" if m["hashrate_th"] else "n/a"
            pw = f"{m['power_w']:.0f} W" if m["power_w"] else "n/a"
            eff = f"{m['hashrate_th'] / m['power_w']:.3f} TH/s/W" if (m["hashrate_th"] and m["power_w"]) else ""
            log(f"  -> {hr} | {pw} | {eff} | {m['samples']} échantillons")
            time.sleep(5)
    ranked = score_rows(rows, objective, kwh, revenue)
    if not ranked and objective == "eff" and any(r.get("hashrate_th") for r in rows):
        log("pas de lecture de puissance (nvidia-smi absent ?) : classement par hashrate brut à la place")
        objective = "max"
        ranked = score_rows(rows, objective, kwh, revenue)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        fields = ["kernel", "core_clock", "powerlimit", "hashrate_th", "power_w", "eff_th_per_w", "net_eur_day",
                  "temp_c", "core_mhz", "samples"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})
    log(f"résultats : {out_csv}")
    if not ranked:
        die("aucune mesure exploitable (hashrate non lu ?). Vérifie que le miner tourne avec `run --dry-run` puis à la main.")
    best = ranked[0]

    def fmt(v, spec, unit):
        return f"{v:{spec}} {unit}" if v is not None else f"n/a {unit}"

    def describe(r):
        s = f"{fmt(r['hashrate_th'], '.1f', 'TH/s')} @ {fmt(r['power_w'], '.0f', 'W')} = {fmt(r['eff_th_per_w'], '.3f', 'TH/s/W')}"
        if r.get("net_eur_day") is not None:
            s += f" | net {r['net_eur_day']:.2f} €/j"
        return s

    log("classement (" + objective + ") :")
    for r in ranked[:5]:
        log(f"  kernel {r['kernel']} core {r['core_clock']} : {describe(r)}")
    tuned = dict(base, core_clock=best["core_clock"], pearlhash_kernel=best["kernel"], powerlimit=args.powerlimit,
                 expected=f"mesuré {describe(best)} ({objective})")
    existing = json.loads(TUNED_FILE.read_text(encoding="utf-8")) if TUNED_FILE.exists() else {}
    existing["tuned"] = tuned
    TUNED_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"profil 'tuned' écrit dans {TUNED_FILE}. Mets PROFILE=tuned dans miner.env puis `run`.")


# ----------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("check", help="vérifie config, adresses, GPU, driver")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("install", help="télécharge le miner")
    s.add_argument("--miner", choices=["wildrig", "bzminer", "srbminer"], default="wildrig")
    s.add_argument("--version", help="version WildRig (défaut %s)" % WILDRIG_VERSION)
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("pools", help="liste les pools connus")
    s.set_defaults(fn=cmd_pools)

    s = sub.add_parser("run", help="lance le miner (watchdog inclus)")
    s.add_argument("--miner", choices=["wildrig", "bzminer", "srbminer"])
    s.add_argument("--profile", help="eco | balanced | max | stock | tuned")
    s.add_argument("--dry-run", action="store_true", help="affiche la commande sans lancer")
    s.add_argument("--max-restarts", type=int, default=50)
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("bench", help="balayage verrou core × kernel, choisit le meilleur")
    s.add_argument("--miner", choices=["wildrig", "bzminer", "srbminer"])
    s.add_argument("--clocks", default="1800,1950,2100,2250,2400", help="verrous core MHz, séparés par des virgules")
    s.add_argument("--kernels", default="0,2", help="valeurs --pearlhash-kernel à tester")
    s.add_argument("--powerlimit", type=int, default=220, help="PL fixe pendant le bench (W)")
    s.add_argument("--base-profile", default="balanced")
    s.add_argument("--warmup", type=int, default=45, help="secondes ignorées au démarrage de chaque point")
    s.add_argument("--window", type=int, default=90, help="secondes de mesure par point")
    s.add_argument("--objective", choices=["eff", "max", "net"], default="eff",
                   help="eff = TH/s par watt (défaut), max = hashrate brut, net = €/jour après électricité")
    s.add_argument("--online", action="store_true", help="bench sur le vrai pool au lieu du mode --benchmark hors ligne")
    s.set_defaults(fn=cmd_bench)

    s = sub.add_parser("stats", help="hashrate API + nvidia-smi")
    s.set_defaults(fn=cmd_stats)

    args = p.parse_args(argv)
    rc = args.fn(args)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
