// Split from index.html — classic scripts share the global scope;
// load order in index.html is load-bearing. See knowledge_base/02_architecture.md.
// ── Save correction text ─────────────────────────────────────────────────────
// ── Authority resolver ─────────────────────────────────────────────────────
let _authRootCache  = {};       // authority file -> [root entities]
const _authChildCache = {};     // `${file}|${parentId}` -> [children]
let _authListCache  = null;     // {authorities:[{file,authority,entity_types,...}], default}

// Authority is chosen per lattice column (table:super_column) when the cell is
// a lattice cell, else per page. Effective file = column override → page default.
function _colKey(shape) {
  const t = _tableOf(shape);
  return (t != null && shape?.super_column != null) ? `${t}:${shape.super_column}` : null;
}
function _pageAuthFile() {
  return (pageData?.flags?.authority_file) || (_authListCache?.default) || 'places_hu.authority.json';
}
function _authFileFor(shape) {
  const ck = shape ? _colKey(shape) : null;
  const map = pageData?.flags?.column_authority || {};
  return (ck && map[ck]) || _pageAuthFile();
}
function _authFile() { return _authFileFor(pageData?.shapes?.[selIdx]); }
async function _ensureAuthList() {
  if (_authListCache) return _authListCache;
  try {
    const r = await fetch(`${API}/api/authorities`);
    _authListCache = r.ok ? await r.json() : {authorities: [], default: 'places_hu.authority.json'};
  } catch (e) { _authListCache = {authorities: [], default: 'places_hu.authority.json'}; }
  return _authListCache;
}
function _authMeta(file) { return (_authListCache?.authorities || []).find(a => a.file === file) || null; }

