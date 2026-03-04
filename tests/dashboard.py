# =============================================================================
# DCS AI Radio - Web Dashboard
#
# Run: python dashboard.py
# Opens: http://localhost:5050
#
# Requires: pip install flask
# =============================================================================

import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, request, Response
from radio_settings import DEFAULTS, SETTINGS_FILE, get_settings, save_settings, ensure_defaults

# =============================================================================
# PATHS
# =============================================================================
TESTS_DIR   = Path(__file__).parent
SHARED_DIR  = Path(os.environ["USERPROFILE"]) / "Saved Games" / "DCS" / "dcs-ai-radio"
STATE_FILE  = SHARED_DIR / "state.json"
MISSION_LOG = SHARED_DIR / "mission_log.txt"
CACHE_FILE  = TESTS_DIR / "routing_cache.json"

ensure_defaults()

# =============================================================================
# PIPELINE PROCESS
# =============================================================================
_proc:     subprocess.Popen | None = None
_proc_lock = threading.Lock()
_log: deque = deque(maxlen=800)


def _capture(proc: subprocess.Popen):
    try:
        for line in proc.stdout:
            _log.append(line.rstrip())
    except Exception:
        pass
    _log.append("[dashboard] Pipeline process exited.")


# =============================================================================
# FLASK APP
# =============================================================================
app = Flask(__name__)


@app.route("/")
def index():
    return Response(_DASHBOARD_HTML, mimetype="text/html")


# ---- Pipeline control ----

