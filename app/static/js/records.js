// Split classic script (shared global scope, load order matters — see
// knowledge_base/02_architecture.md). Record chaining (§H5 of
// knowledge_base/09_hierarchical_layout_compass.md, applied to ONE parent
// label): an entry that runs across a column or page break is several boxes
// of the project's record label, one per fragment; a fragment carries
// `continuation: true` ("continues the previous box in reading order"); the
// sweep numbers records through the volume into `group_id`; `group_locked`
// protects hand-assigned ids. This file holds:
//   • the record context (project / record label / layout from the server)
//   • the ⛓ continuation toggle (inspector button + C hotkey)
//   • the per-record tint palette used by drawOverlay
//   • the sweep calls (full / dry run / forward from a page)
//   • the record browser on the report chassis (dup_report.js pattern:
//     minimizable pill, click-to-jump, in-place fixes)
// Reuses: _escHtml, _searchJump, _serializeWrite, showToast, replaceAllShapes,
// pushUndo, reloadPageData, drawOverlay, updatePanel, _batchTakeSnapshot.

let _recCtx = {project: null, record_label: null, layout_columns: 'auto', record_roles: {}, labels: [], record_authority: null, source_year: null};

async function loadRecordsContext() {
  _recCtx = {project: null, record_label: null, layout_columns: 'auto', record_roles: {}, labels: []};
  if (!folder) return;
  try {
    const r = await fetch(`${API}/api/records/context?folder=${encodeURIComponent(folder)}`);
    if (r.ok) _recCtx = await r.json();
  } catch (e) { /* outside a project: record tools stay off */ }
  updateRecordBtn();
  const rb = document.getElementById('records-btn');
  if (rb) rb.style.display = (_recCtx.record_label || (_recCtx.record_datasets || []).length) ? '' : 'none';   // the toolbar's way into the record browser
  if (pageData) drawOverlay();
}

function isRecordLabel(label) {
  return !!_recCtx.record_label && label === _recCtx.record_label;
}

// ── Tint by record id (cycling palette; record-label shapes only) ──────────
const RECORD_PALETTE = [
  '#f97316', '#22d3ee', '#a3e635', '#e879f9', '#facc15', '#60a5fa',
  '#fb7185', '#34d399', '#c084fc', '#f59e0b', '#2dd4bf', '#f472b6',
];
function recordColor(gid) {
  const n = Number(gid);
  if (!Number.isFinite(n)) return '#8a94a6';
  return RECORD_PALETTE[((n - 1) % RECORD_PALETTE.length + RECORD_PALETTE.length) % RECORD_PALETTE.length];
}

// ── Continuation toggle on the selection ────────────────────────────────────
function _recTargets() {
  const idxs = selSet && selSet.size > 1 ? [...selSet] : (selIdx >= 0 ? [selIdx] : []);
  return idxs.filter(i => pageData?.shapes?.[i] && isRecordLabel(pageData.shapes[i].label));
}

async function toggleContinuation() {
  const idxs = _recTargets();
  if (!idxs.length) {
    if (!_recCtx.record_label) showToast('No record label set for this project (dashboard → Records)');
    return;
  }
  // mixed selection → mark all; all already marked → clear all
  const makeCont = !idxs.every(i => !!pageData.shapes[i].continuation);
  pushUndo();
  idxs.forEach(i => {
    const sh = pageData.shapes[i];
    if (makeCont) sh.continuation = true; else delete sh.continuation;
  });
  const ok = await replaceAllShapes();
  if (ok) {
    showToast(makeCont
      ? `⛓ ${idxs.length} box${idxs.length > 1 ? 'es' : ''} marked as continuation — resweep to renumber`
      : `Continuation mark removed (${idxs.length}) — resweep to renumber`);
    updateRecordBtn(); drawOverlay();
  }
}

function updateRecordBtn() {
  const btn = document.getElementById('record-cont-btn');
  if (!btn) return;
  if (!_recCtx.record_label) { btn.style.display = 'none'; return; }
  const idxs = _recTargets();
  btn.style.display = '';
  if (!idxs.length) {
    btn.disabled = true;
    btn.textContent = `⛓ Continuation (C) — select a "${_recCtx.record_label}" box`;
    btn.style.background = '#1a2740'; btn.style.borderColor = '#3a4a6a';
    return;
  }
  btn.disabled = false;
  const all = idxs.every(i => !!pageData.shapes[i].continuation);
  const sh = pageData.shapes[idxs[0]];
  const idTxt = idxs.length === 1 && sh.group_id != null
    ? ` · record ${sh.group_id}${sh.group_locked ? ' 🔒' : ''}` : '';
  btn.textContent = (all ? '⛓ Un-mark continuation' : '⛓ Mark as continuation')
    + (idxs.length > 1 ? ` ${idxs.length}` : '') + ' (C)' + idTxt;
  btn.style.background = all ? '#3a2a10' : '#1a2740';
  btn.style.borderColor = all ? '#f0c040' : '#3a4a6a';
}

// ── Sweep ───────────────────────────────────────────────────────────────────
function _recFromDatasets() { return !!(_recCtx.record_datasets || []).length; }

// needSweep: the operation renumbers record-label boxes (sweep, fragment
// derivation) — a project whose records are dataset rows (13 §6) has none.
function _recNeedProject(needSweep = false) {
  if (!_recCtx.project) { showToast('Record tools need a project annotations folder'); return false; }
  if (needSweep && !_recCtx.record_label) {
    showToast(_recFromDatasets() ? 'The records of this project are the rows of its dataset declarations: there is nothing to sweep'
                                 : 'No record label set for this project (dashboard → Records)');
    return false;
  }
  if (!_recCtx.record_label && !_recFromDatasets()) {
    showToast('No record label or record datasets set for this project (dashboard → Records)'); return false;
  }
  return true;
}

function _recOrderNote(po) {
  if (!po) return '';
  return po.ok ? ' · page order ✓' : ` · ⚠ page order: ${po.prefixes.length > 1 ? po.prefixes.length + ' prefixes ' : ''}`
    + `${po.gaps?.length ? po.gaps.length + ' gap(s) ' : ''}${po.non_numeric?.length ? po.non_numeric.length + ' non-numeric ' : ''}`
    + `${po.duplicates?.length ? po.duplicates.length + ' dup(s)' : ''}`;
}

