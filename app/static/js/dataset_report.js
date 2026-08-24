// Split classic script (shared global scope, load order matters — see
// knowledge_base/02_architecture.md). Dataset diagnostics report: a batch op
// that runs the declared dataset's check ladder (structure / parse / range /
// unresolved / duplicate key — see knowledge_base/10_dataset_layer.md) and
// shows the findings in the report chassis pioneered by dup_report.js:
// grouped rows with crop + PDF/OCR/LLM + editable Human, click-to-jump,
// minimizable pill. Human edits are written straight into the page JSONs:
//   row units  → PATCH /api/page/shape/row-field   (targeted, server-side RMW)
//   cell units → PATCH /api/page/shape (human_corrected_text)
// Findings are fixed in place; re-run the report to converge.
// Reuses: _escHtml, _searchJump, _serializeWrite, showToast.

let _dsrGroups = [];
let _dsrMeta   = {};

const _DSR_CHECKS = {
  structure:     {label: 'structure',   color: '#f87171',
                  hint: 'the page disagrees with the declaration'},
  parse:         {label: 'parse',       color: '#fbbf24',
                  hint: 'the value fails its declared dtype'},
  range:         {label: 'range',       color: '#fb923c',
                  hint: 'hard min/max violation'},
  unresolved:    {label: 'unresolved',  color: '#a78bfa',
                  hint: 'entity cell with no resolved authority'},
  key:           {label: 'key',         color: '#f472b6',
                  hint: 'record key problems'},
  duplicate_key: {label: 'dup key',     color: '#f472b6',
                  hint: 'the same key appears in several records'},
};

// Populate the dataset dropdown when the batch op is selected.
async function _batchDatasetInit() {
  const sel = document.getElementById('batch-dataset-name');
  if (!sel) return;
  sel.innerHTML = '<option value="">…</option>';
  try {
    const r = await fetch(`${API}/api/datasets?folder=${encodeURIComponent(folder)}`);
    const d = await r.json();
    const ds = d.datasets || [];
    if (!ds.length) {
      sel.innerHTML = '<option value="">— no declarations in projects/…/datasets/ —</option>';
      return;
    }
    sel.innerHTML = ds.map(x =>
      `<option value="${_escHtml(x.name)}" ${x.error ? 'disabled' : ''}>` +
      `${_escHtml(x.name)}${x.error ? ' ⚠ invalid' : ` (${x.variables} vars · pattern ${_escHtml(x.pattern || '1')})`}` +
      `</option>`).join('');
    const bad = ds.filter(x => x.error);
    const info = document.getElementById('batch-dataset-info');
    if (info) info.textContent = bad.length
      ? `⚠ ${bad.map(x => x.name + ': ' + x.error).join(' · ')}` : '';
  } catch (e) {
    sel.innerHTML = '<option value="">— failed to list datasets —</option>';
  }
}

