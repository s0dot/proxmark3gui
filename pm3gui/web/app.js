/* Proxmark3 GUI front-end */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  connected: false,
  mode: "offline",
  busy: false,
  lastSeq: 0,
  lastHadEvents: false,
  history: [],
  histIdx: -1,
  capture: null, // {cmdSeq, lines:[], targetId, fill}
  clientFound: false,
};
let pollTimer = null;

/* ------------------------------------------------------------------ *
 *  Boot
 * ------------------------------------------------------------------ */
window.addEventListener("DOMContentLoaded", () => {
  wireNav();
  wireConsole();
  wireConnect();
  wireActions();
  wireLfForm();
  wireWriteForms();
  wireSettings();
  wireLaunchers();
  loadPorts();
  syncStatus(); // resume if the server already has a live session (e.g. after refresh)
});

async function syncStatus() {
  try {
    const st = await (await fetch("/api/status")).json();
    applyStatus(st);
    if (st.connected) {
      state.lastSeq = 0; // replay buffered session output into the console
      wakePoll();
    }
  } catch (e) {}
}

/* ------------------------------------------------------------------ *
 *  Navigation between panels
 * ------------------------------------------------------------------ */
function wireNav() {
  $$(".nav-item").forEach((b) =>
    b.addEventListener("click", () => {
      $$(".nav-item").forEach((x) => x.classList.remove("active"));
      $$(".panel").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      $("#panel-" + b.dataset.panel).classList.add("active");
    })
  );
}

/* ------------------------------------------------------------------ *
 *  Ports & connection
 * ------------------------------------------------------------------ */
async function loadPorts() {
  let data;
  try {
    data = await (await fetch("/api/ports")).json();
  } catch (e) {
    return;
  }
  state.clientFound = data.client_found;
  state.isWindows = data.is_windows;

  // Chameleon Ultra companion-app launch button
  state.chameleonAvailable = data.chameleon_available;
  const cbtn = $("#launchChameleon");
  if (cbtn) {
    cbtn.disabled = !data.chameleon_available;
    cbtn.title = data.chameleon_available
      ? "Open: " + data.chameleon_path
      : "Chameleon Ultra GUI not found (add a chameleon/ folder with chameleonultragui.exe)";
  }
  // surface what the server auto-detected inside the settings popover
  $("#cfgClientDetected").textContent = data.client_path
    ? "✓ " + shortenPath(data.client_path)
    : "(not auto-found)";
  $("#cfgRuntimeDetected").textContent = data.runtime_dir
    ? "✓ " + shortenPath(data.runtime_dir)
    : (data.is_windows ? "(not auto-found)" : "");

  // populate the discovered-clients dropdown
  const csel = $("#cfgClientSelect");
  const chosen = $("#cfgClientPath").value;
  csel.innerHTML = "";
  const optAuto = document.createElement("option");
  optAuto.value = "";
  optAuto.textContent = data.client_path ? "Auto: " + shortenPath(data.client_path) : "Auto-detect";
  csel.appendChild(optAuto);
  (data.clients || []).forEach((c) => {
    const o = document.createElement("option");
    o.value = c;
    o.textContent = shortenPath(c);
    if (c === chosen) o.selected = true;
    csel.appendChild(o);
  });

  const sel = $("#portSel");
  sel.innerHTML = "";

  (data.ports || []).forEach((p) => {
    const o = document.createElement("option");
    o.value = p.device;
    o.textContent = `${p.device} — ${p.description}` + (p.is_pm3 ? "  ★" : "");
    if (p.is_pm3) o.dataset.pm3 = "1";
    sel.appendChild(o);
  });

  const demo = document.createElement("option");
  demo.value = "__demo__";
  demo.textContent = "Demo device (no hardware)";
  sel.appendChild(demo);

  // Prefer a real PM3 port; otherwise demo.
  const pm3opt = sel.querySelector('option[data-pm3="1"]');
  sel.value = pm3opt ? pm3opt.value : "__demo__";

  const note = $("#clientNote");
  if (data.client_found) {
    note.innerHTML = "client&nbsp;✓<br><span style='color:var(--green)'>ready for live use</span>";
  } else {
    note.innerHTML =
      "client not found<br>running in <b style='color:var(--yellow)'>demo</b> mode<br>" +
      "<span style='opacity:.7'>compile client/ to go live</span>";
  }
}