async function runRecordSweep(opts = {}) {
  if (!_recNeedProject(true)) return null;
  const body = {dry_run: !!opts.dryRun, from_stem: opts.fromStem || null};
  if (!opts.dryRun) {
    const stems = pages.map(p => p.stem);
    await _batchTakeSnapshot(stems, opts.fromStem ? `sweep_records from ${opts.fromStem}` : 'sweep_records');
  }
  showToast(opts.dryRun ? 'Dry run: sweeping records…' : 'Sweeping records…');
  let d;
  try {
    const r = await fetch(`${API}/api/project/${encodeURIComponent(_recCtx.project)}/sweep-records`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    d = await r.json().catch(() => ({}));
    if (!r.ok) { showToast('Sweep error: ' + (d.detail || r.status), 6000); return null; }
  } catch (e) { showToast('Sweep error: ' + (e?.message || e), 6000); return null; }
  const what = d.dry_run ? 'Dry run' : (d.from_stem ? `Resweep from ${d.from_stem}` : 'Sweep');
  showToast(`${what}: ${d.records} record(s) over ${d.boxes} box(es) on ${d.pages} page(s)` +
            ` · ${d.boxes_changed} id(s) ${d.dry_run ? 'would change' : 'changed'} on ${d.pages_changed} page(s)` +
            (d.orphans?.length ? ` · ⚠ ${d.orphans.length} orphan continuation(s)` : '') +
            _recOrderNote(d.page_order), 7000);
  if (!d.dry_run && pageData) { await reloadPageData(); drawOverlay(); updatePanel(); }
  return d;
}

// ── Record browser (report chassis) ─────────────────────────────────────────
let _recData = null;
// records from lattice rows (13 §6) have no sweep: no chain marks, no resweep
function _recSweepTools() { return !_recData || _recData.record_source !== 'dataset'; }
let _recMaxPages = 2;
let _recFlaggedOnly = false;
// The browser renders one page of records at a time: a ledger volume has
// tens of thousands of rows, each with several cell crops — all at once
// would freeze the tab. A text filter narrows the list (name, cell values,
// page number "p12"); the current page follows the focused record.
const _REC_PER_PAGE = 150;
let _recPage = 0;
let _recFilter = '';
function _recSetFilter(v) { _recFilter = (v || '').trim().toLowerCase(); _recPage = 0; _recRenderBody(); }
function _recGoPage(n) { _recPage = Math.max(0, n); _recRenderBody(); }
function _recVisible(d) {
  const pg = _recPageNo();
  const q = _recFilter;
  return d.records.filter(r => {
    if (_recUnresolvedOnly && r.authority) return false;
    if (!q) return true;
    if ((r.name || '').toLowerCase().includes(q)) return true;
    if (pg(r.page_span[0]).toLowerCase() === q || String(r.id) === q) return true;
    if (r.values && Object.values(r.values).some(v => String(v || '').toLowerCase().includes(q))) return true;
    if (r.authority && ((r.authority.id || '').toLowerCase() === q || (r.authority.name || '').toLowerCase().includes(q))) return true;
    return false;
  });
}
function _recFocusIndex(list) {
  // the index in `list` of the record the browser should show first after a
  // decision / chain mark / resweep (see _recFocus), or -1
  const f = _recFocus;
  if (!f || !list.length) return -1;
  const pno = s => { const m = /^p(\d+)$/.exec(_recPageNo()(s)); return m ? parseInt(m[1], 10) : 0; };
  const want = f.stem ? pno(f.stem) : null;
  for (let i = 0; i < list.length; i++) {
    const r = list[i];
    if ((f.id != null && r.id >= f.id) || (want != null && pno(r.page_span[0]) >= want)) return i;
  }
  return list.length - 1;
}
// Strip view: every fragment as a full-width crop, one under the other in
// record order, so a volume reads continuously (the list view's thumbnails
// cannot show a whole entry). Remembered per browser.
let _recStrip = (() => { try { return localStorage.getItem('recStripView') === '1'; } catch (e) { return false; } })();
function _recSetStrip(on) {
  _recStrip = !!on;
  try { localStorage.setItem('recStripView', _recStrip ? '1' : '0'); } catch (e) {}
  _recRenderBody();
}

async function openRecordBrowser(opts = {}) {
  if (!_recNeedProject()) return;
  if (opts.maxPages) _recMaxPages = opts.maxPages;
  if (opts.flaggedOnly != null) _recFlaggedOnly = !!opts.flaggedOnly;
  showToast('Loading records…');
  try {
    const r = await fetch(`${API}/api/project/${encodeURIComponent(_recCtx.project)}/records` +
                          `?max_pages=${_recMaxPages}&flagged_only=${_recFlaggedOnly ? 'true' : 'false'}`);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { showToast('Records error: ' + (d.detail || r.status), 6000); return; }
    _recData = d;
  } catch (e) { showToast('Records error: ' + (e?.message || e), 6000); return; }
  closeBatchModal?.();
  _recOpenModal();
}

function _recCloseModal() {
  document.getElementById('rec-browser-modal')?.remove();
  document.getElementById('rec-browser-pill')?.remove();
}

function _recMinimize() {
  const m = document.getElementById('rec-browser-modal');
  if (!m || document.getElementById('rec-browser-pill')) return;
  m.style.display = 'none';
  const pill = document.createElement('div');
  pill.id = 'rec-browser-pill';
  pill.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:1800;background:#12224a;' +
    'border:1px solid #2a4a8e;border-radius:18px;box-shadow:0 6px 22px rgba(0,0,0,0.55);' +
    'padding:7px 14px;font-size:12px;color:#cde;cursor:pointer;display:flex;gap:8px;align-items:center;';
  pill.title = 'Restore the record browser';
  pill.innerHTML = `⛓ Records <span style="color:#8a94a6;">(${_recData?.n_records ?? 0})</span>` +
    ` <span style="color:#93c5fd;">▲ restore</span>` +
    ` <span onclick="event.stopPropagation();_recCloseModal()" title="Close" style="color:#fca5a5;padding-left:4px;">✕</span>`;
  pill.onclick = _recRestore;
  document.body.appendChild(pill);
}

function _recRestore() {
  document.getElementById('rec-browser-pill')?.remove();
  const m = document.getElementById('rec-browser-modal');
  if (m) m.style.display = 'flex';
}

function _recOpenModal() {
  _recCloseModal();
  const d = _recData;
  const m = document.createElement('div');
  m.id = 'rec-browser-modal';
  m.style.cssText = 'position:fixed;inset:24px;z-index:1800;background:#0a1128;border:1px solid #2a4a8e;' +
    'border-radius:8px;box-shadow:0 12px 40px rgba(0,0,0,0.7);display:flex;flex-direction:column;' +
    'font-size:11px;color:#ccc;';
  const po = d.page_order || {};
  const summary = `${d.n_records} record(s) · ${d.boxes} "${d.record_label}" box(es) on ${d.pages} page(s)` +
    ` · columns: ${d.layout_columns}` +
    (d.n_orphans ? ` · <span style="color:#f87171;">⚠ ${d.n_orphans} orphan</span>` : '') +
    (d.n_long ? ` · <span style="color:#fbbf24;">${d.n_long} span &gt; ${d.max_pages} pages</span>` : '') +
    (d.n_unswept ? ` · <span style="color:#a78bfa;">${d.n_unswept} unswept</span>` : '') +
    (d.record_authority ? ` · <span style="color:#86efac;">🏛 ${d.n_resolved}/${d.n_records} resolved</span>` : '') +
    (po.ok ? ' · page order ✓' : ` · <span style="color:#f87171;" title="${_escHtml(po.note || '')}">⚠ page order</span>`);
  m.innerHTML =
    '<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid #1a3a6e;flex-wrap:wrap;">' +
      '<b style="color:#e0e0e0;font-size:13px;">⛓ Record browser</b>' +
      `<span style="color:#8a94a6;">${summary}</span>` +
      '<div style="flex:1"></div>' +
      '<label style="display:flex;gap:4px;align-items:center;color:#8a94a6;">flag span &gt;' +
        `<input type="number" id="rec-maxpages" value="${d.max_pages}" min="1" style="width:40px;" onchange="openRecordBrowser({maxPages: parseInt(this.value,10)||2})"> pages</label>` +
      '<label style="display:flex;gap:4px;align-items:center;color:#8a94a6;">' +
        `<input type="checkbox" ${_recFlaggedOnly ? 'checked' : ''} onchange="openRecordBrowser({flaggedOnly: this.checked})" style="accent-color:#e94560;">flagged only</label>` +
      (d.record_authority ? '<label style="display:flex;gap:4px;align-items:center;color:#8a94a6;">' +
        `<input type="checkbox" ${_recUnresolvedOnly ? 'checked' : ''} onchange="_recUnresolvedOnly = this.checked; _recRenderBody()" style="accent-color:#e94560;">unresolved only</label>` : '') +
      `<input id="rec-filter" value="${_escHtml(_recFilter)}" placeholder="filter: name, cell text, entity, p12…" ` +
        `style="width:200px;font-size:11px;background:#0a1128;color:#ddd;border:1px solid #2a4a8e;border-radius:3px;padding:3px 6px;" ` +
        `oninput="clearTimeout(window._recFilterT); window._recFilterT = setTimeout(() => _recSetFilter(this.value), 250)">` +
      (d.record_source === 'dataset' ? '' :
      '<button class="nav-btn" onclick="runRecordSweep({dryRun:true})" title="Report what a full sweep would change, without writing">🔍 Dry run</button>' +
      '<button class="nav-btn" onclick="_recResweepAll()" title="Renumber every record through the whole volume (locked ids kept)">⟳ Resweep all</button>') +
      '<button class="nav-btn" id="rec-strip-btn" onclick="_recSetStrip(!_recStrip)" ' +
        'title="Strip: every fragment as a full-width crop, one under the other in record order. List: compact rows.">' +
        (_recStrip ? '☰ List view' : '📖 Strip view') + '</button>' +
      '<button class="nav-btn" onclick="openRecordBrowser()" title="Reload the list">↻</button>' +
      '<span style="color:#556;font-size:10px;">clicking a fragment opens it in the editor and minimizes this browser</span>' +
      '<button class="nav-btn" onclick="_recMinimize()" title="Minimize to a pill (bottom right)">▁</button>' +
      '<button class="nav-btn" onclick="_recCloseModal()" title="Close">✕</button>' +
    '</div>' +
    '<div id="rec-browser-body" style="flex:1;overflow-y:auto;padding:8px 12px;"></div>';
  document.body.appendChild(m);
  _recRenderBody();
  _recScrollToFocus();
}

function _recPageNo() {
  const stemToPage = {};
  pages.forEach((p, i) => { stemToPage[p.stem] = i + 1; });
  return s => (stemToPage[s] ? `p${stemToPage[s]}` : s);
}

// Full-size view of a fragment crop (the thumbnails are too small to judge
// a whole firm entry). Click anywhere or press Esc to close.
function _recShowCrop(src) {
  const old = document.getElementById('rec-crop-overlay');
  if (old) old.remove();
  const ov = document.createElement('div');
  ov.id = 'rec-crop-overlay';
  ov.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,0.85);display:flex;' +
                     'align-items:center;justify-content:center;cursor:zoom-out;overflow:auto;';
  const img = document.createElement('img');
  img.src = src;
  img.style.cssText = 'max-width:96vw;max-height:94vh;object-fit:contain;background:#fff;box-shadow:0 0 24px #000;';
  ov.appendChild(img);
  const close = () => { ov.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  ov.addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  document.body.appendChild(ov);
}

// One fragment in the strip view: a thin toolbar (page · #idx → jump,
// continuation toggle, resweep-from) above the crop at the browser's full
// width. A continuation is edged yellow, a record head in its record colour.
function _recFragStrip(f, gid) {
  const pg = _recPageNo();
  const src = `${API}/api/cell?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(f.stem)}&idx=${f.idx}`;
  const edge = f.continuation ? '#f0c040' : (gid != null ? recordColor(gid) : '#556');
  const contStyle = f.continuation ? 'background:#3a2a10;border-color:#f0c040;color:#f0c040;' : '';
  return `<div style="margin:0 0 6px;border-left:4px solid ${edge};padding-left:8px;">` +
    `<div style="display:flex;gap:8px;align-items:center;margin:2px 0;">` +
      `<span class="rs-clk" style="color:#93c5fd;cursor:pointer;white-space:nowrap;" onclick="_recJump('${_escHtml(f.stem)}',${f.idx})" ` +
        `title="Open this fragment in the editor">${_escHtml(pg(f.stem))} · #${f.idx}</span>` +
      (f.continuation ? '<span style="color:#f0c040;">⛓ continuation</span>' : '<span style="color:#8a94a6;">record head</span>') +
      `<span style="flex:1;color:#667;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_escHtml(f.text || '')}</span>` +
      (_recSweepTools() ? `<button class="nav-btn" style="${contStyle}" onclick="_recToggleFrag('${_escHtml(f.stem)}',${f.idx},${f.continuation ? 'true' : 'false'})" ` +
        `title="${f.continuation ? 'Un-mark: this box STARTS a record' : 'Mark: this box CONTINUES the previous box in reading order'}">⛓${f.continuation ? ' cont.' : ''}</button>` +
      `<button class="nav-btn" onclick="_recResweepFrom('${_escHtml(f.stem)}')" title="Renumber from this page onward (earlier pages keep their ids)">⟳▶</button>` : '') +
      (f.locked ? ' <span title="hand-assigned id, kept by the sweep">🔒</span>' : '') +
    `</div>` +
    `<img src="${src}" loading="lazy" style="width:100%;display:block;background:#fff;border:1px solid #2a2a4a;border-radius:2px;">` +
    `</div>`;
}

function _recFragRow(f, gid) {
  const pg = _recPageNo();
  const src = `${API}/api/cell?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(f.stem)}&idx=${f.idx}`;
  const contStyle = f.continuation ? 'background:#3a2a10;border-color:#f0c040;color:#f0c040;' : '';
  return `<tr>` +
    `<td class="rs-clk" style="white-space:nowrap;color:#93c5fd;" onclick="_recJump('${_escHtml(f.stem)}',${f.idx})" ` +
      `title="Open this fragment in the editor">${_escHtml(pg(f.stem))} · #${f.idx}</td>` +
    `<td class="rs-img"><img src="${src}" loading="lazy" onclick="_recShowCrop(this.src)" title="Click to enlarge" ` +
      `style="max-height:150px;max-width:400px;object-fit:contain;display:block;cursor:zoom-in;"></td>` +
    `<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${_escHtml(f.text || '')}">${_escHtml(f.text || '')}</td>` +
    `<td style="white-space:nowrap;">` +
      (_recSweepTools() ? `<button class="nav-btn" style="${contStyle}" onclick="_recToggleFrag('${_escHtml(f.stem)}',${f.idx},${f.continuation ? 'true' : 'false'})" ` +
        `title="${f.continuation ? 'Un-mark: this box STARTS a record' : 'Mark: this box CONTINUES the previous box in reading order'}">⛓${f.continuation ? ' cont.' : ''}</button> ` +
      `<button class="nav-btn" onclick="_recResweepFrom('${_escHtml(f.stem)}')" title="Renumber from this page onward (earlier pages keep their ids)">⟳▶</button>` : '') +
      (f.locked ? ' <span title="hand-assigned id, kept by the sweep">🔒</span>' : '') +
    `</td></tr>`;
}

function _recRenderBody() {
  const body = document.getElementById('rec-browser-body');
  if (!body || !_recData) return;
  const d = _recData;
  const pg = _recPageNo();
  if (!d.records.length && !d.unswept.length) {
    body.innerHTML = `<div style="padding:20px;color:#8a94a6;">${d.record_source === 'dataset' ? `No records from ${_escHtml((d.record_datasets || []).join(', '))}` : `No "${_escHtml(d.record_label)}" boxes`}${_recFlaggedOnly ? ' with flags' : ''} — ` +
      (d.boxes ? 'nothing flagged. 🎉' : 'draw or detect them first, then sweep.') + '</div>';
    return;
  }
  let html = '';
  const list = _recVisible(d);
  const fi = _recFocusIndex(list);
  if (fi >= 0) _recPage = Math.floor(fi / _REC_PER_PAGE);
  const nPages = Math.max(1, Math.ceil(list.length / _REC_PER_PAGE));
  if (_recPage >= nPages) _recPage = nPages - 1;
  const from = _recPage * _REC_PER_PAGE, to = Math.min(list.length, from + _REC_PER_PAGE);
  const pager = list.length > _REC_PER_PAGE || _recFilter || _recUnresolvedOnly
    ? `<div style="display:flex;gap:8px;align-items:center;margin:4px 0 8px;color:#8a94a6;">` +
        `<button class="nav-btn" onclick="_recGoPage(${_recPage - 1})" ${_recPage ? '' : 'disabled'}>◀</button>` +
        `<span>${list.length ? `${from + 1}–${to} of ${list.length}` : 'no record matches'}${list.length !== d.records.length ? ` (of ${d.records.length})` : ''}</span>` +
        `<button class="nav-btn" onclick="_recGoPage(${_recPage + 1})" ${to < list.length ? '' : 'disabled'}>▶</button>` +
        `<input type="number" min="1" max="${nPages}" value="${_recPage + 1}" style="width:54px;" onchange="_recGoPage(parseInt(this.value,10) - 1 || 0)"> / ${nPages}</div>`
    : '';
  html += pager;
  list.slice(from, to).forEach(r => {
    const span = r.page_span[0] === r.page_span[1] ? pg(r.page_span[0]) : `${pg(r.page_span[0])}–${pg(r.page_span[1])}`;
    const flags = (r.flags || []).map(f => f === 'orphan'
      ? '<span style="color:#f87171;" title="the first fragment is marked as a continuation — its head is missing or lies on an earlier page">⚠ orphan</span>'
      : `<span style="color:#fbbf24;" title="spans more than ${d.max_pages} pages — check for a missed record start">long span</span>`).join(' ');
    const vals = r.values
      ? `<div style="margin:0 0 4px 24px;color:#8a94a6;font-size:11px;">` + Object.entries(r.values).filter(([, v]) => v)
          .map(([k, v]) => `<span style="color:#667;">${_escHtml(k)}</span> ${_escHtml(String(v).slice(0, 140))}`).join(' · ') + `</div>`
      : '';
    html += `<div id="rec-head-${r.id}" style="margin:10px 0 4px;padding:4px 6px;background:#12224a;border-radius:4px;display:flex;gap:10px;align-items:baseline;">` +
      `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${recordColor(r.id)};align-self:center;"></span>` +
      `<b style="color:#93c5fd;font-size:12px;">record ${r.id}</b>` +
      `<span style="color:#cde;max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_escHtml(r.name || '')}</span>` +
      `<span style="color:#667;">${r.n_fragments} fragment${r.n_fragments === 1 ? '' : 's'} · ${span}</span>` +
      (flags ? `<span>${flags}</span>` : '') + _recStructChip(r) + _recAuthChip(r) + `</div>` + vals + `<div id="rec-cand-${r.id}"></div>`;
    if (_recStrip) {
      r.fragments.forEach(f => { html += _recFragStrip(f, r.id); });
    } else {
      html += '<table class="rs-table" style="width:100%;"><tr><th style="white-space:nowrap;">where</th><th></th><th>text</th><th></th></tr>';
      r.fragments.forEach(f => { html += _recFragRow(f, r.id); });
      html += '</table>';
    }
  });
  html += pager;
  const btn = document.getElementById('rec-strip-btn');
  if (btn) btn.textContent = _recStrip ? '☰ List view' : '📖 Strip view';
  if (d.unswept.length && !_recFlaggedOnly && to >= list.length) {
    html += `<div style="margin:14px 0 4px;padding:4px 6px;background:#2a1a3a;border-radius:4px;">` +
      `<b style="color:#a78bfa;font-size:12px;">unswept</b> <span style="color:#667;">${d.unswept.length} box(es) without a record id — run the sweep</span></div>`;
    html += '<table class="rs-table" style="width:100%;"><tr><th style="white-space:nowrap;">where</th><th></th><th>text</th><th></th></tr>';
    d.unswept.forEach(f => { html += _recFragRow(f, null); });
    html += '</table>';
  }
  body.innerHTML = html;
}

async function _recJump(stem, idx) {
  _recMinimize();
  await _searchJump({stem, idx});
}

async function _recSyncEditor(stem) {
  if (pages[pageIdx]?.stem === stem) { await reloadPageData(); drawOverlay(); updatePanel(); }
}

async function _recToggleFrag(stem, idx, cur) {
  const params = new URLSearchParams({folder, stem, idx});
  const ok = await _serializeWrite(async () => {
    try {
      const r = await fetch(`${API}/api/page/shape?${params}`, {
        method: 'PATCH', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({continuation: !cur})});
      return r.ok;
    } catch (e) { return false; }
  });
  if (!ok) { showToast('Save failed — the mark was NOT stored'); return; }
  // The mark alone changes nothing visible: records are the sweep's numbering.
  // Renumber from this page onward right away (earlier pages keep their ids).
  const d = await runRecordSweep({fromStem: stem, quiet: true});
  showToast((!cur ? `⛓ ${stem} #${idx} now continues the previous record` : `⛓ ${stem} #${idx} starts its own record again`) +
    (d ? ` · renumbered from ${stem}: ${d.records} record(s)` : ' · renumbering failed — press ⟳▶'), 6000);
  await _recSyncEditor(stem);
  _recFocus = {stem};
  await openRecordBrowser();
}

async function _recResweepFrom(stem) {
  const d = await runRecordSweep({fromStem: stem});
  _recFocus = {stem};
  if (d) await openRecordBrowser();
}

async function _recResweepAll() {
  if (!(await _batchConfirmInline('Renumber every record through the whole volume? Locked ids are kept; everything else is reassigned in reading order. (Batch undo restores the previous ids.)', '▶ Resweep'))) return;
  const d = await runRecordSweep({});
  if (d) await openRecordBrowser();
}

// ── Derive fragments from start / stop / ignore roles (app/fragments.py) ────
// Roles live in config.record_roles = {label: start|stop|ignore}; the batch
// op derives the record-label boxes (with ⛓ continuation marks) from the
// role shapes, so the detector only has to learn the small, consistent
// objects. Dry run first, then confirm, snapshot, real run (+ optional sweep).
const RECORD_ROLES = ['', 'start', 'stop', 'ignore'];

function _batchFragRenderRoles(labels) {
  const cont = document.getElementById('batch-frag-roles');
  if (!cont) return;
  const roles = _recCtx.record_roles || {};
  const others = (labels || []).filter(l => l !== _recCtx.record_label);
  if (!others.length) { cont.innerHTML = '<div style="color:#667;font-size:10px;">No other labels.</div>'; return; }
  const title = {'': 'no role', start: 'start — a record begins here (a name line, an entry heading)',
                 stop: 'stop — ends the open record, starts none (a section heading, an advertisement block)',
                 ignore: 'ignore — skipped, and masked out of the ink before the trims'};
  cont.innerHTML = others.map(l =>
    `<label class="row" style="margin:0;gap:8px;"><span style="flex:1;min-width:0;font-size:11px;">${_escHtml(l)}</span>` +
    `<select data-frag-label="${_escHtml(l)}" onchange="_batchFragRoleChange()" style="width:110px;">` +
    RECORD_ROLES.map(r => `<option value="${r}" title="${_escHtml(title[r])}"${(roles[l] || '') === r ? ' selected' : ''}>${r || '— none —'}</option>`).join('') +
    `</select></label>`).join('');
}

async function _batchFragRoleChange() {
  if (!_recCtx.project) return;
  const roles = {};
  document.querySelectorAll('#batch-frag-roles select[data-frag-label]').forEach(sel => {
    if (sel.value) roles[sel.dataset.fragLabel] = sel.value;
  });
  try {
    const r = await fetch(`${API}/api/project/${encodeURIComponent(_recCtx.project)}/config`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({record_roles: roles})});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { showToast('Config error: ' + (d.detail || r.status), 5000); return; }
    _recCtx.record_roles = d.config?.record_roles || {};
    showToast('Record roles: ' + (Object.keys(roles).length
      ? Object.entries(roles).map(([l, r]) => `${l}=${r}`).join(', ') : 'none'));
  } catch (e) { showToast('Config error: ' + (e?.message || e), 5000); }
}

