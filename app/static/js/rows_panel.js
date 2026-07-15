// Split from index.html — classic scripts share the global scope;
// load order in index.html is load-bearing. See knowledge_base/02_architecture.md.
// ── Shape operations ─────────────────────────────────────────────────────────
async function patchShape(idx, body) {
  const params=new URLSearchParams({folder,stem:pages[pageIdx].stem,idx});
  try {
    const r = await fetch(`${API}/api/page/shape?${params}`,{
      method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body),
    });
    return r.ok;              // callers (e.g. review accept) gate on this
  } catch (e) { return false; }
}

// ── Internal row structure (row_struct) ──────────────────────────────────────
// Each shape may carry row_struct.rows = [{n, y0, y1, ocr, llm, human}, ...]
// with absolute page Y coordinates.  The flat text layers are kept in sync
// (server-side and mirrored locally) so everything downstream keeps working.

function _rsRows(shape) { return shape?.row_struct?.rows || null; }

// Clearing a flat layer must also clear it inside row_struct, otherwise the
// next sync would resurrect the cleared text from the rows
function _rsClearLayer(shape, layer) {
  const rows = _rsRows(shape);
  if (rows) rows.forEach(r => {
    r[layer] = '';
    if (layer === 'llm') delete r.llm_fixed;
  });
}

function _syncFlatLocal(shape) {
  // HUMAN only — flat OCR/LLM are the models' ORIGINAL outputs and must
  // survive row edits (they feed the ⟳ re-distribute buttons). Mirrors the
  // server's _sync_flat_from_rows default.
  const rows = _rsRows(shape); if (!rows?.length) return;
  const join = L => rows.map(r => r[L] || '').join('\n');
  const any  = L => rows.some(r => (r[L] || '').trim());
  if (any('human')) { (shape.human_output ??= {}).human_corrected_text = join('human'); }
}

function _rescaleRowStructLocal(shape, oldPts, newPts) {
  const rows = _rsRows(shape);
  if (!rows?.length || !oldPts || !newPts) return;
  const oy1=Math.min(oldPts[0][1],oldPts[1][1]), oy2=Math.max(oldPts[0][1],oldPts[1][1]);
  const ny1=Math.min(newPts[0][1],newPts[1][1]), ny2=Math.max(newPts[0][1],newPts[1][1]);
  const oh=oy2-oy1, nh=ny2-ny1;
  if (oh<=0 || nh<=0 || (oy1===ny1 && oy2===ny2)) return;
  rows.forEach(r => {
    r.y0 = ny1 + (r.y0-oy1)*nh/oh;
    r.y1 = ny1 + (r.y1-oy1)*nh/oh;
  });
}

// Bands in cell-crop pixel space (the crop served by /api/cell uses pad=4)
function _rsBandsRel(shape) {
  const rows = _rsRows(shape); if (!rows?.length) return null;
  const ys  = shape.points.map(p=>p[1]);
  const top = Math.max(0, Math.min(...ys) - 4);
  return rows.map(r => [r.y0 - top, r.y1 - top]);
}

// Serialize all page-file writes (PATCH rows / PUT shapes) so concurrent
// operations — e.g. rapid "Apply checked" clicks — can't race on the same
// JSON (read-modify-write lost updates). Each write waits for the previous.
let _writeChain = Promise.resolve();
function _serializeWrite(fn) {
  const run = _writeChain.then(fn, fn);
  _writeChain = run.then(() => {}, () => {});   // keep the chain alive on error
  return run;
}

// Returns true on a confirmed save, false otherwise.
async function saveRowStruct(idx, origin = null) {
  return _serializeWrite(async () => {
    const shape  = pageData.shapes[idx];
    if (!shape) return false;
    const rows   = _rsRows(shape) || [];
    const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx});
    let ok = false;
    try {
      const r = await fetch(`${API}/api/page/shape/rows?${params}`, {
        method:'PATCH', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({rows, origin}),
      });
      if (r.ok) {
        const data = await r.json();
        if (data.row_struct) shape.row_struct = data.row_struct;
        else delete shape.row_struct;
        _syncFlatLocal(shape);
        ok = true;
      } else {
        showToast(`Row save failed: ${r.status}`);
      }
    } catch (e) {
      showToast(`Row save failed: ${e?.message || e}`);
    }
    drawOverlay(); refreshDiag();
    return ok;
  });
}

// After a line-by-line / anchored run the server has written row_struct to
// disk — pull it into the local pageData copy.
async function _refreshShapeRowStruct(idx) {
  try {
    const p = pages[pageIdx];
    const r = await fetch(`${API}/api/page?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(p.stem)}`);
    const data = await r.json();
    if (data.shapes?.[idx]?.row_struct) pageData.shapes[idx].row_struct = data.shapes[idx].row_struct;
  } catch {}
  drawOverlay();
  if (idx === selIdx) updatePanel();
}

