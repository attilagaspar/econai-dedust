// Split from index.html — classic scripts share the global scope;
// load order in index.html is load-bearing. See knowledge_base/02_architecture.md.
// ── Table drawing ────────────────────────────────────────────────────────────
function startTableMode() {
  if (editMode) toggleEditMode();
  tableMode = true; tableRect = null; tableColSeps = []; tableRowSeps = []; tableTool = null;
  document.getElementById('table-toolbar').style.display = 'flex';
  document.getElementById('table-btn').classList.add('active');
  svgOverlay.style.pointerEvents = 'all';
  showToast('Draw the table outline by dragging');
}

function cancelTableMode() {
  tableMode = false; tableRect = null; tableColSeps = []; tableRowSeps = []; tableTool = null;
  document.getElementById('table-toolbar').style.display = 'none';
  document.getElementById('table-btn').classList.remove('active');
  svgOverlay.style.pointerEvents = (editMode || tableMode || perspMode) ? 'all' : 'none';
  drawOverlay();
}

function resetTableRect() {
  tableRect = null; tableColSeps = []; tableRowSeps = []; tableTool = null;
  updateTableToolButtons();
  drawOverlay();
  showToast('Redraw the table outline');
}

function setTableTool(tool) {
  tableTool = tableTool === tool ? null : tool;
  updateTableToolButtons();
  drawOverlay();
}

function updateTableToolButtons() {
  ['col','row','delete'].forEach(t => {
    document.getElementById(`tbl-${t}-btn`)?.classList.toggle('active', tableTool===t);
  });
}

function equalSplit(axis) {
  if (!tableRect) { showToast('Draw the table outline first'); return; }
  const n = parseInt(prompt(`Split into how many equal ${axis==='col'?'columns':'rows'}?`));
  if (!n || n < 2) return;
  pushTableUndo();
  if (axis === 'col') {
    const w = (tableRect.x2 - tableRect.x1) / n;
    tableColSeps = Array.from({length: n-1}, (_,i) => tableRect.x1 + w*(i+1));
  } else {
    const h = (tableRect.y2 - tableRect.y1) / n;
    tableRowSeps = Array.from({length: n-1}, (_,i) => tableRect.y1 + h*(i+1));
  }
  drawOverlay();
}

function handleTableClick(e) {
  if (!tableRect) return;
  pushTableUndo();
  const p = screenToImg(e.clientX, e.clientY);
  if (tableTool === 'col') {
    const x = Math.max(tableRect.x1, Math.min(tableRect.x2, p.x));
    tableColSeps.push(x);
    tableColSeps.sort((a,b) => a-b);
  } else if (tableTool === 'row') {
    const y = Math.max(tableRect.y1, Math.min(tableRect.y2, p.y));
    tableRowSeps.push(y);
    tableRowSeps.sort((a,b) => a-b);
  }
  drawOverlay();
}

async function finishTable() {
  if (!tableRect) { showToast('Draw the table outline first'); return; }
  const label = lastUsedLabel || projectLabels[0] || 'cell';
  const xs = [tableRect.x1, ...tableColSeps, tableRect.x2];
  const ys = [tableRect.y1, ...tableRowSeps, tableRect.y2];
  const shapes = [];
  for (let r = 0; r < ys.length-1; r++)
    for (let c = 0; c < xs.length-1; c++)
      shapes.push({ label, points: [[xs[c], ys[r]], [xs[c+1], ys[r+1]]], group_id:null, shape_type:'rectangle', flags:{} });
  pushUndo();
  pageData.shapes.push(...shapes);
  await replaceAllShapes();
  cancelTableMode();
  showToast(`Created ${shapes.length} cells (${ys.length-1} rows × ${xs.length-1} cols)`);
}

// ── Perspective correction ────────────────────────────────────────────────────
// Auto-detect mode: clicking the button immediately triggers server-side
// corner detection and shows the corrected preview.  No manual corner picking.
async function startPerspMode() {
  if (editMode) toggleEditMode();
  if (tableMode) cancelTableMode();
  perspPoints = [];
  document.getElementById('persp-btn').classList.add('active');
  showToast('Auto-detecting page boundaries…', 8000);
  try {
    const r = await fetch(`${API}/api/page/perspective`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        folder, stem: pages[pageIdx].stem,
        points: [], save: false,
      }),
    });
    if (!r.ok) {
      const txt = await r.text();
      showToast(`Detection failed: ${txt.slice(0, 200)}`, 8000);
      document.getElementById('persp-btn').classList.remove('active');
      return;
    }
    const data = await r.json();
    // Store detected corners so acceptPerspective() can re-submit them for save.
    perspPoints = data.detected_points || [];
    document.getElementById('persp-preview').src =
      `data:image/jpeg;base64,${data.preview}`;
    document.getElementById('persp-modal-info').textContent =
      `Auto-corrected preview: ${data.width} × ${data.height} px — accept to save, reject to discard`;
    document.getElementById('persp-modal').classList.add('show');
  } catch(err) {
    showToast(`Network error: ${err.message}`, 6000);
    document.getElementById('persp-btn').classList.remove('active');
  }
}

function cancelPerspMode() {
  perspPoints = [];
  svgOverlay.style.cursor = '';
  document.getElementById('persp-btn').classList.remove('active');
  document.getElementById('persp-toolbar').style.display = 'none';
  svgOverlay.style.pointerEvents = (editMode || tableMode) ? 'all' : 'none';
  drawOverlay();
}

async function acceptPerspective() {
  document.getElementById('persp-modal').classList.remove('show');
  showToast('Saving…', 3000);
  const r = await fetch(`${API}/api/page/perspective`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      folder, stem: pages[pageIdx].stem,
      points: perspPoints, save: true,
    }),
  });
  if (!r.ok) { showToast(`Save error: ${await r.text()}`); return; }
  cancelPerspMode();
  loadPage._bust = true;
  await loadPage(pageIdx);
  showToast('Perspective correction saved. Shapes cleared.');
}

function rejectPerspective() {
  document.getElementById('persp-modal').classList.remove('show');
  document.getElementById('persp-btn').classList.remove('active');
  perspPoints = [];
  showToast('Rejected — click Perspective to try again');
}

// ── Excel export ─────────────────────────────────────────────────────────────
function openExcelExportModal() {
  // Populate annotation type checkboxes
  const wrap = document.getElementById('xls-types-wrap');
  wrap.innerHTML = '';
  const labels = projectLabels.length ? projectLabels
    : [...new Set((shapes || []).map(s => s.label).filter(Boolean))];
  if (!labels.length) {
    wrap.innerHTML = '<span style="color:#666;font-size:0.8rem;">No labels defined</span>';
  } else {
    labels.forEach(lbl => {
      const id = 'xls-type-' + lbl.replace(/\W/g,'_');
      const label = document.createElement('label');
      label.style.cssText = 'display:flex;align-items:center;gap:5px;cursor:pointer;font-size:0.82rem;';
      label.innerHTML = `<input type="checkbox" id="${id}" value="${lbl}" checked
        style="accent-color:#e94560;"> ${lbl}`;
      wrap.appendChild(label);
    });
  }
  // Pre-fill page range with current page number
  const pagesInput = document.getElementById('xls-pages');
  if (pages[pageIdx]?.stem) {
    // Extract 1-based page number from stem (e.g. "batch1_page_5" → 5, or use pageIdx+1)
    pagesInput.value = String(pageIdx + 1);
  }
  document.getElementById('excel-export-modal').style.display = 'flex';
}

function closeExcelExportModal() {
  document.getElementById('excel-export-modal').style.display = 'none';
}

