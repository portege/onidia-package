"use strict";

// ── ONIDIA grid contract (characters/characters.go) ─────────────────────────
const GRID_W = 112, GRID_H = 160, ZOOM = 4; // canvas = 448 × 640
const CX = 56, GROUND_Y = 156;

// The seven core body parts, bottom-to-top (first painted first).
const LAYERS = [
  { id: "shadow", label: "Ground shadow" },
  { id: "hair_back", label: "Back hair" },
  { id: "legs", label: "Legs" },
  { id: "body", label: "Body / outfit" },
  { id: "arms", label: "Arms" },
  { id: "head", label: "Head / hair" },
  { id: "face", label: "Face" },
];

// ONIDIA preset palette (characters/onidia/palette.go). Exported colours keep
// these semantic var names (colHair, colSkin, ...) in the generated Go code.
const PRESETS = [
  ["Deep plum outline", "#2F223E"],
  ["Skin outline", "#C68664"],
  ["Ground shadow", "#190C2332"],
  ["Hair teal", "#5FCFD6"],
  ["Hair shade", "#41AEB6"],
  ["Hair light", "#A5ECEF"],
  ["Hair tie", "#FF7A9E"],
  ["Skin", "#FFE4CE"],
  ["Nose", "#EBA58C8C"],
  ["Blush", "#FF788746"],
  ["Tear", "#7EBCFFCD"],
  ["Sweat", "#9ED4FFE6"],
  ["Anger", "#F04E4EEB"],
  ["Sclera white", "#FDFDFF"],
  ["Iris", "#363E6E"],
  ["Pupil", "#181C38"],
  ["Eyelash", "#282032"],
  ["Mouth", "#B0425A"],
  ["Tongue", "#E77E8F"],
  ["Dress pink", "#FF9AAA"],
  ["Frill", "#FFD6E0"],
  ["Collar", "#FAF8FC"],
  ["Shoe", "#5C4A6E"],
  ["White", "#FFFFFF"],
];

const MOODS = ["neutral", "happy", "wink", "sad", "thinking", "anxious",
  "angry", "surprised", "sleepy", "fear", "disgust", "contempt", "confused",
  "skeptical", "embarrassed", "adore"];

// ── state ───────────────────────────────────────────────────────────────────
const state = {
  tool: "pen",
  color: "#2F223E",
  width: 4,
  active: "body",
  preview: false,
  reference: true,    // draw the real rendered character behind the strokes
  layers: {},        // id → {visible, strokes: [{color, width, points: [[x,y],...]}]}
  undo: [],          // {type:"add"|"remove"|"clear", ...}
  drawing: null,     // stroke being drawn right now
};
LAYERS.forEach(l => { state.layers[l.id] = { visible: true, strokes: [] }; });

// Reference sprite: the real character, rendered server-side by the ONIDIA
// engine (GET /api/render) and drawn faintly behind the strokes as a tracing
// guide. Cache by "name:expr" so re-toggling doesn't refetch.
const refCache = new Map();
let refImage = null;    // {name, expr, img} currently shown
let refLoadSeq = 0;     // ignore out-of-order fetches

const $ = id => document.getElementById(id);
const board = $("board");
const ctx = board.getContext("2d");
const r2 = v => Math.round(v * 100) / 100;
const clamp = (v, a, b) => Math.min(Math.max(v, a), b);

// ── rendering ───────────────────────────────────────────────────────────────
function render() {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#10121a";
  ctx.fillRect(0, 0, board.width, board.height);
  if (!state.preview) {
    drawReference(); // real rendered character first, as a faint tracing guide
    drawGrid();
  }
  for (const { id } of LAYERS) {
    const lay = state.layers[id];
    if (!lay.visible) continue;
    ctx.globalAlpha = state.preview || id === state.active ? 1 : 0.3;
    drawStrokes(lay.strokes);
  }
  if (state.drawing) drawStrokes([state.drawing]);
  ctx.globalAlpha = 1;
}

// The tracing guide: the pixel-exact sprite from /api/render, scaled to the
// grid and drawn at low opacity behind everything. A dimmer is left out so the
// guide reads clearly; strokes sit on top at full strength.
function drawReference() {
  if (!state.reference || !refImage || !refImage.img.complete) return;
  const { img } = refImage;
  if (!img.naturalWidth) return;
  ctx.save();
  ctx.globalAlpha = 0.55;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(img, 0, 0, GRID_W * ZOOM, GRID_H * ZOOM);
  ctx.restore();
}