// ── Internal rows: table view + actions ─────────────────────────────────────
function renderRowTable(shape) {
  const fg   = document.getElementById('fg-rows');
  const wrap = document.getElementById('rows-table-wrap');
  const hint = document.getElementById('rows-hint');
  const meta = document.getElementById('rows-meta');
  const rows = _rsRows(shape);
  fg.style.display = 'flex';

  // Flat text boxes are replaced by the table when a row structure exists —
  // but remain reachable via the "whole-cell layers" toggle (users who edit /
  // pull the flat fields must not lose them just because rows exist).
  const hasRs = !!rows?.length;
  ['f-ocr', 'f-llm-result', 'human-input'].forEach(id => {
    const el = document.getElementById(id)?.closest('.lined-wrap');
    if (el) el.style.display = (hasRs && !_rsShowFlat) ? 'none' : '';
  });
  document.getElementById('rows-convert-btn').style.display = hasRs ? 'none' : '';
  document.getElementById('rows-remove-btn').style.display  = hasRs ? '' : 'none';

  if (!hasRs) {
    wrap.innerHTML = '';
    meta.textContent = '';
    const hasText = shape.human_output?.human_corrected_text
                 || shape.openai_output?.response
                 || shape.tesseract_output?.ocr_text;
    hint.textContent = hasText
      ? 'No internal rows yet — Convert splits the cell using the best text layer.'
      : 'No internal rows (cell has no text in any layer).';
    return;
  }

  meta.innerHTML = `${rows.length} row${rows.length > 1 ? 's' : ''} · ${shape.row_struct.origin || ''}`
    + ` &nbsp;<a href="#" style="color:#8a94a6;font-size:10px;" onclick="_rsToggleFlat(event)">`
    + `${_rsShowFlat ? '▾ hide' : '▸ show'} whole-cell layers</a>`;

  // A flat layer that disagrees with the table (e.g. a whole-cell LLM run
  // whose lines didn't make it into the rows) must stay visible — un-hide its
  // legacy text box and warn. Comparison is positional and whitespace-tolerant:
  // leading/interior empty lines are EMPTY ROWS, not noise (the flat Human is
  // the join of ALL rows), so a flat layer with fewer lines than rows only
  // matches when the missing tail rows are empty too. The message
  // distinguishes a COUNT mismatch from CONTENT divergence.
  const flatRaw = {
    'f-ocr':        shape.tesseract_output?.ocr_text || shape.easyocr_output?.ocr_text || '',
    'f-llm-result': shape.openai_output?.response || '',
    'human-input':  shape.human_output?.human_corrected_text || '',
  };
  const rowsOf = {'f-ocr': 'ocr', 'f-llm-result': 'llm', 'human-input': 'human'};
  let mismatches = [];
  Object.entries(flatRaw).forEach(([id, txt]) => {
    const L = rowsOf[id];
    const flatLines = _rsSplitFlat(txt);
    const same = !txt.trim() || (flatLines.length <= rows.length
      && rows.every((r, i) => (flatLines[i] ?? '').trim() === (r[L] || '').trim()));
    const mism = txt.trim() && !same;
    const el = document.getElementById(id)?.closest('.lined-wrap');
    if (el) el.style.display = (mism || _rsShowFlat) ? '' : 'none';
    if (mism) mismatches.push(
      (flatLines.length <= rows.length
        ? `${L.toUpperCase()} (content differs from the table)`
        : `${L.toUpperCase()} (${flatLines.length} lines vs ${rows.length} rows)`)
      + ` <button class="rs-copy-btn" onclick="_rsImportFlat('${L}')" `
      + `title="Force-fill the rows line-by-line from the flat text, keeping empty lines in place (extra lines are dropped)">⤓ import anyway</button>`);
  });

  // PDF layer: shown read-only next to the rows, capped (extracts can be huge)
  const pdfLines = _rsSplitFlat(shape.pdf_text);
  const pdfShown = Math.min(pdfLines.length, _rsPdfCap);
  const nRows    = Math.max(rows.length, pdfShown);

  hint.innerHTML =
    (mismatches.length
      ? `<span style="color:#fbbf24;">⚠ ${mismatches.join(', ')} — flat view shown in its section below.</span><br>` : '')
    + (pdfLines.length > _rsPdfCap
      ? `<span style="color:#fbbf24;">PDF has ${pdfLines.length} lines — showing first </span>`
        + `<input type="number" value="${_rsPdfCap}" min="1" style="width:46px;background:#16213e;border:1px solid #2a2a4a;`
        + `border-radius:3px;color:#eee;font-size:10px;padding:0 2px;" onchange="_rsSetPdfCap(this.value)"><br>` : '')
    + (editMode
      ? 'On the crop: hovering a divider turns it <span style="color:#f87171;">red</span> (cursor ↕) — drag moves it, '
        + 'double-click merges the two rows. Away from dividers (cursor ＋), double-click splits the band.'
      : '');

  // Column order follows data quality, worst → best: crop, PDF, OCR, LLM, Human
  const hasPdf = pdfLines.length > 0 || rows.some(r => (r.pdf || '').trim());
  let html = '<table class="rs-table"><tr><th>#</th><th></th>'
           + (hasPdf ? '<th>PDF <button class="rs-copy-btn" onclick="_rsCopyColPdf()" title="Copy whole PDF column to Human">⤓H</button>'
                     + '<button class="rs-copy-btn" onclick="_rsRefreshPdf()" title="Re-extract the PDF text layer per current row band">⟳</button></th>' : '')
           + '<th>OCR <button class="rs-copy-btn" onclick="_rsCopyCol(\'ocr\')" title="Copy whole OCR column to Human">⤓H</button>'
           + '<button class="rs-copy-btn" onclick="_rsImportFlat(\'ocr\')" title="Re-distribute the flat OCR text into the current row bands (top-aligned, one line per row)">⟳</button>'
           + '<button class="rs-copy-btn" onclick="_rsRefreshOcr()" title="Re-run EasyOCR row by row using the current row structure">🔍</button></th>'
           + '<th>LLM <button class="rs-copy-btn" onclick="_rsCopyCol(\'llm\')" title="Copy whole LLM column to Human">⤓H</button>'
           + '<button class="rs-copy-btn" onclick="_rsImportFlat(\'llm\')" title="Re-distribute the flat LLM text into the current row bands (top-aligned, one line per row)">⟳</button>'
           + `<button class="rs-copy-btn" id="rs-llm-refresh-btn" onclick="_rsRefreshLlm()" `
           + `title="${_rsLlmRunning ? 'Stop' : 'Re-run the LLM row by row using the current row structure and the active prompt'}">`
           + `${_rsLlmRunning ? '■' : '🔍'}</button></th>`
           + '<th>Human <button class="rs-copy-btn" onclick="_rsMajorityVote()" '
           + 'title="Fill Human with the majority opinion of PDF/OCR/LLM: one source → copy it; two → copy if they agree; three → copy if at least two agree. Rows without consensus are left unchanged.">⚖</button>'
           + '<button class="rs-copy-btn" onclick="_rsImportFlat(\'human\')" '
           + 'title="Re-distribute the saved Human text into the current row bands (top-aligned, one line per row). Use after changing the row structure.">⟳</button></th>'
           + '<th>Auth <button class="rs-copy-btn" onclick="_rsResolveCol()" '
           + 'title="Resolve every row against the authority using its best text (Human>LLM>OCR>PDF). Rows that are empty or a single character are skipped; manual picks are kept. Then: click a value to switch to an alternative, or double-click a cell to copy the entity from the row above (ditto marks).">🏛</button></th></tr>';
  for (let i = 0; i < nRows; i++) {
    const r   = rows[i];
    const pdf = (r?.pdf != null) ? r.pdf : (i < pdfShown ? pdfLines[i] : '');
    if (!r) {
      // Ghost row: PDF line beyond the row structure
      html += `<tr class="rs-ghost"><td class="rs-n">·</td><td class="rs-img"></td>`
            + `<td title="${_escHtml(pdf)}">${_escHtml(pdf)}</td>`
            + `<td></td><td></td><td></td><td></td></tr>`;
      continue;
    }
    const o = (r.ocr || '').trim(), l = (r.llm || '').trim(), h = (r.human || '').trim();
    const agree    = o && l && o === l && (!h || h === o);
    const conflict = (o && l && o !== l) || (h && ((o && h !== o) || (l && h !== l)));
    const cls = agree ? 'rs-ok' : conflict ? 'rs-bad' : '';
    html += `<tr class="${cls}" onmouseenter="_rsHover(${i})" onmouseleave="_rsHover(-1)">`
          + `<td class="rs-n">${r.n}</td>`
          + `<td class="rs-img"><canvas class="rs-crop" data-ri="${i}"></canvas></td>`
          + (hasPdf
              ? `<td class="rs-clk" onclick="_rsCopyPdf(${i})" title="${_escHtml(pdf)} — click to copy to Human">${_escHtml(pdf)}</td>` : '')
          + `<td class="rs-clk" onclick="_rsCopyCell(${i},'ocr')" title="${_escHtml(r.ocr || '')} — click to copy to Human">${_escHtml(r.ocr || '')}</td>`
          + `<td class="rs-clk" onclick="_rsCopyCell(${i},'llm')" title="${_escHtml(r.llm || '')}${r.llm_fixed ? ' (rule-fix corrected)' : ''} — click to copy to Human">`
          + `${_escHtml(r.llm || '')}${r.llm_fixed ? ' <span style="font-size:9px;" title="Corrected by rule-fix">🛠</span>' : ''}</td>`
          + `<td><input class="rs-human" value="${_escHtml(r.human || '')}" `
          + `onchange="_rsHumanEdit(${i}, this.value)"></td>`
          + `<td class="rs-auth rs-clk" onclick="_rsAuthClick(${i}, event)" ondblclick="_rsAuthDbl(${i}, event)" `
          + `title="Click for alternatives · double-click to copy from row above">${_rsAuthCellHtml(r)}</td></tr>`;
  }
  wrap.innerHTML = html + '</table>';
  _rsDrawCrops();
}