async function runDatasetReport(name, pagesRaw) {
  if (!name) { showToast('Pick a dataset declaration first'); return; }
  showToast(`Diagnosing dataset "${name}"…`);
  let data;
  try {
    const r = await fetch(`${API}/api/dataset/${encodeURIComponent(name)}/diagnose?folder=${encodeURIComponent(folder)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pages: pagesRaw || null}),
    });
    if (!r.ok) { const m = await r.json().catch(() => ({})); showToast('Diagnose error: ' + (m.detail || r.status), 7000); return; }
    data = await r.json();
  } catch (e) { showToast('Diagnose error: ' + (e?.message || e)); return; }
  _dsrGroups = data.groups || [];
  _dsrMeta   = data;
  closeBatchModal?.();
  _dsrOpenModal();
}

function _dsrCloseModal() {
  document.getElementById('dsr-modal')?.remove();
  document.getElementById('dsr-pill')?.remove();
}

function _dsrMinimize() {
  const m = document.getElementById('dsr-modal');
  if (!m || document.getElementById('dsr-pill')) return;
  m.style.display = 'none';
  const pill = document.createElement('div');
  pill.id = 'dsr-pill';
  pill.style.cssText = 'position:fixed;right:18px;bottom:54px;z-index:1800;background:#12224a;' +
    'border:1px solid #2a4a8e;border-radius:18px;box-shadow:0 6px 22px rgba(0,0,0,0.55);' +
    'padding:7px 14px;font-size:12px;color:#cde;cursor:pointer;display:flex;gap:8px;align-items:center;';
  pill.title = 'Restore the dataset diagnostics report';
  pill.innerHTML = `📊 ${_escHtml(_dsrMeta.dataset || 'dataset')} ` +
    `<span style="color:#8a94a6;">(${_dsrMeta.findings_total || 0} finding${_dsrMeta.findings_total === 1 ? '' : 's'})</span>` +
    ` <span style="color:#93c5fd;">▲ restore</span>` +
    ` <span onclick="event.stopPropagation();_dsrCloseModal()" title="Close the report" style="color:#fca5a5;padding-left:4px;">✕</span>`;
  pill.onclick = _dsrRestore;
  document.body.appendChild(pill);
}

function _dsrRestore() {
  document.getElementById('dsr-pill')?.remove();
  const m = document.getElementById('dsr-modal');
  if (m) m.style.display = 'flex';
}

function _dsrOpenModal() {
  _dsrCloseModal();
  const m = document.createElement('div');
  m.id = 'dsr-modal';
  m.style.cssText = 'position:fixed;inset:24px;z-index:1800;background:#0a1128;border:1px solid #2a4a8e;' +
    'border-radius:8px;box-shadow:0 12px 40px rgba(0,0,0,0.7);display:flex;flex-direction:column;' +
    'font-size:11px;color:#ccc;';
  const d = _dsrMeta;
  const summary = `${d.findings_total || 0} finding${d.findings_total === 1 ? '' : 's'}`
    + ` · ${d.records || 0} record(s) from ${d.pages || 0} page(s) / ${d.cycles || 0} cycle(s)`
    + (d.truncated ? ' · ⚠ truncated (narrow the page range)' : '');
  m.innerHTML =
    '<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid #1a3a6e;">' +
      `<b style="color:#e0e0e0;font-size:13px;">📊 Dataset diagnostics — ${_escHtml(d.dataset || '')}</b>` +
      `<span style="color:#8a94a6;">${_escHtml(summary)}</span>` +
      '<div style="flex:1"></div>' +
      '<span style="color:#556;font-size:10px;">fix Human values here, then re-run to converge · clicking a location opens it in the editor</span>' +
      '<button class="nav-btn" onclick="_dsrMinimize()" title="Minimize to a pill (bottom right)">▁</button>' +
      '<button class="nav-btn" onclick="_dsrCloseModal()" title="Close">✕</button>' +
    '</div>' +
    '<div id="dsr-body" style="flex:1;overflow-y:auto;padding:8px 12px;"></div>';
  document.body.appendChild(m);
  _dsrRenderBody();
}

function _dsrRenderBody() {
  const body = document.getElementById('dsr-body');
  if (!body) return;
  if (!_dsrGroups.length) {
    body.innerHTML = '<div style="padding:20px;color:#8a94a6;">' +
      'Every page matches the declaration and every value parses cleanly. 🎉</div>';
    return;
  }
  const stemToPage = {};
  pages.forEach((p, i) => { stemToPage[p.stem] = i + 1; });
  let html = '';
  _dsrGroups.forEach((g, gi) => {
    const c = _DSR_CHECKS[g.check] || {label: g.check, color: '#93c5fd', hint: ''};
    html += `<div style="margin:10px 0 4px;padding:4px 8px;background:#12224a;border-radius:4px;display:flex;gap:10px;align-items:baseline;">` +
      `<span title="${_escHtml(c.hint)}" style="color:${c.color};font-weight:700;font-size:10px;` +
        `border:1px solid ${c.color};border-radius:3px;padding:0 5px;">${_escHtml(c.label)}</span>` +
      `<b style="color:#93c5fd;font-size:12px;">${_escHtml(g.title || g.check)}</b>` +
      (g.variable ? `<span style="color:#667;">${_escHtml(g.variable)}</span>` : '') +
      `<span style="color:#fbbf24;">× ${g.count}</span>` +
      (g.items.length < g.count ? `<span style="color:#f87171;">(showing ${g.items.length})</span>` : '') +
      `</div>`;
    html += '<table class="rs-table" style="width:100%;"><tr><th style="white-space:nowrap;">where</th><th></th>' +
            '<th>PDF</th><th>OCR</th><th>LLM</th><th>Human</th><th>problem</th></tr>';
    g.items.forEach((it, ii) => {
      const pg  = stemToPage[it.stem] ? `p${stemToPage[it.stem]}` : it.stem;
      const loc = (it.idx == null) ? `${pg} (page)` :
        `${pg}` + (it.row_n != null ? ` · row ${it.row_n}` : '');
      const band = (it.y0 != null && it.y1 != null) ? `&y0=${it.y0}&y1=${it.y1}` : '';
      const img = (it.idx == null) ? '' :
        `<img src="${API}/api/cell?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(it.stem)}&idx=${it.idx}${band}" ` +
        `loading="lazy" style="max-height:${it.row_i != null ? 22 : 60}px;max-width:170px;object-fit:contain;display:block;">`;
      const humanCell = (it.idx == null) ? '' :
        `<input class="rs-human" value="${_escHtml(it.human || '')}" onchange="_dsrHumanEdit(${gi},${ii},this.value)">`;
      html += `<tr>` +
        `<td class="rs-clk" style="white-space:nowrap;color:#93c5fd;" onclick="_dsrJump(${gi},${ii})" ` +
          `title="Open this location in the editor">${_escHtml(loc)}</td>` +
        `<td class="rs-img">${img}</td>` +
        `<td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${_escHtml(it.pdf || '')}">${_escHtml(it.pdf || '')}</td>` +
        `<td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${_escHtml(it.ocr || '')}">${_escHtml(it.ocr || '')}</td>` +
        `<td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${_escHtml(it.llm || '')}">${_escHtml(it.llm || '')}</td>` +
        `<td>${humanCell}</td>` +
        `<td style="max-width:260px;color:#fca5a5;" title="${_escHtml(it.detail || '')}">${_escHtml(it.detail || '')}</td>` +
        `</tr>`;
    });
    html += '</table>';
  });
  body.innerHTML = html;
}

