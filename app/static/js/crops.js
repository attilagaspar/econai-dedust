// Split classic script (shared global scope, load order matters — see
// knowledge_base/02_architecture.md). Crop round trip ("crops as pages",
// §H8 of knowledge_base/09_hierarchical_layout_compass.md, app/crops.py):
//   • batch op ✂ "Cut <label> boxes into a crop project" (source project):
//     every box of a label on the selected pages becomes an image page of
//     another project, with provenance (source page / shape / bbox / record
//     id / continuation) in the crop page's flags.crop_source;
//   • batch op ⤴ "Write back to source pages" (crop project): the crop
//     page's shapes go back onto the source page at page coordinates,
//     stamped crop_origin so a repeat replaces exactly them;
//   • the origin pill in the topbar on a crop page (deep link to the
//     source page + shape, record id, continuation) and the ⤴ badge on
//     written-back shapes (drawOverlay).
// Reuses: _recCtx / loadRecordsContext (project + labels + projects_root),
// _escHtml, showToast, reloadPageData, drawOverlay, updatePanel, pages,
// folder, API.

function _cropsReEscape(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

// The batch page range narrows both operations; "every page" → no pattern.
function _cropsPatternFor(sorted) {
  if (!Array.isArray(sorted) || !pages?.length || sorted.length >= pages.length) return null;
  const stems = sorted.map(i => pages[i]?.stem).filter(Boolean);
  return stems.length ? '^(?:' + stems.map(_cropsReEscape).join('|') + ')$' : null;
}

function _cropsStatuses(prefix) {
  return [...document.querySelectorAll(`#batch-crops-opts input[data-${prefix}-status]`)]
    .filter(c => c.checked).map(c => c.getAttribute(`data-${prefix}-status`));
}

async function _batchCropsInit(op) {
  if (typeof loadRecordsContext === 'function') await loadRecordsContext();
  const derive = op === 'crops_derive';
  const dOpts = document.getElementById('batch-crops-derive-opts');
  const wOpts = document.getElementById('batch-crops-wb-opts');
  if (dOpts) dOpts.style.display = derive ? 'flex' : 'none';
  if (wOpts) wOpts.style.display = derive ? 'none' : 'flex';
  const info = document.getElementById('batch-crops-info');
  const prog = document.getElementById('batch-progress');
  if (prog) prog.innerHTML = '';
  if (!_recCtx.project) {
    if (info) info.textContent = 'This folder is not a project annotations folder — crop tools are off.';
    return;
  }
  if (info) info.textContent = '';
  if (derive) {
    const sel = document.getElementById('batch-crops-label');
    const labels = [...new Set([...(_recCtx.labels || []), ...projectLabels])];
    const remembered = localStorage.getItem(`dedust.cropLabel.${_recCtx.project}`);
    const pick = remembered && labels.includes(remembered) ? remembered : (_recCtx.record_label || labels[0] || '');
    if (sel) sel.innerHTML = labels.map(l =>
      `<option value="${_escHtml(l)}"${l === pick ? ' selected' : ''}>${_escHtml(l)}</option>`).join('');
    const tgt = document.getElementById('batch-crops-target');
    if (tgt && tgt.dataset.project !== _recCtx.project) {
      tgt.value = localStorage.getItem(`dedust.cropTarget.${_recCtx.project}`) || `${_recCtx.project}_crops`;
      tgt.dataset.project = _recCtx.project;
    }
  } else {
    const isCrop = !!pageData?.flags?.crop_source;
    if (info) info.textContent = isCrop ? '' :
      'The current page carries no crop provenance — only pages with flags.crop_source are written back.';
  }
}

function _cropsStaleHtml(stale, what) {
  if (!stale?.length) return '';
  const rows = stale.slice(0, 40).map(s =>
    `<div style="color:${s.protected ? '#fbbf24' : '#f87171'};">⚠ ${_escHtml(s.crop_stem)} — ${_escHtml(s.reason)}` +
    (s.protected ? ' (protected: human work / reviewed)' : '') + (s.action ? ` → ${_escHtml(s.action)}` : '') + '</div>').join('');
  return `<div style="margin-top:4px;font-size:10px;">${stale.length} stale ${what}:</div>${rows}` +
    (stale.length > 40 ? `<div style="font-size:10px;color:#888;">… ${stale.length - 40} more</div>` : '');
}

function _deriveSummary(d) {
  return `${d.boxes} "${d.label}" box(es) on ${d.pages_with_boxes}/${d.pages_selected} selected page(s) → ` +
    `${d.created} new, ${d.unchanged} unchanged, ${d.refreshed} provenance refreshed, ${d.replaced} replaced` +
    (d.stale?.length ? `, ${d.stale.length} stale (${d.protected} protected)` : '') +
    (d.children_carried ? `, ${d.children_carried} child shape(s) carried` : '') +
    (d.pages_without_image ? `, ${d.pages_without_image} page(s) without image` : '');
}

async function _postJson(url, body) {
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                              body: JSON.stringify(body)});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
  return d;
}