// Split a flat text into row-positional lines: leading/interior empty lines
// are kept (in a row-join they ARE the empty rows — stripping them shifted
// every value up so imports landed in the first N rows); only CRs and
// trailing blank lines (stray final newline) are dropped. Mirrors the
// server's _split_lines.
function _rsSplitFlat(text) {
  const ls = (text || '').split('\n').map(l => l.replace(/\r$/, ''));
  while (ls.length && !ls[ls.length - 1].trim()) ls.pop();
  return ls;
}

// Force-fill a layer's row values from its flat text, line i → row i with
// empty lines kept in place (used when the flat layer disagrees with the
// table and auto-distribute refuses)
async function _rsImportFlat(layer) {
  if (selIdx < 0) return;
  const shape = pageData.shapes[selIdx];
  const rows  = _rsRows(shape); if (!rows) return;
  const flatText = layer === 'ocr'
                 ? (shape.tesseract_output?.ocr_text || shape.easyocr_output?.ocr_text || '')
                 : layer === 'llm'
                 ? (shape.openai_output?.response || '')
                 : (shape.human_output?.human_corrected_text || '');
  const lines = _rsSplitFlat(flatText);
  const filled = lines.slice(0, rows.length).filter(l => l.trim()).length;
  rows.forEach((r, i) => { r[layer] = lines[i] ?? ''; });
  await saveRowStruct(selIdx);
  renderRowTable(shape);
  showToast(`${layer.toUpperCase()} imported: ${filled} non-empty line${filled !== 1 ? 's' : ''} placed`
            + (lines.length > rows.length ? ` (${lines.length - rows.length} extra lines dropped)` : ''));
}

// Whole-cell flat layers visibility while a row structure exists (bug report:
// "the human correction area disappears and I cannot pull the OCR/LLM fields")
let _rsShowFlat = localStorage.getItem('rsShowFlat') === '1';
function _rsToggleFlat(e) {
  e?.preventDefault();
  _rsShowFlat = !_rsShowFlat;
  try { localStorage.setItem('rsShowFlat', _rsShowFlat ? '1' : '0'); } catch {}
  if (selIdx >= 0) renderRowTable(pageData.shapes[selIdx]);
}

let _rsPdfCap = 60;
function _rsSetPdfCap(v) {
  const n = parseInt(v, 10);
  if (n > 0) { _rsPdfCap = n; if (selIdx >= 0) renderRowTable(pageData.shapes[selIdx]); }
}

// PDF value for row i: prefer the per-row extraction (row.pdf, written by the
// PDF ⟳ refresh), fall back to the flat pdf_text lines top-aligned
function _rsPdfVal(shape, i) {
  const rows = _rsRows(shape);
  if (rows?.[i]?.pdf != null) return rows[i].pdf;
  return _rsSplitFlat(shape.pdf_text)[i] ?? '';
}

// ── Per-column refresh: re-pull content into the CURRENT row structure ──────
async function _rsRefreshPdf() {
  if (selIdx < 0) return;
  const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: selIdx});
  const r = await fetch(`${API}/api/page/shape/rows/pdf-refresh?${params}`, {method: 'POST'});
  if (!r.ok) { showToast(`PDF refresh failed: ${(await r.text()).slice(0, 120)}`); return; }
  const data = await r.json();
  pageData.shapes[selIdx].row_struct = data.row_struct;
  renderRowTable(pageData.shapes[selIdx]);
  showToast('PDF layer re-extracted per row');
}

async function _rsRefreshOcr() {
  if (selIdx < 0) return;
  showToast('EasyOCR over current rows…');
  const ch = parseInt(document.getElementById('ocr-cellheight')?.value, 10) || 26;
  try { await _ocrLineByLineOne(selIdx, ch, '/api/page/shape/ocr/easyocr/linebyline'); }
  catch (e) { showToast(`OCR refresh failed: ${e.message}`); return; }
  renderRowTable(pageData.shapes[selIdx]); refreshDiag();
}

