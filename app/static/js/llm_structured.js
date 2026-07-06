// Split from index.html — classic scripts share the global scope;
// load order in index.html is load-bearing. See knowledge_base/02_architecture.md.
// ── Structured (JSON) extraction ────────────────────────────────────────────
let _projSchemas = [];          // [{name, schema}]
let _structTreeOn = false;

function _jsonModeOn() { return document.getElementById('llm-json-toggle')?.checked; }

function onJsonModeToggle() {
  const on = _jsonModeOn();
  document.getElementById('llm-json-area').style.display = on ? 'flex' : 'none';
  if (on && !_projSchemas.length) reloadSchemas();
  if (selIdx >= 0) renderStructured(pageData.shapes[selIdx]);
}

// Batch-modal JSON mode (mirrors the inspector toggle, shares _projSchemas)
function _fillBatchSchemaSelect() {
  const sel = document.getElementById('batch-llm-schema');
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = `<option value="">— select schema —</option>`
    + _projSchemas.map(s => `<option value="${_escHtml(s.name)}">${_escHtml(s.name)}</option>`).join('');
  if ([..._projSchemas.map(s => s.name), ''].includes(cur)) sel.value = cur;
}
function onBatchLlmJsonToggle() {
  const on = document.getElementById('batch-llm-json').checked;
  document.getElementById('batch-llm-json-row').style.display = on ? 'flex' : 'none';
  if (on) { if (_projSchemas.length) _fillBatchSchemaSelect(); else reloadSchemas().then(_fillBatchSchemaSelect); }
}

async function reloadSchemas() {
  try {
    const r = await fetch(`${API}/api/schemas?folder=${encodeURIComponent(folder)}`);
    _projSchemas = r.ok ? ((await r.json()).schemas || []) : [];
  } catch (e) { _projSchemas = []; }
  const sel = document.getElementById('llm-schema-select');
  const cur = sel.value;
  sel.innerHTML = `<option value="">— select schema —</option>`
    + _projSchemas.map(s => `<option value="${_escHtml(s.name)}">${_escHtml(s.name)}</option>`).join('')
    + `<option value="__new__">+ new schema…</option>`;
  if ([..._projSchemas.map(s => s.name), ''].includes(cur)) sel.value = cur;
}

function onSchemaSelect() {
  const sel = document.getElementById('llm-schema-select');
  const v = sel.value;
  const ed = document.getElementById('llm-schema-editor');
  const nm = document.getElementById('llm-schema-name');
  if (v === '__new__') {
    document.getElementById('llm-schema-editor-wrap').style.display = 'flex';
    ed.value = '{\n  "type": "object",\n  "additionalProperties": false,\n  "required": [],\n  "properties": {}\n}';
    nm.value = '';
    sel.value = '';
  } else if (v) {
    const s = _projSchemas.find(x => x.name === v);
    if (s) { ed.value = JSON.stringify(s.schema, null, 2); nm.value = s.name; }
  }
  _validateSchemaEditor();
}

function toggleSchemaEditor() {
  const w = document.getElementById('llm-schema-editor-wrap');
  w.style.display = (w.style.display === 'none' || !w.style.display) ? 'flex' : 'none';
}

function _activeSchemaObj() {
  const txt = (document.getElementById('llm-schema-editor').value || '').trim();
  if (!txt) return null;
  try { return JSON.parse(txt); } catch (e) { return null; }
}

function _validateSchemaEditor() {
  const txt = (document.getElementById('llm-schema-editor').value || '').trim();
  const st = document.getElementById('llm-schema-status');
  const ed = document.getElementById('llm-schema-editor');
  if (!txt) { st.textContent = ''; ed.style.borderColor = '#1a3a6e'; return; }
  try { JSON.parse(txt); st.textContent = '✓ valid JSON schema'; st.style.color = '#86efac'; ed.style.borderColor = '#22c55e'; }
  catch (e) { st.textContent = '✕ ' + e.message; st.style.color = '#fca5a5'; ed.style.borderColor = '#e94560'; }
}

function formatSchemaEditor() {
  const o = _activeSchemaObj();
  if (o) document.getElementById('llm-schema-editor').value = JSON.stringify(o, null, 2);
  _validateSchemaEditor();
}