function wireConnect() {
  $("#refreshPorts").addEventListener("click", loadPorts);
  $("#connectBtn").addEventListener("click", () => {
    state.connected ? disconnect() : connect();
  });
}

function wireLaunchers() {
  const btn = $("#launchChameleon");
  btn.addEventListener("click", async () => {
    if (btn.disabled) return;
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="ni">&#129422;</span> Opening…';
    try {
      const r = await postJSON("/api/launch/chameleon", {});
      if (r.error) throw new Error(r.error);
      appendConsole(
        "[+] Chameleon Ultra GUI " + (r.result === "already running" ? "is already open" : "launched"),
        "sys"
      );
    } catch (e) {
      appendConsole("[!] Could not open Chameleon GUI: " + e.message, "sys");
    } finally {
      setTimeout(() => {
        btn.innerHTML = orig;
        btn.disabled = !state.chameleonAvailable;
      }, 900);
    }
  });
}

function wireSettings() {
  const panel = $("#settingsPanel");
  const btn = $("#settingsBtn");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    panel.classList.toggle("hidden");
  });
  $("#cfgClose").addEventListener("click", () => panel.classList.add("hidden"));
  $("#cfgClientSelect").addEventListener("change", (e) => {
    $("#cfgClientPath").value = e.target.value;
  });
  $("#cfgSave").addEventListener("click", () => {
    localStorage.setItem("pm3.clientPath", $("#cfgClientPath").value.trim());
    localStorage.setItem("pm3.runtimeDir", $("#cfgRuntimeDir").value.trim());
    panel.classList.add("hidden");
    appendConsole("[=] Client settings saved", "sys");
  });
  // dismiss on outside click
  document.addEventListener("click", (e) => {
    if (!panel.classList.contains("hidden") && !panel.contains(e.target) && e.target !== btn) {
      panel.classList.add("hidden");
    }
  });
  // restore saved values
  $("#cfgClientPath").value = localStorage.getItem("pm3.clientPath") || "";
  $("#cfgRuntimeDir").value = localStorage.getItem("pm3.runtimeDir") || "";
}

async function connect() {
  const port = $("#portSel").value;
  const clientPath = ($("#cfgClientPath").value || "").trim();
  const runtimeDir = ($("#cfgRuntimeDir").value || "").trim();
  const hasClient = state.clientFound || clientPath.length > 0;
  const demo = port === "__demo__" || !hasClient;
  document.body.classList.add("connecting");
  $("#statusText").textContent = "Connecting…";
  try {
    const r = await postJSON("/api/connect", {
      port,
      demo,
      client_path: clientPath || undefined,
      runtime_dir: runtimeDir || undefined,
    });
    if (r.error) throw new Error(r.error);
    applyStatus(r.status);
    wakePoll();
    // greet: pull version into dashboard
    setTimeout(() => runCmd("hw version", { capture: "dashResult" }), 350);
  } catch (e) {
    appendConsole("[!] connect failed: " + e.message, "sys");
    if (/dll|runtime/i.test(e.message)) appendConsole("[?] Tip: open ⚙ Settings and set the Runtime folder.", "sys");
  } finally {
    document.body.classList.remove("connecting");
    if (!state.connected) $("#statusText").textContent = "Disconnected";
  }
}

async function disconnect() {
  try {
    const r = await postJSON("/api/disconnect", {});
    applyStatus(r.status);
  } catch (e) {}
}

function applyStatus(st) {
  if (!st) return;
  state.connected = !!st.connected;
  state.mode = st.mode || "offline";
  document.body.classList.toggle("connected", state.connected);
  document.body.classList.toggle("disconnected", !state.connected);

  $("#statusText").textContent = state.connected
    ? `Connected · ${st.port || ""}`
    : "Disconnected";
  $("#connectBtn").textContent = state.connected ? "Disconnect" : "Connect";
  $("#connectBtn").classList.toggle("btn-primary", !state.connected);
  $("#portSel").disabled = state.connected;

  const badge = $("#modeBadge");
  if (state.connected && state.mode !== "offline") {
    badge.className = "badge " + state.mode;
    badge.textContent = state.mode === "live" ? "LIVE" : "DEMO";
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }

  const dis = !state.connected;
  $("#cmdInput").disabled = dis;
  $("#runBtn").disabled = dis;
  if (st.busy !== undefined) {
    state.busy = !!st.busy;
    $("#busyDot").classList.toggle("hidden", !st.busy);
  }
}