let _rsLlmRunning = false;
async function _rsRefreshLlm() {
  if (selIdx < 0) return;
  if (_rsLlmRunning) {                      // second click = stop
    batchAbort = true;
    const btn = document.getElementById('rs-llm-refresh-btn');
    if (btn) btn.textContent = '…';
    return;
  }
  const model  = document.getElementById('llm-model').value;
  const prompt = document.getElementById('llm-prompt').value.trim();
  if (!prompt) { showToast('LLM prompt is empty'); return; }
  _rsLlmRunning = true; batchAbort = false;
  const btn = document.getElementById('rs-llm-refresh-btn');
  if (btn) { btn.textContent = '■'; btn.title = 'Stop'; }
  showToast('LLM over current rows…');
  const ch = parseInt(document.getElementById('llm-cellheight')?.value, 10) || 28;
  try { await _llmLineByLineOne(selIdx, model, prompt, ch); }
  catch (e) { showToast(`LLM refresh failed: ${e.message}`); }
  finally {
    _rsLlmRunning = false; batchAbort = false;
    if (selIdx >= 0) { renderRowTable(pageData.shapes[selIdx]); refreshDiag(); }
  }
}

// Fill Human with the majority opinion of the available sources (PDF/OCR/LLM):
// 1 source → copy it; 2 → copy iff they agree; 3 → copy if at least 2 agree.
// Rows without a winner keep their current Human value.
async function _rsMajorityVote() {
  if (selIdx < 0) return;
  const shape = pageData.shapes[selIdx];
  const rows  = _rsRows(shape); if (!rows) return;
  let filled = 0, noConsensus = 0;
  rows.forEach((r, i) => {
    const votes = [_rsPdfVal(shape, i), (r.ocr || ''), (r.llm || '')]
      .map(v => v.trim()).filter(Boolean);
    if (!votes.length) return;
    let winner = null;
    if (votes.length === 1) winner = votes[0];
    else {
      const counts = {};
      votes.forEach(v => { counts[v] = (counts[v] || 0) + 1; });
      const [val, n] = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
      if (n >= 2) winner = val;
    }
    if (winner !== null) { r.human = winner; filled++; }
    else noConsensus++;
  });
  await saveRowStruct(selIdx);
  renderRowTable(shape);
  showToast(`Majority vote: ${filled} row${filled !== 1 ? 's' : ''} filled`
            + (noConsensus ? `, ${noConsensus} without consensus left unchanged` : ''));
}

async function _rsCopyColPdf() {
  if (selIdx < 0) return;
  const shape = pageData.shapes[selIdx];
  const rows  = _rsRows(shape); if (!rows) return;
  rows.forEach((r, i) => { r.human = _rsPdfVal(shape, i).trim(); });
  await saveRowStruct(selIdx);
  renderRowTable(shape);
}

async function _rsCopyPdf(i) {
  if (selIdx < 0) return;
  const shape = pageData.shapes[selIdx];
  const rows  = _rsRows(shape); if (!rows?.[i]) return;
  rows[i].human = _rsPdfVal(shape, i).trim();
  await saveRowStruct(selIdx);
  renderRowTable(shape);
}

// Draw each row's slice of the cell crop into its table thumbnail
function _rsDrawCrops() {
  const img = document.getElementById('cell-crop');
  if (!img?.naturalWidth || selIdx < 0) return;
  const bands = _rsBandsRel(pageData.shapes[selIdx]);
  if (!bands) return;
  document.querySelectorAll('#rows-table-wrap canvas.rs-crop').forEach(cv => {
    const band = bands[+cv.dataset.ri];
    if (!band) return;
    const t    = Math.max(0, band[0]);
    const srcH = Math.max(1, Math.min(img.naturalHeight, band[1]) - t);
    const scale = Math.min(16 / srcH, 110 / img.naturalWidth);
    cv.width  = Math.max(1, Math.round(img.naturalWidth * scale));
    cv.height = Math.max(1, Math.round(srcH * scale));
    cv.getContext('2d').drawImage(img, 0, t, img.naturalWidth, srcH, 0, 0, cv.width, cv.height);
  });
}

async function _rsCopyCol(layer) {
  if (selIdx < 0) return;
  const rows = _rsRows(pageData.shapes[selIdx]); if (!rows) return;
  rows.forEach(r => { r.human = r[layer] || ''; });
  await saveRowStruct(selIdx);
  renderRowTable(pageData.shapes[selIdx]);
}

async function _rsCopyCell(i, layer) {
  if (selIdx < 0) return;
  const rows = _rsRows(pageData.shapes[selIdx]); if (!rows?.[i]) return;
  rows[i].human = rows[i][layer] || '';
  await saveRowStruct(selIdx);
  renderRowTable(pageData.shapes[selIdx]);
}

function _rsHover(rowIdx) {
  if (selIdx < 0) return;
  const bands = _rsBandsRel(pageData.shapes[selIdx]);
  if (bands) drawRowOverlay(bands, rowIdx);
}

async function _rsHumanEdit(rowIdx, value) {
  if (selIdx < 0) return;
  const rows = _rsRows(pageData.shapes[selIdx]);
  if (!rows?.[rowIdx]) return;
  rows[rowIdx].human = value;
  await saveRowStruct(selIdx);
  renderRowTable(pageData.shapes[selIdx]);
}

// ── Internal-row authority resolution ──────────────────────────────────────
// Per-row resolved entity lives on row.authority (same shape as shape.authority).
// Candidate lists for the dropdown are cached in-memory per (shape,row); they are
// not persisted (re-fetched lazily on demand).
let _rsAuthCands = {shapeIdx: -1, byRow: {}};
function _rsAuthCandsReset()      { _rsAuthCands = {shapeIdx: selIdx, byRow: {}}; }
function _rsAuthCandsSet(i, c)    { if (_rsAuthCands.shapeIdx !== selIdx) _rsAuthCandsReset(); _rsAuthCands.byRow[i] = c; }
function _rsAuthCandsGet(i)       { return _rsAuthCands.shapeIdx === selIdx ? _rsAuthCands.byRow[i] : null; }

