// Split from index.html — classic scripts share the global scope;
// load order in index.html is load-bearing. See knowledge_base/02_architecture.md.
// ── Keyboard shortcuts ───────────────────────────────────────────────────────
function navigateLattice(dir) {
  if (selIdx < 0 || !pageData?.shapes) return;
  const sh = pageData.shapes[selIdx];
  if (sh.super_row == null || sh.super_column == null) return;
  let tr = sh.super_row, tc = sh.super_column;
  if      (dir === 'right') tc++;
  else if (dir === 'left')  tc--;
  else if (dir === 'down')  tr++;
  else if (dir === 'up')    tr--;
  const tbl = sh.table ?? 0;
  const ni = pageData.shapes.findIndex(s => s.super_row === tr && s.super_column === tc
                                            && (s.table ?? 0) === tbl);
  if (ni < 0) return;
  selIdx = ni;
  selSet.clear();
  selSet.add(ni);
  updatePanel();
  drawOverlay();
}

document.addEventListener('keydown', e => {
  const inField=e.target.tagName==='TEXTAREA'||e.target.tagName==='INPUT'||e.target.tagName==='SELECT';
  if (e.key==='Escape') {
    if (document.getElementById('lattice-modal').classList.contains('show')) { closeLatticeModal(); return; }
    if (document.getElementById('persp-modal').classList.contains('show')) { rejectPerspective(); return; }
    if (perspMode) { cancelPerspMode(); return; }
    if (tableMode) { cancelTableMode(); return; }
    if (latticeSepMode) { latticeSepMode = null; _updateLatticeSepBtns(); drawOverlay(); return; }
  }
  if ((e.ctrlKey||e.metaKey)&&e.key==='s') { e.preventDefault(); saveCorrection(); return; }
  if ((e.ctrlKey||e.metaKey)&&e.key==='z') { e.preventDefault(); if(tableMode) tableUndo(); else undo(); return; }
  if ((e.ctrlKey||e.metaKey)&&e.key==='c') { e.preventDefault(); copyShape(); return; }
  if ((e.ctrlKey||e.metaKey)&&e.key==='v') { e.preventDefault(); pasteShape(); return; }
  if ((e.ctrlKey||e.metaKey)&&e.key.startsWith('Arrow')) { e.preventDefault(); cloneInDirection(e.key); return; }
  if (inField) return;
  if (e.key==='Delete'||e.key==='Backspace') { deleteSelectedShape(); return; }
  if (e.key==='e'||e.key==='E') { if(pages.length) toggleEditMode(); return; }
  if (e.key==='a'||e.key==='A') { pageData.shapes.forEach((_,i)=>selSet.add(i)); selIdx=selSet.size?[...selSet][0]:-1; updatePanel(); drawOverlay(); return; }
  if (e.key==='n'||e.key==='N') { goPage(+1); return; }
  if (e.key==='m'||e.key==='M') { goPage(-1); return; }
  if (e.key==='p'||e.key==='P') { cloneSelectionToPage(-1); return; }
  if (e.key==='o'||e.key==='O') { cloneSelectionToPage(-2); return; }
  if (e.key==='ArrowRight') { navigateLattice('right'); return; }
  if (e.key==='ArrowLeft')  { navigateLattice('left');  return; }
  if (e.key==='ArrowDown')  { navigateLattice('down');  return; }
  if (e.key==='ArrowUp')    { navigateLattice('up');    return; }
});

// ── Page loading ─────────────────────────────────────────────────────────────
async function reloadPageData() {
  const p=pages[pageIdx];
  const r=await fetch(`${API}/api/page?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(p.stem)}`);
  pageData=await r.json();
  computeDiagnostics();
}

async function loadFolder() {
  folder=document.getElementById('folder-input').value.trim();
  if (!folder) return;
  const r=await fetch(`${API}/api/pages?folder=${encodeURIComponent(folder)}`);
  if (!r.ok) { alert('Folder not found: '+folder); return; }
  const data=await r.json(); pages=data.pages;
  if (!pages.length) { alert('No pages found (need matching .json + image)'); return; }
  // Also load all JSON stems (no image requirement) for Excel export range
  fetch(`${API}/api/json-stems?folder=${encodeURIComponent(folder)}`)
    .then(r => r.ok ? r.json() : {stems:[]})
    .then(d => { allJsonStems = d.stems || []; })
    .catch(() => { allJsonStems = []; });
  loadRules();   // row rules → Diagnose dropdown entries
  loadClips();   // document-wide clip index → flag tray
  const sel=document.getElementById('page-select');
  sel.innerHTML=pages.map((p,i)=>`<option value="${i}">${p.stem}</option>`).join('');
  sel.style.display='inline-block';
  document.getElementById('mode-btn').disabled=false;
  document.getElementById('autofill-btn').disabled=false;
  document.getElementById('table-btn').disabled=false;
  document.getElementById('persp-btn').disabled=false;
  document.getElementById('clear-ann-btn').disabled=false;
  document.getElementById('overlap-btn').disabled=false;
  document.getElementById('trim-overlaps-btn').disabled=false;
  document.getElementById('lattice-btn').disabled=false;
  document.getElementById('lattice-sel-btn').disabled=false;
  document.getElementById('ocr-view-btn').disabled=false;
  document.getElementById('smart-correct-btn').disabled=false;
  pageIdx=0; initViewer(); await loadPage(0);
}

