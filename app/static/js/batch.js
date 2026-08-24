// Split from index.html — classic scripts share the global scope;
// load order in index.html is load-bearing. See knowledge_base/02_architecture.md.
// ── Autofill ─────────────────────────────────────────────────────────────────
async function autofill() {
  const n = parseInt(prompt(`Copy annotations from first N pages to all remaining pages.\nEnter N:`));
  if (!n || n < 1 || n >= pages.length) return;
  if (!confirm(`This will overwrite annotations on pages ${n+1}–${pages.length} using pages 1–${n} as a repeating template. Continue?`)) return;

  // Fetch shapes from the template pages
  const templates = [];
  for (let i = 0; i < n; i++) {
    const params = new URLSearchParams({folder, stem: pages[i].stem});
    const res = await fetch(`${API}/api/page?${params}`);
    const data = await res.json();
    templates.push((data.shapes || []).map(s => ({ label: s.label, points: s.points.map(p=>[...p]) })));
  }

  const btn = document.getElementById('autofill-btn');
  btn.disabled = true;
  let filled = 0;
  for (let i = n; i < pages.length; i++) {
    const shapes = templates[i % n];
    const params = new URLSearchParams({folder, stem: pages[i].stem});
    await fetch(`${API}/api/page/shapes?${params}`, {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({shapes}),
    });
    filled++;
    btn.textContent = `Autofill (${filled}/${pages.length - n})`;
  }

  btn.textContent = 'Autofill';
  btn.disabled = false;
  showToast(`Autofilled ${filled} pages using ${n}-page template`);
  await loadPage(pageIdx);  // refresh current view
}

// ── Clear annotations ────────────────────────────────────────────────────────
// ── Batch process modal ───────────────────────────────────────────────────────
let _batchRunning = false;
let _batchStop    = false;

function _parsePageRange(raw) {
  const n = pages.length;
  const indices = new Set();
  const parts = raw.trim() ? raw.split(',') : ['-'];
  for (const part of parts) {
    const s = part.trim();
    if (!s) continue;
    const m = s.match(/^(\d*)-(\d*)$|^(\d+)$/);
    if (!m) { alert(`Cannot parse: "${s}"`); return null; }
    if (m[3] !== undefined) {
      const p = parseInt(m[3]) - 1;
      if (p >= 0 && p < n) indices.add(p);
    } else {
      const from = m[1] === '' ? 0     : parseInt(m[1]) - 1;
      const to   = m[2] === '' ? n - 1 : parseInt(m[2]) - 1;
      for (let i = Math.max(0, from); i <= Math.min(n - 1, to); i++) indices.add(i);
    }
  }
  return indices;
}