function _escH(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function _authCtxKey() { return String(_activeTable()); }
function _authCtx() {
  return (pageData?.flags?.authority_context || {})[_authCtxKey()] || {};
}
function _authParent() {
  const c = _authCtx();
  return c.district || c.county || '';   // tightest available constraint
}
function _bestCellText(shape) {
  return (shape?.human_output?.human_corrected_text || '').trim()
      || (shape?.openai_output?.response || '').trim()
      || (shape?.tesseract_output?.ocr_text || '').trim()
      || (shape?.pdf_text || '').trim();
}

async function _ensureRoots() {
  const f = _authFile();
  if (_authRootCache[f]) return _authRootCache[f];
  try {
    const r = await fetch(`${API}/api/authority/children?name=${encodeURIComponent(f)}`);
    _authRootCache[f] = r.ok ? ((await r.json()).items || []) : [];
  } catch (e) { _authRootCache[f] = []; }
  return _authRootCache[f];
}
async function _childrenOf(parentId) {
  if (!parentId) return [];
  const f = _authFile(), key = `${f}|${parentId}`;
  if (_authChildCache[key]) return _authChildCache[key];
  try {
    const r = await fetch(`${API}/api/authority/children?parent=${encodeURIComponent(parentId)}&name=${encodeURIComponent(f)}`);
    _authChildCache[key] = r.ok ? ((await r.json()).items || []) : [];
  } catch (e) { _authChildCache[key] = []; }
  return _authChildCache[key];
}
function _authCtxLabels() {
  const et = _authMeta(_authFile())?.entity_types || [];
  return [et[0] || 'level 1', et[1] || 'level 2'];
}
function _populateAuthTypes() {
  const sel = document.getElementById('auth-type');
  if (!sel) return;
  const types = _authMeta(_authFile())?.entity_types || [];
  const cur = sel.value;
  sel.innerHTML = `<option value="">All types</option>` +
    types.map(t => `<option value="${_escH(t)}">${_escH(t.charAt(0).toUpperCase() + t.slice(1))}</option>`).join('');
  sel.value = types.includes(cur) ? cur : '';
}

async function refreshAuthorityPanel(shape) {
  const fg = document.getElementById('fg-authority');
  if (!fg) return;
  fg.style.display = 'flex';

  // Authority source picker + type list (driven by the chosen authority)
  await _ensureAuthList();
  const fsel = document.getElementById('auth-file');
  const file = _authFile();
  if (fsel) {
    const list = _authListCache.authorities || [];
    fsel.innerHTML = list.length
      ? list.map(a => `<option value="${_escH(a.file)}"${a.file === file ? ' selected' : ''}>${_escH(a.authority || a.file)}</option>`).join('')
      : `<option value="">(no authorities found)</option>`;
  }
  _populateAuthTypes();

  // Scope label + column-batch button depend on whether this is a lattice cell
  const ck = _colKey(shape);
  const lbl = document.getElementById('auth-file-label');
  if (lbl) lbl.textContent = ck ? `Source · col ${shape.super_column}` : 'Source · page';
  const colBtn = document.getElementById('auth-col-btn');
  if (colBtn) colBtn.style.display = ck ? '' : 'none';

  const cur = document.getElementById('auth-current');
  const a = shape.authority;
  if (a) {
    const loc = [a.district_name, a.county_name].filter(Boolean).join(', ');
    cur.innerHTML =
      `<span style="display:inline-flex;align-items:center;gap:6px;background:#14532d;border:1px solid #166534;border-radius:10px;padding:2px 8px;color:#bbf7d0;">`+
      `✓ ${_escH(a.name)}${loc ? ` · <span style="color:#86efac;">${_escH(loc)}</span>` : ''}`+
      `<button onclick="clearAuthority()" title="Clear" style="background:none;border:none;color:#fca5a5;cursor:pointer;padding:0;font-size:12px;line-height:1;">✕</button></span>`+
      `<div style="color:#667;font-size:9px;margin-top:2px;">${_escH(a.type||'')}${a.score!=null?` · ${a.score}%`:''} · ${_escH(a.source||'human')} · ${_escH(a.id)}</div>`;
  } else {
    cur.innerHTML = `<span style="color:#778;">Unresolved</span>`;
  }

  // Per-table context selectors (active table); labels from the authority's types
  const [l1, l2] = _authCtxLabels();
  document.getElementById('auth-ctx-label').textContent = `T${_authCtxKey()}`;
  const ctx = _authCtx();
  const roots = await _ensureRoots();
  const csel = document.getElementById('auth-ctx-county');
  csel.innerHTML = `<option value="">— ${_escH(l1)} —</option>` +
    roots.map(c => `<option value="${_escH(c.id)}"${c.id===ctx.county?' selected':''}>${_escH(c.name)}</option>`).join('');
  const dsel = document.getElementById('auth-ctx-district');
  const kids = ctx.county ? await _childrenOf(ctx.county) : [];
  dsel.innerHTML = `<option value="">— ${_escH(l2)} —</option>` +
    kids.map(d => `<option value="${_escH(d.id)}"${d.id===ctx.district?' selected':''}>${_escH(d.name)}</option>`).join('');

  document.getElementById('auth-cands').innerHTML = '';
}

async function onAuthFileChange() {
  const f = document.getElementById('auth-file').value;
  const shape = pageData.shapes[selIdx];
  const ck = _colKey(shape);
  if (!pageData.flags) pageData.flags = {};
  const patch = {authority_context: {}};       // context ids are authority-specific
  pageData.flags.authority_context = {};
  if (ck) {                                     // lattice cell → set this column
    pageData.flags.column_authority = pageData.flags.column_authority || {};
    pageData.flags.column_authority[ck] = f;
    patch.column_authority = pageData.flags.column_authority;
  } else {                                      // free cell → set page default
    pageData.flags.authority_file = f;
    patch.authority_file = f;
  }
  try {
    await fetch(`${API}/api/page/flags?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(pages[pageIdx].stem)}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({flags: patch})
    });
  } catch (e) { /* best-effort persistence */ }
  if (selIdx >= 0) await refreshAuthorityPanel(shape);
}

async function _saveAuthCtx(patch) {
  if (!pageData.flags) pageData.flags = {};
  if (!pageData.flags.authority_context) pageData.flags.authority_context = {};
  const k = _authCtxKey();
  pageData.flags.authority_context[k] = {...(pageData.flags.authority_context[k] || {}), ...patch};
  try {
    await fetch(`${API}/api/page/flags?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(pages[pageIdx].stem)}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({flags: {authority_context: pageData.flags.authority_context}})
    });
  } catch (e) { /* context is also held in-memory; persistence is best-effort */ }
}
async function onAuthCountyChange() {
  const csel = document.getElementById('auth-ctx-county');
  const id = csel.value, name = csel.options[csel.selectedIndex]?.text || '';
  await _saveAuthCtx({county: id || null, county_name: id ? name : null, district: null, district_name: null});
  const dsel = document.getElementById('auth-ctx-district');
  const kids = id ? await _childrenOf(id) : [];
  dsel.innerHTML = `<option value="">— ${_escH(_authCtxLabels()[1])} —</option>` +
    kids.map(d => `<option value="${_escH(d.id)}">${_escH(d.name)}</option>`).join('');
}
async function onAuthDistrictChange() {
  const dsel = document.getElementById('auth-ctx-district');
  const id = dsel.value, name = dsel.options[dsel.selectedIndex]?.text || '';
  await _saveAuthCtx({district: id || null, district_name: id ? name : null});
}