async function loadPage(idx) {
  // Preserve edit/review mode across page changes (don't drop the user out of
  // edit mode when they navigate); selection & drag still reset per page.
  const wasEdit = editMode;
  pageIdx=idx; selIdx=-1; selSet.clear(); editMode=wasEdit; dragState=null; flaggedOverlaps=new Set();
  document.getElementById('prev-btn').disabled=idx===0;
  document.getElementById('next-btn').disabled=idx===pages.length-1;
  document.getElementById('page-num-input').value = idx + 1;
  document.getElementById('page-total').textContent = `/ ${pages.length}`;
  document.getElementById('page-select').value=idx;
  document.getElementById('mode-btn').textContent = editMode ? 'Edit mode' : 'Review mode';
  document.getElementById('mode-btn').classList.toggle('active', editMode);
  updatePanel();
  await reloadPageData();
  viewer.open({type:'image',url:`${API}/api/image?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(pages[idx].stem)}${loadPage._bust ? '&t='+Date.now() : ''}`});
  loadPage._bust = false;
  if (ocrViewActive) { _shadowResetTransform(); loadShadowPreview(); }
  buildLegend();
  // Show grid button if this page already has lattice data
  const hasLattice = pageData?.shapes?.some(s => s.super_row != null);
  const gridBtn = document.getElementById('lattice-grid-btn');
  const colSepBtn = document.getElementById('lattice-col-sep-btn');
  const rowSepBtn = document.getElementById('lattice-row-sep-btn');
  const delSepBtn = document.getElementById('lattice-del-sep-btn');
  const splitBtn  = document.getElementById('lattice-split-btn');
  const delLatBtn = document.getElementById('lattice-del-btn');
  if (hasLattice) {
    gridBtn.style.display=''; gridBtn.disabled=false;
    colSepBtn.style.display=''; colSepBtn.disabled=false;
    rowSepBtn.style.display=''; rowSepBtn.disabled=false;
    delSepBtn.style.display=''; delSepBtn.disabled=false;
    if (splitBtn) { splitBtn.style.display=''; splitBtn.disabled=false; }
    if (delLatBtn) { delLatBtn.style.display=''; delLatBtn.disabled=false; }
  } else {
    gridBtn.style.display='none'; latticeVisible=false;
    colSepBtn.style.display='none'; rowSepBtn.style.display='none'; delSepBtn.style.display='none';
    if (splitBtn) splitBtn.style.display='none';
    if (delLatBtn) delLatBtn.style.display='none';
    latticeSepMode=null; latticeSplitMode=false;
  }
  document.getElementById('row-fill-btn').disabled = false;
  document.getElementById('col-fill-btn').disabled = false;
  document.getElementById('snap-btn').disabled = false;
  if (gridBtn.style.display!=='none') gridBtn.textContent = latticeVisible ? '📐 Hide Grid' : '📐 Show Grid';
  _clipArmed = null;
  renderClipTray();   // tray contents depend on the current page
}

function goPage(delta) { const n=pageIdx+delta; if(n>=0&&n<pages.length) loadPage(n); }
function goPageByIndex(val) { loadPage(parseInt(val)); }
function goPageByInput() {
  const v = parseInt(document.getElementById('page-num-input').value);
  if (!isNaN(v) && v >= 1 && v <= pages.length) {
    loadPage(v - 1);
  } else {
    document.getElementById('page-num-input').value = pageIdx + 1; // reset to current
  }
}

loadOcrSettings();

const urlParams=new URLSearchParams(window.location.search);
if (urlParams.get('labels')) {
  projectLabels = urlParams.get('labels').split(',').map(s=>s.trim()).filter(Boolean);
}
if (urlParams.get('folder')) {
  document.getElementById('folder-input').value=urlParams.get('folder');
  loadFolder();
}