// Fetch the real rendered character and show it as the reference. `name` is a
// registered ONIDIA character; `expr` picks the face. Requests are sequenced so
// a slow earlier fetch can't overwrite a newer choice.
function loadReference(name, expr = "neutral") {
  if (!name) { refImage = null; render(); return; }
  const key = `${name}:${expr}`;
  const cached = refCache.get(key);
  const seq = ++refLoadSeq;
  if (cached) {
    refImage = { name, expr, img: cached };
    render();
    return;
  }
  const url = `/api/render?character=${encodeURIComponent(name)}&expr=${encodeURIComponent(expr)}&w=${GRID_W * ZOOM}`;
  const img = new Image();
  img.onload = () => {
    if (seq !== refLoadSeq) return; // a newer reference won
    refCache.set(key, img);
    refImage = { name, expr, img };
    render();
  };
  img.onerror = () => {
    if (seq !== refLoadSeq) return;
    refImage = null;
    toast(`could not render "${name}"`, true);
    render();
  };
  img.src = url;
}

function drawGrid() {
  ctx.lineWidth = 1;
  // fine grid every 8 logical units
  ctx.strokeStyle = "#1e2230";
  ctx.beginPath();
  for (let x = 8; x < GRID_W; x += 8) {
    ctx.moveTo(x * ZOOM + .5, 0); ctx.lineTo(x * ZOOM + .5, GRID_H * ZOOM);
  }
  for (let y = 8; y < GRID_H; y += 8) {
    ctx.moveTo(0, y * ZOOM + .5); ctx.lineTo(GRID_W * ZOOM, y * ZOOM + .5);
  }
  ctx.stroke();
  // centre line (Cx) and ground line (GroundY)
  ctx.strokeStyle = "#3c465e";
  ctx.beginPath();
  ctx.moveTo(CX * ZOOM + .5, 0); ctx.lineTo(CX * ZOOM + .5, GRID_H * ZOOM);
  ctx.stroke();
  ctx.strokeStyle = "#4d5f52";
  ctx.beginPath();
  ctx.moveTo(0, GROUND_Y * ZOOM + .5); ctx.lineTo(GRID_W * ZOOM, GROUND_Y * ZOOM + .5);
  ctx.stroke();
  // frame
  ctx.strokeStyle = "#32384a";
  ctx.strokeRect(.5, .5, GRID_W * ZOOM - 1, GRID_H * ZOOM - 1);
}

function drawStrokes(strokes) {
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const s of strokes) {
    ctx.strokeStyle = s.color;
    ctx.fillStyle = s.color;
    ctx.lineWidth = s.width * ZOOM;
    if (s.points.length === 1) {
      const [x, y] = s.points[0];
      ctx.beginPath();
      ctx.arc(x * ZOOM, y * ZOOM, Math.max(s.width * ZOOM / 2, 1), 0, Math.PI * 2);
      ctx.fill();
    } else {
      ctx.beginPath();
      ctx.moveTo(s.points[0][0] * ZOOM, s.points[0][1] * ZOOM);
      for (let i = 1; i < s.points.length; i++) {
        ctx.lineTo(s.points[i][0] * ZOOM, s.points[i][1] * ZOOM);
      }
      ctx.stroke();
    }
  }
}

// ── pointer input ───────────────────────────────────────────────────────────
function toLogical(ev) {
  const r = board.getBoundingClientRect();
  const x = (ev.clientX - r.left) * (board.width / r.width) / ZOOM;
  const y = (ev.clientY - r.top) * (board.height / r.height) / ZOOM;
  return [r2(clamp(x, 0, GRID_W)), r2(clamp(y, 0, GRID_H))];
}

board.addEventListener("pointerdown", ev => {
  ev.preventDefault();
  board.setPointerCapture(ev.pointerId);
  const [x, y] = toLogical(ev);
  if (state.tool === "eraser") {
    eraseAt(x, y);
    return;
  }
  state.drawing = { color: state.color, width: state.width, points: [[x, y]] };
  render();
});

board.addEventListener("pointermove", ev => {
  const [x, y] = toLogical(ev);
  $("statusPos").textContent = `x ${x}, y ${y}`;
  if (state.tool === "eraser" && ev.buttons) { eraseAt(x, y); return; }
  const d = state.drawing;
  if (!d) return;
  const last = d.points[d.points.length - 1];
  if (Math.hypot(x - last[0], y - last[1]) < 0.5) return;
  d.points.push([x, y]);
  render();
});