function _batchPopulateLabels(containerId, storageKey) {
  const labels = [...new Set([
    ...projectLabels,
    ...(pageData?.shapes || []).map(s => s.label),
  ])].sort();
  let remembered = {};
  try { remembered = JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch(e) {}
  document.getElementById(containerId).innerHTML = labels.map(lbl => {
    const checked = (lbl in remembered) ? remembered[lbl] : true;
    return `<label><input type="checkbox" value="${lbl}" ${checked ? 'checked' : ''}> ${lbl}</label>`;
  }).join('');
}

function openBatchModal() {
  _batchPopulateLabels('batch-label-checks',           'latticeLabels');
  _batchPopulateLabels('batch-ocr-label-checks',       'batchOcrLabels');
  _batchPopulateLabels('batch-llm-label-checks',       'batchLlmLabels');
  _batchPopulateLabels('batch-score-label-checks',     'batchScoreLabels');
  _batchPopulateLabels('batch-clear-ocr-label-checks', 'batchClearOcrLabels');
  _batchPopulateLabels('batch-clear-llm-label-checks',          'batchClearLlmLabels');
  _batchPopulateLabels('batch-llm-halluc-label-checks',      'batchLlmHallucLabels');
  // Sync model/mode from the main LLM panel if available
  const savedModel = localStorage.getItem('llm-model');
  if (savedModel) document.getElementById('batch-llm-model').value = savedModel;
  const sp = localStorage.getItem('llm-payload'), ss = localStorage.getItem('llm-scope');
  if (sp) document.getElementById('batch-llm-payload').value = sp;
  if (ss && ss !== 'anchored') document.getElementById('batch-llm-scope').value = ss === 'whole' ? 'whole' : ss;
  onBatchLlmModeChange();
  const savedPrompt = document.getElementById('llm-prompt')?.value?.trim();
  if (savedPrompt) document.getElementById('batch-llm-prompt').value = savedPrompt;

  _batchRunning = false;
  _batchStop    = false;
  document.getElementById('batch-pages').value = '';
  const _pat = document.getElementById('batch-pattern'); if (_pat) _pat.value = '';
  document.getElementById('batch-op').value = 'overlaps_lattice';
  onBatchOpChange();
  document.getElementById('batch-progress').textContent = '';
  document.getElementById('batch-progress').style.color = '';
  document.getElementById('batch-progress-bar-wrap').style.display = 'none';
  document.getElementById('batch-progress-bar').style.width = '0%';
  document.getElementById('batch-run-btn').textContent = 'Run';
  document.getElementById('batch-run-btn').disabled = false;
  document.getElementById('batch-cancel-btn').textContent = 'Cancel';
  const parEl = document.getElementById('batch-llm-parallel');
  if (parEl) parEl.value = localStorage.getItem('batchLlmParallel') || '6';
  _refreshBatchPresets();
  document.getElementById('batch-modal').classList.add('show');
}

function closeBatchModal() {
  if (_batchRunning) { _batchStop = true; return; }
  document.getElementById('batch-modal').classList.remove('show');
}

function onBatchOpChange() {
  const op = document.getElementById('batch-op').value;
  document.getElementById('batch-status-opts').style.display    = op === 'set_status' ? '' : 'none';
  document.getElementById('batch-ol-opts').style.display        = (op === 'overlaps_lattice' || op === 'overlaps_lattice_snap_trim') ? 'flex' : 'none';
  document.getElementById('batch-ocr-opts').style.display = op === 'ocr' ? 'flex' : 'none';
  if (op === 'ocr') _syncBatchOcrCellHeight();
  const isLlmOp = op === 'llm' || op === 'llm_batchapi';
  document.getElementById('batch-llm-opts').style.display       = isLlmOp ? 'flex' : 'none';
  document.getElementById('batch-llm-parallel-wrap').style.display = op === 'llm' ? '' : 'none';
  document.getElementById('batch-overnight-opts').style.display = op === 'llm_batchapi' ? 'flex' : 'none';
  document.getElementById('batch-run-btn').textContent = op === 'llm_batchapi' ? '🌙 Submit job' : 'Run';
  if (op === 'llm_batchapi') _refreshOvernightJobs();
  document.getElementById('batch-score-opts').style.display     = op === 'score_delete' ? 'flex' : 'none';
  document.getElementById('batch-clear-ocr-opts').style.display = op === 'clear_ocr'         ? 'flex' : 'none';
  document.getElementById('batch-clear-llm-opts').style.display          = op === 'clear_llm'          ? 'flex' : 'none';
  document.getElementById('batch-clear-llm-halluc-opts').style.display = op === 'clear_llm_hallucinations' ? 'flex' : 'none';
  document.getElementById('batch-strip-opts').style.display               = op === 'strip_short_lines'   ? 'flex' : 'none';
  document.getElementById('batch-rowsbuild-opts').style.display = op === 'convert_rows' ? 'flex' : 'none';
  if (op === 'convert_rows') _syncRowsBuildCellHeight();
  const isAuth = op === 'resolve_authority';
  document.getElementById('batch-auth-opts').style.display = isAuth ? 'flex' : 'none';
  if (isAuth) _batchAuthInit();
  document.getElementById('batch-dup-opts').style.display = op === 'auth_duplicates' ? 'flex' : 'none';
  document.getElementById('batch-unres-opts').style.display = op === 'auth_unresolved' ? 'flex' : 'none';
  document.getElementById('batch-lookup-opts').style.display = op === 'auth_lookup' ? 'flex' : 'none';
  const isJsonExp = op === 'json_export';
  document.getElementById('batch-jsonexport-opts').style.display = isJsonExp ? 'flex' : 'none';
  if (isJsonExp) _batchPopulateJsonExportLabels();
  const isDataset = op === 'dataset_diagnose';
  document.getElementById('batch-dataset-opts').style.display = isDataset ? '' : 'none';
  if (isDataset) _batchDatasetInit();
}

// OCR target row height matters only when re-detecting rows from the image
function _syncBatchOcrCellHeight() {
  const scope = document.getElementById('batch-ocr-scope')?.value;
  const wrap = document.getElementById('batch-ocr-cellheight-wrap');
  if (wrap) wrap.style.display = (scope === 'rows-detect') ? '' : 'none';
}

// target row height only matters when rows are detected from the image;
// layer-count sources fix the count and split by histogram
function _syncRowsBuildCellHeight() {
  const src = document.getElementById('batch-rowsbuild-source')?.value;
  const wrap = document.getElementById('batch-rowsbuild-cellheight-wrap');
  if (wrap) wrap.style.display = (src === 'image') ? '' : 'none';
}

function _batchPopulateJsonExportLabels() {
  const labels = [...new Set([...projectLabels, ...(pageData?.shapes || []).map(s => s.label)])].sort();
  let remembered = {};
  try { remembered = JSON.parse(localStorage.getItem('batchJsonExportModes') || '{}'); } catch (e) {}
  const opt = (cur, val, txt) => `<option value="${val}"${cur === val ? ' selected' : ''}>${txt}</option>`;
  document.getElementById('batch-jsonexport-labels').innerHTML = labels.map(lbl => {
    const v = remembered[lbl] || 'export';
    return `<label class="row" style="gap:8px;"><span style="flex:1;min-width:0;">${_escHtml(lbl)}</span>`
      + `<select data-label="${_escHtml(lbl)}" class="bje-mode" style="flex:none;width:160px;">`
      + opt(v, 'export', 'Export JSON') + opt(v, 'ignore', 'Ignore') + opt(v, 'propagate', 'Propagate forward')
      + `</select></label>`;
  }).join('') || '<div style="color:#555;font-size:11px;">No labels found.</div>';
}

async function _batchAuthInit() {
  await _ensureAuthList();
  const fsel = document.getElementById('batch-auth-file');
  if (fsel && !fsel.options.length) {
    const list = _authListCache.authorities || [];
    const cur = _authFile();
    fsel.innerHTML = list.length
      ? list.map(a => `<option value="${_escHtml(a.file)}"${a.file === cur ? ' selected' : ''}>${_escHtml(a.authority || a.file)}</option>`).join('')
      : `<option value="">(no authorities found)</option>`;
  }
  _batchAuthTypes();
}
function _batchAuthTypes() {
  const file = document.getElementById('batch-auth-file').value;
  const types = _authMeta(file)?.entity_types || [];
  document.getElementById('batch-auth-type').innerHTML = `<option value="">All types</option>` +
    types.map(t => `<option value="${_escHtml(t)}">${_escHtml(t.charAt(0).toUpperCase() + t.slice(1))}</option>`).join('');
}

function onBatchParityChange(which) {
  if (which === 'odd'  && document.getElementById('batch-parity-odd').checked)
    document.getElementById('batch-parity-even').checked = false;
  if (which === 'even' && document.getElementById('batch-parity-even').checked)
    document.getElementById('batch-parity-odd').checked  = false;
}

function onBatchLlmModeChange() {
  // visible dropdowns: payload (what the model sees) × scope (what one
  // request covers); the hidden batch-llm-mode keeps the legacy value
  const p = document.getElementById('batch-llm-payload').value;
  const s = document.getElementById('batch-llm-scope').value;
  document.getElementById('batch-llm-mode').value =
    (s === 'rows-keep' || s === 'rows-detect') ? 'linebyline' : p;
  // the target row height only matters when rows are re-detected
  document.getElementById('batch-llm-cellheight-wrap').style.display =
    s === 'rows-detect' ? '' : 'none';
}

function _batchRowParams() {
  const s = document.getElementById('batch-llm-scope').value;
  return { payload: document.getElementById('batch-llm-payload').value,
           rows_source: s === 'rows-detect' ? 'detect'
                      : s === 'rows-keep'   ? 'existing' : 'auto' };
}

function _computeOverlapsOnShapes(shapes, threshold) {
  const rects = shapes.map(_shapeRect);
  const areas = rects.map(r => (r.x2 - r.x1) * (r.y2 - r.y1));
  const toDelete = new Set();
  for (let i = 0; i < shapes.length; i++) {
    if (areas[i] <= 0) continue;
    for (let j = i + 1; j < shapes.length; j++) {
      if (areas[j] <= 0) continue;
      const inter = _intersectionArea(rects[i], rects[j]);
      if (inter <= 0) continue;
      const minArea = Math.min(areas[i], areas[j]);
      if (inter / minArea >= threshold) {
        toDelete.add(areas[i] >= areas[j] ? i : j);
      }
    }
  }
  return toDelete;
}

// Returns a Set of shape indices that pass the condition, or null meaning "all shapes".
function _computeConditionFilter(shapes) {
  const cond = document.getElementById('batch-condition').value;
  if (cond === 'none') return null;

  if (cond === 'skip_blank') {
    // keep only shapes NOT marked as a structural blank (whole cell blank, or
    // — for cells with internal rows — every row blank)
    const flagged = new Set();
    shapes.forEach((s, i) => {
      const rows = s.row_struct?.rows;
      const allBlank = rows?.length ? rows.every(r => r.blank) : !!s.blank;
      if (!allBlank) flagged.add(i);
    });
    return flagged;
  }

  if (cond === 'pdf_has_digits' || cond === 'pdf_has_chars' || cond === 'pdf_has_any') {
    // "meaningful" PDF layer: at least one digit, or at least 3 Unicode letters
    const hasDigit  = t => /\d/.test(t);
    const hasChars  = t => (t.match(/\p{L}/gu) || []).length >= 3;
    const flagged   = new Set();
    shapes.forEach((s, i) => {
      const t = s.pdf_text || '';
      if (!t.trim()) return;
      const ok = cond === 'pdf_has_digits' ? hasDigit(t)
               : cond === 'pdf_has_chars'  ? hasChars(t)
               :                             hasDigit(t) || hasChars(t);
      if (ok) flagged.add(i);
    });
    return flagged;
  }

  if (cond === 'rows_llm_empty' || cond === 'rows_llm_empty_flat') {
    // Cells WITH an internal row structure whose LLM column is entirely
    // empty. Typical case: a whole-cell LLM ran before the structure was
    // rebuilt with a different row count — the flat LLM survives but the
    // rows have nothing, so exports fall back to OCR. These need a per-row
    // re-run. The _flat variant restricts to cells where that whole-cell
    // LLM output actually exists (the mismatch backlog, excluding cells
    // never LLM'd at all). NOTE: when the flat LLM exists, the "already has
    // a result" skip fires unless Overwrite is checked.
    const flagged = new Set();
    shapes.forEach((s, i) => {
      const rows = s.row_struct?.rows;
      if (!rows?.length || rows.some(r => (r.llm || '').trim())) return;
      if (cond === 'rows_llm_empty_flat' && !(s.openai_output?.response || '').trim()) return;
      flagged.add(i);
    });
    return flagged;
  }

  if (cond === 'ocr_row_minority') {
    const flagged = new Set();
    const rowGroups = {};
    shapes.forEach((s, i) => {
      if (s.super_row == null) return;
      (rowGroups[_rk(s)] ??= []).push(i);
    });
    Object.values(rowGroups).forEach(idxs => {
      const withData = idxs.filter(i => shapes[i].tesseract_output?.ocr_text);
      if (withData.length < 2) return;
      const freq = {};
      withData.forEach(i => {
        const n = _lineCount(shapes[i].tesseract_output.ocr_text);
        freq[n] = (freq[n] || 0) + 1;
      });
      const modeCount = parseInt(Object.entries(freq).sort((a, b) => b[1] - a[1])[0][0]);
      withData.forEach(i => {
        if (_lineCount(shapes[i].tesseract_output.ocr_text) !== modeCount) flagged.add(i);
      });
    });
    return flagged;
  }

  return null;
}

// Returns null (no filter) or a Set of shape indices whose super_column matches the user input.
// 1-indexed column spec, same grammar as the server / Excel export:
//   "3" = col 3 · "2-4" = 2,3,4 · "3-" = 3…end · "-5" = 1…5 · "1,3,5" = list.
function _parseColRanges(raw) {
  const ranges = [];
  for (const part of raw.split(',')) {
    const p = part.trim();
    if (!p) continue;
    if (p.includes('-')) {
      const [a, b] = p.split('-');
      const lo = a.trim() ? parseInt(a, 10) : 1;
      const hi = b.trim() ? parseInt(b, 10) : Infinity;
      if (!isNaN(lo) && !isNaN(hi)) ranges.push([lo, hi]);
    } else {
      const v = parseInt(p, 10);
      if (!isNaN(v)) ranges.push([v, v]);
    }
  }
  return ranges.length ? ranges : null;
}

function _computeColumnFilter(shapes) {
  const raw = document.getElementById('batch-col-filter').value.trim();
  if (!raw) return null;
  const ranges = _parseColRanges(raw);
  if (!ranges) return null;
  const match = c => c != null && ranges.some(([lo, hi]) => c >= lo && c <= hi);
  const result = new Set();
  shapes.forEach((s, i) => { if (match(s.super_column)) result.add(i); });
  return result;
}

// ── Strip short lines helpers ─────────────────────────────────────────────────

function _stripShortLines(text) {
  if (!text) return text;
  return text.split('\n').filter(line => {
    const t = line.trim();
    if (!t) return false;                    // blank line → drop
    if (/^\d{1,2}$/.test(t)) return true;   // 1-2 digit number → keep
    return t.length >= 3;                    // short non-number → drop
  }).join('\n');
}

function _getTextField(shape, field) {
  if (field === 'ocr')   return shape.tesseract_output?.ocr_text   ?? null;
  if (field === 'llm')   return shape.openai_output?.response       ?? null;
  if (field === 'human') return shape.human_output?.human_corrected_text ?? null;
  if (field === 'pdf')   return shape.pdf_text                      ?? null;
  return null;
}

function _setTextField(shape, field, text) {
  if (field === 'ocr') {
    if (!shape.tesseract_output) shape.tesseract_output = {};
    shape.tesseract_output.ocr_text = text;
  } else if (field === 'llm') {
    if (!shape.openai_output) shape.openai_output = {};
    shape.openai_output.response = text;
  } else if (field === 'human') {
    if (!shape.human_output) shape.human_output = {};
    shape.human_output.human_corrected_text = text;
  } else if (field === 'pdf') {
    shape.pdf_text = text;
  }
  // Keep the internal row table in sync when the line counts match
  if (field === 'ocr' || field === 'llm' || field === 'human') {
    const rows = _rsRows(shape);
    if (rows) {
      const lines = (text || '').split('\n');
      if (lines.length === rows.length) rows.forEach((r, i) => { r[field] = lines[i]; });
    }
  }
}

// Repeating 1/0 page pattern (e.g. "0,1,0" = the middle page of every 3),
// anchored to page 1 — same idea as the Excel export's page pattern.
// Returns null when the field is blank, an array of 0/1 otherwise, or
// undefined (+ toast) when the input is not parseable.
function _batchPagePattern() {
  const raw = document.getElementById('batch-pattern')?.value.trim() || '';
  if (!raw) return null;
  const bits = raw.split(/[\s,;]+/).filter(Boolean);
  if (!bits.length || bits.some(b => b !== '0' && b !== '1')) {
    showToast('Pattern must be 1s and 0s, e.g. 0,1,0');
    return undefined;
  }
  return bits.map(Number);
}

// Filter a Set of 0-based page indices in place by the repeating pattern
// (pattern[0] applies to page 1). Returns false when the pattern is invalid.
function _batchApplyPattern(indices) {
  const pat = _batchPagePattern();
  if (pat === undefined) return false;
  if (pat) for (const i of [...indices]) { if (!pat[i % pat.length]) indices.delete(i); }
  return true;
}

async function runBatch() {
  const op       = document.getElementById('batch-op').value;
  const rawPages   = document.getElementById('batch-pages').value;
  const indices    = _parsePageRange(rawPages);
  if (!indices) return;
  const parityOdd  = document.getElementById('batch-parity-odd').checked;
  const parityEven = document.getElementById('batch-parity-even').checked;
  if (parityOdd)  for (const i of [...indices]) { if ((i + 1) % 2 === 0) indices.delete(i); }
  if (parityEven) for (const i of [...indices]) { if ((i + 1) % 2 === 1) indices.delete(i); }
  if (!_batchApplyPattern(indices)) return;
  if (!indices.size) { showToast('No valid pages in range'); return; }
  if (op === 'ocr') await _syncOcrSettings();

  const sorted = [...indices].sort((a, b) => a - b);

  // Set page status: bulk-write flags.status over the range (mark clutter,
  // bulk-verify a chapter, …). One server call; no annotations touched.
  if (op === 'set_status') {
    const stems  = sorted.map(i => pages[i]?.stem).filter(Boolean);
    const status = document.getElementById('batch-status-value').value;
    if (!stems.length) { showToast('No pages in range'); return; }
    if (!confirm(`Set status "${status}" on ${stems.length} page(s)?`)) return;
    try {
      const r = await fetch(`${API}/api/pages/status?folder=${encodeURIComponent(folder)}`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ stems, status }),
      });
      const d = await r.json();
      if (!r.ok) { showToast('✕ ' + (d.detail || r.status)); return; }
      showToast(`Set "${status}" on ${d.changed} page(s).`, 3000);
      if (pageData) { pageData.flags = pageData.flags || {};
        if (stems.includes(pages[pageIdx]?.stem)) { pageData.flags.status = status; _syncStatusChip?.(); } }
    } catch (e) { showToast('✕ ' + (e.message || e)); }
    return;
  }

  // Dataset diagnostics: server-side check ladder over the declared dataset,
  // read-only, opens the findings report. The declaration's own scope
  // (pattern, page range) governs page selection; the batch page-range field
  // is passed as an EXTRA restriction (the pattern/parity fields are not —
  // the declaration's pattern is authoritative).
  if (op === 'dataset_diagnose') {
    const name = document.getElementById('batch-dataset-name').value;
    await runDatasetReport(name, rawPages.trim() || null);
    return;
  }

  // Authority duplicate / unresolved / lookup report: read-only scan, opens the review table.
  if (op === 'auth_duplicates' || op === 'auth_unresolved' || op === 'auth_lookup') {
    const stems = sorted.map(i => pages[i]?.stem).filter(Boolean);
    if (!stems.length) { showToast('No pages in range'); return; }
    const opts = {unresolved: op === 'auth_unresolved'};
    if (op === 'auth_lookup') {
      opts.lookup = document.getElementById('batch-lookup-list').value
        .split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
      if (!opts.lookup.length) { showToast('Enter at least one entity ID or name'); return; }
    }
    await runDupReport(stems, document.getElementById('batch-col-filter').value.trim(), opts);
    return;
  }

  // Authority resolution runs server-side in one call (in-process matcher).
  if (op === 'resolve_authority') {
    const stems = sorted.map(i => pages[i]?.stem).filter(Boolean);
    if (!stems.length) { showToast('No pages in range'); return; }
    if (!confirm(`Resolve authorities on ${stems.length} page(s)?`)) return;
    await _batchTakeSnapshot(stems, 'resolve_authority');
    const payload = {
      stems,
      col_filter:  document.getElementById('batch-col-filter').value.trim() || null,
      name:        document.getElementById('batch-auth-file').value || null,
      type:        document.getElementById('batch-auth-type').value || null,
      layer:       document.getElementById('batch-auth-layer').value,
      overwrite:   document.getElementById('batch-auth-overwrite').checked,
      use_context: document.getElementById('batch-auth-context').checked,
    };
    const btn = document.getElementById('batch-run-btn');
    const prog = document.getElementById('batch-progress');
    const old = btn.textContent; btn.disabled = true; btn.textContent = '…';
    document.getElementById('batch-progress-bar-wrap').style.display = '';
    if (prog) prog.textContent = `Resolving across ${stems.length} page(s)…`;
    try {
      const r = await fetch(`${API}/api/authority/batch?folder=${encodeURIComponent(folder)}`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
      });
      if (!r.ok) { const m = await r.json().catch(() => ({})); showToast('Batch error: ' + (m.detail || r.status)); return; }
      const d = await r.json(); const t = d.totals || {};
      const summary = `${d.pages} page(s) · ${t.resolved||0} resolved`
        + (t.ditto ? `, ${t.ditto} ditto` : '') + (t.kept ? `, ${t.kept} kept` : '')
        + (t.nomatch ? `, ${t.nomatch} no match` : '') + (t.skipped ? `, ${t.skipped} skipped` : '');
      if (prog) prog.textContent = `✓ [${d.authority}] ${summary}`;
      showToast(`Authority batch: ${summary}`, 5000);
      if (pageData) { await reloadPageData(); updatePanel(); drawOverlay(); }
    } catch (e) { showToast('Batch error: ' + (e?.message || e)); }
    finally { btn.disabled = false; btn.textContent = old; }
    return;
  }

  // JSON export — gather structured records server-side; download file/zip.
  if (op === 'json_export') {
    const stems = sorted.map(i => pages[i]?.stem).filter(Boolean);
    if (!stems.length) { showToast('No pages in range'); return; }
    const modes = {};
    document.querySelectorAll('#batch-jsonexport-labels select.bje-mode')
      .forEach(s => { modes[s.dataset.label] = s.value; });
    localStorage.setItem('batchJsonExportModes', JSON.stringify(modes));
    if (!Object.values(modes).includes('export')) { showToast('Set at least one label to Export'); return; }
    const outMode = document.getElementById('batch-jsonexport-mode').value;
    const btn = document.getElementById('batch-run-btn');
    const prog = document.getElementById('batch-progress');
    const old = btn.textContent; btn.disabled = true; btn.textContent = '…';
    document.getElementById('batch-progress-bar-wrap').style.display = '';
    if (prog) prog.textContent = `Building JSON export from ${stems.length} page(s)…`;
    try {
      const r = await fetch(`${API}/api/export/json?folder=${encodeURIComponent(folder)}`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({stems, label_modes: modes, mode: outMode}),
      });
      if (!r.ok) { showToast('Export failed: ' + r.status); return; }
      const n = r.headers.get('X-EconAI-Records') || '?';
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = (outMode === 'per_annotation') ? 'structured_export.zip' : 'structured_export.json';
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      if (prog) prog.textContent = `✓ exported ${n} record(s)`;
      showToast(`JSON export: ${n} record(s)`, 4000);
    } catch (e) { showToast('Export error: ' + (e?.message || e)); }
    finally { btn.disabled = false; btn.textContent = old; }
    return;
  }

  if (op === 'clear') {
    if (!confirm(`Clear annotations on ${sorted.length} page(s)? This cannot be undone.`)) return;
  } else if (op === 'overlaps_lattice') {
    if (!confirm(`Run overlap removal + lattice correction on ${sorted.length} page(s)?`)) return;
  } else if (op === 'overlaps_lattice_snap_trim') {
    if (!confirm(`Run overlap removal + lattice correction + snap + overlap removal on ${sorted.length} page(s)?`)) return;
  } else if (op === 'ocr') {
    const eng = document.getElementById('batch-ocr-engine').value;
    const scp = document.getElementById('batch-ocr-scope').value;
    const scpDesc = scp === 'whole' ? 'whole annotation'
                  : scp === 'rows-detect' ? 'internal rows (re-detect)' : 'internal rows (keep structure)';
    if (!confirm(`Run ${eng} OCR — ${scpDesc} — on ${sorted.length} page(s)?`)) return;
  } else if (op === 'llm') {
    if (!document.getElementById('batch-llm-prompt').value.trim()) { showToast('Prompt is empty'); return; }
    const _bjson = document.getElementById('batch-llm-json').checked;
    if (_bjson && !document.getElementById('batch-llm-schema').value) { showToast('Pick a schema for JSON mode'); return; }
    if (!confirm(`Run ${_bjson ? 'JSON extraction' : 'LLM'} on ${sorted.length} page(s)?`)) return;
  } else if (op === 'llm_batchapi') {
    const _bjson = document.getElementById('batch-llm-json').checked;
    if (_bjson && !document.getElementById('batch-llm-schema').value) { showToast('Pick a schema for JSON mode'); return; }
    if (!document.getElementById('batch-llm-prompt').value.trim()) { showToast('Prompt is empty'); return; }
    const m = document.getElementById('batch-llm-model').value;
    if (m.includes(':') && !m.startsWith('azure:') && !m.startsWith('azure-us:')) {
      showToast('Overnight batches need an OpenAI or Azure model'); return;
    }
  } else if (op === 'score_delete') {
    const t = parseFloat(document.getElementById('batch-score-thresh').value);
    if (!confirm(`Delete all shapes with score < ${t} across ${sorted.length} page(s)? This cannot be undone.`)) return;
  } else if (op === 'clear_ocr') {
    if (!confirm(`Remove OCR results on ${sorted.length} page(s)?`)) return;
  } else if (op === 'clear_llm') {
    if (!confirm(`Remove LLM results on ${sorted.length} page(s)?`)) return;
  } else if (op === 'clear_llm_hallucinations') {
    const t  = parseInt(document.getElementById('batch-llm-halluc-thresh').value) || 2;
    const dt = parseInt(document.getElementById('batch-llm-halluc-digit-thresh').value) || 4;
    if (!confirm(`Clear LLM hallucinations on ${sorted.length} page(s)?\n• Row count ≥ ${t} more than OCR\n• OR any row is longer than ${dt} characters`)) return;
  } else if (op === 'strip_short_lines') {
    const field = document.getElementById('batch-strip-field').value;
    const fieldLabel = { ocr: 'OCR', llm: 'LLM', human: 'Human', pdf: 'PDF' }[field] || field;
    if (!confirm(`Strip short lines from ${fieldLabel} text on ${sorted.length} page(s)?`)) return;
  } else if (op === 'trim_overlaps') {
    if (!confirm(`Trim overlapping annotation pairs on ${sorted.length} page(s)?`)) return;
  }

  _batchRunning = true;
  _batchStop    = false;
  document.getElementById('batch-run-btn').textContent  = '■ Stop';
  document.getElementById('batch-cancel-btn').textContent = 'Close';
  document.getElementById('batch-progress-bar-wrap').style.display = '';
  const progText = document.getElementById('batch-progress');
  const progBar  = document.getElementById('batch-progress-bar');

  _batchVerbose = document.getElementById('batch-verbose').checked;
  const verboseEl = _batchLogEl();
  if (verboseEl) { verboseEl.textContent = ''; verboseEl.style.display = _batchVerbose ? 'block' : 'none'; }

  // Snapshot for one-step undo before anything writes (submissions and
  // exports don't modify pages — no snapshot needed for those).
  if (op !== 'json_export' && op !== 'llm_batchapi') {
    progText.textContent = 'Snapshotting pages for undo…';
    await _batchTakeSnapshot(sorted.map(i => pages[i]?.stem).filter(Boolean), op);
  }

  // Structural-blank ink scan — one server-side call (free, local, no API).
  if (op === 'mark_blanks') {
    const stems = sorted.map(i => pages[i]?.stem).filter(Boolean);
    if (!stems.length) { showToast('No pages in range'); return; }
    progText.textContent = `Ink-scanning ${stems.length} page(s)…`;
    try {
      const r = await fetch(`${API}/api/batch/mark_blanks?folder=${encodeURIComponent(folder)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stems,
          col_filter: document.getElementById('batch-col-filter').value.trim() || null }),
      });
      const dd = await r.json();
      if (!r.ok) throw new Error(dd.detail || r.status);
      const t = dd.totals || {};
      progText.style.color = '#4caf50';
      progText.textContent = `✓ ${dd.pages} page(s) · ${t.scanned} cell(s) scanned · `
        + `${t.cells_blank} blank cell(s), ${t.rows_blank} blank row(s) marked`
        + (t.cells_inked ? `, ${t.cells_inked} un-marked (ink returned)` : '');
      showToast(`Ink scan: ${t.cells_blank} blank cell(s) marked on ${dd.pages} page(s)`, 5000);
    } catch (e) { progText.style.color = '#ff9800'; progText.textContent = '✕ ' + (e.message || e); }
    _batchRunning = false;
    document.getElementById('batch-run-btn').textContent = 'Run';
    document.getElementById('batch-run-btn').disabled = false;
    document.getElementById('batch-cancel-btn').textContent = 'Cancel';
    if (pageData) { await reloadPageData(); drawOverlay(); updatePanel(); }
    return;
  }

  // Build internal row structure (detect / anchor-project) — one server call.
  if (op === 'convert_rows') {
    const stems = sorted.map(i => pages[i]?.stem).filter(Boolean);
    if (!stems.length) { showToast('No pages in range'); return; }
    const payload = {
      stems,
      col_filter: document.getElementById('batch-col-filter').value.trim() || null,
      anchor_pattern: document.getElementById('batch-rowsbuild-anchor').value.trim() || null,
      source: document.getElementById('batch-rowsbuild-source').value,
      cell_height: parseInt(document.getElementById('batch-rowsbuild-cellheight').value) || 26,
      overwrite: document.getElementById('batch-rowsbuild-overwrite').checked,
    };
    progText.textContent = `Building row structure on ${stems.length} page(s)…`;
    try {
      const r = await fetch(`${API}/api/rows/build?folder=${encodeURIComponent(folder)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const dd = await r.json();
      if (!r.ok) throw new Error(dd.detail || r.status);
      const t = dd.totals || {};
      progText.style.color = '#4caf50';
      progText.textContent = `✓ ${t.pages} page(s) · ${t.built} detected`
        + (t.projected ? `, ${t.projected} projected` : '')
        + (t.skipped ? `, ${t.skipped} skipped` : '')
        + (t.no_rows ? `, ${t.no_rows} no-rows` : '');
      showToast(`Row structure: ${t.built + t.projected} cell(s) built on ${t.pages} page(s)`, 5000);
    } catch (e) { progText.style.color = '#ff9800'; progText.textContent = '✕ ' + (e.message || e); }
    _batchRunning = false;
    document.getElementById('batch-run-btn').textContent = 'Run';
    document.getElementById('batch-run-btn').disabled = false;
    document.getElementById('batch-cancel-btn').textContent = 'Cancel';
    if (pageData) { await reloadPageData(); drawOverlay(); updatePanel(); }
    return;
  }

  // Overnight lane: package everything and hand it to the provider's batch
  // service (half price, done within 24h) — nothing runs in this browser.
  if (op === 'llm_batchapi') {
    const s = _collectLlmSettings();
    const tasks = await _collectLlmTargets(sorted, s, progText);
    const sendDesc = { image: 'image', ocr: 'OCR text', 'image+ocr': 'image + OCR' }[s.payload] || s.payload;
    const perRow = s.mode === 'linebyline';
    const scopeDesc = perRow
      ? `per internal row (${s.rows_source === 'detect' ? 're-detect rows' : 'keep structure'})`
      : '⚠ WHOLE cell — ONE request per cell; a multi-row cell returns a single value';
    let msg;
    if (!tasks.length) {
      msg = 'No matching cells — nothing submitted.';
    } else if (!confirm(
        `Overnight batch — ${tasks.length} cell(s), model ${s.model}\n`
      + `Send:  ${sendDesc}\n`
      + `Scope: ${scopeDesc}\n\n`
      + (perRow ? '' : 'If your cells have internal rows and you want each row read, cancel and set Scope to "Internal rows".\n\n')
      + `Proceed? (half price, done within 24h)`)) {
      msg = 'Submission cancelled.';
    } else {
      progText.textContent = `Building ${tasks.length} request(s)… (this can take a few minutes)`;
      try {
        const r = await fetch(`${API}/api/llm_batch/submit?folder=${encodeURIComponent(folder)}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            targets: tasks.map(t => ({ stem: t.stem, idx: t.si })),
            model: s.model, mode: s.mode, prompt: s.prompt,
            cell_height: s.cellHeight, use_shadow: s.useShadow,
            json_schema: s.schema, schema_name: s.schemaName,
            payload: s.payload, rows_source: s.rows_source,
          }),
        });
        if (!r.ok) { const m = await r.json().catch(() => ({})); throw new Error(m.detail || r.status); }
        // stream progress: building → uploading → chunk_done (×N) → done
        const reader = r.body.getReader(), dec = new TextDecoder();
        let buf = '', jobsMade = [], done = null, err = null;
        outer: while (true) {
          const { done: rd, value } = await reader.read();
          if (rd) break;
          buf += dec.decode(value, { stream: true });
          const chunks = buf.split('\n\n'); buf = chunks.pop();
          for (const c of chunks) {
            if (!c.startsWith('data: ')) continue;
            let ev; try { ev = JSON.parse(c.slice(6)); } catch { continue; }
            if (ev.type === 'building') {
              progText.textContent = `Building… ${ev.requests} request(s), ~${ev.mb} MB so far`;
              progBar.style.width = '40%';
            } else if (ev.type === 'uploading') {
              progText.textContent = `Uploading ${ev.requests} request(s) (${ev.mb} MB) to the provider…`;
              progBar.style.width = '70%';
            } else if (ev.type === 'chunk_done') {
              jobsMade.push(ev);
              showToast(`🌙 ${ev.id}: ${ev.requests} request(s), ${ev.mb} MB submitted`, 4000);
            } else if (ev.type === 'done') {
              done = ev;
            } else if (ev.type === 'error') {
              err = ev.error; break outer;
            }
          }
        }
        if (err) throw new Error(err);
        const nj = jobsMade.length;
        msg = `🌙 Submitted ${done?.requests ?? '?'} request(s) · ${done?.cells ?? '?'} cell(s) as `
            + `${nj} job(s): ${jobsMade.map(j => j.id).join(', ')}.`
            + (done?.skipped_bad_geometry
               ? ` ⚠ ${done.skipped_bad_geometry} cell(s) skipped — broken geometry (row bands / bbox outside the image).`
               : '')
            + ` You can close the browser.`;
        showToast(msg, 8000);
      } catch (e) { msg = `✕ Submit failed: ${e.message}`; showToast(msg, 8000); }
    }
    progText.style.color = msg.startsWith('✕') ? '#ff9800' : '#4caf50';
    progText.textContent = msg;
    progBar.style.width = '100%';
    _batchRunning = false;
    document.getElementById('batch-run-btn').textContent   = '🌙 Submit job';
    document.getElementById('batch-run-btn').disabled      = false;
    document.getElementById('batch-cancel-btn').textContent = 'Cancel';
    _refreshOvernightJobs();
    return;
  }

  // LLM runs through its own parallel path (N requests in flight at once —
  // the server's per-shape merge writes make same-page concurrency safe).
  if (op === 'llm') {
    const r = await _runBatchLlmParallel(sorted, progText, progBar);
    progBar.style.width  = '100%';
    progText.style.color = _batchStop ? '#ff9800' : '#4caf50';
    progText.textContent = _batchStop
      ? `Stopped after ${r.done}/${r.total} cell(s).`
      : `Done — ${r.done} cell(s)` + (r.errors ? `, ${r.errors} error(s)` : '') + '.';
    _batchRunning = false;
    document.getElementById('batch-run-btn').textContent   = 'Run';
    document.getElementById('batch-run-btn').disabled      = false;
    document.getElementById('batch-cancel-btn').textContent = 'Cancel';
    await reloadPageData(); refreshDiag(); drawOverlay(); updatePanel();
    return;
  }

  let done = 0;
  for (const idx of sorted) {
    if (_batchStop) break;
    const stem   = pages[idx].stem;
    const params = new URLSearchParams({ folder, stem });
    progText.textContent = `Page ${idx + 1} / ${pages.length} — ${stem}`;
    progBar.style.width  = `${Math.round((done / sorted.length) * 100)}%`;

    if (op === 'clear') {
      await fetch(`${API}/api/page/shapes?${params}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shapes: [] }),
      });

    } else if (op === 'overlaps_lattice') {
      // Load page
      const res  = await fetch(`${API}/api/page?${params}`);
      const data = await res.json();
      let shapes = data.shapes || [];

      // Step 1: remove overlaps
      const threshold = parseFloat(document.getElementById('batch-overlap-thresh').value) / 100 || 0.5;
      const toDelete  = _computeOverlapsOnShapes(shapes, threshold);
      shapes = shapes.filter((_, i) => !toDelete.has(i));

      // Step 2: lattice detection
      const checks = document.querySelectorAll('#batch-label-checks input[type=checkbox]');
      const remembered = {};
      const selectedLabels = [];
      checks.forEach(cb => { remembered[cb.value] = cb.checked; if (cb.checked) selectedLabels.push(cb.value); });
      localStorage.setItem('latticeLabels', JSON.stringify(remembered));

      if (selectedLabels.length) {
        // Temporarily swap pageData so _latticeDetect operates on loaded shapes
        const savedPageData = pageData;
        pageData = { shapes };
        _latticeDetect(selectedLabels);
        shapes = pageData.shapes;
        pageData = savedPageData;
      }

      // Save
      await fetch(`${API}/api/page/shapes?${params}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shapes }),
      });

    } else if (op === 'overlaps_lattice_snap_trim') {
      // Load page
      const res  = await fetch(`${API}/api/page?${params}`);
      const data = await res.json();
      let shapes = data.shapes || [];

      // Step 1: remove overlaps
      const threshold = parseFloat(document.getElementById('batch-overlap-thresh').value) / 100 || 0.5;
      const toDelete  = _computeOverlapsOnShapes(shapes, threshold);
      shapes = shapes.filter((_, i) => !toDelete.has(i));

      // Step 2: lattice detection
      const checks = document.querySelectorAll('#batch-label-checks input[type=checkbox]');
      const remembered = {};
      const selectedLabels = [];
      checks.forEach(cb => { remembered[cb.value] = cb.checked; if (cb.checked) selectedLabels.push(cb.value); });
      localStorage.setItem('latticeLabels', JSON.stringify(remembered));

      if (selectedLabels.length) {
        const savedPageData = pageData;
        pageData = { shapes };
        _latticeDetect(selectedLabels);
        shapes = pageData.shapes;
        pageData = savedPageData;
      }

      // Step 3: snap to grid
      _snapShapesToGrid(shapes);

      // Step 4: remove overlaps again
      const toDelete2 = _computeOverlapsOnShapes(shapes, threshold);
      shapes = shapes.filter((_, i) => !toDelete2.has(i));

      // Save
      await fetch(`${API}/api/page/shapes?${params}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shapes }),
      });

    } else if (op === 'ocr') {
      const checks    = document.querySelectorAll('#batch-ocr-label-checks input[type=checkbox]');
      const remembered = {};
      const selLabels  = [];
      checks.forEach(cb => { remembered[cb.value] = cb.checked; if (cb.checked) selLabels.push(cb.value); });
      localStorage.setItem('batchOcrLabels', JSON.stringify(remembered));

      const engine     = document.getElementById('batch-ocr-engine').value;
      const scope      = document.getElementById('batch-ocr-scope').value;
      const overwrite  = document.getElementById('batch-ocr-overwrite').checked;
      const cellHeight = parseInt(document.getElementById('batch-ocr-cellheight').value) || 26;
      const labelSet   = new Set(selLabels);
      const resultField = engine === 'tesseract' ? 'tesseract_output' : 'easyocr_output';

      const res    = await fetch(`${API}/api/page?${params}`);
      const pdata  = await res.json();
      const shapes = pdata.shapes || [];
      const condFilter = _computeConditionFilter(shapes);
      const colFilter  = _computeColumnFilter(shapes);
      let shapeDone = 0;

      for (let si = 0; si < shapes.length; si++) {
        if (_batchStop) break;
        const sh = shapes[si];
        if (!labelSet.has(sh.label)) continue;
        if (condFilter !== null && !condFilter.has(si)) continue;
        if (colFilter  !== null && !colFilter.has(si))  continue;
        if (!overwrite && sh[resultField]?.ocr_text) continue;
        progText.textContent = `Page ${idx + 1}/${pages.length} — shape ${si + 1}/${shapes.length} (${sh.label})${condFilter ? ` [${condFilter.size} flagged]` : ''}${colFilter ? ` [col filter]` : ''}`;
        batchLog(`[${stem}] shape ${si} (${sh.label}) row=${sh.super_row} col=${sh.super_column}`);
        try {
          await _batchOcrShape(stem, si, engine, scope, cellHeight);
          shapeDone++;
        } catch(e) { if (_batchStop) break; batchLog(`  ✕ error: ${e.message}`); }
      }

    } else if (op === 'score_delete') {
      const thresh = parseFloat(document.getElementById('batch-score-thresh').value);
      const checks = document.querySelectorAll('#batch-score-label-checks input[type=checkbox]');
      const remembered = {};
      const selLabels  = [];
      checks.forEach(cb => { remembered[cb.value] = cb.checked; if (cb.checked) selLabels.push(cb.value); });
      localStorage.setItem('batchScoreLabels', JSON.stringify(remembered));
      const filterByLabel = selLabels.length > 0;
      const labelSet = new Set(selLabels);

      const res    = await fetch(`${API}/api/page?${params}`);
      const pdata  = await res.json();
      const before = pdata.shapes || [];
      const colFilter = _computeColumnFilter(before);
      const after  = before.filter((s, i) => {
        if (filterByLabel && !labelSet.has(s.label)) return true; // keep if not in filter
        if (colFilter !== null && !colFilter.has(i)) return true; // keep if outside column filter
        return (s.score == null) || (s.score >= thresh);
      });
      const deleted = before.length - after.length;
      if (deleted > 0) {
        await fetch(`${API}/api/page/shapes?${params}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ shapes: after }),
        });
      }
      progText.textContent = `Page ${idx + 1}/${pages.length} — removed ${deleted} shape(s)`;

    } else if (op === 'clear_ocr') {
      const checks = document.querySelectorAll('#batch-clear-ocr-label-checks input[type=checkbox]');
      const remembered = {}; const selLabels = [];
      checks.forEach(cb => { remembered[cb.value] = cb.checked; if (cb.checked) selLabels.push(cb.value); });
      localStorage.setItem('batchClearOcrLabels', JSON.stringify(remembered));
      const labelSet = new Set(selLabels);

      const res = await fetch(`${API}/api/page?${params}`);
      const pdata = await res.json();
      const shapes = pdata.shapes || [];
      const colFilter = _computeColumnFilter(shapes);
      let n = 0;
      shapes.forEach((s, i) => {
        if (!labelSet.has(s.label)) return;
        if (colFilter !== null && !colFilter.has(i)) return;
        if (s.tesseract_output) { s.tesseract_output = null; _rsClearLayer(s, 'ocr'); n++; }
      });
      if (n > 0) {
        await fetch(`${API}/api/page/shapes?${params}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ shapes }),
        });
      }
      progText.textContent = `Page ${idx + 1}/${pages.length} — cleared OCR from ${n} shape(s)`;

    } else if (op === 'clear_llm') {
      const checks = document.querySelectorAll('#batch-clear-llm-label-checks input[type=checkbox]');
      const remembered = {}; const selLabels = [];
      checks.forEach(cb => { remembered[cb.value] = cb.checked; if (cb.checked) selLabels.push(cb.value); });
      localStorage.setItem('batchClearLlmLabels', JSON.stringify(remembered));
      const labelSet = new Set(selLabels);

      const res = await fetch(`${API}/api/page?${params}`);
      const pdata = await res.json();
      const shapes = pdata.shapes || [];
      const colFilter = _computeColumnFilter(shapes);
      let n = 0;
      shapes.forEach((s, i) => {
        if (!labelSet.has(s.label)) return;
        if (colFilter !== null && !colFilter.has(i)) return;
        if (s.openai_output) { s.openai_output = null; _rsClearLayer(s, 'llm'); n++; }
      });
      if (n > 0) {
        await fetch(`${API}/api/page/shapes?${params}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ shapes }),
        });
      }
      progText.textContent = `Page ${idx + 1}/${pages.length} — cleared LLM from ${n} shape(s)`;

    } else if (op === 'clear_llm_hallucinations') {
      const thresh      = parseInt(document.getElementById('batch-llm-halluc-thresh').value) || 2;
      const digitThresh = parseInt(document.getElementById('batch-llm-halluc-digit-thresh').value) || 4;
      const checks  = document.querySelectorAll('#batch-llm-halluc-label-checks input[type=checkbox]');
      const remembered = {}; const selLabels = [];
      checks.forEach(cb => { remembered[cb.value] = cb.checked; if (cb.checked) selLabels.push(cb.value); });
      localStorage.setItem('batchLlmHallucLabels', JSON.stringify(remembered));
      const labelSet      = new Set(selLabels);
      const filterByLabel = labelSet.size > 0;

      const res   = await fetch(`${API}/api/page?${params}`);
      const pdata = await res.json();
      const shps  = pdata.shapes || [];
      const cf    = _computeColumnFilter(shps);
      let n = 0;
      shps.forEach((s, i) => {
        if (filterByLabel && !labelSet.has(s.label)) return;
        if (cf !== null && !cf.has(i)) return;
        if (!s.openai_output?.response) return;
        const llmLines   = _lineCount(s.openai_output.response);
        const ocrLines   = _lineCount(s.tesseract_output?.ocr_text);
        const wideNumber = s.openai_output.response.split('\n').some(line => line.length > digitThresh);
        if (llmLines - ocrLines >= thresh || wideNumber) { s.openai_output = null; _rsClearLayer(s, 'llm'); n++; }
      });
      if (n > 0) {
        await fetch(`${API}/api/page/shapes?${params}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ shapes: shps }),
        });
      }
      progText.textContent = `Page ${idx + 1}/${pages.length} — cleared LLM from ${n} shape(s)`;

    } else if (op === 'pdf_text_layer') {
      const r2 = await fetch(`/api/page/pdf-text-layer?${params}`, { method: 'POST' });
      if (r2.ok) {
        const data = await r2.json();
        if (idx === pageIdx) {
          data.shapes.forEach((s, si) => {
            if (pageData.shapes[si] !== undefined) pageData.shapes[si].pdf_text = s.pdf_text;
          });
          updatePanel();
        }
        progText.textContent = `Page ${idx + 1}/${pages.length} — extracted ${data.updated} shape(s)`;
      } else {
        progText.textContent = `Page ${idx + 1}/${pages.length} — no PDF source (skipped)`;
      }

    } else if (op === 'strip_short_lines') {
      const field     = document.getElementById('batch-strip-field').value;
      const res       = await fetch(`${API}/api/page?${params}`);
      const pdata     = await res.json();
      const shapes    = pdata.shapes || [];
      const colFilter = _computeColumnFilter(shapes);
      let changed = 0;
      shapes.forEach((s, i) => {
        if (colFilter !== null && !colFilter.has(i)) return;
        const orig = _getTextField(s, field);
        if (orig == null) return;
        const cleaned = _stripShortLines(orig);
        if (cleaned !== orig) { _setTextField(s, field, cleaned); changed++; }
      });
      if (changed > 0) {
        await fetch(`${API}/api/page/shapes?${params}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ shapes }),
        });
      }
      progText.textContent = `Page ${idx + 1}/${pages.length} — cleaned ${changed} shape(s)`;

    } else if (op === 'trim_overlaps') {
      const res = await fetch(`${API}/api/page?${params}`);
      const pdata = await res.json();
      const shapes = pdata.shapes || [];
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
      if (trimmed > 0) {
        rects.forEach((r, i) => { shapes[i].points = [[r.x1, r.y1], [r.x2, r.y2]]; });
        await fetch(`${API}/api/page/shapes?${params}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ shapes }),
        });
      }
      progText.textContent = `Page ${idx + 1}/${pages.length} — trimmed ${trimmed} overlapping pair(s)`;

    }

    done++;
  }

  progBar.style.width  = '100%';
  progText.style.color = _batchStop ? '#ff9800' : '#4caf50';
  progText.textContent = _batchStop
    ? `Stopped after ${done} page(s).`
    : `Done — ${done} page(s) processed.`;

  _batchRunning = false;
  document.getElementById('batch-run-btn').textContent   = 'Run';
  document.getElementById('batch-run-btn').disabled      = false;
  document.getElementById('batch-cancel-btn').textContent = 'Cancel';

  // Refresh current page view
  await reloadPageData(); refreshDiag(); drawOverlay(); updatePanel();
  flaggedOverlaps = new Set();
}

