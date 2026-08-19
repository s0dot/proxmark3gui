"""Real Proxmark3 (Iceman) console transcripts captured from actual hardware
during the USCUID clone work. Used as golden fixtures so parsers/ops are tested
against genuine output, not hand-waved strings."""

# hf mf info on a healthy Gen4 GDM/USCUID blank (factory, 7-byte UID)
INFO_GDM_BLANK = """\
[=] --- ISO14443-a Information -----------------------------
[+]  UID: 04 CD E5 03 F5 EF 45
[+] ATQA: 00 44
[+]  SAK: 08 [1]
[=]              TAG IC Signature: 15F4231104658BEFF62A5ED497CDBCCC
[+]        Signature verification: failed
[=] --- Magic Tag Information
[+] Magic capabilities... Gen 4 GDM / USCUID ( Magic Auth )
[=] --- PRNG Information
[+] Prng....... weak
"""

# hf mf info after a successful clone (UID now the target fob's)
INFO_CLONE_OK = """\
[=] --- ISO14443-a Information -----------------------------
[+]  UID: 04 50 E5 FA 84 6B 80
[+] ATQA: 00 44
[+]  SAK: 08 [1]
"""

# hf mf info on a blanked / UID-wiped card (presents as UL)
INFO_BLANK_0000 = """\
[=] --- ISO14443-a Information -----------------------------
[+]  UID: 00 00 00 00
[+] ATQA: 00 44
[+]  SAK: 00 [0]
[=] MIFARE Ultralight / NTAG detected
"""

# hf mf info on a plain (non-GDM) Gen1a MIFARE Classic 1K
INFO_NON_GDM = """\
[=] --- ISO14443-A Information ----------------------
[+]  UID: 9A 5C 2E F1
[+] ATQA: 00 04
[+]  SAK: 08 [2]
[+] Possible types: MIFARE Classic 1K
[+] Magic capabilities... Gen 1a
[+] Prng detection....... weak
"""

# hf mf gdmcfg (plain / magic-auth) on a factory USCUID: wakeup disabled (8500)
GDMCFG_8500 = """\
[+] ------------------- GDM Gen4 Configuration -----------------------------------------
[+] 8500000000005A00005A005A005A0008
[+] 8500............................ Magic wakeup disabled
[+] ..................5A............ MFC EV1 perso. Unfused
[+] ......................5A........ Magic auth enabled
[+]                               08 SAK
"""

# hf mf gdmcfg --gen1a after enabling the knock: 7AFF (with cfg block access)
GDMCFG_7AFF = """\
[+] ------------------- GDM Gen4 Configuration -----------------------------------------
[+] 7AFF000000005A00005A005A005A0008
[+] 7AFF............................ Magic wakeup enabled with GDM cfg block access
[+] ....00.......................... Magic wakeup style Gen1a 40(7)/43
[+] ..................5A............ MFC EV1 perso. Unfused
[+] ......................5A........ Magic auth enabled
[+]                               08 SAK
"""

# gdmcfg config whose perso byte (index 9) is 00 = NOT a CL2/7-byte mode
GDMCFG_PERSO_BAD = """\
[+] ------------------- GDM Gen4 Configuration -----------------------------------------
[+] 8500000000005A000000005A005A0008
"""

# device error outputs
AUTH_ERROR = "[#] Auth error"
WUPC1_ERROR = "[#] wupC1 error"

# gdmsetcfg write (false-positive-prone: ACK only)
GDMSETCFG_OK = """\
[+] Write ( ok )
[?] Hint: Try `hf mf gdmcfg` to verify
"""

# hf mf cload of an 18-sector (1152-byte) dump
CLOAD_OK = """\
[=] Normally only a GDM / UMC card will handle the extra sectors
[+] Loaded 1152 bytes from binary file `hf-mf-0450E5FA846B80-dump-002`
[=] Copying to magic gen1a MIFARE Classic 1K Ev1
[+] Card loaded 72 blocks from file
[=] Done!
"""

# script run hf_mf_uscuid_prog -t 4 -u 0450E5FA846B80
USCUID_SCRIPT_OK = """\
[+] executing lua hf_mf_uscuid_prog.lua
[+] args '-t 4 -u 0450E5FA846B80'
[+] 0A
[+] 0A
[?] WARNING: nUID should be updated with this value:
[=] UID  | 04 50 E5 FA 84 6B 80
[=] NUID | AF 21 64 7F
[-] Updating real block 0
[+] finished hf_mf_uscuid_prog
"""