/* ------------------------------------------------------------------ *
 *  Output stream — adaptive polling.
 *  While something is happening we hold the request open (live, snappy);
 *  when idle we return at once and poll slowly so the page can settle.
 * ------------------------------------------------------------------ */
function schedulePoll(delay) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(doPoll, delay);
}
function wakePoll() {
  state.lastHadEvents = true; // bias next poll toward "active"
  schedulePoll(0);
}
async function doPoll() {
  const active = state.busy || state.lastHadEvents;
  const wait = active ? 20 : 0;
  try {
    const res = await fetch(`/api/output?since=${state.lastSeq}&wait=${wait}`);
    const data = await res.json();
    const evs = data.events || [];
    state.lastHadEvents = evs.length > 0;
    evs.forEach(handleEvent);
    if (typeof data.last === "number") state.lastSeq = data.last;
    if (data.status) applyStatus(data.status);
    if (state.connected) {
      schedulePoll(state.busy || state.lastHadEvents ? 0 : 1200);
    } else {
      clearTimeout(pollTimer); // idle on disconnect so the page can settle
      pollTimer = null;
    }
  } catch (e) {
    if (state.connected) schedulePoll(1500); // server momentarily unavailable
  }
}

function handleEvent(ev) {
  if (ev.kind === "output") {
    appendConsole(ev.text, ev.stream || "");
    if (state.capture) state.capture.lines.push(ev.text);
  } else if (ev.kind === "done") {
    if (state.capture && ev.cmd_seq === state.capture.cmdSeq) {
      finishCapture(state.capture);
      state.capture = null;
    }
  } else if (ev.kind === "status") {
    applyStatus(ev);
  }
}

/* ------------------------------------------------------------------ *
 *  Console rendering
 * ------------------------------------------------------------------ */
function wireConsole() {
  $("#clearConsole").addEventListener("click", () => ($("#consoleBody").innerHTML = ""));

  const form = $("#cmdForm");
  const input = $("#cmdInput");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const cmd = input.value.trim();
    if (!cmd) return;
    state.history.push(cmd);
    state.histIdx = state.history.length;
    input.value = "";
    runCmd(cmd);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowUp") {
      if (state.histIdx > 0) input.value = state.history[--state.histIdx] || "";
      e.preventDefault();
    } else if (e.key === "ArrowDown") {
      if (state.histIdx < state.history.length - 1)
        input.value = state.history[++state.histIdx] || "";
      else {
        state.histIdx = state.history.length;
        input.value = "";
      }
      e.preventDefault();
    }
  });
}