function _rsRowBest(shape, i) {
  const r = _rsRows(shape)?.[i]; if (!r) return '';
  return ((r.human || '').trim() || (r.llm || '').trim() || (r.ocr || '').trim()
          || (r.pdf != null ? r.pdf : _rsPdfVal(shape, i)).trim());
}
// Worth a lookup: ≥2 non-space chars and not an insanely long blob (a
// mis-segmented paragraph would never be a single place/industry name).
function _rsResolvable(text) {
  const s = (text || '').trim();
  return s.replace(/\s/g, '').length >= 2 && s.length <= 80;
}

// Ditto marks ("same as the line above") — quotes, dashes, repeat glyphs, or
// common abbreviations. These should NOT be fuzzy-matched against the whole
// authority; they inherit the entity from the row above instead.
function _isDitto(text) {
  const s = (text || '').trim();
  if (!s) return false;
  if (/^[\s.,\-–—―_=~:;"'’‘”“„«»<>]+$/.test(s)) return true;     // only ditto-ish glyphs
  const w = s.toLowerCase().replace(/[.\s]/g, '');
  return ['do','dto','ditto','idem','id','ua','uaz','ugyanaz','ugyanott','uo','uott','azelobbi','detto'].includes(w);
}
// Copy a resolved entity onto a ditto row (carried from the row above).
function _dittoCopy(a) { return {...a, source: 'auto', via: 'ditto', ts: new Date().toISOString()}; }

const _AUTH_MIN_ACCEPT = 70;   // auto-accept floor: below = no real string match → don't guess
function _candToAuth(c, source) {
  return {
    id: c.id, name: c.name, type: c.type, parent: c.parent,
    county_name: c.county_name, district_name: c.district_name,
    lat: c.lat, lon: c.lon, score: c.score, via: c.via,
    source, ts: new Date().toISOString(),
  };
}
async function _resolveText(text, type, parent) {
  const params = new URLSearchParams({q: text, name: _authFile()});
  if (type)   params.set('type', type);
  if (parent) params.set('parent', parent);
  try {
    const r = await fetch(`${API}/api/authority/resolve?${params}`);
    return r.ok ? ((await r.json()).candidates || []) : [];
  } catch (e) { return []; }
}

function _rsAuthCellHtml(r) {
  const a = r.authority;
  if (a) {
    const col = a.source === 'human' ? '#86efac' : '#93c5fd';   // green=manual, blue=auto
    const sc  = a.score != null ? `<sup style="color:#667;font-size:8px;">${a.score}</sup>` : '';
    return `<span style="color:${col};">${_escHtml(a.name)}</span>${sc}`;
  }
  return `<span style="color:#556;">—</span>`;
}

// Batch: resolve every row from its best text, skip empty/single-char,
// keep existing manual picks.
async function _rsResolveCol() {
 try {
  if (selIdx < 0) { showToast('Authority: no cell selected'); return; }
  const shape = pageData.shapes[selIdx];
  const rows  = _rsRows(shape);
  if (!rows || !rows.length) { showToast('Authority: this cell has no internal rows'); return; }
  showToast(`Resolving ${rows.length} row${rows.length > 1 ? 's' : ''}…`);
  const type   = document.getElementById('auth-type')?.value || '';
  const parent = _authParent();
  _rsAuthCandsReset();
  // Classify each row first so ditto marks never hit the authority lookup.
  const cls = rows.map((r, i) => {
    if (r.authority && r.authority.source === 'human') return 'human';
    const t = _rsRowBest(shape, i);
    if (_isDitto(t))        return 'ditto';
    if (!_rsResolvable(t))  return 'skip';
    return 'name';
  });
  // Pass 1: resolve real names in parallel.
  await Promise.all(rows.map(async (r, i) => {
    if (cls[i] !== 'name') return;
    const cands = await _resolveText(_rsRowBest(shape, i), type, parent);
    _rsAuthCandsSet(i, cands);
    if (!cands.length || cands[0].score < _AUTH_MIN_ACCEPT) { delete r.authority; cls[i] = 'nomatch'; }
    else                 r.authority = _candToAuth(cands[0], 'auto');
  }));
  // Pass 2: top-down so ditto rows inherit the entity above (chains included).
  let resolved = 0, kept = 0, skipped = 0, nomatch = 0, ditto = 0;
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    if (cls[i] === 'human')   { kept++; continue; }
    if (cls[i] === 'skip')    { delete r.authority; skipped++; continue; }
    if (cls[i] === 'nomatch') { nomatch++; continue; }
    if (cls[i] === 'name')    { resolved++; continue; }
    if (cls[i] === 'ditto') {
      const above = i > 0 ? rows[i - 1].authority : null;
      if (above) { r.authority = _dittoCopy(above); ditto++; }
      else       { delete r.authority; skipped++; }
    }
  }
  await saveRowStruct(selIdx);   // replaces shape.row_struct with the server's copy
  renderRowTable(shape);
  // If we resolved rows but none survived the save, the server stripped the
  // authority field — i.e. it's running pre-restart code.
  const persisted = (_rsRows(shape) || []).filter(r => r.authority).length;
  if ((resolved + ditto) > 0 && persisted === 0) {
    showToast('⚠ Resolved but the server dropped the values — restart the server (app/server.py changed).', 6000);
    return;
  }
  const _an = _authMeta(_authFile())?.authority || _authFile();
  showToast(`[${_an}] ${resolved} resolved`
    + (ditto ? `, ${ditto} ditto→above` : '')
    + (kept ? `, ${kept} manual kept` : '')
    + (nomatch ? `, ${nomatch} no match` : '')
    + (skipped ? `, ${skipped} skipped` : '')
    + (resolved === 0 && ditto === 0 && nomatch > 0 ? ' — wrong authority? check Source' : ''));
 } catch (e) {
  console.error('[auth] _rsResolveCol failed', e);
  showToast('Auth error: ' + (e?.message || e), 6000);
 }
}

// Click (single) → dropdown of alternatives.  Double-click → inherit from above.
// A short timer disambiguates the two on the same cell.
let _rsAuthClickTimer = null;
function _rsAuthClick(i, ev) {
  const el = ev.currentTarget;
  if (_rsAuthClickTimer) return;
  _rsAuthClickTimer = setTimeout(() => {
    _rsAuthClickTimer = null;
    _rsOpenAuthDropdown(i, el).catch(e => {
      console.error('[auth] dropdown failed', e);
      showToast('Auth error: ' + (e?.message || e), 6000);
    });
  }, 230);
}
function _rsAuthDbl(i, ev) {
  if (_rsAuthClickTimer) { clearTimeout(_rsAuthClickTimer); _rsAuthClickTimer = null; }
  _rsInheritAuth(i);
}

async function _rsInheritAuth(i) {
 try {
  if (selIdx < 0) { showToast('Authority: no cell selected'); return; }
  if (i <= 0) { showToast('No row above to copy from'); return; }
  const rows = _rsRows(pageData.shapes[selIdx]); if (!rows?.[i]) return;
  const above = rows[i - 1].authority;
  if (above) { rows[i].authority = {...above, source: 'human', ts: new Date().toISOString()}; }
  else       { delete rows[i].authority; showToast('Row above has no resolved entity'); }
  _rsCloseAuthDropdown();
  await saveRowStruct(selIdx);
  renderRowTable(pageData.shapes[selIdx]);
 } catch (e) { console.error('[auth] inherit failed', e); showToast('Auth error: ' + (e?.message || e), 6000); }
}

async function _rsAssignRowAuth(i, cand) {
 try {
  if (selIdx < 0) return;
  const rows = _rsRows(pageData.shapes[selIdx]); if (!rows?.[i]) return;
  if (!cand) delete rows[i].authority;
  else       rows[i].authority = _candToAuth(cand, 'human');
  _rsCloseAuthDropdown();
  await saveRowStruct(selIdx);
  renderRowTable(pageData.shapes[selIdx]);
 } catch (e) { console.error('[auth] assign failed', e); showToast('Auth error: ' + (e?.message || e), 6000); }
}

function _rsCloseAuthDropdown() {
  document.getElementById('rs-auth-dd')?.remove();
  document.removeEventListener('mousedown', _rsAuthDocClose, true);
}
function _rsAuthDocClose(ev) {
  if (!ev.target.closest('#rs-auth-dd')) _rsCloseAuthDropdown();
}

let _rsDDLastCands = [];   // candidates currently shown in the dropdown
let _rsDDDebounce  = null;
async function _rsOpenAuthDropdown(i, anchorEl) {
  _rsCloseAuthDropdown();
  const shape = pageData.shapes[selIdx];
  const rows  = _rsRows(shape); const r = rows?.[i]; if (!r) return;
  const type = document.getElementById('auth-type').value;
  const parent = _authParent();

  const dd = document.createElement('div');
  dd.id = 'rs-auth-dd';
  dd.style.cssText = 'position:fixed;z-index:2000;background:#0d1b35;border:1px solid #2a4a8e;'
    + 'border-radius:5px;box-shadow:0 6px 22px rgba(0,0,0,0.55);max-height:55vh;overflow:auto;'
    + 'min-width:220px;max-width:380px;font-size:11px;padding:4px;';
  const initial = _rsRowBest(shape, i) || (r.authority?.name || '');
  dd.innerHTML =
    `<input id="rs-auth-dd-search" type="text" placeholder="type to search… (3+ chars)" value="${_escHtml(initial)}" `
    + `style="width:100%;box-sizing:border-box;background:#16213e;border:1px solid #2a4a8e;border-radius:3px;`
    + `color:#cde;font:inherit;padding:3px 6px;margin-bottom:4px;">`
    + `<div id="rs-auth-dd-list"></div>`;
  document.body.appendChild(dd);
  const rect = anchorEl.getBoundingClientRect();
  dd.style.top  = Math.min(rect.bottom + 2, window.innerHeight - 60) + 'px';
  dd.style.left = Math.min(rect.left, window.innerWidth - dd.offsetWidth - 6) + 'px';

  const listEl = dd.querySelector('#rs-auth-dd-list');
  const render = (cands) => {
    _rsDDLastCands = cands || [];
    const curId = r.authority?.id;
    listEl.innerHTML = (_rsDDLastCands.length
      ? _rsDDLastCands.map(c => {
          const loc = [c.district_name, c.county_name].filter(Boolean).join(', ');
          const sc  = c.score >= 95 ? '#22c55e' : c.score >= 80 ? '#eab308' : '#f87171';
          const sel = c.id === curId ? 'background:#14532d;' : '';
          return `<div class="rs-auth-opt" data-id="${_escHtml(c.id)}" style="display:flex;justify-content:space-between;gap:8px;`
            + `align-items:center;padding:4px 6px;border-radius:3px;cursor:pointer;${sel}">`
            + `<span><b style="color:#cde;">${_escHtml(c.name)}</b> <span style="color:#778;font-size:9px;">${_escHtml(c.type || '')}</span>`
            + `${loc ? `<br><span style="color:#7a8;font-size:9px;">${_escHtml(loc)}</span>` : ''}</span>`
            + `<span style="color:${sc};font-weight:600;white-space:nowrap;">${c.score}%</span></div>`;
        }).join('')
      : `<div style="padding:6px;color:#a55;">No match.</div>`)
      + (r.authority ? `<div class="rs-auth-clear" style="padding:4px 6px;border-top:1px solid #1a3a6e;`
          + `margin-top:3px;color:#fca5a5;cursor:pointer;">✕ Clear</div>` : '');
    listEl.querySelectorAll('.rs-auth-opt').forEach(opt => opt.onclick = () =>
      _rsAssignRowAuth(i, _rsDDLastCands.find(c => c.id === opt.dataset.id)));
    listEl.querySelector('.rs-auth-clear')?.addEventListener('click', () => _rsAssignRowAuth(i, null));
  };

  // Initial list: cached candidates, or resolve the row's best text.
  let cands = _rsAuthCandsGet(i);
  if (!cands) {
    const t = _rsRowBest(shape, i);
    cands = _rsResolvable(t) ? await _resolveText(t, type, parent) : [];
    _rsAuthCandsSet(i, cands);
  }
  render(cands);

  // Live, debounced search — fires from the 3rd character.
  const search = dd.querySelector('#rs-auth-dd-search');
  search.addEventListener('input', () => {
    const q = search.value.trim();
    if (_rsDDDebounce) clearTimeout(_rsDDDebounce);
    if (q.length < 3) return;
    _rsDDDebounce = setTimeout(async () => {
      const res = await _resolveText(q, type, parent);
      // ignore if the dropdown was closed meanwhile
      if (document.getElementById('rs-auth-dd')) render(res);
    }, 180);
  });
  setTimeout(() => { search.focus(); search.select(); }, 0);
  setTimeout(() => document.addEventListener('mousedown', _rsAuthDocClose, true), 0);
}

console.log('%c[econai] structured-extraction build-19 (delete lattice button) active', 'color:#86efac');

async function convertToRows() {
  if (selIdx < 0 || !pages.length) return;
  const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: selIdx, force: 'true'});
  const r = await fetch(`${API}/api/page/shape/rows/convert?${params}`, {method: 'POST'});
  if (!r.ok) { showToast(`Convert failed: ${(await r.text()).slice(0, 120)}`); return; }
  const data = await r.json();
  pageData.shapes[selIdx].row_struct = data.row_struct;
  _syncFlatLocal(pageData.shapes[selIdx]);
  updatePanel(); drawOverlay(); refreshDiag();
  showToast(`Converted to ${data.rows} internal rows`);
}