// ── A. cut boxes into a crop project ─────────────────────────────────────────
async function runDeriveCrops(sorted, {dryRun = false} = {}) {
  if (!_recCtx.project) { showToast('Crop tools need a project annotations folder'); return null; }
  const label  = document.getElementById('batch-crops-label')?.value || '';
  const target = (document.getElementById('batch-crops-target')?.value || '').trim();
  if (!label) { showToast('Pick the box label first'); return null; }
  if (!target) { showToast('Name the crop project'); return null; }
  localStorage.setItem(`dedust.cropLabel.${_recCtx.project}`, label);
  localStorage.setItem(`dedust.cropTarget.${_recCtx.project}`, target);
  const body = {
    label, target,
    statuses: _cropsStatuses('src'),
    pattern: _cropsPatternFor(sorted),
    margin: parseFloat(document.getElementById('batch-crops-margin')?.value) || 12,
    policy: document.getElementById('batch-crops-policy')?.value || 'keep',
    with_children: !!document.getElementById('batch-crops-children')?.checked,
    dry_run: true,
  };
  if (!body.statuses.length) { showToast('Tick at least one source page status'); return null; }
  const prog = document.getElementById('batch-progress');
  const url = `${API}/api/project/${encodeURIComponent(_recCtx.project)}/derive-crops`;
  let d;
  try {
    if (prog) prog.textContent = 'Dry run…';
    d = await _postJson(url, body);
    const head = `${dryRun ? 'Dry run' : 'Preview'}: ${_deriveSummary(d)}` +
      (d.target_created ? ` · project "${target}" would be created` : '');
    if (prog) prog.innerHTML = _escHtml(head) + _cropsStaleHtml(d.stale, 'crop(s)');
    if (dryRun) { showToast(head, 7000); return d; }
    if (!d.created && !d.replaced && !d.refreshed && !d.source_pages_stamped) {
      showToast('Nothing to do: every crop is up to date', 5000); return d;
    }
    const msg = `Cut "${label}" boxes into project "${target}"?\n\n${_deriveSummary(d)}` +
      (d.target_created ? `\n\nThe project "${target}" will be created (labels copied from this project).` : '') +
      (body.with_children ? '\n\nChild shapes travel along; their source copies are stamped crop_origin so the first write-back replaces them.' : '') +
      '\n\nNothing is deleted; existing crop pages are only replaced under the chosen policy.';
    if (!(await _batchConfirmInline(msg))) return null;
    if (body.with_children) {
      // stamping touches source pages → one batch-undo generation for them
      const stems = sorted.map(i => pages[i]?.stem).filter(Boolean);
      if (typeof _batchTakeSnapshot === 'function') await _batchTakeSnapshot(stems, 'derive_crops (crop_origin stamps)');
    }
    if (prog) prog.textContent = 'Cutting crops…';
    d = await _postJson(url, {...body, dry_run: false});
    const done = `✓ ${_deriveSummary(d)}` + (d.target_created ? ` · project "${target}" created` : '');
    if (prog) prog.innerHTML = _escHtml(done) + _cropsStaleHtml(d.stale, 'crop(s)') +
      `<div style="margin-top:4px;"><a href="index.html?folder=${encodeURIComponent(d.target_folder)}" target="_blank" style="color:#93c5fd;">Open the crop project →</a></div>`;
    showToast(done, 8000);
    if (body.with_children && d.source_pages_stamped && pageData) { await reloadPageData(); drawOverlay(); updatePanel(); }
    return d;
  } catch (e) {
    if (prog) prog.textContent = 'Error: ' + (e?.message || e);
    showToast('Cut crops error: ' + (e?.message || e), 7000);
    return null;
  }
}