function _fragSummary(d) {
  return `${d.fragments} fragment(s) (${d.continuations} ⛓) on ${d.pages_selected} selected page(s): ` +
    `${d.written} ${d.dry_run ? 'would be written' : 'written'}, ${d.replaced} stale derived replaced, ${d.unchanged} unchanged` +
    (d.protected ? `, ${d.protected} hand / edited box(es) kept` : '') +
    (d.suppressed ? `, ${d.suppressed} suppressed (overlap a kept box)` : '') +
    (d.pages_without_image ? `, ${d.pages_without_image} page(s) without image skipped` : '') +
    ` · ${d.pages_written} page(s) ${d.dry_run ? 'would change' : 'changed'}`;
}

function _fragPagesHtml(d) {
  const pg = _recPageNo();
  const rows = (d.pages || []).filter(p => p.skipped || p.changed || p.protected || p.suppressed).slice(0, 60).map(p =>
    p.skipped
      ? `<div style="font-size:10px;color:#fbbf24;">${_escHtml(pg(p.stem))} ${_escHtml(p.stem)}: skipped (${_escHtml(p.skipped)})</div>`
      : `<div style="font-size:10px;color:#aaa;">${_escHtml(pg(p.stem))} ${_escHtml(p.stem)}: ${p.starts} start / ${p.stops} stop / ${p.ignores} ignore → ` +
        `${p.fragments} fragment(s)${p.continuations ? ` (${p.continuations} ⛓)` : ''}, ${p.written} new, ${p.replaced} replaced, ${p.unchanged} unchanged` +
        (p.protected ? `, <span style="color:#fbbf24;">${p.protected} kept</span>` : '') +
        (p.suppressed ? `, <span style="color:#f87171;">${p.suppressed} suppressed</span>` : '') +
        (p.open_at_end ? ' · open →' : ' · closed') + '</div>').join('');
  const empty = d.empty_pages || [];
  const emptyHtml = empty.length
    ? `<div style="margin-top:4px;font-size:10px;color:#93c5fd;" title="Pages with no start and no stop: continuation pages of a record running on (one ⛓ fragment per column while a record is open), or pages the detector missed">` +
      `${empty.length} page(s) with zero starts and zero stops (candidate continuation pages): ` +
      _escHtml(empty.slice(0, 40).map(pg).join(', ')) + (empty.length > 40 ? ' …' : '') + '</div>'
    : '';
  return rows + emptyHtml;
}

