"""Golden-transcript tests for pm3parse. Run: python -m unittest (from pm3gui/)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # pm3gui/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))                   # tests/
import pm3parse as P            # noqa: E402
import goldens as G             # noqa: E402


class TestHelpers(unittest.TestCase):
    def test_norm_hex(self):
        self.assertEqual(P.norm_hex("04 50 e5 fa 84 6b 80"), "0450E5FA846B80")
        self.assertEqual(P.norm_hex(None), "")

    def test_size_flag(self):
        self.assertEqual(P.size_flag(1152), "--1k+")
        self.assertEqual(P.size_flag(1024), "--1k")
        self.assertEqual(P.size_flag(4096), "--4k")
        self.assertEqual(P.size_flag(999), "--1k")  # unknown → safe default


class TestHfInfo(unittest.TestCase):
    def test_gdm_blank(self):
        i = P.parse_hf_info(G.INFO_GDM_BLANK)
        self.assertEqual(i["uid"], "04 CD E5 03 F5 EF 45")
        self.assertEqual(i["atqa"], "00 44")
        self.assertEqual(i["sak"], "08")
        self.assertTrue(i["is_gdm"])
        self.assertIn("GDM", i["magic"])
        self.assertEqual(i["prng"], "weak")
        self.assertIsNone(i["error"])

    def test_clone_ok(self):
        i = P.parse_hf_info(G.INFO_CLONE_OK)
        self.assertEqual(P.norm_hex(i["uid"]), "0450E5FA846B80")

    def test_blank_0000(self):
        i = P.parse_hf_info(G.INFO_BLANK_0000)
        self.assertEqual(P.norm_hex(i["uid"]), "00000000")
        self.assertEqual(i["sak"], "00")

    def test_non_gdm(self):
        i = P.parse_hf_info(G.INFO_NON_GDM)
        self.assertFalse(i["is_gdm"])
        self.assertEqual(P.norm_hex(i["uid"]), "9A5C2EF1")

    def test_prng_word_both_wordings(self):
        # "Prng....... weak" and "Prng detection....... weak" both -> "weak"
        self.assertEqual(P.parse_hf_info(G.INFO_GDM_BLANK)["prng"], "weak")
        self.assertEqual(P.parse_hf_info(G.INFO_NON_GDM)["prng"], "weak")


class TestGdmConfig(unittest.TestCase):
    def test_factory_8500(self):
        c = P.parse_gdm_config(G.GDMCFG_8500)
        self.assertEqual(c["hex"], "8500000000005A00005A005A005A0008")
        self.assertFalse(c["wakeup_enabled"])
        self.assertFalse(c["cfg_block_access"])
        self.assertEqual(c["perso"], "5A")
        self.assertEqual(c["magic_auth"], "5A")
        self.assertFalse(c["wakeup_20_23"])

    def test_enabled_7aff(self):
        c = P.parse_gdm_config(G.GDMCFG_7AFF)
        self.assertEqual(c["hex"], "7AFF000000005A00005A005A005A0008")
        self.assertTrue(c["wakeup_enabled"])
        self.assertTrue(c["cfg_block_access"])
        self.assertEqual(c["perso"], "5A")
        self.assertTrue(c["hex"].startswith("7AFF"))

    def test_perso_bad(self):
        c = P.parse_gdm_config(G.GDMCFG_PERSO_BAD)
        self.assertEqual(c["perso"], "00")

    def test_auth_error_is_none(self):
        self.assertIsNone(P.parse_gdm_config(G.AUTH_ERROR))
        self.assertIsNone(P.parse_gdm_config(G.WUPC1_ERROR))


class TestWriteAndLoad(unittest.TestCase):
    def test_write_ok_is_ack_only(self):
        w = P.parse_write_result(G.GDMSETCFG_OK)
        self.assertTrue(w["ack"])
        self.assertFalse(w["fail"])

    def test_write_auth_error_is_fail(self):
        w = P.parse_write_result(G.AUTH_ERROR)
        self.assertTrue(w["fail"])

    def test_cload(self):
        c = P.parse_cload(G.CLOAD_OK)
        self.assertTrue(c["ok"])
        self.assertEqual(c["blocks"], 72)

    def test_uscuid_script(self):
        s = P.parse_uscuid_script(G.USCUID_SCRIPT_OK)
        self.assertEqual(P.norm_hex(s["uid"]), "0450E5FA846B80")  # not the NUID line
        self.assertTrue(s["ok"])


class TestErrors(unittest.TestCase):
    def test_signatures(self):
        self.assertEqual(P.error_signature(G.AUTH_ERROR), "auth-error")
        self.assertEqual(P.error_signature(G.WUPC1_ERROR), "gen1a-knock-fail")
        self.assertIsNone(P.error_signature(G.INFO_GDM_BLANK))


class TestMagicGen(unittest.TestCase):
    def test_slugs(self):
        self.assertEqual(P.magic_gen_slug("Gen 4 GDM / USCUID ( Magic Auth )"), "gdm")
        self.assertEqual(P.magic_gen_slug("Gen 4 GTU"), "gen4")
        self.assertEqual(P.magic_gen_slug("Gen 3"), "gen3")
        self.assertEqual(P.magic_gen_slug("Gen 2 / CUID"), "gen2")
        self.assertEqual(P.magic_gen_slug("Gen 1a"), "gen1a")
        self.assertIsNone(P.magic_gen_slug("N/A"))

    def test_in_hf_info(self):
        self.assertEqual(P.parse_hf_info(G.INFO_GDM_BLANK)["magic_gen"], "gdm")
        self.assertEqual(P.parse_hf_info(G.INFO_NON_GDM)["magic_gen"], "gen1a")
        self.assertIsNone(P.parse_hf_info(G.INFO_CLONE_OK)["magic_gen"])


class TestRawAck(unittest.TestCase):
    def test_ack(self):
        self.assertTrue(P.parse_raw_ack("[+] 0A "))
        self.assertTrue(P.parse_raw_ack("received bytes: 0a"))
        self.assertFalse(P.parse_raw_ack(G.WUPC1_ERROR))
        self.assertFalse(P.parse_raw_ack(""))


class TestCompareDumps(unittest.TestCase):
    def test_match(self):
        self.assertTrue(P.compare_dumps(bytes(1024), bytes(1024))["match"])

    def test_diff_block(self):
        b = bytearray(1024); b[16 * 5] = 0xFF
        c = P.compare_dumps(bytes(1024), bytes(b))
        self.assertFalse(c["match"])
        self.assertEqual(c["diff_blocks"], [5])
        self.assertEqual(c["blocks"], 64)

    def test_unreadable(self):
        self.assertFalse(P.compare_dumps(None, bytes(16))["readable"])
        self.assertFalse(P.compare_dumps(bytes(16), b"")["readable"])


if __name__ == "__main__":
    unittest.main()