function endStroke() {
  const d = state.drawing;
  if (!d) return;
  state.drawing = null;
  const lay = state.layers[state.active];
  lay.strokes.push(d);
  state.undo.push({ type: "add", layer: state.active, stroke: d });
  refreshLayers();
  render();
}
board.addEventListener("pointerup", endStroke);
board.addEventListener("pointercancel", endStroke);
board.addEventListener("pointerleave", ev => { if (!ev.buttons) endStroke(); });

// ── eraser ──────────────────────────────────────────────────────────────────
function segDist(ax, ay, bx, by, px, py) {
  const dx = bx - ax, dy = by - ay;
  const len2 = dx * dx + dy * dy;
  let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
  t = clamp(t, 0, 1);
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function strokeHit(s, x, y, er) {
  const r = s.width / 2 + er;
  const p = s.points;
  if (p.length === 1) return Math.hypot(p[0][0] - x, p[0][1] - y) <= r;
  for (let i = 0; i + 1 < p.length; i++) {
    if (segDist(p[i][0], p[i][1], p[i + 1][0], p[i + 1][1], x, y) <= r) return true;
  }
  return false;
}

// The eraser only removes strokes on the ACTIVE layer (predictable with a
// layer-based workflow).
function eraseAt(x, y) {
  const lay = state.layers[state.active];
  const er = state.width / 2;
  let changed = false;
  for (let i = lay.strokes.length - 1; i >= 0; i--) {
    if (strokeHit(lay.strokes[i], x, y, er)) {
      const [stroke] = lay.strokes.splice(i, 1);
      state.undo.push({ type: "remove", layer: state.active, index: i, stroke });
      changed = true;
    }
  }
  if (changed) { refreshLayers(); render(); }
}

// ── undo ────────────────────────────────────────────────────────────────────
function undo() {
  const op = state.undo.pop();
  if (!op) { toast("nothing to undo"); return; }
  const lay = state.layers[op.layer];
  if (op.type === "add") {
    const i = lay.strokes.lastIndexOf(op.stroke);
    if (i >= 0) lay.strokes.splice(i, 1);
  } else if (op.type === "remove") {
    lay.strokes.splice(op.index, 0, op.stroke);
  } else if (op.type === "clear") {
    lay.strokes = op.strokes;
  }
  refreshLayers();
  render();
}

// ── layer panel ─────────────────────────────────────────────────────────────
function refreshLayers() {
  const ul = $("layerList");
  ul.innerHTML = "";
  // show topmost layer first, like a graphics editor
  for (const { id, label } of [...LAYERS].reverse()) {
    const lay = state.layers[id];
    const li = document.createElement("li");
    li.className = (id === state.active ? "active" : "") +
      (lay.visible ? "" : " hidden-layer");
    li.innerHTML = `<button class="eye" title="toggle visibility">${lay.visible ? "👁" : "—"}</button>` +
      `<span class="lname">${label}</span>` +
      `<span class="lcount">${lay.strokes.length}</span>`;
    li.querySelector(".eye").addEventListener("click", ev => {
      ev.stopPropagation();
      lay.visible = !lay.visible;
      refreshLayers();
      render();
    });
    li.addEventListener("click", () => {
      state.active = id;
      $("statusLayer").textContent = `layer: ${id}`;
      refreshLayers();
      render();
    });
    ul.appendChild(li);
  }
}

// ── tools & colours ─────────────────────────────────────────────────────────
function setTool(tool) {
  state.tool = tool;
  $("toolPen").classList.toggle("active", tool === "pen");
  $("toolEraser").classList.toggle("active", tool === "eraser");
  $("statusTool").textContent = `${tool} · ${state.width}u`;
}

function setColor(hex) {
  hex = hex.toUpperCase();
  state.color = hex;
  $("colorPick").value = hex.slice(0, 7);
  $("colorHex").value = hex;
}

function buildSwatches() {
  const box = $("swatches");
  for (const [name, hex] of PRESETS) {
    const b = document.createElement("button");
    b.className = "swatch";
    b.title = `${name} ${hex}`;
    b.style.background = hex;
    b.style.backgroundColor = hex; // 8-digit hex works in modern browsers
    b.addEventListener("click", () => setColor(hex));
    box.appendChild(b);
  }
}

// ── phrases ─────────────────────────────────────────────────────────────────
// Parses the textarea: "mood: text" per line, or plain text (= neutral).
function parsePhrases() {
  const out = [];
  const lines = $("phrases").value.split("\n");
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    const m = line.match(/^([A-Za-z]+):\s*(.+)$/);
    let mood = "neutral", text = line;
    if (m) {
      mood = m[1].toLowerCase();
      text = m[2];
      if (!MOODS.includes(mood)) {
        throw new Error(`unknown mood "${mood}" on line "${line}" — use one of: ${MOODS.join(", ")}`);
      }
    }
    out.push({ text, expr: mood });
  }
  if (!out.length) out.push({ text: "hi there!", expr: "neutral" });
  return out;
}