// ── record-level entity resolution (13 §2, E3) ─────────────────────────────

let _recUnresolvedOnly = false;
// Where the browser positions itself after a reload: {id} = the first record
// listed with id >= id, {stem} = the first record starting on/after that page.
// Set by every action that changes the list, consumed by _recOpenModal.
let _recFocus = null;
function _recScrollToFocus() {
  const f = _recFocus; _recFocus = null;
  if (!f || !_recData) return;
  const pg = _recPageNo();
  const pno = s => parseInt(String(pg(s)).replace(/^p/, ''), 10) || 0;
  const want = f.stem != null ? pno(f.stem) : null;
  let target = null, last = null;
  for (const r of _recData.records) {
    const el = document.getElementById(`rec-head-${r.id}`);
    if (!el) continue;
    last = el;
    if ((f.id != null && r.id >= f.id) || (want != null && pno(r.page_span[0]) >= want)) { target = el; break; }
  }
  const el = target || last;
  if (!el) return;
  el.scrollIntoView({block: 'start'});
  el.style.outline = '1px solid #e94560';
  setTimeout(() => { el.style.outline = ''; }, 1800);
}

// ── the model judge: suggestions on open records (13 §2.3) ─────────────────

function _recEntityLink(id, name, tone) {
  // the entity in the cross-year browser, filtered to this one id
  const d = _recData || {};
  if (!id) return _escHtml(name || '');
  const href = `/static/entities.html?authority=${encodeURIComponent(d.record_authority || '')}&q=${encodeURIComponent(id)}`;
  return `<a href="${href}" target="_blank" style="color:${tone || '#93c5fd'};text-decoration:underline dotted;" onclick="event.stopPropagation()" ` +
    `title="Open ${_escHtml(id)} in the entity browser: every year it is linked to">${_escHtml(id)}${name ? ' ' + _escHtml(name) : ''}</a>`;
}

function _recSugChip(r) {
  const s = r.suggestion;
  if (!s || (r.authority && r.authority.id)) return '';
  const tone = s.verdict === 'unsure' ? '#8a94a6' : '#c4b5fd';
  const what = s.verdict === 'unsure' ? 'unsure' : `${s.verdict} ` + (s.entity_id ? _recEntityLink(s.entity_id, s.entity_name, tone) : '');
  return `<span style="color:${tone};white-space:nowrap;" title="${_escHtml(`${s.stage} model ${s.model || ''} · ${s.reason || ''}`)}">🤖 ${what} ${s.confidence != null ? '(' + Number(s.confidence).toFixed(2) + ')' : ''}</span>` +
    (s.verdict !== 'unsure' ? `<button class="nav-btn" onclick="_recAcceptOne(${r.id})" title="Accept this suggestion as your decision">✓</button>` : '') +
    (s.verdict !== 'new' ? `<button class="nav-btn" onclick="_recDecide(${r.id},'new')" title="No: this record is a NEW entity (mint it now)">＋ new</button>` : '') +
    `<button class="nav-btn" onclick="_recAcceptOne(${r.id}, true)" title="Dismiss this suggestion and decide from the candidates">✕</button>`;
}

async function _recAcceptOne(gid, dismiss = false) {
  try {
    const r = await fetch(`${API}/api/project/${encodeURIComponent(_recCtx.project)}/records/accept-suggestions`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({record_ids: [gid], dismiss, min_confidence: 0})});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    const it = (d.items || [])[0];
    if (it && it.error) throw new Error(it.error);
    showToast(dismiss ? `record ${gid}: suggestion dismissed` : `record ${gid} → ${it?.verdict || ''} ${it?.entity_id || ''}`, 5000);
    _recFocus = {id: gid + 1};
    await openRecordBrowser();
    if (pageData) { await reloadPageData(); drawOverlay(); updatePanel(); }
  } catch (e) { showToast('Suggestion error: ' + (e?.message || e), 7000); }
}

