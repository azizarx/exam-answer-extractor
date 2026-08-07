/* MCQ grid annotator UI — no innerHTML; safe DOM updates only. */
const state = {
  mode: "cols",
  cols: [],
  rows: [],
  img: null,
  imgPath: null,
  imgNatural: { w: 0, h: 0 },
  zoom: 0.5,
  history: [],
  drag: null, // move or resize drag state
  didDrag: false,
  hoverHandle: null, // 'e' | 's' | 'se' | null
};

const LETTERS = ["A", "B", "C", "D", "E"];
const $ = (id) => document.getElementById(id);
const canvas = $("cv");
const ctx = canvas.getContext("2d");

function setStatus(msg) {
  $("status").textContent = msg;
}

function cellW() {
  return +$("cellW").value || 36;
}
function cellH() {
  return +$("cellH").value || 36;
}
function bubH() {
  return +$("bubH").value || 32;
}

function pushHistory() {
  state.history.push(JSON.stringify({ cols: state.cols, rows: state.rows }));
  if (state.history.length > 80) state.history.shift();
}

function buildPayload() {
  // Clicks are fill-top Y. Extractor row_positions are label-band tops:
  // fill_y = row_y + cell_height  ⇒  row_y = fill_y - cell_height
  const ch = cellH();
  const row_positions = state.rows.map((y) => Math.round(y - ch));
  const col_positions = [state.cols.map((x) => Math.round(x))];
  const pitch =
    row_positions.length >= 2
      ? (row_positions[row_positions.length - 1] - row_positions[0]) /
        Math.max(1, row_positions.length - 1)
      : 80;
  return {
    source_image: state.imgPath,
    image_size: state.imgNatural,
    reference_dpi_note:
      "Coordinates are in this image pixel space (~300 DPI gold pages).",
    click_convention:
      "cols = fill center X; rows clicks = fill-top Y; row_positions = fill_y - cell_height",
    grid: {
      rows: 20,
      cols: 1,
      questions_per_col: [20],
      options: LETTERS.slice(),
      row_positions,
      col_positions,
      cell_width: cellW(),
      cell_height: ch,
      bubble_height: bubH(),
      row_pitch: Math.round(pitch * 10) / 10,
      first_row_offset: 38,
    },
    raw_clicks: {
      col_xs: state.cols.slice(),
      fill_top_ys: state.rows.slice(),
    },
  };
}

function updatePills() {
  const pc = $("pillCols");
  const pr = $("pillRows");
  pc.textContent = `cols ${state.cols.length}/5`;
  pr.textContent = `rows ${state.rows.length}/20`;
  pc.classList.toggle("on", state.cols.length === 5);
  pr.classList.toggle("on", state.rows.length === 20);
  $("coords").textContent = JSON.stringify(buildPayload().grid, null, 2);
}

function setMode(m) {
  state.mode = m;
  $("modeCols").classList.toggle("active", m === "cols");
  $("modeRows").classList.toggle("active", m === "rows");
  $("modeMove").classList.toggle("active", m === "move");
  $("modeResize").classList.toggle("active", m === "resize");
  const vp = $("viewport");
  vp.classList.toggle("move-mode", m === "move");
  vp.classList.toggle("resize-mode", m === "resize");
  clearResizeCursor();
  if (m !== "move" && m !== "resize") {
    state.drag = null;
    vp.classList.remove("dragging");
  }
  const hints = {
    cols: "Click fill centers for A, then B, C, D, E (same row).",
    rows: "Click the TOP-CENTER of each fill bubble Q1→Q20 (or Q1 + Q20 then Fit rows).",
    move: "Drag to shift the whole grid. Arrow keys nudge 1px (Shift = 5px).",
    resize:
      "Drag E / S / SE handles (anchor = Q1/A). Arrows nudge far edge 1px (Shift = 5).",
  };
  $("modeHint").textContent = hints[m] || "";
}

function canvasPoint(ev) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (ev.clientX - rect.left) / state.zoom,
    y: (ev.clientY - rect.top) / state.zoom,
  };
}

function translateGrid(dx, dy) {
  state.cols = state.cols.map((x) => x + dx);
  state.rows = state.rows.map((y) => y + dy);
}

function scaleGridFromAnchor(sx, sy, ax, ay) {
  state.cols = state.cols.map((x) => ax + (x - ax) * sx);
  state.rows = state.rows.map((y) => ay + (y - ay) * sy);
}