async function resolveAuthority() {
  if (selIdx < 0) return;
  const shape = pageData.shapes[selIdx];
  const q = _bestCellText(shape);
  const box = document.getElementById('auth-cands');
  if (!q) { box.innerHTML = `<span style="color:#a55;font-size:10px;">No text in this cell to resolve.</span>`; return; }
  const type = document.getElementById('auth-type').value;
  const parent = _authParent();
  box.innerHTML = `<span style="color:#778;font-size:10px;">Resolving “${_escH(q.split('\n')[0])}”…</span>`;
  const params = new URLSearchParams({q, name: _authFile()});
  if (type)   params.set('type', type);
  if (parent) params.set('parent', parent);
  try {
    const r = await fetch(`${API}/api/authority/resolve?${params}`);
    if (!r.ok) { box.innerHTML = `<span style="color:#a55;font-size:10px;">Error ${r.status}: ${_escH((await r.text()).slice(0,80))}</span>`; return; }
    _renderAuthCands((await r.json()).candidates || [], q);
  } catch (e) {
    box.innerHTML = `<span style="color:#a55;font-size:10px;">${_escH(String(e))}</span>`;
  }
}
// Resolve every cell in the selected cell's lattice column with that column's
// authority. Cells with internal rows resolve per row; others resolve whole.
async function resolveColumn() {
  if (selIdx < 0) return;
  const sel = pageData.shapes[selIdx];
  if (!_colKey(sel)) { showToast('Select a lattice cell — “Resolve column” works on a lattice column'); return; }
  const AT = _tableOf(sel), col = sel.super_column;
  const type = document.getElementById('auth-type').value;
  const parent = _authParent();
  // Reading order top-to-bottom so ditto marks inherit the entity above
  const cells = pageData.shapes
    .filter(s => _tableOf(s) === AT && s.super_column === col)
    .sort((a, b) => (a.super_row ?? 0) - (b.super_row ?? 0));
  if (!cells.length) return;
  const btn = document.getElementById('auth-col-btn');
  const oldTxt = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  let resolved = 0, kept = 0, skipped = 0, nomatch = 0, ditto = 0;
  let lastAuth = null;   // nearest resolved entity above — reset per cell (a
                         // ditto must never inherit from a different lattice cell)
  try {
    // One resolvable unit (whole cell, or each internal row) processed in order
    const resolveUnit = async (getText, getAuth, setAuth, isHuman) => {
      if (isHuman()) { kept++; const a = getAuth(); if (a) lastAuth = a; return; }
      const text = getText();
      if (_isDitto(text)) {
        if (lastAuth) { setAuth(_dittoCopy(lastAuth)); ditto++; } else { setAuth(null); skipped++; }
        return;
      }
      if (!_rsResolvable(text)) { setAuth(null); skipped++; return; }
      const cands = await _resolveText(text, type, parent);
      // Reject low-similarity tops: no real string match → don't guess.
      if (!cands.length || cands[0].score < _AUTH_MIN_ACCEPT) { setAuth(null); nomatch++; return; }
      const a = _candToAuth(cands[0], 'auto'); setAuth(a); lastAuth = a; resolved++;
    };
    for (const shape of cells) {
      lastAuth = null;                       // ditto never crosses a cell boundary
      const rows = _rsRows(shape);
      if (rows && rows.length) {
        for (let i = 0; i < rows.length; i++) {
          const r = rows[i];
          await resolveUnit(
            () => _rsRowBest(shape, i),
            () => r.authority,
            (a) => { if (a) r.authority = a; else delete r.authority; },
            () => r.authority && r.authority.source === 'human');
        }
      } else {
        await resolveUnit(
          () => _bestCellText(shape),
          () => shape.authority,
          (a) => { if (a) shape.authority = a; else delete shape.authority; },
          () => shape.authority && shape.authority.source === 'human');
      }
    }
    await replaceAllShapes();
    if (pageData.shapes[selIdx]) refreshAuthorityPanel(pageData.shapes[selIdx]);
    drawOverlay(); refreshDiag();
    const _an = _authMeta(_authFile())?.authority || _authFile();
    showToast(`Col ${col} [${_an}]: ${resolved} resolved`
      + (ditto ? `, ${ditto} ditto→above` : '')
      + (kept ? `, ${kept} manual kept` : '')
      + (nomatch ? `, ${nomatch} no match` : '')
      + (skipped ? `, ${skipped} skipped` : '')
      + (resolved === 0 && ditto === 0 && nomatch > 0 ? ' — wrong authority? check Source' : ''));
  } catch (e) {
    console.error('[auth] resolveColumn failed', e);
    showToast('Auth error: ' + (e?.message || e), 6000);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = oldTxt || '📋 Resolve column'; }
  }
}