async function runJudgeRecords(sorted, {dryRun = false} = {}) {
  if (!_recNeedProject()) return null;
  if (!_recCtx.record_authority) { showToast('Pick the authority the records resolve against first'); return null; }
  const base = {
    pattern: typeof _cropsPatternFor === 'function' ? _cropsPatternFor(sorted) : null,
    stage1: document.getElementById('batch-judge-s1')?.checked !== false,
    stage2: document.getElementById('batch-judge-s2')?.checked !== false,
    threshold: parseFloat(document.getElementById('batch-judge-thr')?.value) || 0.85,
    vision_model: document.getElementById('batch-judge-model')?.value || 'gpt-4.1-mini',
    overwrite: !!document.getElementById('batch-judge-overwrite')?.checked,
  };
  if (!base.stage1 && !base.stage2) { showToast('Tick at least one stage'); return null; }
  const prog = document.getElementById('batch-progress');
  const url = `${API}/api/project/${encodeURIComponent(_recCtx.project)}/judge-records`;
  const post = async (b) => {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d;
  };
  try {
    if (prog) prog.textContent = 'Listing open records…';
    const plan = await post({...base, dry_run: true});
    const n = plan.items.length;
    const head = `${n} open record(s) to judge (${plan.counts.kept} already suggested, ${plan.counts.skipped} resolved)` +
      ` · stage 1 ${base.stage1 ? 'on (≥ ' + base.threshold + ')' : 'off'} · stage 2 ${base.stage2 ? base.vision_model : 'off'}`;
    if (dryRun) {
      if (prog) prog.innerHTML = _escHtml(head) + '<div style="max-height:200px;overflow:auto;margin-top:4px;color:#8a94a6;">' +
        plan.items.map(i => `record ${i.id} · ${_escHtml(i.name || '')} · candidates: ${i.candidates.map(c => `${_escHtml(c.id)} ${c.score}`).join(', ') || 'none'}`).join('<br>') + '</div>';
      showToast(head, 8000); return plan;
    }
    if (!n) { showToast('Nothing to judge: ' + head, 6000); if (prog) prog.textContent = head; return plan; }
    if (!(await _batchConfirmInline(head + '\n\nSuggestions only — nothing is written to the authority file until you accept them.', '🤖 Judge'))) return null;
    const ids = plan.items.map(i => i.id);
    const CH = 3; let sug = 0, uns = 0, err = 0, tin = 0, tout = 0; const errs = [];
    for (let k = 0; k < ids.length; k += CH) {
      if (prog) prog.textContent = `Judging… ${k}/${ids.length} · ${sug} suggested, ${uns} unsure${err ? ', ' + err + ' error(s)' : ''}`;
      const d = await post({...base, record_ids: ids.slice(k, k + CH), dry_run: false});
      sug += d.counts.suggested; uns += d.counts.unsure; err += d.counts.error; tin += d.tokens_in; tout += d.tokens_out;
      (d.items || []).filter(i => i.error).forEach(i => errs.push(`record ${i.id}: ${i.error}`));
    }
    const msg = `✓ judged ${ids.length}: ${sug} suggested, ${uns} unsure${err ? ', ' + err + ' error(s)' : ''}` + (tin ? ` · vision tokens ${tin}→${tout}` : '');
    if (prog) prog.innerHTML = _escHtml(msg) + (errs.length ? '<div style="color:#f87171;margin-top:4px;">' + errs.slice(0, 10).map(_escHtml).join('<br>') + '</div>' : '');
    showToast(msg, 9000);
    if (document.getElementById('rec-browser-modal')) await openRecordBrowser();
    return {sug, uns, err};
  } catch (e) {
    if (prog) prog.textContent = 'Error: ' + (e?.message || e);
    showToast('Judge error: ' + (e?.message || e), 7000);
    return null;
  }
}

async function runAcceptSuggestions({dismiss = false, dryRun = false} = {}) {
  if (!_recNeedProject()) return null;
  const body = {dismiss, min_confidence: parseFloat(document.getElementById('batch-judge-accept-thr')?.value) || 0, dry_run: true};
  const prog = document.getElementById('batch-progress');
  const url = `${API}/api/project/${encodeURIComponent(_recCtx.project)}/records/accept-suggestions`;
  const post = async (b) => {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d;
  };
  const fmt = (d) => dismiss ? `${d.counts.dismissed} suggestion(s) dismissed` :
    `${d.counts.accepted} suggestion(s) accepted ≥ ${body.min_confidence}, ${d.counts.below} below / unsure, ${d.counts.skipped} already resolved` + (d.counts.error ? `, ${d.counts.error} error(s)` : '');
  try {
    const plan = await post(body);
    if (dryRun) { if (prog) prog.textContent = 'Dry run: ' + fmt(plan); showToast(fmt(plan), 7000); return plan; }
    const todo = dismiss ? plan.counts.dismissed : plan.counts.accepted;
    if (!todo) { showToast('Nothing to do: ' + fmt(plan), 6000); return plan; }
    if (!(await _batchConfirmInline((dismiss ? 'Dismiss ' : 'Accept ') + todo + ' suggestion(s)? ' + (dismiss ? '' : 'They become your decisions: slices, aliases, new entities and edges are written to the authority file.'), dismiss ? '✕ Dismiss' : '✓ Accept'))) return null;
    const d = await post({...body, dry_run: false});
    if (prog) prog.textContent = '✓ ' + fmt(d);
    showToast('✓ ' + fmt(d), 9000);
    if (document.getElementById('rec-browser-modal')) await openRecordBrowser();
    if (pageData) { await reloadPageData(); drawOverlay(); updatePanel(); }
    return d;
  } catch (e) { showToast('Accept error: ' + (e?.message || e), 7000); return null; }
}


function _recAuthChip(r) {
  const d = _recData || {};
  if (!d.record_authority) return '';
  const a = r.authority;
  if (!a) {
    return _recSugChip(r) + `<button class="nav-btn" onclick="_recCandidates(${r.id})" title="Find entity candidates for this record">🏛 resolve…</button>`;
  }
  const tone = a.outcome === 'new' ? '#fbbf24' : (a.outcome === 'successor' ? '#f0abfc' : '#86efac');
  const what = a.outcome === 'new' ? 'new entity' : (a.outcome === 'successor' ? `successor (${a.edge?.type || ''} of ${a.edge?.from || ''})` : 'same entity');
  return `<span class="rs-clk" onclick="_recCandidates(${r.id})" style="color:${tone};cursor:pointer;white-space:nowrap;" ` +
    `title="${_escHtml(`${what} · ${a.source}${a.score != null ? ' · score ' + a.score : ''} · ${a.ts || ''} · click to review`)}">` +
    `🏛 ${_recEntityLink(a.id, a.name, tone)}${a.source === 'human' ? ' ✎' : ''}</span>`;
}

