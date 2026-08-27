"""Tests for pm3ops using a fake runner that replays golden transcripts.
Verifies the happy path AND — critically — that every safety abort issues NO
data writes. Run: python -m unittest (from pm3gui/)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # pm3gui/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))                   # tests/
import pm3ops as OPS            # noqa: E402
import goldens as G             # noqa: E402

UID = "04 A1 B2 C3 D4 E5 F6"
DATA_WRITE = ("hf mf cload", "script run")
ANY_WRITE = ("hf mf gdmsetcfg", "hf mf cload", "script run")


class FakeRunner:
    """Maps a command prefix -> golden output. `info_seq` feeds successive
    `hf mf info` calls (current card, then verify)."""

    def __init__(self, table, info_seq=None):
        self.table = table
        self.info = list(info_seq or [])
        self.cmds = []

    def __call__(self, cmd):
        self.cmds.append(cmd)
        if cmd.startswith("hf mf info"):
            return (self.info.pop(0) if self.info else G.INFO_BLANK_0000).splitlines()
        best = None
        for pre in self.table:
            if cmd.startswith(pre) and (best is None or len(pre) > len(best)):
                best = pre
        return (self.table[best] if best else "").splitlines()

    def matching(self, prefixes):
        return [c for c in self.cmds if c.startswith(prefixes)]


def full_table():
    return {
        "hf mf gdmcfg --gen1a": G.GDMCFG_7AFF,
        "hf mf gdmcfg": G.GDMCFG_8500,
        "hf mf gdmsetcfg": G.GDMSETCFG_OK,
        "hf mf cload": G.CLOAD_OK,
        "script run hf_mf_uscuid_prog": G.USCUID_SCRIPT_OK,
    }


def clone(runner, execute, uid=UID, dump_bytes=1152, dump_base="d-002"):
    return OPS.clone_gdm_uscuid(runner, dump_base=dump_base, target_uid=uid,
                                dump_bytes=dump_bytes, execute=execute)


class TestHappyPath(unittest.TestCase):
    def test_execute_clones_and_verifies(self):
        r = FakeRunner(full_table(), info_seq=[G.INFO_GDM_BLANK, G.INFO_CLONE_OK])
        res = clone(r, execute=True)
        self.assertTrue(res["ok"])
        self.assertFalse(res["aborted"])
        self.assertEqual(res["result"]["cload_flag"], "--1k+")
        import pm3parse as P
        self.assertEqual(P.norm_hex(res["result"]["verified_uid"]), "04A1B2C3D4E5F6")

    def test_uid_script_is_the_last_write(self):
        r = FakeRunner(full_table(), info_seq=[G.INFO_GDM_BLANK, G.INFO_CLONE_OK])
        clone(r, execute=True)
        writes = r.matching(DATA_WRITE)
        self.assertTrue(writes[-1].startswith("script run"),
                        f"last data write must be the UID script, got: {writes}")
        # no cload appears AFTER the UID script
        last_script = max(i for i, c in enumerate(r.cmds) if c.startswith("script run"))
        self.assertFalse(any(c.startswith("hf mf cload") for c in r.cmds[last_script + 1:]),
                         "cload must never run after the UID script")

    def test_final_command_is_verify(self):
        r = FakeRunner(full_table(), info_seq=[G.INFO_GDM_BLANK, G.INFO_CLONE_OK])
        clone(r, execute=True)
        self.assertTrue(r.cmds[-1].startswith("hf mf info"))

    def test_20_23_card_uses_gdm_channel(self):
        # a 20/23-knock card (byte2==85) must verify the knock over --gdm and
        # program the UID with -t 2, not the hardcoded gen1a/40-43 path
        table = full_table()
        table["hf mf gdmcfg"] = G.GDMCFG_8500_2023
        table["hf mf gdmcfg --gdm"] = G.GDMCFG_7AFF_2023
        r = FakeRunner(table, info_seq=[G.INFO_GDM_BLANK, G.INFO_CLONE_OK])
        res = clone(r, execute=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"]["wake"], "2")
        self.assertIn("hf mf gdmcfg --gdm", r.cmds)
        self.assertTrue(any("-t 2" in c for c in r.cmds))
        self.assertFalse(any(c.startswith("hf mf gdmcfg --gen1a") for c in r.cmds))


class TestDryRun(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        r = FakeRunner(full_table(), info_seq=[G.INFO_GDM_BLANK])
        res = clone(r, execute=False)
        self.assertTrue(res["ok"])
        self.assertFalse(res["aborted"])
        self.assertTrue(res["result"]["ready"])
        self.assertIn("plan", res["result"])
        self.assertEqual(r.matching(ANY_WRITE), [], "dry run must not write anything")


class TestSafetyAborts(unittest.TestCase):
    def test_not_a_gdm_card(self):
        r = FakeRunner(full_table(), info_seq=[G.INFO_NON_GDM])
        res = clone(r, execute=True)
        self.assertTrue(res["aborted"])
        self.assertIn("NOT a Gen4 GDM", res["error"])
        self.assertEqual(r.matching(ANY_WRITE), [])

    def test_knock_gate_fails_before_any_data_write(self):
        # both plain and gen1a gdmcfg return 8500 → enabling the knock "fails"
        table = full_table()
        table["hf mf gdmcfg --gen1a"] = G.GDMCFG_8500
        r = FakeRunner(table, info_seq=[G.INFO_GDM_BLANK])
        res = clone(r, execute=True)
        self.assertTrue(res["aborted"])
        self.assertIn("did NOT enable", res["error"])
        # the enable write may have been attempted, but NO data writes happened
        self.assertEqual(r.matching(DATA_WRITE), [],
                         "must abort before cload/script when the knock gate fails")

    def test_perso_not_7byte(self):
        table = full_table()
        table["hf mf gdmcfg"] = G.GDMCFG_PERSO_BAD
        r = FakeRunner(table, info_seq=[G.INFO_GDM_BLANK])
        res = clone(r, execute=True)
        self.assertTrue(res["aborted"])
        self.assertIn("perso", res["error"])
        self.assertEqual(r.matching(ANY_WRITE), [])

    def test_locked_card_config_unreadable(self):
        table = full_table()
        table["hf mf gdmcfg --gen1a"] = G.AUTH_ERROR
        table["hf mf gdmcfg"] = G.AUTH_ERROR
        r = FakeRunner(table, info_seq=[G.INFO_GDM_BLANK])
        res = clone(r, execute=True)
        self.assertTrue(res["aborted"])
        self.assertIn("locked", res["error"])
        self.assertEqual(r.matching(ANY_WRITE), [])

    def test_bad_uid_length_no_device_contact(self):
        r = FakeRunner(full_table())
        res = clone(r, execute=True, uid="11 22")  # 2 bytes
        self.assertTrue(res["aborted"])
        self.assertEqual(r.cmds, [], "should reject before touching the device")

    def test_missing_dump_no_device_contact(self):
        r = FakeRunner(full_table())
        res = clone(r, execute=True, dump_bytes=0)
        self.assertTrue(res["aborted"])
        self.assertEqual(r.cmds, [])


class TestVerifyMismatch(unittest.TestCase):
    def test_verify_catches_wiped_uid(self):
        # everything "succeeds" but the final read shows 0000 → op reports failure
        r = FakeRunner(full_table(), info_seq=[G.INFO_GDM_BLANK, G.INFO_BLANK_0000])
        res = clone(r, execute=True)
        self.assertFalse(res["ok"])
        self.assertFalse(res["aborted"])
        self.assertIn("verify failed", res["error"])


class TestIdentify(unittest.TestCase):
    def test_identify(self):
        r = FakeRunner({}, info_seq=[G.INFO_GDM_BLANK])
        res = OPS.identify(r)
        self.assertTrue(res["ok"])
        self.assertTrue(res["result"]["is_gdm"])


class TestProbeMagic(unittest.TestCase):
    def test_detects_gdm(self):
        r = FakeRunner({}, info_seq=[G.INFO_GDM_BLANK])
        res = OPS.probe_magic(r)
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"]["magic_gen"], "gdm")

    def test_non_magic_warns(self):
        r = FakeRunner({}, info_seq=[G.INFO_CLONE_OK])  # no magic line
        res = OPS.probe_magic(r)
        self.assertTrue(res["ok"])
        self.assertIsNone(res["result"]["magic_gen"])
        self.assertTrue(res["warnings"])

    def test_no_card(self):
        r = FakeRunner({}, info_seq=["[=] --- ISO14443-A Information"])
        res = OPS.probe_magic(r)
        self.assertTrue(res["aborted"])


class FakeIO:
    def __init__(self, mapping):
        self.m = mapping

    def read_dump(self, base):
        return self.m.get(base)


class TestDeepVerify(unittest.TestCase):
    def _run(self, verify_bytes):
        r = FakeRunner({"hf mf dump": ""})
        io = FakeIO({"hf-mf-x-dump": bytes(1024), "hf-mf-verify": verify_bytes})
        return OPS.deep_verify_mfc(r, io, dump_base="hf-mf-x-dump",
                                   key_base="hf-mf-x-key", dump_bytes=1024)

    def test_match(self):
        res = self._run(bytes(1024))
        self.assertTrue(res["ok"])
        self.assertTrue(res["result"]["match"])

    def test_mismatch(self):
        b = bytearray(1024); b[16 * 3] = 0x9
        res = self._run(bytes(b))
        self.assertFalse(res["ok"])
        self.assertEqual(res["result"]["diff_blocks"], [3])

    def test_unreadable_is_not_a_false_fail(self):
        res = self._run(None)  # target couldn't be read back
        self.assertFalse(res["ok"])
        self.assertFalse(res["result"]["readable"])
        self.assertIn("couldn't read", res["error"])


class TestRescue(unittest.TestCase):
    def test_recoverable_via_gen1a(self):
        table = {
            "hf 14a info": G.INFO_GDM_BLANK,
            "hf mf gdmcfg --gen1a": G.GDMCFG_7AFF,
            "hf mf gdmcfg --gdm": G.WUPC1_ERROR,
            "hf mf gdmcfg": G.AUTH_ERROR,
            "hf 14a raw": "",
        }
        res = OPS.rescue_magic(FakeRunner(table))
        self.assertTrue(res["ok"])
        self.assertIn("gen1a_gdmcfg", res["result"]["alive"])

    def test_soft_bricked(self):
        table = {
            "hf 14a info": G.INFO_BLANK_0000,   # present but UID 0000
            "hf mf gdmcfg --gen1a": G.WUPC1_ERROR,
            "hf mf gdmcfg --gdm": G.WUPC1_ERROR,
            "hf mf gdmcfg": G.AUTH_ERROR,
            "hf 14a raw": G.WUPC1_ERROR,
        }
        res = OPS.rescue_magic(FakeRunner(table))
        self.assertFalse(res["ok"])
        self.assertIn("soft-bricked", res["error"])


if __name__ == "__main__":
    unittest.main()