function hasGrid() {
  return state.cols.length > 0 || state.rows.length > 0;
}

function canResize() {
  return state.cols.length >= 2 || state.rows.length >= 2;
}

function clearResizeCursor() {
  const vp = $("viewport");
  vp.classList.remove("resize-ew", "resize-ns", "resize-nwse");
}

function setResizeCursor(kind) {
  clearResizeCursor();
  if (kind === "e") $("viewport").classList.add("resize-ew");
  else if (kind === "s") $("viewport").classList.add("resize-ns");
  else if (kind === "se") $("viewport").classList.add("resize-nwse");
}

/** Handle positions in image coords (fill centers / fill tops). */
function resizeHandles() {
  if (!canResize()) return [];
  const cols = state.cols;
  const rows = state.rows;
  const ax = cols[0] ?? 0;
  const ay = rows[0] ?? 0;
  const lx = cols.length ? cols[cols.length - 1] : ax + 100;
  const ly = rows.length ? rows[rows.length - 1] : ay + 100;
  const midX = (ax + lx) / 2;
  const midY = (ay + ly) / 2;
  const out = [];
  if (cols.length >= 2) out.push({ kind: "e", x: lx, y: midY });
  if (rows.length >= 2) out.push({ kind: "s", x: midX, y: ly });
  if (cols.length >= 2 && rows.length >= 2) out.push({ kind: "se", x: lx, y: ly });
  return out;
}

function hitHandle(p) {
  const hitR = 14 / state.zoom;
  let best = null;
  let bestD = hitR;
  for (const h of resizeHandles()) {
    const d = Math.hypot(p.x - h.x, p.y - h.y);
    if (d <= bestD) {
      bestD = d;
      best = h.kind;
    }
  }
  return best;
}

function applyResizeFromDrag(p) {
  const d = state.drag;
  const ax = d.anchorX;
  const ay = d.anchorY;
  const oSpanX = d.originLastX - ax;
  const oSpanY = d.originLastY - ay;
  let sx = 1;
  let sy = 1;
  if ((d.kind === "e" || d.kind === "se") && Math.abs(oSpanX) > 1e-3) {
    sx = Math.max(0.05, (p.x - ax) / oSpanX);
  }
  if ((d.kind === "s" || d.kind === "se") && Math.abs(oSpanY) > 1e-3) {
    sy = Math.max(0.05, (p.y - ay) / oSpanY);
  }
  state.cols = d.originCols.map((x) => ax + (x - ax) * sx);
  state.rows = d.originRows.map((y) => ay + (y - ay) * sy);
  return { sx, sy };
}

/** Nudge the far edge by pixels (keeps Q1/A fixed). */
function nudgeFarEdge(dx, dy) {
  const cols = state.cols;
  const rows = state.rows;
  const ax = cols[0] ?? 0;
  const ay = rows[0] ?? 0;
  let sx = 1;
  let sy = 1;
  if (dx && cols.length >= 2) {
    const span = cols[cols.length - 1] - ax;
    if (Math.abs(span) > 1e-3) sx = Math.max(0.05, (span + dx) / span);
  }
  if (dy && rows.length >= 2) {
    const span = rows[rows.length - 1] - ay;
    if (Math.abs(span) > 1e-3) sy = Math.max(0.05, (span + dy) / span);
  }
  scaleGridFromAnchor(sx, sy, ax, ay);
  return { sx, sy };
}

function fillSelect(sel, images) {
  while (sel.firstChild) sel.removeChild(sel.firstChild);
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "— pick gold page —";
  sel.appendChild(blank);
  for (const p of images) {
    const o = document.createElement("option");
    o.value = p;
    o.textContent = p;
    sel.appendChild(o);
  }
}

async function listImages() {
  const r = await fetch("/api/list");
  const data = await r.json();
  fillSelect($("imageSelect"), data.images || []);
}

async function loadImage(relPath) {
  if (!relPath) return;
  state.imgPath = relPath;
  const img = new Image();
  img.onload = () => {
    state.img = img;
    state.imgNatural = { w: img.naturalWidth, h: img.naturalHeight };
    $("imgMeta").textContent = `${relPath}  ${img.naturalWidth}×${img.naturalHeight}px`;
    if (hasGrid()) {
      setMode("move");
      setStatus(
        `Loaded ${relPath} — grid kept from previous page. Drag to align, then Save.`,
      );
    } else {
      setStatus(`Loaded ${relPath}`);
    }
    redraw();
  };
  img.onerror = () => setStatus("Failed to load image: " + relPath);
  img.src = "/api/image?path=" + encodeURIComponent(relPath);
}