async function saveSchema() {
  const o = _activeSchemaObj();
  if (!o) { showToast('Schema is not valid JSON'); return; }
  let name = (document.getElementById('llm-schema-name').value || '').trim();
  if (!name) { showToast('Give the schema a name'); return; }
  try {
    const r = await fetch(`${API}/api/schemas?folder=${encodeURIComponent(folder)}&name=${encodeURIComponent(name)}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(o),
    });
    if (!r.ok) { showToast(`Save failed: ${r.status}`); return; }
    const d = await r.json();
    await reloadSchemas();
    document.getElementById('llm-schema-select').value = d.name;
    showToast(`Schema "${d.name}" saved`);
  } catch (e) { showToast('Save error: ' + e.message); }
}

async function deleteSchema() {
  const name = (document.getElementById('llm-schema-name').value || '').trim();
  if (!name || !confirm(`Delete schema "${name}"?`)) return;
  try {
    await fetch(`${API}/api/schemas?folder=${encodeURIComponent(folder)}&name=${encodeURIComponent(name)}`, {method: 'DELETE'});
    await reloadSchemas();
    document.getElementById('llm-schema-editor').value = '';
    document.getElementById('llm-schema-name').value = '';
    _validateSchemaEditor();
    showToast(`Schema "${name}" deleted`);
  } catch (e) { showToast('Delete error: ' + e.message); }
}

// Lightweight schema conformance check (type / required, recursive) for live feedback
function _schemaIssues(obj, schema, path = '') {
  const out = [];
  if (!schema || typeof schema !== 'object') return out;
  const here = path || '(root)';
  const t = schema.type;
  if (t === 'object') {
    if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) { out.push(`${here}: expected object`); return out; }
    (schema.required || []).forEach(k => { if (obj[k] === undefined) out.push(`missing: ${path ? path + '.' : ''}${k}`); });
    const props = schema.properties || {};
    for (const k in props) if (obj[k] !== undefined) out.push(..._schemaIssues(obj[k], props[k], (path ? path + '.' : '') + k));
  } else if (t === 'array') {
    if (!Array.isArray(obj)) out.push(`${here}: expected array`);
    else if (schema.items) obj.forEach((el, i) => out.push(..._schemaIssues(el, schema.items, `${path}[${i}]`)));
  } else if (typeof t === 'string') {
    const ok = (t === 'string' && typeof obj === 'string')
      || (t === 'number' && typeof obj === 'number')
      || (t === 'integer' && Number.isInteger(obj))
      || (t === 'boolean' && typeof obj === 'boolean')
      || (t === 'null' && obj === null);
    if (!ok) out.push(`${here}: should be ${t}`);
  }
  return out;
}

function _jsonHighlight(obj) {
  const s = _escHtml(JSON.stringify(obj, null, 2));
  return s
    .replace(/(&quot;(?:\\.|[^&]|&(?!quot;))*?&quot;)(\s*:)/g, '<span style="color:#9cdcfe">$1</span>$2')
    .replace(/:\s*(&quot;(?:\\.|[^&]|&(?!quot;))*?&quot;)/g, ': <span style="color:#ce9178">$1</span>')
    .replace(/\b(-?\d+\.?\d*)\b/g, '<span style="color:#b5cea8">$1</span>')
    .replace(/\b(true|false|null)\b/g, '<span style="color:#569cd6">$1</span>');
}

// Show/populate the Structured group for a shape
function renderStructured(shape) {
  const fg = document.getElementById('fg-structured');
  if (!fg) return;
  const st = shape?.structured;
  if (!st) { fg.style.display = _jsonModeOn() ? 'flex' : 'none';
             if (_jsonModeOn()) { document.getElementById('struct-editor').value = ''; document.getElementById('struct-status').textContent = 'No structured record yet — run the LLM in JSON mode.'; }
             return; }
  fg.style.display = 'flex';
  const rec = (st.data !== undefined && st.data !== null) ? st.data : st.llm;
  document.getElementById('struct-editor').value = JSON.stringify(rec ?? {}, null, 2);
  _validateStructEditor();
}

function _validateStructEditor() {
  const ed = document.getElementById('struct-editor');
  const st = document.getElementById('struct-status');
  const txt = (ed.value || '').trim();
  if (!txt) { st.textContent = ''; ed.style.borderColor = '#1a3a6e'; return; }
  let obj;
  try { obj = JSON.parse(txt); }
  catch (e) { st.innerHTML = `<span style="color:#fca5a5;">✕ ${_escHtml(e.message)}</span>`; ed.style.borderColor = '#e94560'; return; }
  ed.style.borderColor = '#22c55e';
  // schema conformance (against the active schema, if any)
  const schema = _jsonModeOn() ? _activeSchemaObj() : null;
  const issues = schema ? _schemaIssues(obj, schema) : [];
  if (issues.length) st.innerHTML = `<span style="color:#eab308;">✓ valid JSON · ⚠ ${issues.length} schema issue(s): ${_escHtml(issues.slice(0, 4).join('; '))}${issues.length > 4 ? '…' : ''}</span>`;
  else               st.innerHTML = `<span style="color:#86efac;">✓ valid JSON${schema ? ' · matches schema' : ''}</span>`;
  if (_structTreeOn) document.getElementById('struct-tree').innerHTML = _jsonHighlight(obj);
}

function formatStructured() {
  const txt = (document.getElementById('struct-editor').value || '').trim();
  try { document.getElementById('struct-editor').value = JSON.stringify(JSON.parse(txt), null, 2); } catch (e) {}
  _validateStructEditor();
}

function toggleStructTree() {
  _structTreeOn = !_structTreeOn;
  const tree = document.getElementById('struct-tree');
  tree.style.display = _structTreeOn ? 'block' : 'none';
  document.getElementById('struct-tree-btn').classList.toggle('active', _structTreeOn);
  if (_structTreeOn) { try { tree.innerHTML = _jsonHighlight(JSON.parse(document.getElementById('struct-editor').value || '{}')); } catch (e) { tree.textContent = '(invalid JSON)'; } }
}

async function saveStructured() {
  if (selIdx < 0) return;
  const txt = (document.getElementById('struct-editor').value || '').trim();
  let obj; try { obj = JSON.parse(txt); } catch (e) { showToast('Not valid JSON — fix it first'); return; }
  const shape = pageData.shapes[selIdx];
  const schema_name = shape.structured?.schema_name || document.getElementById('llm-schema-select')?.value || null;
  try {
    const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: selIdx});
    const r = await fetch(`${API}/api/page/shape/structured?${params}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({data: obj, schema_name}),
    });
    if (!r.ok) { showToast(`Save failed: ${r.status}`); return; }
    shape.structured = (await r.json()).structured;
    drawOverlay();
    showToast('✓ Structured record saved');
  } catch (e) { showToast('Save error: ' + e.message); }
}

