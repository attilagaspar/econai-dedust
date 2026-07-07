// Split from index.html — classic scripts share the global scope;
// load order in index.html is load-bearing. See knowledge_base/02_architecture.md.
// ── LLM layout detection (magic wand) ──────────────────────────────────────
function _llmLayoutDefaultPrompt() {
  const labels = [...new Set([...(projectLabels || []),
                              ...((pageData?.shapes || []).map(s => s.label))])]
                 .filter(Boolean);
  const labelStr = labels.length ? labels.join(', ')
                                 : 'the annotation labels used in this project';
  return `You are a document layout detector. The image is one page of a historical statistical table / register.

Detect every distinct layout region and return them as bounding boxes, using ONLY these labels: ${labelStr}.

Output ONLY a JSON array — no prose, no markdown fences — exactly like:
[{"label": "<one of the labels above>", "box": [x1, y1, x2, y2]}, ...]

Coordinates are FRACTIONS of the image size: x1,y1 = top-left corner, x2,y2 = bottom-right corner, each between 0 (left/top edge) and 1 (right/bottom edge). Make each box tight around its region. Detect as many regions as you can see.`;
}

function openLlmLayout() {
  if (!pages.length || !pageData) { showToast('Load a page first'); return; }
  const sel = document.getElementById('lll-model');
  sel.innerHTML = document.getElementById('llm-model').innerHTML;
  sel.value = localStorage.getItem('llmLayoutModel') || document.getElementById('llm-model').value
              || sel.value;
  document.getElementById('lll-prompt').value =
    localStorage.getItem('llmLayoutPrompt') || _llmLayoutDefaultPrompt();
  document.getElementById('lll-status').textContent = '';
  document.getElementById('lll-raw').textContent = '';
  document.getElementById('lll-clear').checked = false;
  document.getElementById('llm-layout-modal').style.display = 'flex';
}

function closeLlmLayout() {
  document.getElementById('llm-layout-modal').style.display = 'none';
}