async function _drainSSE(response) {
  const reader = response.body.getReader();
  const dec    = new TextDecoder();
  let   buf    = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    if (_batchStop) { reader.cancel(); break; }
  }
}

// ── Verbose batch logging ─────────────────────────────────────────────────────
let _batchVerbose = false;
const _batchLogEl = () => document.getElementById('batch-verbose-log');

function batchLog(msg) {
  if (!_batchVerbose) return;
  const el = _batchLogEl();
  if (!el) return;
  el.textContent += msg + '\n';
  el.scrollTop = el.scrollHeight;
}

async function _drainSSEVerbose(response, onMsg) {
  /** Drain an SSE response, calling onMsg(parsedObject) for each event. */
  const reader  = response.body.getReader();
  const dec     = new TextDecoder();
  let   buf     = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const chunks = buf.split('\n\n'); buf = chunks.pop();
    for (const chunk of chunks) {
      if (!chunk.startsWith('data: ')) continue;
      try { onMsg(JSON.parse(chunk.slice(6))); } catch {}
    }
    if (_batchStop) { reader.cancel(); break; }
  }
}

function _collectLlmSettings() {
  const checks = document.querySelectorAll('#batch-llm-label-checks input[type=checkbox]');
  const remembered = {};
  const selLabels  = [];
  checks.forEach(cb => { remembered[cb.value] = cb.checked; if (cb.checked) selLabels.push(cb.value); });
  localStorage.setItem('batchLlmLabels', JSON.stringify(remembered));

  const s = {
    model:      document.getElementById('batch-llm-model').value,
    mode:       document.getElementById('batch-llm-mode').value,
    ..._batchRowParams(),                    // payload + rows_source
    prompt:     document.getElementById('batch-llm-prompt').value.trim(),
    cellHeight: parseInt(document.getElementById('batch-llm-cellheight').value) || 26,
    useShadow:      document.getElementById('batch-llm-use-shadow').checked,
    overwrite:      document.getElementById('batch-llm-overwrite').checked,
    requireOcrNums: document.getElementById('batch-llm-require-ocr-numbers').checked,
    labelSet:       new Set(selLabels),
    jsonOn:  document.getElementById('batch-llm-json').checked,
    schema:  null, schemaName: null,
  };
  if (s.jsonOn) {
    s.schemaName = document.getElementById('batch-llm-schema').value || null;
    const sch = _projSchemas.find(x => x.name === s.schemaName);
    s.schema = sch ? sch.schema : null;   // existence validated in the confirm step
  }
  return s;
}