async function doExcelExport() {
  const patternRaw = document.getElementById('xls-pattern').value.trim();
  if (patternRaw && !/^[01](\s*,\s*[01])*$/.test(patternRaw)) {
    alert('Page pattern must be 1s and 0s separated by commas, e.g. 1,1,0,0'); return;
  }
  const layer      = document.getElementById('xls-layer').value;
  const checked    = [...document.querySelectorAll('#xls-types-wrap input[type=checkbox]:checked')]
                       .map(cb => cb.value);
  const types      = checked.join(',');
  const colHeaders = document.getElementById('xls-col-headers').value.trim();
  const colFilter  = document.getElementById('xls-col-filter').value.trim();

  // Resolve page range using allJsonStems (all JSON files, no image requirement).
  // Falls back to pages[] if allJsonStems hasn't loaded yet.
  const rawPages   = document.getElementById('xls-pages').value.trim();
  const stemSource = allJsonStems.length ? allJsonStems : pages.map(p => p.stem);
  const n          = stemSource.length;

  // Parse range against the full stem list
  let indices;
  if (rawPages === '') {
    indices = new Set([...Array(n).keys()]);
  } else {
    // Inline parse — same logic as _parsePageRange but against n (full JSON count)
    indices = new Set();
    for (const part of rawPages.split(',')) {
      const s = part.trim(); if (!s) continue;
      const m = s.match(/^(\d*)-(\d*)$|^(\d+)$/);
      if (!m) { alert(`Cannot parse: "${s}"`); return; }
      if (m[3] !== undefined) {
        const p = parseInt(m[3]) - 1; if (p >= 0 && p < n) indices.add(p);
      } else {
        const from = m[1] === '' ? 0     : parseInt(m[1]) - 1;
        const to   = m[2] === '' ? n - 1 : parseInt(m[2]) - 1;
        for (let i = Math.max(0, from); i <= Math.min(n - 1, to); i++) indices.add(i);
      }
    }
  }
  if (!indices.size) { alert('No pages in selected range.'); return; }

  const stemsList = [...indices].sort((a,b)=>a-b).map(i => stemSource[i]).filter(Boolean);
  if (!stemsList.length) { alert('No pages found.'); return; }
  const pageParams = { stems: stemsList.join(',') };

  const rowsOnly  = document.getElementById('xls-rows-only').checked;
  const clipCol   = document.getElementById('xls-clip-col').checked;
  const clipsOnly = document.getElementById('xls-clips-only').checked;
  const params = new URLSearchParams({
    folder, layer, types,
    scope: 'document',
    ...pageParams,
    ...(patternRaw ? { pattern:     patternRaw } : {}),
    ...(colHeaders ? { col_headers: colHeaders } : {}),
    ...(colFilter  ? { col_filter:  colFilter  } : {}),
    ...(rowsOnly   ? { rows_only:   'true'     } : {}),
    ...(clipCol    ? { clip_col:    'true'     } : {}),
    ...(clipsOnly  ? { clips_only:  'true'     } : {}),
  });

  closeExcelExportModal();
  showToast('Building Excel…');
  try {
    const resp = await fetch(`${API}/api/export/excel?${params}`);
    if (!resp.ok) {
      const msg = await resp.json().catch(() => ({detail: resp.statusText}));
      alert('Export failed: ' + (msg.detail || resp.statusText));
      return;
    }
    const blob = await resp.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    const cd   = resp.headers.get('Content-Disposition') || '';
    const match = cd.match(/filename="([^"]+)"/);
    a.download = match ? match[1] : 'export.xlsx';
    a.href = url;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast('Excel exported ✓');
  } catch(e) {
    alert('Export error: ' + e.message);
  }
}

// ── Prompt palette ────────────────────────────────────────────────────────────
const PALETTE_KEY = 'promptPalette';
let _paletteFromBatch = false;
let _paletteTarget = 'batch-llm-prompt';   // id of textarea to load into

function _getPalette() {
  try { return JSON.parse(localStorage.getItem(PALETTE_KEY) || '[]'); } catch { return []; }
}
function _setPalette(arr) {
  localStorage.setItem(PALETTE_KEY, JSON.stringify(arr));
}

function openPromptPalette(fromBatch, targetId) {
  _paletteFromBatch = !!fromBatch;
  _paletteTarget = targetId || 'batch-llm-prompt';
  _renderPaletteList();
  // clear add-new fields
  document.getElementById('palette-new-name').value = '';
  document.getElementById('palette-new-text').value = '';
  document.getElementById('prompt-palette-modal').style.display = 'flex';
}
function openPromptPaletteForBatch()      { openPromptPalette(true,  'batch-llm-prompt'); }
function openPromptPaletteForInspector()  { openPromptPalette(true,  'llm-prompt'); }
function closePromptPalette() {
  document.getElementById('prompt-palette-modal').style.display = 'none';
}

function _renderPaletteList() {
  const list    = document.getElementById('prompt-palette-list');
  const palette = _getPalette();
  if (!palette.length) {
    list.innerHTML = '<div style="font-size:12px;color:#555;padding:8px 0;">No saved prompts yet.</div>';
    return;
  }
  list.innerHTML = palette.map((p, i) => `
    <div style="background:#060f20;border:1px solid #1a3a6e;border-radius:5px;padding:8px 10px;display:flex;flex-direction:column;gap:4px;">
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-size:12px;font-weight:600;color:#e0e0e0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_escHtml(p.name)}</span>
        <button onclick="loadFromPalette(${i})"
          style="background:#0f3460;border:1px solid #1a3a6e;border-radius:3px;color:#e0e0e0;font-size:11px;padding:2px 10px;cursor:pointer;white-space:nowrap;">
          ${_paletteFromBatch ? '↙ Load' : '↙ Load'}
        </button>
        <button onclick="deleteFromPalette(${i})"
          style="background:none;border:1px solid #3a1a1a;border-radius:3px;color:#e94560;font-size:11px;padding:2px 8px;cursor:pointer;">✕</button>
      </div>
      <div style="font-size:10px;color:#556;white-space:pre-wrap;max-height:52px;overflow:hidden;line-height:1.4;">${_escHtml(p.text.slice(0, 200))}${p.text.length > 200 ? '…' : ''}</div>
    </div>
  `).join('');
}

function _escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function loadFromPalette(idx) {
  const palette = _getPalette();
  if (!palette[idx]) return;
  document.getElementById(_paletteTarget).value = palette[idx].text;
  closePromptPalette();
  showToast('Prompt loaded ✓');
}

function deleteFromPalette(idx) {
  const palette = _getPalette();
  palette.splice(idx, 1);
  _setPalette(palette);
  _renderPaletteList();
}

function addPromptToPalette() {
  const name = document.getElementById('palette-new-name').value.trim();
  const text = document.getElementById('palette-new-text').value.trim();
  if (!name) { showToast('Enter a name'); return; }
  if (!text) { showToast('Prompt text is empty'); return; }
  const palette = _getPalette();
  palette.push({ name, text });
  _setPalette(palette);
  document.getElementById('palette-new-name').value = '';
  document.getElementById('palette-new-text').value = '';
  _renderPaletteList();
  showToast('Saved to palette ✓');
}

function saveInspectorPromptToPalette() {
  const text = document.getElementById('llm-prompt').value.trim();
  if (!text) { showToast('Prompt is empty'); return; }
  const name = text.slice(0, 40).replace(/\n/g, ' ');
  openPromptPalette(false);
  document.getElementById('palette-new-name').value = name;
  document.getElementById('palette-new-text').value = text;
  setTimeout(() => document.getElementById('palette-new-name').select(), 50);
}

function saveBatchPromptToPalette() {
  const text = document.getElementById('batch-llm-prompt').value.trim();
  if (!text) { showToast('Prompt is empty'); return; }
  // Pre-fill the add-new section and open modal
  const name = text.slice(0, 40).replace(/\n/g, ' ');
  openPromptPalette(false);
  document.getElementById('palette-new-name').value = name;
  document.getElementById('palette-new-text').value = text;
  // focus the name field so user can rename quickly
  setTimeout(() => document.getElementById('palette-new-name').select(), 50);
}

// ── Edit mode toggle ─────────────────────────────────────────────────────────
function toggleEditMode() {
  editMode=!editMode;
  const btn=document.getElementById('mode-btn');
  btn.textContent=editMode?'Edit mode':'Review mode';
  btn.classList.toggle('active',editMode);
  drawOverlay(); updatePanel();
}

// ── Legend ───────────────────────────────────────────────────────────────────
function legendSwatch({fill,border,borderWidth}) {
  return `<div class="legend-swatch" style="background:${fill};outline:${borderWidth}px solid ${border};outline-offset:-${borderWidth}px"></div>`;
}
function buildLegend() {
  const legend=document.getElementById('legend'); legend.innerHTML='';
  [...new Set((pageData?.shapes||[]).map(s=>s.label))].sort().forEach(lbl=>{
    const div=document.createElement('div'); div.className='legend-item';
    div.innerHTML=`${legendSwatch({fill:colorFor(lbl),border:'#888',borderWidth:1})}<span>${lbl}</span>`;
    legend.appendChild(div);
  });
  const sep=document.createElement('span');
  sep.style.cssText='color:#444;padding:0 4px;'; sep.textContent='│';
  legend.appendChild(sep);
  [{s:STATUS_BORDER.human,label:'Human corrected'},{s:STATUS_BORDER.llm,label:'LLM output'},
   {s:STATUS_BORDER.ocr,label:'OCR only'},{s:STATUS_BORDER.none,label:'Unprocessed'}]
  .forEach(({s,label})=>{
    const div=document.createElement('div'); div.className='legend-item';
    div.innerHTML=`${legendSwatch({fill:'transparent',border:s.color,borderWidth:s.width})}<span>${label}</span>`;
    legend.appendChild(div);
  });
  const combo=document.createElement('div'); combo.className='legend-item';
  const o=STATUS_BORDER.ocr;
  combo.innerHTML=`<div class="legend-swatch" style="background:transparent;outline:${o.width}px solid ${o.color};outline-offset:-${o.width}px;box-shadow:inset 0 0 0 ${o.width+1}px ${STATUS_BORDER.llm.color}"></div><span>OCR+LLM agree</span>`;
  legend.appendChild(combo);
  const dis=document.createElement('div'); dis.className='legend-item';
  dis.innerHTML=`${legendSwatch({fill:'transparent',border:'#f59e0b',borderWidth:3})}<span>OCR≠LLM — check</span>`;
  legend.appendChild(dis);
}