async function runLlmLayout() {
  if (!pages.length || !pageData) return;
  const btn    = document.getElementById('lll-run-btn');
  const status = document.getElementById('lll-status');
  const model  = document.getElementById('lll-model').value;
  const prompt = document.getElementById('lll-prompt').value;
  const clear  = document.getElementById('lll-clear').checked;
  localStorage.setItem('llmLayoutModel', model);
  localStorage.setItem('llmLayoutPrompt', prompt);
  btn.disabled = true; status.textContent = '⟳ asking the LLM…';
  try {
    const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, model});
    const r = await fetch(`${API}/api/page/llm-layout?${params}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt}),
    });
    if (!r.ok) { status.textContent = `✕ ${r.status}: ${(await r.text()).slice(0,120)}`; return; }
    const data = await r.json();
    document.getElementById('lll-raw').textContent = data.response || '(empty)';
    if (!data.shapes?.length) { status.textContent = `Parsed 0 boxes (see raw reply)`; return; }
    pushUndo();
    if (clear) pageData.shapes = [];
    data.shapes.forEach(s => pageData.shapes.push(s));
    await replaceAllShapes();
    drawOverlay(); refreshDiag(); updatePanel();
    status.textContent = `✓ added ${data.count} box(es)${clear ? ' (replaced existing)' : ''} — Ctrl+Z to undo`;
    showToast(`LLM layout: ${data.count} boxes added`);
  } catch (e) {
    status.textContent = `✕ ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

function computeDiagnostics() {
  diagnosticFlagged   = new Set();
  diagnosticRowCounts = {};
  diagnosticRuleRows  = {};
  const countEl = document.getElementById('diagnostic-count');
  if (diagnosticMode === 'none' || !pageData?.shapes) {
    if (countEl) countEl.textContent = '';
    return;
  }
  const shapes = pageData.shapes;

  if (diagnosticMode.startsWith('rule:')) {
    const all = diagnosticMode === 'rule:all';
    _computeRuleDiagnostic(all ? 'all' : parseInt(diagnosticMode.slice(5), 10));
    // (n/a) when the pattern(s) skip this page entirely
    const applies = all
      ? projectRules.some(r => _ruleAppliesToPage(r))
      : _ruleAppliesToPage(projectRules[parseInt(diagnosticMode.slice(5), 10)]);
    if (countEl) countEl.textContent = !applies
      ? '(n/a)'
      : (diagnosticFlagged.size ? `(${diagnosticFlagged.size})` : '');
    return;
  }

  if (diagnosticMode === 'ocr_llm') {
    // Flag shapes where both OCR and LLM exist but have different line counts
    shapes.forEach((s, i) => {
      const n1 = _lineCount(s.tesseract_output?.ocr_text);
      const n2 = _lineCount(s.openai_output?.response);
      if (n1 > 0 && n2 > 0 && n1 !== n2) diagnosticFlagged.add(i);
    });
  } else {
    // Row-mismatch checks: group by super_row
    const getField = s => {
      if (diagnosticMode === 'ocr_rows')   return s.tesseract_output?.ocr_text;
      if (diagnosticMode === 'llm_rows')   return s.openai_output?.response;
      if (diagnosticMode === 'human_rows') return s.human_output?.human_corrected_text;
      if (diagnosticMode === 'best_rows')
        return s.human_output?.human_corrected_text
            || s.openai_output?.response
            || s.tesseract_output?.ocr_text;
      if (diagnosticMode === 'best_rows_pdf')
        return s.human_output?.human_corrected_text
            || s.openai_output?.response
            || s.tesseract_output?.ocr_text
            || s.pdf_text;
    };
    const rowGroups = {};
    shapes.forEach((s, i) => {
      if (s.super_row == null) return;
      (rowGroups[_rk(s)] ??= []).push(i);
    });
    Object.values(rowGroups).forEach(idxs => {
      const withData = idxs.filter(i => getField(shapes[i]));
      if (withData.length < 1) return;
      // Store per-shape counts for label rendering
      withData.forEach(i => {
        diagnosticRowCounts[i] = _lineCount(getField(shapes[i]));
      });
      if (withData.length < 2) return; // need ≥2 to compare
      // Find the mode (most frequent line count in this super_row)
      const freq = {};
      withData.forEach(i => {
        const n = diagnosticRowCounts[i];
        freq[n] = (freq[n] || 0) + 1;
      });
      const modeCount = parseInt(Object.entries(freq).sort((a, b) => b[1] - a[1])[0][0]);
      // Flag only cells that differ from the mode
      withData.forEach(i => {
        if (diagnosticRowCounts[i] !== modeCount) diagnosticFlagged.add(i);
      });
    });
  }

  if (countEl) countEl.textContent = diagnosticFlagged.size ? `(${diagnosticFlagged.size})` : '';
}

function onDiagnosticChange() {
  diagnosticMode = document.getElementById('diagnostic-select').value;
  computeDiagnostics();
  drawOverlay();
  const scBtn = document.getElementById('smart-correct-btn');
  if (scBtn && !scBtn.disabled) {
    const rowModes = ['ocr_rows','llm_rows','human_rows','best_rows','best_rows_pdf'];
    scBtn.style.opacity = rowModes.includes(diagnosticMode) ? '1' : '0.35';
    scBtn.title = rowModes.includes(diagnosticMode)
      ? 'Auto-correct ±1 line-count mismatches found by Diagnose'
      : 'Switch Diagnose to a row-mismatch mode to use Smart Correct';
  }
}

// Re-run diagnostics if a mode is active (call before drawOverlay after data changes)
function refreshDiag() { if (diagnosticMode !== 'none') computeDiagnostics(); }

// ── Lattice detection ─────────────────────────────────────────────────────────

function openLatticeModal() {
  if (!pageData?.shapes?.length) return;
  const labels = [...new Set(pageData.shapes.map(s => s.label))].sort();
  let remembered = {};
  try { remembered = JSON.parse(localStorage.getItem('latticeLabels') || '{}'); } catch(e){}
  const container = document.getElementById('lattice-label-checks');
  container.innerHTML = labels.map(lbl => {
    const checked = (lbl in remembered) ? remembered[lbl] : true;
    return `<label><input type="checkbox" value="${lbl}" ${checked?'checked':''}> ${lbl}</label>`;
  }).join('');
  document.getElementById('lattice-modal').classList.add('show');
}

function closeLatticeModal() {
  document.getElementById('lattice-modal').classList.remove('show');
}

async function runLatticeDetect() {
  const checks = document.querySelectorAll('#lattice-label-checks input[type=checkbox]');
  const remembered = {};
  const selectedLabels = [];
  checks.forEach(cb => {
    remembered[cb.value] = cb.checked;
    if (cb.checked) selectedLabels.push(cb.value);
  });
  localStorage.setItem('latticeLabels', JSON.stringify(remembered));
  if (!selectedLabels.length) { showToast('Select at least one label'); return; }
  closeLatticeModal();
  pushUndo();
  _latticeDetect(selectedLabels);
  await replaceAllShapes();
  const n = pageData.shapes.filter(s => s.super_row != null).length;
  // Show grid automatically after first run
  const gridBtn = document.getElementById('lattice-grid-btn');
  gridBtn.style.display = ''; gridBtn.disabled = false;
  const colSepBtn2 = document.getElementById('lattice-col-sep-btn');
  const rowSepBtn2 = document.getElementById('lattice-row-sep-btn');
  colSepBtn2.style.display = ''; colSepBtn2.disabled = false;
  rowSepBtn2.style.display = ''; rowSepBtn2.disabled = false;
  const delSepBtn2 = document.getElementById('lattice-del-sep-btn');
  delSepBtn2.style.display = ''; delSepBtn2.disabled = false;
  const splitBtn2 = document.getElementById('lattice-split-btn');
  splitBtn2.style.display = ''; splitBtn2.disabled = false;
  const delLatBtn2 = document.getElementById('lattice-del-btn');
  delLatBtn2.style.display = ''; delLatBtn2.disabled = false;
  if (!latticeVisible) {
    latticeVisible = true;
    gridBtn.textContent = '📐 Hide Grid';
  }
  drawOverlay(); updatePanel();
  showToast(`Lattice assigned to ${n} shapes`);
}

// Detect a separate lattice (a new table) on the currently-selected shapes only.
async function runLatticeDetectSelection() {
  if (!pageData?.shapes) return;
  const sel = [...selSet];
  if (sel.length < 2) {
    showToast('Select the cells of one table first (Ctrl+drag or Ctrl+click)'); return;
  }
  const subset = sel.map(i => pageData.shapes[i]);
  const labels = [...new Set(subset.map(s => s.label).filter(Boolean))];
  if (!labels.length) { showToast('Selected shapes have no labels'); return; }
  // Source tables the selection is being carved out of (so we can renumber
  // their leftovers afterward — a carve is a split, both halves stay clean)
  const srcTables = [...new Set(subset.map(s => _tableOf(s)).filter(t => t != null))];
  const ids = _latticeTableIds();
  const tid = ids.length ? Math.max(...ids) + 1 : 0;
  pushUndo();
  const n = _latticeDetect(labels, {subset, table: tid});
  // Re-detect the remainder of each source table so it renumbers contiguously
  srcTables.forEach(st => {
    if (st === tid) return;
    const rest = pageData.shapes.filter(s => (s.table ?? 0) === st && s.super_row != null);
    if (rest.length) _latticeDetect([...new Set(rest.map(s => s.label))], {subset: rest, table: st});
  });
  await replaceAllShapes();
  // make the grid + separator tools available
  const gridBtn = document.getElementById('lattice-grid-btn');
  gridBtn.style.display = ''; gridBtn.disabled = false;
  ['lattice-col-sep-btn', 'lattice-row-sep-btn', 'lattice-del-sep-btn', 'lattice-split-btn', 'lattice-del-btn'].forEach(id => {
    const b = document.getElementById(id); if (b) { b.style.display = ''; b.disabled = false; }
  });
  if (!latticeVisible) { latticeVisible = true; gridBtn.textContent = '📐 Hide Grid'; }
  drawOverlay(); refreshDiag(); updatePanel();
  showToast(`Table ${tid}: lattice on ${n} selected shape(s)`);
}

function toggleLatticeGrid() {
  latticeVisible = !latticeVisible;
  document.getElementById('lattice-grid-btn').textContent = latticeVisible ? '📐 Hide Grid' : '📐 Show Grid';
  if (!latticeVisible) { latticeSepMode = null; _updateLatticeSepBtns(); }
  drawOverlay();
}

function toggleLatticeSepMode(type) {
  latticeSepMode = latticeSepMode === type ? null : type;
  if (latticeSepMode) latticeSplitMode = false;          // mutually exclusive
  if (latticeSepMode && !latticeVisible) { latticeVisible = true; document.getElementById('lattice-grid-btn').textContent = '📐 Hide Grid'; }
  _updateLatticeSepBtns();
  drawOverlay();
}

function toggleLatticeSplitMode() {
  latticeSplitMode = !latticeSplitMode;
  if (latticeSplitMode) {
    latticeSepMode = null;                                // mutually exclusive
    if (!latticeVisible) { latticeVisible = true; document.getElementById('lattice-grid-btn').textContent = '📐 Hide Grid'; }
  }
  _updateLatticeSepBtns();
  drawOverlay();
}

// Split the active table at an interior grid line into two independent tables.
// Vertical line between cols (colLeft|colRight): cols >= colRight become a new
// table (super_column renumbered from 0). Horizontal is the row analogue.
async function _latticeSplitVert(colLeft, colRight) {
  const AT = _activeTable();
  const newT = _latticeTableIds().reduce((m, t) => Math.max(m, t), -1) + 1;
  pushUndo();
  let moved = 0;
  pageData.shapes.forEach(s => {
    if (_tableOf(s) !== AT) return;
    if (s.super_column >= colRight) { s.table = newT; s.super_column -= colRight; moved++; }
  });
  if (!moved) { undoStack.pop(); showToast('Nothing to split there'); return; }
  latticeSplitMode = false; _updateLatticeSepBtns();
  await replaceAllShapes();
  drawOverlay(); updatePanel();
  showToast(`Split off table ${newT} (vertical) · ${moved} cells`);
}
async function _latticeSplitHoriz(rowAbove, rowBelow) {
  const AT = _activeTable();
  const newT = _latticeTableIds().reduce((m, t) => Math.max(m, t), -1) + 1;
  pushUndo();
  let moved = 0;
  pageData.shapes.forEach(s => {
    if (_tableOf(s) !== AT) return;
    if (s.super_row >= rowBelow) { s.table = newT; s.super_row -= rowBelow; moved++; }
  });
  if (!moved) { undoStack.pop(); showToast('Nothing to split there'); return; }
  latticeSplitMode = false; _updateLatticeSepBtns();
  await replaceAllShapes();
  drawOverlay(); updatePanel();
  showToast(`Split off table ${newT} (horizontal) · ${moved} cells`);
}

// Delete the active lattice = remove that table's cell annotations. With
// multiple lattices, only the active table (the one whose cell is selected, or
// the first) is removed; the others are left intact.
async function deleteActiveLattice() {
  if (!pageData?.shapes) return;
  const ids = _latticeTableIds();
  if (!ids.length) { showToast('No lattice on this page'); return; }
  const AT = _activeTable();
  const victims = pageData.shapes.filter(s => _tableOf(s) === AT);
  if (!victims.length) { showToast('No active lattice to delete'); return; }
  const multi = ids.length > 1;
  const msg = multi
    ? `Delete the active lattice (table ${AT}) — ${victims.length} cell annotation(s)? Other lattices on the page are kept.`
    : `Delete this lattice — ${victims.length} cell annotation(s)?`;
  if (!confirm(msg)) return;
  pushUndo();
  pageData.shapes = pageData.shapes.filter(s => _tableOf(s) !== AT);
  selSet.clear(); selIdx = -1;
  latticeSepMode = null; latticeSplitMode = false;
  await replaceAllShapes();
  // Hide the lattice toolbar if nothing lattice-y is left on the page
  if (!pageData.shapes.some(s => s.super_row != null)) {
    ['lattice-grid-btn', 'lattice-col-sep-btn', 'lattice-row-sep-btn',
     'lattice-del-sep-btn', 'lattice-split-btn', 'lattice-del-btn'].forEach(id => {
      const b = document.getElementById(id); if (b) b.style.display = 'none';
    });
    latticeVisible = false;
  }
  _updateLatticeSepBtns();
  drawOverlay(); refreshDiag(); updatePanel();
  showToast(multi ? `Deleted lattice ${AT} · ${victims.length} cells` : `Deleted lattice · ${victims.length} cells`);
}

// Red crosshair cursor for separator insertion modes
const _SEP_CURSOR = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'%3E%3Cline x1='12' y1='2' x2='12' y2='22' stroke='%23e94560' stroke-width='2.5' stroke-linecap='round'/%3E%3Cline x1='2' y1='12' x2='22' y2='12' stroke='%23e94560' stroke-width='2.5' stroke-linecap='round'/%3E%3Ccircle cx='12' cy='12' r='2.5' fill='%23e94560'/%3E%3C/svg%3E\") 12 12, crosshair";

function _updateLatticeSepBtns() {
  document.getElementById('lattice-col-sep-btn').classList.toggle('active', latticeSepMode === 'col');
  document.getElementById('lattice-row-sep-btn').classList.toggle('active', latticeSepMode === 'row');
  document.getElementById('lattice-del-sep-btn').classList.toggle('active', latticeSepMode === 'delete');
  document.getElementById('lattice-split-btn')?.classList.toggle('active', latticeSplitMode);
  // Red crosshair cursor while in insertion mode; restore default when off
  const container = document.getElementById('osd-container');
  if (container) {
    container.style.cursor = (latticeSepMode === 'row' || latticeSepMode === 'col') ? _SEP_CURSOR : '';
  }
}

async function handleLatticeSepClick(e, rowBands, colBands) {
  if (!latticeSepMode) return;
  const p = screenToImg(e.clientX, e.clientY);
  const AT = _activeTable();
  const latticeShapes = pageData.shapes.filter(s => _tableOf(s) === AT && s.points?.length >= 2);
  if (!latticeShapes.length) return;
  pushUndo();
  const toAdd = [];

  if (latticeSepMode === 'col') {
    // Find which column band contains the click x
    const sortedCols = Object.keys(colBands).map(Number).sort((a,b) => colBands[a].left - colBands[b].left);
    const targetCol = sortedCols.find(c => p.x >= colBands[c].left && p.x <= colBands[c].right);
    if (targetCol == null) { undoStack.pop(); return; }
    const x = Math.round(p.x);

    // Make room: shift all shapes with super_column > targetCol up by 1
    latticeShapes.forEach(s => { if (s.super_column > targetCol) s.super_column++; });

    // Split shapes in the target column
    latticeShapes.forEach(s => {
      if (s.super_column !== targetCol) return;
      const xs = s.points.map(q => q[0]), ys = s.points.map(q => q[1]);
      const x1 = Math.min(...xs), x2 = Math.max(...xs), y1 = Math.min(...ys), y2 = Math.max(...ys);
      if (x <= x1 || x >= x2) return;
      s.points = [[x1, y1], [x, y2]];
      // New right part gets targetCol+1, same super_row
      toAdd.push({ label: s.label, shape_type: 'rectangle', flags: {}, group_id: null,
                   points: [[x, y1], [x2, y2]], super_row: s.super_row, super_column: targetCol + 1 });
    });

  } else { // 'row'
    const sortedRows = Object.keys(rowBands).map(Number).sort((a,b) => rowBands[a].top - rowBands[b].top);
    const targetRow = sortedRows.find(r => p.y >= rowBands[r].top && p.y <= rowBands[r].bot);
    if (targetRow == null) { undoStack.pop(); return; }
    const y = Math.round(p.y);

    // Make room: shift all shapes with super_row > targetRow up by 1
    latticeShapes.forEach(s => { if (s.super_row > targetRow) s.super_row++; });

    // Split shapes in the target row
    latticeShapes.forEach(s => {
      if (s.super_row !== targetRow) return;
      const xs = s.points.map(q => q[0]), ys = s.points.map(q => q[1]);
      const x1 = Math.min(...xs), x2 = Math.max(...xs), y1 = Math.min(...ys), y2 = Math.max(...ys);
      if (y <= y1 || y >= y2) return;
      s.points = [[x1, y1], [x2, y]];
      // New bottom part gets targetRow+1, same super_column
      toAdd.push({ label: s.label, shape_type: 'rectangle', flags: {}, group_id: null,
                   points: [[x1, y], [x2, y2]], super_row: targetRow + 1, super_column: s.super_column });
    });
  }

  toAdd.forEach(s => { s.table = AT; pageData.shapes.push(s); });   // new split cells stay in this table
  // No _latticeDetect re-run needed — we assigned super_row/super_column directly
  await replaceAllShapes();
  drawOverlay(); updatePanel();
  showToast(`${latticeSepMode === 'col' ? 'Column' : 'Row'} separator added`);
}

async function handleLatticeMergeCol(colLeft, colRight, rowBands, colBands) {
  const AT = _activeTable();
  const latticeShapes = pageData.shapes.filter(s => _tableOf(s) === AT && s.points?.length >= 2);
  pushUndo();
  const rows = [...new Set(latticeShapes.map(s => s.super_row))];
  const toDelete = new Set();
  rows.forEach(r => {
    const sL = latticeShapes.find(s => s.super_row === r && s.super_column === colLeft);
    const sR = latticeShapes.find(s => s.super_row === r && s.super_column === colRight);
    if (sL && sR) {
      // Extend left shape rightward to cover right shape, then delete right shape
      const newX2 = Math.max(...sR.points.map(p => p[0]));
      sL.points = [[Math.min(...sL.points.map(p=>p[0])), Math.min(...sL.points.map(p=>p[1]))],
                   [newX2,                                Math.max(...sL.points.map(p=>p[1]))]];
      toDelete.add(sR);
    } else if (!sL && sR) {
      // Only right shape: extend its left edge to cover left column band
      const newX1 = colBands[colLeft]?.left ?? Math.min(...sR.points.map(p => p[0]));
      const newX2 = Math.max(...sR.points.map(p => p[0]));
      sR.points = [[newX1, Math.min(...sR.points.map(p=>p[1]))], [newX2, Math.max(...sR.points.map(p=>p[1]))]];
      sR.super_column = colLeft;
    } else if (sL && !sR) {
      // Only left shape: extend its right edge to cover right column band
      const newX2 = colBands[colRight]?.right ?? Math.max(...sL.points.map(p => p[0]));
      sL.points = [[Math.min(...sL.points.map(p=>p[0])), Math.min(...sL.points.map(p=>p[1]))],
                   [newX2,                                Math.max(...sL.points.map(p=>p[1]))]];
    }
  });
  pageData.shapes = pageData.shapes.filter(s => !toDelete.has(s));
  // Close the gap: shapes in THIS table with super_column > colRight shift down by 1
  pageData.shapes.forEach(s => { if (_tableOf(s) === AT && s.super_column > colRight) s.super_column--; });
  await replaceAllShapes();
  drawOverlay(); updatePanel();
  showToast('Columns merged');
}

async function handleLatticeMergeRow(rowAbove, rowBelow, rowBands, colBands) {
  const AT = _activeTable();
  const latticeShapes = pageData.shapes.filter(s => _tableOf(s) === AT && s.points?.length >= 2);
  pushUndo();
  const cols = [...new Set(latticeShapes.map(s => s.super_column))];
  const toDelete = new Set();
  cols.forEach(c => {
    const sT = latticeShapes.find(s => s.super_column === c && s.super_row === rowAbove);
    const sB = latticeShapes.find(s => s.super_column === c && s.super_row === rowBelow);
    if (sT && sB) {
      const newY2 = Math.max(...sB.points.map(p => p[1]));
      sT.points = [[Math.min(...sT.points.map(p=>p[0])), Math.min(...sT.points.map(p=>p[1]))],
                   [Math.max(...sT.points.map(p=>p[0])), newY2]];
      toDelete.add(sB);
    } else if (!sT && sB) {
      const newY1 = rowBands[rowAbove]?.top ?? Math.min(...sB.points.map(p => p[1]));
      const newY2 = Math.max(...sB.points.map(p => p[1]));
      sB.points = [[Math.min(...sB.points.map(p=>p[0])), newY1], [Math.max(...sB.points.map(p=>p[0])), newY2]];
      sB.super_row = rowAbove;
    } else if (sT && !sB) {
      const newY2 = rowBands[rowBelow]?.bot ?? Math.max(...sT.points.map(p => p[1]));
      sT.points = [[Math.min(...sT.points.map(p=>p[0])), Math.min(...sT.points.map(p=>p[1]))],
                   [Math.max(...sT.points.map(p=>p[0])), newY2]];
    }
  });
  pageData.shapes = pageData.shapes.filter(s => !toDelete.has(s));
  // Close the gap: shapes in THIS table with super_row > rowBelow shift down by 1
  pageData.shapes.forEach(s => { if (_tableOf(s) === AT && s.super_row > rowBelow) s.super_row--; });
  await replaceAllShapes();
  drawOverlay(); updatePanel();
  showToast('Rows merged');
}

// ── Row / Column fill ─────────────────────────────────────────────────────────
async function _runFill(direction) {
  const AT = _activeTable();
  const latticeShapes = pageData.shapes.filter(s => _tableOf(s) === AT);
  if (!latticeShapes.length) { showToast('No lattice detected'); return; }
  pushUndo();

  const prevLabels = [...new Set(latticeShapes.map(s => s.label))];
  const labelCount = {};
  latticeShapes.forEach(s => { labelCount[s.label] = (labelCount[s.label]||0)+1; });
  const fallbackLabel = Object.entries(labelCount).sort((a,b)=>b[1]-a[1])[0][0];
  const posMap = new Map();
  latticeShapes.forEach(s => posMap.set(`${s.super_row},${s.super_column}`, s));

  // Helper: pixel bounding box of a shape
  const bb = s => {
    const xs = s.points.map(p=>p[0]), ys = s.points.map(p=>p[1]);
    return { x1:Math.min(...xs), x2:Math.max(...xs), y1:Math.min(...ys), y2:Math.max(...ys) };
  };

  // Minimum pixel gap to create a fill shape (avoids touching/floating-point issues)
  const GAP_PX = 8;
  let added = 0;

  if (direction === 'row') {
    // For each super_row: sort shapes by left-x, find x-coordinate gaps between neighbours
    [...new Set(latticeShapes.map(s => s.super_row))].sort((a,b)=>a-b).forEach(r => {
      const rowShapes = latticeShapes.filter(s => s.super_row === r)
        .map(s => ({ ...bb(s), label: s.label, super_column: s.super_column }))
        .sort((a, b) => a.x1 - b.x1);
      if (rowShapes.length < 2) return;
      // Median row top/bottom across ALL shapes in this row
      const rowTop = Math.round(_median(rowShapes.map(s => s.y1)));
      const rowBot = Math.round(_median(rowShapes.map(s => s.y2)));
      for (let i = 0; i < rowShapes.length - 1; i++) {
        const left = rowShapes[i], right = rowShapes[i+1];
        const gapX1 = left.x2, gapX2 = right.x1;
        if (gapX2 - gapX1 < GAP_PX) continue;
        // Label: shape above the left neighbour's column, fallback to most frequent
        const above = posMap.get(`${r-1},${left.super_column}`);
        const label = above ? above.label : fallbackLabel;
        pageData.shapes.push({
          label, shape_type:'rectangle', flags:{}, group_id:null, table: AT,
          points: [[gapX1, rowTop], [gapX2, rowBot]],
        });
        added++;
      }
    });
  } else {
    // For each super_column: sort shapes by top-y, find y-coordinate gaps between neighbours
    [...new Set(latticeShapes.map(s => s.super_column))].sort((a,b)=>a-b).forEach(c => {
      const colShapes = latticeShapes.filter(s => s.super_column === c)
        .map(s => ({ ...bb(s), label: s.label, super_row: s.super_row }))
        .sort((a, b) => a.y1 - b.y1);
      if (colShapes.length < 2) return;
      // Median col left/right across ALL shapes in this column
      const colX1 = Math.round(_median(colShapes.map(s => s.x1)));
      const colX2 = Math.round(_median(colShapes.map(s => s.x2)));
      for (let i = 0; i < colShapes.length - 1; i++) {
        const top = colShapes[i], bot = colShapes[i+1];
        const gapY1 = top.y2, gapY2 = bot.y1;
        if (gapY2 - gapY1 < GAP_PX) continue;
        // Label: first shape to the right in the starting row
        const rightNeighbour = latticeShapes
          .filter(s => s.super_row === top.super_row && s.super_column > c)
          .sort((a,b) => a.super_column - b.super_column)[0];
        const label = rightNeighbour ? rightNeighbour.label : fallbackLabel;
        pageData.shapes.push({
          label, shape_type:'rectangle', flags:{}, group_id:null, table: AT,
          points: [[colX1, gapY1], [colX2, gapY2]],
        });
        added++;
      }
    });
  }

  if (!added) { showToast('No pixel gaps found between shapes'); return; }

  // Re-detect only THIS table (its existing cells + the new fill cells)
  _latticeDetect(prevLabels, {subset: pageData.shapes.filter(s => (s.table ?? 0) === AT), table: AT});
  await replaceAllShapes();
  drawOverlay(); updatePanel();
  showToast(`Filled ${added} gap${added>1?'s':''}, lattice re-detected`);
}

async function runRowFill() { await _runFill('row'); }
async function runColFill() { await _runFill('col'); }

async function runSnapToGrid() {
  const AT = _activeTable();
  const sShapes = pageData.shapes.filter(s => _tableOf(s) === AT && s.points?.length >= 2);
  if (!sShapes.length) { showToast('No lattice shapes on this page'); return; }
  pushUndo();

  // Compute band medians — same as overlay
  const rowData = {}, colData = {};
  sShapes.forEach(s => {
    const xs = s.points.map(p => p[0]), ys = s.points.map(p => p[1]);
    const r = s.super_row, c = s.super_column;
    if (!rowData[r]) rowData[r] = { tops: [], bots: [] };
    rowData[r].tops.push(Math.min(...ys)); rowData[r].bots.push(Math.max(...ys));
    if (!colData[c]) colData[c] = { lefts: [], rights: [] };
    colData[c].lefts.push(Math.min(...xs)); colData[c].rights.push(Math.max(...xs));
  });
  const rowBands = {}, colBands = {};
  Object.entries(rowData).forEach(([r, d]) => rowBands[+r] = {
    top: Math.round(_median(d.tops)), bot: Math.round(_median(d.bots))
  });
  Object.entries(colData).forEach(([c, d]) => colBands[+c] = {
    left: Math.round(_median(d.lefts)), right: Math.round(_median(d.rights))
  });

  // The overlay draws the RIGHT boundary of column C as the LEFT line of column C+1.
  // So set each column's right edge = next column's left edge, matching the blue lines exactly.
  const sortedCols = Object.keys(colBands).map(Number).sort((a, b) => colBands[a].left - colBands[b].left);
  for (let i = 0; i < sortedCols.length - 1; i++)
    colBands[sortedCols[i]].right = colBands[sortedCols[i + 1]].left;

  // Same for rows: bottom of row R = top of row R+1.
  const sortedRows = Object.keys(rowBands).map(Number).sort((a, b) => rowBands[a].top - rowBands[b].top);
  for (let i = 0; i < sortedRows.length - 1; i++)
    rowBands[sortedRows[i]].bot = rowBands[sortedRows[i + 1]].top;

  // Snap every shape to its cell in the grid
  sShapes.forEach(s => {
    const rb = rowBands[s.super_row], cb = colBands[s.super_column];
    if (!rb || !cb) return;
    s.points = [[cb.left, rb.top], [cb.right, rb.bot]];
  });

  await replaceAllShapes();
  drawOverlay(); updatePanel();
  showToast('Snapped all lattice shapes to grid');
}

// ── Core lattice algorithm (port of layout_superstructure_detect.py) ──────────

function _median(arr) {
  if (!arr.length) return 0;
  const s = [...arr].sort((a,b)=>a-b);
  const m = Math.floor(s.length/2);
  return s.length%2 ? s[m] : (s[m-1]+s[m])/2;
}

function _latticeDetect(selectedLabels, opts = {}) {
  const shapes = pageData.shapes;
  const labelSet = new Set(selectedLabels);
  const subset = opts.subset || null;        // restrict detection to these shapes (selection)
  const tableId = opts.table ?? 0;           // table id to stamp on the detected lattice
  const inScope = s => labelSet.has(s.label) && s.points?.length >= 2
                       && (!subset || subset.includes(s));
  // Clear existing super coords + table from the shapes being (re)detected
  shapes.forEach(s => {
    if (inScope(s)) { delete s.super_row; delete s.super_column; delete s.table; }
  });
  const useful = shapes.filter(inScope);
  if (!useful.length) return 0;
  let regions = _latticeIdentifyRegions(useful);
  regions = _latticeMergeRegions(regions);
  regions.forEach(r => { _latticeAssignCoords(r); _latticeSmoothRegion(r); _latticeCompleteRegion(r); });
  // Stamp the table id on every shape now in these regions (incl. completion cells)
  regions.forEach(r => r.forEach(s => { s.table = tableId; }));
  return useful.length;
}

function _latticeIdentifyRegions(useful) {
  const PROX = 600;
  const processed = new Set();
  const regions = [];
  for (const seed of useful) {
    if (processed.has(seed)) continue;
    const region = [];
    const queue = [seed];
    while (queue.length) {
      const cur = queue.shift();
      if (processed.has(cur)) continue;
      processed.add(cur);
      region.push(cur);
      const pts = cur.points;
      const cx = pts.reduce((s,p)=>s+p[0],0)/pts.length;
      const cy = pts.reduce((s,p)=>s+p[1],0)/pts.length;
      for (const cand of useful) {
        if (processed.has(cand)) continue;
        const cp = cand.points;
        const ax = cp.reduce((s,p)=>s+p[0],0)/cp.length;
        const ay = cp.reduce((s,p)=>s+p[1],0)/cp.length;
        if (Math.hypot(cx-ax,cy-ay) <= PROX) queue.push(cand);
      }
    }
    if (region.length) regions.push(region);
  }
  return regions;
}

function _latticeMergeRegions(regions) {
  if (regions.length <= 1) return regions;
  const metrics = regions.map(r => {
    const allX = r.flatMap(s=>s.points.map(p=>p[0]));
    const allY = r.flatMap(s=>s.points.map(p=>p[1]));
    return { shapes:r, xMin:Math.min(...allX), xMax:Math.max(...allX),
             yMin:Math.min(...allY), yMax:Math.max(...allY) };
  });
  metrics.sort((a,b) => ((a.yMin+a.yMax)/2) - ((b.yMin+b.yMax)/2));
  const merged = [];
  let i = 0;
  while (i < metrics.length) {
    let cur = metrics[i];
    let mShapes = [...cur.shapes];
    let j = i+1;
    while (j < metrics.length) {
      const cand = metrics[j];
      const oX1 = Math.max(cur.xMin, cand.xMin), oX2 = Math.min(cur.xMax, cand.xMax);
      if (oX2 > oX1) {
        const avgW = ((cur.xMax-cur.xMin)+(cand.xMax-cand.xMin))/2;
        if ((oX2-oX1) >= avgW*0.5) {
          mShapes = mShapes.concat(cand.shapes);
          cur = { shapes:mShapes,
                  xMin:Math.min(cur.xMin,cand.xMin), xMax:Math.max(cur.xMax,cand.xMax),
                  yMin:Math.min(cur.yMin,cand.yMin), yMax:Math.max(cur.yMax,cand.yMax) };
          j++; continue;
        }
      }
      break;
    }
    merged.push(mShapes);
    i = j > i+1 ? j : i+1;
  }
  return merged;
}

// Fraction of the SHORTER interval that two 1-D intervals [a1,a2],[b1,b2] share.
function _intervalOverlapFrac(a1, a2, b1, b2) {
  const inter   = Math.max(0, Math.min(a2, b2) - Math.max(a1, b1));
  const shorter = Math.max(1, Math.min(a2 - a1, b2 - b1));
  return inter / shorter;
}

// Group shapes into rows/columns by INTERVAL OVERLAP rather than edge
// proximity: each shape joins the existing group it overlaps most (>= thresh
// of the shorter span), else starts a new one. Robust to tall full-height
// strips (they all overlap vertically → one row) and to slightly-misaligned
// boxes, while cells merely sharing a border (~0 overlap) never merge.
function _groupByOverlap(shapes, lo, hi, thresh) {
  const groups = [];   // {members, los, his, lo, hi}  (lo/hi = median interval)
  const sorted = [...shapes].sort((a, b) => lo(a) - lo(b));
  for (const s of sorted) {
    const sLo = lo(s), sHi = hi(s);
    let best = -1, bestFrac = thresh;
    for (let gi = 0; gi < groups.length; gi++) {
      const f = _intervalOverlapFrac(sLo, sHi, groups[gi].lo, groups[gi].hi);
      if (f >= bestFrac) { bestFrac = f; best = gi; }
    }
    if (best >= 0) {
      const g = groups[best];
      g.members.push(s); g.los.push(sLo); g.his.push(sHi);
      g.lo = _median(g.los); g.hi = _median(g.his);
    } else {
      groups.push({ members: [s], los: [sLo], his: [sHi], lo: sLo, hi: sHi });
    }
  }
  return groups;
}

function _latticeAssignCoords(regionShapes) {
  const left  = s => Math.min(...s.points.map(p => p[0]));
  const right = s => Math.max(...s.points.map(p => p[0]));
  const top   = s => Math.min(...s.points.map(p => p[1]));
  const bot   = s => Math.max(...s.points.map(p => p[1]));
  const TH = 0.5;   // overlap fraction needed to share a row/column

  const cols = _groupByOverlap(regionShapes, left, right, TH)
                 .sort((a, b) => a.lo - b.lo);
  cols.forEach((g, i) => g.members.forEach(s => { s.super_column = i + 1; }));

  const rows = _groupByOverlap(regionShapes, top, bot, TH)
                 .sort((a, b) => a.lo - b.lo);
  rows.forEach((g, i) => g.members.forEach(s => { s.super_row = i + 1; }));
}

function _latticeSmoothRegion(regionShapes) {
  // Snap all cells in same super_row to median top/bottom y
  const rowNums = [...new Set(regionShapes.filter(s=>s.super_row!=null).map(s=>s.super_row))];
  rowNums.forEach(r => {
    const rs = regionShapes.filter(s=>s.super_row===r);
    if (rs.length < 2) return;
    const medTop = Math.round(_median(rs.map(s=>Math.min(...s.points.map(p=>p[1])))));
    const medBot = Math.round(_median(rs.map(s=>Math.max(...s.points.map(p=>p[1])))));
    rs.forEach(s => {
      const x1 = Math.min(...s.points.map(p=>p[0]));
      const x2 = Math.max(...s.points.map(p=>p[0]));
      s.points = [[x1, medTop], [x2, medBot]];
    });
  });
  // Snap all cells in same super_column to median left/right x
  const colNums = [...new Set(regionShapes.filter(s=>s.super_column!=null).map(s=>s.super_column))];
  colNums.forEach(c => {
    const cs = regionShapes.filter(s=>s.super_column===c);
    if (cs.length < 2) return;
    const medL = Math.round(_median(cs.map(s=>Math.min(...s.points.map(p=>p[0])))));
    const medR = Math.round(_median(cs.map(s=>Math.max(...s.points.map(p=>p[0])))));
    cs.forEach(s => {
      const y1 = Math.min(...s.points.map(p=>p[1]));
      const y2 = Math.max(...s.points.map(p=>p[1]));
      s.points = [[medL, y1], [medR, y2]];
    });
  });
}

function _latticeCompleteRegion(regionShapes) {
  if (!regionShapes.length) return;

  const rows = [...new Set(regionShapes.map(s => s.super_row))];
  const cols = [...new Set(regionShapes.map(s => s.super_column))];
  const minRow = Math.min(...rows), maxRow = Math.max(...rows);
  const minCol = Math.min(...cols), maxCol = Math.max(...cols);

  // Build lookup: "r,c" → shape
  const posMap = new Map();
  regionShapes.forEach(s => posMap.set(`${s.super_row},${s.super_column}`, s));

  // Row bands (top/bot y) and col bands (left/right x) from post-smoothing shapes
  const rowBands = {}, colBands = {};
  rows.forEach(r => {
    const rs = regionShapes.filter(s => s.super_row === r);
    rowBands[r] = {
      top: Math.round(_median(rs.map(s => Math.min(...s.points.map(p=>p[1]))))),
      bot: Math.round(_median(rs.map(s => Math.max(...s.points.map(p=>p[1])))))
    };
  });
  cols.forEach(c => {
    const cs = regionShapes.filter(s => s.super_column === c);
    colBands[c] = {
      left:  Math.round(_median(cs.map(s => Math.min(...s.points.map(p=>p[0]))))),
      right: Math.round(_median(cs.map(s => Math.max(...s.points.map(p=>p[0])))))
    };
  });

  // Most frequent label across the whole region (fallback)
  const labelCount = {};
  regionShapes.forEach(s => { labelCount[s.label] = (labelCount[s.label]||0)+1; });
  const mostFreqLabel = Object.entries(labelCount).sort((a,b)=>b[1]-a[1])[0][0];

  // Never fabricate a predicted cell that overlaps an existing shape. A tall
  // cell spanning several rows of a neighbouring column occupies only one grid
  // slot, so completion used to stack duplicate boxes over it — which then
  // needed a manual "remove overlaps" pass. Guard against that here.
  const existRects = pageData.shapes
    .filter(s => s.points?.length >= 2)
    .map(s => { const xs=s.points.map(p=>p[0]), ys=s.points.map(p=>p[1]);
                return {x1:Math.min(...xs), y1:Math.min(...ys),
                        x2:Math.max(...xs), y2:Math.max(...ys)}; });
  const overlapsExisting = (x1,y1,x2,y2) => {
    const area = Math.max(1, (x2-x1)*(y2-y1));
    return existRects.some(r => {
      const ix = Math.max(0, Math.min(x2,r.x2) - Math.max(x1,r.x1));
      const iy = Math.max(0, Math.min(y2,r.y2) - Math.max(y1,r.y1));
      return ix*iy > 0.10*area;          // >10% of the predicted cell covered
    });
  };

  let added = 0, skipped = 0;
  // Iterate top-to-bottom so "above" lookups can find just-created shapes
  for (let r = minRow; r <= maxRow; r++) {
    for (let c = minCol; c <= maxCol; c++) {
      if (posMap.has(`${r},${c}`)) continue;
      const rb = rowBands[r], cb = colBands[c];
      if (!rb || !cb) continue;
      if (overlapsExisting(cb.left, rb.top, cb.right, rb.bot)) { skipped++; continue; }

      // Type: cell directly above in the same column; fall back to most frequent
      const above = posMap.get(`${r-1},${c}`);
      const label = above ? above.label : mostFreqLabel;

      const newShape = {
        label,
        points: [[cb.left, rb.top], [cb.right, rb.bot]],
        group_id: null,
        shape_type: 'rectangle',
        flags: {},
        super_row: r,
        super_column: c,
      };
      pageData.shapes.push(newShape);
      regionShapes.push(newShape);          // keep regionShapes in sync for future band updates
      posMap.set(`${r},${c}`, newShape);    // available immediately for "above" on next rows
      existRects.push({x1:cb.left, y1:rb.top, x2:cb.right, y2:rb.bot}); // avoid stacking later predictions
      added++;
    }
  }
  if (added)   showToast(`Lattice: predicted ${added} missing cell${added>1?'s':''}`
                         + (skipped ? ` (${skipped} skipped — would overlap)` : ''), 3000);
}

// ── Lattice resize ────────────────────────────────────────────────────────────

function startLatticeRowResize(e, rowAbove, rowBelow) {
  pushUndo();
  const AT = _activeTable();
  const adjustments = [], origPts = {};
  pageData.shapes.forEach((s, idx) => {
    if (_tableOf(s) !== AT) return;                       // only the active table
    if (s.super_row !== rowAbove && s.super_row !== rowBelow) return;
    origPts[idx] = s.points.map(p=>[...p]);
    // rowAbove shapes: drag moves their bottom (max-y) edge
    // rowBelow shapes: drag moves their top (min-y) edge
    adjustments.push({ idx, moveMax: s.super_row === rowAbove });
  });
  dragState = { type:'lattice-row', startX:e.clientX, startY:e.clientY, adjustments, origPts };
}

function startLatticeColResize(e, colLeft, colRight) {
  pushUndo();
  const AT = _activeTable();
  const adjustments = [], origPts = {};
  pageData.shapes.forEach((s, idx) => {
    if (_tableOf(s) !== AT) return;                       // only the active table
    if (s.super_column !== colLeft && s.super_column !== colRight) return;
    origPts[idx] = s.points.map(p=>[...p]);
    // colLeft shapes: drag moves their right (max-x) edge
    // colRight shapes: drag moves their left (min-x) edge
    adjustments.push({ idx, moveMax: s.super_column === colLeft });
  });
  dragState = { type:'lattice-col', startX:e.clientX, startY:e.clientY, adjustments, origPts };
}

function _applyLatticeDrag(adjustments, origPts, delta, axis) {
  // axis 'y': moveMax shifts the larger-y point, else the smaller-y point
  // axis 'x': moveMax shifts the larger-x point, else the smaller-x point
  const ai = axis === 'y' ? 1 : 0;
  adjustments.forEach(({idx, moveMax}) => {
    const orig = origPts[idx];
    const pts = orig.map(p=>[...p]);
    const i0bigger = orig[0][ai] >= orig[1][ai];
    const tgt = moveMax ? (i0bigger ? 0 : 1) : (i0bigger ? 1 : 0);
    pts[tgt][ai] = orig[tgt][ai] + delta;
    pageData.shapes[idx].points = pts;
  });
}

// ── Overlay drawing ─────────────────────────────────────────────────────────
function drawOverlay() {
  if (!svgOverlay || !pageData) return;
  while (svgOverlay.firstChild) svgOverlay.removeChild(svgOverlay.firstChild);
  svgOverlay.style.pointerEvents = (editMode || tableMode || perspMode || clipMode) ? 'all' : 'none';

  (pageData.shapes || []).forEach((shape, i) => {
    const pts = (() => {
    if (!dragState || !dragCurrentPts) return shape.points;
    if (dragState.type==='resize' && dragState.idx===i) return dragCurrentPts;
    if (dragState.type==='move' && dragState.ptsMap?.[i]) return dragState.ptsMap[i];
    return shape.points;
  })();
    if (!pts || pts.length < 2) return;
    const xs=pts.map(p=>p[0]), ys=pts.map(p=>p[1]);
    const tl=imgToScreen(Math.min(...xs),Math.min(...ys));
    const br=imgToScreen(Math.max(...xs),Math.max(...ys));
    if (!tl||!br) return;

    const fillColor = colorFor(shape.label);
    const isSel = selSet.has(i);
    statusBorders(shape, isSel).forEach((border, bi) => {
      const ins=border.inset;
      const rect=document.createElementNS('http://www.w3.org/2000/svg','rect');
      rect.setAttribute('x',      tl.x+ins);
      rect.setAttribute('y',      tl.y+ins);
      rect.setAttribute('width',  Math.max(0,br.x-tl.x-ins*2));
      rect.setAttribute('height', Math.max(0,br.y-tl.y-ins*2));
      rect.setAttribute('fill',         bi===0 ? fillColor : 'none');
      rect.setAttribute('fill-opacity', bi===0 ? (isSel?0.35:0.15) : 0);
      rect.setAttribute('stroke',        border.color);
      rect.setAttribute('stroke-width',  border.width);
      rect.setAttribute('stroke-opacity','1');
      rect.style.pointerEvents = 'all';
      rect.style.cursor = editMode ? 'move' : 'pointer';
      rect.addEventListener('mousedown', e => {
        e.stopPropagation();
        // Clip mode: an armed flag drops onto the clicked annotation
        if (clipMode && _clipArmed != null) { assignClip(_clipArmed, i); return; }
        const ctrl = e.ctrlKey || e.metaKey;
        if (ctrl) {
          selectShape(i, true);
        } else if (!selSet.has(i)) {
          selectShape(i, false);
        } else {
          selIdx = i; drawOverlay(); updatePanel();
        }
        if (editMode && !ctrl) {
          if (e.button===2) startCopyDrag(e, i);
          else startMoveDrag(e, i);
        }
      });
      rect.addEventListener('contextmenu', e => e.preventDefault());
      svgOverlay.appendChild(rect);
    });

    // Authority badge: green dot = fully resolved, amber = partially (rows)
    const _ab = _authBadgeState(shape);
    if (_ab) {
      const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      dot.setAttribute('cx', br.x - 6);
      dot.setAttribute('cy', tl.y + 6);
      dot.setAttribute('r', 3.5);
      dot.setAttribute('fill', _ab === 'full' ? '#22c55e' : '#f59e0b');
      dot.setAttribute('stroke', '#0d1b35');
      dot.setAttribute('stroke-width', '1');
      dot.style.pointerEvents = 'none';
      svgOverlay.appendChild(dot);
    }

    // Structural-blank marker: a small grey ∅ at bottom-left of blank cells
    const _rowsB = shape.row_struct?.rows;
    const _blank = _rowsB?.length ? _rowsB.every(r => r.blank) : !!shape.blank;
    if (_blank) {
      const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      t.setAttribute('x', tl.x + 4);
      t.setAttribute('y', br.y - 4);
      t.setAttribute('font-size', '11');
      t.setAttribute('fill', '#8a94a6');
      t.textContent = '∅';
      t.style.pointerEvents = 'none';
      svgOverlay.appendChild(t);
    }

    // Internal row dividers (row_struct) — always visible
    const rsRows = shape.row_struct?.rows;
    if (rsRows?.length > 1) {
      for (let ri = 1; ri < rsRows.length; ri++) {
        const a = imgToScreen(Math.min(...xs), rsRows[ri].y0);
        const b = imgToScreen(Math.max(...xs), rsRows[ri].y0);
        if (!a || !b) continue;
        const ln = document.createElementNS('http://www.w3.org/2000/svg','line');
        ln.setAttribute('x1', a.x); ln.setAttribute('y1', a.y);
        ln.setAttribute('x2', b.x); ln.setAttribute('y2', b.y);
        ln.setAttribute('stroke', '#00e0ff');
        ln.setAttribute('stroke-width', '1');
        ln.setAttribute('stroke-dasharray', '4 3');
        ln.setAttribute('stroke-opacity', '0.85');
        ln.style.pointerEvents = 'none';
        svgOverlay.appendChild(ln);
      }
    }

    // Clip flag badge (top-right corner) for any annotation carrying a clip
    if (shape.clip != null) {
      const col = clipColor(shape.clip);
      const label = String(shape.clip);
      const bw = 13 + label.length * 6, bh = 14;
      const bx = Math.max(tl.x, br.x - bw), by = tl.y;
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      bg.setAttribute('x', bx); bg.setAttribute('y', by);
      bg.setAttribute('width', bw); bg.setAttribute('height', bh);
      bg.setAttribute('rx', '2'); bg.setAttribute('fill', col);
      bg.setAttribute('stroke', '#fff'); bg.setAttribute('stroke-width', '0.7');
      g.appendChild(bg);
      const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      t.setAttribute('x', bx + bw / 2); t.setAttribute('y', by + bh - 3.5);
      t.setAttribute('text-anchor', 'middle'); t.setAttribute('fill', '#fff');
      t.setAttribute('font-size', '10'); t.setAttribute('font-weight', 'bold');
      t.textContent = '🚩' + label;
      g.appendChild(t);
      if (clipMode) {
        g.style.pointerEvents = 'all'; g.style.cursor = 'pointer';
        g.addEventListener('mousedown', e => { e.stopPropagation(); removeClip(i); });
        const tt = document.createElementNS('http://www.w3.org/2000/svg', 'title');
        tt.textContent = `Clip ${label} — click to remove`;
        g.appendChild(tt);
      } else {
        g.style.pointerEvents = 'none';
      }
      svgOverlay.appendChild(g);
    }

    // Resize handles for selected shape in edit mode
    if (editMode && i===selIdx) {
      HANDLES.forEach(h => {
        const pos = getHandleCenter(tl, br, h);
        const sq = document.createElementNS('http://www.w3.org/2000/svg','rect');
        sq.setAttribute('x',      pos.x-HANDLE_SIZE/2);
        sq.setAttribute('y',      pos.y-HANDLE_SIZE/2);
        sq.setAttribute('width',  HANDLE_SIZE);
        sq.setAttribute('height', HANDLE_SIZE);
        sq.setAttribute('fill','#ffffff');
        sq.setAttribute('stroke','#333333');
        sq.setAttribute('stroke-width','1');
        sq.style.pointerEvents='all'; sq.style.cursor=HANDLE_CURSORS[h];
        sq.addEventListener('mousedown', e => { e.stopPropagation(); startResizeDrag(e,i,h); });
        svgOverlay.appendChild(sq);
      });
    }
  });

  // Orange dashed highlight for flagged overlapping shapes
  flaggedOverlaps.forEach(i => {
    const shape = pageData.shapes[i]; if (!shape?.points?.length) return;
    const xs = shape.points.map(p=>p[0]), ys = shape.points.map(p=>p[1]);
    const tl = imgToScreen(Math.min(...xs), Math.min(...ys));
    const br = imgToScreen(Math.max(...xs), Math.max(...ys));
    if (!tl || !br) return;
    const r = document.createElementNS('http://www.w3.org/2000/svg','rect');
    r.setAttribute('x',      tl.x - 3);
    r.setAttribute('y',      tl.y - 3);
    r.setAttribute('width',  Math.max(0, br.x - tl.x + 6));
    r.setAttribute('height', Math.max(0, br.y - tl.y + 6));
    r.setAttribute('fill',          'none');
    r.setAttribute('stroke',        '#ff6b00');
    r.setAttribute('stroke-width',  '2.5');
    r.setAttribute('stroke-dasharray', '7 3');
    r.style.pointerEvents = 'none';
    svgOverlay.appendChild(r);
  });

  // Diagnostic: row-count labels on all cells + dashed outline only on deviants
  if (diagnosticMode !== 'none' && diagnosticMode !== 'ocr_llm' && Object.keys(diagnosticRowCounts).length) {
    Object.entries(diagnosticRowCounts).forEach(([idxStr, count]) => {
      const i = parseInt(idxStr);
      const shape = pageData.shapes[i]; if (!shape?.points?.length) return;
      const xs = shape.points.map(p => p[0]), ys = shape.points.map(p => p[1]);
      const tl = imgToScreen(Math.min(...xs), Math.min(...ys));
      const br = imgToScreen(Math.max(...xs), Math.max(...ys));
      if (!tl || !br) return;
      const flagged = diagnosticFlagged.has(i);
      const isRule  = diagnosticMode.startsWith('rule:');
      // Dashed outline only on deviants (softer in rule mode — the violating
      // internal rows carry the strong highlight there)
      if (flagged) {
        const r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        r.setAttribute('x',               tl.x);
        r.setAttribute('y',               tl.y);
        r.setAttribute('width',           Math.max(0, br.x - tl.x));
        r.setAttribute('height',          Math.max(0, br.y - tl.y));
        r.setAttribute('fill',            isRule ? 'none' : 'rgba(255,0,100,0.07)');
        r.setAttribute('stroke',          '#ff0064');
        r.setAttribute('stroke-width',    isRule ? '1' : '1.5');
        r.setAttribute('stroke-opacity',  isRule ? '0.45' : '1');
        r.setAttribute('stroke-dasharray','5 3');
        r.style.pointerEvents = 'none';
        svgOverlay.appendChild(r);
      }
      // Rule mode: highlight the violating INTERNAL rows in red
      if (flagged && isRule && diagnosticRuleRows[i]?.size && shape.row_struct?.rows) {
        const rsRows = shape.row_struct.rows;
        diagnosticRuleRows[i].forEach(k => {
          const rr = rsRows[k]; if (!rr) return;
          const a = imgToScreen(Math.min(...xs), rr.y0);
          const b = imgToScreen(Math.max(...xs), rr.y1);
          if (!a || !b) return;
          const band = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
          band.setAttribute('x',               a.x);
          band.setAttribute('y',               a.y);
          band.setAttribute('width',           Math.max(0, b.x - a.x));
          band.setAttribute('height',          Math.max(0, b.y - a.y));
          band.setAttribute('fill',            'rgba(255,0,80,0.18)');
          band.setAttribute('stroke',          '#ff2050');
          band.setAttribute('stroke-width',    '1.2');
          band.setAttribute('stroke-dasharray','4 3');
          band.style.pointerEvents = 'none';
          svgOverlay.appendChild(band);
        });
      }
      // Row-count label in top-left of every cell with data
      const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      t.setAttribute('x',           tl.x + 3);
      t.setAttribute('y',           tl.y + 11);
      t.setAttribute('fill',        flagged ? '#ff0064' : '#00cc88');
      t.setAttribute('font-size',   '10');
      t.setAttribute('font-weight', 'bold');
      t.setAttribute('font-family', 'monospace');
      t.style.pointerEvents = 'none';
      t.textContent = String(count);
      svgOverlay.appendChild(t);
    });
  } else {
    // ocr_llm mode: plain dashed outline (no counts)
    diagnosticFlagged.forEach(i => {
      const shape = pageData.shapes[i]; if (!shape?.points?.length) return;
      const xs = shape.points.map(p => p[0]), ys = shape.points.map(p => p[1]);
      const tl = imgToScreen(Math.min(...xs), Math.min(...ys));
      const br = imgToScreen(Math.max(...xs), Math.max(...ys));
      if (!tl || !br) return;
      const r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      r.setAttribute('x',               tl.x - 4);
      r.setAttribute('y',               tl.y - 4);
      r.setAttribute('width',           Math.max(0, br.x - tl.x + 8));
      r.setAttribute('height',          Math.max(0, br.y - tl.y + 8));
      r.setAttribute('fill',            'rgba(255,0,100,0.10)');
      r.setAttribute('stroke',          '#ff0064');
      r.setAttribute('stroke-width',    '2.5');
      r.setAttribute('stroke-dasharray','6 3');
      r.style.pointerEvents = 'none';
      svgOverlay.appendChild(r);
    });
  }

  // Multi-lattice: outline each table's region + a T# badge (only when >1 table)
  {
    const _tids = _latticeTableIds();
    if (_tids.length > 1) {
      const _TPAL = ['#ffb300','#00e5ff','#b388ff','#69f0ae','#ff8a80','#f48fb1','#80d8ff','#ffd180'];
      const _at = _activeTable();
      _tids.forEach((tid, ti) => {
        const ts = pageData.shapes.filter(s => _tableOf(s) === tid && s.points?.length >= 2);
        if (!ts.length) return;
        let x1=Infinity, y1=Infinity, x2=-Infinity, y2=-Infinity;
        ts.forEach(s => { const xs=s.points.map(p=>p[0]), ys=s.points.map(p=>p[1]);
          x1=Math.min(x1,...xs); y1=Math.min(y1,...ys); x2=Math.max(x2,...xs); y2=Math.max(y2,...ys); });
        const tl=imgToScreen(x1,y1), br=imgToScreen(x2,y2); if(!tl||!br) return;
        const col=_TPAL[ti % _TPAL.length], pad=3;
        const r=document.createElementNS('http://www.w3.org/2000/svg','rect');
        r.setAttribute('x',tl.x-pad); r.setAttribute('y',tl.y-pad);
        r.setAttribute('width',Math.max(0,br.x-tl.x+2*pad)); r.setAttribute('height',Math.max(0,br.y-tl.y+2*pad));
        r.setAttribute('fill','none'); r.setAttribute('stroke',col);
        r.setAttribute('stroke-width', tid===_at ? '2.5' : '1.3');
        r.setAttribute('stroke-dasharray','9 5'); r.style.pointerEvents='none';
        svgOverlay.appendChild(r);
        const t=document.createElementNS('http://www.w3.org/2000/svg','text');
        t.setAttribute('x',tl.x-pad+2); t.setAttribute('y',tl.y-pad-3);
        t.setAttribute('fill',col); t.setAttribute('font-size','12'); t.setAttribute('font-weight','bold');
        t.style.pointerEvents='none'; t.textContent=`T${tid}` + (tid===_at ? ' ●' : '');
        svgOverlay.appendChild(t);
      });
    }
  }

  // Lattice grid overlay — draw EVERY table's grid. The active table (the one
  // containing the selected cell) is bright with interactive resize/merge/sep
  // handles; other tables are drawn dim and static (click a cell in one to
  // make it active and editable).
  if (latticeVisible && pageData?.shapes) {
    const _AT = _activeTable();
    _latticeTableIds().forEach(tid => {
      const isActive = tid === _AT;
      const sShapes = pageData.shapes.filter(s => _tableOf(s)===tid && s.points?.length>=2);
      if (sShapes.length <= 1) return;
      const rowData={}, colData={};
      sShapes.forEach(s => {
        const r=_shapeRect(s), sr=s.super_row, sc=s.super_column;
        if (!rowData[sr]) rowData[sr]={tops:[],bots:[]};
        rowData[sr].tops.push(r.y1); rowData[sr].bots.push(r.y2);
        if (!colData[sc]) colData[sc]={lefts:[],rights:[]};
        colData[sc].lefts.push(r.x1); colData[sc].rights.push(r.x2);
      });
      const rowBands={}, colBands={};
      Object.entries(rowData).forEach(([r,d])=>rowBands[+r]={top:_median(d.tops),bot:_median(d.bots)});
      Object.entries(colData).forEach(([c,d])=>colBands[+c]={left:_median(d.lefts),right:_median(d.rights)});
      const sRows=Object.keys(rowBands).map(Number).sort((a,b)=>a-b);
      const sCols=Object.keys(colBands).map(Number).sort((a,b)=>a-b);
      if (!(sRows.length && sCols.length)) return;
      const gxL=colBands[sCols[0]].left, gxR=colBands[sCols[sCols.length-1]].right;
      const gyT=rowBands[sRows[0]].top,   gyB=rowBands[sRows[sRows.length-1]].bot;
      const inDelMode = isActive && latticeSepMode === 'delete';
      const lineCol = !isActive ? '#5fa8c0' : (inDelMode ? '#ff4466' : '#00e5ff');
      const lineW   = isActive ? (inDelMode ? 3 : 2.5) : 1.2;
      const lineOp  = isActive ? 0.75 : 0.35;
      const mkLine=(x1,y1,x2,y2)=>{
        const p1=imgToScreen(x1,y1),p2=imgToScreen(x2,y2); if(!p1||!p2) return;
        const l=document.createElementNS('http://www.w3.org/2000/svg','line');
        l.setAttribute('x1',p1.x); l.setAttribute('y1',p1.y);
        l.setAttribute('x2',p2.x); l.setAttribute('y2',p2.y);
        l.setAttribute('stroke', lineCol);
        l.setAttribute('stroke-width', lineW);
        l.setAttribute('stroke-opacity', lineOp); l.style.pointerEvents='none';
        svgOverlay.appendChild(l);
      };
      const mkLabel=(ix,iy,txt,anchor='middle')=>{
        const sp=imgToScreen(ix,iy); if(!sp) return;
        const t=document.createElementNS('http://www.w3.org/2000/svg','text');
        t.setAttribute('x',sp.x); t.setAttribute('y',sp.y);
        t.setAttribute('fill', lineCol); t.setAttribute('font-size','10');
        t.setAttribute('font-weight','700'); t.setAttribute('text-anchor',anchor);
        t.setAttribute('opacity', isActive ? 1 : 0.55);
        t.style.pointerEvents='none';
        t.textContent=txt; svgOverlay.appendChild(t);
      };
      // Interactive handles only on the active table
      const mkRowHandle=(yImg,rowAbove,rowBelow)=>{
        if (!isActive) return;
        const pL=imgToScreen(gxL,yImg),pR=imgToScreen(gxR,yImg); if(!pL||!pR) return;
        const hit=document.createElementNS('http://www.w3.org/2000/svg','rect');
        hit.setAttribute('x',pL.x); hit.setAttribute('y',pL.y-5);
        hit.setAttribute('width',Math.max(0,pR.x-pL.x)); hit.setAttribute('height',10);
        hit.setAttribute('fill','transparent');
        hit.style.pointerEvents='all';
        const canSplit = latticeSplitMode && rowAbove!=null && rowBelow!=null;
        hit.style.cursor = canSplit ? 'crosshair'
                         : (inDelMode && rowAbove!=null && rowBelow!=null) ? 'pointer' : 'row-resize';
        hit.addEventListener('mousedown',e=>{
          e.stopPropagation();
          if (latticeSplitMode) { if (rowAbove!=null&&rowBelow!=null){ e.preventDefault(); _latticeSplitHoriz(rowAbove,rowBelow); } return; }
          if (inDelMode) { if (rowAbove!=null&&rowBelow!=null) handleLatticeMergeRow(rowAbove,rowBelow,rowBands,colBands); }
          else startLatticeRowResize(e,rowAbove,rowBelow);
        });
        svgOverlay.appendChild(hit);
      };
      const mkColHandle=(xImg,colLeft,colRight)=>{
        if (!isActive) return;
        const pT=imgToScreen(xImg,gyT),pB=imgToScreen(xImg,gyB); if(!pT||!pB) return;
        const hit=document.createElementNS('http://www.w3.org/2000/svg','rect');
        hit.setAttribute('x',pT.x-5); hit.setAttribute('y',pT.y);
        hit.setAttribute('width',10); hit.setAttribute('height',Math.max(0,pB.y-pT.y));
        hit.setAttribute('fill','transparent');
        hit.style.pointerEvents='all';
        const canSplit = latticeSplitMode && colLeft!=null && colRight!=null;
        hit.style.cursor = canSplit ? 'crosshair'
                         : (inDelMode && colLeft!=null && colRight!=null) ? 'pointer' : 'col-resize';
        hit.addEventListener('mousedown',e=>{
          e.stopPropagation();
          if (latticeSplitMode) { if (colLeft!=null&&colRight!=null){ e.preventDefault(); _latticeSplitVert(colLeft,colRight); } return; }
          if (inDelMode) { if (colLeft!=null&&colRight!=null) handleLatticeMergeCol(colLeft,colRight,rowBands,colBands); }
          else startLatticeColResize(e,colLeft,colRight);
        });
        svgOverlay.appendChild(hit);
      };
      // Horizontal lines + row labels + row resize handles
      sRows.forEach((r,ri) => {
        const b=rowBands[r];
        mkLine(gxL,b.top,gxR,b.top);
        mkLabel(gxL-4,(b.top+b.bot)/2,`${r}`,'end');
        mkRowHandle(b.top, ri>0?sRows[ri-1]:null, r);
      });
      mkLine(gxL,gyB,gxR,gyB);
      mkRowHandle(gyB, sRows[sRows.length-1], null);
      // Vertical lines + col labels + col resize handles
      sCols.forEach((c,ci) => {
        const b=colBands[c];
        mkLine(b.left,gyT,b.left,gyB);
        mkLabel(b.left+(colBands[c].right-b.left)/2,gyT-4,`${c}`,'middle');
        mkColHandle(b.left, ci>0?sCols[ci-1]:null, c);
      });
      mkLine(gxR,gyT,gxR,gyB);
      mkColHandle(gxR, sCols[sCols.length-1], null);

      // Lattice sep mode: transparent catcher over the active grid only
      if (isActive && (latticeSepMode === 'col' || latticeSepMode === 'row')) {
        const ptl=imgToScreen(gxL,gyT), pbr=imgToScreen(gxR,gyB);
        if (ptl && pbr) {
          const catcher=document.createElementNS('http://www.w3.org/2000/svg','rect');
          catcher.setAttribute('x',ptl.x); catcher.setAttribute('y',ptl.y);
          catcher.setAttribute('width',Math.max(0,pbr.x-ptl.x));
          catcher.setAttribute('height',Math.max(0,pbr.y-ptl.y));
          catcher.setAttribute('fill','none'); catcher.setAttribute('stroke','none');
          catcher.style.pointerEvents='all';
          catcher.style.cursor=_SEP_CURSOR;
          catcher.addEventListener('mousedown',e=>{e.stopPropagation();e.preventDefault();handleLatticeSepClick(e,rowBands,colBands);});
          svgOverlay.appendChild(catcher);
        }
      }
    });
  }

  // Ghost outlines for copy-drag (one per mover)
  if (dragState?.type==='copy' && dragState.ptsMap) {
    dragState.movers.forEach(mi => {
      const pts=dragState.ptsMap[mi]; if (!pts) return;
      const tl=imgToScreen(Math.min(pts[0][0],pts[1][0]),Math.min(pts[0][1],pts[1][1]));
      const br=imgToScreen(Math.max(pts[0][0],pts[1][0]),Math.max(pts[0][1],pts[1][1]));
      if (!tl||!br) return;
      const r=document.createElementNS('http://www.w3.org/2000/svg','rect');
      r.setAttribute('x',tl.x); r.setAttribute('y',tl.y);
      r.setAttribute('width',Math.max(0,br.x-tl.x));
      r.setAttribute('height',Math.max(0,br.y-tl.y));
      r.setAttribute('fill','#e94560'); r.setAttribute('fill-opacity','0.15');
      r.setAttribute('stroke','#e94560'); r.setAttribute('stroke-width','2');
      r.setAttribute('stroke-dasharray','6 3');
      svgOverlay.appendChild(r);
    });
  }

  // Rubber-band selection rect
  if (dragState?.type==='select-rect' && dragCurrentPts) {
    const pts=dragCurrentPts;
    const tl=imgToScreen(Math.min(pts[0][0],pts[1][0]),Math.min(pts[0][1],pts[1][1]));
    const br=imgToScreen(Math.max(pts[0][0],pts[1][0]),Math.max(pts[0][1],pts[1][1]));
    if (tl&&br) {
      const r=document.createElementNS('http://www.w3.org/2000/svg','rect');
      r.setAttribute('x',tl.x); r.setAttribute('y',tl.y);
      r.setAttribute('width',Math.max(0,br.x-tl.x));
      r.setAttribute('height',Math.max(0,br.y-tl.y));
      r.setAttribute('fill','#4ecdc4'); r.setAttribute('fill-opacity','0.08');
      r.setAttribute('stroke','#4ecdc4'); r.setAttribute('stroke-width','1.5');
      r.setAttribute('stroke-dasharray','5 3');
      svgOverlay.appendChild(r);
    }
  }

  // Table drawing overlay
  if (tableMode && tableRect) {
    const tl = imgToScreen(tableRect.x1, tableRect.y1);
    const br = imgToScreen(tableRect.x2, tableRect.y2);
    if (tl && br) {
      // Outline rect — click to redraw
      const outline = document.createElementNS('http://www.w3.org/2000/svg','rect');
      outline.setAttribute('x', tl.x); outline.setAttribute('y', tl.y);
      outline.setAttribute('width', Math.max(0, br.x-tl.x));
      outline.setAttribute('height', Math.max(0, br.y-tl.y));
      outline.setAttribute('fill', '#f0a500'); outline.setAttribute('fill-opacity','0.06');
      outline.setAttribute('stroke','#f0a500'); outline.setAttribute('stroke-width','2');
      outline.style.pointerEvents='all'; outline.style.cursor='crosshair';
      outline.addEventListener('mousedown', e => { e.stopPropagation(); handleTableClick(e); });
      outline.addEventListener('contextmenu', e => e.preventDefault());
      svgOverlay.appendChild(outline);

      // Column separators
      tableColSeps.forEach((cx, ci) => {
        const sx = imgToScreen(cx, tableRect.y1);
        const ex = imgToScreen(cx, tableRect.y2);
        if (!sx || !ex) return;
        const line = document.createElementNS('http://www.w3.org/2000/svg','line');
        line.setAttribute('x1', sx.x); line.setAttribute('y1', tl.y);
        line.setAttribute('x2', ex.x); line.setAttribute('y2', br.y);
        line.setAttribute('stroke','#f0a500'); line.setAttribute('stroke-width','1.5');
        line.style.pointerEvents='all'; line.style.cursor = tableTool==='delete'?'pointer':'default';
        line.addEventListener('mousedown', e => { e.stopPropagation(); if(tableTool==='delete'){pushTableUndo();tableColSeps.splice(ci,1);drawOverlay();} });
        svgOverlay.appendChild(line);
      });

      // Row separators
      tableRowSeps.forEach((ry, ri) => {
        const sy = imgToScreen(tableRect.x1, ry);
        const ey = imgToScreen(tableRect.x2, ry);
        if (!sy || !ey) return;
        const line = document.createElementNS('http://www.w3.org/2000/svg','line');
        line.setAttribute('x1', tl.x); line.setAttribute('y1', sy.y);
        line.setAttribute('x2', br.x); line.setAttribute('y2', ey.y);
        line.setAttribute('stroke','#f0a500'); line.setAttribute('stroke-width','1.5');
        line.style.pointerEvents='all'; line.style.cursor = tableTool==='delete'?'pointer':'default';
        line.addEventListener('mousedown', e => { e.stopPropagation(); if(tableTool==='delete'){pushTableUndo();tableRowSeps.splice(ri,1);drawOverlay();} });
        svgOverlay.appendChild(line);
      });

      // Transparent overlay over the whole table that sets cursor and routes clicks
      const catcher = document.createElementNS('http://www.w3.org/2000/svg','rect');
      catcher.setAttribute('x', tl.x); catcher.setAttribute('y', tl.y);
      catcher.setAttribute('width', Math.max(0, br.x-tl.x));
      catcher.setAttribute('height', Math.max(0, br.y-tl.y));
      catcher.setAttribute('fill','none'); catcher.setAttribute('stroke','none');
      catcher.style.pointerEvents = (tableTool==='col'||tableTool==='row') ? 'all' : 'none';
      catcher.style.cursor = tableTool==='col'?'col-resize': tableTool==='row'?'row-resize':'default';
      catcher.addEventListener('mousedown', e => { e.stopPropagation(); e.preventDefault(); handleTableClick(e); });
      catcher.addEventListener('contextmenu', e => e.preventDefault());
      svgOverlay.appendChild(catcher);
    }
  }

  // In-progress table outline
  if (dragState?.type==='draw-table' && dragCurrentPts) {
    const pts=dragCurrentPts;
    const tl=imgToScreen(Math.min(pts[0][0],pts[1][0]),Math.min(pts[0][1],pts[1][1]));
    const br=imgToScreen(Math.max(pts[0][0],pts[1][0]),Math.max(pts[0][1],pts[1][1]));
    if (tl&&br) {
      const r=document.createElementNS('http://www.w3.org/2000/svg','rect');
      r.setAttribute('x',tl.x); r.setAttribute('y',tl.y);
      r.setAttribute('width',Math.max(0,br.x-tl.x));
      r.setAttribute('height',Math.max(0,br.y-tl.y));
      r.setAttribute('fill','#f0a500'); r.setAttribute('fill-opacity','0.1');
      r.setAttribute('stroke','#f0a500'); r.setAttribute('stroke-width','2');
      r.setAttribute('stroke-dasharray','6 3');
      svgOverlay.appendChild(r);
    }
  }

  // In-progress new-box outline
  if (dragState?.type==='draw' && dragCurrentPts) {
    const pts=dragCurrentPts;
    const tl=imgToScreen(Math.min(pts[0][0],pts[1][0]),Math.min(pts[0][1],pts[1][1]));
    const br=imgToScreen(Math.max(pts[0][0],pts[1][0]),Math.max(pts[0][1],pts[1][1]));
    if (tl&&br) {
      const r=document.createElementNS('http://www.w3.org/2000/svg','rect');
      r.setAttribute('x',tl.x); r.setAttribute('y',tl.y);
      r.setAttribute('width',Math.max(0,br.x-tl.x));
      r.setAttribute('height',Math.max(0,br.y-tl.y));
      r.setAttribute('fill','#ffffff'); r.setAttribute('fill-opacity','0.08');
      r.setAttribute('stroke','#ffffff'); r.setAttribute('stroke-width','1.5');
      r.setAttribute('stroke-dasharray','6 3');
      svgOverlay.appendChild(r);
    }
  }

  // Perspective mode: transparent overlay captures all clicks before shapes do
  if (perspMode) {
    const cap = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    cap.setAttribute('x', '-999999'); cap.setAttribute('y', '-999999');
    cap.setAttribute('width', '9999999'); cap.setAttribute('height', '9999999');
    cap.setAttribute('fill', 'none');
    cap.style.pointerEvents = 'all';
    cap.style.cursor = 'crosshair';
    cap.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      if (!_inImageBounds(e.clientX, e.clientY)) {
        e.preventDefault(); e.stopPropagation();
        dragState = { type: 'pan', startX: e.clientX, startY: e.clientY };
        return;
      }
      e.preventDefault(); e.stopPropagation();
      if (perspPoints.length >= 4) return;
      const p = screenToImg(e.clientX, e.clientY);
      perspPoints.push([p.x, p.y]);
      document.getElementById('persp-corner-count').textContent = `${perspPoints.length} / 4 corners`;
      drawOverlay();
      showToast(perspPoints.length === 4
        ? '4 corners set — click "Apply correction" to preview'
        : `Corner ${perspPoints.length} of 4 placed`);
    });
    svgOverlay.appendChild(cap);
  }

  // Batch "All" — orange dashed frame around the shape currently being processed
  if (batchHighlight >= 0 && batchHighlight !== selIdx) {
    const bsh = pageData.shapes[batchHighlight];
    if (bsh?.points?.length >= 2) {
      const bpts = bsh.points;
      const btl = imgToScreen(Math.min(bpts[0][0],bpts[1][0]), Math.min(bpts[0][1],bpts[1][1]));
      const bbr = imgToScreen(Math.max(bpts[0][0],bpts[1][0]), Math.max(bpts[0][1],bpts[1][1]));
      if (btl && bbr) {
        const br = document.createElementNS('http://www.w3.org/2000/svg','rect');
        br.setAttribute('x',      btl.x); br.setAttribute('y',      btl.y);
        br.setAttribute('width',  Math.max(0, bbr.x - btl.x));
        br.setAttribute('height', Math.max(0, bbr.y - btl.y));
        br.setAttribute('fill',           'rgba(255,165,0,0.12)');
        br.setAttribute('stroke',         '#ffa500');
        br.setAttribute('stroke-width',   '2.5');
        br.setAttribute('stroke-dasharray','6 3');
        br.style.pointerEvents = 'none';
        svgOverlay.appendChild(br);
      }
    }
  }

  // LLM line-by-line progress: show all detected rows, highlight active one
  if (llmProgress && llmProgress.lines.length) {
    const { cropOriginX, cropOriginY, cropRight, lines, activeRow, emptyRows } = llmProgress;
    const empty = emptyRows ?? new Set();
    lines.forEach(([top, bottom], i) => {
      const isActive  = i === activeRow;
      const isEmpty   = empty.has(i);
      const tl = imgToScreen(cropOriginX, cropOriginY + top);
      const br = imgToScreen(cropRight,   cropOriginY + bottom);
      if (!tl || !br) return;
      const r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      r.setAttribute('x',      tl.x);
      r.setAttribute('y',      tl.y);
      r.setAttribute('width',  Math.max(0, br.x - tl.x));
      r.setAttribute('height', Math.max(0, br.y - tl.y));
      r.setAttribute('fill',   isActive ? 'rgba(255,100,0,0.18)' : isEmpty ? 'rgba(220,30,30,0.10)' : 'none');
      r.setAttribute('stroke', isActive ? '#ff6400' : isEmpty ? '#dc1e1e' : '#00a0ff');
      r.setAttribute('stroke-width',   isActive ? '2.5' : '1');
      r.setAttribute('stroke-opacity', isActive ? '1'   : isEmpty ? '0.7' : '0.5');
      r.style.pointerEvents = 'none';
      svgOverlay.appendChild(r);
    });
  }

  // Perspective corner points
  if (perspMode && perspPoints.length > 0) {
    const screenPts = perspPoints.map(([x,y]) => imgToScreen(x,y)).filter(Boolean);
    if (screenPts.length >= 2) {
      const poly = document.createElementNS('http://www.w3.org/2000/svg','polyline');
      const closed = screenPts.length === 4;
      const tag = closed ? 'polygon' : 'polyline';
      const shape = document.createElementNS('http://www.w3.org/2000/svg', tag);
      shape.setAttribute('points', screenPts.map(p=>`${p.x},${p.y}`).join(' '));
      shape.setAttribute('fill', closed ? '#e9456020' : 'none');
      shape.setAttribute('stroke','#e94560');
      shape.setAttribute('stroke-width','2');
      shape.setAttribute('stroke-dasharray','6 3');
      svgOverlay.appendChild(shape);
    }
    screenPts.forEach((sp, i) => {
      const c = document.createElementNS('http://www.w3.org/2000/svg','circle');
      c.setAttribute('cx', sp.x); c.setAttribute('cy', sp.y); c.setAttribute('r', 7);
      c.setAttribute('fill','#e94560'); c.setAttribute('stroke','#fff'); c.setAttribute('stroke-width','1.5');
      svgOverlay.appendChild(c);
      const t = document.createElementNS('http://www.w3.org/2000/svg','text');
      t.setAttribute('x', sp.x); t.setAttribute('y', sp.y+4);
      t.setAttribute('text-anchor','middle'); t.setAttribute('fill','#fff');
      t.setAttribute('font-size','9'); t.setAttribute('font-weight','bold');
      t.textContent = i+1;
      svgOverlay.appendChild(t);
    });
  }
}