async function removeRowStruct() {
  if (selIdx < 0) return;
  if (!confirm('Remove the internal row structure? Flat text layers are kept.')) return;
  delete pageData.shapes[selIdx].row_struct;
  await saveRowStruct(selIdx);     // empty rows → server deletes it
  updatePanel();
}

// ── Internal rows: divider editing on the cell crop (edit mode) ─────────────
let _rsDrag = null;   // {divider: i} — boundary between rows[i-1] and rows[i]

function _rsCropY(e) {
  // Mouse event → cell-crop pixel Y (the canvas draws at natural resolution)
  const img  = document.getElementById('cell-crop');
  const rect = img.getBoundingClientRect();
  return (e.clientY - rect.top) * ((img.naturalHeight || img.clientHeight) / img.clientHeight);
}

function _rsCropTop(shape) {
  const ys = shape.points.map(p => p[1]);
  return Math.max(0, Math.min(...ys) - 4);
}

// Hit tolerance in CROP pixels, derived from ~7 screen pixels so the feel is
// the same regardless of how much the crop is scaled in the panel
function _rsTol() {
  const img = document.getElementById('cell-crop');
  const scale = (img?.naturalHeight && img.clientHeight) ? img.naturalHeight / img.clientHeight : 1;
  return Math.max(3, 7 * scale);
}