function _renderAuthCands(cands, q) {
  const box = document.getElementById('auth-cands');
  if (!cands.length) { box.innerHTML = `<span style="color:#a55;font-size:10px;">No match for “${_escH(q.split('\n')[0])}”.</span>`; return; }
  box.innerHTML = cands.map(c => {
    const loc = [c.district_name, c.county_name].filter(Boolean).join(', ');
    const sc = c.score >= 95 ? '#22c55e' : c.score >= 80 ? '#eab308' : '#f87171';
    return `<div onclick='assignAuthority(${JSON.stringify(c.id)})' title="${_escH(c.id)} · matched “${_escH(c.matched||'')}” via ${_escH(c.via||'')}" `+
      `style="display:flex;justify-content:space-between;gap:6px;align-items:center;padding:3px 5px;border:1px solid #1a3a6e;border-radius:3px;margin-bottom:2px;cursor:pointer;font-size:11px;background:#0d1b35;">`+
      `<span><b>${_escH(c.name)}</b> <span style="color:#778;font-size:9px;">${_escH(c.type||'')}</span>${loc?`<br><span style="color:#7a8;font-size:9px;">${_escH(loc)}</span>`:''}</span>`+
      `<span style="color:${sc};font-weight:600;white-space:nowrap;">${c.score}%</span></div>`;
  }).join('');
}
async function assignAuthority(id) {
  if (selIdx < 0) return;
  const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: selIdx});
  try {
    const r = await fetch(`${API}/api/page/shape/authority?${params}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, source: 'human', name: _authFile()})
    });
    if (!r.ok) { showToast(`Assign error ${r.status}`); return; }
    pageData.shapes[selIdx].authority = (await r.json()).authority;
    refreshAuthorityPanel(pageData.shapes[selIdx]);
    drawOverlay();
    showToast(`✓ ${pageData.shapes[selIdx].authority?.name || 'assigned'}`);
  } catch (e) { showToast(String(e)); }
}
async function clearAuthority() {
  if (selIdx < 0) return;
  const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, idx: selIdx});
  try {
    await fetch(`${API}/api/page/shape/authority?${params}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: null})
    });
  } catch (e) { /* ignore */ }
  delete pageData.shapes[selIdx].authority;
  refreshAuthorityPanel(pageData.shapes[selIdx]);
  drawOverlay();
}

function copyToHuman(source) {
  const text = source === 'ocr'
    ? document.getElementById('f-ocr').value
    : document.getElementById('f-llm-result').value;
  if (!text || text === '—') return;
  const ta = document.getElementById('human-input');
  ta.value = text;
  updateLineNums('human-input', 'human-input-lines');
  ta.focus();
}

async function saveCorrection() {
  if (selIdx<0) return;
  const text=document.getElementById('human-input').value;
  const r=await patchShape(selIdx,{human_corrected_text:text});
  if (!pageData.shapes[selIdx].human_output) pageData.shapes[selIdx].human_output={};
  pageData.shapes[selIdx].human_output.human_corrected_text=text;
  if (pageData.shapes[selIdx].row_struct) await _refreshShapeRowStruct(selIdx);
  document.getElementById('save-status').textContent='✓ Saved';
  setTimeout(()=>document.getElementById('save-status').textContent='',2000);
  refreshDiag(); drawOverlay();
}

// ── Structural blank: manual toggle on the selected cell ────────────────────
function _shapeIsBlank(sh) {
  const rows = sh?.row_struct?.rows;
  return rows?.length ? rows.every(r => r.blank) : !!sh?.blank;
}

function _blankTargets() {
  // the whole current selection (rubber-band / Ctrl-click), else the primary cell
  const idxs = selSet && selSet.size > 1 ? [...selSet] : (selIdx >= 0 ? [selIdx] : []);
  return idxs.filter(i => pageData?.shapes?.[i]);
}

