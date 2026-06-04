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
  wireSettings();
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
    if (opts.capture) {
      state.capture = {
        cmdSeq: r.cmd_seq,
        lines: [],
        targetId: opts.capture,
        fill: opts.fill,
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
  const target = document.getElementById(cap.targetId);
  if (!target) return;
  const clean = cap.lines.map((l) => l.replace(/\x1b\[[0-9;]*m/g, ""));
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
    let cmd;
    if (type === "em") {
      const id = $("#emId").value.trim();
      if (!/^[0-9A-Fa-f]{10}$/.test(id)) {
        appendConsole("[!] EM ID must be 10 hex characters", "sys");
        return;
      }
      cmd = `lf em 410x clone --id ${id.toUpperCase()}`;
    } else {
      const fc = $("#hidFc").value.trim(),
        cn = $("#hidCn").value.trim();
      if (!fc || !cn) {
        appendConsole("[!] HID needs Facility and Card #", "sys");
        return;
      }
      cmd = `lf hid clone -w H10301 --fc ${fc} --cn ${cn}`;
    }
    runCmd(cmd, { capture: "lfResult" });
  });
  toggleLfFields();
}
function toggleLfFields() {
  const t = $("#lfType").value;
  $("#emFields").classList.toggle("hidden", t !== "em");
  $("#hidFields").classList.toggle("hidden", t !== "hid");
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