async function runLlm() {
  if (selIdx < 0 || !pages.length) return;
  const mode = document.getElementById('llm-mode').value;
  // JSON / structured extraction — whole-annotation, single shape only
  if (_jsonModeOn()) { runLlmJson(); return; }
  // Anchored uses selected shape as reference — bypass multi-select batch
  if (mode === 'anchored') { runLlmAnchored(); return; }
  if (selSet.size > 1) {
    await _runLlmBatch([...selSet].sort((a, b) => a - b));
    return;
  }
  if (mode === 'linebyline') { runLlmLineByLine(); return; }

  const myIdx = selIdx;  // capture now — user may click away during await
  const btn = document.getElementById('llm-btn');
  btn.disabled = true; btn.textContent = '…';
  try {
    const model     = document.getElementById('llm-model').value;
    const prompt    = document.getElementById('llm-prompt').value.trim();
    const useShadow = document.getElementById('llm-use-shadow').checked;
    if (!prompt) { showToast('Prompt is empty'); return; }
    const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: myIdx, model, mode, use_shadow: useShadow});
    const r = await fetch(`${API}/api/page/shape/llm?${params}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt}),
    });
    if (!r.ok) { showToast(`LLM error ${r.status}: ${(await r.text()).slice(0, 120)}`); return; }
    const data = await r.json();
    console.log('[LLM] response:', JSON.stringify(data.response)?.slice(0, 120),
                '| tokens:', data.tokens_in, '→', data.tokens_out,
                '| myIdx:', myIdx, '| selIdx now:', selIdx,
                '| label:', pageData.shapes[myIdx]?.label);
    if (!pageData.shapes[myIdx]) { showToast('LLM done (shape gone)'); return; }
    pageData.shapes[myIdx].openai_output = {
      response: data.response, model: data.model,
      mode: data.mode, timestamp: data.timestamp,
      tokens_in: data.tokens_in, tokens_out: data.tokens_out,
    };
    if (pageData.shapes[myIdx].row_struct) await _refreshShapeRowStruct(myIdx);
    if (myIdx === selIdx) {
      // Still on the same shape — refresh panel
      const el = document.getElementById('f-llm-result');
      console.log('[LLM] textarea before updatePanel:', JSON.stringify(el?.value)?.slice(0, 80));
      updatePanel();
      console.log('[LLM] textarea after  updatePanel:', JSON.stringify(el?.value)?.slice(0, 80));
    }
    refreshDiag(); drawOverlay();
    showToast('LLM done' + (myIdx !== selIdx ? ' (navigated away — result saved)' : ''));
  } finally {
    btn.disabled = false; btn.textContent = '▶ Send';
  }
}

async function runLlmJson() {
  const schema = _activeSchemaObj();
  if (!schema) { showToast('Select or fix the schema (must be valid JSON)'); return; }
  let mode = document.getElementById('llm-mode').value;
  if (mode === 'linebyline' || mode === 'anchored') mode = 'image';   // JSON = whole-annotation
  const myIdx = selIdx;
  const btn = document.getElementById('llm-btn');
  btn.disabled = true; btn.textContent = '…';
  try {
    const model     = document.getElementById('llm-model').value;
    const prompt    = document.getElementById('llm-prompt').value.trim();
    const useShadow = document.getElementById('llm-use-shadow').checked;
    const schema_name = document.getElementById('llm-schema-select').value
                     || document.getElementById('llm-schema-name').value || null;
    if (!prompt) { showToast('Prompt is empty'); return; }
    const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: myIdx, model, mode, use_shadow: useShadow});
    const r = await fetch(`${API}/api/page/shape/llm?${params}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt, json_schema: schema, schema_name}),
    });
    if (!r.ok) { showToast(`LLM error ${r.status}: ${(await r.text()).slice(0, 160)}`); return; }
    const data = await r.json();
    if (!pageData.shapes[myIdx]) { showToast('Done (shape gone)'); return; }
    const sh = pageData.shapes[myIdx];
    const prev = sh.structured || {};
    sh.structured = {
      schema_name: data.schema_name, llm: data.structured,
      data: prev.edited ? prev.data : data.structured,   // keep human edits across re-runs
      edited: !!prev.edited, model: data.model, ts: data.timestamp,
    };
    if (myIdx === selIdx) renderStructured(sh);
    drawOverlay();
    showToast('Structured extraction done' + (myIdx !== selIdx ? ' (saved)' : ''));
  } finally {
    btn.disabled = false; btn.textContent = '▶ Send';
  }
}

