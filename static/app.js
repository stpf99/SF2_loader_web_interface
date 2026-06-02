/* SF2 Player – app.js v4 mobile */
"use strict";

const UI = (() => {

  let presets     = [];
  let banks       = [];
  let currentSF2  = null;
  let learnParam  = null;
  let ccMap       = {};
  let sustainOn   = false;
  let portaSwOn   = false;
  let eventSource = null;

  // ── Helpers ──────────────────────────────────────────────────────────────
  const post = (url, body) =>
    fetch(url, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) })
    .then(r => r.json()).catch(() => ({}));

  const get = url => fetch(url).then(r => r.json()).catch(() => null);

  function toast(msg, ms = 2400) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove("show"), ms);
  }

  // ── Arc knob rendering (SVG) ──────────────────────────────────────────────
  // Draw a 270° arc; filled portion = value/max
  function drawArc(input) {
    const svg   = input.closest(".knob-ring").querySelector(".arc-svg");
    if (!svg) return;
    const val   = +input.value;
    const max   = +input.max || 127;
    const pct   = val / max;
    const cx    = 30, cy = 30, r = 24;
    const startA = 135, totalA = 270;
    const fillA  = totalA * pct;
    const isAmber  = input.classList.contains("amber");
    const isAccent2= input.classList.contains("accent2");
    const color  = isAmber ? "#f0a030" : isAccent2 ? "#00a07a" : "#00e5b0";

    function polar(deg) {
      const rad = (deg - 90) * Math.PI / 180;
      return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
    }
    function arcPath(a1, a2, col, w) {
      const [x1,y1] = polar(a1);
      const [x2,y2] = polar(a2);
      const large   = (a2 - a1) > 180 ? 1 : 0;
      return `<path d="M${x1},${y1} A${r},${r} 0 ${large} 1 ${x2},${y2}"
        fill="none" stroke="${col}" stroke-width="${w}" stroke-linecap="round"/>`;
    }

    const endA = startA + fillA;
    svg.innerHTML =
      arcPath(startA, startA + totalA, "#252a36", 4) +
      (pct > 0 ? arcPath(startA, endA, color, 4) : "");
  }

  function arcVal(input) {
    const el = document.getElementById(`val-${input.dataset.param}`);
    if (el) el.textContent = input.value;
    drawArc(input);
  }

  function initArcs() {
    document.querySelectorAll("input[type=range].arc").forEach(inp => {
      drawArc(inp);
      // Also update on touch (mobile range input fires oninput reliably on touch)
    });
  }

  // ── LED helpers ───────────────────────────────────────────────────────────
  function setLed(id, on) {
    document.getElementById(id)?.classList.toggle("active", !!on);
  }

  function pollStatus() {
    get("/api/status").then(s => {
      if (!s) return;
      setLed("led-synth", s.synth);
      setLed("led-midi",  s.midi && !!s.midi_port);
      const vc = document.getElementById("voice-count");
      if (vc) vc.textContent = s.voices || 0;
      if (s.sf2_loaded && s.sf2 && !currentSF2) {
        document.getElementById("sf2-name").textContent = s.sf2;
        currentSF2 = s.sf2;
      }
    });
  }

  // ── SF2 Browser ───────────────────────────────────────────────────────────
  function browseSF2() {
    get("/api/sf2_files").then(files => {
      const sel = document.getElementById("sf2-sel");
      sel.innerHTML = "";
      if (!files?.length) {
        sel.innerHTML = '<option disabled>No .sf2 files in sf2/ folder</option>';
      } else {
        files.forEach(f => {
          const o = document.createElement("option");
          o.value = o.textContent = f;
          sel.appendChild(o);
        });
      }
      document.getElementById("modal-overlay").style.display = "flex";
    });
  }

  function loadSelectedSF2() {
    const file = document.getElementById("sf2-sel").value;
    if (!file) return;
    closeModalForce();
    currentSF2 = file;
    document.getElementById("sf2-name").textContent = file;
    toast(`Loading ${file}…`);
    post("/api/load", {sf2_file: file, bank: 0, preset: 0}).then(r => {
      if (r.status === "ok") { toast("Loaded ✓"); setLed("led-synth", true); }
      else toast("Load failed");
    });
    get(`/api/presets?sf2_file=${encodeURIComponent(file)}`).then(p => {
      if (!p?.length) return;
      presets = p;
      populateBanks();
    });
  }

  function closeModal(e) { if (e.target.id === "modal-overlay") closeModalForce(); }
  function closeModalForce() { document.getElementById("modal-overlay").style.display = "none"; }

  // ── Bank / Preset ─────────────────────────────────────────────────────────
  function populateBanks() {
    const sel  = document.getElementById("bank-sel");
    const seen = new Set();
    banks = [];
    presets.forEach(p => { if (!seen.has(p.bank)) { seen.add(p.bank); banks.push(p.bank); } });
    banks.sort((a, b) => a - b);
    sel.innerHTML = banks.map(b => `<option value="${b}">${b}</option>`).join("");
    sel.value = banks[0];
    populatePresets(banks[0]);
  }

  function populatePresets(bank) {
    const sel = document.getElementById("preset-sel");
    const flt = presets.filter(p => p.bank === bank);
    sel.innerHTML = flt.map(
      p => `<option value="${p.preset}">${String(p.preset).padStart(3,"0")} ${p.name}</option>`
    ).join("");
    if (flt.length) sel.value = flt[0].preset;
    applyPreset();
  }

  function onBankChange()   { populatePresets(parseInt(document.getElementById("bank-sel").value)); }
  function onPresetChange() { applyPreset(); }

  function applyPreset() {
    const bank   = parseInt(document.getElementById("bank-sel").value)   || 0;
    const preset = parseInt(document.getElementById("preset-sel").value) || 0;
    post("/api/preset_select", {bank, preset});
  }

  function bankStep(dir) {
    const sel = document.getElementById("bank-sel");
    const idx = banks.indexOf(parseInt(sel.value));
    sel.value = banks[(idx + dir + banks.length) % banks.length];
    onBankChange();
  }

  function presetStep(dir) {
    const sel  = document.getElementById("preset-sel");
    const opts = [...sel.options];
    const idx  = opts.findIndex(o => o.selected);
    opts[(idx + dir + opts.length) % opts.length].selected = true;
    applyPreset();
  }

  // ── Params ────────────────────────────────────────────────────────────────
  function param(name, value) {
    post("/api/param", {name, value: Math.round(value)});
  }

  function updateVal(input) {
    const el = document.getElementById(`val-${input.dataset.param}`);
    if (el) el.textContent = input.value;
    if (input.classList.contains("arc")) drawArc(input);
  }

  // ── Toggles ───────────────────────────────────────────────────────────────
  function toggleSustain() {
    sustainOn = !sustainOn;
    param("sustain", sustainOn ? 127 : 0);
    document.getElementById("btn-sustain").classList.toggle("active", sustainOn);
  }
  function togglePortaSw() {
    portaSwOn = !portaSwOn;
    param("porta_sw", portaSwOn ? 127 : 0);
    document.getElementById("btn-porta-sw").classList.toggle("active", portaSwOn);
  }

  // ── MIDI Ports ────────────────────────────────────────────────────────────
  function refreshMidiPorts() {
    get("/api/midi/ports").then(data => {
      if (!data) return;
      const sel = document.getElementById("midi-port-sel");
      sel.innerHTML = data.ports.map(p => `<option value="${p.index}">${p.name}</option>`).join("");
      if (data.active) {
        const match = [...sel.options].find(o => o.textContent === data.active);
        if (match) { match.selected = true; setLed("led-midi", true); }
      }
    });
  }

  function openMidiPort() {
    const idx = parseInt(document.getElementById("midi-port-sel").value);
    post("/api/midi/open", {index: idx}).then(r => {
      setLed("led-midi", r.status === "ok");
      if (r.status === "ok") { toast("MIDI port opened"); refreshCCMap(); }
    });
  }

  // ── MIDI Learn ────────────────────────────────────────────────────────────
  function startLearn(paramName) {
    if (learnParam) cancelLearn();
    learnParam = paramName;
    post("/api/midi/learn", {param: paramName}).then(() => {
      document.getElementById("led-learn").classList.add("active");
      document.getElementById("btn-cancel-learn").style.display = "";
      document.querySelectorAll(".btn-learn").forEach(b =>
        b.classList.toggle("active", b.dataset.param === paramName)
      );
      toast(`Move knob → "${paramName}"`);
    });
  }

  function cancelLearn() {
    learnParam = null;
    post("/api/midi/learn/cancel", {});
    document.getElementById("led-learn").classList.remove("active");
    document.getElementById("btn-cancel-learn").style.display = "none";
    document.querySelectorAll(".btn-learn").forEach(b => b.classList.remove("active"));
  }

  function refreshCCMap() {
    get("/api/midi/cc_map").then(map => {
      if (!map) return;
      ccMap = {};
      Object.entries(map).forEach(([cc, p]) => { ccMap[parseInt(cc)] = p; });
      renderCCMap();
    });
  }

  function renderCCMap() {
    document.getElementById("cc-map-display").innerHTML =
      Object.entries(ccMap).map(([cc, p]) =>
        `<span class="cc-tag" onclick="UI.removeCC(${cc})">CC${cc}→${p}</span>`
      ).join("");
  }

  function removeCC(cc) {
    delete ccMap[cc];
    post("/api/midi/cc_map", {cc: parseInt(cc), param: ""});
    renderCCMap();
  }

  function showDetectedCCs() {
    get("/api/midi/detected_ccs").then(data => {
      const el = document.getElementById("detected-ccs");
      if (!data) return;
      const entries = Object.entries(data);
      el.innerHTML = entries.length
        ? entries.map(([cc, info]) =>
            `<span class="cc-detected" onclick="UI._assignDetected(${cc},'${info.name}')">CC${cc} ${info.name}</span>`
          ).join("")
        : "none yet";
    });
  }

  function _assignDetected(cc, name) {
    if (learnParam) {
      ccMap[cc] = learnParam;
      post("/api/midi/cc_map", {cc, param: learnParam});
      renderCCMap();
      cancelLearn();
      toast(`CC${cc} → ${learnParam}`);
    } else {
      toast("Tap a Learn button first");
    }
  }

  function saveDeviceProfile() {
    post("/api/midi/save_profile", {}).then(r =>
      toast(r.status === "ok" ? "Profile saved ✓" : "Nothing to save")
    );
  }

  // ── SSE ───────────────────────────────────────────────────────────────────
  function startSSE() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource("/api/midi/events");
    eventSource.onmessage = e => {
      try { handleSSE(JSON.parse(e.data)); } catch(_) {}
    };
    eventSource.onerror = () => setLed("led-midi", false);
  }

  function handleSSE(ev) {
    if (ev.type === "note_on" || ev.type === "note_off") {
      const key = document.querySelector(`.key[data-note="${ev.note}"]`);
      if (key) {
        key.classList.toggle("pressed", ev.type === "note_on");
        if (ev.type === "note_on") setTimeout(() => key.classList.remove("pressed"), 300);
      }
    } else if (ev.type === "cc") {
      const input = document.querySelector(`input[data-param="${ev.param}"]`);
      if (input) { input.value = ev.val; arcVal(input); }
    } else if (ev.type === "learned") {
      ccMap[ev.cc] = ev.param;
      renderCCMap();
      cancelLearn();
      toast(`CC${ev.cc} → ${ev.param}`);
    } else if (ev.type === "bend") {
      const input = document.querySelector('input[data-param="bend"]');
      if (input) { input.value = ev.val; arcVal(input); }
    }
  }

  // ── Keyboard ──────────────────────────────────────────────────────────────
  const NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
  const IS_BLACK   = new Set([1,3,6,8,10]);

  function buildKeyboard() {
    const kb = document.getElementById("keyboard");
    const inner = document.createElement("div");
    inner.className = "kb-inner";
    kb.appendChild(inner);

    const startNote = 48; // C3
    const endNote   = 71; // B4
    const WW = 37; // white key width px
    let whiteX = 0;

    for (let n = startNote; n <= endNote; n++) {
      const sem     = n % 12;
      const isBlack = IS_BLACK.has(sem);
      const key     = document.createElement("div");
      key.className    = `key ${isBlack ? "black" : "white"}`;
      key.dataset.note = n;

      const lbl = document.createElement("span");
      lbl.className   = "note-label";
      lbl.textContent = NOTE_NAMES[sem] + (Math.floor(n / 12) - 1);
      key.appendChild(lbl);

      if (isBlack) {
        key.style.left = (whiteX - 11) + "px";
      } else {
        key.style.width  = WW + "px";
        whiteX += WW + 1;
      }

      // Pointer events (mouse + touch unified)
      key.addEventListener("pointerdown",  e => { e.preventDefault(); noteOn(n);  key.classList.add("pressed"); key.setPointerCapture(e.pointerId); });
      key.addEventListener("pointerup",    e => { noteOff(n); key.classList.remove("pressed"); });
      key.addEventListener("pointercancel",e => { noteOff(n); key.classList.remove("pressed"); });

      inner.appendChild(key);
    }

    // Total width
    inner.style.width = whiteX + "px";
  }

  function noteOn(note)  { post("/api/note_on",  {note, vel: 100}); }
  function noteOff(note) { post("/api/note_off", {note}); }

  // Computer keyboard
  const KEY_MAP = {
    "a":48,"w":49,"s":50,"e":51,"d":52,"f":53,"t":54,"g":55,
    "y":56,"h":57,"u":58,"j":59,"k":60,"o":61,"l":62,"p":63,
  };
  const heldKeys = new Set();

  function setupKBD() {
    document.addEventListener("keydown", e => {
      if (e.repeat || ["INPUT","SELECT","TEXTAREA"].includes(e.target.tagName)) return;
      const note = KEY_MAP[e.key.toLowerCase()];
      if (note !== undefined && !heldKeys.has(note)) {
        heldKeys.add(note);
        noteOn(note);
        document.querySelector(`.key[data-note="${note}"]`)?.classList.add("pressed");
      }
    });
    document.addEventListener("keyup", e => {
      const note = KEY_MAP[e.key.toLowerCase()];
      if (note !== undefined) {
        heldKeys.delete(note);
        noteOff(note);
        document.querySelector(`.key[data-note="${note}"]`)?.classList.remove("pressed");
      }
    });
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  function init() {
    buildKeyboard();
    setupKBD();
    initArcs();
    refreshMidiPorts();
    refreshCCMap();
    pollStatus();
    startSSE();
    document.querySelectorAll(".btn-learn").forEach(btn =>
      btn.addEventListener("click", () => startLearn(btn.dataset.param))
    );
    document.getElementById("midi-port-sel")
      .addEventListener("change", openMidiPort);
    setInterval(pollStatus, 4000);
  }

  document.addEventListener("DOMContentLoaded", init);

  return {
    browseSF2, loadSelectedSF2, closeModal, closeModalForce,
    onBankChange, onPresetChange, bankStep, presetStep,
    param, updateVal, arcVal,
    toggleSustain, togglePortaSw,
    refreshMidiPorts, openMidiPort,
    startLearn, cancelLearn,
    refreshCCMap, removeCC,
    showDetectedCCs, _assignDetected,
    saveDeviceProfile,
  };
})();
