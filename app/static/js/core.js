// Split from index.html — classic scripts share the global scope;
// load order in index.html is load-bearing. See knowledge_base/02_architecture.md.
// ── State ──────────────────────────────────────────────────────────────────
const API = '';
let folder        = '';
let ocrViewActive = false;
let ocrSettings   = {
  line_removal_fraction:  0.22,
  use_adaptive_threshold: true,
  adaptive_block_size:    15,
  adaptive_c:             10,
  morph_open_kernel:      2,
};
let pages         = [];
let allJsonStems  = [];   // all JSON stems in folder, no image requirement
let pageIdx       = 0;
let pageData      = null;
let selIdx        = -1;          // primary selected shape (for panel display)
let selSet        = new Set();   // all selected indices
let editMode      = false;
let dragState     = null;        // active drag: {type,idx,handle,startX,startY,origPts}
let dragCurrentPts = null;       // live-updated points during drag (primary shape)
let lastUsedLabel  = null;       // remembered label for new box drawing
let projectLabels  = [];         // labels from project config (passed via URL)
let clipboard      = null;       // copied shape for Ctrl+V
const undoStack   = [];          // array of shapes-array snapshots
let flaggedOverlaps    = new Set(); // indices of shapes with excessive overlap
let latticeVisible     = false;     // whether to draw lattice grid overlay
let latticeSepMode     = null;      // 'col' | 'row' | null — lattice separator insertion mode
let latticeSplitMode   = false;     // click an interior grid line to split the table in two
let diagnosticMode      = 'none';
let diagnosticFlagged   = new Set();
let diagnosticRowCounts = {}; // shape index → line count (for row-mismatch modes)
let diagnosticRuleRows  = {}; // shape index → Set of violated internal row indices (rule modes)
let llmProgress     = null;      // {cropOriginX, cropOriginY, cropRight, lines[], activeRow, emptyRows}
let llmAbortCtrl    = null;      // AbortController for the active line-by-line run
let batchAbort      = false;     // flag to stop a running "All" batch
let batchHighlight  = -1;        // shape index highlighted during batch processing
let lastRowLines    = null;      // persists detected rows on the crop panel after a run
let lastEmptyRows   = new Set(); // persists empty-row classification after llmProgress clears
let _lastPanelIdx   = -2;        // tracks when selection changes, to clear lastRowLines

// ── Toast ────────────────────────────────────────────────────────────────────
// ── Panel collapse ───────────────────────────────────────────────────────────
function togglePanel() {
  const panel = document.getElementById('panel');
  const btn   = document.getElementById('panel-toggle');
  const collapsed = panel.classList.toggle('panel-collapsed');
  btn.textContent = collapsed ? '»' : '«';
}

// Drag the left edge of the Cell inspector to resize its width. Width is driven
// by the --panel-w CSS variable (not inline `width`) so the collapse rule still
// wins; the chosen width persists in localStorage.
function startPanelResize(e) {
  e.preventDefault();
  const panel = document.getElementById('panel');
  if (panel.classList.contains('panel-collapsed')) return;
  const startX = e.clientX;
  const startW = panel.getBoundingClientRect().width;
  const prevTransition = panel.style.transition;
  panel.style.transition = 'none';
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
  const move = (ev) => {
    const w = Math.min(Math.max(220, startW + (startX - ev.clientX)),
                       Math.round(window.innerWidth * 0.7));
    panel.style.setProperty('--panel-w', w + 'px');
  };
  const up = () => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
    panel.style.transition = prevTransition;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    try { localStorage.setItem('panelWidth', parseInt(panel.style.getPropertyValue('--panel-w')) || ''); } catch (e) {}
  };
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
}

function _restorePanelWidth() {
  try {
    const w = parseInt(localStorage.getItem('panelWidth'));
    if (w >= 220) document.getElementById('panel')?.style.setProperty('--panel-w', w + 'px');
  } catch (e) {}
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _restorePanelWidth);
else _restorePanelWidth();

function togglePreproc() {
  const col = document.getElementById('preproc');
  const btn = document.getElementById('preproc-toggle');
  const collapsed = col.classList.toggle('preproc-collapsed');
  btn.textContent = collapsed ? '»' : '«';
  if (!collapsed) {
    const first = document.querySelector('#preproc-rows img');
    col.style.width = (first?.naturalWidth)
      ? Math.min(420, Math.max(80, first.naturalWidth + 20)) + 'px'
      : '160px';
  } else {
    col.style.width = '28px';
  }
}

function toggleInspector() {
  const col = document.getElementById('inspector');
  const btn = document.getElementById('inspector-toggle');
  const collapsed = col.classList.toggle('inspector-collapsed');
  btn.textContent = collapsed ? '»' : '«';
  if (collapsed) {
    col.style.width = '28px';
  } else {
    const img = document.getElementById('cell-crop');
    col.style.width = img.naturalWidth
      ? Math.min(420, Math.max(80, img.naturalWidth + 20)) + 'px'
      : '80px';
  }
}

// ── OCR View ─────────────────────────────────────────────────────────────────
async function loadOcrSettings() {
  try {
    const r = await fetch(`${API}/api/ocr-settings`);
    if (r.ok) { ocrSettings = await r.json(); _ocrSettingsToUI(); }
  } catch(e) {}
}