async function toggleBlank() {
  const idxs = _blankTargets();
  if (!idxs.length) return;
  // mixed selection → mark all blank; all already blank → un-mark all
  const makeBlank = !idxs.every(i => _shapeIsBlank(pageData.shapes[i]));
  idxs.forEach(i => {
    const sh = pageData.shapes[i];
    const rows = sh.row_struct?.rows;
    if (rows?.length) rows.forEach(r => { if (makeBlank) r.blank = true; else delete r.blank; });
    if (makeBlank) sh.blank = true; else delete sh.blank;
  });
  const ok = await replaceAllShapes();
  if (ok) {
    const n = idxs.length;
    showToast(makeBlank ? `∅ Marked ${n} blank` : `Blank mark removed (${n})`);
    updateBlankBtn(); drawOverlay(); refreshDiag();
  }
}

function updateBlankBtn() {
  const btn = document.getElementById('blank-btn');
  if (!btn) return;
  const idxs = _blankTargets();
  if (!idxs.length) { btn.disabled = true; btn.textContent = '∅ Mark blank (B)'; return; }
  btn.disabled = false;
  const multi = idxs.length > 1;
  const allB = idxs.every(i => _shapeIsBlank(pageData.shapes[i]));
  const suffix = multi ? ` ${idxs.length} (B)` : ' (B)';
  btn.textContent = (allB ? '∅ Un-mark blank' : '∅ Mark blank') + suffix;
  btn.style.background = allB ? '#3a3320' : '#1a2740';
  btn.style.borderColor = allB ? '#8a7a3a' : '#3a4a6a';
}

// ── Smart Correct ────────────────────────────────────────────────────────────

function _applyHumanCorrection(shape, text) {
  if (!shape.human_output) shape.human_output = {};
  shape.human_output.human_corrected_text = text;
}

async function runSmartCorrect() {
  if (!pageData?.shapes?.length) return;
  const mode = diagnosticMode;
  const ROW_MODES = ['ocr_rows','llm_rows','human_rows','best_rows','best_rows_pdf'];
  if (!ROW_MODES.includes(mode)) {
    showToast('Smart Correct only works in row-mismatch Diagnose modes');
    return;
  }

  const shapes = pageData.shapes;

  // Read from current diagnostic source field
  const getField = s => {
    if (mode === 'ocr_rows')   return s.tesseract_output?.ocr_text;
    if (mode === 'llm_rows')   return s.openai_output?.response;
    if (mode === 'human_rows') return s.human_output?.human_corrected_text;
    if (mode === 'best_rows')
      return s.human_output?.human_corrected_text
          || s.openai_output?.response
          || s.tesseract_output?.ocr_text;
    if (mode === 'best_rows_pdf')
      return s.human_output?.human_corrected_text
          || s.openai_output?.response
          || s.tesseract_output?.ocr_text
          || s.pdf_text;
  };

  // Write back to the same source field (overwrite machine-generated results)
  const setField = (s, text) => {
    if (mode === 'ocr_rows') {
      if (!s.tesseract_output) s.tesseract_output = {};
      s.tesseract_output.ocr_text = text;
    } else if (mode === 'llm_rows') {
      if (!s.openai_output) s.openai_output = {};
      s.openai_output.response = text;
    } else if (mode === 'human_rows') {
      if (!s.human_output) s.human_output = {};
      s.human_output.human_corrected_text = text;
    } else if (mode === 'best_rows' || mode === 'best_rows_pdf') {
      // Write to whichever field provided the value
      if (s.human_output?.human_corrected_text) {
        s.human_output.human_corrected_text = text;
      } else if (s.openai_output?.response) {
        s.openai_output.response = text;
      } else if (s.tesseract_output?.ocr_text || mode === 'best_rows') {
        if (!s.tesseract_output) s.tesseract_output = {};
        s.tesseract_output.ocr_text = text;
      } else {
        s.pdf_text = text;   // pdf_text was the source
      }
    }
  };

  // A line is "empty" if it is blank/whitespace only
  const isBlank   = l => l.trim() === '';
  // A line is a "dash or zero" placeholder (non-empty but trivially empty-meaning)
  const isDashZero = l => l.trim().length > 0 && /^[-—–0]+$/.test(l.trim());
  const isEmptyOrDash = l => isBlank(l) || isDashZero(l);

  // Group shapes by (table, super_row)
  const rowGroups = {};
  shapes.forEach((s, i) => {
    if (s.super_row == null) return;
    (rowGroups[_rk(s)] ??= []).push(i);
  });

  let fixCount = 0;

  for (const idxs of Object.values(rowGroups)) {
    const withData = idxs.filter(i => getField(shapes[i]));
    if (withData.length < 2) continue;

    // Cache split lines for each shape index
    const linesOf = {};
    withData.forEach(i => { linesOf[i] = getField(shapes[i]).split('\n'); });

    // Step 1: mismatch = raw line counts are not all equal
    const rawLen = i => linesOf[i].length;
    const rawVals = withData.map(rawLen);
    if (rawVals.every(v => v === rawVals[0])) continue;

    // "Longer" = raw count strictly above the minimum raw count in this row
    const minRaw = Math.min(...rawVals);
    const longer = withData.filter(i => rawLen(i) > minRaw);
    if (!longer.length) continue;

    // Trim each longer cell independently:
    // prefer removing the last line if it is EOD, otherwise the first line if it is EOD.
    for (const i of longer) {
      let ls = linesOf[i].slice();
      let changed = false;

      if (isEmptyOrDash(ls[ls.length - 1])) {
        ls = ls.slice(0, -1);
        changed = true;
      } else if (isEmptyOrDash(ls[0])) {
        ls = ls.slice(1);
        changed = true;
      }

      if (changed) {
        setField(shapes[i], ls.join('\n'));
        fixCount++;
      }
    }
  }

  if (!fixCount) {
    showToast('No fixable mismatches found');
    return;
  }

  await replaceAllShapes();
  onDiagnosticChange();   // re-run in same mode — diagnostic now reads the corrected values
  updatePanel();
  showToast(`Smart Correct: fixed ${fixCount} cell${fixCount!==1?'s':''}`);
}