// ── B. write crop pages back onto their source pages ─────────────────────────
function _writeBackSummary(d) {
  return `${d.crop_pages_selected} crop page(s) → ${d.source_pages} source page(s): ` +
    `${d.written} ${d.dry_run ? 'would change' : 'written'}, ${d.unchanged} unchanged, ` +
    `${d.shapes_written} shape(s) placed, ${d.shapes_replaced} earlier write-back shape(s) replaced` +
    (d.stale?.length ? `, ${d.stale.length} stale (skipped)` : '') +
    (d.missing_source?.length ? `, ${d.missing_source.length} source page(s) missing` : '') +
    (d.no_provenance ? `, ${d.no_provenance} selected page(s) without provenance` : '');
}

async function runWriteBack(sorted, {dryRun = false} = {}) {
  if (!_recCtx.project) { showToast('Crop tools need a project annotations folder'); return null; }
  const body = {statuses: _cropsStatuses('wb'), pattern: _cropsPatternFor(sorted), dry_run: true};
  if (!body.statuses.length) { showToast('Tick at least one crop page status'); return null; }
  const prog = document.getElementById('batch-progress');
  const url = `${API}/api/project/${encodeURIComponent(_recCtx.project)}/write-back`;
  let d;
  try {
    if (prog) prog.textContent = 'Dry run…';
    d = await _postJson(url, body);
    const head = `${dryRun ? 'Dry run' : 'Preview'}: ${_writeBackSummary(d)}`;
    const pagesHtml = (d.pages || []).filter(p => p.changed).slice(0, 40).map(p =>
      `<div style="font-size:10px;color:#aaa;">${_escHtml(p.source_project)}/${_escHtml(p.source_stem)}: ` +
      `${p.n_before} → ${p.n_after} shape(s) (${p.crops.map(c => _escHtml(c.crop_stem) + (c.status === 'stale' ? ' ⚠stale' : '')).join(', ')})</div>`).join('');
    if (prog) prog.innerHTML = _escHtml(head) + pagesHtml + _cropsStaleHtml(d.stale, 'crop page(s)');
    if (dryRun) { showToast(head, 7000); return d; }
    if (!d.written) { showToast('Nothing to write: the source pages already hold this content', 5000); return d; }
    const msg = `Write ${d.crop_pages_selected} crop page(s) back onto ${d.written} source page(s)?\n\n${_writeBackSummary(d)}` +
      '\n\nOnly shapes written from these crops earlier are replaced; the parent boxes and everything else on the source pages stay as they are. ' +
      'A batch-undo snapshot of the affected source pages is taken first (restore from the batch dialog of the SOURCE project).';
    if (!(await _batchConfirmInline(msg))) return null;
    // one undo generation per affected source project
    const byFolder = {};
    (d.pages || []).filter(p => p.changed).forEach(p => (byFolder[p.source_folder] ??= []).push(p.source_stem));
    for (const [f, stems] of Object.entries(byFolder)) {
      try {
        await fetch(`${API}/api/batch_snapshot?folder=${encodeURIComponent(f)}`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({stems, op: `write_back from ${_recCtx.project}`})});
      } catch (e) { /* the snapshot is a convenience; the write-back still runs */ }
    }
    if (prog) prog.textContent = 'Writing back…';
    d = await _postJson(url, {...body, dry_run: false});
    const done = `✓ ${_writeBackSummary(d)}`;
    if (prog) prog.innerHTML = _escHtml(done) + _cropsStaleHtml(d.stale, 'crop page(s)');
    showToast(done, 8000);
    return d;
  } catch (e) {
    if (prog) prog.textContent = 'Error: ' + (e?.message || e);
    showToast('Write-back error: ' + (e?.message || e), 7000);
    return null;
  }
}