function _ocrSettingsToUI() {
  const pct = Math.round(ocrSettings.line_removal_fraction * 100);
  document.getElementById('ocr-opt-line-frac').value = pct;
  document.getElementById('ocr-opt-line-frac-val').textContent = pct + '%';

  const vpct = Math.round((ocrSettings.v_line_fraction ?? 0) * 100);
  document.getElementById('ocr-opt-vfrac').value = vpct;
  document.getElementById('ocr-opt-vfrac-val').textContent = vpct === 0 ? 'same' : vpct + '%';

  const useThresh = ocrSettings.use_adaptive_threshold;
  document.getElementById('ocr-opt-use-thresh').checked = useThresh;
  document.getElementById('ocr-thresh-params').style.display = useThresh ? '' : 'none';

  document.getElementById('ocr-opt-block').value = ocrSettings.adaptive_block_size;
  document.getElementById('ocr-opt-block-val').textContent = ocrSettings.adaptive_block_size;

  document.getElementById('ocr-opt-c').value = ocrSettings.adaptive_c;
  document.getElementById('ocr-opt-c-val').textContent = ocrSettings.adaptive_c;

  const m = ocrSettings.morph_open_kernel;
  document.getElementById('ocr-opt-morph').value = m;
  document.getElementById('ocr-opt-morph-val').textContent = m === 0 ? '0 (off)' : m;

  const lc = ocrSettings.line_close_kernel ?? 0;
  document.getElementById('ocr-opt-close').value = lc;
  document.getElementById('ocr-opt-close-val').textContent = lc === 0 ? '0 (off)' : lc;

  const ld = ocrSettings.line_dilate_thickness ?? 3;
  document.getElementById('ocr-opt-dilate').value = ld;
  document.getElementById('ocr-opt-dilate-val').textContent = ld;

  const b = ocrSettings.blur_sigma ?? 0;
  document.getElementById('ocr-opt-blur').value = b;
  document.getElementById('ocr-opt-blur-val').textContent = b === 0 ? '0 (off)' : b;

  document.getElementById('ocr-opt-output-binarize').checked = ocrSettings.output_binarize ?? false;

  const oo = ocrSettings.output_open_kernel ?? 0;
  document.getElementById('ocr-opt-out-open').value = oo;
  document.getElementById('ocr-opt-out-open-val').textContent = oo === 0 ? '0 (off)' : oo;
}

function ocrOptLiveUpdate() {
  const pct = parseInt(document.getElementById('ocr-opt-line-frac').value);
  document.getElementById('ocr-opt-line-frac-val').textContent = pct + '%';

  const vpct = parseInt(document.getElementById('ocr-opt-vfrac').value);
  document.getElementById('ocr-opt-vfrac-val').textContent = vpct === 0 ? 'same' : vpct + '%';

  const block = parseInt(document.getElementById('ocr-opt-block').value);
  document.getElementById('ocr-opt-block-val').textContent = block;

  const c = parseInt(document.getElementById('ocr-opt-c').value);
  document.getElementById('ocr-opt-c-val').textContent = c;

  const m = parseInt(document.getElementById('ocr-opt-morph').value);
  document.getElementById('ocr-opt-morph-val').textContent = m === 0 ? '0 (off)' : m;

  const useThresh = document.getElementById('ocr-opt-use-thresh').checked;
  document.getElementById('ocr-thresh-params').style.display = useThresh ? '' : 'none';

  const lc2 = parseInt(document.getElementById('ocr-opt-close').value);
  document.getElementById('ocr-opt-close-val').textContent = lc2 === 0 ? '0 (off)' : lc2;

  const ld2 = parseInt(document.getElementById('ocr-opt-dilate').value);
  document.getElementById('ocr-opt-dilate-val').textContent = ld2;

  const bl = parseInt(document.getElementById('ocr-opt-blur').value);
  document.getElementById('ocr-opt-blur-val').textContent = bl === 0 ? '0 (off)' : bl;

  const oo = parseInt(document.getElementById('ocr-opt-out-open').value);
  document.getElementById('ocr-opt-out-open-val').textContent = oo === 0 ? '0 (off)' : oo;
}

function _ocrSettingsFromUI() {
  return {
    line_removal_fraction:  parseInt(document.getElementById('ocr-opt-line-frac').value) / 100,
    v_line_fraction:        parseInt(document.getElementById('ocr-opt-vfrac').value) / 100,
    use_adaptive_threshold: document.getElementById('ocr-opt-use-thresh').checked,
    adaptive_block_size:    parseInt(document.getElementById('ocr-opt-block').value),
    adaptive_c:             parseInt(document.getElementById('ocr-opt-c').value),
    morph_open_kernel:      parseInt(document.getElementById('ocr-opt-morph').value),
    line_close_kernel:      parseInt(document.getElementById('ocr-opt-close').value),
    line_dilate_thickness:  parseInt(document.getElementById('ocr-opt-dilate').value),
    blur_sigma:             parseInt(document.getElementById('ocr-opt-blur').value),
    output_binarize:        document.getElementById('ocr-opt-output-binarize').checked,
    output_open_kernel:     parseInt(document.getElementById('ocr-opt-out-open').value),
  };
}