function lineLevel(text) {
  const t = text.replace(/\x1b\[[0-9;]*m/g, "");
  if (t.startsWith("pm3 -->") || t.startsWith("pm3 »")) return "cmd";
  const m = t.match(/^\s*\[([+=!\-?#])\]/);
  if (m)
    return { "+": "ok", "=": "info", "!": "warn", "-": "err", "?": "hint", "#": "debug" }[m[1]];
  return "";
}

const ANSI_MAP = {
  30: "a-k", 31: "a-r", 32: "a-g", 33: "a-y", 34: "a-b", 35: "a-m", 36: "a-c", 37: "a-w",
  90: "a-k", 91: "a-r", 92: "a-g", 93: "a-y", 94: "a-b", 95: "a-m", 96: "a-c", 97: "a-w",
};
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function ansiToHtml(s) {
  let out = "",
    fg = "",
    bold = false,
    i = 0;
  const re = /\x1b\[([0-9;]*)m/g;
  let m;
  const flush = (txt) => {
    if (!txt) return "";
    const cls = [fg, bold ? "a-bold" : ""].filter(Boolean).join(" ");
    return cls ? `<span class="${cls}">${esc(txt)}</span>` : esc(txt);
  };
  while ((m = re.exec(s))) {
    out += flush(s.slice(i, m.index));
    i = re.lastIndex;
    const codes = m[1] === "" ? [0] : m[1].split(";").map(Number);
    codes.forEach((c) => {
      if (c === 0) {
        fg = "";
        bold = false;
      } else if (c === 1) bold = true;
      else if (c === 22) bold = false;
      else if (ANSI_MAP[c]) fg = ANSI_MAP[c];
      else if (c === 39) fg = "";
    });
  }
  out += flush(s.slice(i));
  return out;
}

function appendConsole(text, stream) {
  const body = $("#consoleBody");
  const div = document.createElement("div");
  let level = stream && stream !== "cmd" ? "sys" : "";
  if (stream === "cmd") level = "cmd";
  if (!level) level = lineLevel(text);
  div.className = "ln" + (level ? " ln-" + level : "");
  div.innerHTML = ansiToHtml(text) || "&nbsp;";
  body.appendChild(div);

  // trim very long buffers
  while (body.childNodes.length > 4000) body.removeChild(body.firstChild);

  if ($("#autoscroll").checked) body.scrollTop = body.scrollHeight;
}

/* ------------------------------------------------------------------ *
 *  Command dispatch + capture
 * ------------------------------------------------------------------ */
async function runCmd(cmd, opts = {}) {
  if (!state.connected) {
    appendConsole("[!] Not connected. Click Connect first.", "sys");
    return;
  }
  try {
    const r = await postJSON("/api/command", { cmd });
    if (r.error) throw new Error(r.error);
    if (opts.capture || opts.onDone) {
      state.capture = {
        cmdSeq: r.cmd_seq,
        lines: [],
        targetId: opts.capture,
        fill: opts.fill,
        onDone: opts.onDone,
      };
    }
    if (r.status) applyStatus(r.status);
    wakePoll();
  } catch (e) {
    appendConsole("[!] " + e.message, "sys");
  }
}

function wireActions() {
  $$(".cmd-btn").forEach((b) =>
    b.addEventListener("click", () => {
      runCmd(b.dataset.cmd, { capture: b.dataset.capture, fill: b.dataset.fill });
    })
  );
}

/* ------------------------------------------------------------------ *
 *  Result extraction (guided-workflow flavour)
 * ------------------------------------------------------------------ */
function finishCapture(cap) {
  const clean = cap.lines.map((l) => l.replace(/\x1b\[[0-9;]*m/g, ""));
  if (cap.targetId) renderFacts(cap, clean);
  if (cap.onDone) {
    try {
      cap.onDone(clean);
    } catch (e) {
      appendConsole("[!] verify error: " + e.message, "sys");
    }
  }
}

function renderFacts(cap, clean) {
  const target = document.getElementById(cap.targetId);
  if (!target) return;
  const found = extractFacts(clean);

  if (cap.fill === "em") {
    const id = factValue(found, "EM410x ID");
    if (id) $("#emId").value = id.replace(/\s/g, "");
  }
  if (cap.fill === "hid") {
    const fc = factValue(found, "Facility");
    const cn = factValue(found, "Card #");
    if (fc) $("#hidFc").value = fc;
    if (cn) $("#hidCn").value = cn;
    if (fc || cn) $("#lfType").value = "hid", toggleLfFields();
  }

  target.classList.remove("hidden");
  if (!found.length) {
    target.innerHTML = `<h4>Result</h4><div class="result-empty">Done — see console output below.</div>`;
    return;
  }
  const chips = found
    .map(
      (f) =>
        `<div class="chip${f.ok ? " ok" : ""}"><span class="k">${esc(f.k)}</span><span class="v">${esc(
          f.v
        )}</span></div>`
    )
    .join("");
  target.innerHTML = `<h4>Result</h4><div class="chips">${chips}</div>`;
}

function factValue(facts, key) {
  const f = facts.find((x) => x.k === key);
  return f ? f.v : null;
}

function extractFacts(lines) {
  const facts = [];
  const add = (k, v, ok) => {
    if (v && !facts.some((f) => f.k === k)) facts.push({ k, v: v.trim(), ok: !!ok });
  };
  for (const ln of lines) {
    let m;
    if ((m = ln.match(/EM\s*410x\s*ID\s*([0-9A-Fa-f]{8,})/))) add("EM410x ID", m[1], true);
    if ((m = ln.match(/\bUID:\s*([0-9A-Fa-f ]{6,})/))) add("UID", m[1], true);
    if ((m = ln.match(/\bATQA:\s*([0-9A-Fa-f ]{2,})/))) add("ATQA", m[1]);
    if ((m = ln.match(/\bSAK:\s*([0-9A-Fa-f]{2}(?:\s*\[\d\])?)/))) add("SAK", m[1]);
    if ((m = ln.match(/\bCSN:\s*([0-9A-Fa-f ]{8,})/))) add("CSN", m[1], true);
    if ((m = ln.match(/FC:\s*(\d+)\s+CN:\s*(\d+)/))) {
      add("Facility", m[1]);
      add("Card #", m[2]);
    }
    if ((m = ln.match(/Chip type[.\s]*([A-Za-z0-9x/ ]+)/))) add("Chip", m[1]);
    if ((m = ln.match(/Possible types?:\s*(.+)/))) add("Type", m[1]);
    if (/Valid .*tag found/i.test(ln)) {
      const t = ln.replace(/^\s*\[\+\]\s*/, "").replace(/\.*$/, "");
      add("Status", t, true);
    }
    if (/LF antenna:.*125/.test(ln) && (m = ln.match(/([\d.]+)\s*V/))) add("LF antenna", m[1] + " V");
    if (/HF antenna:.*13\.56/.test(ln) && (m = ln.match(/([\d.]+)\s*V/))) add("HF antenna", m[1] + " V");
    if ((m = ln.match(/Saved .* to .*file\s+(\S+)/))) add("Saved", m[1], true);
    if ((m = ln.match(/found\s+(\d+\s*\/\s*\d+)\s+keys/))) add("Keys", m[1], true);
    if ((m = ln.match(/\bClient\.{2,}\s*(.+)/))) add("Client", m[1]);
    if ((m = ln.match(/\bTarget\.{2,}\s*(.+)/))) add("Target", m[1]);
    if ((m = ln.match(/\bMCU\.{2,}\s*(.+)/))) add("MCU", m[1]);
    // real `hw version` output (device)
    if ((m = ln.match(/\bOS\.{2,}\s*(\S+)/))) add("Firmware", m[1]);
    if ((m = ln.match(/uC:\s*([A-Za-z0-9]+(?:\s*Rev\s*\w)?)/))) add("MCU", m[1]);
    if ((m = ln.match(/Embedded flash memory\s*([0-9KMG]+)\s*bytes\s*\(\s*(\d+%)/)))
      add("Flash", m[1] + " (" + m[2] + " used)", true);
    if (/\bDone!/.test(ln) || /\bcomplete\b/i.test(ln)) add("Status", "Done", true);
  }
  return facts;
}

/* ------------------------------------------------------------------ *
 *  LF clone form
 * ------------------------------------------------------------------ */
function wireLfForm() {
  $("#lfType").addEventListener("change", toggleLfFields);
  $("#lfWriteBtn").addEventListener("click", () => {
    const type = $("#lfType").value;
    let spec;
    if (type === "em") {
      const id = $("#emId").value.trim();
      if (!/^[0-9A-Fa-f]{10}$/.test(id)) {
        appendConsole("[!] EM ID must be 10 hex characters", "sys");
        return;
      }
      const ID = id.toUpperCase();
      spec = { writeCmd: `lf em 410x clone --id ${ID}`, readCmd: "lf em 410x reader",
               expect: ID, kind: "hex", targetId: "lfResult", label: `EM410x ID ${ID}` };
    } else {
      const fc = $("#hidFc").value.trim(),
        cn = $("#hidCn").value.trim();
      if (!fc || !cn) {
        appendConsole("[!] HID needs Facility and Card #", "sys");
        return;
      }
      spec = { writeCmd: `lf hid clone -w H10301 --fc ${fc} --cn ${cn}`, readCmd: "lf hid read",
               expect: `FC ${fc} CN ${cn}`, kind: "hidfccn", targetId: "lfResult", label: `HID FC ${fc} CN ${cn}` };
    }
    writeVerify(spec);
  });
  toggleLfFields();
}
function toggleLfFields() {
  const t = $("#lfType").value;
  $("#emFields").classList.toggle("hidden", t !== "em");
  $("#hidFields").classList.toggle("hidden", t !== "hid");
}

/* ------------------------------------------------------------------ *
 *  Write forms (LF raw T5577 + HF MIFARE / magic / Ultralight)
 * ------------------------------------------------------------------ */
const hexN = (s, n) => new RegExp(`^[0-9A-Fa-f]{${n}}$`).test((s || "").trim());
const isInt = (s) => /^\d+$/.test((s || "").trim());
function warn(msg) {
  appendConsole("[!] " + msg, "sys");
  return false;
}

function wireWriteForms() {
  // ---- LF: raw T5577 block write ----------------------------------- //
  $("#t5WriteBtn").addEventListener("click", () => {
    const blk = $("#t5Block").value;
    const data = $("#t5Data").value.trim();
    const pwd = $("#t5Pwd").value.trim();
    if (!hexN(data, 8)) return warn("T5577 data must be 8 hex chars (4 bytes)");
    if (pwd && !hexN(pwd, 8)) return warn("T5577 password must be 8 hex chars");
    if (blk === "0" && !confirm("Block 0 sets the T5577 config/modulation. A wrong value can make the tag unreadable.\n\nWrite block 0?")) return;
    let cmd = `lf t55xx write -b ${blk} -d ${data.toUpperCase()}`;
    if (pwd) cmd += ` -p ${pwd.toUpperCase()}`;
    if ($("#t5Verify").checked) cmd += " --verify";
    const readCmd = `lf t55xx read -b ${blk}` + (pwd ? ` -p ${pwd.toUpperCase()}` : "");
    writeVerify({ writeCmd: cmd, readCmd, expect: data.toUpperCase(), kind: "hex",
                  targetId: "lfResult", label: `T5577 block ${blk}` });
  });

  // ---- HF: MIFARE Classic block write ------------------------------ //
  $("#mfWriteBtn").addEventListener("click", () => {
    const blk = $("#mfwBlk").value.trim();
    const key = $("#mfwKey").value.trim();
    const data = $("#mfwData").value.trim();
    if (!isInt(blk)) return warn("Block must be a number");
    if (!hexN(key, 12)) return warn("Key must be 12 hex chars (6 bytes)");
    if (!hexN(data, 32)) return warn("Data must be 32 hex chars (16 bytes)");
    if (blk === "0" && !confirm("Block 0 is the manufacturer block (UID/BCC/SAK/ATQA). Bad data can brick a Magic Gen2 card.\n\nWrite block 0?")) return;
    const kt = $("#mfwKeyType").value === "b" ? "-b" : "-a";
    let cmd = `hf mf wrbl --blk ${blk} ${kt} -k ${key.toUpperCase()} -d ${data.toUpperCase()}`;
    if ($("#mfwForce").checked) cmd += " --force";
    const readCmd = `hf mf rdbl --blk ${blk} ${kt} -k ${key.toUpperCase()}`;
    writeVerify({ writeCmd: cmd, readCmd, expect: data.toUpperCase(), kind: "hex",
                  targetId: "hfwResult", label: `MIFARE block ${blk}` });
  });

  // ---- HF: magic Gen1a set UID ------------------------------------- //
  $("#magUidBtn").addEventListener("click", () => {
    const uid = $("#magUid").value.trim();
    const atqa = $("#magAtqa").value.trim();
    const sak = $("#magSak").value.trim();
    if (!hexN(uid, 8) && !hexN(uid, 14)) return warn("UID must be 8 or 14 hex chars (4 or 7 bytes)");
    if (atqa && !hexN(atqa, 4)) return warn("ATQA must be 4 hex chars");
    if (sak && !hexN(sak, 2)) return warn("SAK must be 2 hex chars");
    if (!confirm(`Set magic-card UID to ${uid.toUpperCase()}? (magic Gen1a cards only)`)) return;
    let cmd = `hf mf csetuid -u ${uid.toUpperCase()}`;
    if (atqa) cmd += ` -a ${atqa.toUpperCase()}`;
    if (sak) cmd += ` -s ${sak.toUpperCase()}`;
    writeVerify({ writeCmd: cmd, readCmd: "hf 14a info", expect: uid.toUpperCase(), kind: "hex",
                  targetId: "hfwResult", label: `UID ${uid.toUpperCase()}` });
  });

  // ---- HF: Ultralight / NTAG write page ---------------------------- //
  $("#muWriteBtn").addEventListener("click", () => {
    const page = $("#muPage").value.trim();
    const data = $("#muData").value.trim();
    const key = $("#muKey").value.trim();
    if (!isInt(page)) return warn("Page must be a number");
    if (!hexN(data, 8)) return warn("Data must be 8 hex chars (4 bytes)");
    if (key && !hexN(key, 8) && !hexN(key, 32)) return warn("Key must be 8 or 32 hex chars");
    if (parseInt(page, 10) <= 3 && !confirm(`Pages 0-2 are UID/lock bytes; page 3 is OTP (one-time, irreversible).\n\nWrite page ${page}?`)) return;
    let cmd = `hf mfu wrbl -b ${page} -d ${data.toUpperCase()}`;
    if (key) cmd += ` -k ${key.toUpperCase()}`;
    const readCmd = `hf mfu rdbl -b ${page}` + (key ? ` -k ${key.toUpperCase()}` : "");
    writeVerify({ writeCmd: cmd, readCmd, expect: data.toUpperCase(), kind: "hex",
                  targetId: "hfwResult", label: `Ultralight page ${page}` });
  });
}

/* ------------------------------------------------------------------ *
 *  Write → read-back → compare verification
 * ------------------------------------------------------------------ */
const READ_FAIL = /no (tag|card|answer|known)|couldn'?t read|can'?t read|cannot read|auth\w*\s*(failed|error)|read error|tag removed|timeout|0\s*\/\s*\d+\s*blocks|operation failed|failed to/i;

function normHex(s) {
  return (s || "").replace(/[^0-9a-fA-F]/g, "").toUpperCase();
}

function writeVerify(spec) {
  runCmd(spec.writeCmd, {
    capture: spec.targetId,
    onDone: () => {
      appendConsole("[=] Verifying — reading the tag back to compare…", "sys");
      runCmd(spec.readCmd, {
        onDone: (readLines) => {
          const v = compareReadback(readLines, spec.expect, spec.kind);
          renderVerify(spec.targetId, { label: spec.label, expect: spec.expect, ...v });
        },
      });
    },
  });
}

function compareReadback(lines, expect, kind) {
  const text = lines.join("\n");
  if (kind === "hidfccn") {
    const m = text.match(/FC:?\s*(\d+)[^]*?CN:?\s*(\d+)/i);
    if (!m) return { status: READ_FAIL.test(text) ? "unverified" : "mismatch", got: "(not read)" };
    const got = `FC ${m[1]} CN ${m[2]}`;
    return { status: got === expect ? "ok" : "mismatch", got };
  }
  // hex-based: did the value we wrote show up when reading back?
  const want = normHex(expect);
  if (want && normHex(text).includes(want)) return { status: "ok", got: expect };
  const got = extractReadValue(lines, kind);
  if (!got || READ_FAIL.test(text)) return { status: "unverified", got: got || "(couldn't read the tag)" };
  return { status: "mismatch", got };
}

function extractReadValue(lines) {
  const text = lines.join("\n");
  let m;
  if ((m = text.match(/EM\s*410x\s*ID\s*([0-9A-Fa-f]+)/i))) return m[1].toUpperCase();
  if ((m = text.match(/\bUID:\s*([0-9A-Fa-f ]{6,})/i))) return m[1].trim().toUpperCase();
  if ((m = text.match(/\|\s*([0-9A-Fa-f][0-9A-Fa-f ]{6,46}[0-9A-Fa-f])\s*\|/))) return m[1].replace(/\s+/g, " ").toUpperCase();
  if ((m = text.match(/\b\d{1,2}\s*\|\s*([0-9A-Fa-f]{8})\b/))) return m[1].toUpperCase();
  return null;
}

function renderVerify(targetId, v) {
  const el = document.getElementById(targetId);
  if (!el) return;
  el.classList.remove("hidden");
  const map = {
    ok: { cls: "vr-ok", icon: "&#10004;", head: "Write verified" },
    mismatch: { cls: "vr-bad", icon: "&#10008;", head: "Verify FAILED — mismatch" },
    unverified: { cls: "vr-warn", icon: "&#9888;", head: "Couldn't read back to verify" },
  };
  const m = map[v.status] || map.unverified;
  appendConsole(
    (v.status === "ok" ? "[+] " : v.status === "mismatch" ? "[!] " : "[?] ") + m.head + " — " + v.label,
    "sys"
  );
  el.innerHTML =
    `<h4>Verify — ${esc(v.label)}</h4>` +
    `<div class="verify ${m.cls}"><span class="vr-icon">${m.icon}</span>` +
    `<div class="vr-body"><div class="vr-head">${m.head}</div>` +
    `<div class="vr-cmp"><span class="vr-k">wrote</span><code>${esc(v.expect)}</code></div>` +
    `<div class="vr-cmp"><span class="vr-k">read back</span><code>${esc(v.got)}</code></div>` +
    `</div></div>`;
  el.scrollIntoView({ block: "nearest" });
}

/* ------------------------------------------------------------------ *
 *  utils
 * ------------------------------------------------------------------ */
async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}
function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
function shortenPath(p) {
  if (!p) return "";
  return p.length > 38 ? "…" + p.slice(-37) : p;
}