// ── Origin pill (crop page → its source page) ────────────────────────────────
function cropSourceFolder(project) {
  const root = _recCtx?.projects_root;
  if (!root || !project) return null;
  const sep = root.includes('\\') ? '\\' : '/';
  return `${root}${sep}${project}${sep}annotations`;
}

function updateCropOriginPill() {
  const pill = document.getElementById('crop-origin-pill');
  if (!pill) return;
  const src = pageData?.flags?.crop_source;
  if (!src || !src.project) { pill.style.display = 'none'; pill.innerHTML = ''; return; }
  const gid = src.group_id != null ? ` · <span style="color:${typeof recordColor === 'function' ? recordColor(src.group_id) : '#f0c040'};font-weight:700;">r${_escHtml(src.group_id)}</span>` : '';
  const cont = src.continuation ? ' · <span style="color:#f0c040;" title="This box continues the previous one in reading order (fragment of one record)">⛓ continuation</span>' : '';
  const f = cropSourceFolder(src.project);
  const href = f ? `index.html?folder=${encodeURIComponent(f)}&stem=${encodeURIComponent(src.stem)}&idx=${encodeURIComponent(src.shape_idx ?? -1)}` : null;
  const where = `${_escHtml(src.project)}/${_escHtml(src.stem)} #${_escHtml(src.shape_idx ?? '?')}`;
  pill.innerHTML = `⤴ crop of ${href ? `<a href="${href}" target="_blank" style="color:#93c5fd;" title="Open the source page with this box selected (new tab)">${where}</a>` : where}` +
    ` <span style="color:#8a94a6;">(${_escHtml(src.label || '')}${src.source_status ? ', ' + _escHtml(src.source_status) : ''})</span>` + gid + cont;
  pill.title = `This page is a crop of box #${src.shape_idx} on ${src.project}/${src.stem}` +
    ` (bbox ${(src.bbox || []).map(v => Math.round(v)).join(',')}, margin ${src.margin} px).` +
    ' Verify it and run the batch op "⤴ Write back to source pages" to put its content onto the source page.';
  pill.style.display = '';
}

// ── ⤴ badge on a written-back shape (called from drawOverlay) ────────────────
function cropOriginBadge(shape, tl, br) {
  const o = shape?.crop_origin;
  if (!o || !o.project) return null;
  const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  t.setAttribute('x', br.x - 4);
  t.setAttribute('y', tl.y + 13);
  t.setAttribute('text-anchor', 'end');
  t.setAttribute('fill', '#7dd3fc');
  t.setAttribute('font-size', '11');
  t.setAttribute('font-weight', '700');
  t.setAttribute('paint-order', 'stroke');
  t.setAttribute('stroke', '#0d1b35');
  t.setAttribute('stroke-width', '3');
  t.textContent = '⤴';
  const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
  title.textContent = `Written back from crop ${o.project}/${o.stem}` +
    (shape.group_id != null ? ` · record ${shape.group_id}` : '') + ' — a repeat write-back of that crop replaces this shape.';
  t.appendChild(title);
  t.style.pointerEvents = 'all';
  t.style.cursor = 'help';
  return t;
}