// ── Authority worklist: distinct unresolved strings, fix once → apply to all ──
let _wlData = null;        // last worklist response
let _wlMode = null;        // 'worklist' | 'aliases'

function _authWlScope() {
  // Reuse the ⚙ Batch modal's scope fields (pages, parity, column filter, authority).
  const indices = _parsePageRange(document.getElementById('batch-pages').value);
  if (!indices) return null;
  if (document.getElementById('batch-parity-odd').checked)
    for (const i of [...indices]) { if ((i + 1) % 2 === 0) indices.delete(i); }
  if (document.getElementById('batch-parity-even').checked)
    for (const i of [...indices]) { if ((i + 1) % 2 === 1) indices.delete(i); }
  const stems = [...indices].sort((a, b) => a - b).map(i => pages[i]?.stem).filter(Boolean);
  return {
    stems,
    col_filter: document.getElementById('batch-col-filter').value.trim() || null,
    name:       document.getElementById('batch-auth-file').value || null,
    type:       document.getElementById('batch-auth-type').value || null,
    layer:      document.getElementById('batch-auth-layer').value,
  };
}

function closeAuthWorklist() {
  document.getElementById('auth-worklist-modal').style.display = 'none';
}

function _wlShow(title) {
  document.getElementById('auth-wl-title').textContent = title;
  document.getElementById('auth-wl-summary').textContent = '';
  document.getElementById('auth-wl-status').textContent = 'Loading…';
  document.getElementById('auth-wl-list').innerHTML = '';
  document.getElementById('auth-wl-footer').innerHTML = '';
  document.getElementById('auth-worklist-modal').style.display = 'flex';
}

