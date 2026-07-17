// Split classic script (shared global scope, load order matters — see
// knowledge_base/02_architecture.md). Authority duplicate report: a batch op
// that finds every entity resolved in 2+ places across the selected pages /
// columns and shows the occurrences one below the other in an editable
// table (crop, PDF, OCR, LLM, Human, Authority). Human + Authority edits are
// written straight into the corresponding page JSONs:
//   row units  → PATCH /api/page/shape/row-field   (targeted, server-side RMW)
//   cell units → PATCH /api/page/shape (human) / POST /api/page/shape/authority
// Reuses: _resolveText, _candToAuth, _rsAuthCellHtml, _escHtml, _searchJump.

let _dupGroups = [];

async function runDupReport(stems, colFilter) {
  const minCount = parseInt(document.getElementById('batch-dup-mincount')?.value, 10) || 2;
  showToast('Scanning for duplicate resolutions…');
  let data;
  try {
    const r = await fetch(`${API}/api/authority/duplicates?folder=${encodeURIComponent(folder)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({stems, col_filter: colFilter || null, min_count: minCount}),
    });
    if (!r.ok) { const m = await r.json().catch(() => ({})); showToast('Report error: ' + (m.detail || r.status)); return; }
    data = await r.json();
  } catch (e) { showToast('Report error: ' + (e?.message || e)); return; }
  _dupGroups = data.groups || [];
  closeBatchModal?.();
  _dupOpenModal(data);
}

function _dupCloseModal() {
  document.getElementById('dup-report-modal')?.remove();
  _dupCloseAuthDD();
}

function _dupOpenModal(data) {
  _dupCloseModal();
  const m = document.createElement('div');
  m.id = 'dup-report-modal';
  m.style.cssText = 'position:fixed;inset:24px;z-index:1800;background:#0a1128;border:1px solid #2a4a8e;' +
    'border-radius:8px;box-shadow:0 12px 40px rgba(0,0,0,0.7);display:flex;flex-direction:column;' +
    'font-size:11px;color:#ccc;';
  const summary = `${data.duplicate_entities} entit${data.duplicate_entities === 1 ? 'y' : 'ies'} with duplicates`
    + ` · ${data.total_resolved} resolved units on ${data.pages} page(s)`
    + ` · ${data.distinct_entities} distinct entities`;
  m.innerHTML =
    '<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid #1a3a6e;">' +
      '<b style="color:#e0e0e0;font-size:13px;">🏛 Authority duplicate report</b>' +
      `<span style="color:#8a94a6;">${_escHtml(summary)}</span>` +
      '<div style="flex:1"></div>' +
      '<span style="color:#556;font-size:10px;">click a location to open it in the editor behind this window</span>' +
      '<button class="nav-btn" onclick="_dupCloseModal()" title="Close">✕</button>' +
    '</div>' +
    '<div id="dup-report-body" style="flex:1;overflow-y:auto;padding:8px 12px;"></div>';
  document.body.appendChild(m);
  _dupRenderBody();
}

function _dupRenderBody() {
  const body = document.getElementById('dup-report-body');
  if (!body) return;
  if (!_dupGroups.length) {
    body.innerHTML = '<div style="padding:20px;color:#8a94a6;">No entity is resolved in more than one place — nothing to review. 🎉</div>';
    return;
  }
  const stemToPage = {};
  pages.forEach((p, i) => { stemToPage[p.stem] = i + 1; });
  let html = '';
  _dupGroups.forEach((g, gi) => {
    html += `<div style="margin:10px 0 4px;padding:4px 6px;background:#12224a;border-radius:4px;display:flex;gap:10px;align-items:baseline;">` +
      `<b style="color:#93c5fd;font-size:12px;">${_escHtml(g.name)}</b>` +
      `<span style="color:#667;">${_escHtml(g.id)}${g.type ? ' · ' + _escHtml(g.type) : ''}</span>` +
      `<span style="color:#fbbf24;">× ${g.count}</span></div>`;
    html += '<table class="rs-table" style="width:100%;"><tr><th style="white-space:nowrap;">where</th><th></th>' +
            '<th>PDF</th><th>OCR</th><th>LLM</th><th>Human</th><th>Auth</th></tr>';
    g.items.forEach((it, ii) => {
      const pg  = stemToPage[it.stem] ? `p${stemToPage[it.stem]}` : it.stem;
      const loc = `${pg} · r${it.super_row}c${it.super_col}` + (it.row_n != null ? ` · row ${it.row_n}` : '');
      const band = (it.y0 != null && it.y1 != null) ? `&y0=${it.y0}&y1=${it.y1}` : '';
      const src = `${API}/api/cell?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(it.stem)}&idx=${it.idx}${band}`;
      html += `<tr>` +
        `<td class="rs-clk" style="white-space:nowrap;color:#93c5fd;" onclick="_dupJump(${gi},${ii})" ` +
          `title="Open this cell in the editor">${_escHtml(loc)}</td>` +
        `<td class="rs-img"><img src="${src}" loading="lazy" style="max-height:${it.row_n != null ? 22 : 60}px;max-width:170px;object-fit:contain;display:block;"></td>` +
        `<td style="max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${_escHtml(it.pdf)}">${_escHtml(it.pdf)}</td>` +
        `<td style="max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${_escHtml(it.ocr)}">${_escHtml(it.ocr)}</td>` +
        `<td style="max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${_escHtml(it.llm)}">${_escHtml(it.llm)}</td>` +
        `<td><input class="rs-human" value="${_escHtml(it.human)}" onchange="_dupHumanEdit(${gi},${ii},this.value)"></td>` +
        `<td class="rs-auth rs-clk" id="dup-auth-${gi}-${ii}" onclick="_dupAuthClick(${gi},${ii},event)" ` +
          `title="Click to pick another entity or clear">${_rsAuthCellHtml({authority: it.authority})}</td>` +
        `</tr>`;
    });
    html += '</table>';
  });
  body.innerHTML = html;
}

async function _dupJump(gi, ii) {
  const it = _dupGroups[gi]?.items[ii];
  if (!it) return;
  await _searchJump({stem: it.stem, idx: it.idx});   // loads page, selects, zooms
}

// After any save: if the edited page happens to be the one open in the
// editor, refresh the loaded copy so the panel doesn't show stale data.
async function _dupSyncEditor(stem) {
  if (pages[pageIdx]?.stem === stem) {
    await reloadPageData(); drawOverlay(); updatePanel();
  }
}

async function _dupHumanEdit(gi, ii, value) {
  const it = _dupGroups[gi]?.items[ii];
  if (!it) return;
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
  showToast(`Human saved → ${it.stem}`);
  await _dupSyncEditor(it.stem);
}

async function _dupSetAuthority(gi, ii, auth) {
  const it = _dupGroups[gi]?.items[ii];
  if (!it) return;
  const params = new URLSearchParams({folder, stem: it.stem, idx: it.idx});
  let saved = null;
  const ok = await _serializeWrite(async () => {
    try {
      if (it.row_i != null) {
        const r = await fetch(`${API}/api/page/shape/row-field?${params}`, {
          method: 'PATCH', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({row_i: it.row_i, set_authority: true, authority: auth})});
        if (r.ok) saved = auth;
        return r.ok;
      }
      const r = await fetch(`${API}/api/page/shape/authority?${params}`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: auth?.id || null, source: 'human', name: _authFile()})});
      if (r.ok) saved = (await r.json()).authority;
      return r.ok;
    } catch (e) { return false; }
  });
  if (!ok) { showToast('Save failed — the edit was NOT stored'); return; }
  it.authority = saved;
  const cell = document.getElementById(`dup-auth-${gi}-${ii}`);
  if (cell) cell.innerHTML = _rsAuthCellHtml({authority: it.authority});
  showToast(auth ? `Authority saved → ${it.stem}` : `Authority cleared → ${it.stem}`);
  _dupCloseAuthDD();
  await _dupSyncEditor(it.stem);
}

// ── Entity picker dropdown (same interaction as the rows-panel Auth cell) ──
let _dupDDDebounce = null;
function _dupCloseAuthDD() {
  document.getElementById('dup-auth-dd')?.remove();
  document.removeEventListener('mousedown', _dupAuthDocClose, true);
}
function _dupAuthDocClose(ev) {
  if (!ev.target.closest('#dup-auth-dd')) _dupCloseAuthDD();
}

async function _dupAuthClick(gi, ii, ev) {
  const it = _dupGroups[gi]?.items[ii];
  if (!it) return;
  _dupCloseAuthDD();
  const anchor = ev.currentTarget.getBoundingClientRect();
  const dd = document.createElement('div');
  dd.id = 'dup-auth-dd';
  dd.style.cssText = 'position:fixed;z-index:2000;background:#0d1b35;border:1px solid #2a4a8e;' +
    'border-radius:5px;box-shadow:0 6px 22px rgba(0,0,0,0.55);max-height:50vh;overflow:auto;' +
    'min-width:220px;max-width:380px;font-size:11px;padding:4px;';
  const initial = (it.human || it.llm || it.ocr || it.pdf || it.authority?.name || '').trim();
  dd.innerHTML =
    `<input id="dup-auth-dd-search" type="text" placeholder="type to search… (3+ chars)" value="${_escHtml(initial)}" ` +
    `style="width:100%;box-sizing:border-box;background:#16213e;border:1px solid #2a4a8e;border-radius:3px;` +
    `color:#cde;font:inherit;padding:3px 6px;margin-bottom:4px;">` +
    `<div id="dup-auth-dd-list" style="padding:4px;color:#667;">…</div>`;
  document.body.appendChild(dd);
  dd.style.top  = Math.min(anchor.bottom + 2, window.innerHeight - 80) + 'px';
  dd.style.left = Math.min(anchor.left, window.innerWidth - dd.offsetWidth - 8) + 'px';

  const listEl = dd.querySelector('#dup-auth-dd-list');
  const render = (cands) => {
    const curId = it.authority?.id;
    listEl.style.padding = '0'; listEl.style.color = '';
    listEl.innerHTML = ((cands || []).length
      ? cands.map((c, k) => {
          const loc = [c.district_name, c.county_name].filter(Boolean).join(', ');
          const sc  = c.score >= 95 ? '#22c55e' : c.score >= 80 ? '#eab308' : '#f87171';
          const sel = c.id === curId ? 'background:#14532d;' : '';
          return `<div class="dup-auth-opt" data-k="${k}" style="display:flex;justify-content:space-between;gap:8px;` +
            `align-items:center;padding:4px 6px;border-radius:3px;cursor:pointer;${sel}">` +
            `<span><b style="color:#cde;">${_escHtml(c.name)}</b> <span style="color:#778;font-size:9px;">${_escHtml(c.type || '')}</span>` +
            `${loc ? `<br><span style="color:#7a8;font-size:9px;">${_escHtml(loc)}</span>` : ''}</span>` +
            `<span style="color:${sc};font-weight:600;white-space:nowrap;">${c.score}%</span></div>`;
        }).join('')
      : `<div style="padding:6px;color:#a55;">No match.</div>`)
      + (it.authority ? `<div id="dup-auth-clear" style="padding:4px 6px;border-top:1px solid #1a3a6e;` +
          `margin-top:3px;color:#fca5a5;cursor:pointer;">✕ Clear</div>` : '');
    listEl.querySelectorAll('.dup-auth-opt').forEach(opt => opt.onclick = () =>
      _dupSetAuthority(gi, ii, _candToAuth(cands[+opt.dataset.k], 'human')));
    listEl.querySelector('#dup-auth-clear')?.addEventListener('click', () => _dupSetAuthority(gi, ii, null));
  };

  const type = document.getElementById('auth-type')?.value || '';
  render(initial.length >= 2 ? await _resolveText(initial, type, _authParent()) : []);

  const search = dd.querySelector('#dup-auth-dd-search');
  search.addEventListener('input', () => {
    const q = search.value.trim();
    if (_dupDDDebounce) clearTimeout(_dupDDDebounce);
    if (q.length < 3) return;
    _dupDDDebounce = setTimeout(async () => {
      const res = await _resolveText(q, type, _authParent());
      if (document.getElementById('dup-auth-dd')) render(res);
    }, 180);
  });
  setTimeout(() => { search.focus(); search.select(); }, 0);
  setTimeout(() => document.addEventListener('mousedown', _dupAuthDocClose, true), 0);
}
