/* MCQ grid annotator UI — no innerHTML; safe DOM updates only.
 *
 * A sheet is annotated as one or more BLOCKS. A block is a visual column of
 * questions: its own option X centers plus its own fill-top Ys. Format-B
 * papers are one block of 20 questions over A–E; Paper K is two blocks of
 * 10 and 5 over A–C. That mirrors the template schema, where `cols` counts
 * visual question columns and `col_positions` holds one entry per block.
 */
const ALPHABET = ["A", "B", "C", "D", "E", "F", "G", "H"];

const state = {
  mode: "cols",
  blocks: [{ cols: [], rows: [] }],
  active: 0,
  img: null,
  imgPath: null,
  imgNatural: { w: 0, h: 0 },
  zoom: 0.5,
  history: [],
  drag: null, // move or resize drag state
  didDrag: false,
  hoverHandle: null, // 'e' | 's' | 'se' | null
};

const $ = (id) => document.getElementById(id);
const canvas = $("cv");
const ctx = canvas.getContext("2d");

function setStatus(msg) {
  $("status").textContent = msg;
}

/** Read a numeric field, falling back only when it is blank or unparseable.
 *
 * `+value || fallback` would turn a deliberate 0 into the fallback, and
 * cell_height 0 is real: on Paper K the row position IS the fill top, with no
 * label band above it. That silently shifted every row by the fallback.
 */
function numField(id, fallback) {
  const raw = $(id).value;
  if (raw === "" || raw == null) return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

function cellW() {
  return numField("cellW", 36);
}
function cellH() {
  return numField("cellH", 36);
}
function bubH() {
  return numField("bubH", 32);
}

/** Option labels, e.g. A–E for format B or A–C for Paper K. */
function letters() {
  const n = Math.min(ALPHABET.length, Math.max(2, +$("optCount").value || 5));
  return ALPHABET.slice(0, n);
}

/** Questions per visual column, e.g. [20] or [10, 5]. */
function questionsPerCol() {
  const raw = ($("qpc").value || "20")
    .split(",")
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => Number.isFinite(n) && n > 0);
  return raw.length ? raw : [20];
}

function blockCount() {
  return questionsPerCol().length;
}

function totalQuestions() {
  return questionsPerCol().reduce((a, b) => a + b, 0);
}

/** Grow/shrink the block list to match questions-per-column. */
function syncBlocks() {
  const want = blockCount();
  while (state.blocks.length < want) state.blocks.push({ cols: [], rows: [] });
  if (state.blocks.length > want) state.blocks.length = want;
  if (state.active >= want) state.active = want - 1;
  renderBlockButtons();
}

function block() {
  return state.blocks[state.active];
}

function allCols() {
  return state.blocks.flatMap((b) => b.cols);
}
function allRows() {
  return state.blocks.flatMap((b) => b.rows);
}

function pushHistory() {
  state.history.push(JSON.stringify({ blocks: state.blocks, active: state.active }));
  if (state.history.length > 80) state.history.shift();
}

function buildPayload() {
  // Clicks are fill-top Y. Extractor row_positions are label-band tops:
  // fill_y = row_y + cell_height  ⇒  row_y = fill_y - cell_height
  const ch = cellH();
  const row_positions = state.blocks.flatMap((b) =>
    b.rows.map((y) => Math.round(y - ch)),
  );
  const col_positions = state.blocks.map((b) => b.cols.map((x) => Math.round(x)));
  // Pitch is a property of one column, so measure it inside the first block
  // that has enough rows rather than across the concatenated list.
  const ref = state.blocks.find((b) => b.rows.length >= 2);
  const pitch = ref
    ? (ref.rows[ref.rows.length - 1] - ref.rows[0]) / Math.max(1, ref.rows.length - 1)
    : 80;
  return {
    source_image: state.imgPath,
    image_size: state.imgNatural,
    reference_dpi_note:
      "Coordinates are in this image pixel space (~300 DPI pages).",
    click_convention:
      "cols = fill center X per block; rows clicks = fill-top Y per block; " +
      "row_positions = fill_y - cell_height, blocks concatenated in order",
    grid: {
      rows: totalQuestions(),
      cols: blockCount(),
      questions_per_col: questionsPerCol(),
      options: letters(),
      row_positions,
      col_positions,
      cell_width: cellW(),
      cell_height: ch,
      bubble_height: bubH(),
      row_pitch: Math.round(pitch * 10) / 10,
      first_row_offset: 38,
    },
    raw_clicks: {
      blocks: state.blocks.map((b) => ({
        col_xs: b.cols.slice(),
        fill_top_ys: b.rows.slice(),
      })),
    },
  };
}