@app.route("/api/pipeline/start", methods=["POST"])
def api_start():
    global _proc
    with _proc_lock:
        if _proc and _proc.poll() is None:
            return jsonify({"error": "already running"})
        _log.clear()
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
        try:
            _proc = subprocess.Popen(
                [sys.executable, "-u", str(TESTS_DIR / "pipelinev3.py")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(TESTS_DIR),
                env=env,
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        threading.Thread(target=_capture, args=(_proc,), daemon=True).start()
    return jsonify({"ok": True, "pid": _proc.pid})


@app.route("/api/pipeline/stop", methods=["POST"])
def api_stop():
    global _proc
    with _proc_lock:
        if _proc:
            try:
                _proc.stdin.write("quit\n")
                _proc.stdin.flush()
                _proc.wait(timeout=8)
            except Exception:
                _proc.terminate()
            _proc = None
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    with _proc_lock:
        if _proc is None:
            running, pid = False, None
        else:
            running = _proc.poll() is None
            pid     = _proc.pid if running else None
    return jsonify({"pipeline_running": running, "pid": pid})


def _write_stdin(text: str):
    """Write a line to the pipeline subprocess stdin."""
    with _proc_lock:
        if _proc and _proc.poll() is None:
            _proc.stdin.write(text + "\n")
            _proc.stdin.flush()
            return True
    return False


@app.route("/api/transmit", methods=["POST"])
def api_transmit():
    text = (request.json or {}).get("text", "").strip()
    if not text:
        return jsonify({"error": "empty"}), 400
    if _write_stdin(text):
        return jsonify({"ok": True})
    return jsonify({"error": "pipeline not running"}), 400


@app.route("/api/transmit/mic", methods=["POST"])
def api_transmit_mic():
    if _write_stdin("__MIC__"):
        return jsonify({"ok": True})
    return jsonify({"error": "pipeline not running"}), 400


# ---- Log ----

@app.route("/api/pipeline_log")
def api_pipeline_log():
    offset = int(request.args.get("offset", 0))
    lines  = list(_log)
    return jsonify({"lines": lines[offset:], "total": len(lines)})


# ---- DCS data ----

@app.route("/api/state")
def api_state():
    try:
        return jsonify(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except Exception:
        return jsonify({"error": "no_data"})


@app.route("/api/mission_log")
def api_mission_log():
    try:
        lines = MISSION_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return jsonify({"lines": lines[-60:]})
    except Exception:
        return jsonify({"lines": []})


# ---- Routing cache ----

@app.route("/api/cache")
def api_cache():
    try:
        return jsonify(json.loads(CACHE_FILE.read_text(encoding="utf-8")))
    except Exception:
        return jsonify({})


@app.route("/api/cache/<path:key>", methods=["DELETE"])
def api_cache_delete(key):
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if key not in data:
            return jsonify({"error": "not found"}), 404
        del data[key]
        CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---- Settings ----

@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify(get_settings())


@app.route("/api/settings", methods=["PUT"])
def api_settings_put():
    data = request.json or {}
    save_settings(data)
    return jsonify({"ok": True})


# =============================================================================
# DASHBOARD HTML
# =============================================================================
_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DCS AI Radio</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f0f0f;--bg2:#161616;--bg3:#1c1c1c;
  --border:#252525;--border2:#2e2e2e;
  --accent:#3a8a3a;--accent2:#4aaa4a;
  --text:#c0c0c0;--dim:#666;--dim2:#444;
  --blue:#4488cc;--red:#cc4444;--green:#44aa44;--orange:#cc8833;
}
body{background:var(--bg);color:var(--text);
     font:13px/1.4 'Segoe UI',system-ui,sans-serif;
     height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* ---- HEADER ---- */
header{
  background:var(--bg2);border-bottom:1px solid var(--border);
  padding:7px 14px;display:flex;align-items:center;gap:10px;flex-shrink:0
}
.logo{font-size:14px;font-weight:700;color:var(--accent2);letter-spacing:.02em}
.dot{width:9px;height:9px;border-radius:50%;background:var(--dim);transition:.3s;flex-shrink:0}
.dot.on{background:var(--green);box-shadow:0 0 6px var(--green)}
#status-text{font-size:11px;color:var(--dim)}
.spacer{flex:1}
.dcs-badge{font-size:11px;color:var(--dim)}

/* ---- BUTTONS ---- */
button{
  padding:4px 11px;border:1px solid var(--border2);background:var(--bg3);
  color:var(--text);cursor:pointer;border-radius:3px;font-size:12px;
  font-family:inherit;transition:.15s;white-space:nowrap
}
button:hover{background:#232323}
.btn-green{border-color:#3a6a3a;color:#8dcc8d}
.btn-green:hover{background:#1a2a1a}
.btn-blue{border-color:#3a5a7a;color:#8db8cc}
.btn-blue:hover{background:#1a2a3a}
.btn-blue:disabled{opacity:.5;cursor:default}
.btn-red{border-color:#6a3a3a;color:#cc8d8d}
.btn-red:hover{background:#2a1a1a}
.btn-icon{padding:4px 8px;font-size:14px;border-color:transparent;background:transparent;color:var(--dim)}
.btn-icon:hover{color:var(--text);background:var(--bg3)}

/* ---- MAIN GRID ---- */
main{flex:1;display:grid;grid-template-columns:1fr 230px 300px;
     gap:1px;background:var(--border);min-height:0}
.panel{background:var(--bg);display:flex;flex-direction:column;min-height:0}
.ph{padding:5px 10px;font-size:10px;font-weight:700;color:var(--dim);
    text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid var(--border);
    flex-shrink:0;display:flex;align-items:center;gap:6px}
.ph .phx{font-weight:400;color:var(--dim2);margin-left:auto}
.pb{flex:1;overflow-y:auto;padding:8px 10px}
.pb::-webkit-scrollbar{width:4px}
.pb::-webkit-scrollbar-thumb{background:#2a2a2a;border-radius:2px}

/* ---- LOG PANEL ---- */
#log{font:11px/1.55 'Cascadia Code','Consolas',monospace;white-space:pre-wrap;word-break:break-all}
.ll{color:var(--text)}
.ls{color:var(--accent2)}
.lw{color:var(--orange)}
.le{color:var(--red)}
.la{color:var(--blue)}
.ld{color:var(--dim)}

/* ---- BATTLEFIELD ---- */
.row{display:flex;justify-content:space-between;align-items:center;
     padding:3px 0;border-bottom:1px solid #1a1a1a}
.rl{color:var(--dim);font-size:12px}
.rv{font-family:monospace;font-size:12px}
.rv.blue{color:var(--blue)}.rv.red{color:var(--red)}.rv.green{color:var(--green)}
.sect{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim2);margin:10px 0 3px}

/* ---- CACHE ---- */
.ce{padding:5px 6px;border-bottom:1px solid #1a1a1a;
    display:flex;justify-content:space-between;align-items:flex-start;gap:6px}
.ck{color:#88bbee;font:11px/1.3 monospace}
.cv{color:var(--dim);font-size:10px;margin-top:1px}
.ch{color:var(--dim2);font-size:10px}
.cd{padding:2px 7px;font-size:10px;border-color:#5a3a3a;color:#cc8888;flex-shrink:0}
.cd:hover{background:#2a1a1a}
.empty{color:var(--dim);text-align:center;margin-top:30px;font-size:12px}

/* ---- FOOTER ---- */
footer{border-top:1px solid var(--border);padding:8px 12px;
       display:flex;flex-direction:column;gap:5px;flex-shrink:0}
.tx-row{display:flex;gap:8px}
#tx{flex:1;padding:5px 10px;background:var(--bg2);border:1px solid var(--border2);
    color:var(--text);font:13px/1 inherit;border-radius:3px}
#tx:focus{outline:none;border-color:var(--accent)}
#mlog{font:10px/1.5 monospace;color:#3a3a3a;max-height:50px;
      overflow-y:auto;white-space:pre-wrap;word-break:break-all}

/* ---- SETTINGS MODAL ---- */
.modal-bg{
  display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);
  z-index:100;align-items:center;justify-content:center
}
.modal-bg.open{display:flex}
.modal{
  background:var(--bg2);border:1px solid var(--border2);border-radius:6px;
  width:420px;max-height:80vh;display:flex;flex-direction:column;
  box-shadow:0 8px 32px rgba(0,0,0,.6)
}
.modal-hdr{
  padding:10px 14px;font-size:13px;font-weight:700;color:var(--text);
  border-bottom:1px solid var(--border);display:flex;align-items:center;
  justify-content:space-between
}
.modal-body{flex:1;overflow-y:auto;padding:14px}
.modal-ftr{padding:10px 14px;border-top:1px solid var(--border);
           display:flex;justify-content:flex-end;gap:8px}

/* settings rows */
.set-section{font-size:10px;text-transform:uppercase;letter-spacing:.1em;
             color:var(--dim2);margin:14px 0 6px}
.set-section:first-child{margin-top:0}
.set-row{
  display:grid;grid-template-columns:90px 50px 1fr 38px;
  align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #1a1a1a
}
.set-role{font-size:12px;color:var(--text)}
/* toggle */
.tog{position:relative;display:inline-block;width:32px;height:18px}
.tog input{opacity:0;width:0;height:0}
.tog-sl{
  position:absolute;inset:0;background:#333;border-radius:18px;
  cursor:pointer;transition:.2s
}
.tog-sl:before{
  content:'';position:absolute;width:12px;height:12px;
  left:3px;top:3px;background:#888;border-radius:50%;transition:.2s
}
.tog input:checked + .tog-sl{background:#2a5a2a}
.tog input:checked + .tog-sl:before{transform:translateX(14px);background:var(--green)}
/* slider */
.vol-slider{width:100%;accent-color:var(--accent2)}
.vol-val{font-size:11px;color:var(--dim);text-align:right;font-family:monospace}
/* awacs range row */
.set-row-wide{
  display:grid;grid-template-columns:90px 1fr 50px;
  align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #1a1a1a
}
</style>
</head>
<body>

<!-- SETTINGS MODAL -->
<div class="modal-bg" id="modal-bg" onclick="if(event.target===this)closeSettings()">
  <div class="modal">
    <div class="modal-hdr">
      <span>Settings</span>
      <button class="btn-icon" onclick="closeSettings()">✕</button>
    </div>
    <div class="modal-body" id="settings-body">Loading…</div>
    <div class="modal-ftr">
      <button onclick="closeSettings()">Cancel</button>
      <button class="btn-green" onclick="saveSettings()">Save Settings</button>
    </div>
  </div>
</div>

<header>
  <span class="logo">DCS AI Radio</span>
  <div class="dot" id="dot"></div>
  <span id="status-text">Pipeline stopped</span>
  <button class="btn-green" id="btn-start" onclick="startPipeline()">▶ Start</button>
  <button class="btn-red"   id="btn-stop"  onclick="stopPipeline()" style="display:none">■ Stop</button>
  <span class="spacer"></span>
  <span class="dcs-badge" id="dcs-badge">DCS: —</span>
  <button class="btn-icon" onclick="openSettings()" title="Settings">⚙</button>
</header>

<main>
  <!-- LEFT: Pipeline log -->
  <div class="panel">
    <div class="ph">
      Pipeline Output
      <span class="phx" id="log-lines"></span>
      <button class="btn-icon" onclick="clearLog()" title="Clear log" style="margin-left:4px;padding:2px 5px;font-size:11px">✕</button>
    </div>
    <div class="pb" id="log-scroll"><div id="log"></div></div>
  </div>

  <!-- CENTER: Battlefield -->
  <div class="panel">
    <div class="ph">Battlefield</div>
    <div class="pb" id="bf"><div class="empty">Waiting for DCS…</div></div>
  </div>

  <!-- RIGHT: Routing Cache -->
  <div class="panel">
    <div class="ph">Routing Cache <span class="phx" id="cache-count"></span></div>
    <div class="pb" id="cache"><div class="empty">Empty — fills as you fly</div></div>
  </div>
</main>

<footer>
  <div class="tx-row">
    <input id="tx" placeholder="Type a transmission and press Enter…" autocomplete="off">
    <button class="btn-green" onclick="transmit()">Transmit</button>
    <button class="btn-blue" onclick="triggerMic()" id="btn-mic">Use Mic</button>
  </div>
  <div id="mlog"></div>
</footer>

<script>
// ================================================================
// STATE
// ================================================================
let logOffset  = 0;
let running    = false;
let autoScroll = true;

const $   = id => document.getElementById(id);
const esc = s  => String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const esq = s  => String(s??'').replace(/\\/g,'\\\\').replace(/'/g,"\\'");

async function api(path, opts={}) {
  try {
    const r = await fetch(path, opts);
    const text = await r.text();
    try { return JSON.parse(text); } catch { return {_raw: text}; }
  } catch(e) { return {_err: String(e)}; }
}

// ================================================================
// PIPELINE
// ================================================================
async function startPipeline() {
  appendLog('[dashboard] Starting pipeline…', 'ld');
  logOffset = 0;
  const r = await api('/api/pipeline/start', {method:'POST'});
  if (r?.ok) {
    appendLog(`[dashboard] Process started (PID ${r.pid})`, 'ls');
  } else {
    appendLog(`[dashboard] Start failed: ${r?.error || JSON.stringify(r)}`, 'le');
  }
  refreshStatus();
}

async function stopPipeline() {
  appendLog('[dashboard] Stopping pipeline…', 'ld');
  await api('/api/pipeline/stop', {method:'POST'});
  refreshStatus();
}

async function triggerMic() {
  appendLog('[dashboard] Triggering mic…', 'ld');
  const btn = $('btn-mic');
  btn.textContent = '🔴 Listening…';
  btn.disabled = true;
  const r = await api('/api/transmit/mic', {method:'POST'});
  if (!r?.ok) appendLog(`[dashboard] Mic trigger failed: ${r?.error || 'pipeline not running'}`, 'le');
  setTimeout(() => { btn.textContent = 'Use Mic'; btn.disabled = false; }, 3000);
}

async function transmit() {
  const el = $('tx'), text = el.value.trim();
  if (!text) return;
  el.value = '';
  appendLog(`[dashboard] Transmitting: "${text}"`, 'ld');
  const r = await api('/api/transmit', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({text})
  });
  if (!r?.ok) appendLog(`[dashboard] Transmit failed: ${r?.error || 'pipeline not running'}`, 'le');
}

// ================================================================
// LOG
// ================================================================
function appendLog(line, cls='ll') {
  const el = $('log');
  const div = document.createElement('div');
  div.className = cls + ' ll';
  div.textContent = line;
  el.appendChild(div);
  if (el.children.length > 800) el.removeChild(el.firstChild);
  if (autoScroll) {
    const s = $('log-scroll');
    s.scrollTop = s.scrollHeight;
  }
}

function classifyLine(l) {
  if (l.includes('[AWACS]'))              return 'la';
  if (l.includes('[dashboard]'))          return 'ld';
  if (l.includes('✓')||l.includes('✅')) return 'ls';
  if (l.includes('⚠'))                   return 'lw';
  if (l.includes('✗')||l.includes('ERROR')||/error:/i.test(l)) return 'le';
  return 'll';
}

function clearLog() {
  $('log').innerHTML = '';
  logOffset = 0;
  $('log-lines').textContent = '';
}

async function refreshLog() {
  const data = await api('/api/pipeline_log?offset=' + logOffset);
  if (!data || data._err) return;
  (data.lines || []).forEach(l => appendLog(l, classifyLine(l)));
  logOffset = data.total ?? logOffset;
  $('log-lines').textContent = logOffset ? `(${logOffset})` : '';
}

// ================================================================
// STATUS
// ================================================================
async function refreshStatus() {
  const s = await api('/api/status');
  if (!s || s._err) return;
  running = s.pipeline_running;
  $('dot').className        = 'dot' + (running ? ' on' : '');
  $('status-text').textContent = running ? `Running  PID ${s.pid}` : 'Stopped';
  $('btn-start').style.display = running ? 'none' : '';
  $('btn-stop').style.display  = running ? ''     : 'none';
}

// ================================================================
// BATTLEFIELD
// ================================================================
function fmtTime(sec) {
  const h=Math.floor(sec/3600), m=Math.floor(sec%3600/60), s=Math.floor(sec%60);
  return [h,m,s].map(v=>String(v).padStart(2,'0')).join(':');
}

async function refreshState() {
  const d = await api('/api/state');
  const el = $('bf'), badge = $('dcs-badge');
  if (!d || d.error || !d.player) {
    badge.textContent = 'DCS: offline';
    el.innerHTML = '<div class="empty">No DCS connection</div>';
    return;
  }
  badge.textContent = 'DCS: connected';
  const p   = d.player || {};
  const all = d.units  || [];
  const coa = (p.coalition||'').toLowerCase();
  const fri = all.filter(u => (u.coalition||'').toLowerCase() === coa);
  const hos = all.filter(u => {
    const c = (u.coalition||'').toLowerCase();
    return c && c !== coa && c !== 'neutral';
  });
  const altFt  = Math.round((p.altitude_asl||0) * 3.281);
  const spdKts = Math.round((p.ias_mps||0) * 1.944);
  el.innerHTML = `
    <div class="sect">Player</div>
    <div class="row"><span class="rl">Callsign</span><span class="rv">${esc(p.callsign)}</span></div>
    <div class="row"><span class="rl">Aircraft</span><span class="rv">${esc(p.type)}</span></div>
    <div class="row"><span class="rl">Coalition</span><span class="rv ${coa}">${esc(p.coalition)}</span></div>
    <div class="row"><span class="rl">Altitude</span><span class="rv">${altFt.toLocaleString()} ft</span></div>
    <div class="row"><span class="rl">Speed</span><span class="rv">${spdKts} kts</span></div>
    <div class="row"><span class="rl">Heading</span><span class="rv">${Math.round(p.heading||0)}°</span></div>
    <div class="row"><span class="rl">Fuel</span><span class="rv">${((p.fuel_internal||0)*100).toFixed(0)}%</span></div>
    <div class="sect">Battlefield</div>
    <div class="row"><span class="rl">Friendlies</span><span class="rv green">${fri.length}</span></div>
    <div class="row"><span class="rl">Hostiles</span><span class="rv red">${hos.length}</span></div>
    <div class="sect">Mission</div>
    <div class="row"><span class="rl">Time</span><span class="rv">${fmtTime(d.timestamp||0)}</span></div>
  `;
}

// ================================================================
// CACHE
// ================================================================
async function refreshCache() {
  const d = await api('/api/cache');
  if (!d) return;
  const keys = Object.keys(d);
  $('cache-count').textContent = keys.length ? `(${keys.length})` : '';
  if (!keys.length) {
    $('cache').innerHTML = '<div class="empty">Empty — fills as you fly</div>';
    return;
  }
  keys.sort((a,b) => (d[b].hits||0) - (d[a].hits||0));
  $('cache').innerHTML = keys.map(k => {
    const e = d[k];
    return `<div class="ce">
      <div style="min-width:0">
        <div class="ck">${esc(k)}</div>
        <div class="cv">→ ${esc(e.category)}/${esc(e.action)}</div>
        <div class="ch">hits: ${e.hits||0}</div>
      </div>
      <button class="cd" onclick="delCache('${esq(k)}')">✕</button>
    </div>`;
  }).join('');
}

async function delCache(key) {
  await api('/api/cache/' + encodeURIComponent(key), {method:'DELETE'});
  refreshCache();
}

// ================================================================
// MISSION LOG
// ================================================================
async function refreshMissionLog() {
  const d = await api('/api/mission_log');
  $('mlog').textContent = (d?.lines||[]).slice(-6).join('\n');
}

// ================================================================
// SETTINGS
// ================================================================
const ROLES = [
  {id:'atc',        label:'ATC'},
  {id:'jtac',       label:'JTAC'},
  {id:'wingman',    label:'Wingman'},
  {id:'ground_crew',label:'Ground Crew'},
  {id:'awacs',      label:'AWACS'},
];

async function openSettings() {
  const d = await api('/api/settings');
  const roles = (d && d.roles) || {};
  const awacs_nm = (d && d.awacs_range_nm) || 80;
  const awacs_dbg = (d && d.awacs_debug) || false;
  $('settings-body').innerHTML = `
    <div class="set-section">Voices &amp; Volume  <span style="color:#444;font-size:10px">(100% = default gain)</span></div>
    ${ROLES.map(r => {
      const cfg = roles[r.id] || {enabled:true,volume:100,speed:1.0};
      const spd = cfg.speed != null ? cfg.speed : 1.0;
      const spdPct = Math.round(spd * 100);
      return `<div class="set-row">
        <span class="set-role">${r.label}</span>
        <label class="tog">
          <input type="checkbox" id="en-${r.id}" ${cfg.enabled?'checked':''}>
          <span class="tog-sl"></span>
        </label>
        <input type="range" class="vol-slider" id="vol-${r.id}"
               min="0" max="200" value="${cfg.volume}"
               oninput="$('vv-${r.id}').textContent=this.value+'%'">
        <span class="vol-val" id="vv-${r.id}">${cfg.volume}%</span>
      </div>
      <div class="set-row" style="padding-top:0;margin-top:-4px">
        <span class="set-role" style="color:#666;font-size:10px">speed</span>
        <span style="width:36px"></span>
        <input type="range" class="vol-slider" id="spd-${r.id}"
               min="50" max="150" value="${spdPct}"
               oninput="$('sv-${r.id}').textContent=this.value+'%'">
        <span class="vol-val" id="sv-${r.id}">${spdPct}%</span>
      </div>`;
    }).join('')}
    <div class="set-section" style="margin-top:16px">AWACS Radar Range</div>
    <div class="set-row-wide">
      <span class="set-role">Range</span>
      <input type="range" class="vol-slider" id="awacs-nm"
             min="20" max="150" value="${awacs_nm}"
             oninput="$('vv-nm').textContent=this.value+' nm'">
      <span class="vol-val" id="vv-nm">${awacs_nm} nm</span>
    </div>
    <div class="set-section" style="margin-top:16px">Debug</div>
    <div class="set-row">
      <span class="set-role">Force AWACS alive</span>
      <label class="tog">
        <input type="checkbox" id="awacs-debug" ${awacs_dbg?'checked':''}>
        <span class="tog-sl"></span>
      </label>
      <span class="vol-val" style="color:#888;font-size:10px">bypasses DCS unit check</span>
    </div>
  `;
  $('modal-bg').classList.add('open');
}

function closeSettings() {
  $('modal-bg').classList.remove('open');
}

async function saveSettings() {
  const roles = {};
  ROLES.forEach(r => {
    roles[r.id] = {
      enabled: $('en-'+r.id).checked,
      volume:  parseInt($('vol-'+r.id).value),
      speed:   parseInt($('spd-'+r.id).value) / 100,
    };
  });
  const awacs_range_nm = parseInt($('awacs-nm').value);
  const awacs_debug = $('awacs-debug').checked;
  await api('/api/settings', {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({roles, awacs_range_nm, awacs_debug})
  });
  closeSettings();
}

// ================================================================
// POLLING
// ================================================================
$('log-scroll').addEventListener('scroll', function() {
  autoScroll = this.scrollHeight - this.scrollTop - this.clientHeight < 40;
});
$('tx').addEventListener('keydown', e => { if(e.key==='Enter') transmit(); });

function refreshAll() {
  refreshStatus();
  refreshLog();
  refreshState();
  refreshCache();
  refreshMissionLog();
}
refreshAll();
setInterval(refreshAll, 2000);
</script>
</body>
</html>"""


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    import webbrowser
    port = 5050
    print(f"DCS AI Radio Dashboard → http://localhost:{port}")
    threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