function _rsNearestDivider(shape, cropY, tol) {
  const rows = _rsRows(shape); if (!rows) return -1;
  tol = tol ?? _rsTol();
  const top = _rsCropTop(shape);
  let best = -1, bestD = Infinity;
  for (let i = 1; i < rows.length; i++) {
    const d = Math.abs((rows[i].y0 - top) - cropY);
    if (d <= tol && d < bestD) { best = i; bestD = d; }
  }
  return best;
}

let _rsHoverDiv = -1;   // divider index currently highlighted under the cursor

// Redraw the bands, then mark the hovered divider red (= "dbl-click merges HERE")
async function _rsRedrawWithHover(shape) {
  await drawRowOverlay(_rsBandsRel(shape), -1);
  if (_rsHoverDiv < 0) return;
  const canvas = document.getElementById('row-canvas');
  const rows   = _rsRows(shape); if (!rows?.[_rsHoverDiv]) return;
  const y   = rows[_rsHoverDiv].y0 - _rsCropTop(shape);
  const ctx = canvas.getContext('2d');
  ctx.strokeStyle = 'rgba(255,40,40,0.95)';
  ctx.lineWidth   = 3;
  ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
}

function _initRowCanvasEditing() {
  const canvas = document.getElementById('row-canvas');

  canvas.addEventListener('mousemove', e => {
    if (selIdx < 0 || !editMode) return;
    const shape = pageData.shapes[selIdx];
    if (_rsDrag) {
      // Only treat it as a drag once the mouse actually moves — a pure click
      // (e.g. the first click of a double-click to merge) must NOT save/render,
      // or it disrupts the dblclick's divider detection.
      if (!_rsDrag.moved) {
        if (Math.abs(e.clientY - _rsDrag.startY) < 3) { e.preventDefault(); return; }
        _rsDrag.moved = true;
      }
      const rows = _rsRows(shape);
      const i    = _rsDrag.divider;
      const top  = _rsCropTop(shape);
      const minY = rows[i - 1].y0 + 3, maxY = rows[i].y1 - 3;
      const yAbs = Math.min(maxY, Math.max(minY, _rsCropY(e) + top));
      rows[i - 1].y1 = yAbs;
      rows[i].y0     = yAbs;
      drawRowOverlay(_rsBandsRel(shape), -1);
      drawOverlay();
      e.preventDefault();
      return;
    }
    const div = _rsNearestDivider(shape, _rsCropY(e));
    if (div !== _rsHoverDiv) { _rsHoverDiv = div; _rsRedrawWithHover(shape); }
    canvas.style.cursor = div >= 0 ? 'row-resize' : 'copy';
  });

  canvas.addEventListener('mouseleave', () => {
    if (_rsHoverDiv >= 0 && selIdx >= 0) {
      _rsHoverDiv = -1;
      const shape = pageData.shapes[selIdx];
      if (_rsRows(shape)) drawRowOverlay(_rsBandsRel(shape), -1);
    }
  });

  canvas.addEventListener('mousedown', e => {
    if (selIdx < 0 || !editMode) return;
    const div = _rsNearestDivider(pageData.shapes[selIdx], _rsCropY(e));
    if (div >= 0) { _rsDrag = {divider: div, startY: e.clientY, moved: false}; e.preventDefault(); e.stopPropagation(); }
  });

  window.addEventListener('mouseup', async () => {
    if (!_rsDrag) return;
    const moved = _rsDrag.moved;
    _rsDrag = null;
    // A click that never moved is not a resize — leave the structure untouched
    // (and don't re-render) so a double-click can merge the divider cleanly.
    if (moved && selIdx >= 0) {
      await saveRowStruct(selIdx, 'manual');
      renderRowTable(pageData.shapes[selIdx]);
    }
  });

  canvas.addEventListener('dblclick', async e => {
    if (selIdx < 0 || !editMode) return;
    const shape = pageData.shapes[selIdx];
    const rows  = _rsRows(shape);
    if (!rows) return;
    const top   = _rsCropTop(shape);
    const div   = _rsNearestDivider(shape, _rsCropY(e));

    if (div >= 0) {
      // Merge the two rows around the divider; texts joined with a space
      const a = rows[div - 1], b = rows[div];
      a.y1 = b.y1;
      ['ocr', 'llm', 'human'].forEach(L => {
        a[L] = [(a[L] || '').trim(), (b[L] || '').trim()].filter(Boolean).join(' ');
      });
      rows.splice(div, 1);
    } else {
      // Split the band under the cursor; the upper half keeps the texts
      const yAbs = _rsCropY(e) + top;
      const i = rows.findIndex(r => yAbs > r.y0 + 3 && yAbs < r.y1 - 3);
      if (i < 0) return;
      const r = rows[i];
      rows.splice(i + 1, 0, {n: 0, y0: yAbs, y1: r.y1, ocr: '', llm: '', human: ''});
      r.y1 = yAbs;
    }
    _rsHoverDiv = -1;
    await saveRowStruct(selIdx, 'manual');
    drawRowOverlay(_rsBandsRel(shape), -1);
    renderRowTable(shape);
    e.preventDefault();
  });
}
document.addEventListener('DOMContentLoaded', _initRowCanvasEditing);

