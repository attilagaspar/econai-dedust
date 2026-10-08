// Split classic script (shared global scope, load order matters — see
// knowledge_base/02_architecture.md). The 📋 Dataset window: create and edit
// dataset declarations (knowledge_base/10_dataset_layer.md) without touching
// JSON. A side panel, so the page stays visible: hovering a variable row
// highlights that column's cells on the page.
//   GET/PUT/DELETE /api/dataset/<name>/declaration   load / save / soft-delete
//   POST /api/dataset/page-map                       which page is which slot
//   POST /api/dataset/preview                        ▶ Test (build, no save)
//   POST /api/dataset/suggest-identities             🔍 find sums in the data
// Unknown fields of a loaded declaration (diagnostics overrides, per-variable
// parse/scale/esd, key.columns …) are preserved: the form edits the object.
// Reuses: folder, pages, pageIdx, pageData, projectLabels, projectRules,
// selSet, selIdx, drawOverlay, _escHtml, showToast, _batchDatasetInit.

let _dd = null;     // {name, isNew, decl, list, pageMap, preview, suggestions}

const _DD_HU_EXCLUDES = ['osszesen', 'osszesites', 'jaras', 'varmegye', '\\bvm\\b',
                         'torvenyhatosag', '^\\s*[a-e]\\)', '^\\s*\\d+\\.', '\\sj\\.?\\s*$'];
const _DD_TYPES = {int: 'whole number', number: 'number', text: 'text', entity: 'place / name (authority)'};

function _ddBlank() {
  return {
    name: '', version: 1,
    scope: {labels: _ddPageLatticeLabels(), pattern: '1', pages: null, tables: [0]},
    record: {unit: 'internal_row', join: 'positional', exclude_keys: [],
             key: {slot: 1, column: null, dtype: 'entity', authority: null}},
    variables: [],
    parse: {missing: ['-', '—', '·', ''], thousands: [' ', '.'], decimal: ','},
    identities: [], totals: null, diagnostics: {},
  };
}

function _ddPageLatticeLabels() {
  const s = new Set();
  (pageData?.shapes || []).forEach(sh => { if (sh.super_column != null) s.add(sh.label); });
  return [...s];
}

// ── open / load ────────────────────────────────────────────────────────────
async function openDatasetDecl(name) {
  if (!folder) { showToast('Load a folder first'); return; }
  _dd = {name: null, isNew: true, decl: _ddBlank(), list: [], pageMap: null,
         preview: null, suggestions: null};
  await _ddLoadList();
  const pick = name || (_dd.list.find(x => !x.error) || {}).name;
  if (pick) await _ddLoad(pick);
  else await _ddRefreshPageMap();
  _ddOpenPanel();
}

async function _ddLoadList() {
  try {
    const r = await fetch(`${API}/api/datasets?folder=${encodeURIComponent(folder)}`);
    _dd.list = (await r.json()).datasets || [];
  } catch (e) { _dd.list = []; }
}

async function _ddLoad(name) {
  try {
    const r = await fetch(`${API}/api/dataset/${encodeURIComponent(name)}/declaration?folder=${encodeURIComponent(folder)}`);
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const d = await r.json();
    _dd.decl = d.declaration;
    _dd.decl.record = _dd.decl.record || {};
    _dd.decl.record.key = _dd.decl.record.key || {slot: 1, column: null, dtype: 'entity'};
    _dd.decl.record.exclude_keys = _dd.decl.record.exclude_keys || [];
    _dd.decl.identities = _dd.decl.identities || [];
    _dd.decl.parse = _dd.decl.parse || {missing: ['-', '—', '·', ''], thousands: [' ', '.'], decimal: ','};
    _dd.decl.scope = _dd.decl.scope || {labels: [], pattern: '1', tables: [0]};
    _dd.name = name; _dd.isNew = false; _dd.preview = null; _dd.suggestions = null;
  } catch (e) { showToast('Could not load declaration: ' + (e.message || e)); }
  await _ddRefreshPageMap();
}

