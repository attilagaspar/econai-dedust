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
let _revPinpoint = false;   // big animated arrow at the current item
let _revTarget = null;      // {stem, idx, y0, y1} — read by drawOverlay

function _revTogglePinpoint(on) {
  _revPinpoint = !!on;
  try { localStorage.setItem('revPinpoint', on ? '1' : '0'); } catch (e) {}
  drawOverlay();
}

// Drawn at the end of drawOverlay (lattice.js). Big pulsing arrow + ring on the
// current review item, so a cell that needs real work is impossible to miss.
function _drawReviewPointer() {
  if (!_revPinpoint || !_revActive || !_revTarget) return;
  if (!pageData || !pages[pageIdx] || (pages[pageIdx].stem || pages[pageIdx]) !== _revTarget.stem) return;
  const sh = pageData.shapes[_revTarget.idx];
  if (!sh?.points?.length) return;
  const xs = sh.points.map(p => p[0]), ys = sh.points.map(p => p[1]);
  const y0 = _revTarget.y0 != null ? _revTarget.y0 : Math.min(...ys);
  const y1 = _revTarget.y1 != null ? _revTarget.y1 : Math.max(...ys);
  const tl = imgToScreen(Math.min(...xs), y0);
  const br = imgToScreen(Math.max(...xs), y1);
  if (!tl || !br) return;
  const SVGNS = 'http://www.w3.org/2000/svg';
  const cx = (tl.x + br.x) / 2;

  // pulsing ring around the cell/row band
  const ring = document.createElementNS(SVGNS, 'rect');
  ring.setAttribute('x', tl.x - 4); ring.setAttribute('y', tl.y - 4);
  ring.setAttribute('width', Math.max(2, br.x - tl.x + 8));
  ring.setAttribute('height', Math.max(2, br.y - tl.y + 8));
  ring.setAttribute('fill', 'none');
  ring.setAttribute('stroke', '#ff3b6b');
  ring.setAttribute('stroke-width', '3');
  ring.setAttribute('rx', '3');
  ring.style.pointerEvents = 'none';
  const a1 = document.createElementNS(SVGNS, 'animate');
  a1.setAttribute('attributeName', 'stroke-width');
  a1.setAttribute('values', '2;6;2'); a1.setAttribute('dur', '0.9s');
  a1.setAttribute('repeatCount', 'indefinite');
  ring.appendChild(a1);
  const a2 = document.createElementNS(SVGNS, 'animate');
  a2.setAttribute('attributeName', 'stroke-opacity');
  a2.setAttribute('values', '1;0.35;1'); a2.setAttribute('dur', '0.9s');
  a2.setAttribute('repeatCount', 'indefinite');
  ring.appendChild(a2);
  svgOverlay.appendChild(ring);

  // big arrow above the cell, bobbing down toward it
  const size = 26;
  const tipY = tl.y - 6;
  const arrow = document.createElementNS(SVGNS, 'path');
  arrow.setAttribute('d',
    `M ${cx - size} ${tipY - size} L ${cx + size} ${tipY - size} L ${cx} ${tipY} Z`);
  arrow.setAttribute('fill', '#ff3b6b');
  arrow.setAttribute('stroke', '#fff');
  arrow.setAttribute('stroke-width', '2');
  arrow.style.pointerEvents = 'none';
  const bob = document.createElementNS(SVGNS, 'animateTransform');
  bob.setAttribute('attributeName', 'transform'); bob.setAttribute('type', 'translate');
  bob.setAttribute('values', '0 -8;0 2;0 -8'); bob.setAttribute('dur', '0.7s');
  bob.setAttribute('repeatCount', 'indefinite');
  arrow.appendChild(bob);
  svgOverlay.appendChild(arrow);
}

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
      body: JSON.stringify({ stems, signals, limit: 50000,
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
    const pinEl = document.getElementById('rev-pinpoint');
    if (pinEl) { pinEl.checked = localStorage.getItem('revPinpoint') === '1'; _revPinpoint = pinEl.checked; }
    const capped = (d.total || _revQueue.length) > _revQueue.length;
    showToast(`${_revQueue.length} cell(s) to review`
      + (capped ? ` (of ${d.total} — showing the first ${_revQueue.length})` : ''), 4000);
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
  _revTarget = { stem: it.stem, idx: it.idx, y0: it.y0, y1: it.y1 };
  const pi = _revStemIdx(it.stem);
  if (pi >= 0 && pi !== pageIdx) { await loadPage(pi); }
  if (pi >= 0) { selIdx = it.idx; selSet = new Set([it.idx]); drawOverlay(); }
  setTimeout(() => { inp.focus(); inp.select(); }, 30);
}

// One review action at a time — the keydown handler fires these without
// awaiting, so a second Enter before the first finishes would double-process
// one item and skip (never save) the next. _revBusy serializes them.
let _revBusy = false;

async function _revAccept() {
  if (_revBusy || !_revActive || _revPos >= _revQueue.length) return;
  _revBusy = true;
  try {
    const it = _revQueue[_revPos];
    const val = document.getElementById('rev-input').value;
    const pi = _revStemIdx(it.stem);
    if (pi < 0) { _revPos++; await _revShow(); return; }
    if (pi !== pageIdx) await loadPage(pi);
    const sh = pageData.shapes[it.idx];
    _revUndo = { pos: _revPos, stem: it.stem, idx: it.idx, row: it.row,
                 prev: JSON.parse(JSON.stringify(sh)) };
    let ok = false;
    if (it.row && sh.row_struct?.rows) {
      const r = sh.row_struct.rows.find(x => x.n === it.row);
      if (r) { r.human = val; }
      ok = await saveRowStruct(it.idx);
    } else {
      sh.human_output = sh.human_output || {};
      sh.human_output.human_corrected_text = val;
      ok = await patchShape(it.idx, { human_corrected_text: val });
    }
    if (ok === false) {   // save failed — stay on this item, don't advance
      showToast('✕ Save failed — press Enter to retry', 4000);
      _revUndo = null;
      return;
    }
    drawOverlay();
    _revPos++;
    await _revShow();
  } finally { _revBusy = false; }
}

async function _revSkip() {
  if (_revBusy || !_revActive) return;
  _revBusy = true;
  try { _revPos++; await _revShow(); } finally { _revBusy = false; }
}

async function _revUndoLast() {
  if (_revBusy) return;
  if (!_revUndo) { showToast('Nothing to undo'); return; }
  _revBusy = true;
  try {
    const u = _revUndo; _revUndo = null;
    const pi = _revStemIdx(u.stem);
    if (pi < 0) return;
    if (pi !== pageIdx) await loadPage(pi);
    pageData.shapes[u.idx] = u.prev;
    await replaceAllShapes();
    drawOverlay();
    _revPos = u.pos;
    await _revShow();
  } finally { _revBusy = false; }
}

function _revFinish() {
  showToast('✓ Review queue cleared for this scope');
  _revClose();
}
function _revClose() {
  _revActive = false;
  _revTarget = null;
  document.getElementById('review-strip').style.display = 'none';
  drawOverlay();   // clear the pointer
}

// keyboard: only while the strip is open. Enter accept, ↓ skip, U undo, Esc quit.
document.addEventListener('keydown', e => {
  if (!_revActive) return;
  const inInput = e.target && e.target.id === 'rev-input';
  if (e.key === 'Enter' && inInput) { e.preventDefault(); _revAccept(); }
  else if (e.key === 'ArrowDown' || (e.key === 'Tab' && inInput)) {
    e.preventDefault(); _revSkip();
  } else if ((e.key === 'u' || e.key === 'U') && inInput && !e.ctrlKey) {
    // 'u' inside the field is rare in numeric data; guard: only when field is a pure number/dash
    if (/^[\d.\-\s]*$/.test(document.getElementById('rev-input').value)) { e.preventDefault(); _revUndoLast(); }
  } else if (e.key === 'Escape') { e.preventDefault(); _revClose(); }
});