// ── Image bounds helper ──────────────────────────────────────────────────────
function _inImageBounds(clientX, clientY) {
  if (!viewer?.viewport || !pageData) return false;
  const p = screenToImg(clientX, clientY);
  const w = pageData.imageWidth  || 99999;
  const h = pageData.imageHeight || 99999;
  return p.x >= 0 && p.y >= 0 && p.x <= w && p.y <= h;
}

// ── Drag: move ──────────────────────────────────────────────────────────────
function startMoveDrag(e, idx) {
  pushUndo();
  const movers = selSet.has(idx) ? [...selSet] : [idx];
  const origPtsMap = {};
  movers.forEach(i => origPtsMap[i] = pageData.shapes[i].points.map(p=>[...p]));
  dragState = { type:'move', idx, startX:e.clientX, startY:e.clientY, movers, origPtsMap, ptsMap:{...origPtsMap} };
  dragCurrentPts = origPtsMap[idx].map(p=>[...p]);
}

// ── Drag: copy ───────────────────────────────────────────────
function startCopyDrag(e, idx) {
  const movers = selSet.has(idx) ? [...selSet] : [idx];
  const origPtsMap = {};
  movers.forEach(i => origPtsMap[i] = pageData.shapes[i].points.map(p=>[...p]));
  dragState = { type:'copy', idx, startX:e.clientX, startY:e.clientY, movers, origPtsMap, ptsMap:{...origPtsMap} };
  dragCurrentPts = origPtsMap[idx].map(p=>[...p]);
}