function stopLlmLineByLine() {
  if (llmAbortCtrl) { llmAbortCtrl.abort(); llmAbortCtrl = null; }
}

function stopBatch() {
  batchAbort = true;
}

// ── Panel section fold/unfold ────────────────────────────────────────────────
function toggleFg(id) {
  const body = document.getElementById(id + '-body');
  const btn  = document.getElementById(id + '-toggle');
  if (!body) return;
  const collapsed = body.classList.toggle('fg-body-collapsed');
  if (btn) btn.textContent = collapsed ? '▸' : '▾';
}

// ── Clear OCR / LLM results ──────────────────────────────────────────────────
function _clearTargets() {
  // Returns the set of shape indices to operate on:
  // multi-selection if >1 selected, otherwise just the focused shape.
  if (selSet.size > 1) return [...selSet];
  if (selIdx >= 0)     return [selIdx];
  return [];
}

async function clearOcr() {
  if (!pageData?.shapes) return;
  const targets = _clearTargets();
  if (!targets.length) return;
  targets.forEach(i => { pageData.shapes[i].tesseract_output = null; _rsClearLayer(pageData.shapes[i], 'ocr'); });
  updatePanel(); drawOverlay();
  await replaceAllShapes();
  showToast(`OCR cleared for ${targets.length} shape${targets.length !== 1 ? 's' : ''}`);
}

async function clearOcrPage() {
  if (!pageData?.shapes?.length) return;
  let n = 0;
  pageData.shapes.forEach(s => { if (s.tesseract_output) { s.tesseract_output = null; _rsClearLayer(s, 'ocr'); n++; } });
  updatePanel(); drawOverlay();
  await replaceAllShapes();
  showToast(`OCR cleared for ${n} shape${n !== 1 ? 's' : ''} on this page`);
}