async function _syncOcrSettings() {
  // Silently push the current slider values to the server before any OCR run,
  // so the user doesn't have to remember to click Apply first.
  try {
    await fetch(`${API}/api/ocr-settings`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(_ocrSettingsFromUI()),
    });
  } catch(e) {}
}

async function applyOcrSettings() {
  const status = document.getElementById('ocr-opts-status');
  status.style.color = '#888';
  status.textContent = 'Applying…';
  try {
    const r = await fetch(`${API}/api/ocr-settings`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(_ocrSettingsFromUI()),
    });
    if (r.ok) {
      ocrSettings = await r.json();
      status.textContent = 'Refreshing preview…';
      await loadShadowPreview();
      status.style.color = '#4caf50';
      status.textContent = 'Preview updated.';
    } else { status.style.color='#e94560'; status.textContent = 'Error ' + r.status; }
  } catch(e) { status.style.color='#e94560'; status.textContent = e.message; }
}

async function saveOcrSettings() {
  const status = document.getElementById('ocr-opts-status');
  status.style.color = '#888';
  status.textContent = 'Saving…';
  try {
    const r = await fetch(`${API}/api/ocr-settings?save=true`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(_ocrSettingsFromUI()),
    });
    if (r.ok) {
      ocrSettings = await r.json();
      await loadShadowPreview();
      status.style.color = '#4caf50';
      status.textContent = 'Saved as default.';
    } else { status.style.color='#e94560'; status.textContent = 'Error ' + r.status; }
  } catch(e) { status.style.color='#e94560'; status.textContent = e.message; }
}

async function loadShadowPreview() {
  if (!folder || !pages.length) return;
  const stem = pages[pageIdx].stem;
  document.getElementById('shadow-overlay').src =
    `${API}/api/shadow-preview?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(stem)}&t=${Date.now()}`;
}

// ── Shadow overlay zoom / pan ─────────────────────────────────────────────────
let _sz = 1, _spx = 0, _spy = 0, _sdrag = null;

function _shadowUpdateTransform() {
  const img = document.getElementById('shadow-overlay');
  img.style.transformOrigin = '0 0';
  img.style.transform = `translate(${_spx}px,${_spy}px) scale(${_sz})`;
}

function _shadowResetTransform() {
  _sz = 1; _spx = 0; _spy = 0; _sdrag = null;
  const img = document.getElementById('shadow-overlay');
  img.style.transform = '';
  img.style.transformOrigin = '';
  img.style.cursor = '';
}

function _shadowWheel(e) {
  e.preventDefault();
  const rect  = document.getElementById('osd-container').getBoundingClientRect();
  const mx    = e.clientX - rect.left;
  const my    = e.clientY - rect.top;
  const delta = e.deltaY < 0 ? 1.15 : (1 / 1.15);
  const nz    = Math.max(0.25, Math.min(30, _sz * delta));
  _spx = mx - (mx - _spx) * (nz / _sz);
  _spy = my - (my - _spy) * (nz / _sz);
  _sz  = nz;
  _shadowUpdateTransform();
}

function _shadowMouseDown(e) {
  if (e.button !== 0) return;
  _sdrag = { x: e.clientX, y: e.clientY, px: _spx, py: _spy };
  document.getElementById('shadow-overlay').style.cursor = 'grabbing';
}

function _shadowMouseMove(e) {
  if (!_sdrag) return;
  _spx = _sdrag.px + (e.clientX - _sdrag.x);
  _spy = _sdrag.py + (e.clientY - _sdrag.y);
  _shadowUpdateTransform();
}

function _shadowMouseUp() {
  if (!_sdrag) return;
  _sdrag = null;
  if (ocrViewActive) document.getElementById('shadow-overlay').style.cursor = 'grab';
}

function _shadowAddHandlers() {
  const c = document.getElementById('osd-container');
  c.addEventListener('wheel',     _shadowWheel,     { passive: false });
  c.addEventListener('mousedown', _shadowMouseDown);
  window.addEventListener('mousemove', _shadowMouseMove);
  window.addEventListener('mouseup',   _shadowMouseUp);
  document.getElementById('shadow-overlay').style.cursor = 'grab';
}

function _shadowRemoveHandlers() {
  const c = document.getElementById('osd-container');
  c.removeEventListener('wheel',     _shadowWheel);
  c.removeEventListener('mousedown', _shadowMouseDown);
  window.removeEventListener('mousemove', _shadowMouseMove);
  window.removeEventListener('mouseup',   _shadowMouseUp);
}

function toggleOcrView() {
  ocrViewActive = !ocrViewActive;
  const btn          = document.getElementById('ocr-view-btn');
  const overlay      = document.getElementById('shadow-overlay');
  const ocrOpts      = document.getElementById('ocr-opts');
  const noSel        = document.getElementById('no-selection');
  const fieldsContent= document.getElementById('fields-content');
  const panelTitle   = document.querySelector('#panel-header .panel-title');

  btn.classList.toggle('active', ocrViewActive);
  btn.textContent = ocrViewActive ? 'OCR View ✓' : 'OCR View';

  if (ocrViewActive) {
    overlay.style.display = 'block';
    ocrOpts.style.display = 'flex';
    noSel.style.display = 'none';
    fieldsContent.style.display = 'none';
    panelTitle.textContent = 'OCR Options';
    _ocrSettingsToUI();
    _shadowAddHandlers();
    loadShadowPreview();
  } else {
    _shadowRemoveHandlers();
    _shadowResetTransform();
    overlay.style.display = 'none';
    ocrOpts.style.display = 'none';
    panelTitle.textContent = 'Cell inspector';
    if (selIdx === -1) {
      noSel.style.display = '';
      fieldsContent.style.display = 'none';
    } else {
      noSel.style.display = 'none';
      fieldsContent.style.display = 'flex';
    }
  }
}