async function _recCandidates(gid, q) {
  // q == null: toggle the panel (the record's own candidates);
  // q = '…': search the authority by keyword, in the same panel;
  // q = '':  back to the record's own candidates.
  const box = document.getElementById(`rec-cand-${gid}`);
  if (!box) return;
  if (box.innerHTML && q == null) { box.innerHTML = ''; return; }
  box.innerHTML = '<div style="padding:6px;color:#8a94a6;">Looking up candidates…</div>';
  let c;
  try {
    const r = await fetch(`${API}/api/project/${encodeURIComponent(_recCtx.project)}/records/candidates?gid=${gid}&k=8` +
                          (q ? `&q=${encodeURIComponent(q)}` : ''));
    c = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(c.detail || `HTTP ${r.status}`);
  } catch (e) { box.innerHTML = `<div style="padding:6px;color:#f87171;">${_escHtml(e?.message || String(e))}</div>`; return; }
  const id = c.identity || {};
  const identTxt = Object.keys(id).length
    ? ['name_hu', 'name_de', 'legal_form', 'seat', 'county', 'founded', 'capital_nominal'].filter(k => id[k] != null && id[k] !== '')
        .map(k => `<span style="color:#667;">${k}</span> ${_escHtml(String(id[k]))}`).join(' · ')
    : '<span style="color:#fbbf24;">no identity object — matching on the first line of text (run 🧩 extract first for better candidates)</span>';
  const cur = c.current;
  const curCanon = cur ? (cur.canonical_id || cur.id) : null;
  const sq = c.search || '';
  let html = `<div style="margin:2px 0 8px 14px;padding:6px 8px;border-left:3px solid #2a4a8e;background:#0d1b35;border-radius:0 4px 4px 0;">` +
    `<div style="margin-bottom:4px;">${identTxt}</div>` +
    `<div style="margin-bottom:4px;color:#8a94a6;">${sq ? `search: <b style="color:#cde;">${_escHtml(sq)}</b>` : `queries: ${(c.queries || []).map(q => _escHtml(q[0])).join(' | ') || '—'}`} · authority ${_escHtml(c.authority_file)} · year ${c.source_year ?? '<span style="color:#f87171;">not set</span>'}` +
    (c.auto ? ` · batch would say <b style="color:${c.auto.verdict === 'same' ? '#86efac' : '#fbbf24'};">${_escHtml(c.auto.verdict)}</b> <span style="color:#667;">(${_escHtml(c.auto.reason)})</span>` : '') + `</div>` +
    `<div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;">` +
      `<input id="rec-candq-${gid}" value="${_escHtml(sq)}" placeholder="search the authority: a name, an alias, a seat or an entity id…" ` +
        `style="flex:1;max-width:380px;font-size:11px;background:#0a1128;color:#ddd;border:1px solid #2a4a8e;border-radius:3px;padding:3px 6px;" ` +
        `onkeydown="if (event.key === 'Enter') _recCandidates(${gid}, this.value.trim())">` +
      `<button class="nav-btn" onclick="_recCandidates(${gid}, document.getElementById('rec-candq-${gid}').value.trim())" title="List the entities matching the keyword, with the same buttons">🔍 search</button>` +
      (sq ? `<button class="nav-btn" onclick="_recCandidates(${gid}, '')" title="Back to the record's own candidates">✕ back</button>` : '') +
    `</div>`;
  if (!(c.candidates || []).length) html += `<div style="color:#8a94a6;margin:4px 0;">${sq ? 'nothing in the authority matches the keyword' : 'no candidates in the authority'}</div>`;
  html += '<table class="rs-table" style="width:100%;"><tr><th>score</th><th>entity</th><th>facts</th><th>last seen</th><th></th><th></th></tr>';
  (c.candidates || []).forEach((k, i) => {
    const facts = Object.entries(k.facts || {}).map(([a, b]) => `${a} ${b}`).join(', ');
    const ls = k.last_slice || {};
    const crop = k.crop && k.crop.stem && k.crop.idx != null
      ? `<img src="${API}/api/cell?folder=${encodeURIComponent(k.crop.folder)}&stem=${encodeURIComponent(k.crop.stem)}&idx=${k.crop.idx}" loading="lazy" onclick="_recShowCrop(this.src)" style="max-height:90px;max-width:300px;object-fit:contain;cursor:zoom-in;display:block;background:#fff;">`
      : (k.crop && k.crop.stem ? `<span class="rs-clk" style="color:#93c5fd;cursor:pointer;" onclick="_recOpenOther('${_escHtml(k.crop.folder)}','${_escHtml(k.crop.stem)}')">${_escHtml(ls.source || '')} · ${_escHtml(k.crop.stem)}</span>` : '');
    const isCur = cur && curCanon === k.id;
    const mergeBtn = cur && !isCur
      ? ` <button class="nav-btn" onclick="_recMerge(${gid},'${_escHtml(curCanon)}','${_escHtml(k.id)}')" title="This record's entity ${_escHtml(curCanon)} and ${_escHtml(k.id)} are ONE firm: the lower id survives, the other is marked duplicate_of it (its slices and names fold into the survivor; undo in the entity browser)">⊕ merge</button>`
      : '';
    html += `<tr style="${isCur ? 'background:#12224a;' : ''}">` +
      `<td style="white-space:nowrap;" title="raw similarity ${k.score}${k.boost ? ' + boost ' + k.boost + ' (' + (k.boost_why || []).join(', ') + ')' : ''} · via ${_escHtml(k.via || '')} “${_escHtml(k.matched || '')}”">` +
        `${k.score}${k.boost ? `<span style="color:#86efac;">+${k.boost}</span>` : ''}${i === 0 && k.score >= (c.accept_floor || 70) ? ' ★' : ''}</td>` +
      `<td><b>${_recEntityLink(k.id, k.name)}</b>${(k.merged_from || []).length ? ` <span style="color:#fca5a5;font-size:10px;" title="entities merged into this one">= ${_escHtml(k.merged_from.join(', '))}</span>` : ''}` +
        ((k.aliases || []).length ? `<div style="color:#667;font-size:10px;">${_escHtml(k.aliases.join(' · '))}</div>` : '') + `</td>` +
      `<td style="color:#8a94a6;">${_escHtml(facts)}${k.seat_agree === false ? ' <span style="color:#f87171;" title="the record\'s seat differs">seat≠</span>' : ''}${k.year_agree === false ? ' <span style="color:#f87171;" title="the record\'s founding year differs">year≠</span>' : ''}${k.founded_after_seen ? ` <span style="color:#fbbf24;" title="the record says it was founded after this candidate was already listed in ${k.first_seen}: a re-foundation or a change of legal form, or a different firm">founded after ${k.first_seen}</span>` : ''}</td>` +
      `<td style="white-space:nowrap;color:#8a94a6;">${ls.as_of ?? ''} ${_escHtml(ls.source || '')}${k.n_slices > 1 ? ` (${k.n_slices} slices)` : ''}<div>${crop}</div></td>` +
      `<td style="white-space:nowrap;"><button class="nav-btn" onclick="_recDecide(${gid},'same','${_escHtml(k.id)}')" title="This record IS this entity: add a ${c.source_year ?? '?'} slice and the printed name as alias">= same</button>${mergeBtn}</td>` +
      `<td style="white-space:nowrap;"><select id="rec-edge-${gid}-${i}" style="font-size:10px;background:#0d1b35;color:#ccc;border:1px solid #333;"><option value="rename">rename</option><option value="merger">merger</option><option value="split">split</option><option value="legal_form">legal form</option><option value="successor">successor</option></select> ` +
        `<button class="nav-btn" onclick="_recDecide(${gid},'successor','${_escHtml(k.id)}',document.getElementById('rec-edge-${gid}-${i}').value)" title="This record is a NEW entity that succeeds this one (typed edge from it)">→ successor</button></td>` +
      `</tr>`;
  });
  html += '</table>' +
    `<div style="display:flex;gap:8px;margin-top:6px;align-items:center;">` +
      `<button class="nav-btn" onclick="_recDecide(${gid},'new')" title="None of these: mint a new entity from this record">＋ new entity</button>` +
      (cur ? `<button class="nav-btn" onclick="_recDecide(${gid},'clear')" title="Drop the decision on this record (the authority file keeps what was written)">✕ clear decision</button>` : '') +
      `<span style="color:#556;font-size:10px;">same = slice on the entity · new = mint · successor = mint + typed edge from the chosen entity${cur ? ' · merge = this record\'s entity and the candidate are one firm (duplicate_of edge)' : ''}. Decisions are stored on the record's head fragment as human.</span>` +
    `</div></div>`;
  box.innerHTML = html;
  if (q) { const inp = document.getElementById(`rec-candq-${gid}`); if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); } }
}

async function _recMerge(gid, a, b) {
  // two entities are one firm: duplicate_of edge, lower id survives (13 §5.3)
  try {
    const r = await fetch(`${API}/api/authority/merge`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: _recCtx.record_authority, ids: [a, b], source: 'human'})});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    if (!d.added) { showToast(`${a} and ${b} are already one entity (${d.survivor})`, 5000); return; }
    const warn = (d.conflicts || []).length
      ? ` · ⚠ both were listed in ${d.conflicts.map(x => x.as_of ?? x.source).join(', ')}: check that they are one firm (the entity browser flags the row; unmerge there)`
      : '';
    showToast(`merged ${d.loser} ${d.loser_name || ''} into ${d.survivor} ${d.survivor_name || ''}${warn}`, warn ? 12000 : 6000);
    _recFocus = {id: gid};
    await openRecordBrowser();
  } catch (e) { showToast('Merge error: ' + (e?.message || e), 7000); }
}

async function _recOpenOther(folderPath, stem) {
  const u = new URL(location.href); u.searchParams.set('folder', folderPath); u.searchParams.set('stem', stem);
  window.open(u.toString(), '_blank');
}

async function _recDecide(gid, outcome, entityId, edgeType) {
  try {
    const r = await fetch(`${API}/api/project/${encodeURIComponent(_recCtx.project)}/records/resolve`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({gid, outcome, entity_id: entityId || null, edge_type: edgeType || null, source: 'human'})});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    showToast(outcome === 'clear' ? `record ${gid}: decision cleared`
      : `record ${gid} → ${outcome} ${d.entity?.id || ''} ${d.entity?.name || ''}`, 5000);
    _recFocus = {id: gid + 1};
    await openRecordBrowser();
    if (pageData) { await reloadPageData(); drawOverlay(); updatePanel(); }
  } catch (e) { showToast('Resolve error: ' + (e?.message || e), 7000); }
}

// The resolve batch runs as a server job: poll its status every 2 s into
// the batch panel's progress line (pass, done / total, counts, elapsed, a
// ■ Stop button) and return the finished run's result. Resumes on an
// already running job (the panel was reopened).
async function _recWatchResolveJob(prog, fmt) {
  const url = `${API}/api/project/${encodeURIComponent(_recCtx.project)}/resolve-records/status`;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  for (;;) {
    let j;
    try { const r = await fetch(url); j = (await r.json()).job; } catch (e) { j = null; }
    if (!j) { if (prog) prog.textContent = 'No resolve job found'; return null; }
    const c = j.counts || {};
    const el = Math.round(((j.finished || Date.now() / 1000) - j.started));
    const line = `${j.state === 'running' ? '⏳' : '✓'} pass ${j.phase} · ${j.done}${j.total != null ? ' / ' + j.total : ''} records · ` +
      `${c.same || 0} same · ${c.new || 0} new · ${c.unresolved || 0} unresolved · ${c.kept || 0} kept` +
      (c.error ? ` · ${c.error} error(s)` : '') + ` · ${Math.floor(el / 60)}m${String(el % 60).padStart(2, '0')}s` +
      (j.current ? ` · <span style="color:#667;">${_escHtml(j.current)}</span>` : '');
    if (j.state === 'running') {
      if (prog) prog.innerHTML = line + ` <button class="nav-btn" onclick="_recStopResolveJob()" title="Stop after the current record; what is decided stays written">■ Stop</button>`;
      await sleep(2000);
      continue;
    }
    if (j.state === 'failed') { if (prog) prog.innerHTML = `<span style="color:#f87171;">✗ ${_escHtml(j.error || 'failed')}</span>`; showToast('Resolve job failed: ' + (j.error || ''), 9000); return null; }
    const r2 = await fetch(url + '?items=true'); const d = (await r2.json()).job?.result;
    if (j.state === 'stopped') showToast('Resolve job stopped: ' + (d ? fmt(d) : ''), 8000);
    return d || null;
  }
}

async function _recStopResolveJob() {
  try { await fetch(`${API}/api/project/${encodeURIComponent(_recCtx.project)}/resolve-records/stop`, {method: 'POST'}); } catch (e) {}
}

function _batchSortedPages() {
  // the batch modal's page range when a helper exposes it; else every page
  try { if (typeof _batchSelectedIndices === 'function') return [..._batchSelectedIndices()].sort((a, b) => a - b); } catch (e) { /* fall through */ }
  return pages.map((_, i) => i);
}