function blockComplete(i) {
  const b = state.blocks[i];
  return b.cols.length === letters().length && b.rows.length === questionsPerCol()[i];
}

function allComplete() {
  return state.blocks.every((_, i) => blockComplete(i));
}

function renderBlockButtons() {
  const host = $("blockBar");
  while (host.firstChild) host.removeChild(host.firstChild);
  if (blockCount() < 2) {
    host.classList.add("hidden");
    return;
  }
  host.classList.remove("hidden");
  state.blocks.forEach((_, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = `COL ${i + 1}`;
    btn.classList.toggle("active", i === state.active);
    btn.classList.toggle("done", blockComplete(i));
    btn.onclick = () => {
      state.active = i;
      setMode("cols");
      redraw();
    };
    host.appendChild(btn);
  });
}

function updatePills() {
  const b = block();
  const nOpt = letters().length;
  const nRow = questionsPerCol()[state.active];
  const pc = $("pillCols");
  const pr = $("pillRows");
  const tag = blockCount() > 1 ? `C${state.active + 1} ` : "";
  pc.textContent = `${tag}cols ${b.cols.length}/${nOpt}`;
  pr.textContent = `${tag}rows ${b.rows.length}/${nRow}`;
  pc.classList.toggle("on", b.cols.length === nOpt);
  pr.classList.toggle("on", b.rows.length === nRow);
  $("coords").textContent = JSON.stringify(buildPayload().grid, null, 2);
  renderBlockButtons();
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
  const L = letters();
  const nRow = questionsPerCol()[state.active];
  const which = blockCount() > 1 ? ` for column ${state.active + 1}` : "";
  const hints = {
    cols: `Click fill centers for ${L.join(", ")} on one row${which}.`,
    rows: `Click the TOP-CENTER of each fill bubble Q1→Q${nRow}${which} (or first + last, then Fit rows).`,
    move: "Drag to shift every block together. Arrow keys nudge 1px (Shift = 5px).",
    resize:
      "Drag E / S / SE handles (anchor = first block's Q1/A). Arrows nudge far edge 1px (Shift = 5).",
  };
  $("modeHint").textContent = hints[m] || "";
  $("modeCols").textContent = `COLS ${L[0]}–${L[L.length - 1]}`;
  $("modeRows").textContent = `ROWS Q1–${nRow}`;
  $("fitRowsBtn").textContent = `Fit rows Q1→Q${nRow}`;
  updatePills();
}

function canvasPoint(ev) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (ev.clientX - rect.left) / state.zoom,
    y: (ev.clientY - rect.top) / state.zoom,
  };
}

function translateGrid(dx, dy) {
  for (const b of state.blocks) {
    b.cols = b.cols.map((x) => x + dx);
    b.rows = b.rows.map((y) => y + dy);
  }
}

function scaleGridFromAnchor(sx, sy, ax, ay) {
  for (const b of state.blocks) {
    b.cols = b.cols.map((x) => ax + (x - ax) * sx);
    b.rows = b.rows.map((y) => ay + (y - ay) * sy);
  }
}

function hasGrid() {
  return allCols().length > 0 || allRows().length > 0;
}