function redraw() {
  if (!state.img) return;
  const z = state.zoom;
  canvas.width = Math.round(state.img.naturalWidth * z);
  canvas.height = Math.round(state.img.naturalHeight * z);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(state.img, 0, 0, canvas.width, canvas.height);

  const cw = cellW();
  const bh = bubH();

  if (state.cols.length === 5 && state.rows.length > 0) {
    for (let i = 0; i < state.rows.length; i++) {
      const fillTop = state.rows[i];
      for (let c = 0; c < 5; c++) {
        const cx = state.cols[c];
        const x1 = (cx - cw / 2) * z;
        const y1 = fillTop * z;
        const x2 = (cx + cw / 2) * z;
        const y2 = (fillTop + bh) * z;
        ctx.strokeStyle = "rgba(0,220,80,0.9)";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
      }
      ctx.fillStyle = "rgba(0,0,0,0.65)";
      ctx.fillRect(state.cols[0] * z - 70 * z, fillTop * z, 60 * z, 16 * z);
      ctx.fillStyle = "#7CFF9A";
      ctx.font = `${Math.max(10, 12 * z)}px sans-serif`;
      ctx.fillText(`Q${i + 1}`, state.cols[0] * z - 65 * z, fillTop * z + 12 * z);
    }
    LETTERS.forEach((L, i) => {
      ctx.fillStyle = "#fff";
      ctx.font = `bold ${Math.max(11, 13 * z)}px sans-serif`;
      ctx.fillText(L, state.cols[i] * z - 5 * z, state.rows[0] * z - 8 * z);
    });
  }

  state.cols.forEach((x, i) => {
    const yMark = state.rows[0] ? state.rows[0] + bh / 2 : 80;
    ctx.beginPath();
    ctx.arc(x * z, yMark * z, 4, 0, Math.PI * 2);
    ctx.fillStyle = "#5b9fd4";
    ctx.fill();
    ctx.fillText(LETTERS[i], x * z - 4, (state.rows[0] ? state.rows[0] - 10 : 70) * z);
  });

  state.rows.forEach((y) => {
    const x0 = state.cols[0] ?? 40;
    const x1 = state.cols.length
      ? state.cols[state.cols.length - 1] + 40
      : x0 + 200;
    ctx.beginPath();
    ctx.moveTo(x0 * z - 20, y * z);
    ctx.lineTo(x1 * z, y * z);
    ctx.strokeStyle = "rgba(255,200,0,0.5)";
    ctx.stroke();
  });

  if (state.mode === "resize") {
    const ax = state.cols[0];
    const ay = state.rows[0];
    if (ax != null && ay != null) {
      ctx.beginPath();
      ctx.arc(ax * z, ay * z, 5, 0, Math.PI * 2);
      ctx.fillStyle = "#ff6b6b";
      ctx.fill();
      ctx.fillStyle = "#ffb4b4";
      ctx.font = `${Math.max(10, 11 * z)}px sans-serif`;
      ctx.fillText("anchor", ax * z + 8, ay * z - 6);
    }
    for (const h of resizeHandles()) {
      const r = state.hoverHandle === h.kind || (state.drag && state.drag.kind === h.kind) ? 7 : 5;
      ctx.beginPath();
      ctx.rect(h.x * z - r, h.y * z - r, r * 2, r * 2);
      ctx.fillStyle = "#ffcc33";
      ctx.fill();
      ctx.strokeStyle = "#111";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = "#ffe9a0";
      ctx.font = `${Math.max(10, 11 * z)}px sans-serif`;
      ctx.fillText(h.kind.toUpperCase(), h.x * z + 8, h.y * z + 4);
    }
  }

  updatePills();
}