async function _collectLlmTargets(sorted, s, progText, stats = null) {
  const tally = k => { if (stats) stats[k] = (stats[k] || 0) + 1; };
  if (document.getElementById('batch-condition').value.startsWith('rows_llm_empty') && !s.overwrite) {
    showToast('⚠ "Internal rows have NO LLM" targets cells whose FLAT LLM exists — '
              + 'check Overwrite, otherwise they are all skipped', 7000);
  }
  const tasks = [];
  for (const idx of sorted) {
    if (_batchStop) break;
    const stem = pages[idx].stem;
    progText.textContent = `Scanning page ${idx + 1}/${pages.length} — ${stem}`;
    const res    = await fetch(`${API}/api/page?${new URLSearchParams({ folder, stem })}`);
    const shapes = (await res.json()).shapes || [];
    const condFilter = _computeConditionFilter(shapes);
    const colFilter  = _computeColumnFilter(shapes);
    for (let si = 0; si < shapes.length; si++) {
      const sh = shapes[si];
      if (!s.labelSet.has(sh.label)) { tally('label not selected'); continue; }
      if (condFilter !== null && !condFilter.has(si)) { tally('condition filter'); continue; }
      if (colFilter  !== null && !colFilter.has(si))  { tally('column filter'); continue; }
      if (!s.overwrite && (s.jsonOn ? sh.structured : sh.openai_output?.response)) {
        tally('already has a result (Overwrite off)'); continue;
      }
      const ocrTxt = sh.tesseract_output?.ocr_text || sh.easyocr_output?.ocr_text
        || ((sh.row_struct?.rows || []).map(r => r.ocr || '').join('\n'));
      if (s.requireOcrNums && !/\d/.test(ocrTxt)) { tally('no digits in OCR'); continue; }
      tasks.push({ stem, si, label: sh.label, row: sh.super_row, col: sh.super_column });
    }
  }
  return tasks;
}