async function clearLlm() {
  if (!pageData?.shapes) return;
  const targets = _clearTargets();
  if (!targets.length) return;
  targets.forEach(i => { pageData.shapes[i].openai_output = null; _rsClearLayer(pageData.shapes[i], 'llm'); });
  updatePanel(); drawOverlay();
  await replaceAllShapes();
  showToast(`LLM cleared for ${targets.length} shape${targets.length !== 1 ? 's' : ''}`);
}

async function clearLlmPage() {
  if (!pageData?.shapes?.length) return;
  let n = 0;
  pageData.shapes.forEach(s => { if (s.openai_output) { s.openai_output = null; _rsClearLayer(s, 'llm'); n++; } });
  updatePanel(); drawOverlay();
  await replaceAllShapes();
  showToast(`LLM cleared for ${n} shape${n !== 1 ? 's' : ''} on this page`);
}

// ── PDF text layer ────────────────────────────────────────────────────────────

async function runPdfTextLayer() {
  if (!pages.length) return;
  const btn = document.getElementById('pdf-layer-btn');
  btn.disabled = true; btn.textContent = '…';
  try {
    const p = pages[pageIdx];
    const params = new URLSearchParams({ folder, stem: p.stem });
    const r = await fetch(`/api/page/pdf-text-layer?${params}`, { method: 'POST' });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      const msg = err.detail || r.statusText;
      showToast('PDF layer: ' + msg, 4000, 'red');
      // If no PDF source recorded, show a helpful hint
      if (r.status === 400) {
        document.getElementById('f-pdf-meta').textContent = msg;
      }
      return;
    }
    const data = await r.json();
    // Merge pdf_text back into local pageData without a full reload
    data.shapes.forEach((s, i) => {
      if (pageData.shapes[i] !== undefined) {
        pageData.shapes[i].pdf_text = s.pdf_text;
      }
    });
    updatePanel();
    showToast(`PDF text extracted for ${data.updated} shape(s)`);
  } finally {
    btn.disabled = false; btn.textContent = '▶ Extract page';
  }
}

async function runPdfTextLayerAll() {
  if (!pages.length) return;
  const allBtn  = document.getElementById('pdf-layer-all-btn');
  const pageBtn = document.getElementById('pdf-layer-btn');
  allBtn.disabled = true; allBtn.textContent = '…';
  pageBtn.disabled = true;
  let done = 0, errors = 0;
  try {
    for (let i = 0; i < pages.length; i++) {
      if (batchAbort) break;
      const p = pages[i];
      allBtn.textContent = `${i + 1}/${pages.length}`;
      const params = new URLSearchParams({ folder, stem: p.stem });
      try {
        const r = await fetch(`/api/page/pdf-text-layer?${params}`, { method: 'POST' });
        if (!r.ok) { errors++; continue; }
        const data = await r.json();
        done += data.updated || 0;
        // If it's the current page, merge results into local state
        if (i === pageIdx) {
          data.shapes.forEach((s, si) => {
            if (pageData.shapes[si] !== undefined) pageData.shapes[si].pdf_text = s.pdf_text;
          });
          updatePanel();
        }
      } catch (_) { errors++; }
    }
    const msg = errors ? `PDF text done: ${done} shape(s), ${errors} page error(s)` : `PDF text extracted: ${done} shape(s) across ${pages.length} pages`;
    showToast(msg, 4000, errors ? 'orange' : undefined);
  } finally {
    allBtn.disabled = false; allBtn.textContent = '▶ All pages';
    pageBtn.disabled = false;
  }
}

// ── Batch helpers (shared by runOcrAll / runLlmAll) ───────────────────────────

function _batchTargets() {
  // Returns indices of all shapes on the page with the same label as the selected shape
  if (selIdx < 0 || !pageData?.shapes) return [];
  const label = pageData.shapes[selIdx]?.label;
  if (!label) return [];
  return pageData.shapes.map((s, i) => i).filter(i => pageData.shapes[i].label === label);
}

function _batchStart(allBtn, stopBtn, ...disableBtns) {
  batchAbort = false;
  allBtn.disabled = true;
  stopBtn.style.display = 'inline-block';
  disableBtns.forEach(b => { if (b) b.disabled = true; });
}