async function openAuthWorklist() {
  const scope = _authWlScope();
  if (!scope) return;
  if (!scope.stems.length) { showToast('No pages in range'); return; }
  _wlMode = 'worklist';
  _wlShow('📋 Unresolved worklist');
  try {
    const r = await fetch(`${API}/api/authority/worklist?folder=${encodeURIComponent(folder)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(scope)});
    if (!r.ok) { const m = await r.json().catch(() => ({})); throw new Error(m.detail || r.status); }
    _wlData = await r.json();
    _wlRender(scope);
  } catch (e) {
    document.getElementById('auth-wl-status').textContent = '✕ ' + (e?.message || e);
  }
}

function _wlCellImg(loc) {
  const u = `${API}/api/cell?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(loc.stem)}&idx=${loc.idx}&pad=4`;
  return `<img src="${u}" title="${_escH(loc.stem)}" style="max-height:44px;max-width:220px;border:1px solid #0f3460;border-radius:3px;background:#fff;">`;
}

function _wlCandOpts(cands, selectedId) {
  const opts = (cands || []).map(c => {
    const ctx = [c.type, c.county_name, c.district_name].filter(Boolean).join(', ');
    const sel = c.id === selectedId ? ' selected' : '';
    return `<option value="${_escH(c.id)}"${sel}>${_escH(c.name)} — ${_escH(ctx)} (${Math.round(c.score ?? 0)})</option>`;
  }).join('');
  return `<option value="">— pick entity —</option>` + opts;
}

function _wlRender(scope) {
  const d = _wlData;
  document.getElementById('auth-wl-status').textContent = '';
  document.getElementById('auth-wl-summary').textContent =
    `${d.total_unresolved} unresolved in ${d.distinct} distinct strings · ${d.pages} page(s) · ${d.authority}`;
  const list = document.getElementById('auth-wl-list');
  if (!d.groups.length) { list.innerHTML = '<div style="color:#7ec8a0;font-size:13px;">✓ Nothing unresolved in this scope.</div>'; return; }
  list.innerHTML = d.groups.map((g, gi) => {
    const samples = (g.locations || []).slice(0, 2).map(_wlCellImg).join(' ');
    return `<div class="wl-row" data-gi="${gi}" style="display:flex;align-items:center;gap:8px;background:#091530;border:1px solid #0f3460;border-radius:5px;padding:6px 8px;">
      <span style="flex:none;min-width:34px;text-align:right;color:#e94560;font-weight:700;font-size:12px;">${g.count}×</span>
      <span style="flex:none;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#e0e0e0;font-size:12px;" title="${_escH(g.text)}">${_escH(g.text)}</span>
      <span style="flex:none;">${samples}</span>
      <select class="wl-pick" style="flex:1;min-width:160px;background:#0d1b35;border:1px solid #0f3460;color:#e0e0e0;border-radius:4px;padding:4px 6px;font-size:12px;">${_wlCandOpts(g.candidates, (g.candidates && g.candidates[0] && g.candidates[0].score >= 85) ? g.candidates[0].id : null)}</select>
      <input class="wl-search" placeholder="search…" style="flex:none;width:110px;background:#0d1b35;border:1px solid #0f3460;color:#e0e0e0;border-radius:4px;padding:4px 6px;font-size:12px;">
      <button class="wl-llm" title="Ask the LLM to pick using the cell image" style="flex:none;background:#0f3460;border:1px solid #16588e;color:#e0e0e0;border-radius:4px;padding:4px 8px;font-size:12px;cursor:pointer;">🤖</button>
      <button class="wl-apply" style="flex:none;background:#1b4d2e;border:1px solid #2e7d4f;color:#e0e0e0;border-radius:4px;padding:4px 10px;font-size:12px;cursor:pointer;">Apply</button>
    </div>`;
  }).join('');

  list.querySelectorAll('.wl-row').forEach(row => {
    const gi = +row.dataset.gi, g = d.groups[gi];
    const pick = row.querySelector('.wl-pick');
    // live search from the 3rd character (same behavior as the panel dropdown)
    let t = null;
    row.querySelector('.wl-search').addEventListener('input', ev => {
      const q = ev.target.value.trim();
      clearTimeout(t);
      if (q.length < 3) return;
      t = setTimeout(async () => {
        try {
          const p = new URLSearchParams({q, k: '8'});
          if (scope.name) p.set('name', scope.name);
          if (scope.type) p.set('type', scope.type);
          const r = await fetch(`${API}/api/authority/resolve?${p}`);
          const cands = (await r.json()).candidates || [];
          g.candidates = cands;
          pick.innerHTML = _wlCandOpts(cands, cands[0]?.id);
        } catch (e) {}
      }, 250);
    });
    row.querySelector('.wl-llm').addEventListener('click', async ev => {
      const btn = ev.target;
      const cands = (g.candidates || []).slice(0, 5);
      if (!cands.length) { showToast('No candidates to choose from — search first'); return; }
      const loc = g.locations && g.locations[0];
      if (!loc) return;
      btn.disabled = true; btn.textContent = '…';
      try {
        const model = document.getElementById('batch-llm-model')?.value || 'gpt-4o-mini';
        const r = await fetch(`${API}/api/authority/llm_pick?folder=${encodeURIComponent(folder)}`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({stem: loc.stem, idx: loc.idx, text: g.text, model,
                                candidates: cands.map(c => ({id: c.id, name: c.name, type: c.type,
                                  county_name: c.county_name, district_name: c.district_name}))})});
        const m = await r.json();
        if (!r.ok) throw new Error(m.detail || r.status);
        if (m.choice) { pick.value = m.choice; showToast('LLM picked: ' + ((cands.find(c => c.id === m.choice) || {}).name || m.choice)); }
        else showToast('LLM: none of the candidates fit');
      } catch (e) { showToast('LLM error: ' + (e?.message || e)); }
      finally { btn.disabled = false; btn.textContent = '🤖'; }
    });
    row.querySelector('.wl-apply').addEventListener('click', async ev => {
      const id = pick.value;
      if (!id) { showToast('Pick an entity first'); return; }
      const btn = ev.target; btn.disabled = true; btn.textContent = '…';
      try {
        const r = await fetch(`${API}/api/authority/apply_string?folder=${encodeURIComponent(folder)}`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({...scope, fold: g.fold, entity_id: id})});
        const m = await r.json();
        if (!r.ok) throw new Error(m.detail || r.status);
        row.style.opacity = '0.55'; row.style.borderColor = '#2e7d4f';
        btn.textContent = `✓ ${m.applied}`;
        showToast(`"${g.text}" → ${m.entity.name}: ${m.applied} cell(s) on ${m.pages_changed} page(s)`);
        if (pageData) { await reloadPageData(); updatePanel(); drawOverlay(); }
      } catch (e) {
        showToast('Apply error: ' + (e?.message || e));
        btn.disabled = false; btn.textContent = 'Apply';
      }
    });
  });
}

// ── Alias suggestions: confirmed picks the authority file doesn't know yet ──
async function openAuthAliases() {
  const scope = _authWlScope();
  if (!scope) return;
  if (!scope.stems.length) { showToast('No pages in range'); return; }
  _wlMode = 'aliases';
  _wlShow('➕ Alias suggestions');
  try {
    const r = await fetch(`${API}/api/authority/alias_candidates?folder=${encodeURIComponent(folder)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(scope)});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    document.getElementById('auth-wl-status').textContent = '';
    document.getElementById('auth-wl-summary').textContent =
      `${d.candidates.length} suggestion(s) · ${d.authority}`;
    const list = document.getElementById('auth-wl-list');
    if (!d.candidates.length) {
      list.innerHTML = '<div style="color:#7ec8a0;font-size:13px;">No new aliases — every confirmed pick is already known to the authority.</div>';
      return;
    }
    list.innerHTML = d.candidates.map((c, i) => `
      <label style="display:flex;align-items:center;gap:8px;background:#091530;border:1px solid #0f3460;border-radius:5px;padding:6px 8px;cursor:pointer;font-size:12px;color:#e0e0e0;">
        <input type="checkbox" class="wl-alias" data-i="${i}" checked style="accent-color:#e94560;width:13px;height:13px;">
        <span style="flex:none;min-width:34px;text-align:right;color:#e94560;font-weight:700;">${c.count}×</span>
        <span style="flex:none;color:#f0c040;">"${_escH(c.alias)}"</span>
        <span style="flex:none;color:#666;">→</span>
        <span style="flex:1;">${_escH(c.entity_name)} <span style="color:#666;font-size:10px;">${_escH(c.id)}</span></span>
      </label>`).join('');
    document.getElementById('auth-wl-footer').innerHTML =
      `<span style="flex:1;font-size:10px;color:#555;align-self:center;">Appends aliases (source: econai_confirmed) to the git-tracked authority file — review the diff before committing.</span>
       <button onclick="_wlPromote()" style="background:#1b4d2e;border:1px solid #2e7d4f;color:#e0e0e0;border-radius:4px;padding:6px 12px;font-size:12px;cursor:pointer;">➕ Add selected to authority</button>`;
    _wlData = d;
  } catch (e) {
    document.getElementById('auth-wl-status').textContent = '✕ ' + (e?.message || e);
  }
}