// ── Selection & panel ────────────────────────────────────────────────────────
function selectShape(idx, addToSel=false) {
  if (addToSel && idx >= 0) {
    if (selSet.has(idx)) { selSet.delete(idx); if (selIdx===idx) selIdx=selSet.size>0?[...selSet].at(-1):-1; }
    else { selSet.add(idx); selIdx=idx; }
  } else {
    selSet.clear();
    if (idx >= 0) { selSet.add(idx); selIdx=idx; }
    else selIdx=-1;
  }
  if (idx>=0&&pageData?.shapes[idx]) lastUsedLabel=pageData.shapes[idx].label;
  drawOverlay(); updatePanel();
}

function updatePanel() {
  const noSel=document.getElementById('no-selection');
  const content=document.getElementById('fields-content');
  const cropImg=document.getElementById('cell-crop');
  const cropPh=document.getElementById('cell-crop-placeholder');
  const delBtn=document.getElementById('delete-btn');

  if (selIdx<0||!pageData) {
    noSel.style.display='block'; content.style.display='none';
    cropImg.style.display='none'; cropPh.style.display='block';
    document.getElementById('inspector').style.width = '80px';
    return;
  }
  noSel.style.display='none'; content.style.display='flex';

  // Multi-selection: show simplified panel (label + delete only)
  if (selSet.size > 1) {
    cropImg.style.display='none'; cropPh.style.display='block';
    const staticLbl=document.getElementById('f-label');
    const selectLbl=document.getElementById('f-label-select');
    const labels=[...new Set([...projectLabels,...(pageData.shapes||[]).map(s=>s.label)])].sort();
    const commonLabel=[...selSet].map(i=>pageData.shapes[i]?.label);
    const allSame=commonLabel.every(l=>l===commonLabel[0]);
    staticLbl.style.display='none'; selectLbl.style.display='block';
    selectLbl.innerHTML=labels.map(l=>`<option value="${l}"${allSame&&l===commonLabel[0]?' selected':''}>${l}</option>`).join('');
    document.getElementById('fg-score').style.display='none';
    document.getElementById('fg-super').style.display='none';

    // Show OCR/LLM panels so the user can run/clear on all selected shapes
    const selArr = [...selSet];
    const ocrCount = selArr.filter(i => pageData.shapes[i]?.tesseract_output?.ocr_text).length;
    const llmCount = selArr.filter(i => pageData.shapes[i]?.openai_output?.response).length;
    document.getElementById('fg-ocr').style.display='flex';
    document.getElementById('fg-llm').style.display='flex';
    document.getElementById('f-ocr').value =
      `${selSet.size} shapes selected · ${ocrCount} have OCR · ${selSet.size - ocrCount} empty`;
    document.getElementById('f-ocr-conf').textContent='';
    document.getElementById('f-llm-result').value =
      `${selSet.size} shapes selected · ${llmCount} have LLM · ${selSet.size - llmCount} empty`;
    document.getElementById('f-llm-meta').textContent='▶ Run / ▶ All / ✕ operate on all selected shapes';
    document.getElementById('cell-meta').textContent=`${selSet.size} shapes selected`;
    document.getElementById('fg-authority').style.display='none';
    document.getElementById('fg-structured').style.display='none';
    document.getElementById('save-btn').disabled=true;
    document.getElementById('save-status').textContent='';
    delBtn.style.display=editMode?'block':'none';
    return;
  }

  const shape=pageData.shapes[selIdx];

  // Clear the row grid when the user moves to a different cell
  if (selIdx !== _lastPanelIdx) {
    _lastPanelIdx = selIdx;
    lastRowLines  = null;
    lastEmptyRows = new Set();
    drawRowOverlay(null, -1);
    document.getElementById('preproc-rows').innerHTML = '';
    document.getElementById('preproc-placeholder').style.display = 'block';
  }

  cropImg.onload = function() {
    const inspector = document.getElementById('inspector');
    if (!inspector.classList.contains('inspector-collapsed')) {
      inspector.style.width = Math.min(420, Math.max(80, this.naturalWidth + 20)) + 'px';
    }
    const rsBands = _rsBandsRel(pageData?.shapes?.[selIdx]);
    if (rsBands)           drawRowOverlay(rsBands, -1);
    else if (lastRowLines) drawRowOverlay(lastRowLines, -1);
    _rsDrawCrops();
  };

  // Divider editing is done on the crop overlay canvas in edit mode
  const rowCanvas = document.getElementById('row-canvas');
  rowCanvas.style.pointerEvents = (editMode && _rsRows(shape)) ? 'auto' : 'none';
  cropImg.src=`${API}/api/cell?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(pages[pageIdx].stem)}&idx=${selIdx}`;
  cropImg.style.display='block'; cropPh.style.display='none';

  // Label: static in review, dropdown in edit
  const staticLbl=document.getElementById('f-label');
  const selectLbl=document.getElementById('f-label-select');
  if (editMode) {
    staticLbl.style.display='none'; selectLbl.style.display='block';
    const labels=[...new Set([...projectLabels, ...(pageData.shapes||[]).map(s=>s.label)])].sort();
    selectLbl.innerHTML=labels.map(l=>`<option value="${l}"${l===shape.label?' selected':''}>${l}</option>`).join('');
  } else {
    staticLbl.style.display='block'; staticLbl.textContent=shape.label||'—';
    selectLbl.style.display='none';
  }
  delBtn.style.display=editMode?'block':'none';

  const fgScore=document.getElementById('fg-score');
  shape.score!=null
    ? (fgScore.style.display='flex', document.getElementById('f-score').textContent=shape.score.toFixed(4))
    : (fgScore.style.display='none');

  const fgSuper=document.getElementById('fg-super');
  shape.super_row!=null
    ? (fgSuper.style.display='flex', document.getElementById('f-super').textContent=`row ${shape.super_row}  col ${shape.super_column}`)
    : (fgSuper.style.display='none');

  const fgOcr=document.getElementById('fg-ocr');
  fgOcr.style.display='flex';
  const ocrResult = shape.tesseract_output;
  document.getElementById('f-ocr').value = ocrResult?.ocr_text || '—';
  const confEl = document.getElementById('f-ocr-conf');
  if (ocrResult?.mean_conf != null) {
    confEl.textContent = `conf ${ocrResult.mean_conf}%  ·  lang: ${ocrResult.lang||'?'}`;
  } else {
    confEl.textContent = '';
  }

  // LLM cleaner — always visible when a shape is selected
  document.getElementById('fg-llm').style.display='flex';
  const llmOut = shape.openai_output;
  // Show '—' if no result yet; '(empty)' if the LLM ran but returned nothing
  document.getElementById('f-llm-result').value = llmOut
    ? (llmOut.response || '(empty)')
    : '—';
  const llmMeta = document.getElementById('f-llm-meta');
  if (llmOut?.model) {
    const ts  = llmOut.timestamp ? new Date(llmOut.timestamp).toLocaleString() : '';
    const tok = (llmOut.tokens_in != null)
      ? `  ·  ${llmOut.tokens_in}→${llmOut.tokens_out} tok` : '';
    llmMeta.textContent = `${llmOut.model}  ·  ${llmOut.mode||''}  ·  ${ts}${tok}`;
  } else {
    llmMeta.textContent = '';
  }

  document.getElementById('human-input').value=shape.human_output?.human_corrected_text||'';

  // Authority resolver — match the cell's text to a gazetteer entity
  refreshAuthorityPanel(shape);
  renderStructured(shape);

  // Internal rows: table view replaces the flat text boxes when present
  renderRowTable(shape);

  // PDF text layer
  document.getElementById('fg-pdf').style.display='flex';
  document.getElementById('f-pdf').value = shape.pdf_text ?? '—';
  document.getElementById('f-pdf-meta').textContent =
    shape.pdf_text != null ? 'from PDF text layer' : 'not yet extracted';

  refreshAllLineNums();

  const pts=shape.points||[], xs=pts.map(p=>p[0]), ys=pts.map(p=>p[1]);
  document.getElementById('cell-meta').textContent=
    `Shape ${selIdx}  •  ${Math.round(Math.max(...xs)-Math.min(...xs))} × ${Math.round(Math.max(...ys)-Math.min(...ys))} px`;
  document.getElementById('save-btn').disabled=false;
  document.getElementById('save-status').textContent='';
}