async function replaceAllShapes() {
  return _serializeWrite(async () => {
    const params=new URLSearchParams({folder,stem:pages[pageIdx].stem});
    try {
      const r = await fetch(`${API}/api/page/shapes?${params}`,{
        method:'PUT', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({shapes:pageData.shapes}),
      });
      if (!r.ok) { showToast(`Save failed: ${r.status}`); return false; }
      return true;
    } catch (e) { showToast(`Save failed: ${e?.message || e}`); return false; }
  });
}

function copyShape() {
  if (selSet.size === 0) return;
  clipboard = [...selSet].map(i => JSON.parse(JSON.stringify(pageData.shapes[i])));
}

async function cloneInDirection(arrowKey) {
  if (selSet.size === 0) return;
  const params = new URLSearchParams({folder, stem: pages[pageIdx].stem});
  pushUndo();
  const newIdxs = [];
  for (const i of selSet) {
    const shape = pageData.shapes[i];
    const pts = shape.points;
    const x1=Math.min(pts[0][0],pts[1][0]), y1=Math.min(pts[0][1],pts[1][1]);
    const x2=Math.max(pts[0][0],pts[1][0]), y2=Math.max(pts[0][1],pts[1][1]);
    const w=x2-x1, h=y2-y1;
    let nx1=x1, ny1=y1;
    if (arrowKey==='ArrowRight') nx1=x2;
    else if (arrowKey==='ArrowLeft') nx1=x1-w;
    else if (arrowKey==='ArrowDown') ny1=y2;
    else if (arrowKey==='ArrowUp') ny1=y1-h;
    const r = await fetch(`${API}/api/page/shape?${params}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ label: shape.label, points: [[nx1,ny1],[nx1+w,ny1+h]] }),
    });
    if (r.ok) { const data = await r.json(); newIdxs.push(data.idx); }
  }
  await reloadPageData();
  selSet.clear(); selIdx=-1;
  newIdxs.forEach(i => { selSet.add(i); selIdx=i; });
  drawOverlay(); updatePanel();
}

async function pasteShape() {
  if (!clipboard?.length) return;
  const OFFSET = 10;
  const params = new URLSearchParams({folder, stem: pages[pageIdx].stem});
  pushUndo();
  const newIdxs = [];
  for (const shape of clipboard) {
    const newPts = shape.points.map(([x,y]) => [x+OFFSET, y+OFFSET]);
    const r = await fetch(`${API}/api/page/shape?${params}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ label: shape.label, points: newPts }),
    });
    if (r.ok) { const data = await r.json(); newIdxs.push(data.idx); }
  }
  await reloadPageData();
  selSet.clear(); selIdx=-1;
  newIdxs.forEach(i => { selSet.add(i); selIdx=i; });
  drawOverlay(); updatePanel();
}

let _stampingInProgress = false;
async function cloneSelectionToPage(delta) {
  if (_stampingInProgress) return;
  _stampingInProgress = true;
  try { await _doCloneSelectionToPage(delta); } finally { _stampingInProgress = false; }
}
async function _doCloneSelectionToPage(delta) {
  if (selSet.size === 0) { showToast('No selection to stamp'); return; }
  const targetIdx = pageIdx + delta;
  if (targetIdx < 0 || targetIdx >= pages.length) { showToast('No page to stamp to'); return; }
  const shapesToClone = [...selSet]
    .map(i => pageData.shapes[i])
    .filter(Boolean)
    .map(s => ({ label: s.label, points: s.points.map(p => [...p]) }));
  if (!shapesToClone.length) { showToast('Nothing to stamp'); return; }
  const targetStem = pages[targetIdx].stem;
  await loadPage(targetIdx);
  const params = new URLSearchParams({folder, stem: targetStem});
  const newIdxs = [];
  for (const shape of shapesToClone) {
    const r = await fetch(`${API}/api/page/shape?${params}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(shape),
    });
    if (r.ok) { const data = await r.json(); newIdxs.push(data.idx); }
    else { showToast(`Stamp failed: ${r.status} ${await r.text()}`); }
  }
  await reloadPageData();
  selSet.clear(); selIdx = -1;
  newIdxs.forEach(i => { selSet.add(i); selIdx = i; });
  drawOverlay(); updatePanel();
  showToast(`Stamped ${newIdxs.length}/${shapesToClone.length} shapes to page ${targetIdx+1}`);
}

async function deleteSelectedShape() {
  if (selSet.size === 0) return;
  const count = selSet.size;
  if (!confirm(`Delete ${count} box${count>1?'es':''}?`)) return;
  pushUndo();
  const newShapes = pageData.shapes.filter((_,i) => !selSet.has(i));
  const params = new URLSearchParams({folder, stem: pages[pageIdx].stem});
  await fetch(`${API}/api/page/shapes?${params}`, {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ shapes: newShapes }),
  });
  selSet.clear(); selIdx=-1; flaggedOverlaps=new Set();
  await reloadPageData(); drawOverlay(); updatePanel();
}

async function changeLabel(newLabel) {
  if (selSet.size === 0) return;
  pushUndo();
  lastUsedLabel = newLabel;
  selSet.forEach(i => { pageData.shapes[i].label = newLabel; });
  await replaceAllShapes();
  drawOverlay();
}