let _toastTimer = null;
function showToast(msg, ms=2500) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), ms);
}

// ── Perspective correction state ─────────────────────────────────────────────
let perspMode   = false;
let perspPoints = [];   // array of [x,y] in image coords (max 4)

// ── Table drawing state ──────────────────────────────────────────────────────
let tableMode    = false;
let tableRect    = null;   // {x1,y1,x2,y2} in image coords, or null
let tableColSeps = [];     // sorted x coords of vertical separators
let tableRowSeps = [];     // sorted y coords of horizontal separators
let tableTool    = null;   // 'col' | 'row' | 'delete' | null
const tableUndoStack = []; // snapshots of {tableRect, tableColSeps, tableRowSeps}

function pushTableUndo() {
  tableUndoStack.push({
    tableRect: tableRect ? {...tableRect} : null,
    tableColSeps: [...tableColSeps],
    tableRowSeps: [...tableRowSeps],
  });
  if (tableUndoStack.length > 50) tableUndoStack.shift();
}

function tableUndo() {
  if (!tableUndoStack.length) return;
  const s = tableUndoStack.pop();
  tableRect = s.tableRect; tableColSeps = s.tableColSeps; tableRowSeps = s.tableRowSeps;
  drawOverlay();
}

// ── Undo ────────────────────────────────────────────────────────────────────
function pushUndo() {
  undoStack.push(JSON.parse(JSON.stringify(pageData.shapes)));
  if (undoStack.length > 50) undoStack.shift();
}
async function undo() {
  if (!undoStack.length) return;
  const prev = undoStack.pop();
  pageData.shapes = prev;
  selIdx = -1; selSet.clear();
  const params = new URLSearchParams({folder, stem: pages[pageIdx].stem});
  await fetch(`${API}/api/page/shapes?${params}`, {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ shapes: prev }),
  });
  drawOverlay(); updatePanel();
}

// ── Color palette — fill = label type ──────────────────────────────────────
const PALETTE = [
  '#e94560','#4ecdc4','#ffe66d','#a8e6cf','#ff8b94',
  '#b39ddb','#80cbc4','#ffcc02','#f48fb1','#80deea',
];
let labelColors = {};
function colorFor(label) {
  if (!labelColors[label])
    labelColors[label] = PALETTE[Object.keys(labelColors).length % PALETTE.length];
  return labelColors[label];
}

