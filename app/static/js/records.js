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

let _recCtx = {project: null, record_label: null, layout_columns: 'auto', record_roles: {}, labels: []};

async function loadRecordsContext() {
  _recCtx = {project: null, record_label: null, layout_columns: 'auto', record_roles: {}, labels: []};
  if (!folder) return;
  try {
    const r = await fetch(`${API}/api/records/context?folder=${encodeURIComponent(folder)}`);
    if (r.ok) _recCtx = await r.json();
  } catch (e) { /* outside a project: record tools stay off */ }
  updateRecordBtn();
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
function _recNeedProject() {
  if (!_recCtx.project) { showToast('Record tools need a project annotations folder'); return false; }
  if (!_recCtx.record_label) { showToast('No record label set for this project (dashboard → Records)'); return false; }
  return true;
}

function _recOrderNote(po) {
  if (!po) return '';
  return po.ok ? ' · page order ✓' : ` · ⚠ page order: ${po.prefixes.length > 1 ? po.prefixes.length + ' prefixes ' : ''}`
    + `${po.gaps?.length ? po.gaps.length + ' gap(s) ' : ''}${po.non_numeric?.length ? po.non_numeric.length + ' non-numeric ' : ''}`
    + `${po.duplicates?.length ? po.duplicates.length + ' dup(s)' : ''}`;
}

async function runRecordSweep(opts = {}) {
  if (!_recNeedProject()) return null;
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
let _recMaxPages = 2;
let _recFlaggedOnly = false;
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
      '<button class="nav-btn" onclick="runRecordSweep({dryRun:true})" title="Report what a full sweep would change, without writing">🔍 Dry run</button>' +
      '<button class="nav-btn" onclick="_recResweepAll()" title="Renumber every record through the whole volume (locked ids kept)">⟳ Resweep all</button>' +
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
      `<button class="nav-btn" style="${contStyle}" onclick="_recToggleFrag('${_escHtml(f.stem)}',${f.idx},${f.continuation ? 'true' : 'false'})" ` +
        `title="${f.continuation ? 'Un-mark: this box STARTS a record' : 'Mark: this box CONTINUES the previous box in reading order'}">⛓${f.continuation ? ' cont.' : ''}</button>` +
      `<button class="nav-btn" onclick="_recResweepFrom('${_escHtml(f.stem)}')" title="Renumber from this page onward (earlier pages keep their ids)">⟳▶</button>` +
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
      `<button class="nav-btn" style="${contStyle}" onclick="_recToggleFrag('${_escHtml(f.stem)}',${f.idx},${f.continuation ? 'true' : 'false'})" ` +
        `title="${f.continuation ? 'Un-mark: this box STARTS a record' : 'Mark: this box CONTINUES the previous box in reading order'}">⛓${f.continuation ? ' cont.' : ''}</button> ` +
      `<button class="nav-btn" onclick="_recResweepFrom('${_escHtml(f.stem)}')" title="Renumber from this page onward (earlier pages keep their ids)">⟳▶</button>` +
      (f.locked ? ' <span title="hand-assigned id, kept by the sweep">🔒</span>' : '') +
    `</td></tr>`;
}

function _recRenderBody() {
  const body = document.getElementById('rec-browser-body');
  if (!body || !_recData) return;
  const d = _recData;
  const pg = _recPageNo();
  if (!d.records.length && !d.unswept.length) {
    body.innerHTML = `<div style="padding:20px;color:#8a94a6;">No "${_escHtml(d.record_label)}" boxes${_recFlaggedOnly ? ' with flags' : ''} — ` +
      (d.boxes ? 'nothing flagged. 🎉' : 'draw or detect them first, then sweep.') + '</div>';
    return;
  }
  let html = '';
  d.records.forEach(r => {
    const span = r.page_span[0] === r.page_span[1] ? pg(r.page_span[0]) : `${pg(r.page_span[0])}–${pg(r.page_span[1])}`;
    const flags = (r.flags || []).map(f => f === 'orphan'
      ? '<span style="color:#f87171;" title="the first fragment is marked as a continuation — its head is missing or lies on an earlier page">⚠ orphan</span>'
      : `<span style="color:#fbbf24;" title="spans more than ${d.max_pages} pages — check for a missed record start">long span</span>`).join(' ');
    html += `<div style="margin:10px 0 4px;padding:4px 6px;background:#12224a;border-radius:4px;display:flex;gap:10px;align-items:baseline;">` +
      `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${recordColor(r.id)};align-self:center;"></span>` +
      `<b style="color:#93c5fd;font-size:12px;">record ${r.id}</b>` +
      `<span style="color:#cde;max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_escHtml(r.name || '')}</span>` +
      `<span style="color:#667;">${r.n_fragments} fragment${r.n_fragments === 1 ? '' : 's'} · ${span}</span>` +
      (flags ? `<span>${flags}</span>` : '') + `</div>`;
    if (_recStrip) {
      r.fragments.forEach(f => { html += _recFragStrip(f, r.id); });
    } else {
      html += '<table class="rs-table" style="width:100%;"><tr><th style="white-space:nowrap;">where</th><th></th><th>text</th><th></th></tr>';
      r.fragments.forEach(f => { html += _recFragRow(f, r.id); });
      html += '</table>';
    }
  });
  const btn = document.getElementById('rec-strip-btn');
  if (btn) btn.textContent = _recStrip ? '☰ List view' : '📖 Strip view';
  if (d.unswept.length && !_recFlaggedOnly) {
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
  showToast(!cur ? `⛓ marked as continuation → ${stem} #${idx} — ⟳▶ to renumber` : `Continuation mark removed → ${stem} #${idx} — ⟳▶ to renumber`);
  await _recSyncEditor(stem);
  await openRecordBrowser();
}

async function _recResweepFrom(stem) {
  const d = await runRecordSweep({fromStem: stem});
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

async function runDeriveFragments(sorted, {dryRun = false} = {}) {
  if (!_recNeedProject()) return null;
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