async function _dsrJump(gi, ii) {
  const it = _dsrGroups[gi]?.items[ii];
  if (!it) return;
  _dsrMinimize();                                    // get out of the way first
  await _searchJump({stem: it.stem, idx: it.idx});   // idx null = page only
}

async function _dsrSyncEditor(stem) {
  if (pages[pageIdx]?.stem === stem) {
    await reloadPageData(); drawOverlay(); updatePanel();
  }
}

async function _dsrHumanEdit(gi, ii, value) {
  const it = _dsrGroups[gi]?.items[ii];
  if (!it || it.idx == null) return;
  const params = new URLSearchParams({folder, stem: it.stem, idx: it.idx});
  const ok = await _serializeWrite(async () => {
    try {
      const r = (it.row_i != null)
        ? await fetch(`${API}/api/page/shape/row-field?${params}`, {
            method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({row_i: it.row_i, human: value})})
        : await fetch(`${API}/api/page/shape?${params}`, {
            method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({human_corrected_text: value})});
      return r.ok;
    } catch (e) { return false; }
  });
  if (!ok) { showToast('Save failed — the edit was NOT stored'); return; }
  it.human = value;
  showToast(`Human saved → ${it.stem} (re-run the report to re-check)`);
  await _dsrSyncEditor(it.stem);
}