async function _batchRecrInit() {
  const sel = document.getElementById('batch-recr-auth');
  const yr = document.getElementById('batch-recr-year');
  const vm = document.getElementById('batch-judge-model');
  const src = document.getElementById('batch-llm-model');
  if (vm && src && !vm.options.length) {
    vm.innerHTML = src.innerHTML;
    // prefer an Azure vision model (the server may hold Azure keys only)
    const pref = [...vm.options].find(o => o.value.startsWith('azure:')) || [...vm.options].find(o => o.value === 'gpt-4.1-mini') || vm.options[0];
    if (pref) vm.value = pref.value;
  }
  if (!sel) return;
  try {
    const r = await fetch(`${API}/api/authorities`); const d = await r.json();
    const cur = _recCtx.record_authority || '';
    sel.innerHTML = '<option value="">— none —</option>' + (d.authorities || []).filter(a => !a.error)
      .map(a => `<option value="${_escHtml(a.file)}"${a.file === cur ? ' selected' : ''}>${_escHtml(a.authority || a.file)}${a.id_scheme ? '' : ' (no id scheme: cannot mint)'}</option>`).join('');
  } catch (e) { sel.innerHTML = '<option value="">(authorities unavailable)</option>'; }
  if (yr) yr.value = _recCtx.source_year ?? '';
  // a resolve job started earlier (another tab, a reload) still runs on the server: show it
  try {
    const r = await fetch(`${API}/api/project/${encodeURIComponent(_recCtx.project)}/resolve-records/status`);
    const j = (await r.json()).job;
    if (j && j.state === 'running') {
      const prog = document.getElementById('batch-progress');
      const fmt = (d) => `${d.counts.same || 0} same · ${d.counts.new || 0} new · ${d.counts.unresolved || 0} unresolved · ${d.counts.kept || 0} kept`;
      _recWatchResolveJob(prog, fmt).then(d => { if (d) showToast('✓ resolve job finished: ' + fmt(d), 9000); });
    }
  } catch (e) { /* no project context */ }
}

async function runResolveRecords(sorted, {dryRun = false} = {}) {
  if (!_recNeedProject()) return null;
  if (!_recCtx.record_authority) { showToast('Pick the authority the records resolve against first'); return null; }
  const body = {
    pattern: typeof _cropsPatternFor === 'function' ? _cropsPatternFor(sorted) : null,
    accept_floor: parseFloat(document.getElementById('batch-recr-floor')?.value) || 85,
    margin: parseFloat(document.getElementById('batch-recr-margin')?.value) || 0,
    mint_unmatched: !!document.getElementById('batch-recr-mint')?.checked,
    overwrite: !!document.getElementById('batch-recr-overwrite')?.checked,
    dry_run: true,
  };
  const prog = document.getElementById('batch-progress');
  const url = `${API}/api/project/${encodeURIComponent(_recCtx.project)}/resolve-records`;
  const post = async (b) => {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d;
  };
  const fmt = (d) => `${d.counts.same || 0} same · ${d.counts.new || 0} new · ${d.counts.unresolved || 0} unresolved · ${d.counts.kept || 0} kept` +
    (d.counts.error ? ` · ${d.counts.error} error(s)` : '') + ` (of ${d.n_records} records, ${_escHtml(d.authority_file)}, year ${d.source_year ?? 'not set'})`;
  try {
    if (prog) prog.textContent = 'Dry run…';
    const plan = await post(body);
    const head = `${dryRun ? 'Dry run' : 'Preview'}: ${fmt(plan)}`;
    if (prog) prog.innerHTML = _escHtml(head) + '<div style="max-height:200px;overflow:auto;margin-top:4px;color:#8a94a6;">' +
      plan.items.filter(i => i.decision !== 'kept').map(i => `record ${i.id} · ${_escHtml(i.name || '')} → <b>${i.decision}</b>` +
        (i.best ? ` (best ${_escHtml(i.best.id)} ${_escHtml(i.best.name || '')} ${i.best.score}${(i.best.boost_why || []).length ? ' +' + i.best.boost_why.join(',') : ''})` : '') +
        (i.reason ? ` <span style="color:#667;">${_escHtml(i.reason)}</span>` : '')).join('<br>') + '</div>';
    if (dryRun) { showToast(head, 8000); return plan; }
    const todo = (plan.counts.same || 0) + (plan.counts.new || 0);
    if (!todo) { showToast('Nothing to write: ' + fmt(plan), 6000); return plan; }
    if (plan.source_year == null && !(await _batchConfirmInline('Source year is not set: slices will carry no year. Continue anyway?', '▶ Continue'))) return null;
    if (!(await _batchConfirmInline(`Write ${todo} decision(s)? ${fmt(plan)}\n\nThe authority file gains slices${plan.counts.new ? ' and ' + plan.counts.new + ' new entit' + (plan.counts.new === 1 ? 'y' : 'ies') : ''}; each record's head fragment gets the mark (source auto). Human decisions are untouched.`, '▶ Resolve'))) return null;
    if (prog) prog.textContent = 'Resolving…';
    await post({...body, dry_run: false, background: true});
    const d = await _recWatchResolveJob(prog, fmt);
    if (!d) return null;
    const done = `✓ ${fmt(d)}`;
    if (prog) prog.innerHTML = _escHtml(done) + (d.items.some(i => i.error) ? '<div style="color:#f87171;margin-top:4px;">' +
      d.items.filter(i => i.error).map(i => `record ${i.id}: ${_escHtml(i.error)}`).join('<br>') + '</div>' : '');
    showToast(done, 9000);
    if (pageData) { await reloadPageData(); drawOverlay(); updatePanel(); }
    if (document.getElementById('rec-browser-modal')) await openRecordBrowser();
    return d;
  } catch (e) {
    if (prog) prog.textContent = 'Error: ' + (e?.message || e);
    showToast('Resolve records error: ' + (e?.message || e), 7000);
    return null;
  }
}

// ── record-level structured extraction (13 §1b) ─────────────────────────────

let _recStructCache = {};

function _recStructChip(r) {
  const st = r.structured;
  if (!st) return '';
  _recStructCache[r.id] = st;
  const vals = st.data && typeof st.data === 'object'
    ? Object.entries(st.data).filter(([, v]) => v !== null && v !== '' && typeof v !== 'object').slice(0, 3)
        .map(([k, v]) => `${k}: ${String(v)}`).join(' · ')
    : '';
  return `<span class="rs-clk" onclick="_recShowStructured(${r.id})" style="color:#86efac;cursor:pointer;white-space:nowrap;" ` +
    `title="${_escHtml((st.record ? 'whole record · ' : 'head fragment only · ') + (st.schema_name || '') + ' · ' + (st.model || '') + ' · ' + (st.ts || '') + (st.edited ? ' · edited by hand' : ''))}">` +
    `🧩 ${_escHtml(st.schema_name || 'structured')}${st.edited ? ' ✎' : ''}</span>` +
    (vals ? `<span style="color:#8a94a6;max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_escHtml(vals)}</span>` : '');
}

function _recShowStructured(gid) {
  const st = _recStructCache[gid];
  if (!st) return;
  document.getElementById('rec-struct-pop')?.remove();
  const d = document.createElement('div');
  d.id = 'rec-struct-pop';
  d.style.cssText = 'position:fixed;inset:0;z-index:1900;background:rgba(0,0,0,0.75);display:flex;align-items:center;justify-content:center;';
  d.onclick = (e) => { if (e.target === d) d.remove(); };
  const pre = document.createElement('pre');
  pre.style.cssText = 'max-width:80vw;max-height:80vh;overflow:auto;background:#0a1128;border:1px solid #2a4a8e;border-radius:8px;padding:14px 18px;color:#cde;font-size:12px;margin:0;';
  pre.textContent = `record ${gid} · ${st.schema_name || ''} · ${st.model || ''} · ${st.ts || ''}${st.edited ? ' · edited by hand' : ''}\n` +
    (st.record ? '' : '(head fragment only — not a record-level extraction)\n') + '\n' + JSON.stringify(st.data, null, 2);
  d.appendChild(pre);
  document.body.appendChild(d);
}

function _batchRecxFillSchemas() {
  const sel = document.getElementById('batch-recx-schema');
  if (!sel) return;
  const cur = sel.value;
  const list = (typeof _projSchemas !== 'undefined' ? _projSchemas : []) || [];
  sel.innerHTML = '<option value="">— pick a schema —</option>' +
    list.map(x => `<option value="${_escHtml(x.name)}"${x.name === cur ? ' selected' : ''}>${_escHtml(x.name)}</option>`).join('') +
    '<option value="__copy__">⇩ copy from another project…</option>';
  sel.onchange = () => {
    if (sel.value !== '__copy__') return;
    sel.value = '';
    if (typeof schemaCopyChooser === 'function')
      schemaCopyChooser(sel, async name => { await reloadSchemas(); _batchRecxFillSchemas(); sel.value = name; });
  };
}

async function _batchRecxInit() {
  const m = document.getElementById('batch-recx-model');
  const src = document.getElementById('batch-llm-model');
  if (m && src && !m.options.length) {
    m.innerHTML = src.innerHTML;
    m.value = src.value || m.value;
  }
  try { if (typeof reloadSchemas === 'function') await reloadSchemas(); } catch (e) { /* no project schemas */ }
  _batchRecxFillSchemas();
  if (typeof _refreshOvernightJobs === 'function') _refreshOvernightJobs();
}