// ── export ──────────────────────────────────────────────────────────────────
function buildPayload() {
  const name = $("charName").value.trim();
  if (!/^[a-z][a-z0-9_]*$/.test(name)) {
    throw new Error("name must be a lowercase package name: a–z, 0–9, _, starting with a letter");
  }
  return {
    name,
    phrases: parsePhrases(),
    layers: LAYERS.map(({ id }) => ({
      id,
      visible: state.layers[id].visible,
      strokes: state.layers[id].strokes.map(s => ({
        color: s.color,
        width: s.width,
        points: s.points.map(p => [r2(p[0]), r2(p[1])]),
      })),
    })),
  };
}

async function exportCode() {
  let payload;
  try {
    payload = buildPayload();
  } catch (e) {
    toast(e.message, true);
    return;
  }
  const total = payload.layers.reduce((n, l) => n + l.strokes.length, 0);
  if (!total) { toast("draw something first (or load the sample)", true); return; }

  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `server error ${res.status}`);
    openModal(data);
  } catch (e) {
    toast(e.message, true);
  }
}

function openModal(data) {
  $("modalTitle").textContent = `${data.filename}`;
  $("modalInstall").textContent = data.install;
  $("modalCode").textContent = data.code;
  $("btnDownload").onclick = () => download(data.filename, data.code, "text/x-go");
  $("modal").classList.remove("hidden");
}

function closeModal() { $("modal").classList.add("hidden"); }

function download(filename, text, type) {
  const blob = new Blob([text], { type: type || "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

let toastTimer = 0;
function toast(msg, isError) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.toggle("error", !!isError);
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3500);
}

// ── project save / load ─────────────────────────────────────────────────────
function saveProject() {
  const project = {
    version: 1,
    name: $("charName").value,
    active: state.active,
    color: state.color,
    width: state.width,
    phrases: $("phrases").value,
    layers: LAYERS.map(({ id }) => ({
      id,
      visible: state.layers[id].visible,
      strokes: state.layers[id].strokes,
    })),
  };
  download(($("charName").value.trim() || "character") + ".json",
    JSON.stringify(project), "application/json");
  toast("project saved");
}

function loadProject(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const p = JSON.parse(reader.result);
      if (p.version !== 1 || !Array.isArray(p.layers)) throw new Error("not a character-builder project");
      $("charName").value = p.name || "mychar";
      $("phrases").value = p.phrases || "";
      state.color = p.color || state.color;
      state.width = p.width || state.width;
      $("brushWidth").value = state.width;
      $("widthVal").textContent = `${state.width}u`;
      setColor(state.color);
      LAYERS.forEach(l => state.layers[l.id] = { visible: true, strokes: [] });
      for (const l of p.layers) {
        if (!state.layers[l.id]) continue;
        state.layers[l.id] = { visible: l.visible !== false, strokes: l.strokes || [] };
      }
      state.active = state.layers[p.active] ? p.active : "body";
      state.undo = [];
      refreshLayers();
      setTool(state.tool);
      render();
      toast(`loaded ${file.name}`);
    } catch (e) {
      toast(`load failed: ${e.message}`, true);
    }
  };
  reader.readAsText(file);
}


// ── sample figure ───────────────────────────────────────────────────────────
// "Load sample" fills every layer with Onidia herself, ported 1:1 from
// characters/onidia/*.go (see static/sample.js) and names the export
// onidia2 so it can drop straight into onidia/characters/.
function loadSample() {
  const S = SAMPLE_ONIDIA;
  LAYERS.forEach(l => { state.layers[l.id] = { visible: true, strokes: [] }; });
  for (const layer of S.layers) {
    const dest = state.layers[layer.id];
    if (!dest) continue;
    // Deep-copy so the editor never mutates the shared sample data.
    dest.strokes = layer.strokes.map(s => ({
      color: s.color,
      width: s.width,
      points: s.points.map(p => [p[0], p[1]]),
    }));
  }
  $("charName").value = S.name;
  $("phrases").value = S.phrases.map(([mood, text]) => `${mood}: ${text}`).join("\n");
  state.active = "body";
  state.undo = [];
  refreshLayers();
  // Show the real Onidia (rendered by the Go engine) behind the stroke port so
  // the two can be compared and the figure traced precisely.
  state.reference = true;
  $("btnReference").classList.add("active");
  const refName = $("referenceChar").value || "onidia";
  loadReference(refName, "neutral");
  render();
  toast("Onidia loaded — the real sprite shows behind as a tracing guide");
}