// ── Drag: resize ─────────────────────────────────────────────────────────────
function startResizeDrag(e, idx, handle) {
  pushUndo();
  dragState = { type:'resize', idx, handle, startX:e.clientX, startY:e.clientY,
                origPts: pageData.shapes[idx].points.map(p=>[...p]) };
  dragCurrentPts = dragState.origPts.map(p=>[...p]);
}
function applyResize(origPts, handle, dxi, dyi) {
  const xs=origPts.map(p=>p[0]), ys=origPts.map(p=>p[1]);
  let x1=Math.min(...xs),y1=Math.min(...ys),x2=Math.max(...xs),y2=Math.max(...ys);
  if (handle.includes('w')) x1+=dxi;
  if (handle.includes('e')) x2+=dxi;
  if (handle.includes('n')) y1+=dyi;
  if (handle.includes('s')) y2+=dyi;
  if (x2-x1<5) { handle.includes('w') ? x1=x2-5 : (x2=x1+5); }
  if (y2-y1<5) { handle.includes('n') ? y1=y2-5 : (y2=y1+5); }
  return [[x1,y1],[x2,y2]];
}

// ── Drag: draw new box OR rubber-band select ─────────────────────────────────
function onSvgBackground(e) {
  // perspMode clicks are handled entirely by the capture rect in drawOverlay
  if (perspMode) return;

  if (tableMode && !tableRect && e.target===svgOverlay) {
    if (!_inImageBounds(e.clientX, e.clientY)) {
      e.preventDefault(); e.stopPropagation();
      dragState = { type: 'pan', startX: e.clientX, startY: e.clientY }; return;
    }
    e.preventDefault(); e.stopPropagation();
    const p = screenToImg(e.clientX, e.clientY);
    dragState = { type:'draw-table', startX:e.clientX, startY:e.clientY, origPts:[[p.x,p.y],[p.x,p.y]] };
    dragCurrentPts = [[p.x,p.y],[p.x,p.y]];
    return;
  }
  if (!editMode || e.target!==svgOverlay) return;
  if (!_inImageBounds(e.clientX, e.clientY)) {
    e.preventDefault(); e.stopPropagation();
    dragState = { type: 'pan', startX: e.clientX, startY: e.clientY }; return;
  }
  e.preventDefault(); e.stopPropagation();
  const p=screenToImg(e.clientX, e.clientY);
  if (e.ctrlKey||e.metaKey||e.button===2) {
    dragState = { type:'select-rect', startX:e.clientX, startY:e.clientY,
                  origPts:[[p.x,p.y],[p.x,p.y]] };
    dragCurrentPts = [[p.x,p.y],[p.x,p.y]];
  } else {
    pushUndo();
    const drawLabel = lastUsedLabel || (pageData.shapes.length ? pageData.shapes[pageData.shapes.length-1].label : (projectLabels[0] || 'cell'));
    selIdx = -1; selSet.clear();
    dragState = { type:'draw', startX:e.clientX, startY:e.clientY,
                  origPts:[[p.x,p.y],[p.x,p.y]], drawLabel };
    dragCurrentPts = [[p.x,p.y],[p.x,p.y]];
  }
}