// ── OCR ───────────────────────────────────────────────────────────────────────
function updateOcrMode() {
  const mode = document.getElementById('ocr-mode').value;
  const showHeight  = mode === 'linebyline' || mode === 'easyocr-linebyline';
  const showAnchor  = mode === 'easyocr-anchored';
  document.getElementById('ocr-cellheight-wrap').style.display    = showHeight ? 'flex' : 'none';
  document.getElementById('ocr-anchor-source-wrap').style.display = showAnchor ? 'flex' : 'none';
  document.getElementById('ocr-anchor-cols-wrap').style.display   = showAnchor ? 'flex' : 'none';
}

async function runOcr() {
  if (selIdx < 0 || !pages.length) return;
  await _syncOcrSettings();
  const mode = document.getElementById('ocr-mode').value;

  // Anchored mode always uses the selected shape as reference — ignore multi-select
  if (mode === 'easyocr-anchored')   { runOcrAnchored();                                                 return; }

  // Multi-select: run on every selected shape
  if (selSet.size > 1) {
    await _runOcrBatch([...selSet].sort((a, b) => a - b));
    return;
  }

  if (mode === 'linebyline')         { runOcrLineByLine('/api/page/shape/ocr/linebyline');                return; }
  if (mode === 'easyocr-linebyline') { runOcrLineByLine('/api/page/shape/ocr/easyocr/linebyline');       return; }
  if (mode === 'easyocr')            { runOcrEasyOcr();                                                  return; }

  const btn = document.getElementById('ocr-btn');
  btn.disabled = true; btn.textContent = '…';
  try {
    const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: selIdx});
    const r = await fetch(`${API}/api/page/shape/ocr?${params}`, {method: 'POST'});
    if (!r.ok) { showToast(`OCR error ${r.status}: ${(await r.text()).slice(0,80)}`); return; }
    const data = await r.json();
    if (!pageData.shapes[selIdx].tesseract_output) pageData.shapes[selIdx].tesseract_output = {};
    pageData.shapes[selIdx].tesseract_output.ocr_text  = data.ocr_text;
    pageData.shapes[selIdx].tesseract_output.mean_conf = data.mean_conf;
    if (pageData.shapes[selIdx].row_struct) await _refreshShapeRowStruct(selIdx);
    updatePanel(); refreshDiag(); drawOverlay();
    showToast(`OCR done · conf ${data.mean_conf}%`);
  } finally {
    btn.disabled = false; btn.textContent = '▶ Run';
  }
}

// Shared OCR batch runner — used by both runOcr (multi-select) and runOcrAll (by-label)
async function _runOcrBatch(targets) {
  if (!targets.length) return;
  const ocrMode    = document.getElementById('ocr-mode').value;
  const cellHeight = parseInt(document.getElementById('ocr-cell-height').value) || 26;
  const overwrite  = document.getElementById('overwrite-cb').checked;
  const allBtn     = document.getElementById('ocr-all-btn');
  const stopBtn    = document.getElementById('ocr-stop-btn');
  const runBtn     = document.getElementById('ocr-btn');
  _batchStart(allBtn, stopBtn, runBtn);

  const ocrStatusEl = document.getElementById('ocr-batch-status');
  let done = 0, skipped = 0;
  for (const i of targets) {
    if (batchAbort) break;
    if (!overwrite && pageData.shapes[i].tesseract_output?.ocr_text) {
      skipped++;
      if (ocrStatusEl) ocrStatusEl.textContent = `⏭ ${skipped} already have OCR (check Overwrite to redo)`;
      continue;
    }
    batchHighlight = i;
    allBtn.textContent = `${done + 1}/${targets.length}`;
    const s = pageData.shapes[i];
    const cellInfo = (s.super_row != null && s.super_col != null)
      ? `row ${s.super_row}, col ${s.super_col}` : `shape ${i}`;
    if (ocrStatusEl) ocrStatusEl.textContent = `⟳ ${cellInfo}  (${done + 1}/${targets.length - skipped} to run)`;
    drawOverlay();
    try {
      if (ocrMode === 'linebyline') {
        await _ocrLineByLineOne(i, cellHeight, '/api/page/shape/ocr/linebyline');
      } else if (ocrMode === 'easyocr-linebyline') {
        await _ocrLineByLineOne(i, cellHeight, '/api/page/shape/ocr/easyocr/linebyline');
      } else if (ocrMode === 'easyocr') {
        const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: i});
        const r = await fetch(`${API}/api/page/shape/ocr/easyocr?${params}`, {method: 'POST'});
        if (!r.ok) { const _m = await r.json().catch(() => ({})); throw new Error(_m.detail || r.status); }
        const data = await r.json();
        pageData.shapes[i].tesseract_output = {ocr_text: data.ocr_text, mean_conf: data.mean_conf, engine: 'easyocr'};
        if (pageData.shapes[i].row_struct) await _refreshShapeRowStruct(i);
      } else {
        const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: i});
        const r = await fetch(`${API}/api/page/shape/ocr?${params}`, {method: 'POST'});
        if (!r.ok) { const _m = await r.json().catch(() => ({})); throw new Error(_m.detail || r.status); }
        const data = await r.json();
        pageData.shapes[i].tesseract_output = {ocr_text: data.ocr_text, mean_conf: data.mean_conf};
        if (pageData.shapes[i].row_struct) await _refreshShapeRowStruct(i);
      }
      done++;
      if (i === selIdx) updatePanel(); else drawOverlay();
    } catch(e) { if (batchAbort) break; else showToast(`OCR error shape ${i}: ${e.message}`); }
  }
  if (ocrStatusEl) ocrStatusEl.textContent = skipped && !done
    ? `All ${skipped} skipped — use ✕ Page to clear OCR first, or check Overwrite`
    : (skipped ? `${skipped} skipped, ${done} run` : '');
  _batchEnd(allBtn, stopBtn, done, targets.length, pageData.shapes[targets[0]]?.label || '', runBtn);
  updatePanel();  // refresh summary counts after batch
}

async function runOcrEasyOcr() {
  if (selIdx < 0 || !pages.length) return;
  const btn = document.getElementById('ocr-btn');
  btn.disabled = true; btn.textContent = '…';
  try {
    const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: selIdx});
    const r = await fetch(`${API}/api/page/shape/ocr/easyocr?${params}`, {method: 'POST'});
    if (!r.ok) { showToast(`EasyOCR error ${r.status}: ${(await r.text()).slice(0,80)}`); return; }
    const data = await r.json();
    pageData.shapes[selIdx].tesseract_output = {
      ocr_text: data.ocr_text, mean_conf: data.mean_conf, engine: 'easyocr',
    };
    if (pageData.shapes[selIdx].row_struct) await _refreshShapeRowStruct(selIdx);
    updatePanel(); refreshDiag(); drawOverlay();
    showToast(`EasyOCR done · conf ${data.mean_conf}%`);
  } finally {
    btn.disabled = false; btn.textContent = '▶ Run';
  }
}