function clearLayer() {
  const lay = state.layers[state.active];
  if (!lay.strokes.length) return;
  state.undo.push({ type: "clear", layer: state.active, strokes: lay.strokes });
  lay.strokes = [];
  refreshLayers();
  render();
}

function clearAll() {
  LAYERS.forEach(l => {
    const lay = state.layers[l.id];
    if (lay.strokes.length) {
      state.undo.push({ type: "clear", layer: l.id, strokes: lay.strokes });
      lay.strokes = [];
    }
  });
  refreshLayers();
  render();
}

// ── bindings ────────────────────────────────────────────────────────────────
function bind() {
  $("toolPen").addEventListener("click", () => setTool("pen"));
  $("toolEraser").addEventListener("click", () => setTool("eraser"));
  $("brushWidth").addEventListener("input", e => {
    state.width = +e.target.value;
    $("widthVal").textContent = `${state.width}u`;
    $("statusTool").textContent = `${state.tool} · ${state.width}u`;
  });
  $("colorPick").addEventListener("input", e => setColor(e.target.value));
  $("colorHex").addEventListener("change", e => {
    const v = e.target.value.trim();
    if (/^#?[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(v)) {
      setColor(v.startsWith("#") ? v : "#" + v);
    } else {
      toast("colour must be #RRGGBB or #RRGGBBAA", true);
      setColor(state.color);
    }
  });
  $("btnUndo").addEventListener("click", undo);
  $("btnClearLayer").addEventListener("click", clearLayer);
  $("btnClearAll").addEventListener("click", clearAll);
  $("btnSample").addEventListener("click", loadSample);
  $("btnSave").addEventListener("click", saveProject);
  $("btnLoad").addEventListener("click", () => $("fileLoad").click());
  $("fileLoad").addEventListener("change", e => {
    if (e.target.files[0]) loadProject(e.target.files[0]);
    e.target.value = "";
  });
  $("btnPreview").addEventListener("click", () => {
    state.preview = !state.preview;
    $("btnPreview").classList.toggle("active", state.preview);
    render();
  });
  // Reference tracing guide: toggle it on/off, and switch which real character
  // the ONIDIA engine renders behind the strokes.
  $("btnReference").addEventListener("click", () => {
    state.reference = !state.reference;
    $("btnReference").classList.toggle("active", state.reference);
    render();
  });
  $("referenceChar").addEventListener("change", e => {
    const name = e.target.value;
    if (!name) { refImage = null; render(); return; }
    loadReference(name, "neutral");
  });
  $("btnExport").addEventListener("click", exportCode);
  $("modalClose").addEventListener("click", closeModal);
  $("modal").addEventListener("click", e => {
    if (e.target === $("modal")) closeModal();
  });
  $("btnCopy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("modalCode").textContent);
      toast("code copied");
    } catch (e) {
      toast("copy failed — select the code manually", true);
    }
  });

  document.addEventListener("keydown", e => {
    if (e.target.matches("input, textarea")) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      undo();
    } else if (e.key === "[") {
      $("brushWidth").value = Math.max(1, state.width - 1);
      $("brushWidth").dispatchEvent(new Event("input"));
    } else if (e.key === "]") {
      $("brushWidth").value = Math.min(24, state.width + 1);
      $("brushWidth").dispatchEvent(new Event("input"));
    } else if (e.key === "b" || e.key === "B") {
      setTool("pen");
    } else if (e.key === "e" || e.key === "E") {
      setTool("eraser");
    } else if (e.key === "Escape") {
      closeModal();
    }
  });
}

// ── init ────────────────────────────────────────────────────────────────────
buildSwatches();
refreshLayers();
setTool("pen");
setColor(state.color);
bind();
render();

// Populate the reference <select> with every character the ONIDIA engine can
// render (Go → image), then show the default one behind the strokes.
populateReferenceChars();

function populateReferenceChars() {
  const sel = $("referenceChar");
  if (!sel) return;
  fetch("/api/characters")
    .then(r => r.json())
    .then(names => {
      if (!Array.isArray(names) || !names.length) return;
      sel.innerHTML = "";
      for (const n of names) {
        const opt = document.createElement("option");
        opt.value = n;
        opt.textContent = n;
        sel.appendChild(opt);
      }
      if (!names.includes(sel.value)) sel.value = names[0];
      loadReference(sel.value || "onidia", "neutral");
    })
    .catch(() => { /* server unreachable: the select keeps its static option */ });
}