async function _recxSubmitBatch(base, prog) {
  const url = `${API}/api/project/${encodeURIComponent(_recCtx.project)}/extract-records/submit`;
  if (prog) prog.textContent = 'Building the batch file… (images are packaged; this can take a few minutes)';
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(base)});
  if (!r.ok) { const m = await r.json().catch(() => ({})); throw new Error(m.detail || `HTTP ${r.status}`); }
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = '', jobs = [], done = null, err = null;
  outer: while (true) {
    const {done: rd, value} = await reader.read();
    if (rd) break;
    buf += dec.decode(value, {stream: true});
    const chunks = buf.split('\n\n'); buf = chunks.pop();
    for (const c of chunks) {
      if (!c.startsWith('data: ')) continue;
      let ev; try { ev = JSON.parse(c.slice(6)); } catch { continue; }
      if (ev.type === 'building' && prog) prog.textContent = `Building… ${ev.requests} record(s), ~${ev.mb} MB so far`;
      else if (ev.type === 'uploading' && prog) prog.textContent = `Uploading ${ev.requests} record(s) (${ev.mb} MB) to the provider…`;
      else if (ev.type === 'chunk_done') { jobs.push(ev); showToast(`🌙 ${ev.id}: ${ev.cells} record(s), ${ev.mb} MB submitted`, 4000); }
      else if (ev.type === 'done') done = ev;
      else if (ev.type === 'error') { err = ev.error; break outer; }
    }
  }
  if (err) throw new Error(err);
  const msg = `🌙 ${jobs.length} job(s) submitted: ${done ? done.cells : jobs.reduce((a, j) => a + j.cells, 0)} record(s). ` +
    'They are listed below; ⟳ refreshes their status and ⬇ Apply writes the answers when a job is completed.';
  if (prog) prog.textContent = msg;
  showToast(msg, 10000);
  if (typeof _refreshOvernightJobs === 'function') _refreshOvernightJobs();
  return {jobs, done};
}

async function runExtractRecords(sorted, {dryRun = false} = {}) {
  if (!_recNeedProject()) return null;
  const schema = document.getElementById('batch-recx-schema')?.value || '';
  if (!schema) { showToast('Pick a schema first (project schemas/ folder; LLM panel → JSON to create one)'); return null; }
  const base = {
    schema_name: schema,
    model: document.getElementById('batch-recx-model')?.value || 'gpt-4o-mini',
    prompt: (document.getElementById('batch-recx-prompt')?.value || '').trim() || null,
    pattern: typeof _cropsPatternFor === 'function' ? _cropsPatternFor(sorted) : null,
    overwrite: !!document.getElementById('batch-recx-overwrite')?.checked,
    with_text: document.getElementById('batch-recx-text')?.checked !== false,
    max_fragments: parseInt(document.getElementById('batch-recx-maxfrag')?.value, 10) || 8,
  };
  const prog = document.getElementById('batch-progress');
  const url = `${API}/api/project/${encodeURIComponent(_recCtx.project)}/extract-records`;
  const post = async (body) => {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d;
  };
  try {
    if (prog) prog.textContent = 'Planning…';
    const plan = await post({...base, dry_run: true});
    const planTxt = `${plan.n_planned} record(s) to extract with "${schema}" (${base.model})` +
      (plan.n_skipped ? `, ${plan.n_skipped} already have it` : '') +
      ` · ${plan.items.reduce((a, i) => a + i.n_images, 0)} image(s), ${plan.items.filter(i => i.has_text).length} with attached text`;
    if (dryRun) {
      if (prog) prog.innerHTML = _escHtml(planTxt) + '<div style="max-height:200px;overflow:auto;margin-top:4px;color:#8a94a6;">' +
        plan.items.map(i => `record ${i.id} · ${_escHtml(i.name || '')} · ${i.n_fragments} fragment(s), ${i.n_images} image(s)${i.has_text ? ', text' : ''}`).join('<br>') + '</div>';
      showToast(planTxt, 8000); return plan;
    }
    if (!plan.n_planned) { showToast('Nothing to do: ' + planTxt, 6000); if (prog) prog.textContent = planTxt; return plan; }
    const overnight = !!document.getElementById('batch-recx-overnight')?.checked;
    if (overnight) {
      if (!(await _batchConfirmInline(planTxt + '\n\nSubmit as a batch job (half price)? The answers are applied later from 🌙 LLM — overnight batch → jobs.', '🌙 Submit job'))) return null;
      return await _recxSubmitBatch(base, prog);
    }
    if (!(await _batchConfirmInline(planTxt + '\n\nOne LLM call per record; the object lands on each record\'s head fragment. Human-edited objects are kept.', '▶ Extract'))) return null;
    const ids = plan.items.map(i => i.id);
    const CH = 3;
    let done = 0, failed = 0, tin = 0, tout = 0;
    const fails = [];
    for (let k = 0; k < ids.length; k += CH) {
      const chunk = ids.slice(k, k + CH);
      if (prog) prog.textContent = `Extracting… ${done + failed}/${ids.length}` + (failed ? ` (${failed} failed)` : '');
      const d = await post({...base, record_ids: chunk, dry_run: false});
      done += d.n_done; failed += d.n_failed; tin += d.tokens_in; tout += d.tokens_out;
      (d.failures || []).forEach(f => fails.push(`record ${f.id}: ${f.error}`));
    }
    const msg = `✓ ${done} record(s) extracted with "${schema}"` + (failed ? `, ${failed} failed` : '') + ` · tokens ${tin}→${tout}`;
    if (prog) prog.innerHTML = _escHtml(msg) + (fails.length ? '<div style="color:#f87171;margin-top:4px;">' + fails.map(_escHtml).join('<br>') + '</div>' : '');
    showToast(msg, 9000);
    if (pageData) { await reloadPageData(); drawOverlay(); updatePanel(); }
    if (document.getElementById('rec-browser-modal')) await openRecordBrowser();
    return {done, failed};
  } catch (e) {
    if (prog) prog.textContent = 'Error: ' + (e?.message || e);
    showToast('Extract records error: ' + (e?.message || e), 7000);
    return null;
  }
}

async function runDeriveFragments(sorted, {dryRun = false} = {}) {
  if (!_recNeedProject(true)) return null;
  const roles = _recCtx.record_roles || {};
  if (!Object.values(roles).includes('start')) { showToast('Give at least one label the start role first'); return null; }
  const statuses = [...document.querySelectorAll('#batch-frag-opts input[data-frag-status]')]
    .filter(c => c.checked).map(c => c.getAttribute('data-frag-status'));
  if (!statuses.length) { showToast('Tick at least one page status'); return null; }
  const body = {
    statuses,
    pattern: typeof _cropsPatternFor === 'function' ? _cropsPatternFor(sorted) : null,
    margin: parseFloat(document.getElementById('batch-frag-margin')?.value) || 0,
    threshold: parseInt(document.getElementById('batch-frag-threshold')?.value, 10) || 160,
    sweep: !!document.getElementById('batch-frag-sweep')?.checked,
    dry_run: true,
  };
  const prog = document.getElementById('batch-progress');
  const url = `${API}/api/project/${encodeURIComponent(_recCtx.project)}/derive-fragments`;
  const roleTxt = Object.entries(roles).map(([l, r]) => `${l}=${r}`).join(', ');
  let d;
  try {
    if (prog) prog.textContent = 'Dry run…';
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    const head = `${dryRun ? 'Dry run' : 'Preview'} (roles: ${roleTxt} → "${d.record_label}", columns ${d.layout_columns}): ${_fragSummary(d)}`;
    if (prog) prog.innerHTML = _escHtml(head) + _fragPagesHtml(d);
    if (dryRun) { showToast(head, 8000); return d; }
    if (!d.pages_written) { showToast('Nothing to do: the derived fragments are up to date', 5000); return d; }
    const msg = `Derive "${d.record_label}" fragments on ${d.pages_selected} page(s)?\n\n${_fragSummary(d)}` +
      '\n\nOnly untouched derived boxes are replaced; hand-drawn and edited boxes stay. ' +
      (body.sweep ? 'The record sweep runs over the whole volume afterwards. ' : '') +
      'A batch-undo snapshot of the selected pages is taken first.';
    if (!(await _batchConfirmInline(msg, '▶ Derive'))) return null;
    const stems = sorted.map(i => pages[i]?.stem).filter(Boolean);
    await _batchTakeSnapshot(body.sweep ? pages.map(p => p.stem) : stems, 'derive_fragments');
    if (prog) prog.textContent = 'Deriving fragments…';
    const r2 = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                 body: JSON.stringify({...body, dry_run: false})});
    d = await r2.json().catch(() => ({}));
    if (!r2.ok) throw new Error(d.detail || `HTTP ${r2.status}`);
    const sw = d.sweep;
    const done = `✓ ${_fragSummary(d)}` + (sw ? ` · sweep: ${sw.records} record(s) over ${sw.boxes} box(es)` +
      (sw.orphans?.length ? `, ⚠ ${sw.orphans.length} orphan continuation(s)` : '') + _recOrderNote(sw.page_order) : '');
    if (prog) prog.innerHTML = _escHtml(done) + _fragPagesHtml(d);
    showToast(done, 9000);
    if (pageData) { await reloadPageData(); drawOverlay(); updatePanel(); }
    return d;
  } catch (e) {
    if (prog) prog.textContent = 'Error: ' + (e?.message || e);
    showToast('Derive fragments error: ' + (e?.message || e), 7000);
    return null;
  }
}