async function _drainSse(url, jsonBody) {
  /** POST to an SSE endpoint and drain it (server writes results to disk). */
  const opts = jsonBody
    ? { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(jsonBody) }
    : { method: 'POST' };
  const r = await fetch(url, opts);
  if (!r.ok) { const _m = await r.json().catch(() => ({})); throw new Error(_m.detail || r.status); }
  const reader = r.body.getReader();
  while (true) { const {done} = await reader.read(); if (done) break; }
}

function _batchEnd(allBtn, stopBtn, done, total, label, ...enableBtns) {
  batchHighlight = -1; batchAbort = false; llmProgress = null;
  refreshDiag(); drawOverlay();
  allBtn.disabled = false; allBtn.textContent = '▶ All';
  stopBtn.style.display = 'none';
  enableBtns.forEach(b => { if (b) b.disabled = false; });
  showToast(`Done: ${done}/${total} · ${label}`);
}

async function runOcrAll() {
  await _syncOcrSettings();
  // "All" = every shape on the page with the same label as the selected shape
  if (selIdx < 0 || !pages.length) return;
  const targets = _batchTargets();
  if (!targets.length) return;
  await _runOcrBatch(targets);
}

async function _llmSingleShape(shapeIdx, model, mode, prompt) {
  // Regular (non-streaming) LLM call for one shape
  const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: shapeIdx, model, mode});
  const r = await fetch(`${API}/api/page/shape/llm?${params}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({prompt}),
  });
  if (!r.ok) { const _m = await r.json().catch(() => ({})); throw new Error(_m.detail || r.status); }
  const data = await r.json();
  pageData.shapes[shapeIdx].openai_output = {
    response: data.response, model: data.model, mode: data.mode, timestamp: data.timestamp,
  };
  if (pageData.shapes[shapeIdx].row_struct) await _refreshShapeRowStruct(shapeIdx);
}

async function _llmLineByLineOne(shapeIdx, model, prompt, cellHeight) {
  // Streaming line-by-line LLM for one shape; updates llmProgress overlay
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
                                      idx: shapeIdx, model, cell_height: cellHeight,
                                      ..._llmRowParams()});
  const r = await fetch(`${API}/api/page/shape/llm/linebyline?${params}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({prompt}),
  });
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
        pageData.shapes[shapeIdx].openai_output = {
          response: msg.response, model: msg.model, mode: 'linebyline', timestamp: msg.timestamp,
        };
        await _refreshShapeRowStruct(shapeIdx);
      }
    }
    if (batchAbort) { reader.cancel(); break; }
  }
  llmProgress = null;
}

async function _runLlmBatch(targets) {
  if (!targets.length) return;
  const model      = document.getElementById('llm-model').value;
  const mode       = document.getElementById('llm-mode').value;
  const prompt     = document.getElementById('llm-prompt').value.trim();
  const cellHeight = parseInt(document.getElementById('llm-cell-height').value) || 26;
  if (!prompt) { showToast('Prompt is empty'); return; }

  const allBtn  = document.getElementById('llm-all-btn');
  const stopBtn = document.getElementById('llm-batch-stop-btn');
  const sendBtn = document.getElementById('llm-btn');
  _batchStart(allBtn, stopBtn, sendBtn);

  const overwrite   = document.getElementById('overwrite-cb').checked;
  const llmStatusEl = document.getElementById('llm-batch-status');
  const label       = pageData.shapes[selIdx]?.label ?? '';
  let done = 0, skipped = 0;
  for (const i of targets) {
    if (batchAbort) break;
    if (!overwrite && pageData.shapes[i].openai_output?.response) {
      skipped++;
      if (llmStatusEl) llmStatusEl.textContent = `⏭ ${skipped} already have LLM (check Overwrite to redo)`;
      continue;
    }
    batchHighlight = i;
    allBtn.textContent = `${done + 1}/${targets.length}`;
    const s = pageData.shapes[i];
    const cellInfo = (s.super_row != null && s.super_col != null)
      ? `row ${s.super_row}, col ${s.super_col}` : `shape ${i}`;
    if (llmStatusEl) llmStatusEl.textContent = `⟳ ${cellInfo}  (${done + 1}/${targets.length - skipped} to run)`;
    drawOverlay();
    try {
      if (mode === 'linebyline') {
        await _llmLineByLineOne(i, model, prompt, cellHeight);
      } else {
        await _llmSingleShape(i, model, mode, prompt);
      }
      done++;
      if (i === selIdx) updatePanel(); else drawOverlay();
    } catch(e) { if (batchAbort) break; else showToast(`LLM error shape ${i}: ${e.message}`); }
  }
  if (llmStatusEl) llmStatusEl.textContent = skipped && !done
    ? `All ${skipped} skipped — use ✕ Page to clear LLM first, or check Overwrite`
    : (skipped ? `${skipped} skipped, ${done} run` : '');
  _batchEnd(allBtn, stopBtn, done, targets.length, label, sendBtn);
  updatePanel();
}

