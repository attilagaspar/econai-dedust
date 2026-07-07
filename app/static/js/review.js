// Split from index.html — classic scripts share the global scope;
// load order in index.html is load-bearing. See knowledge_base/02_architecture.md.
// ── P3: page status scoreboard ──────────────────────────────────────────────
// Status lives in the page JSON's flags.status; set via PATCH /api/page/flags.

async function setPageStatus(status, opts = {}) {
  if (!pages.length || !pages[pageIdx]) return;
  const stem = pages[pageIdx].stem;
  try {
    await fetch(`${API}/api/page/flags?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(stem)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ flags: { status } }),
    });
    if (pageData) { pageData.flags = pageData.flags || {}; pageData.flags.status = status; }
    _syncStatusChip();
    if (!opts.silent) showToast(`Page marked: ${status}`);
  } catch (e) { showToast('Status save failed: ' + (e.message || e)); }
}

function _syncStatusChip() {
  const sel = document.getElementById('page-status');
  if (!sel) return;
  sel.value = (pageData?.flags?.status) || 'predicted';
  const c = { predicted: '#8a94a6', corrected: '#f0c040', verified: '#22c55e', problem: '#e94560' }[sel.value];
  sel.style.color = c; sel.style.borderColor = c;
}

// V = verify current page and jump to the next page that isn't verified yet.
async function verifyAndAdvance() {
  await setPageStatus('verified', { silent: true });
  try {
    const r = await fetch(`${API}/api/project/status?folder=${encodeURIComponent(folder)}`);
    const st = (await r.json()).pages || [];
    const order = pages.map(p => p.stem);
    const rank = {}; st.forEach(p => rank[p.stem] = p.status);
    for (let k = 1; k <= pages.length; k++) {
      const j = (pageIdx + k) % pages.length;
      if ((rank[pages[j].stem] || 'predicted') !== 'verified') {
        showToast(`✓ verified — jumping to page ${j + 1}`);
        await loadPage(j);
        return;
      }
    }
    showToast('✓ verified — all pages are verified 🎉');
  } catch (e) { showToast('✓ verified'); }
}

// ── P1: review queue ─────────────────────────────────────────────────────────
let _revQueue = [], _revPos = 0, _revActive = false, _revUndo = null;

function openReview() {
  if (!pages.length) { showToast('Open a project first'); return; }
  document.getElementById('review-modal').style.display = 'flex';
}
function closeReviewModal() { document.getElementById('review-modal').style.display = 'none'; }

async function startReview() {
  const signals = [...document.querySelectorAll('.rev-sig:checked')].map(c => c.value);
  if (!signals.length) { showToast('Pick at least one signal'); return; }
  const pagesInput = document.getElementById('rev-pages').value.trim();
  let stems = [];
  if (pagesInput) {
    const idxs = _parsePageRange(pagesInput);
    if (idxs) stems = [...idxs].sort((a, b) => a - b).map(i => pages[i]?.stem).filter(Boolean);
  } else {
    stems = pages.map(p => p.stem);
  }
  closeReviewModal();
  showToast('Scanning for suspect cells…');
  try {
    const r = await fetch(`${API}/api/review/queue?folder=${encodeURIComponent(folder)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stems, signals,
        col_filter: document.getElementById('rev-cols').value.trim() || null,
        exclude_verified: document.getElementById('rev-skip-verified').checked }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    _revQueue = d.queue || [];
    _revPos = 0; _revUndo = null;
    if (!_revQueue.length) { showToast('✓ Nothing flagged in this scope.'); return; }
    _revActive = true;
    document.getElementById('review-strip').style.display = 'block';
    showToast(`${_revQueue.length} cell(s) to review`);
    await _revShow();
  } catch (e) { showToast('Review error: ' + (e.message || e)); }
}

function _revStemIdx(stem) { return pages.findIndex(p => (p.stem || p) === stem); }

async function _revShow() {
  if (_revPos >= _revQueue.length) { _revFinish(); return; }
  const it = _revQueue[_revPos];
  document.getElementById('rev-counter').textContent = `${_revPos + 1} / ${_revQueue.length}`;
  document.getElementById('rev-why').textContent = it.why + (it.extra ? ` (${it.extra})` : '');
  document.getElementById('rev-loc').textContent =
    `${it.stem} · col ${it.col ?? '–'}${it.row ? ' · row ' + it.row : ''}`;
  const inp = document.getElementById('rev-input');
  inp.value = it.best || '';
  // crop snippet (row band if present)
  let u = `${API}/api/cell?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(it.stem)}&idx=${it.idx}&pad=3`;
  if (it.y0 != null && it.y1 != null) u += `&y0=${it.y0}&y1=${it.y1}`;
  document.getElementById('rev-crop').src = u;
  // navigate the canvas to the item's page and highlight the cell
  const pi = _revStemIdx(it.stem);
  if (pi >= 0 && pi !== pageIdx) { await loadPage(pi); }
  if (pi >= 0) { selIdx = it.idx; selSet = new Set([it.idx]); drawOverlay(); }
  setTimeout(() => { inp.focus(); inp.select(); }, 30);
}

async function _revAccept() {
  if (!_revActive || _revPos >= _revQueue.length) return;
  const it = _revQueue[_revPos];
  const val = document.getElementById('rev-input').value;
  const pi = _revStemIdx(it.stem);
  if (pi < 0) { _revPos++; await _revShow(); return; }
  if (pi !== pageIdx) await loadPage(pi);
  const sh = pageData.shapes[it.idx];
  _revUndo = { pos: _revPos, stem: it.stem, idx: it.idx, row: it.row,
               prev: JSON.parse(JSON.stringify(sh)) };
  if (it.row && sh.row_struct?.rows) {
    const r = sh.row_struct.rows.find(x => x.n === it.row);
    if (r) { r.human = val; }
    await saveRowStruct(it.idx);
  } else {
    sh.human_output = sh.human_output || {};
    sh.human_output.human_corrected_text = val;
    await patchShape(it.idx, { human_corrected_text: val });
  }
  drawOverlay();
  _revPos++;
  await _revShow();
}

async function _revUndoLast() {
  if (!_revUndo) { showToast('Nothing to undo'); return; }
  const u = _revUndo; _revUndo = null;
  const pi = _revStemIdx(u.stem);
  if (pi < 0) return;
  if (pi !== pageIdx) await loadPage(pi);
  pageData.shapes[u.idx] = u.prev;
  await replaceAllShapes();
  drawOverlay();
  _revPos = u.pos;
  await _revShow();
}

function _revFinish() {
  showToast('✓ Review queue cleared for this scope');
  _revClose();
}
function _revClose() {
  _revActive = false;
  document.getElementById('review-strip').style.display = 'none';
}

// keyboard: only while the strip is open. Enter accept, ↓ skip, U undo, Esc quit.
document.addEventListener('keydown', e => {
  if (!_revActive) return;
  const inInput = e.target && e.target.id === 'rev-input';
  if (e.key === 'Enter' && inInput) { e.preventDefault(); _revAccept(); }
  else if (e.key === 'ArrowDown' || (e.key === 'Tab' && inInput)) {
    e.preventDefault(); _revPos++; _revShow();
  } else if ((e.key === 'u' || e.key === 'U') && inInput && !e.ctrlKey) {
    // 'u' inside the field is rare in numeric data; guard: only when field is a pure number/dash
    if (/^[\d.\-\s]*$/.test(document.getElementById('rev-input').value)) { e.preventDefault(); _revUndoLast(); }
  } else if (e.key === 'Escape') { e.preventDefault(); _revClose(); }
});