async function _runBatchLlmParallel(sorted, progText, progBar) {
  const s = _collectLlmSettings();
  const { model, mode, prompt, cellHeight, useShadow, schema, schemaName } = s;
  const conc = Math.max(1, Math.min(16,
    parseInt(document.getElementById('batch-llm-parallel')?.value) || 6));
  localStorage.setItem('batchLlmParallel', String(conc));

  // Phase 1 — collect every (page, shape) target across the selected pages
  const tasks = await _collectLlmTargets(sorted, s, progText);
  if (!tasks.length) return { done: 0, total: 0, errors: 0 };
  batchLog(`${tasks.length} cell(s) to process, ${conc} in parallel`);

  // Phase 2 — worker pool: `conc` requests in flight until the queue drains
  let done = 0, errors = 0, next = 0;
  const worker = async () => {
    while (!_batchStop) {
      const t = tasks[next++];
      if (!t) return;
      batchLog(`[${t.stem}] shape ${t.si} (${t.label}) row=${t.row} col=${t.col}`);
      try {
        await _batchLlmShape(t.stem, t.si, model, mode, prompt, cellHeight, useShadow, schema, schemaName);
      } catch (e) {
        errors++;
        batchLog(`  ✕ [${t.stem}#${t.si}] ${e.message}`);
      }
      done++;
      progText.textContent = `LLM ${done}/${tasks.length} cell(s)`
        + (errors ? ` · ${errors} error(s)` : '') + ` · ${conc} in flight`;
      progBar.style.width = `${Math.round((done / tasks.length) * 100)}%`;
    }
  };
  await Promise.all(Array.from({ length: Math.min(conc, tasks.length) }, worker));
  return { done, total: tasks.length, errors };
}