function canResize() {
  return allCols().length >= 2 || allRows().length >= 2;
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

/** Anchor is the first block's Q1/A so scaling keeps the sheet origin fixed. */
function anchorPoint() {
  const first = state.blocks.find((b) => b.cols.length || b.rows.length) || block();
  return { ax: first.cols[0] ?? 0, ay: first.rows[0] ?? 0 };
}

/** Handle positions in image coords (fill centers / fill tops). */
function resizeHandles() {
  if (!canResize()) return [];
  const cols = allCols();
  const rows = allRows();
  const { ax, ay } = anchorPoint();
  const lx = cols.length ? Math.max(...cols) : ax + 100;
  const ly = rows.length ? Math.max(...rows) : ay + 100;
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

function cloneBlocks(blocks) {
  return blocks.map((b) => ({ cols: b.cols.slice(), rows: b.rows.slice() }));
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
  state.blocks = d.originBlocks.map((b) => ({
    cols: b.cols.map((x) => ax + (x - ax) * sx),
    rows: b.rows.map((y) => ay + (y - ay) * sy),
  }));
  return { sx, sy };
}

/** Nudge the far edge by pixels (keeps the anchor fixed). */
function nudgeFarEdge(dx, dy) {
  const cols = allCols();
  const rows = allRows();
  const { ax, ay } = anchorPoint();
  let sx = 1;
  let sy = 1;
  if (dx && cols.length >= 2) {
    const span = Math.max(...cols) - ax;
    if (Math.abs(span) > 1e-3) sx = Math.max(0.05, (span + dx) / span);
  }
  if (dy && rows.length >= 2) {
    const span = Math.max(...rows) - ay;
    if (Math.abs(span) > 1e-3) sy = Math.max(0.05, (span + dy) / span);
  }
  scaleGridFromAnchor(sx, sy, ax, ay);
  return { sx, sy };
}

function fillSelect(sel, images) {
  while (sel.firstChild) sel.removeChild(sel.firstChild);
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "— pick a page —";
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

function drawBlock(b, bi, z) {
  const cw = cellW();
  const bh = bubH();
  const L = letters();
  const qOffset = questionsPerCol()
    .slice(0, bi)
    .reduce((a, n) => a + n, 0);
  const isActive = bi === state.active;

  if (b.cols.length === L.length && b.rows.length > 0) {
    for (let i = 0; i < b.rows.length; i++) {
      const fillTop = b.rows[i];
      for (let c = 0; c < L.length; c++) {
        const cx = b.cols[c];
        ctx.strokeStyle = isActive ? "rgba(0,220,80,0.9)" : "rgba(0,160,220,0.75)";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(
          (cx - cw / 2) * z,
          fillTop * z,
          cw * z,
          bh * z,
        );
      }
      ctx.fillStyle = "rgba(0,0,0,0.65)";
      ctx.fillRect(b.cols[0] * z - 70 * z, fillTop * z, 60 * z, 16 * z);
      ctx.fillStyle = isActive ? "#7CFF9A" : "#8ad4ff";
      ctx.font = `${Math.max(10, 12 * z)}px sans-serif`;
      ctx.fillText(
        `Q${qOffset + i + 1}`,
        b.cols[0] * z - 65 * z,
        fillTop * z + 12 * z,
      );
    }
    L.forEach((letter, i) => {
      ctx.fillStyle = "#fff";
      ctx.font = `bold ${Math.max(11, 13 * z)}px sans-serif`;
      ctx.fillText(letter, b.cols[i] * z - 5 * z, b.rows[0] * z - 8 * z);
    });
  }

  b.cols.forEach((x, i) => {
    const yMark = b.rows[0] ? b.rows[0] + bubH() / 2 : 80;
    ctx.beginPath();
    ctx.arc(x * z, yMark * z, 4, 0, Math.PI * 2);
    ctx.fillStyle = isActive ? "#5b9fd4" : "#39627f";
    ctx.fill();
    ctx.fillText(L[i] || "?", x * z - 4, (b.rows[0] ? b.rows[0] - 10 : 70) * z);
  });

  b.rows.forEach((y) => {
    const x0 = b.cols[0] ?? 40;
    const x1 = b.cols.length ? b.cols[b.cols.length - 1] + 40 : x0 + 200;
    ctx.beginPath();
    ctx.moveTo(x0 * z - 20, y * z);
    ctx.lineTo(x1 * z, y * z);
    ctx.strokeStyle = isActive ? "rgba(255,200,0,0.5)" : "rgba(120,170,255,0.35)";
    ctx.stroke();
  });
}

function redraw() {
  if (!state.img) return;
  const z = state.zoom;
  canvas.width = Math.round(state.img.naturalWidth * z);
  canvas.height = Math.round(state.img.naturalHeight * z);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(state.img, 0, 0, canvas.width, canvas.height);

  state.blocks.forEach((b, i) => drawBlock(b, i, z));

  if (state.mode === "resize") {
    const { ax, ay } = anchorPoint();
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
      const r =
        state.hoverHandle === h.kind || (state.drag && state.drag.kind === h.kind)
          ? 7
          : 5;
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
    const { ax, ay } = anchorPoint();
    const cols = allCols();
    const rows = allRows();
    state.drag = {
      type: "resize",
      kind,
      anchorX: ax,
      anchorY: ay,
      originLastX: cols.length ? Math.max(...cols) : ax,
      originLastY: rows.length ? Math.max(...rows) : ay,
      originBlocks: cloneBlocks(state.blocks),
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
    originBlocks: cloneBlocks(state.blocks),
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
    state.blocks = state.drag.originBlocks.map((b) => ({
      cols: b.cols.map((x) => x + dx),
      rows: b.rows.map((y) => y + dy),
    }));
    setStatus(`Moved Δx=${Math.round(dx)} Δy=${Math.round(dy)}  (release to lock)`);
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
    const o = d.originBlocks[0] || { cols: [], rows: [] };
    const n = state.blocks[0] || { cols: [], rows: [] };
    const dx = (n.cols[0] ?? 0) - (o.cols[0] ?? 0);
    const dy = (n.rows[0] ?? 0) - (o.rows[0] ?? 0);
    setStatus(`Grid shifted Δx=${Math.round(dx)} Δy=${Math.round(dy)}`);
  } else if (d.type === "resize" && state.didDrag) {
    const cols = allCols();
    const rows = allRows();
    const sx =
      Math.abs(d.originLastX - d.anchorX) > 1e-3
        ? ((cols.length ? Math.max(...cols) : d.anchorX) - d.anchorX) /
          (d.originLastX - d.anchorX)
        : 1;
    const sy =
      Math.abs(d.originLastY - d.anchorY) > 1e-3
        ? ((rows.length ? Math.max(...rows) : d.anchorY) - d.anchorY) /
          (d.originLastY - d.anchorY)
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
  const b = block();
  const L = letters();
  const nRow = questionsPerCol()[state.active];
  pushHistory();
  if (state.mode === "cols") {
    if (b.cols.length >= L.length) b.cols = [];
    b.cols.push(p.x);
    setStatus(
      `COL ${L[b.cols.length - 1]} = ${Math.round(p.x)}  (${b.cols.length}/${L.length})`,
    );
    if (b.cols.length === L.length) setMode("rows");
  } else {
    if (b.rows.length >= nRow) {
      setStatus(`Already have ${nRow} rows for this column — Clear mode or Undo.`);
      return;
    }
    b.rows.push(p.y);
    setStatus(
      `ROW Q${b.rows.length} fill-top Y = ${Math.round(p.y)}  (${b.rows.length}/${nRow})`,
    );
    // Walking straight into the next column keeps a two-column sheet flowing.
    if (b.rows.length === nRow && state.active < state.blocks.length - 1) {
      state.active += 1;
      setMode("cols");
      setStatus(`Column ${state.active} done — now place column ${state.active + 1}.`);
    }
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
  state.blocks = s.blocks;
  state.active = Math.min(s.active ?? 0, state.blocks.length - 1);
  redraw();
};
$("clearModeBtn").onclick = () => {
  pushHistory();
  const b = block();
  if (state.mode === "cols") b.cols = [];
  else if (state.mode === "rows") b.rows = [];
  else {
    b.cols = [];
    b.rows = [];
  }
  redraw();
};
$("clearAllBtn").onclick = () => {
  pushHistory();
  state.blocks = questionsPerCol().map(() => ({ cols: [], rows: [] }));
  state.active = 0;
  redraw();
};
$("fitRowsBtn").onclick = () => {
  const b = block();
  const nRow = questionsPerCol()[state.active];
  if (b.rows.length < 2) {
    setStatus(`Need at least two row clicks (e.g. Q1 and Q${nRow}) to fit this column.`);
    return;
  }
  pushHistory();
  const y0 = b.rows[0];
  const y1 = b.rows[b.rows.length - 1];
  b.rows = Array.from(
    { length: nRow },
    (_, i) => y0 + ((y1 - y0) * i) / Math.max(1, nRow - 1),
  );
  setStatus(`Interpolated ${nRow} rows from Y=${Math.round(y0)} → ${Math.round(y1)}`);
  redraw();
};
$("cellW").oninput = $("cellH").oninput = $("bubH").oninput = () => redraw();
$("optCount").oninput = () => {
  // Shrinking the option count must not leave stale X centers behind.
  const n = letters().length;
  for (const b of state.blocks) if (b.cols.length > n) b.cols.length = n;
  setMode(state.mode);
  redraw();
};
$("qpc").oninput = () => {
  syncBlocks();
  const qpc = questionsPerCol();
  state.blocks.forEach((b, i) => {
    if (b.rows.length > qpc[i]) b.rows.length = qpc[i];
  });
  setMode(state.mode);
  redraw();
};
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
  if (!allComplete()) {
    const missing = state.blocks
      .map((b, i) =>
        blockComplete(i)
          ? null
          : `col ${i + 1}: ${b.cols.length}/${letters().length} cols, ` +
            `${b.rows.length}/${questionsPerCol()[i]} rows`,
      )
      .filter(Boolean);
    setStatus("Incomplete — " + missing.join("; "));
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
syncBlocks();
setMode("cols");
state.zoom = 0.5;