canvas.addEventListener("mousedown", (ev) => {
  if (!state.img) return;
  if (state.mode !== "move" && state.mode !== "resize") return;
  if (!hasGrid()) {
    setStatus("No grid yet — annotate COLS/ROWS first, or keep grid from a prior page.");
    return;
  }
  const p = canvasPoint(ev);
  if (state.mode === "resize") {
    if (!canResize()) {
      setStatus("Need at least 2 cols or 2 rows to resize.");
      return;
    }
    const kind = hitHandle(p);
    if (!kind) {
      setStatus("Grab an E / S / SE handle (yellow squares).");
      return;
    }
    ev.preventDefault();
    pushHistory();
    state.drag = {
      type: "resize",
      kind,
      anchorX: state.cols[0] ?? 0,
      anchorY: state.rows[0] ?? 0,
      originLastX: state.cols.length
        ? state.cols[state.cols.length - 1]
        : (state.cols[0] ?? 0),
      originLastY: state.rows.length
        ? state.rows[state.rows.length - 1]
        : (state.rows[0] ?? 0),
      originCols: state.cols.slice(),
      originRows: state.rows.slice(),
    };
    state.didDrag = false;
    setResizeCursor(kind);
    $("viewport").classList.add("dragging");
    return;
  }
  ev.preventDefault();
  pushHistory();
  state.drag = {
    type: "move",
    startX: p.x,
    startY: p.y,
    originCols: state.cols.slice(),
    originRows: state.rows.slice(),
  };
  state.didDrag = false;
  $("viewport").classList.add("dragging");
});

canvas.addEventListener("mousemove", (ev) => {
  const p = canvasPoint(ev);
  if (!state.drag) {
    if (state.mode === "resize" && hasGrid()) {
      const kind = hitHandle(p);
      if (kind !== state.hoverHandle) {
        state.hoverHandle = kind;
        setResizeCursor(kind);
        redraw();
      }
    }
    return;
  }
  if (state.drag.type === "move") {
    const dx = p.x - state.drag.startX;
    const dy = p.y - state.drag.startY;
    if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) state.didDrag = true;
    state.cols = state.drag.originCols.map((x) => x + dx);
    state.rows = state.drag.originRows.map((y) => y + dy);
    setStatus(
      `Moved Δx=${Math.round(dx)} Δy=${Math.round(dy)}  (release to lock)`,
    );
  } else if (state.drag.type === "resize") {
    const { sx, sy } = applyResizeFromDrag(p);
    state.didDrag = true;
    setStatus(
      `Resize ${state.drag.kind.toUpperCase()}  sx=${sx.toFixed(3)} sy=${sy.toFixed(3)}`,
    );
  }
  redraw();
});

function endDrag() {
  if (!state.drag) return;
  const d = state.drag;
  state.drag = null;
  $("viewport").classList.remove("dragging");
  if (d.type === "move" && state.didDrag) {
    const dx = (state.cols[0] ?? 0) - (d.originCols[0] ?? 0);
    const dy = (state.rows[0] ?? 0) - (d.originRows[0] ?? 0);
    setStatus(`Grid shifted Δx=${Math.round(dx)} Δy=${Math.round(dy)}`);
  } else if (d.type === "resize" && state.didDrag) {
    const ax = d.anchorX;
    const ay = d.anchorY;
    const sx =
      Math.abs(d.originLastX - ax) > 1e-3
        ? ((state.cols[state.cols.length - 1] ?? ax) - ax) / (d.originLastX - ax)
        : 1;
    const sy =
      Math.abs(d.originLastY - ay) > 1e-3
        ? ((state.rows[state.rows.length - 1] ?? ay) - ay) / (d.originLastY - ay)
        : 1;
    setStatus(`Resized sx=${sx.toFixed(3)} sy=${sy.toFixed(3)}`);
  }
  redraw();
}

canvas.addEventListener("mouseup", endDrag);
canvas.addEventListener("mouseleave", () => {
  endDrag();
  if (state.mode === "resize") {
    state.hoverHandle = null;
    clearResizeCursor();
  }
});

canvas.addEventListener("click", (ev) => {
  if (!state.img) return;
  if (state.mode === "move" || state.mode === "resize") return;
  if (state.didDrag) {
    state.didDrag = false;
    return;
  }
  const p = canvasPoint(ev);
  pushHistory();
  if (state.mode === "cols") {
    if (state.cols.length >= 5) state.cols = [];
    state.cols.push(p.x);
    setStatus(
      `COL ${LETTERS[state.cols.length - 1]} = ${Math.round(p.x)}  (${state.cols.length}/5)`,
    );
    if (state.cols.length === 5) setMode("rows");
  } else {
    if (state.rows.length >= 20) {
      setStatus("Already have 20 rows — Clear mode or Undo.");
      return;
    }
    state.rows.push(p.y);
    setStatus(
      `ROW Q${state.rows.length} fill-top Y = ${Math.round(p.y)}  (${state.rows.length}/20)`,
    );
  }
  redraw();
});