async function _batchLlmShape(stem, idx, model, mode, prompt, cellHeight, useShadow, schema = null, schemaName = null) {
  // JSON / structured mode — whole-annotation (one record per shape)
  if (schema) {
    const m = (mode === 'linebyline' || mode === 'anchored') ? 'image' : mode;
    const p = new URLSearchParams({ folder, stem, idx, model, mode: m, use_shadow: !!useShadow });
    const r = await fetch(`${API}/api/page/shape/llm?${p}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, json_schema: schema, schema_name: schemaName }),
    });
    if (!r.ok) throw new Error(r.status);
    if (_batchVerbose) {
      const data = await r.json().catch(() => ({}));
      batchLog(`  ↳ ${JSON.stringify(data.structured || {}).slice(0, 200)}`);
    }
    return;
  }
  const params = new URLSearchParams({ folder, stem, idx, model, mode, use_shadow: !!useShadow });
  if (mode === 'linebyline') {
    params.set('cell_height', cellHeight);
    const rp = _batchRowParams();
    params.set('payload', rp.payload);
    params.set('rows_source', rp.rows_source);
    const r = await fetch(`${API}/api/page/shape/llm/linebyline?${params}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    });
    if (!r.ok) throw new Error(r.status);
    if (_batchVerbose) {
      await _drainSSEVerbose(r, msg => {
        if (msg.type === 'row_result') batchLog(`    row ${msg.row + 1}: ${msg.text}`);
        if (msg.type === 'done')       batchLog(`  ↳ ${msg.response.replace(/\n/g, ' | ')}`);
      });
    } else {
      await _drainSSE(r);
    }
  } else {
    const r = await fetch(`${API}/api/page/shape/llm?${params}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    });
    if (!r.ok) throw new Error(r.status);
    if (_batchVerbose) {
      const data = await r.json().catch(() => ({}));
      batchLog(`  ↳ ${(data.response || '').replace(/\n/g, ' | ')}`);
    }
  }
}

async function _batchOcrShape(stem, idx, engine, scope, cellHeight) {
  const params = new URLSearchParams({ folder, stem, idx });
  const perRow = scope === 'rows-keep' || scope === 'rows-detect';
  if (!perRow) {
    // whole annotation
    const url = engine === 'tesseract'
      ? `${API}/api/page/shape/ocr?${params}`
      : `${API}/api/page/shape/ocr/easyocr?${params}`;
    const r = await fetch(url, { method: 'POST' });
    if (!r.ok) { const _m = await r.json().catch(() => ({})); throw new Error(_m.detail || r.status); }
    if (_batchVerbose) {
      const data = await r.json().catch(() => ({}));
      batchLog(`  ↳ ${(data.ocr_text || '').replace(/\n/g, ' | ')}`);
    }
    return;
  }
  // per internal row
  params.set('cell_height', cellHeight);
  params.set('rows_source', scope === 'rows-detect' ? 'detect' : 'existing');
  const url = engine === 'tesseract'
    ? `${API}/api/page/shape/ocr/linebyline?${params}`
    : `${API}/api/page/shape/ocr/easyocr/linebyline?${params}`;
  const r = await fetch(url, { method: 'POST' });
  if (!r.ok) { const _m = await r.json().catch(() => ({})); throw new Error(_m.detail || r.status); }
  if (_batchVerbose) {
    await _drainSSEVerbose(r, msg => {
      if (msg.type === 'error')      batchLog(`    ✕ ${msg.error}`);
      if (msg.type === 'row_result') batchLog(`    row ${msg.row + 1}: ${msg.text}`);
      if (msg.type === 'done')       batchLog(`  ↳ ${(msg.ocr_text || '').replace(/\n/g, ' | ')}`);
    });
  } else {
    await _drainSSE(r);
  }
}


// ── Overnight (Batch API) jobs list ──────────────────────────────────────────
async function _refreshOvernightJobs() {
  const box = document.getElementById('batch-overnight-jobs');
  if (!box) return;
  box.innerHTML = '<div style="color:#555;font-size:11px;">Loading…</div>';
  try {
    const r = await fetch(`${API}/api/llm_batch/jobs?folder=${encodeURIComponent(folder)}`);
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    const jobs = (d.jobs || []).slice().reverse();   // newest first
    if (!jobs.length) {
      box.innerHTML = '<div style="color:#555;font-size:11px;">No overnight jobs yet for this project.</div>';
      return;
    }
    const chip = st => {
      const c = { completed: '#22c55e', applied: '#22c55e', failed: '#e94560',
                  cancelled: '#e94560', expired: '#e94560' }[st] || '#f0c040';
      return `<span style="color:${c};font-weight:700;">${_escHtml(st)}</span>`;
    };
    box.innerHTML = jobs.map(j => {
      const when = (j.submitted || '').replace('T', ' ').slice(0, 16);
      const counts = j.counts ? ` · ${j.counts.completed}/${j.counts.total} done`
        + (j.counts.failed ? `, ${j.counts.failed} failed` : '') : '';
      const applied = j.status === 'applied'
        ? ` · ✓ ${j.applied_cells} cell(s) written` + (j.failed_requests ? `, ${j.failed_requests} request(s) failed` : '')
        : '';
      const btns = [];
      if (j.status === 'completed')
        btns.push(`<button onclick="_ovApply('${j.id}', this)" style="background:#1b4d2e;border:1px solid #2e7d4f;color:#e0e0e0;border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer;">⬇ Apply results</button>`);
      const running = ['validating', 'in_progress', 'finalizing', 'queued'].includes(j.status);
      if (running)
        btns.push(`<button onclick="_ovCancel('${j.id}', this)" style="background:#5c1f2e;border:1px solid #8e2f47;color:#e0e0e0;border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer;">✕ Cancel</button>`);
      else
        btns.push(`<button onclick="_ovRemove('${j.id}', this)" title="Remove this job from the list (doesn't touch the provider)" style="background:#1a2740;border:1px solid #3a4a6a;color:#8a94a6;border-radius:4px;padding:2px 7px;font-size:11px;cursor:pointer;">🗑</button>`);
      return `<div style="display:flex;align-items:center;gap:8px;background:#091530;border:1px solid #0f3460;border-radius:5px;padding:5px 8px;font-size:11px;color:#aaa;">
        <span style="flex:none;color:#e0e0e0;font-weight:700;">${_escHtml(j.id)}</span>
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
          ${when} · ${_escHtml(j.model)} · ${_escHtml(j.mode)}${j.json ? ' · JSON' : ''} · ${j.n_cells} cell(s)${counts}${applied}
          ${j.status_note ? ` · <span style=\"color:#ff9800;\">${_escHtml(j.status_note)}</span>` : ''}
        </span>
        ${chip(j.status)} ${btns.join(' ')}
      </div>`;
    }).join('');
  } catch (e) {
    box.innerHTML = `<div style="color:#e94560;font-size:11px;">✕ ${_escHtml(e.message || String(e))}</div>`;
  }
}

async function _ovApply(jobId, btn) {
  if (!confirm(`Write ${jobId}'s results into the pages? Existing human edits are kept.`)) return;
  btn.disabled = true; btn.textContent = '…';
  try {
    const r = await fetch(`${API}/api/llm_batch/apply?folder=${encodeURIComponent(folder)}&job=${encodeURIComponent(jobId)}`,
                          { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    showToast(`⬇ ${jobId}: ${d.applied_cells} cell(s) written`
      + (d.failed_requests ? `, ${d.failed_requests} request(s) failed` : '')
      + (d.bad_json ? `, ${d.bad_json} invalid JSON` : ''), 7000);
    if (pageData) { await reloadPageData(); updatePanel(); drawOverlay(); }
  } catch (e) { showToast('Apply failed: ' + (e.message || e), 7000); }
  _refreshOvernightJobs();
}

async function _ovCancel(jobId, btn) {
  if (!confirm(`Cancel ${jobId}? Requests already completed are still billed and can be applied.`)) return;
  btn.disabled = true; btn.textContent = '…';
  try {
    const r = await fetch(`${API}/api/llm_batch/cancel?folder=${encodeURIComponent(folder)}&job=${encodeURIComponent(jobId)}`,
                          { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    showToast(`✕ ${jobId} → ${d.status}`);
  } catch (e) { showToast('Cancel failed: ' + (e.message || e), 7000); }
  _refreshOvernightJobs();
}

async function _ovRemove(jobId, btn) {
  btn.disabled = true;
  try {
    const r = await fetch(`${API}/api/llm_batch/remove?folder=${encodeURIComponent(folder)}&job=${encodeURIComponent(jobId)}`,
                          { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
  } catch (e) { showToast('Remove failed: ' + (e.message || e), 6000); }
  _refreshOvernightJobs();
}

async function _ovClearFinished() {
  if (!confirm('Remove all finished jobs (applied / failed / cancelled / expired) from the list?')) return;
  try {
    const r = await fetch(`${API}/api/llm_batch/remove?folder=${encodeURIComponent(folder)}&finished=1`,
                          { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    showToast(`Removed ${d.removed} finished job(s)`);
  } catch (e) { showToast('Clear failed: ' + (e.message || e), 6000); }
  _refreshOvernightJobs();
}

// ── Batch presets (recipes): every setting of this dialog, saved by name ────
const _LABEL_CONTAINERS = ['batch-label-checks', 'batch-ocr-label-checks',
  'batch-llm-label-checks', 'batch-score-label-checks',
  'batch-clear-ocr-label-checks', 'batch-clear-llm-label-checks',
  'batch-llm-halluc-label-checks'];

function _batchPresetsKey() { return 'batchPresets::' + folder; }
function _batchPresetsLoad() {
  try { return JSON.parse(localStorage.getItem(_batchPresetsKey()) || '{}'); }
  catch (e) { return {}; }
}
function _refreshBatchPresets(selected) {
  const sel = document.getElementById('batch-preset');
  if (!sel) return;
  const names = Object.keys(_batchPresetsLoad()).sort();
  sel.innerHTML = '<option value="">— recipes —</option>'
    + names.map(n => `<option value="${_escHtml(n)}"${n === selected ? ' selected' : ''}>${_escHtml(n)}</option>`).join('');
}

function _batchSettingsSnapshot() {
  const s = { inputs: {}, labels: {} };
  document.querySelectorAll('#batch-modal-body input[id], #batch-modal-body select[id], #batch-modal-body textarea[id]')
    .forEach(el => {
      if (el.id === 'batch-preset') return;
      s.inputs[el.id] = el.type === 'checkbox' ? { c: el.checked } : { v: el.value };
    });
  for (const cid of _LABEL_CONTAINERS) {
    s.labels[cid] = {};
    document.querySelectorAll(`#${cid} input[type=checkbox]`)
      .forEach(cb => { s.labels[cid][cb.value] = cb.checked; });
  }
  return s;
}

function _batchSettingsRestore(s) {
  for (const [id, v] of Object.entries(s.inputs || {})) {
    const el = document.getElementById(id);
    if (!el) continue;
    if ('c' in v) el.checked = v.c; else el.value = v.v;
  }
  for (const [cid, map] of Object.entries(s.labels || {})) {
    document.querySelectorAll(`#${cid} input[type=checkbox]`)
      .forEach(cb => { if (cb.value in map) cb.checked = map[cb.value]; });
  }
  onBatchOpChange();
  if (typeof onBatchLlmModeChange === 'function') onBatchLlmModeChange();
}

function saveBatchPreset() {
  const cur = document.getElementById('batch-preset').value;
  const name = prompt('Recipe name:', cur || '');
  if (!name || !name.trim()) return;
  const p = _batchPresetsLoad();
  p[name.trim()] = _batchSettingsSnapshot();
  localStorage.setItem(_batchPresetsKey(), JSON.stringify(p));
  _refreshBatchPresets(name.trim());
  showToast(`💾 Recipe "${name.trim()}" saved`);
}

function applyBatchPreset() {
  const name = document.getElementById('batch-preset').value;
  if (!name) return;
  const p = _batchPresetsLoad()[name];
  if (!p) return;
  _batchSettingsRestore(p);
  showToast(`Recipe "${name}" applied — check the page range, then Run`);
}

function deleteBatchPreset() {
  const name = document.getElementById('batch-preset').value;
  if (!name) { showToast('Pick a recipe to delete'); return; }
  if (!confirm(`Delete recipe "${name}"?`)) return;
  const p = _batchPresetsLoad();
  delete p[name];
  localStorage.setItem(_batchPresetsKey(), JSON.stringify(p));
  _refreshBatchPresets();
}

// ── Preview: count what a batch would touch, write nothing ──────────────────
function _batchSelectedIndices() {
  const indices = _parsePageRange(document.getElementById('batch-pages').value);
  if (!indices) return null;
  if (document.getElementById('batch-parity-odd').checked)
    for (const i of [...indices]) { if ((i + 1) % 2 === 0) indices.delete(i); }
  if (document.getElementById('batch-parity-even').checked)
    for (const i of [...indices]) { if ((i + 1) % 2 === 1) indices.delete(i); }
  if (!_batchApplyPattern(indices)) return null;
  return [...indices].sort((a, b) => a - b);
}

async function previewBatch() {
  const op = document.getElementById('batch-op').value;
  const sorted = _batchSelectedIndices();
  const progText = document.getElementById('batch-progress');
  if (!sorted || !sorted.size && !sorted.length) { progText.textContent = 'No pages in range.'; return; }
  progText.style.color = '';
  if (op === 'llm' || op === 'llm_batchapi') {
    const s = _collectLlmSettings();
    const stats = {};
    const tasks = await _collectLlmTargets(sorted, s, progText, stats);
    const skipped = Object.entries(stats).map(([k, v]) => `${v} × ${k}`).join(', ');
    progText.textContent = `👁 Would send ${tasks.length} cell(s) on ${sorted.length} page(s) to ${s.model}.`
      + (skipped ? ` Skipped: ${skipped}.` : '') + ' Nothing was changed.';
  } else if (op === 'ocr') {
    const labelSet = new Set([...document.querySelectorAll('#batch-ocr-label-checks input:checked')].map(cb => cb.value));
    const overwrite = document.getElementById('batch-ocr-overwrite').checked;
    const engine = document.getElementById('batch-ocr-engine').value;
    const resultField = engine === 'tesseract' ? 'tesseract_output' : 'easyocr_output';
    let n = 0;
    for (const idx of sorted) {
      const stem = pages[idx].stem;
      progText.textContent = `Scanning ${stem}…`;
      const res = await fetch(`${API}/api/page?${new URLSearchParams({ folder, stem })}`);
      const shapes = (await res.json()).shapes || [];
      const condFilter = _computeConditionFilter(shapes);
      const colFilter  = _computeColumnFilter(shapes);
      shapes.forEach((sh, si) => {
        if (!labelSet.has(sh.label)) return;
        if (condFilter !== null && !condFilter.has(si)) return;
        if (colFilter  !== null && !colFilter.has(si))  return;
        if (!overwrite && sh[resultField]?.ocr_text) return;
        n++;
      });
    }
    progText.textContent = `👁 Would ${engine} OCR ${n} cell(s) on ${sorted.length} page(s). Nothing was changed.`;
  } else {
    progText.textContent = `👁 Would run "${op}" on ${sorted.length} page(s). Nothing was changed.`;
  }
}

// ── One-step batch undo ──────────────────────────────────────────────────────
async function _batchTakeSnapshot(stems, op) {
  try {
    await fetch(`${API}/api/batch_snapshot?folder=${encodeURIComponent(folder)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stems, op }),
    });
  } catch (e) { /* snapshot failure must never block the batch */ }
}

async function undoLastBatch() {
  try {
    const r = await fetch(`${API}/api/batch_snapshot?folder=${encodeURIComponent(folder)}`);
    const m = (await r.json()).snapshot;
    if (!m) { showToast('No batch snapshot to restore'); return; }
    const when = (m.ts || '').replace('T', ' ').slice(0, 16);
    if (!confirm(`Restore ${m.pages} page(s) to their state before "${m.op}" (${when})?\nEverything done to them since — including manual edits — is rolled back.`)) return;
    const r2 = await fetch(`${API}/api/batch_snapshot/restore?folder=${encodeURIComponent(folder)}`, { method: 'POST' });
    const d = await r2.json();
    if (!r2.ok) throw new Error(d.detail || r2.status);
    showToast(`↩ Restored ${d.restored} page(s)`);
    if (pageData) { await reloadPageData(); refreshDiag(); drawOverlay(); updatePanel(); }
  } catch (e) { showToast('Undo failed: ' + (e.message || e), 6000); }
}
