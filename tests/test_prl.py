"""Tests unitaires du kit (logique pure, sans GPU ni réseau).  Lancer : python3 -m unittest -v"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import prl  # noqa: E402

PRL = "prl1qqxyz0s3jn54khce6mua7lqpzry9x8gf2tvdw0s3jn54"
NOCK = "3ZfP2rM6vTq9XwE4bJ7kHdN1aC5sYgL8uRtVnQ2iKp"


class UsernameTests(unittest.TestCase):
    def test_merge_login(self):
        self.assertEqual(prl.build_username(PRL, NOCK, "rig"), f"{PRL}+{NOCK}.rig")

    def test_without_nock_drops_plus(self):
        self.assertEqual(prl.build_username(PRL, "", "rig"), f"{PRL}.rig")

    def test_custom_template(self):
        self.assertEqual(prl.build_username(PRL, NOCK, "rig", "{prl}.{worker}"), f"{PRL}.rig")

    def test_no_worker(self):
        self.assertEqual(prl.build_username(PRL, NOCK, ""), f"{PRL}+{NOCK}")


class AddressTests(unittest.TestCase):
    def test_prl_valid(self):
        self.assertTrue(prl.validate_prl(PRL))

    def test_prl_rejects_wrong_prefix_or_charset(self):
        self.assertFalse(prl.validate_prl("prl2abc"))
        self.assertFalse(prl.validate_prl("prl1" + "B" * 30))  # majuscules refusées
        self.assertFalse(prl.validate_prl(""))

    def test_nock_valid_base58(self):
        self.assertTrue(prl.validate_nock(NOCK))

    def test_nock_rejects_hex_and_short(self):
        self.assertFalse(prl.validate_nock("0x" + "a" * 40))
        self.assertFalse(prl.validate_nock("abc"))
        self.assertFalse(prl.validate_nock("O0Il" * 10))  # caractères exclus de base58


class EnvTests(unittest.TestCase):
    def test_parse_env(self):
        env = prl.parse_env("# c\nA=1\nB = deux \nC=\"trois\"\nD\nE=a=b\n")
        self.assertEqual(env, {"A": "1", "B": "deux", "C": "trois", "E": "a=b"})

    def test_example_env_parses_and_has_keys(self):
        env = prl.parse_env((ROOT / "config" / "miner.env.example").read_text(encoding="utf-8"))
        for key in ("PRL_WALLET", "NOCK_WALLET", "WORKER", "POOL", "MINER", "PROFILE", "USERNAME_TEMPLATE"):
            self.assertIn(key, env)


class ProfileTests(unittest.TestCase):
    def test_profiles_json_valid(self):
        profiles = json.loads((ROOT / "config" / "profiles.json").read_text(encoding="utf-8"))
        profiles.pop("_doc")
        for name in ("eco", "balanced", "max", "stock"):
            self.assertIn(name, profiles)
            p = profiles[name]
            self.assertLessEqual(p["powerlimit"], 220, f"{name}: PL > TDP 4070 SUPER")
            self.assertIn(p["pearlhash_kernel"], (0, 1, 2))
            self.assertTrue(p["core_clock"] == 0 or 1500 <= p["core_clock"] <= 2800)


class CommandTests(unittest.TestCase):
    profile = {"core_clock": 2100, "core_offset": 0, "mem_offset": -500, "powerlimit": 185,
               "fan": 70, "pearlhash_kernel": 2, "intensity": None}

    def test_wildrig_cmd(self):
        cmd = prl.build_cmd("wildrig", "wildrig", "stratum+tcp://pool:3360", "u", "x", self.profile,
                            "0", 4068, Path("m.log"))
        s = " ".join(cmd)
        self.assertIn("-a pearlhash", s)
        self.assertIn("-o stratum+tcp://pool:3360 -u u -p x", s)
        self.assertIn("--pearlhash-kernel 2", s)
        self.assertIn("--gpu-core-clock 2100", s)
        self.assertIn("--gpu-memory-offset -500", s)
        self.assertIn("--gpu-powerlimit 185", s)
        self.assertIn("--gpu-fan-speed 70", s)
        self.assertIn("-d 0", s)
        self.assertIn("--api-port 4068", s)
        self.assertIn("--watchdog", s)
        self.assertNotIn("--gpu-core-offset", s)

    def test_wildrig_benchmark_has_no_pool(self):
        cmd = prl.build_cmd("wildrig", "wildrig", "", "", "", self.profile, None, 4068, None, benchmark_seconds=120)
        s = " ".join(cmd)
        self.assertIn("--benchmark-timeout 120", s)
        self.assertNotIn(" -o ", s)
        self.assertNotIn("--api-port", s)
        self.assertNotIn("-d", cmd)

    def test_stock_profile_adds_no_oc(self):
        stock = dict(self.profile, core_clock=0, mem_offset=0, powerlimit=0, fan=0, pearlhash_kernel=0)
        cmd = prl.build_cmd("wildrig", "wildrig", "p", "u", "x", stock, None, 4068, None)
        s = " ".join(cmd)
        for flag in ("--gpu-core-clock", "--gpu-powerlimit", "--pearlhash-kernel", "--gpu-fan-speed"):
            self.assertNotIn(flag, s)

    def test_bzminer_cmd(self):
        cmd = prl.build_cmd("bzminer", "bzminer", "stratum+tcp://pool:3360", "u", "x", self.profile, "0", 4068, None)
        s = " ".join(cmd)
        self.assertIn("-a pearl -p stratum+tcp://pool:3360 -w u --pass x", s)
        self.assertIn("--oc-lock-core-clock 2100", s)
        self.assertIn("--oc-power-limit 185", s)

    def test_srbminer_cmd(self):
        cmd = prl.build_cmd("srbminer", "SRBMiner-MULTI", "stratum+tcp://pool:3360", "u", "x", self.profile,
                            "0", 21550, None)
        s = " ".join(cmd)
        self.assertIn("--algorithm pearlhash", s)
        self.assertIn("--pool pool:3360", s)  # SRBMiner veut host:port sans schéma
        self.assertIn("--gpu-id 0", s)
        self.assertIn("--pearl-k2 0", s)
        self.assertIn("--gpu-cclock0 2100", s)
        self.assertIn("--gpu-plimit0 185", s)


class HashrateParseTests(unittest.TestCase):
    def test_wildrig_speed_line(self):
        line = "[2026-09-28 12:00:00] speed 10s/60s/15m 108.2 108.0 n/a TH/s max 109.0 TH/s"
        self.assertAlmostEqual(prl.parse_hashrate_line(line), 108.2)

    def test_speed_line_na_falls_back_to_60s(self):
        line = "speed 10s/60s/15m n/a 107.5 n/a TH/s max 109.0 TH/s"
        self.assertAlmostEqual(prl.parse_hashrate_line(line), 107.5)

    def test_gpu_line_with_unit_conversion(self):
        self.assertAlmostEqual(prl.parse_hashrate_line("GPU #0: 98500.0 GH/s"), 98.5)

    def test_generic_needs_keyword(self):
        self.assertIsNone(prl.parse_hashrate_line("accepted (12/0) diff 4.00M 120 ms"))
        self.assertAlmostEqual(prl.parse_hashrate_line("hashrate: 132.3 TH/s"), 132.3)

    def test_noise_returns_none(self):
        self.assertIsNone(prl.parse_hashrate_line("new job from pool diff 2M"))


class ScoringTests(unittest.TestCase):
    rows = [
        {"kernel": 0, "core_clock": 2400, "hashrate_th": 132.0, "power_w": 205.0},
        {"kernel": 2, "core_clock": 1950, "hashrate_th": 108.0, "power_w": 160.0},
        {"kernel": 2, "core_clock": 2100, "hashrate_th": None, "power_w": 175.0},
    ]

    def test_efficiency_picks_low_clock(self):
        ranked = prl.score_rows([dict(r) for r in self.rows], "eff", 0.25, None)
        self.assertEqual(ranked[0]["core_clock"], 1950)
        self.assertEqual(len(ranked), 2)  # ligne sans hashrate écartée

    def test_max_picks_high_clock(self):
        ranked = prl.score_rows([dict(r) for r in self.rows], "max", 0.25, None)
        self.assertEqual(ranked[0]["core_clock"], 2400)

    def test_net_uses_electricity_price(self):
        # revenu 0.01 €/TH/j : 132*0.01 - 0.205*24*0.25 = 0.09 ; 108*0.01 - 0.160*24*0.25 = 0.12
        ranked = prl.score_rows([dict(r) for r in self.rows], "net", 0.25, 0.01)
        self.assertEqual(ranked[0]["core_clock"], 1950)
        self.assertAlmostEqual(ranked[0]["net_eur_day"], 0.12, places=3)


class PoolTests(unittest.TestCase):
    def test_known_pools_have_scheme_and_port(self):
        for name, p in prl.POOLS.items():
            self.assertRegex(p["url"], r"^stratum\+tcp://[\w.-]+:\d+$", name)

    def test_host_port_strip(self):
        self.assertEqual(prl.pool_host_port("stratum+tcp://a.b:1"), "a.b:1")
        self.assertEqual(prl.pool_host_port("a.b:1"), "a.b:1")


if __name__ == "__main__":
    unittest.main()