async function _ddRefreshPageMap() {
  try {
    const r = await fetch(`${API}/api/dataset/page-map?folder=${encodeURIComponent(folder)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pattern: _dd.decl.scope.pattern || '1', pages: _dd.decl.scope.pages || null})});
    _dd.pageMap = await r.json();
  } catch (e) { _dd.pageMap = null; }
}

function _ddCurrentSlot() {
  const m = _dd?.pageMap, stem = pages[pageIdx]?.stem;
  if (!m || !stem) return null;
  const i = m.stems.indexOf(stem);
  return i >= 0 ? m.slots[i] : null;
}

// ── panel chrome ───────────────────────────────────────────────────────────
function _ddOpenPanel() {
  document.getElementById('dd-panel')?.remove();
  const p = document.createElement('div');
  p.id = 'dd-panel';
  p.style.cssText = 'position:fixed;top:56px;right:10px;bottom:10px;width:min(720px,62vw);z-index:1700;' +
    'background:#0a1128;border:1px solid #2a4a8e;border-radius:8px;box-shadow:0 12px 40px rgba(0,0,0,0.7);' +
    'display:flex;flex-direction:column;font-size:12px;color:#ccc;';
  p.innerHTML = '<div id="dd-head" style="padding:8px 12px;border-bottom:1px solid #1a3a6e;display:flex;gap:8px;align-items:center;flex-wrap:wrap;"></div>' +
                '<div id="dd-body" style="flex:1;overflow-y:auto;padding:4px 14px 20px;"></div>';
  document.body.appendChild(p);
  _ddRender();
}

function closeDatasetDecl() {
  document.getElementById('dd-panel')?.remove();
  _ddUnhighlight();
}

const _ddInput = 'background:#0d1b35;border:1px solid #0f3460;color:#ccc;border-radius:3px;padding:3px 6px;font-size:12px;';
const _ddH = (t, sub) => `<div style="margin:16px 0 6px;"><b style="color:#e94560;font-size:13px;">${t}</b>` +
  (sub ? `<div style="color:#8a94a6;font-size:11px;margin-top:2px;">${sub}</div>` : '') + '</div>';

function _ddRender() {
  _ddRenderHead();
  const b = document.getElementById('dd-body');
  if (!b) return;
  b.innerHTML = _ddPagesSection() + _ddVarsSection() + _ddRowsSection() +
                _ddSumsSection() + _ddFormatSection() + _ddResultsSection();
}

function _ddRenderHead() {
  const h = document.getElementById('dd-head');
  if (!h) return;
  const opts = _dd.list.map(x => `<option value="${_escHtml(x.name)}" ${x.name === _dd.name ? 'selected' : ''}>` +
    `${_escHtml(x.name)}${x.error ? ' ⚠' : ''}</option>`).join('');
  h.innerHTML =
    '<b style="color:#e0e0e0;font-size:13px;">📋 Dataset</b>' +
    `<select onchange="_ddSwitch(this.value)" style="${_ddInput}">${opts}` +
      `<option value="__new__" ${_dd.isNew ? 'selected' : ''}>➕ new dataset…</option></select>` +
    (_dd.isNew ? `<input id="dd-name" placeholder="name, e.g. machines_main" value="${_escHtml(_dd.decl.name || '')}" ` +
                 `onchange="_dd.decl.name=this.value.trim()" style="${_ddInput}width:170px;">` : '') +
    '<div style="flex:1"></div>' +
    '<button class="nav-btn" onclick="_ddTest()" title="Build the dataset from the pages with these settings — nothing is saved">▶ Test</button>' +
    '<button class="nav-btn" onclick="_ddSave()" style="color:#86efac;">💾 Save</button>' +
    (!_dd.isNew ? '<button class="nav-btn" onclick="_ddOpenQuality()" title="Open the data-quality page for this dataset">📈 Quality</button>' : '') +
    (!_dd.isNew ? '<button class="nav-btn" onclick="_ddDelete()" title="Delete this declaration (kept as a backup file)" style="color:#fca5a5;">🗑</button>' : '') +
    '<button class="nav-btn" onclick="closeDatasetDecl()">✕</button>';
}

async function _ddSwitch(v) {
  if (v === '__new__') {
    _dd.decl = _ddBlank(); _dd.name = null; _dd.isNew = true;
    _dd.preview = null; _dd.suggestions = null;
    await _ddRefreshPageMap();
  } else {
    await _ddLoad(v);
  }
  _ddRender();
}

// ── section 1: which pages ─────────────────────────────────────────────────
function _ddBits() {
  return (_dd.decl.scope.pattern || '1').split(',').map(s => s.trim() === '1' ? 1 : 0);
}

function _ddPagesSection() {
  const bits = _ddBits(), n = bits.length;
  const ord = i => ['1st', '2nd', '3rd'][i] || `${i + 1}th`;
  let html = _ddH('1 · Which pages',
    'Books often alternate tables: if this table spreads over 2 facing pages and the next 2 pages hold another table, ' +
    'the cycle is 4 pages and this dataset uses the 1st and 2nd. Each page it uses is a “slot” (left page = slot 1, right page = slot 2).');
  html += `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">Cycle of
    <select style="${_ddInput}" onchange="_ddSetCycle(+this.value)">
      ${[1, 2, 3, 4, 5, 6].map(k => `<option ${k === n ? 'selected' : ''}>${k}</option>`).join('')}
    </select> page(s); this dataset uses:
    ${bits.map((b, i) => `<label style="cursor:pointer;"><input type="checkbox" ${b ? 'checked' : ''} ` +
      `onchange="_ddToggleBit(${i},this.checked)" style="accent-color:#e94560;"> ${ord(i)}</label>`).join(' ')}
  </div>`;
  const m = _dd.pageMap;
  if (m) {
    const bySlot = {};
    m.slots.forEach((s, i) => { if (s) (bySlot[s] = bySlot[s] || []).push(i + 1); });
    const cur = _ddCurrentSlot();
    html += `<div style="margin-top:6px;color:#8a94a6;">` +
      Object.entries(bySlot).map(([s, ps]) => `slot ${s}: pages ${ps.slice(0, 6).join(', ')}${ps.length > 6 ? ', …' : ''} (${ps.length})`).join(' · ') +
      `<br>This page (p${pageIdx + 1}) is ${cur ? `<b style="color:#93c5fd;">slot ${cur}</b>` : '<b style="color:#fbbf24;">not part of this dataset</b>'}.</div>`;
  }
  html += `<div style="margin-top:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
    Only pages <input style="${_ddInput}width:110px;" placeholder="all (e.g. 1-120)" value="${_escHtml(_dd.decl.scope.pages || '')}"
      onchange="_dd.decl.scope.pages=this.value.trim()||null;_ddRefreshPageMap().then(_ddRender)">
    · read cells labelled
    ${[...new Set([...(projectLabels || []), ..._ddPageLatticeLabels(), ...(_dd.decl.scope.labels || [])])].map(l =>
      `<label style="cursor:pointer;"><input type="checkbox" ${(_dd.decl.scope.labels || []).includes(l) ? 'checked' : ''} ` +
      `onchange="_ddToggleLabel('${_escHtml(l)}',this.checked)" style="accent-color:#e94560;"> ${_escHtml(l)}</label>`).join(' ')}
  </div>`;
  const rec = _dd.decl.record;
  html += `<details style="margin-top:6px;"><summary style="cursor:pointer;color:#8a94a6;">advanced</summary>
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:6px;align-items:center;">
      record =
      <select style="${_ddInput}" onchange="_dd.decl.record.unit=this.value" title="internal row: one record per text line inside a cell (the usual case); lattice row: one record per grid row">
        <option value="internal_row" ${rec.unit !== 'lattice_row' ? 'selected' : ''}>one text line (internal row)</option>
        <option value="lattice_row" ${rec.unit === 'lattice_row' ? 'selected' : ''}>one grid row (lattice row)</option></select>
      slots are joined
      <select style="${_ddInput}" onchange="_dd.decl.record.join=this.value" title="by position: the n-th line of the left page belongs to the n-th line of the right page; by key: both pages repeat the name column and are matched by it">
        <option value="positional" ${rec.join !== 'keyed' ? 'selected' : ''}>by position</option>
        <option value="keyed" ${rec.join === 'keyed' ? 'selected' : ''}>by the key column</option></select>
      tables <input style="${_ddInput}width:60px;" value="${_escHtml((_dd.decl.scope.tables || [0]).join(','))}"
        onchange="_dd.decl.scope.tables=this.value.split(',').map(s=>parseInt(s,10)).filter(n=>!isNaN(n))">
    </div></details>`;
  return html;
}

async function _ddSetCycle(n) {
  const bits = _ddBits();
  const nb = Array.from({length: n}, (_, i) => bits[i] ?? 0);
  if (!nb.some(Boolean)) nb[0] = 1;
  _dd.decl.scope.pattern = nb.join(',');
  await _ddRefreshPageMap(); _ddRender();
}

async function _ddToggleBit(i, on) {
  const bits = _ddBits(); bits[i] = on ? 1 : 0;
  _dd.decl.scope.pattern = bits.join(',');
  await _ddRefreshPageMap(); _ddRender();
}

function _ddToggleLabel(l, on) {
  const s = new Set(_dd.decl.scope.labels || []);
  on ? s.add(l) : s.delete(l);
  _dd.decl.scope.labels = [...s];
}

// ── section 2: columns → variables ─────────────────────────────────────────
function _ddCellText(sh) {
  return (sh.human_output?.human_corrected_text || sh.openai_output?.response ||
          sh.tesseract_output?.ocr_text || sh.easyocr_output?.ocr_text || '');
}

function _ddPageColumns() {
  // {column: {labels:Set, samples:[], idx:[shape indices]}} for the current page
  const labels = new Set(_dd.decl.scope.labels || []);
  const tables = new Set(_dd.decl.scope.tables || [0]);
  const cols = {};
  (pageData?.shapes || []).forEach((sh, i) => {
    if (sh.super_column == null || !tables.has(sh.table || 0)) return;
    if (labels.size && !labels.has(sh.label)) return;
    const c = cols[sh.super_column] = cols[sh.super_column] || {labels: new Set(), samples: [], idx: [], sr: []};
    c.labels.add(sh.label); c.idx.push(i); c.sr.push([sh.super_row, sh]);
  });
  Object.values(cols).forEach(c => {
    c.sr.sort((a, b) => a[0] - b[0]);
    for (const [, sh] of c.sr) {
      const rows = sh.row_struct?.rows;
      const texts = rows ? rows.map(r => r.human || r.llm || r.ocr || '') : _ddCellText(sh).split('\n');
      for (const t of texts) { if (t.trim() && c.samples.length < 4) c.samples.push(t.trim()); }
      if (c.samples.length >= 4) break;
    }
  });
  return cols;
}

function _ddReadColumns() {
  const slot = _ddCurrentSlot();
  if (!slot) { showToast('This page is not part of the dataset — go to a page of the right slot'); return; }
  const cols = _ddPageColumns();
  const vars = _dd.decl.variables;
  let added = 0, firstText = null;
  Object.keys(cols).map(Number).sort((a, b) => a - b).forEach(c => {
    if (vars.some(v => v.slot === slot && v.column === c)) return;
    const isText = [...cols[c].labels].some(l => /text/i.test(l));
    const v = {name: '', slot, column: c, dtype: isText ? 'text' : 'number', label: ''};
    if (isText && firstText == null) firstText = v;
    vars.push(v); added++;
  });
  const key = _dd.decl.record.key;
  if (firstText && key.column == null) {
    firstText.dtype = 'entity';
    key.slot = slot; key.column = firstText.column; key.dtype = 'entity';
  }
  vars.sort((a, b) => a.slot - b.slot || a.column - b.column);
  showToast(added ? `Added ${added} column(s) from slot ${slot} — now name them` : 'All columns of this page are already listed');
  _ddRender();
}

function _ddVarsSection() {
  const slot = _ddCurrentSlot();
  const cols = slot ? _ddPageColumns() : {};
  const pv = {};
  (_dd.preview?.variables || []).forEach(v => { pv[v.name] = v; });
  const key = _dd.decl.record.key;
  let html = _ddH('2 · Columns → variables',
    'Name every column you want in the dataset (short names, no spaces — they become the column names of the export). ' +
    'Mark the column that identifies a row as the key (usually the place name). Hover a row to see the column on the page.');
  html += `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px;">
    <button class="nav-btn" onclick="_ddReadColumns()" ${slot ? '' : 'disabled'}
      title="List every lattice column of THIS page as a variable of slot ${slot || '?'} (existing ones are kept)">⬇ Read columns from this page${slot ? ` (slot ${slot})` : ''}</button>
    <span style="color:#667;">Do it once on a page of each slot.</span></div>`;
  const vars = _dd.decl.variables;
  if (!vars.length) return html + '<div style="color:#667;">No variables yet.</div>';
  html += `<div style="overflow-x:auto;"><table class="rs-table" style="width:100%;">
    <tr><th title="the column that identifies a record">key</th><th>slot·col</th><th>name</th><th>type</th>
    <th>printed header</th><th title="hard bounds">min</th><th>max</th>
    <th title="include in statistical checks (switch off for row numbers / codes)">stats</th>
    <th>${_dd.preview ? 'read ✓ / ✗' : 'sample values (this page)'}</th><th></th></tr>`;
  vars.forEach((v, i) => {
    const isKey = key.slot === v.slot && key.column === v.column;
    const p = pv[v.name];
    const sample = p
      ? `<span style="color:#86efac;">${p.ok}</span> / <span style="color:${p.error ? '#f87171' : '#667'};" title="${_escHtml((p.errors || []).join('  ·  '))}">${p.error}</span>` +
        `<span style="color:#667;"> ${_escHtml((p.samples || []).slice(0, 3).join(' · '))}</span>`
      : (v.slot === slot && cols[v.column] ? `<span style="color:#667;">${_escHtml(cols[v.column].samples.join(' · '))}</span>` : '');
    const nameBad = !v.name || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(v.name);
    html += `<tr onmouseenter="_ddHighlight(${i})" onmouseleave="_ddUnhighlight()">
      <td><input type="radio" name="dd-key" ${isKey ? 'checked' : ''} onchange="_ddSetKey(${i})"></td>
      <td style="white-space:nowrap;color:#93c5fd;">${v.slot}·${v.column}</td>
      <td><input value="${_escHtml(v.name || '')}" placeholder="name" onchange="_ddVarSet(${i},'name',this.value.trim())"
           style="${_ddInput}width:130px;${nameBad ? 'border-color:#c04040;' : ''}"></td>
      <td><select style="${_ddInput}" onchange="_ddVarSet(${i},'dtype',this.value)">
        ${Object.entries(_DD_TYPES).map(([k, lbl]) => `<option value="${k}" ${v.dtype === k ? 'selected' : ''}>${lbl}</option>`).join('')}</select></td>
      <td><input value="${_escHtml(v.label || '')}" placeholder="as printed" onchange="_ddVarSet(${i},'label',this.value)"
           style="${_ddInput}width:150px;"></td>
      <td><input value="${v.min ?? ''}" onchange="_ddVarSet(${i},'min',this.value)" style="${_ddInput}width:44px;"></td>
      <td><input value="${v.max ?? ''}" onchange="_ddVarSet(${i},'max',this.value)" style="${_ddInput}width:44px;"></td>
      <td><input type="checkbox" ${v.stats === false ? '' : 'checked'} onchange="_ddVarSet(${i},'stats',this.checked)"></td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${sample}</td>
      <td><span style="cursor:pointer;color:#fca5a5;" title="remove" onclick="_ddVarDel(${i})">✕</span></td></tr>`;
  });
  html += '</table></div>';
  const kv = vars.find(v => key.slot === v.slot && key.column === v.column);
  html += `<div style="margin-top:6px;color:#8a94a6;">Key: ${kv ? `<b style="color:#ccc;">${_escHtml(kv.name || `${kv.slot}·${kv.column}`)}</b>` : '<b style="color:#fbbf24;">none — pick one</b>'}
    ${key.dtype === 'entity' ? ` · resolves against authority <input style="${_ddInput}width:120px;" value="${_escHtml(key.authority || '')}" placeholder="e.g. places_hu" onchange="_dd.decl.record.key.authority=this.value.trim()||null">` : ''}</div>`;
  return html;
}

function _ddVarSet(i, field, val) {
  const v = _dd.decl.variables[i];
  if (!v) return;
  const key = _dd.decl.record.key;
  const wasKey = key.slot === v.slot && key.column === v.column;
  if (field === 'min' || field === 'max') {
    const n = parseFloat(val);
    if (val === '' || isNaN(n)) delete v[field]; else v[field] = n;
  } else if (field === 'stats') {
    if (val) delete v.stats; else v.stats = false;
  } else if (field === 'label') {
    v.label = val || undefined;
  } else {
    v[field] = val;
  }
  if (field === 'dtype' && wasKey) key.dtype = val === 'entity' ? 'entity' : 'text';
  if (field === 'name' || field === 'dtype') _ddRender();
}

function _ddVarDel(i) {
  const v = _dd.decl.variables[i];
  const used = _dd.decl.identities.some(id => id.total === v.name || id.parts.includes(v.name));
  if (used && !confirm(`"${v.name}" is used in a declared sum — remove it anyway (the sum goes too)?`)) return;
  _dd.decl.identities = _dd.decl.identities.filter(id => id.total !== v.name && !id.parts.includes(v.name));
  _dd.decl.variables.splice(i, 1);
  _ddRender();
}

function _ddSetKey(i) {
  const v = _dd.decl.variables[i];
  const k = _dd.decl.record.key;
  k.slot = v.slot; k.column = v.column;
  k.dtype = v.dtype === 'entity' ? 'entity' : 'text';
  _ddRender();
}

let _ddSavedSel = null;
function _ddHighlight(i) {
  const v = _dd.decl.variables[i];
  if (!v || v.slot !== _ddCurrentSlot()) return;
  const col = _ddPageColumns()[v.column];
  if (!col) return;
  if (_ddSavedSel == null) _ddSavedSel = {sel: new Set(selSet), idx: selIdx};
  selSet = new Set(col.idx); selIdx = -1;
  drawOverlay();
}
function _ddUnhighlight() {
  if (_ddSavedSel == null) return;
  selSet = _ddSavedSel.sel; selIdx = _ddSavedSel.idx; _ddSavedSel = null;
  drawOverlay();
}

// ── section 3: rows that are not records ───────────────────────────────────
function _ddRowsSection() {
  const ex = _dd.decl.record.exclude_keys || [];
  let html = _ddH('3 · Rows that are not records',
    'Totals, county and district lines sit among the data rows. A row whose key text matches any of these patterns ' +
    '(one per line; matched without accents and case, e.g. “osszesen” matches “Összesen”) is left out of the data.');
  html += `<div style="display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap;">
    <textarea rows="4" style="${_ddInput}width:280px;font-family:monospace;" onchange="_dd.decl.record.exclude_keys=this.value.split('\\n').map(s=>s.trim()).filter(Boolean)">${_escHtml(ex.join('\n'))}</textarea>
    <div style="display:flex;flex-direction:column;gap:6px;">
      <button class="nav-btn" onclick="_ddHuDefaults()" title="${_escHtml(_DD_HU_EXCLUDES.join('   '))}">＋ Hungarian statistics defaults</button>
      <label>Printed total rows <input style="${_ddInput}width:130px;" placeholder="e.g. osszesen" value="${_escHtml(_dd.decl.totals?.row_pattern || '')}"
        onchange="_dd.decl.totals=this.value.trim()?{...(_dd.decl.totals||{}),row_pattern:this.value.trim()}:null"
        title="Rows matching this are checked against the sum of the records above them. They must also match a pattern on the left."></label>
    </div></div>`;
  const xs = _dd.preview?.excluded_sample;
  if (xs?.length) html += `<div style="margin-top:4px;color:#8a94a6;">Left out by the last test: ${_escHtml(xs.slice(0, 15).join(' · '))}${xs.length > 15 ? ' …' : ''}</div>`;
  return html;
}

function _ddHuDefaults() {
  const s = new Set(_dd.decl.record.exclude_keys || []);
  _DD_HU_EXCLUDES.forEach(p => s.add(p));
  _dd.decl.record.exclude_keys = [...s];
  if (!_dd.decl.totals) _dd.decl.totals = {row_pattern: 'osszesen'};
  _ddRender();
}

// ── section 4: sums the book guarantees ────────────────────────────────────
function _ddNumVars() {
  return _dd.decl.variables.filter(v => (v.dtype === 'int' || v.dtype === 'number') && v.name);
}

function _ddSumsSection() {
  const ids = _dd.decl.identities;
  const nv = _ddNumVars();
  const st = {};
  (_dd.preview?.identities || []).forEach(s => { st[s.identity] = s; });
  let html = _ddH('4 · Sums the book guarantees',
    'Column totals printed in each row (e.g. “all holdings = sum of the size classes”). Every row where a sum does not hold is ' +
    'flagged on the data-quality page — usually a reading error, often with the exact fix.');
  html += `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px;">
    <button class="nav-btn" onclick="_ddSuggest()" title="Test which columns add up to which, across all pages (a few seconds)">🔍 Find sums in the data</button>
    <button class="nav-btn" onclick="_ddAddSum()">＋ Add sum</button>
    <button class="nav-btn" onclick="_ddImportRules()" title="Turn the page's ⚖ Rules (e.g. 1+2=4) into sums of named variables">⇩ Import from ⚖ Rules</button></div>`;
  if (_dd.suggestions) {
    const sg = _dd.suggestions;
    html += `<div style="background:#0d1b35;border:1px solid #1a3a6e;border-radius:4px;padding:6px 8px;margin-bottom:8px;">` +
      (sg.length ? sg.map((s, i) => `<div style="display:flex;gap:6px;align-items:baseline;margin:2px 0;">
        <span style="color:${s.strength === 'strong' ? '#86efac' : '#fbbf24'};min-width:46px;" title="${s.strength === 'strong' ? 'holds in ≥95% of rows' : 'holds in 80–95% — real, but many rows break it (reading errors?)'}">${Math.round(s.rate * 100)}%</span>
        <span style="flex:1;"><b>${_escHtml(s.total)}</b> = ${_escHtml(s.parts.join(' + '))} <span style="color:#667;">(${s.hold}/${s.testable} rows)</span></span>
        ${s.already_declared || _ddHasSum(s) ? '<span style="color:#667;">declared</span>'
          : `<button class="nav-btn" style="font-size:10px;padding:1px 6px;" onclick="_ddAcceptSuggestion(${i})">＋ add</button>`}</div>`).join('')
        : '<span style="color:#667;">No column adds up to a run of other columns in ≥80% of rows.</span>') + '</div>';
  }
  if (!ids.length) return html + '<div style="color:#667;">No sums declared.</div>';
  const opt = (sel) => nv.map(v => `<option ${v.name === sel ? 'selected' : ''}>${_escHtml(v.name)}</option>`).join('');
  ids.forEach((id, i) => {
    const s = st[id.label || `${id.total} = ${id.parts.join(' + ')}`];
    html += `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:4px 0;padding:4px 0;border-bottom:1px solid #1a2a4a;">
      <select style="${_ddInput}" onchange="_dd.decl.identities[${i}].total=this.value;_ddRender()">${opt(id.total)}</select> =
      ${id.parts.map((p, j) => `<span style="background:#12224a;border-radius:10px;padding:1px 8px;">${_escHtml(p)}
          <span style="cursor:pointer;color:#fca5a5;" onclick="_dd.decl.identities[${i}].parts.splice(${j},1);_ddRender()">×</span></span>`).join(' + ')}
      <select style="${_ddInput}" onchange="if(this.value){_dd.decl.identities[${i}].parts.push(this.value);_ddRender()}">
        <option value="">＋ part</option>${nv.filter(v => v.name !== id.total && !id.parts.includes(v.name)).map(v => `<option>${_escHtml(v.name)}</option>`).join('')}</select>
      <input style="${_ddInput}width:170px;" placeholder="description (optional)" value="${_escHtml(id.label || '')}"
        onchange="_dd.decl.identities[${i}].label=this.value.trim()||undefined">
      ${s ? `<span style="color:#86efac;">${s.ok} hold</span> <span style="color:${s.mismatch ? '#f87171' : '#667'};">${s.mismatch} broken</span>` : ''}
      <span style="cursor:pointer;color:#fca5a5;margin-left:auto;" title="remove" onclick="_dd.decl.identities.splice(${i},1);_ddRender()">✕</span></div>`;
  });
  return html;
}

function _ddHasSum(s) {
  return _dd.decl.identities.some(id => id.total === s.total &&
    id.parts.length === s.parts.length && id.parts.every(p => s.parts.includes(p)));
}

function _ddAddSum() {
  const nv = _ddNumVars();
  if (nv.length < 2) { showToast('Name at least two number columns first'); return; }
  _dd.decl.identities.push({total: nv[0].name, parts: []});
  _ddRender();
}

function _ddAcceptSuggestion(i) {
  const s = _dd.suggestions[i];
  _dd.decl.identities.push({total: s.total, parts: [...s.parts]});
  _ddRender();
}

async function _ddSuggest() {
  const bad = _ddProblemsLocal();
  if (bad.length) { showToast(bad[0]); return; }
  showToast('Testing which columns add up…');
  try {
    const r = await fetch(`${API}/api/dataset/suggest-identities?folder=${encodeURIComponent(folder)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({declaration: _ddForServer()})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    _dd.suggestions = d.suggestions;
    _ddRender();
  } catch (e) { showToast('Could not test sums: ' + (e.message || e), 6000); }
}

function _ddImportRules() {
  const rules = (typeof projectRules !== 'undefined' ? projectRules : []) || [];
  if (!rules.length) { showToast('This project has no ⚖ Rules'); return; }
  const m = _dd.pageMap;
  let added = 0, skipped = 0;
  rules.forEach(r => {
    const ex = (r.expr || '').replace(/\s/g, '');
    const [lhs, rhs] = ex.split('=');
    if (!lhs || !rhs) { skipped++; return; }
    let parts = lhs.split('+'), total = rhs.split('+');
    if (total.length > 1 && parts.length === 1) [parts, total] = [total, parts];
    if (total.length !== 1 || parts.some(p => !/^\d+$/.test(p)) || !/^\d+$/.test(total[0])) { skipped++; return; }
    // which slots does the rule's page pattern touch?
    const rb = (r.pattern || '').split(',').filter(s => s.trim() !== '').map(s => s.trim() === '1' ? 1 : 0);
    const slots = new Set();
    (m?.slots || []).forEach((s, i) => { if (s && (!rb.length || rb[i % rb.length])) slots.add(s); });
    let done = false;
    for (const s of [...slots].sort()) {
      const byCol = c => _dd.decl.variables.find(v => v.slot === s && v.column === +c && v.name);
      const tv = byCol(total[0]), pv = parts.map(byCol);
      if (!tv || pv.some(x => !x)) continue;
      const cand = {total: tv.name, parts: pv.map(x => x.name)};
      if (!_ddHasSum(cand)) {
        _dd.decl.identities.push({...cand, label: r.name || undefined});
        added++;
      }
      done = true; break;
    }
    if (!done) skipped++;
  });
  showToast(`Imported ${added} sum(s)` + (skipped ? ` · ${skipped} rule(s) skipped (columns not named yet, or not a simple a+b=c)` : ''), 6000);
  _ddRender();
}

// ── section 5: number format ───────────────────────────────────────────────
function _ddFormatSection() {
  const p = _dd.decl.parse;
  const th = new Set(p.thousands || []);
  const chk = (c, lbl) => `<label style="cursor:pointer;"><input type="checkbox" ${th.has(c) ? 'checked' : ''} ` +
    `onchange="_ddThousands('${c}',this.checked)" style="accent-color:#e94560;"> ${lbl}</label>`;
  const clash = th.has(p.decimal) && p.decimal;
  return _ddH('5 · How numbers are printed',
    'If the book prints no decimals at all, set the decimal mark to “none” and tick “,” as a thousands separator — ' +
    'then readings like 2,216 parse as 2216 instead of 2.216 or an error.') +
    `<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
      thousands separator: ${chk(' ', 'space')} ${chk('.', '.')} ${chk(',', ',')}
      · decimal mark <select style="${_ddInput}" onchange="_dd.decl.parse.decimal=this.value;_ddRender()">
        <option value="," ${p.decimal === ',' ? 'selected' : ''}>,</option>
        <option value="." ${p.decimal === '.' ? 'selected' : ''}>.</option>
        <option value="" ${!p.decimal ? 'selected' : ''}>none (no decimals in this book)</option></select>
      · “no value” marks <input style="${_ddInput}width:90px;" value="${_escHtml((p.missing || []).filter(Boolean).join(' '))}"
        onchange="_dd.decl.parse.missing=[...this.value.split(/\\s+/).filter(Boolean),'']" title="space-separated; an empty cell always counts as no value">
    </div>` +
    (clash ? `<div style="color:#f87171;margin-top:4px;">“${_escHtml(p.decimal)}” cannot be both the thousands separator and the decimal mark.</div>` : '');
}

function _ddThousands(c, on) {
  const s = new Set(_dd.decl.parse.thousands || []);
  on ? s.add(c) : s.delete(c);
  _dd.decl.parse.thousands = [...s];
  _ddRender();
}

// ── test / save / delete ───────────────────────────────────────────────────
function _ddProblemsLocal() {
  const d = _dd.decl, out = [];
  if (!d.variables.length) out.push('No variables yet — use “Read columns from this page”');
  const names = new Set();
  d.variables.forEach(v => {
    if (!v.name || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(v.name))
      out.push(`Column ${v.slot}·${v.column} needs a name (letters, digits, _)`);
    else if (names.has(v.name)) out.push(`The name "${v.name}" is used twice`);
    names.add(v.name);
  });
  if (d.record.key.column == null) out.push('Pick the key column (the radio button)');
  const p = d.parse;
  if (p.decimal && (p.thousands || []).includes(p.decimal)) out.push('The decimal mark is also a thousands separator');
  d.identities.forEach(id => { if (!id.parts.length) out.push(`The sum for ${id.total} has no parts`); });
  return out;
}

function _ddForServer() {
  const d = JSON.parse(JSON.stringify(_dd.decl));
  d.name = _dd.isNew ? (d.name || 'new_dataset') : _dd.name;
  if (!d.totals?.row_pattern) delete d.totals;
  if (!d.scope.pages) delete d.scope.pages;
  d.identities = d.identities.filter(id => id.parts.length);
  return d;
}

async function _ddTest() {
  const bad = _ddProblemsLocal();
  if (bad.length) { _dd.preview = {problems: bad}; _ddRender(); _ddScrollResults(); return; }
  showToast('Building the dataset from the pages…');
  try {
    const r = await fetch(`${API}/api/dataset/preview?folder=${encodeURIComponent(folder)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({declaration: _ddForServer()})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    _dd.preview = d;
  } catch (e) { _dd.preview = {problems: [String(e.message || e)]}; }
  _ddRender(); _ddScrollResults();
}

function _ddScrollResults() {
  document.getElementById('dd-results')?.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function _ddResultsSection() {
  const p = _dd.preview;
  if (!p) return _ddH('Test results', 'Press ▶ Test to build the dataset with these settings (nothing is saved).');
  let html = '<div id="dd-results">' + _ddH('Test results', '');
  if (p.problems?.length)
    return html + p.problems.map(x => `<div style="color:#f87171;">⚠ ${_escHtml(x)}</div>`).join('') + '</div>';
  const t = p.totals || {};
  html += `<div><b style="color:#e0e0e0;">${p.records}</b> records from ${p.pages} pages (${p.cycles} cycles)` +
    `${p.excluded_records ? ` · ${p.excluded_records} total/header rows left out` : ''}` +
    `${(t.anchored || t.unanchored) ? ` · printed totals: ${t.anchored} checkable, ${t.unanchored} not` : ''}</div>`;
  html += `<div style="margin-top:4px;color:${p.structure_n ? '#fbbf24' : '#86efac'};">${p.structure_n} layout problem(s)` +
    (p.structure_n ? ' — pages whose columns or rows do not match these settings:' : ' 🎉') + '</div>';
  (p.structure_sample || []).forEach(f => {
    html += `<div style="color:#8a94a6;font-size:11px;margin-left:10px;"><span style="color:#93c5fd;cursor:pointer;" ` +
      `onclick="_searchJump({stem:'${_escHtml(f.stem)}',idx:-1})">${_escHtml(f.stem.slice(-18))}</span> ${_escHtml(f.detail)}</div>`;
  });
  html += '<div style="color:#8a94a6;margin-top:4px;">Per-variable read counts are shown in the table above (✓ read / ✗ unreadable; hover ✗ for examples).</div>';
  return html + '</div>';
}

async function _ddSave() {
  const bad = _ddProblemsLocal();
  if (bad.length) { _dd.preview = {problems: bad}; _ddRender(); _ddScrollResults(); return; }
  const name = _dd.isNew ? (document.getElementById('dd-name')?.value || _dd.decl.name || '').trim() : _dd.name;
  if (!/^[A-Za-z0-9_\-]{1,80}$/.test(name || '')) { showToast('Give the dataset a name (letters, digits, _ and -)'); return; }
  if (_dd.isNew && _dd.list.some(x => x.name === name) &&
      !confirm(`A dataset "${name}" already exists — replace it?`)) return;
  const decl = _ddForServer(); decl.name = name;
  try {
    const r = await fetch(`${API}/api/dataset/${encodeURIComponent(name)}/declaration?folder=${encodeURIComponent(folder)}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({declaration: decl})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
  } catch (e) { showToast('Not saved: ' + (e.message || e), 8000); return; }
  _dd.name = name; _dd.isNew = false; _dd.decl.name = name;
  await _ddLoadList();
  _ddRenderHead();
  if (typeof _batchDatasetInit === 'function') _batchDatasetInit();
  showToast(`Saved “${name}” — 📈 Quality runs the checks`);
}

async function _ddDelete() {
  if (!confirm(`Delete the declaration “${_dd.name}”? (It is kept as a backup file; the pages are not touched.)`)) return;
  try {
    const r = await fetch(`${API}/api/dataset/${encodeURIComponent(_dd.name)}/declaration?folder=${encodeURIComponent(folder)}`, {method: 'DELETE'});
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
  } catch (e) { showToast('Delete failed: ' + (e.message || e)); return; }
  showToast(`Deleted “${_dd.name}”`);
  await _ddLoadList();
  await _ddSwitch((_dd.list[0] || {}).name || '__new__');
  if (typeof _batchDatasetInit === 'function') _batchDatasetInit();
}

function _ddOpenQuality() {
  window.open(`/static/quality.html?folder=${encodeURIComponent(folder)}&dataset=${encodeURIComponent(_dd.name)}` +
              `&labels=${encodeURIComponent((projectLabels || []).join(','))}`, '_blank');
}

// Re-render when the user pages through the book with the window open (the
// "this page is slot k" line and the sample values follow the page).
function _ddOnPageChange() {
  if (document.getElementById('dd-panel')) { _ddSavedSel = null; _ddRender(); }
}