async function runLlmAll() {
  if (selIdx < 0 || !pages.length) return;
  const targets = _batchTargets();
  if (!targets.length) return;
  await _runLlmBatch(targets);
}

async function runLlmLineByLine() {
  if (selIdx < 0 || !pages.length) return;
  const myIdx   = selIdx;  // capture now — user may click away during streaming
  const btn     = document.getElementById('llm-btn');
  const stopBtn = document.getElementById('llm-stop-btn');
  btn.disabled = true; btn.textContent = '…';
  stopBtn.style.display = 'inline-block';
  llmAbortCtrl = new AbortController();

  const model      = document.getElementById('llm-model').value;
  const prompt     = document.getElementById('llm-prompt').value.trim();
  const cellHeight = parseInt(document.getElementById('llm-cell-height').value) || 28;
  const useShadow  = document.getElementById('llm-use-shadow').checked;
  if (!prompt) { showToast('Prompt is empty'); btn.disabled = false; btn.textContent = '▶ Send'; return; }

  const resultEl = document.getElementById('f-llm-result');
  const metaEl   = document.getElementById('f-llm-meta');
  resultEl.value = '';
  metaEl.textContent = 'Detecting rows…';

  // Pre-compute crop origin in full-image space so we can draw on the main overlay
  const shape = pageData.shapes[myIdx];
  const spts  = shape.points;
  const sx1   = Math.min(spts[0][0], spts[1][0]);
  const sy1   = Math.min(spts[0][1], spts[1][1]);
  const sx2   = Math.max(spts[0][0], spts[1][0]);
  const sy2   = Math.max(spts[0][1], spts[1][1]);  // eslint-disable-line no-unused-vars
  const pad   = 4;
  const imgW  = pageData.imageWidth  || 99999;
  const cropOriginX = Math.max(0, sx1 - pad);
  const cropOriginY = Math.max(0, sy1 - pad);
  const cropRight   = Math.min(imgW, sx2 + pad);

  const lineResults = [];

  try {
    const params = new URLSearchParams({
      folder, stem: pages[pageIdx].stem, idx: myIdx,
      model, cell_height: cellHeight, use_shadow: useShadow,
      ..._llmRowParams(),
    });
    const r = await fetch(`${API}/api/page/shape/llm/linebyline?${params}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt}),
      signal: llmAbortCtrl.signal,
    });
    if (!r.ok) {
      showToast(`LLM error ${r.status}: ${(await r.text()).slice(0, 120)}`);
      return;
    }

    const reader  = r.body.getReader();
    const decoder = new TextDecoder();
    let   buffer  = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const chunks = buffer.split('\n\n');
      buffer = chunks.pop();

      for (const chunk of chunks) {
        if (!chunk.startsWith('data: ')) continue;
        let msg;
        try { msg = JSON.parse(chunk.slice(6)); } catch { continue; }
        if (msg.type === 'error') { reader.cancel(); showToast('⚠ ' + msg.error, 6000); return; }

        if (msg.type === 'lines_detected') {
          // Initialise main-image progress overlay (all rows, none active)
          lastEmptyRows = new Set();
          llmProgress = { cropOriginX, cropOriginY, cropRight,
                          lines: msg.lines, activeRow: -1, emptyRows: null };
          drawOverlay();
          lastRowLines = msg.lines;
          drawRowOverlay(msg.lines, -1);
          metaEl.textContent = `Detected ${msg.count} rows — sending…`;

        } else if (msg.type === 'row_result') {
          lineResults[msg.row] = msg.text;
          // Advance the orange highlight on the main image
          llmProgress.activeRow = msg.row;
          drawOverlay();
          drawRowOverlay(llmProgress.lines, msg.row);
          resultEl.value = lineResults.filter(t => t != null).join('\n');
          updateLineNums('f-llm-result', 'f-llm-result-lines');
          metaEl.textContent = `Row ${msg.row + 1} / ${llmProgress.lines.length}`;

        } else if (msg.type === 'done') {
          if (pageData.shapes[myIdx]) {
            pageData.shapes[myIdx].openai_output = {
              response: msg.response, model: msg.model,
              mode: 'linebyline', timestamp: msg.timestamp,
            };
            await _refreshShapeRowStruct(myIdx);
          }
          llmProgress = null;
          if (myIdx === selIdx) updatePanel();
          refreshDiag(); drawRowOverlay(lastRowLines, -1);
          showToast(`Line-by-line done · ${lineResults.length} rows`);
        }
      }
    }
  } catch(err) {
    if (err.name !== 'AbortError') showToast(`LLM error: ${err.message}`);
    else showToast('Stopped');
  } finally {
    llmAbortCtrl = null;
    llmProgress  = null;
    drawOverlay();
    drawRowOverlay(lastRowLines, -1);
    btn.disabled = false; btn.textContent = '▶ Send';
    stopBtn.style.display = 'none';
  }
}

// ── LLM anchored ─────────────────────────────────────────────────────────────
// Selected shape is the row-count reference; all other shapes in the same
// super_row are split into exactly that many rows and LLM-processed.

async function runLlmAnchored() {
  if (selIdx < 0 || !pages.length) return;
  const refShape = pageData.shapes[selIdx];

  const anchorSrc = document.getElementById('llm-anchor-source').value;
  let nRows, refIdx = -1;
  if (anchorSrc === 'structure') {
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

  const prompt = document.getElementById('llm-prompt').value.trim();
  if (!prompt) { showToast('Prompt is empty'); return; }

  const colSet   = _parseColSet(document.getElementById('llm-anchor-cols').value);
  const anchorCol = refShape.super_column;
  const targets = [];
  pageData.shapes.forEach((s, i) => {
    if (i === selIdx) return;
    if (s.super_row !== superRow || _tableOf(s) !== refTable) return;
    if (colSet && !colSet(s.super_column)) return;
    if (s.super_column === anchorCol) return;
    targets.push(i);
  });
  if (!targets.length) { showToast('No target shapes in same lattice row'); return; }

  const btn     = document.getElementById('llm-btn');
  const stopBtn = document.getElementById('llm-batch-stop-btn');
  btn.disabled = true; btn.textContent = '…';
  if (stopBtn) stopBtn.style.display = 'inline-block';
  batchAbort = false;

  showToast(`Anchored LLM · ${nRows} rows → ${targets.length} shapes`);

  try {
    for (const idx of targets) {
      if (batchAbort) break;
      try { await _llmAnchoredOne(idx, nRows, refIdx); }
      catch(e) { showToast(`Shape ${idx}: ${e.message}`); }
    }
  } finally {
    refreshDiag(); drawOverlay(); updatePanel();
    btn.disabled = false; btn.textContent = '▶ Send';
    if (stopBtn) stopBtn.style.display = 'none';
    batchAbort = false;
  }
}

async function _llmAnchoredOne(shapeIdx, nRows, refIdx = -1) {
  const shape = pageData.shapes[shapeIdx];
  const spts  = shape.points;
  const sx1   = Math.min(spts[0][0], spts[1][0]);
  const sy1   = Math.min(spts[0][1], spts[1][1]);
  const sx2   = Math.max(spts[0][0], spts[1][0]);
  const pad   = 4, imgW = pageData.imageWidth || 99999;
  const cropOriginX = Math.max(0, sx1 - pad);
  const cropOriginY = Math.max(0, sy1 - pad);
  const cropRight   = Math.min(imgW, sx2 + pad);

  const model     = document.getElementById('llm-model').value;
  const prompt    = document.getElementById('llm-prompt').value.trim();
  const useShadow = document.getElementById('llm-use-shadow').checked;
  const params    = new URLSearchParams({
    folder, stem: pages[pageIdx].stem, idx: shapeIdx,
    n_rows: nRows, model, use_shadow: useShadow, ref_idx: refIdx,
  });
  const r = await fetch(`${API}/api/page/shape/llm/anchored?${params}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({prompt}),
  });
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
        pageData.shapes[shapeIdx].openai_output = {
          response: msg.response, model: msg.model,
          mode: 'anchored', timestamp: msg.timestamp,
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