// ── Processing status borders ───────────────────────────────────────────────
const STATUS_BORDER = {
  human: { color:'#4caf50', width:3   },
  llm:   { color:'#64b5f6', width:2.5 },
  ocr:   { color:'#ffe066', width:2   },
  none:  { color:'#888888', width:1.5 },
};
// OCR and LLM both exist but read the text differently — and no human has
// looked yet. These are the cells worth a second glance (amber border).
function _layersDisagree(shape) {
  const norm = t => (t || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const rows = shape.row_struct?.rows;
  if (rows?.length) {
    return rows.some(r => (r.ocr || '').trim() && (r.llm || '').trim()
                          && norm(r.ocr) !== norm(r.llm));
  }
  const o = shape.tesseract_output?.ocr_text, l = shape.openai_output?.response;
  return !!(o?.trim() && l?.trim() && norm(o) !== norm(l));
}

function statusBorders(shape, selected) {
  const hasHuman = !!shape.human_output?.human_corrected_text;
  const hasLLM   = !!shape.openai_output?.response;
  const hasOCR   = !!shape.tesseract_output?.ocr_text;
  if (selected && !editMode) return [{ color:'#ffffff', width:3, inset:0 }];
  if (hasHuman)         return [{ ...STATUS_BORDER.human, inset:0 }];
  if (hasLLM && hasOCR) {
    if (_layersDisagree(shape)) return [{ color:'#f59e0b', width:3, inset:0 }];
    return [
      { ...STATUS_BORDER.ocr, inset:0 },
      { ...STATUS_BORDER.llm, inset: STATUS_BORDER.ocr.width + 1 },
    ];
  }
  if (hasLLM) return [{ ...STATUS_BORDER.llm, inset:0 }];
  if (hasOCR) return [{ ...STATUS_BORDER.ocr, inset:0 }];
  return [{ ...STATUS_BORDER.none, inset:0 }];
}

// ── Resize handles ──────────────────────────────────────────────────────────
const HANDLES = ['nw','n','ne','e','se','s','sw','w'];
const HANDLE_SIZE = 8;
const HANDLE_CURSORS = {
  nw:'nw-resize', n:'n-resize', ne:'ne-resize', e:'e-resize',
  se:'se-resize', s:'s-resize', sw:'sw-resize', w:'w-resize',
};
function getHandleCenter(tl, br, h) {
  const cx=(tl.x+br.x)/2, cy=(tl.y+br.y)/2;
  return { nw:{x:tl.x,y:tl.y}, n:{x:cx,y:tl.y}, ne:{x:br.x,y:tl.y},
           e:{x:br.x,y:cy}, se:{x:br.x,y:br.y}, s:{x:cx,y:br.y},
           sw:{x:tl.x,y:br.y}, w:{x:tl.x,y:cy} }[h];
}

// ── OSD + SVG setup ─────────────────────────────────────────────────────────
let viewer = null;
let svgOverlay = null;

function initViewer() {
  if (viewer) viewer.destroy();
  labelColors = {}; editMode = false; dragState = null;

  viewer = OpenSeadragon({
    id: 'osd-container',
    prefixUrl: 'https://cdn.jsdelivr.net/npm/openseadragon@4.1/build/openseadragon/images/',
    showNavigationControl: true, showNavigator: true,
    navigatorPosition: 'BOTTOM_LEFT',
    zoomPerClick: 1,
    gestureSettingsMouse: { clickToZoom: false, scrollToZoom: false },
  });

  // Custom wheel-zoom handler on the container so it fires in all modes
  // (OSD's internal listener is on its inner canvas and misses events when
  // the SVG overlay has pointer-events:all)
  const _osdEl = document.getElementById('osd-container');
  _osdEl.addEventListener('wheel', e => {
    if (!viewer?.viewport) return;
    e.preventDefault();
    const r = _osdEl.getBoundingClientRect();
    const pt = viewer.viewport.viewerElementToViewportCoordinates(
      new OpenSeadragon.Point(e.clientX - r.left, e.clientY - r.top)
    );
    viewer.viewport.zoomBy(e.deltaY < 0 ? 1.15 : 1/1.15, pt, true);
  }, { passive: false });

  svgOverlay = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svgOverlay.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;overflow:visible;';
  document.getElementById('osd-container').appendChild(svgOverlay);

  viewer.addHandler('open',            () => drawOverlay());
  viewer.addHandler('animation',       () => drawOverlay());
  viewer.addHandler('animation-finish',() => drawOverlay());
  viewer.addHandler('resize',          () => drawOverlay());

  svgOverlay.addEventListener('mousedown', onSvgBackground);
  svgOverlay.addEventListener('contextmenu', e => e.preventDefault());
}

// ── Coordinate helpers ──────────────────────────────────────────────────────
function imgToScreen(x, y) {
  if (!viewer?.viewport || !pageData) return null;
  const vpPt = viewer.viewport.imageToViewportCoordinates(new OpenSeadragon.Point(x, y));
  return viewer.viewport.viewportToViewerElementCoordinates(vpPt);
}
function screenToImg(sx, sy) {
  const r = document.getElementById('osd-container').getBoundingClientRect();
  const vpPt = viewer.viewport.viewerElementToViewportCoordinates(new OpenSeadragon.Point(sx-r.left, sy-r.top));
  return viewer.viewport.viewportToImageCoordinates(vpPt);
}
function screenDeltaToImg(dx, dy) {
  const r  = document.getElementById('osd-container').getBoundingClientRect();
  const cx = r.left+r.width/2, cy = r.top+r.height/2;
  const p0 = screenToImg(cx, cy), p1 = screenToImg(cx+dx, cy+dy);
  return { dx: p1.x-p0.x, dy: p1.y-p0.y };
}

// ── Overlap detection ────────────────────────────────────────────────────────
function _shapeRect(shape) {
  const xs = shape.points.map(p => p[0]), ys = shape.points.map(p => p[1]);
  return { x1: Math.min(...xs), y1: Math.min(...ys), x2: Math.max(...xs), y2: Math.max(...ys) };
}
function _intersectionArea(a, b) {
  const ix1 = Math.max(a.x1, b.x1), iy1 = Math.max(a.y1, b.y1);
  const ix2 = Math.min(a.x2, b.x2), iy2 = Math.min(a.y2, b.y2);
  return ix1 < ix2 && iy1 < iy2 ? (ix2 - ix1) * (iy2 - iy1) : 0;
}
function findOverlaps() {
  if (!pageData?.shapes?.length) return;
  // Toggle off if frames are already showing
  if (flaggedOverlaps.size > 0) {
    flaggedOverlaps = new Set();
    selSet.clear(); selIdx = -1;
    drawOverlay(); updatePanel();
    showToast('Overlap highlights cleared');
    return;
  }
  const threshold = parseFloat(document.getElementById('overlap-threshold').value) / 100 || 0.5;
  const shapes = pageData.shapes;
  const rects  = shapes.map(_shapeRect);
  const areas  = rects.map(r => (r.x2 - r.x1) * (r.y2 - r.y1));
  flaggedOverlaps = new Set();
  // For each pair, if the smaller shape is mostly covered, flag the LARGER one
  for (let i = 0; i < shapes.length; i++) {
    if (areas[i] <= 0) continue;
    for (let j = i + 1; j < shapes.length; j++) {
      if (areas[j] <= 0) continue;
      const inter = _intersectionArea(rects[i], rects[j]);
      if (inter <= 0) continue;
      const minArea = Math.min(areas[i], areas[j]);
      if (inter / minArea >= threshold) {
        // The larger shape is the bad prediction — flag it
        flaggedOverlaps.add(areas[i] >= areas[j] ? i : j);
      }
    }
  }
  // Select all flagged shapes so Del works immediately
  selSet.clear();
  flaggedOverlaps.forEach(i => selSet.add(i));
  selIdx = flaggedOverlaps.size ? [...flaggedOverlaps][0] : -1;
  drawOverlay();
  updatePanel();
  const n = flaggedOverlaps.size;
  showToast(n > 0
    ? `${n} oversized shape${n > 1 ? 's' : ''} selected (orange) — review, then Del to delete`
    : 'No significant overlaps found');
}

async function trimOverlaps() {
  if (!pageData?.shapes?.length) return;
  const shapes = pageData.shapes;
  // Work with mutable rect copies so multi-overlap pairs resolve consistently
  const rects = shapes.map(_shapeRect);
  let trimmed = 0;
  for (let i = 0; i < shapes.length; i++) {
    if (rects[i].x2 <= rects[i].x1 || rects[i].y2 <= rects[i].y1) continue;
    for (let j = i + 1; j < shapes.length; j++) {
      if (rects[j].x2 <= rects[j].x1 || rects[j].y2 <= rects[j].y1) continue;
      const a = rects[i], b = rects[j];
      const ix1 = Math.max(a.x1, b.x1), iy1 = Math.max(a.y1, b.y1);
      const ix2 = Math.min(a.x2, b.x2), iy2 = Math.min(a.y2, b.y2);
      if (ix1 >= ix2 || iy1 >= iy2) continue;   // no overlap
      const overlapW = ix2 - ix1, overlapH = iy2 - iy1;
      if (overlapW <= overlapH) {
        // Thin vertical strip → move the shared X boundary to the midpoint
        const mid = Math.round((ix1 + ix2) / 2);
        if (a.x1 <= b.x1) { a.x2 = Math.min(a.x2, mid); b.x1 = Math.max(b.x1, mid); }
        else               { b.x2 = Math.min(b.x2, mid); a.x1 = Math.max(a.x1, mid); }
      } else {
        // Thin horizontal strip → move the shared Y boundary to the midpoint
        const mid = Math.round((iy1 + iy2) / 2);
        if (a.y1 <= b.y1) { a.y2 = Math.min(a.y2, mid); b.y1 = Math.max(b.y1, mid); }
        else               { b.y2 = Math.min(b.y2, mid); a.y1 = Math.max(a.y1, mid); }
      }
      trimmed++;
    }
  }
  if (!trimmed) { showToast('No overlaps found'); return; }
  // Write updated rects back as canonical [[x1,y1],[x2,y2]] points
  rects.forEach((r, i) => { shapes[i].points = [[r.x1, r.y1], [r.x2, r.y2]]; });
  drawOverlay(); updatePanel();
  await replaceAllShapes();
  showToast(`Trimmed ${trimmed} overlapping pair${trimmed !== 1 ? 's' : ''}`);
}

// ── Pure (no-UI) helpers for batch use ───────────────────────────────────────

/** Snap all lattice shapes in `shapes` in-place to their row/col band medians,
 *  independently per table (multiple lattices per page). */
function _snapShapesToGrid(shapes) {
  const all = shapes.filter(s => s.super_row != null && s.super_column != null && s.points?.length >= 2);
  if (!all.length) return;
  [...new Set(all.map(s => s.table ?? 0))].forEach(t => _snapOneTableToGrid(all.filter(s => (s.table ?? 0) === t)));
}

function _snapOneTableToGrid(sShapes) {
  if (!sShapes.length) return;
  const rowData = {}, colData = {};
  sShapes.forEach(s => {
    const xs = s.points.map(p => p[0]), ys = s.points.map(p => p[1]);
    const r = s.super_row, c = s.super_column;
    (rowData[r] ??= { tops: [], bots: [] }).tops.push(Math.min(...ys));
    rowData[r].bots.push(Math.max(...ys));
    (colData[c] ??= { lefts: [], rights: [] }).lefts.push(Math.min(...xs));
    colData[c].rights.push(Math.max(...xs));
  });
  const rowBands = {}, colBands = {};
  Object.entries(rowData).forEach(([r, d]) => rowBands[+r] = {
    top: Math.round(_median(d.tops)), bot: Math.round(_median(d.bots))
  });
  Object.entries(colData).forEach(([c, d]) => colBands[+c] = {
    left: Math.round(_median(d.lefts)), right: Math.round(_median(d.rights))
  });
  const sortedCols = Object.keys(colBands).map(Number).sort((a, b) => colBands[a].left - colBands[b].left);
  for (let i = 0; i < sortedCols.length - 1; i++)
    colBands[sortedCols[i]].right = colBands[sortedCols[i + 1]].left;
  const sortedRows = Object.keys(rowBands).map(Number).sort((a, b) => rowBands[a].top - rowBands[b].top);
  for (let i = 0; i < sortedRows.length - 1; i++)
    rowBands[sortedRows[i]].bot = rowBands[sortedRows[i + 1]].top;
  sShapes.forEach(s => {
    const rb = rowBands[s.super_row], cb = colBands[s.super_column];
    if (!rb || !cb) return;
    s.points = [[cb.left, rb.top], [cb.right, rb.bot]];
  });
}

/** Trim overlapping shape pairs in `shapes` in-place. Returns number of pairs trimmed. */
function _trimShapesOverlaps(shapes) {
  const rects = shapes.map(_shapeRect);
  let trimmed = 0;
  for (let i = 0; i < shapes.length; i++) {
    if (rects[i].x2 <= rects[i].x1 || rects[i].y2 <= rects[i].y1) continue;
    for (let j = i + 1; j < shapes.length; j++) {
      if (rects[j].x2 <= rects[j].x1 || rects[j].y2 <= rects[j].y1) continue;
      const a = rects[i], b = rects[j];
      const ix1 = Math.max(a.x1, b.x1), iy1 = Math.max(a.y1, b.y1);
      const ix2 = Math.min(a.x2, b.x2), iy2 = Math.min(a.y2, b.y2);
      if (ix1 >= ix2 || iy1 >= iy2) continue;
      const overlapW = ix2 - ix1, overlapH = iy2 - iy1;
      if (overlapW <= overlapH) {
        const mid = Math.round((ix1 + ix2) / 2);
        if (a.x1 <= b.x1) { a.x2 = Math.min(a.x2, mid); b.x1 = Math.max(b.x1, mid); }
        else               { b.x2 = Math.min(b.x2, mid); a.x1 = Math.max(a.x1, mid); }
      } else {
        const mid = Math.round((iy1 + iy2) / 2);
        if (a.y1 <= b.y1) { a.y2 = Math.min(a.y2, mid); b.y1 = Math.max(b.y1, mid); }
        else               { b.y2 = Math.min(b.y2, mid); a.y1 = Math.max(a.y1, mid); }
      }
      trimmed++;
    }
  }
  if (trimmed) rects.forEach((r, i) => { shapes[i].points = [[r.x1, r.y1], [r.x2, r.y2]]; });
  return trimmed;
}

// ── Diagnostic highlighting ───────────────────────────────────────────────────
function _lineCount(text) {
  return text ? text.split('\n').filter(l => l.trim()).length : 0;
}

// Parse a column list string (e.g. "1,3-5,7") into a Set of 1-indexed ints.
// Returns null if the string is blank (meaning "all columns").
// Parse a column filter string into a predicate col => bool.
// Supports: single numbers ("3"), closed ranges ("2-5"), open upper ("10-"),
// open lower ("-5"), and comma-separated combinations.
// Returns null (= no filter, accept all) if the string is blank or unparseable.
function _parseColSet(str) {
  if (!str?.trim()) return null;
  const ranges = [];
  for (const part of str.split(',')) {
    const p = part.trim();
    if (!p) continue;
    let m;
    if ((m = p.match(/^(\d+)-(\d+)$/)))  { ranges.push([+m[1], +m[2]]);       continue; }
    if ((m = p.match(/^(\d+)-$/)))        { ranges.push([+m[1], Infinity]);     continue; }
    if ((m = p.match(/^-(\d+)$/)))        { ranges.push([1,      +m[1]]);       continue; }
    if (/^\d+$/.test(p))                  { ranges.push([+p,     +p]);          continue; }
    // unrecognised token — ignore silently
  }
  if (!ranges.length) return null;
  return col => ranges.some(([lo, hi]) => col >= lo && col <= hi);
}


// ── Command palette (Ctrl+K) — every visible button, searchable ──────────────
let _cpEl = null, _cpSel = 0, _cpItems = [];

function _cpCollect() {
  const out = [];
  document.querySelectorAll('button').forEach(b => {
    if (!b.offsetParent) return;                       // hidden (closed modals etc.)
    const label = (b.title || b.textContent || '').replace(/\s+/g, ' ').trim();
    if (!label || label.length > 70) return;
    out.push({ label, el: b });
  });
  return out;
}

function _cpEnsure() {
  if (_cpEl) return _cpEl;
  _cpEl = document.createElement('div');
  _cpEl.id = 'cmd-palette';
  _cpEl.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:4000;align-items:flex-start;justify-content:center;padding-top:12vh;';
  _cpEl.innerHTML = `
    <div style="background:#0d1b35;border:1px solid #16588e;border-radius:8px;width:min(520px,92vw);box-shadow:0 8px 40px #000c;overflow:hidden;">
      <input id="cmd-palette-q" placeholder="Type a command… (Esc to close)"
             style="width:100%;box-sizing:border-box;background:#091530;border:none;border-bottom:1px solid #0f3460;color:#e0e0e0;padding:10px 12px;font-size:14px;outline:none;">
      <div id="cmd-palette-list" style="max-height:46vh;overflow-y:auto;"></div>
    </div>`;
  _cpEl.addEventListener('mousedown', e => { if (e.target === _cpEl) _cpClose(); });
  document.body.appendChild(_cpEl);
  const q = _cpEl.querySelector('#cmd-palette-q');
  q.addEventListener('input', () => { _cpSel = 0; _cpRender(); });
  q.addEventListener('keydown', e => {
    if (e.key === 'Escape') { _cpClose(); e.stopPropagation(); }
    else if (e.key === 'ArrowDown') { _cpSel = Math.min(_cpSel + 1, _cpFiltered().length - 1); _cpRender(); e.preventDefault(); }
    else if (e.key === 'ArrowUp')   { _cpSel = Math.max(_cpSel - 1, 0); _cpRender(); e.preventDefault(); }
    else if (e.key === 'Enter') {
      const it = _cpFiltered()[_cpSel];
      if (it) { _cpClose(); it.el.click(); }
      e.preventDefault();
    }
  });
  return _cpEl;
}

function _cpFiltered() {
  const q = (document.getElementById('cmd-palette-q')?.value || '').toLowerCase().trim();
  if (!q) return _cpItems;
  const words = q.split(/\s+/);
  return _cpItems.filter(it => words.every(w => it.label.toLowerCase().includes(w)));
}

function _cpRender() {
  const list = document.getElementById('cmd-palette-list');
  const items = _cpFiltered().slice(0, 40);
  if (_cpSel >= items.length) _cpSel = Math.max(0, items.length - 1);
  list.innerHTML = items.map((it, i) =>
    `<div class="cp-item" data-i="${i}" style="padding:7px 12px;font-size:13px;color:#e0e0e0;cursor:pointer;${i === _cpSel ? 'background:#0f3460;' : ''}">${it.label.replace(/</g, '&lt;')}</div>`
  ).join('') || '<div style="padding:10px 12px;color:#555;font-size:12px;">No matching command.</div>';
  list.querySelectorAll('.cp-item').forEach(d => {
    d.addEventListener('mousedown', e => {
      const it = items[+d.dataset.i];
      _cpClose(); it.el.click(); e.preventDefault();
    });
  });
}

function _cpOpen() {
  _cpEnsure();
  _cpItems = _cpCollect();
  _cpSel = 0;
  _cpEl.style.display = 'flex';
  const q = document.getElementById('cmd-palette-q');
  q.value = ''; _cpRender(); q.focus();
}

function _cpClose() { if (_cpEl) _cpEl.style.display = 'none'; }

// ── '?' shortcut cheatsheet ──────────────────────────────────────────────────
let _shEl = null;
function _shToggle() {
  if (_shEl && _shEl.style.display !== 'none') { _shEl.style.display = 'none'; return; }
  if (!_shEl) {
    _shEl = document.createElement('div');
    _shEl.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:4000;display:flex;align-items:center;justify-content:center;';
    const row = (k, d) => `<tr><td style="padding:2px 14px 2px 0;color:#f0c040;white-space:nowrap;font-family:monospace;">${k}</td><td style="padding:2px 0;color:#ccc;">${d}</td></tr>`;
    _shEl.innerHTML = `<div style="background:#0d1b35;border:1px solid #16588e;border-radius:8px;padding:18px 24px;max-height:84vh;overflow-y:auto;box-shadow:0 8px 40px #000c;">
      <div style="font-size:14px;font-weight:700;color:#e0e0e0;margin-bottom:10px;">Keyboard shortcuts <span style="color:#555;font-weight:400;">(? to close)</span></div>
      <table style="font-size:12px;border-collapse:collapse;">
        ${row('N / M', 'Next / previous page')}
        ${row('← → ↑ ↓', 'Move between lattice cells')}
        ${row('E', 'Toggle edit / review mode')}
        ${row('A', 'Select all shapes')}
        ${row('Del', 'Delete selected shape(s)')}
        ${row('Ctrl+Z', 'Undo (50 steps)')}
        ${row('Ctrl+S', 'Save human correction')}
        ${row('Ctrl+C / Ctrl+V', 'Copy / paste shapes')}
        ${row('Ctrl+←→↑↓', 'Clone shape flush-adjacent')}
        ${row('Ctrl+drag', 'Rubber-band select')}
        ${row('Right-drag', 'Copy shape to new position')}
        ${row('P / O', 'Stamp selection 1 / 2 page(s) back')}
        ${row('H', 'Focus the Human correction field')}
        ${row('Ctrl+K', 'Command palette (every button, searchable)')}
        ${row('?', 'This cheatsheet')}
        ${row('Esc', 'Close modal / cancel drawing')}
      </table></div>`;
    _shEl.addEventListener('mousedown', e => { if (e.target === _shEl) _shEl.style.display = 'none'; });
    document.body.appendChild(_shEl);
  }
  _shEl.style.display = 'flex';
}

// Ctrl+K, ?, H — registered here (core.js) so they exist on every page state.
document.addEventListener('keydown', e => {
  const inField = /INPUT|TEXTAREA|SELECT/.test(e.target?.tagName || '');
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault(); _cpOpen(); return;
  }
  if (inField) return;
  if (e.key === '?') { e.preventDefault(); _shToggle(); }
  else if ((e.key === 'h' || e.key === 'H') && !e.ctrlKey && !e.metaKey && !e.altKey) {
    // focus the Human field: the flat textarea, or (cells with internal rows)
    // the first Human cell of the rows table
    const ta = document.getElementById('human-input');
    if (ta && ta.offsetParent) { e.preventDefault(); ta.focus(); ta.select(); return; }
    const cell = document.querySelector('input.rs-human');
    if (cell && cell.offsetParent) { e.preventDefault(); cell.focus(); cell.select(); }
  }
});