document.addEventListener("keydown", (ev) => {
  if (!hasGrid()) return;
  if (ev.target && /INPUT|TEXTAREA|SELECT/.test(ev.target.tagName)) return;
  const step = ev.shiftKey ? 5 : 1;
  if (state.mode === "move") {
    let dx = 0;
    let dy = 0;
    if (ev.key === "ArrowLeft") dx = -step;
    else if (ev.key === "ArrowRight") dx = step;
    else if (ev.key === "ArrowUp") dy = -step;
    else if (ev.key === "ArrowDown") dy = step;
    else return;
    ev.preventDefault();
    pushHistory();
    translateGrid(dx, dy);
    setStatus(`Nudged Δx=${dx} Δy=${dy}`);
    redraw();
    return;
  }
  if (state.mode === "resize") {
    let dx = 0;
    let dy = 0;
    if (ev.key === "ArrowLeft") dx = -step;
    else if (ev.key === "ArrowRight") dx = step;
    else if (ev.key === "ArrowUp") dy = -step;
    else if (ev.key === "ArrowDown") dy = step;
    else return;
    ev.preventDefault();
    if (!canResize()) return;
    pushHistory();
    const { sx, sy } = nudgeFarEdge(dx, dy);
    setStatus(`Edge nudge → sx=${sx.toFixed(3)} sy=${sy.toFixed(3)}`);
    redraw();
  }
});

$("modeCols").onclick = () => setMode("cols");
$("modeRows").onclick = () => setMode("rows");
$("modeMove").onclick = () => {
  if (!hasGrid()) {
    setStatus("Nothing to move yet — place COLS/ROWS first.");
    return;
  }
  setMode("move");
};
$("modeResize").onclick = () => {
  if (!canResize()) {
    setStatus("Need at least 2 cols or 2 rows to resize.");
    return;
  }
  setMode("resize");
};
$("undoBtn").onclick = () => {
  const prev = state.history.pop();
  if (!prev) return;
  const s = JSON.parse(prev);
  state.cols = s.cols;
  state.rows = s.rows;
  redraw();
};
$("clearModeBtn").onclick = () => {
  pushHistory();
  if (state.mode === "cols") state.cols = [];
  else if (state.mode === "rows") state.rows = [];
  else {
    state.cols = [];
    state.rows = [];
  }
  redraw();
};
$("clearAllBtn").onclick = () => {
  pushHistory();
  state.cols = [];
  state.rows = [];
  redraw();
};
$("fitRowsBtn").onclick = () => {
  if (state.rows.length < 2) {
    setStatus("Need at least two row clicks (e.g. Q1 and Q20) to fit 20 rows.");
    return;
  }
  pushHistory();
  const y0 = state.rows[0];
  const y1 = state.rows[state.rows.length - 1];
  state.rows = Array.from({ length: 20 }, (_, i) => y0 + ((y1 - y0) * i) / 19);
  setStatus(`Interpolated 20 rows from Y=${Math.round(y0)} → ${Math.round(y1)}`);
  redraw();
};
$("cellW").oninput = $("cellH").oninput = $("bubH").oninput = () => redraw();
$("zoom").oninput = () => {
  state.zoom = (+$("zoom").value || 50) / 100;
  redraw();
};
$("zoomIn").onclick = () => {
  $("zoom").value = Math.min(200, +$("zoom").value + 10);
  state.zoom = +$("zoom").value / 100;
  redraw();
};
$("zoomOut").onclick = () => {
  $("zoom").value = Math.max(10, +$("zoom").value - 10);
  state.zoom = +$("zoom").value / 100;
  redraw();
};
$("imageSelect").onchange = (e) => loadImage(e.target.value);
$("loadPathBtn").onclick = () => loadImage($("pathInput").value.trim());
$("saveBtn").onclick = async () => {
  if (state.cols.length !== 5) {
    setStatus("Need 5 column clicks.");
    return;
  }
  if (state.rows.length !== 20) {
    setStatus("Need 20 row clicks (or Fit rows).");
    return;
  }
  const payload = buildPayload();
  const r = await fetch("/api/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await r.json();
  if (data.ok) setStatus("Saved → " + data.path);
  else setStatus("Save failed: " + (data.error || r.status));
};

listImages();
setMode("cols");
state.zoom = 0.5;