async function runOcrLineByLine(apiPath = '/api/page/shape/ocr/linebyline') {
  if (selIdx < 0 || !pages.length) return;
  const btn     = document.getElementById('ocr-btn');
  const stopBtn = document.getElementById('ocr-stop-btn');
  btn.disabled = true; btn.textContent = '…';
  stopBtn.style.display = 'inline-block';
  llmAbortCtrl = new AbortController();

  const cellHeight = parseInt(document.getElementById('ocr-cell-height').value) || 26;

  const ocrEl = document.getElementById('f-ocr');
  ocrEl.value = '';

  // Pre-compute crop origin for main-image overlay
  const shape = pageData.shapes[selIdx];
  const spts  = shape.points;
  const sx1   = Math.min(spts[0][0], spts[1][0]);
  const sy1   = Math.min(spts[0][1], spts[1][1]);
  const sx2   = Math.max(spts[0][0], spts[1][0]);
  const pad   = 4, imgW = pageData.imageWidth || 99999;
  const cropOriginX = Math.max(0, sx1 - pad);
  const cropOriginY = Math.max(0, sy1 - pad);
  const cropRight   = Math.min(imgW, sx2 + pad);

  const lineResults = [];

  try {
    const params = new URLSearchParams({folder, stem: pages[pageIdx].stem,
                                        idx: selIdx, cell_height: cellHeight});
    const r = await fetch(`${API}${apiPath}?${params}`, {
      method: 'POST', signal: llmAbortCtrl.signal,
    });
    if (!r.ok) { showToast(`OCR error ${r.status}: ${(await r.text()).slice(0, 120)}`); return; }

    const reader  = r.body.getReader();
    const decoder = new TextDecoder();
    let   buffer  = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split('\n\n'); buffer = chunks.pop();

      for (const chunk of chunks) {
        if (!chunk.startsWith('data: ')) continue;
        let msg; try { msg = JSON.parse(chunk.slice(6)); } catch { continue; }
        if (msg.type === 'error') { reader.cancel(); showToast('⚠ ' + msg.error, 6000); return; }

        if (msg.type === 'lines_detected') {
          lastRowLines  = msg.lines;
          lastEmptyRows = new Set();   // reset so stale results don't persist
          llmProgress = { cropOriginX, cropOriginY, cropRight, lines: msg.lines, activeRow: -1, emptyRows: null };
          drawOverlay();
          drawRowOverlay(msg.lines, -1);
          // Pre-create one placeholder img per row
          const rows = document.getElementById('preproc-rows');
          const ph   = document.getElementById('preproc-placeholder');
          rows.innerHTML = '';
          for (let r = 0; r < msg.count; r++) {
            const img = document.createElement('img');
            img.alt = `row ${r + 1}`;
            rows.appendChild(img);
          }
          ph.style.display = 'none';

        } else if (msg.type === 'row_result') {
          lineResults[msg.row] = msg.text;
          llmProgress.activeRow = msg.row;
          drawOverlay();
          drawRowOverlay(llmProgress.lines, msg.row);
          ocrEl.value = lineResults.filter(t => t != null).join('\n');
          updateLineNums('f-ocr', 'f-ocr-lines');
          if (msg.img_b64) {
            const rows      = document.getElementById('preproc-rows');
            const preprocCol = document.getElementById('preproc');
            const slot = rows.children[msg.row];
            if (slot) {
              slot.onload = function() {
                this.classList.add('loaded');
                // Set column width from first loaded image
                if (!preprocCol.classList.contains('preproc-collapsed') && msg.row === 0)
                  preprocCol.style.width = Math.min(420, Math.max(80, this.naturalWidth + 20)) + 'px';
              };
              slot.src = 'data:image/png;base64,' + msg.img_b64;
              // Highlight active row, clear previous
              [...rows.children].forEach((el, i) => el.classList.toggle('active', i === msg.row));
            }
          }

        } else if (msg.type === 'done') {
          pageData.shapes[selIdx].tesseract_output = {
            ocr_text:  msg.ocr_text,
            mean_conf: msg.mean_conf ?? null,
            mode:      'linebyline',
          };
          llmProgress = null;
          await _refreshShapeRowStruct(selIdx);
          updatePanel(); refreshDiag();
          drawRowOverlay(lastRowLines, -1);
          showToast(`OCR line-by-line done · ${lineResults.length} rows`);
        }
      }
    }
  } catch(err) {
    if (err.name !== 'AbortError') showToast(`OCR error: ${err.message}`);
    else showToast('Stopped');
  } finally {
    llmAbortCtrl = null;
    llmProgress  = null;
    drawOverlay();
    drawRowOverlay(lastRowLines, -1);
    btn.disabled = false; btn.textContent = '▶ Run';
    stopBtn.style.display = 'none';
  }
}

async function _ocrLineByLineOne(shapeIdx, cellHeight, apiPath = '/api/page/shape/ocr/linebyline') {
  // Streaming line-by-line OCR for one shape; updates overlay
  const shape = pageData.shapes[shapeIdx];
  const spts  = shape.points;
  const sx1   = Math.min(spts[0][0], spts[1][0]);
  const sy1   = Math.min(spts[0][1], spts[1][1]);
  const sx2   = Math.max(spts[0][0], spts[1][0]);
  const pad   = 4, imgW = pageData.imageWidth || 99999;
  const cropOriginX = Math.max(0, sx1 - pad);
  const cropOriginY = Math.max(0, sy1 - pad);
  const cropRight   = Math.min(imgW, sx2 + pad);

  const params = new URLSearchParams({folder, stem: pages[pageIdx].stem,
                                      idx: shapeIdx, cell_height: cellHeight});
  const r = await fetch(`${API}${apiPath}?${params}`, {method: 'POST'});
  if (!r.ok) { const _m = await r.json().catch(() => ({})); throw new Error(_m.detail || r.status); }

  const reader = r.body.getReader(), decoder = new TextDecoder();
  let buffer = '';
  outer: while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    const chunks = buffer.split('\n\n'); buffer = chunks.pop();
    for (const chunk of chunks) {
      if (!chunk.startsWith('data: ')) continue;
      let msg; try { msg = JSON.parse(chunk.slice(6)); } catch { continue; }
      if (msg.type === 'error') { reader.cancel(); throw new Error(msg.error); }
      if (msg.type === 'lines_detected') {
        lastEmptyRows = new Set();
        llmProgress = {cropOriginX, cropOriginY, cropRight, lines: msg.lines, activeRow: -1, emptyRows: null};
        drawOverlay();
      } else if (msg.type === 'row_result') {
        llmProgress.activeRow = msg.row; drawOverlay();
        if (batchAbort) { reader.cancel(); break outer; }
      } else if (msg.type === 'done') {
        pageData.shapes[shapeIdx].tesseract_output = {
          ocr_text:  msg.ocr_text,
          mean_conf: msg.mean_conf ?? null,
          mode:      'linebyline',
        };
        await _refreshShapeRowStruct(shapeIdx);
        if (shapeIdx === selIdx) updatePanel();
        refreshDiag();
      }
    }
    if (batchAbort) { reader.cancel(); break; }
  }
  llmProgress = null;
  drawOverlay();
}

// ── EasyOCR anchored ─────────────────────────────────────────────────────────
// The selected shape acts as a row-count reference; all other shapes in the
// same super_row are cut into exactly that many rows and EasyOCR'd.

async function runOcrAnchored() {
  if (selIdx < 0 || !pages.length) return;
  const refShape = pageData.shapes[selIdx];

  // Determine row count from the chosen anchor source
  const anchorSrc = document.getElementById('ocr-anchor-source').value;
  let nRows, refIdx = -1;
  if (anchorSrc === 'structure') {
    // Project the reference cell's exact row bands onto the targets
    const refRows = refShape.row_struct?.rows;
    if (!refRows?.length) {
      showToast('Reference shape has no internal row structure — convert it first'); return;
    }
    nRows = refRows.length;
    refIdx = selIdx;
  } else {
    const refText = anchorSrc === 'human' ? refShape.human_output?.human_corrected_text
                  : anchorSrc === 'llm'   ? refShape.openai_output?.response
                  : anchorSrc === 'pdf'   ? refShape.pdf_text
                  :                         refShape.tesseract_output?.ocr_text;
    if (!refText?.trim()) {
      showToast(`Reference shape has no ${anchorSrc.toUpperCase()} text`); return;
    }
    nRows = _lineCount(refText);
    if (nRows < 1) { showToast('Reference shape has 0 lines'); return; }
  }

  const superRow = refShape.super_row;
  const refTable = _tableOf(refShape);
  if (superRow == null) {
    showToast('Selected shape is not in a lattice row — run Lattice detect first'); return;
  }

  // Build target list — same super_row, not the anchor, optionally filtered by column
  const colSet  = _parseColSet(document.getElementById('ocr-anchor-cols').value);
  const anchorCol = pageData.shapes[selIdx].super_column;
  const targets = [];
  pageData.shapes.forEach((s, i) => {
    if (i === selIdx) return;
    if (s.super_row !== superRow || _tableOf(s) !== refTable) return;
    if (colSet && !colSet(s.super_column)) return;        // column filter
    if (s.super_column === anchorCol) return;             // skip anchor column even if listed
    targets.push(i);
  });
  if (!targets.length) { showToast('No target shapes in same lattice row'); return; }

  const btn     = document.getElementById('ocr-btn');
  const stopBtn = document.getElementById('ocr-stop-btn');
  btn.disabled = true; btn.textContent = '…';
  stopBtn.style.display = 'inline-block';
  batchAbort = false;

  showToast(`Anchored OCR · ${nRows} rows → ${targets.length} shapes`);

  try {
    for (const idx of targets) {
      if (batchAbort) break;
      try { await _ocrAnchoredOne(idx, nRows, refIdx); }
      catch(e) { showToast(`Shape ${idx}: ${e.message}`); }
    }
  } finally {
    refreshDiag(); drawOverlay(); updatePanel();
    btn.disabled = false; btn.textContent = '▶ Run';
    stopBtn.style.display = 'none';
    batchAbort = false;
  }
}