// ── Global mouse tracking ────────────────────────────────────────────────────
window.addEventListener('mousemove', e => {
  if (!dragState) return;
  const dx=e.clientX-dragState.startX, dy=e.clientY-dragState.startY;
  if (dragState.type==='move'||dragState.type==='copy') {
    const d=screenDeltaToImg(dx,dy);
    const ptsMap={};
    dragState.movers.forEach(i => { ptsMap[i]=dragState.origPtsMap[i].map(p=>[p[0]+d.dx,p[1]+d.dy]); });
    dragState.ptsMap=ptsMap;
    dragCurrentPts=ptsMap[dragState.idx];
  } else if (dragState.type==='resize') {
    const d=screenDeltaToImg(dx,dy);
    dragCurrentPts=applyResize(dragState.origPts,dragState.handle,d.dx,d.dy);
  } else if (dragState.type==='pan') {
    if (viewer?.viewport) {
      const vd = viewer.viewport.deltaPointsFromPixels(new OpenSeadragon.Point(dx, dy));
      viewer.viewport.panBy(new OpenSeadragon.Point(-vd.x, -vd.y), false);
      dragState.startX = e.clientX; dragState.startY = e.clientY;
    }
    return;
  } else if (dragState.type==='lattice-row') {
    const d=screenDeltaToImg(0, e.clientY-dragState.startY);
    _applyLatticeDrag(dragState.adjustments, dragState.origPts, d.dy, 'y');
  } else if (dragState.type==='lattice-col') {
    const d=screenDeltaToImg(e.clientX-dragState.startX, 0);
    _applyLatticeDrag(dragState.adjustments, dragState.origPts, d.dx, 'x');
  } else if (dragState.type==='draw'||dragState.type==='select-rect'||dragState.type==='draw-table') {
    const p=screenToImg(e.clientX,e.clientY);
    dragCurrentPts=[dragState.origPts[0],[p.x,p.y]];
  }
  drawOverlay();
});

