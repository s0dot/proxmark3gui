/* Proxmark3 LF card registry — every Iceman LF clone-capable credential type.
 *
 * Each entry:
 *   id, name           identity
 *   sig                RegExp that identifies the type in `lf search` output
 *   readCmd            dedicated reader (canonical decoded output)
 *   clone              clone-to-T5577 template with {placeholders} == field keys
 *   fields[]           { k, re (capture grp 1), kind: dec|hex|str, conv? }
 *   extra(text)        optional extra clone flag derived from read text (e.g. nedap -l)
 *
 * Syntax verified against the bundled client's own `lf <type> clone -h`.
 * Regexes target ANSI-stripped reader output.
 */
window.CARD_REGISTRY = [
  { id: "em410x", name: "EM410x", sig: /EM 410x ID/i, readCmd: "lf em 410x reader",
    clone: "lf em 410x clone --id {id}",
    fields: [{ k: "id", re: /EM 410x ID\s+([0-9A-Fa-f]{10})/i, kind: "hex" }] },

  { id: "hid", name: "HID Prox", sig: /\bHID\b/, readCmd: "lf hid reader",
    clone: "lf hid clone -w {fmt} --fc {fc} --cn {cn}",
    fields: [
      { k: "fmt", re: /\[\s*([A-Za-z0-9]+)\s*\]/, kind: "str" },
      { k: "fc", re: /FC:\s*(\d+)/, kind: "dec" },
      { k: "cn", re: /CN:\s*(\d+)/, kind: "dec" },
    ] },

  { id: "awid", name: "AWID", sig: /AWID/i, readCmd: "lf awid reader",
    clone: "lf awid clone --fmt {fmt} --fc {fc} --cn {cn}",
    fields: [
      { k: "fmt", re: /AWID\s*-\s*len:\s*(\d+)/i, kind: "dec" },
      { k: "fc", re: /FC:\s*(\d+)/i, kind: "dec" },
      { k: "cn", re: /Card:\s*(\d+)/i, kind: "dec" },
    ] },

  { id: "indala", name: "Indala", sig: /Indala/i, readCmd: "lf indala reader",
    clone: "lf indala clone -r {raw}",
    fields: [{ k: "raw", re: /Indala \(len \d+\)\s+Raw:\s*([0-9A-Fa-f]+)/i, kind: "hex" }] },

  { id: "io", name: "ioProx", sig: /IO Prox/i, readCmd: "lf io reader",
    clone: "lf io clone --vn {vn} --fc {fc} --cn {cn}",
    fields: [
      { k: "vn", re: /IO Prox - XSF\((\d+)\)/i, kind: "dec" },
      { k: "fc", re: /IO Prox - XSF\(\d+\)([0-9A-Fa-f]{2}):/i, kind: "hex", conv: "hex2dec" },
      { k: "cn", re: /IO Prox - XSF\(\d+\)[0-9A-Fa-f]{2}:(\d+)/i, kind: "dec" },
    ] },

  { id: "fdxb", name: "FDX-B", sig: /FDX-B/i, readCmd: "lf fdxb reader",
    clone: "lf fdxb clone --country {country} --national {national}",
    fields: [
      { k: "country", re: /Country Code[\s.]*(\d+)/i, kind: "dec" },
      { k: "national", re: /National Code[\s.]*(\d+)/i, kind: "dec" },
    ] },

  { id: "paradox", name: "Paradox", sig: /Paradox/i, readCmd: "lf paradox reader",
    clone: "lf paradox clone --fc {fc} --cn {cn}",
    fields: [
      { k: "fc", re: /Paradox\s*-\s*ID:\s*[0-9A-Fa-f]+\s+FC:\s*(\d+)/i, kind: "dec" },
      { k: "cn", re: /FC:\s*\d+\s+Card:\s*(\d+)/i, kind: "dec" },
    ] },

  { id: "pyramid", name: "Farpointe/Pyramid", sig: /Pyramid/i, readCmd: "lf pyramid reader",
    clone: "lf pyramid clone --fc {fc} --cn {cn}",
    fields: [
      { k: "fc", re: /FC:\s*(\d+)/i, kind: "dec" },
      { k: "cn", re: /Card:\s*(\d+)/i, kind: "dec" },
    ] },

  { id: "viking", name: "Viking", sig: /Viking/i, readCmd: "lf viking reader",
    clone: "lf viking clone --cn {cn}",
    fields: [{ k: "cn", re: /Viking\s*-\s*Card\s+([0-9A-Fa-f]{8})/i, kind: "hex" }] },

  { id: "visa2000", name: "Visa2000", sig: /Visa2000/i, readCmd: "lf visa2000 reader",
    clone: "lf visa2000 clone --cn {cn}",
    fields: [{ k: "cn", re: /Visa2000\s*-\s*Card\s+(\d+)/i, kind: "dec" }] },

  { id: "nexwatch", name: "NexWatch", sig: /NexWatch|Quadrakey|Nexkey|Honeywell/i, readCmd: "lf nexwatch reader",
    clone: "lf nexwatch clone --raw {raw}",
    fields: [{ k: "raw", re: /Raw\s*:\s*([0-9A-Fa-f]{24})/i, kind: "hex" }] },

  { id: "securakey", name: "Securakey", sig: /Securakey/i, readCmd: "lf securakey reader",
    clone: "lf securakey clone --raw {raw}",
    fields: [{ k: "raw", re: /Raw:\s*([0-9A-Fa-f]{24})/i, kind: "hex" }] },

  { id: "keri", name: "KERI", sig: /KERI/i, readCmd: "lf keri reader",
    clone: "lf keri clone -t i --cn {cn}",
    fields: [{ k: "cn", re: /KERI - Internal ID:\s*(\d+)/i, kind: "dec" }] },

  { id: "gallagher", name: "Gallagher", sig: /GALLAGHER/i, readCmd: "lf gallagher reader",
    clone: "lf gallagher clone --rc {rc} --fc {fc} --cn {cn} --il {il}",
    fields: [
      { k: "rc", re: /Region:\s*(\d+)/i, kind: "dec" },
      { k: "fc", re: /Facility:\s*(\d+)/i, kind: "dec" },
      { k: "cn", re: /Card No\.?:\s*(\d+)/i, kind: "dec" },
      { k: "il", re: /Issue Level:\s*(\d+)/i, kind: "dec" },
    ] },

  { id: "noralsy", name: "Noralsy", sig: /Noralsy/i, readCmd: "lf noralsy reader",
    clone: "lf noralsy clone --cn {cn} -y {year}",
    fields: [
      { k: "cn", re: /Noralsy - Card:\s*(\d+)/i, kind: "dec" },
      { k: "year", re: /Year:\s*(\d+)/i, kind: "dec" },
    ] },

  { id: "jablotron", name: "Jablotron", sig: /Jablotron/i, readCmd: "lf jablotron reader",
    clone: "lf jablotron clone --cn {cn}",
    fields: [{ k: "cn", re: /Raw:\s*[fF]{4}([0-9A-Fa-f]{10})[0-9A-Fa-f]{2}/i, kind: "hex" }] },

  { id: "presco", name: "Presco", sig: /Presco/i, readCmd: "lf presco reader",
    clone: "lf presco clone -c {fullcode}",
    fields: [{ k: "fullcode", re: /Full code:\s*([0-9A-Fa-f]{8})/i, kind: "hex" }] },

  { id: "nedap", name: "Nedap", sig: /NEDAP/i, readCmd: "lf nedap reader",
    clone: "lf nedap clone --st {st} --cc {cc} --id {id}",
    extra: (t) => (/\(128b\)/.test(t) ? "-l" : ""),
    fields: [
      { k: "id", re: /NEDAP\s*\([^)]*\)\s*-\s*ID:\s*(\d+)/i, kind: "dec" },
      { k: "st", re: /subtype:\s*(\d+)/i, kind: "dec" },
      { k: "cc", re: /customer code:\s*(\d+)/i, kind: "dec" },
    ] },

  { id: "motorola", name: "Motorola Flexpass", sig: /Motorola/i, readCmd: "lf motorola reader",
    clone: "lf motorola clone --raw {raw}",
    fields: [{ k: "raw", re: /Raw:\s*([0-9A-Fa-f]{16})/i, kind: "hex" }] },

  { id: "idteck", name: "Idteck", sig: /IDTECK/i, readCmd: "lf idteck reader",
    clone: "lf idteck clone --raw {raw}",
    fields: [{ k: "raw", re: /Raw:\s*([0-9A-Fa-f]{16})/i, kind: "hex" }] },

  { id: "destron", name: "FDX-A Destron", sig: /Destron|FDX-A/i, readCmd: "lf destron reader",
    clone: "lf destron clone --uid {uid}",
    fields: [{ k: "uid", re: /FDX-A FECAVA Destron:\s*((?:[0-9A-Fa-f]{2}\s*){5})/i, kind: "hex" }] },

  { id: "pac", name: "PAC/Stanley", sig: /PAC\/Stanley/i, readCmd: "lf pac reader",
    clone: "lf pac clone --cn {cn}",
    fields: [{ k: "cn", re: /PAC\/Stanley\s*-\s*Card:\s*([0-9A-Fa-f]{8,9})/i, kind: "hex" }] },

  { id: "gproxii", name: "Guardall G-Prox II", sig: /G-Prox-II/i, readCmd: "lf gproxii reader",
    clone: "lf gproxii clone --xor {xor} --fmt {fmt} --fc {fc} --cn {cn}",
    fields: [
      { k: "fmt", re: /G-Prox-II[^\n]*?Len:\s*(\d+)/i, kind: "dec" },
      { k: "fc", re: /G-Prox-II[^\n]*?FC:\s*(\d+)/i, kind: "dec" },
      { k: "cn", re: /G-Prox-II[^\n]*?Card:\s*(\d+)/i, kind: "dec" },
      { k: "xor", re: /G-Prox-II[^\n]*?xor:\s*(\d+)/i, kind: "dec" },
    ] },
];