async function _ocrAnchoredOne(shapeIdx, nRows, refIdx = -1) {
  const shape = pageData.shapes[shapeIdx];
  const spts  = shape.points;
  const sx1   = Math.min(spts[0][0], spts[1][0]);
  const sy1   = Math.min(spts[0][1], spts[1][1]);
  const sx2   = Math.max(spts[0][0], spts[1][0]);
  const pad   = 4, imgW = pageData.imageWidth || 99999;
  const cropOriginX = Math.max(0, sx1 - pad);
  const cropOriginY = Math.max(0, sy1 - pad);
  const cropRight   = Math.min(imgW, sx2 + pad);

  const params = new URLSearchParams({
    folder, stem: pages[pageIdx].stem, idx: shapeIdx, n_rows: nRows, ref_idx: refIdx,
  });
  const r = await fetch(`${API}/api/page/shape/ocr/easyocr/anchored?${params}`, {method: 'POST'});
  if (!r.ok) { const _m = await r.json().catch(() => ({})); throw new Error(_m.detail || r.status); }

  const reader = r.body.getReader(), decoder = new TextDecoder();
  let buffer = '';
  outer: while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    const chunks = buffer.split('\n\n'); buffer = chunks.pop();
    for (const chunk of chunks) {
      if (!chunk.startsWith('data: ')) continue;
      let msg; try { msg = JSON.parse(chunk.slice(6)); } catch { continue; }
      if (msg.type === 'error') { reader.cancel(); throw new Error(msg.error); }
      if (msg.type === 'lines_detected') {
        llmProgress = {cropOriginX, cropOriginY, cropRight,
                       lines: msg.lines, activeRow: -1, emptyRows: null};
        drawOverlay();
      } else if (msg.type === 'row_result') {
        llmProgress.activeRow = msg.row; drawOverlay();
        if (batchAbort) { reader.cancel(); break outer; }
      } else if (msg.type === 'done') {
        pageData.shapes[shapeIdx].tesseract_output = {
          ocr_text: msg.ocr_text, mean_conf: msg.mean_conf ?? null,
          engine: 'easyocr', mode: 'anchored',
        };
        await _refreshShapeRowStruct(shapeIdx);
        if (shapeIdx === selIdx) updatePanel();
        refreshDiag();
      }
    }
    if (batchAbort) { reader.cancel(); break; }
  }
  llmProgress = null;
  drawOverlay();
}

// ── LLM Cleaner ───────────────────────────────────────────────────────────────
const LLM_PROMPTS = {
  'image':     'Transcribe the text in this image exactly as it appears. Preserve line breaks. Return only the transcribed text.',
  'image+ocr': 'Here is a cell image and its OCR output. Correct any OCR errors using the image as the reference. Preserve line breaks. Return only the corrected text.',
  'ocr':       'Clean up the following OCR text. Fix recognition errors and preserve line breaks. Return only the corrected text.',
  'linebyline':'Is this a number or a dash/missing value? Return only the number or a dash, no other text.',
};

function updateLlmPrompt() {
  const mode = document.getElementById('llm-mode').value;
  const el   = document.getElementById('llm-prompt');
  const isDefault = Object.values(LLM_PROMPTS).includes(el.value.trim()) || !el.value.trim();
  if (isDefault) el.value = LLM_PROMPTS[mode] || '';
  document.getElementById('llm-cellheight-wrap').style.display    = mode === 'linebyline' ? 'flex' : 'none';
  document.getElementById('llm-anchor-source-wrap').style.display = mode === 'anchored'   ? 'flex' : 'none';
  document.getElementById('llm-anchor-cols-wrap').style.display   = mode === 'anchored'   ? 'flex' : 'none';
}

// ── Empty row detection ───────────────────────────────────────────────────────
// Returns a Set of row indices whose pixel content is essentially blank (no ink).
//
// Strategy: scan texture / grid lines produce a noisy baseline that varies by
// image, so a fixed luminance threshold is unreliable.  Instead we:
//   1. Sample the whole cell image and compute an Otsu threshold — this finds
//      the natural ink/background split for THIS specific scan.
//   2. Trim BORDER_PAD pixels from the top+bottom of each band to exclude the
//      horizontal table-border line.
//   3. Count pixels below the Otsu threshold in the trimmed interior.
//   4. If the count is below MIN_INK_PX (absolute, not ratio) → empty.
//      A single dash stroke ≈ 20-40 ink pixels; empty bands ≈ 0-5.
function _otsuThreshold(data) {
  // data: raw RGBA pixel array; returns the optimal binarisation threshold (0-255).
  const hist = new Int32Array(256);
  const n    = data.length >> 2;          // pixel count
  for (let p = 0; p < data.length; p += 4) {
    hist[Math.round(0.299 * data[p] + 0.587 * data[p+1] + 0.114 * data[p+2])]++;
  }
  let sum = 0;
  for (let i = 0; i < 256; i++) sum += i * hist[i];
  let sumB = 0, wB = 0, maxVar = 0, t = 128;
  for (let i = 0; i < 256; i++) {
    wB += hist[i]; if (!wB) continue;
    const wF = n - wB; if (!wF) break;
    sumB += i * hist[i];
    const mB = sumB / wB, mF = (sum - sumB) / wF;
    const v  = wB * wF * (mB - mF) ** 2;
    if (v > maxVar) { maxVar = v; t = i; }
  }
  return t;
}

async function _classifyEmptyRowBands(lines) {
  const BORDER_PAD = 3;    // px trimmed top+bottom to exclude border lines
  const MIN_INK_PX = 1;    // ink pixels below Otsu threshold → "not empty"
                            // (dash ≈ 15-40 px; empty ≈ 0 px; text ≈ 30-300 px)

  // Use the shadow (line-erased) cell crop so table borders don't pollute the count
  const shadowUrl = `${API}/api/cell?folder=${encodeURIComponent(folder)}`
                  + `&stem=${encodeURIComponent(pages[pageIdx].stem)}`
                  + `&idx=${selIdx}&shadow=true`;

  const shadowImg = await new Promise(resolve => {
    const im = new Image();
    im.crossOrigin = 'anonymous';
    im.onload  = () => resolve(im);
    im.onerror = () => resolve(null);
    im.src = shadowUrl;
  });
  if (!shadowImg) return new Set();

  const natW = shadowImg.naturalWidth, natH = shadowImg.naturalHeight;
  const off  = document.createElement('canvas');
  off.width  = natW; off.height = natH;
  const ctx  = off.getContext('2d', { willReadFrequently: true });
  try { ctx.drawImage(shadowImg, 0, 0); } catch { return new Set(); }

  // Otsu threshold from the whole shadow cell image.
  // Cap at 180: if Otsu fires above that, the image has no clear ink/paper split
  // (very clean shadow with no foreground), and using a high threshold would count
  // background texture as ink, giving false "non-empty" classifications.
  let fullData;
  try { fullData = ctx.getImageData(0, 0, natW, natH).data; } catch { return new Set(); }
  const otsu = Math.min(_otsuThreshold(fullData), 180);

  const emptyRows = new Set();
  const log = [];
  lines.forEach(([top, bottom], i) => {
    const y  = Math.min(natH - 1, Math.max(0, top  + BORDER_PAD));
    const y2 = Math.min(natH,     Math.max(y + 1,  bottom - BORDER_PAD));
    const h  = y2 - y;
    if (h <= 0) { log.push(`row${i+1}: thin`); return; }
    let data;
    try { data = ctx.getImageData(0, y, natW, h).data; } catch { log.push(`row${i+1}: ERR`); return; }
    let ink = 0;
    for (let p = 0; p < data.length; p += 4) {
      if (0.299 * data[p] + 0.587 * data[p+1] + 0.114 * data[p+2] < otsu) ink++;
    }
    log.push(`row${i+1}: ${ink}px${ink < MIN_INK_PX ? ' ← EMPTY' : ''}`);
    if (ink < MIN_INK_PX) emptyRows.add(i);
  });
  console.log(`[emptyRows] shadow otsu=${otsu} minInk=${MIN_INK_PX} found=${emptyRows.size}/${lines.length}\n` + log.join('\n'));
  return emptyRows;
}