window.addEventListener('mouseup', async e => {
  if (!dragState) return;
  const state=dragState; dragState=null;

  if (state.type==='select-rect') {
    const pts=dragCurrentPts; dragCurrentPts=null;
    if (!pts) { drawOverlay(); return; }
    const rx1=Math.min(pts[0][0],pts[1][0]), ry1=Math.min(pts[0][1],pts[1][1]);
    const rx2=Math.max(pts[0][0],pts[1][0]), ry2=Math.max(pts[0][1],pts[1][1]);
    (pageData.shapes||[]).forEach((shape,i) => {
      const sp=shape.points||[];
      const sx1=Math.min(sp[0][0],sp[1][0]), sy1=Math.min(sp[0][1],sp[1][1]);
      const sx2=Math.max(sp[0][0],sp[1][0]), sy2=Math.max(sp[0][1],sp[1][1]);
      const overlaps = sx1<rx2 && sx2>rx1 && sy1<ry2 && sy2>ry1;
      if (overlaps) { selSet.add(i); selIdx=i; }
    });
    drawOverlay(); updatePanel();

  } else if (state.type==='lattice-row' || state.type==='lattice-col') {
    // Points were mutated during the drag; remap internal row bands from the
    // original bbox to the new one before persisting
    Object.entries(state.origPts || {}).forEach(([i, op]) => {
      _rescaleRowStructLocal(pageData.shapes[i], op, pageData.shapes[i].points);
    });
    drawOverlay();
    await replaceAllShapes();

  } else if (state.type==='resize') {
    _rescaleRowStructLocal(pageData.shapes[state.idx], pageData.shapes[state.idx].points, dragCurrentPts);
    pageData.shapes[state.idx].points=dragCurrentPts;
    dragCurrentPts=null; drawOverlay();
    await patchShape(state.idx, { points: pageData.shapes[state.idx].points });
    updatePanel();

  } else if (state.type==='move') {
    dragCurrentPts=null;
    state.movers.forEach(i => {
      _rescaleRowStructLocal(pageData.shapes[i], pageData.shapes[i].points, state.ptsMap[i]);
      pageData.shapes[i].points=state.ptsMap[i];
    });
    drawOverlay();
    await replaceAllShapes();
    updatePanel();

  } else if (state.type==='copy') {
    dragCurrentPts=null;
    const params=new URLSearchParams({folder, stem:pages[pageIdx].stem});
    pushUndo();
    const newIdxs=[];
    for (const i of state.movers) {
      const r=await fetch(`${API}/api/page/shape?${params}`,{
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ label:pageData.shapes[i].label, points:state.ptsMap[i] }),
      });
      if (r.ok) { const data=await r.json(); newIdxs.push(data.idx); }
    }
    await reloadPageData();
    selSet.clear(); selIdx=-1;
    newIdxs.forEach(i => { selSet.add(i); selIdx=i; });
    drawOverlay(); updatePanel();

  } else if (state.type==='draw-table') {
    const pts=dragCurrentPts; dragCurrentPts=null;
    if (!pts) { drawOverlay(); return; }
    const xs=pts.map(p=>p[0]), ys=pts.map(p=>p[1]);
    if (Math.abs(xs[1]-xs[0])<5||Math.abs(ys[1]-ys[0])<5) { drawOverlay(); return; }
    tableRect = { x1:Math.min(...xs), y1:Math.min(...ys), x2:Math.max(...xs), y2:Math.max(...ys) };
    tableTool = 'col';
    updateTableToolButtons();
    drawOverlay();
    showToast('Click inside to add column or row separators');
    return;

  } else if (state.type==='draw') {
    const pts=dragCurrentPts; dragCurrentPts=null;
    if (!pts) { drawOverlay(); return; }
    const xs=pts.map(p=>p[0]), ys=pts.map(p=>p[1]);
    if (Math.abs(xs[1]-xs[0])<5||Math.abs(ys[1]-ys[0])<5) {
      undoStack.pop(); drawOverlay(); return;
    }
    const label=state.drawLabel;
    const params=new URLSearchParams({folder,stem:pages[pageIdx].stem});
    const body={ label, points:[[Math.min(...xs),Math.min(...ys)],[Math.max(...xs),Math.max(...ys)]] };
    try {
      const r=await fetch(`${API}/api/page/shape?${params}`,{
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body),
      });
      if (r.ok) {
        const data=await r.json();
        await reloadPageData(); selectShape(data.idx);
      } else {
        const txt=await r.text();
        document.getElementById('save-status').textContent=`✗ Error ${r.status}: ${txt.slice(0,80)}`;
        document.getElementById('save-status').style.color='#e94560';
      }
    } catch(err) {
      document.getElementById('save-status').textContent=`✗ ${err.message}`;
      document.getElementById('save-status').style.color='#e94560';
    }
  }
});