async function _wlPromote() {
  const sel = [...document.querySelectorAll('.wl-alias:checked')]
    .map(cb => _wlData.candidates[+cb.dataset.i])
    .map(c => ({id: c.id, alias: c.alias}));
  if (!sel.length) { showToast('Nothing selected'); return; }
  try {
    const r = await fetch(`${API}/api/authority/promote_aliases`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: document.getElementById('batch-auth-file').value || null, aliases: sel})});
    const m = await r.json();
    if (!r.ok) throw new Error(m.detail || r.status);
    showToast(`Added ${m.added} alias(es) to ${m.file} — review with git diff`, 6000);
    closeAuthWorklist();
  } catch (e) { showToast('Promote error: ' + (e?.message || e)); }
}

// ── Canvas badge: is this shape (fully / partly) resolved? ──────────────────
function _authBadgeState(shape) {
  const rows = shape.row_struct?.rows;
  if (rows?.length) {
    const withText = rows.filter(r =>
      ((r.human || r.llm || r.ocr || r.pdf) || '').trim().replace(/\s/g, '').length >= 2);
    const resolved = withText.filter(r => r.authority);
    if (!resolved.length) return null;
    return resolved.length >= withText.length ? 'full' : 'part';
  }
  return shape.authority ? 'full' : null;
}