// ── Row overlay on cell crop ──────────────────────────────────────────────────
async function drawRowOverlay(lines, activeRow) {
  const canvas = document.getElementById('row-canvas');
  const img    = document.getElementById('cell-crop');
  if (!lines || !lines.length) { canvas.style.display = 'none'; return; }

  // Position canvas exactly over the img element
  canvas.style.display = 'block';
  canvas.style.top     = img.offsetTop  + 'px';
  canvas.style.left    = img.offsetLeft + 'px';
  canvas.style.width   = img.clientWidth  + 'px';
  canvas.style.height  = img.clientHeight + 'px';

  // Draw at native resolution so coordinates match the crop pixel space
  const natW = img.naturalWidth  || img.clientWidth;
  const natH = img.naturalHeight || img.clientHeight;
  canvas.width  = natW;
  canvas.height = natH;

  // Classify empty bands once per run and cache in both llmProgress (for the
  // SVG overlay during the run) and lastEmptyRows (survives after llmProgress
  // is cleared, so the cell-crop view stays correct after the run ends).
  if (llmProgress && !llmProgress.emptyRows) {
    llmProgress.emptyRows = await _classifyEmptyRowBands(lines);
    lastEmptyRows = llmProgress.emptyRows;          // persist for post-run redraws
  }
  const emptyRows = llmProgress?.emptyRows ?? lastEmptyRows;

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, natW, natH);

  const fontSize = Math.max(9, Math.min(16, Math.round((lines[0][1] - lines[0][0]) * 0.55)));
  ctx.font = `bold ${fontSize}px sans-serif`;

  lines.forEach(([top, bottom], i) => {
    const active  = i === activeRow;
    const isEmpty = emptyRows.has(i);
    if (active) {
      ctx.fillStyle   = 'rgba(255,100,0,0.22)';
      ctx.strokeStyle = 'rgba(255,100,0,0.95)';
      ctx.lineWidth   = 2;
    } else if (isEmpty) {
      ctx.fillStyle   = 'rgba(220,30,30,0.12)';
      ctx.strokeStyle = 'rgba(220,30,30,0.80)';
      ctx.lineWidth   = 1;
    } else {
      ctx.fillStyle   = 'rgba(0,160,255,0.07)';
      ctx.strokeStyle = 'rgba(0,160,255,0.45)';
      ctx.lineWidth   = 1;
    }
    ctx.fillRect(0, top, natW, bottom - top);
    ctx.strokeRect(0.5, top + 0.5, natW - 1, bottom - top - 1);

    // Row number label
    const label = String(i + 1);
    const mid   = top + (bottom - top) / 2;
    ctx.fillStyle    = active ? 'rgba(255,100,0,0.9)' : isEmpty ? 'rgba(200,20,20,0.85)' : 'rgba(0,100,200,0.75)';
    ctx.textBaseline = 'middle';
    ctx.textAlign    = 'right';
    ctx.fillText(label, natW - 3, mid);
  });
}

// ── Line-numbered textareas ───────────────────────────────────────────────────
function updateLineNums(taId, numsId) {
  const ta = document.getElementById(taId);
  const ln = document.getElementById(numsId);
  if (!ta || !ln) return;
  const count = (ta.value.match(/\n/g) || []).length + 1;
  if (parseInt(ln.dataset.count) === count) return;   // nothing changed
  ln.dataset.count = count;
  ln.innerHTML = Array.from({length: count}, (_, i) => i + 1).join('<br>');
}

function syncLineNumScroll(taId, numsId) {
  const ta = document.getElementById(taId);
  const ln = document.getElementById(numsId);
  if (ta && ln) ln.scrollTop = ta.scrollTop;
}

function refreshAllLineNums() {
  updateLineNums('f-ocr',        'f-ocr-lines');
  updateLineNums('f-llm-result', 'f-llm-result-lines');
  updateLineNums('f-pdf',        'f-pdf-lines');
  updateLineNums('human-input',  'human-input-lines');
}

// Initialise prompt on first load
document.addEventListener('DOMContentLoaded', () => {
  // restore persisted model/mode
  const savedModel = localStorage.getItem('llm-model');
  const savedMode  = localStorage.getItem('llm-mode');
  if (savedModel) document.getElementById('llm-model').value = savedModel;
  if (savedMode)  document.getElementById('llm-mode').value  = savedMode;
  updateLlmPrompt();   // sets prompt text AND shows/hides cell-height input
  // persist on change
  document.getElementById('llm-model').addEventListener('change', e => localStorage.setItem('llm-model', e.target.value));
  document.getElementById('llm-mode').addEventListener('change',  e => localStorage.setItem('llm-mode',  e.target.value));

  // Scroll sync for line numbers
  [['f-ocr','f-ocr-lines'], ['f-llm-result','f-llm-result-lines'], ['human-input','human-input-lines']]
    .forEach(([taId, lnId]) => {
      const ta = document.getElementById(taId);
      if (ta) ta.addEventListener('scroll', () => syncLineNumScroll(taId, lnId));
    });

  // Live line numbers while typing in the human correction box
  const humanTa = document.getElementById('human-input');
  if (humanTa) humanTa.addEventListener('input', () => updateLineNums('human-input', 'human-input-lines'));
});

// ── LLM Test modal ───────────────────────────────────────────────────────────
let _llmTestAbort = false;

function openLlmTestModal() {
  if (selIdx < 0 || !pages.length) { showToast('Select a shape first'); return; }
  _llmTestAbort = false;
  document.getElementById('llm-test-thead').innerHTML = '';
  document.getElementById('llm-test-tbody').innerHTML = '';
  document.getElementById('llm-test-status').textContent = '';
  document.getElementById('llm-test-prompt-sent').textContent = '';
  document.getElementById('llm-test-prompt-details').open = false;
  document.getElementById('llm-test-modal').style.display = 'flex';
}

function closeLlmTestModal() {
  _llmTestAbort = true;
  document.getElementById('llm-test-modal').style.display = 'none';
}

function stopLlmTest() {
  _llmTestAbort = true;
  document.getElementById('llm-test-status').textContent = 'Stopped.';
}

function _showTestPromptSent(text) {
  const el = document.getElementById('llm-test-prompt-sent');
  if (el) el.textContent = text;
  const det = document.getElementById('llm-test-prompt-details');
  if (det) det.open = true;
}

function _llmTestEsc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function _llmTestSubTable(text) {
  if (!text) return '<span style="color:#555;">—</span>';
  const lines = String(text).split('\n');
  const rows = lines.map(l =>
    `<tr><td style="padding:1px 6px;border-bottom:1px solid #1a3a6e;white-space:pre;font-family:monospace;">${_llmTestEsc(l)}</td></tr>`
  ).join('');
  return `<table style="border-collapse:collapse;width:100%;">${rows}</table>`;
}

function _levenshtein(a, b) {
  const m = a.length, n = b.length;
  const dp = Array.from({length: m + 1}, (_, i) => {
    const row = new Array(n + 1).fill(0);
    row[0] = i;
    return row;
  });
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = a[i-1] === b[j-1] ? dp[i-1][j-1]
        : 1 + Math.min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1]);
  return dp[m][n];
}

function _lltLineDist(a, b) {
  // Treat dashes (empty cell markers) as zero
  const norm = s => /^[-–—]+$/.test(s.trim()) ? '0' : s;
  a = norm(a); b = norm(b);
  // Numeric comparison (handle European decimal comma)
  const fa = parseFloat(a.replace(',', '.').replace(/\s/g, ''));
  const fb = parseFloat(b.replace(',', '.').replace(/\s/g, ''));
  if (isFinite(fa) && isFinite(fb)) return (fa - fb) ** 2;
  // Text: normalized edit distance squared
  if (a === b) return 0;
  const maxLen = Math.max(a.length, b.length, 1);
  return (_levenshtein(a, b) / maxLen) ** 2;
}

function _lltExtractText(td) {
  const rows = td.querySelectorAll('tr');
  if (!rows.length) return td.textContent.trim();
  return Array.from(rows).map(r => r.textContent).join('\n');
}

function _lltMsd(humanLines, runText) {
  const runLines = runText.split('\n');
  const len = Math.max(humanLines.length, runLines.length);
  let sum = 0;
  for (let i = 0; i < len; i++) {
    const h = (humanLines[i] ?? '').trim();
    const r = (runLines[i] ?? '').trim();
    sum += _lltLineDist(h, r);
  }
  return len > 0 ? sum / len : 0;
}

const _LLT_TD = 'style="vertical-align:top;border:1px solid #1a3a6e;padding:6px;min-width:110px;max-width:260px;"';
const _LLT_TH = 'style="border:1px solid #1a3a6e;padding:5px 8px;background:#0f1e3a;color:#aaa;font-weight:600;white-space:nowrap;text-align:left;"';

async function runLlmTest() {
  if (selIdx < 0 || !pages.length) return;
  _llmTestAbort = false;

  const N          = Math.max(1, Math.min(30, parseInt(document.getElementById('llm-test-n').value) || 5));
  const model      = document.getElementById('llm-model').value;
  const mode       = document.getElementById('llm-mode').value;
  const prompt     = document.getElementById('llm-prompt').value.trim();
  const cellHeight = parseInt(document.getElementById('llm-cell-height').value) || 28;
  const useShadow  = document.getElementById('llm-use-shadow').checked;
  const stem       = pages[pageIdx].stem;
  const statusEl   = document.getElementById('llm-test-status');

  if (!prompt) { showToast('Prompt is empty'); return; }

  // Show the exact prompt that will be sent (user text + server-side guard suffix)
  const _EMPTY_CELL_GUARD = '\nIf the cell is empty, contains only a dash, or the image shows no readable content, return exactly -.';
  _showTestPromptSent(prompt + _EMPTY_CELL_GUARD);

  const shape = pageData.shapes[selIdx];
  if (!shape?.points?.length) return;
  const pts = shape.points;
  const sx1 = Math.min(pts[0][0], pts[1][0]);
  const sy1 = Math.min(pts[0][1], pts[1][1]);
  const sx2 = Math.max(pts[0][0], pts[1][0]);
  const sy2 = Math.max(pts[0][1], pts[1][1]);
  const pad = 6;

  const humanText = shape.human_output?.human_corrected_text || null;
  const _LLT_TH_HUMAN = `style="border:1px solid #1a3a6e;padding:5px 8px;background:#0a2010;color:#6f6;font-weight:600;white-space:nowrap;text-align:left;"`;

  // Build header
  const thead = document.getElementById('llm-test-thead');
  const tbody = document.getElementById('llm-test-tbody');
  let hdr = `<tr><th ${_LLT_TH}>Image</th>`;
  if (humanText) hdr += `<th ${_LLT_TH_HUMAN}>Human</th>`;
  hdr += `<th ${_LLT_TH}>OCR</th>`;
  for (let i = 0; i < N; i++) hdr += `<th ${_LLT_TH}>Run ${i+1}</th>`;
  hdr += '</tr>';
  thead.innerHTML = hdr;

  // OCR text — prefer easyocr_output (line-by-line aware), fall back to tesseract
  const ocrText = shape.easyocr_output?.ocr_text || shape.tesseract_output?.ocr_text || '';

  // Build row with placeholders
  let row = `<tr>`;
  row += `<td ${_LLT_TD} id="llt-img-cell"><em style="color:#666;">loading…</em></td>`;
  if (humanText) row += `<td ${_LLT_TD} style="background:#050f05;">${_llmTestSubTable(humanText)}</td>`;
  row += `<td ${_LLT_TD}>${_llmTestSubTable(ocrText)}</td>`;
  for (let i = 0; i < N; i++)
    row += `<td ${_LLT_TD} id="llt-run-${i}"><em style="color:#555;">waiting…</em></td>`;
  row += '</tr>';
  tbody.innerHTML = row;

  // Start loading the full page image in the background; we'll crop + insert AFTER all runs finish
  const _imgDataUrlPromise = new Promise(resolve => {
    const imgUrl = `${API}/api/image?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(stem)}`;
    const fullImg = new Image();
    fullImg.crossOrigin = 'anonymous';
    fullImg.onload = () => {
      const cx = Math.max(0, sx1 - pad);
      const cy = Math.max(0, sy1 - pad);
      const cw = Math.min(fullImg.width,  sx2 + pad) - cx;
      const ch = Math.min(fullImg.height, sy2 + pad) - cy;
      const cv = document.createElement('canvas');
      cv.width = cw; cv.height = ch;
      cv.getContext('2d').drawImage(fullImg, cx, cy, cw, ch, 0, 0, cw, ch);
      resolve(cv.toDataURL('image/jpeg', 0.92));
    };
    fullImg.onerror = () => resolve(null);
    fullImg.src = imgUrl;
  });

  // Run N LLM calls sequentially, dry_run=true so stored result is untouched
  for (let i = 0; i < N; i++) {
    if (_llmTestAbort) break;
    const td = document.getElementById(`llt-run-${i}`);
    if (!td) break;
    statusEl.textContent = `Running ${i+1} / ${N}…`;
    td.innerHTML = '<em style="color:#aaa;">⏳</em>';

    try {
      const params = new URLSearchParams({
        folder, stem, idx: selIdx, model, mode,
        use_shadow: useShadow, dry_run: true,
      });
      let resultText = '';

      if (mode === 'linebyline') {
        params.set('cell_height', cellHeight);
        const r = await fetch(`${API}/api/page/shape/llm/linebyline?${params}`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({prompt}),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        // Drain SSE and collect per-row text
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        const lineTexts = [];
        outer: while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          buf += dec.decode(value, {stream: true});
          const chunks = buf.split('\n\n'); buf = chunks.pop();
          for (const chunk of chunks) {
            if (_llmTestAbort) { reader.cancel(); break outer; }
            if (!chunk.startsWith('data: ')) continue;
            try {
              const msg = JSON.parse(chunk.slice(6));
              if (msg.type === 'row_result') lineTexts.push(msg.text);
              if (msg.type === 'done') { resultText = msg.response; }
            } catch {}
          }
        }
        if (!resultText) resultText = lineTexts.join('\n');
      } else {
        const r = await fetch(`${API}/api/page/shape/llm?${params}`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({prompt}),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        resultText = data.response;
      }

      td.innerHTML = _llmTestSubTable(resultText);
    } catch(e) {
      if (td) td.innerHTML = `<em style="color:#e94560;">${_llmTestEsc(String(e))}</em>`;
    }
  }

  statusEl.textContent = _llmTestAbort ? 'Stopped.' : `Done — ${N} run${N>1?'s':''} completed.`;

  // All columns are now in the DOM — measure the final row height and insert the image
  const dataUrl = await _imgDataUrlPromise;
  const imgTd   = document.getElementById('llt-img-cell');
  if (dataUrl && imgTd) {
    const tds  = imgTd.closest('tr')?.querySelectorAll('td');
    let maxH = 0;
    tds?.forEach((td, i) => { if (i > 0) maxH = Math.max(maxH, td.clientHeight); });
    const im = document.createElement('img');
    im.src = dataUrl;
    if (maxH > 4) {
      im.style.cssText = `display:block;height:${maxH}px;width:auto;max-width:100%;`;
    } else {
      im.style.cssText = 'display:block;width:100%;height:auto;';
    }
    imgTd.innerHTML = '';
    imgTd.appendChild(im);
  }

  // MSD vs. human correction
  if (humanText && !_llmTestAbort) {
    const humanLines = humanText.split('\n');
    // Fixed columns before run columns: Image + (Human?) + OCR = 2 or 3
    const fixedCols = humanText ? 3 : 2;
    const table = document.getElementById('llm-test-table');
    const tfoot = table.tFoot || table.createTFoot();
    tfoot.innerHTML = '';

    const msds = [];
    const labelCols = humanText ? 2 : 1; // Image + (Human?)
    let footRow = `<tr>`;
    footRow += `<td colspan="${labelCols}" style="padding:5px 8px;color:#888;font-style:italic;border:1px solid #1a3a6e;text-align:right;">MSD vs. human:</td>`;
    // OCR MSD
    const ocrMsd = _lltMsd(humanLines, ocrText);
    const ocrCol = ocrMsd < 0.05 ? '#6f6' : ocrMsd < 0.5 ? '#fa0' : '#f64';
    footRow += `<td style="padding:5px 8px;border:1px solid #1a3a6e;text-align:center;font-weight:700;font-family:monospace;color:${ocrCol};">${ocrMsd.toFixed(4)}</td>`;
    // Run MSDs
    for (let i = 0; i < N; i++) {
      const td = document.getElementById(`llt-run-${i}`);
      const runText = td ? _lltExtractText(td) : '';
      const msd = _lltMsd(humanLines, runText);
      msds.push(msd);
      const col = msd < 0.05 ? '#6f6' : msd < 0.5 ? '#fa0' : '#f64';
      footRow += `<td style="padding:5px 8px;border:1px solid #1a3a6e;text-align:center;font-weight:700;font-family:monospace;color:${col};">${msd.toFixed(4)}</td>`;
    }
    footRow += '</tr>';
    tfoot.innerHTML = footRow;

    const meanMsd = msds.reduce((a, b) => a + b, 0) / msds.length;
    const bestRun = msds.indexOf(Math.min(...msds)) + 1;
    statusEl.textContent += `  ·  Mean MSD: ${meanMsd.toFixed(4)}  ·  Best: Run ${bestRun}`;
  }
}

// ── LLM send / line-by-line ───────────────────────────────────────────────────
