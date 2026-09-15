// Split from index.html conventions — classic scripts share the global scope;
// load order in index.html is load-bearing (this file needs lattice.js's
// _median / _groupByOverlap). See knowledge_base/02_architecture.md.
//
// ── Newspaper flow reconstruction ─────────────────────────────────────────────
// Rebuilds a newspaper page's reading structure from noisy region detections:
// find the column skeleton, cut horizontal bands at page-wide separators (and
// optionally at whitespace aligned across all columns), then within each
// band × column merge same-type text boxes into single elements and split them
// wherever a breaker (title / image / table) interrupts the column. Elements
// get a reading order (band → column → y) and consecutive elements between
// titles share a group_id (= one article). Deterministic post-processing in
// the spirit of lattice correction — the model finds ink, geometry builds
// structure.
//
// Roles (per label, remembered in localStorage 'newsflowRoles'):
//   text      — mergeable article flow (cikkszoveg)
//   title     — breaks the column AND starts a new article group (cim1/2/3sav)
//   breaker   — breaks the column, stays in the flow (kep, tablazat_keplet)
//   furniture — page chrome: creates a band cut, excluded from the flow
//               (fejlec, folyoiratcim)
//   ignore    — not part of the reconstruction at all
//
// The reconstruction REPLACES the original text boxes with the merged
// elements (undo via pushUndo / batch snapshot) and collapses duplicate
// overlapping breakers into one box. Run it BEFORE OCR: merged elements
// carry no text from their sources.

const NEWSFLOW_ROLES = ['text', 'title', 'breaker', 'furniture', 'ignore'];

// Sensible defaults for known label names; anything unknown starts as 'ignore'.
function _newsflowDefaultRole(label) {
  const l = (label || '').toLowerCase();
  if (/szoveg|text|cikk/.test(l)) return 'text';
  if (/^cim|title|cím/.test(l))   return 'title';
  if (/kep|kép|tabla|image|figure/.test(l)) return 'breaker';
  if (/fejlec|fejléc|folyoirat|header|masthead/.test(l)) return 'furniture';
  return 'ignore';
}

function _newsflowRoles() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('newsflowRoles') || '{}'); } catch (e) {}
  const labels = [...new Set((pageData?.shapes || []).map(s => s.label).filter(Boolean))];
  const roles = {};
  labels.forEach(l => { roles[l] = saved[l] || _newsflowDefaultRole(l); });
  return roles;
}

// ── geometry helpers ──────────────────────────────────────────────────────────
function _nfRect(s) {
  const xs = s.points.map(p => p[0]), ys = s.points.map(p => p[1]);
  return { x1: Math.min(...xs), y1: Math.min(...ys),
           x2: Math.max(...xs), y2: Math.max(...ys) };
}

// Merge 1-D intervals [ [a,b], ... ] → sorted disjoint intervals.
function _nfMergeIntervals(iv, slack = 0) {
  const s = iv.filter(i => i[1] > i[0]).sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const [a, b] of s) {
    const last = out[out.length - 1];
    if (last && a <= last[1] + slack) last[1] = Math.max(last[1], b);
    else out.push([a, b]);
  }
  return out;
}

// Subtract interval list `cuts` from interval list `iv` (both disjoint sorted).
function _nfSubtractIntervals(iv, cuts) {
  let cur = iv.map(i => [...i]);
  for (const [c1, c2] of cuts) {
    const next = [];
    for (const [a, b] of cur) {
      if (c2 <= a || c1 >= b) { next.push([a, b]); continue; }
      if (c1 > a) next.push([a, c1]);
      if (c2 < b) next.push([c2, b]);
    }
    cur = next;
  }
  return cur.filter(i => i[1] - i[0] > 1);
}

// ── core ──────────────────────────────────────────────────────────────────────
// opts: { roles?, whitespaceCuts?: bool, minGap?: px }
// Returns { columns, bands, elements, articles } and MUTATES pageData.shapes.
function _newsflowReconstruct(opts = {}) {
  const roles = opts.roles || _newsflowRoles();
  const whitespaceCuts = opts.whitespaceCuts !== false;
  const minGap = opts.minGap || 40;

  const shapes = pageData.shapes;
  const roleOf = s => roles[s.label] || 'ignore';
  const valid  = s => s.points?.length >= 2;

  const textShapes    = shapes.filter(s => valid(s) && roleOf(s) === 'text');
  const breakerShapes = shapes.filter(s => valid(s) && (roleOf(s) === 'title' || roleOf(s) === 'breaker'));
  const furniture     = shapes.filter(s => valid(s) && roleOf(s) === 'furniture');
  if (!textShapes.length) return { columns: [], bands: [], elements: [], articles: 0 };

  // ── B. column skeleton from the text boxes ─────────────────────────────────
  const left  = s => _nfRect(s).x1, right = s => _nfRect(s).x2;
  const colGroups = _groupByOverlap(textShapes, left, right, 0.5)
                      .sort((a, b) => a.lo - b.lo);
  const columns = colGroups.map(g => ({ x1: g.lo, x2: g.hi }));
  // stitch gutters: column boundaries meet halfway so clipping is stable
  for (let i = 0; i < columns.length - 1; i++) {
    const mid = (columns[i].x2 + columns[i + 1].x1) / 2;
    columns[i].x2 = Math.min(columns[i].x2, mid);
    columns[i + 1].x1 = Math.max(columns[i + 1].x1, mid);
  }

  const colW = c => columns[c].x2 - columns[c].x1;
  const xCover = (r, c) =>
    Math.max(0, Math.min(r.x2, columns[c].x2) - Math.max(r.x1, columns[c].x1));
  // Which columns a breaker affects: it must cover ≥60% of the column's width,
  // OR be ≥90% contained in it (centered titles are narrower than their column
  // yet must split it; a masthead merely grazing a side column must NOT).
  const coveredCols = r => columns.map((_, c) => c).filter(c =>
    xCover(r, c) >= 0.6 * colW(c) || xCover(r, c) >= 0.9 * (r.x2 - r.x1));
  // which single column a TEXT rect belongs to most (for split-at-gutter)
  const textCols = r => {
    const cs = columns.map((_, c) => c).filter(c => xCover(r, c) >= 0.4 * colW(c));
    return cs.length ? cs : [columns.map((_, c) => c)
      .reduce((b, c) => xCover(r, c) > xCover(r, b) ? c : b, 0)];
  };

  const pageTop = Math.min(...[...textShapes, ...breakerShapes, ...furniture].map(s => _nfRect(s).y1));
  const pageBot = Math.max(...[...textShapes, ...breakerShapes, ...furniture].map(s => _nfRect(s).y2));

  // ── A+C. band cuts ─────────────────────────────────────────────────────────
  // (1) furniture always cuts; (2) a breaker covering EVERY column cuts;
  // (3) optional: whitespace gaps aligned across all columns. Gaps are
  //     measured against TEXT coverage only, and a narrow common gap still
  //     counts when a title/breaker overlaps it — a page-wide text gap with a
  //     title in it is what a fold (e.g. the tárca line) looks like when the
  //     fold rule itself is not detected as a shape. (Real case: Népszava
  //     page folds leave only a ~25px common gap because detection is tight.)
  const cutIntervals = [];
  furniture.forEach(s => { const r = _nfRect(s); cutIntervals.push([r.y1, r.y2]); });
  breakerShapes.forEach(s => {
    const r = _nfRect(s);
    if (coveredCols(r).length === columns.length) cutIntervals.push([r.y1, r.y2]);
  });
  if (whitespaceCuts && columns.length > 1) {
    // per-column occupied y-intervals (text only — breakers sit inside folds)
    const occ = columns.map((_, c) => _nfMergeIntervals(
      textShapes
        .filter(s => xCover(_nfRect(s), c) >= 0.4 * colW(c))
        .map(s => { const r = _nfRect(s); return [r.y1, r.y2]; })));
    // gaps per column, intersected across all columns
    let common = [[pageTop, pageBot]];
    occ.forEach(iv => { common = _nfSubtractIntervals(common, iv); });
    const brkOverlaps = g => breakerShapes.some(s => {
      const r = _nfRect(s);
      return Math.min(r.y2, g[1]) - Math.max(r.y1, g[0]) > 0;
    });
    common.filter(g => (g[1] - g[0] >= minGap) ||
                       (g[1] - g[0] >= 15 && brkOverlaps(g)))
          .forEach(g => cutIntervals.push([g[0], g[1]]));
  }
  const cuts = _nfMergeIntervals(cutIntervals);

  // bands = page y-range minus cut intervals
  const bands = _nfSubtractIntervals([[pageTop, pageBot]], cuts)
                  .map(([y1, y2], i) => ({ y1, y2 }));

  // ── collapse duplicate breakers (same label, overlapping) ──────────────────
  // Merged clusters can newly overlap each other (A↔B↔C chains where A and C
  // never touch directly), so repeat the pass until nothing merges.
  let breakerMerged = breakerShapes.map(s => ({ label: s.label, rect: _nfRect(s), members: [s] }));
  for (let changed = true; changed;) {
    changed = false;
    const out = [];
    for (const m of breakerMerged) {
      const hit = out.find(o => o.label === m.label &&
        Math.max(0, Math.min(m.rect.x2, o.rect.x2) - Math.max(m.rect.x1, o.rect.x1)) > 0 &&
        Math.max(0, Math.min(m.rect.y2, o.rect.y2) - Math.max(m.rect.y1, o.rect.y1)) > 0);
      if (hit) {
        hit.rect = { x1: Math.min(hit.rect.x1, m.rect.x1), y1: Math.min(hit.rect.y1, m.rect.y1),
                     x2: Math.max(hit.rect.x2, m.rect.x2), y2: Math.max(hit.rect.y2, m.rect.y2) };
        hit.members.push(...m.members);
        changed = true;
      } else out.push(m);
    }
    breakerMerged = out;
  }

  // ── D. per band × column: merge text runs, split at breakers ───────────────
  // text coverage per column (tight y-intervals of the actual boxes)
  const elements = [];   // {kind:'text'|'breaker', label, rect, band, col, span, shape?}
  for (let b = 0; b < bands.length; b++) {
    const band = bands[b];
    const perCol = columns.map((_, c) => {
      const clip = iv => _nfSubtractIntervals(
        iv.map(i => [Math.max(i[0], band.y1), Math.min(i[1], band.y2)]), []);
      const cover = _nfMergeIntervals(
        textShapes.filter(s => textCols(_nfRect(s)).includes(c))
                  .map(s => { const r = _nfRect(s); return [r.y1, r.y2]; }));
      const brk = _nfMergeIntervals(
        breakerMerged.filter(m => coveredCols(m.rect).includes(c))
                     .map(m => [m.rect.y1, m.rect.y2]));
      const runs = clip(_nfSubtractIntervals(cover, brk))
        .filter(([y1, y2]) => y2 - y1 >= 15);   // drop sub-line slivers
      return { runs, brk };
    });

    // Optional (default on): extend each column's first/last text run to the
    // band's common extent (detection often cuts column bottoms short — this
    // recovers the under-covered lines). Titles/breakers count toward the
    // common extent, so a title north of the text columns pulls them up to
    // its latitude. Extension never crosses a breaker in its own column and
    // never invents elements in empty columns.
    if (opts.extendCols !== false && perCol.some(pc => pc.runs.length)) {
      const tops = [], bots = [];
      perCol.forEach(pc => {
        if (pc.runs.length) {
          tops.push(pc.runs[0][0]);
          bots.push(pc.runs[pc.runs.length - 1][1]);
        }
        pc.brk.forEach(([b1, b2]) => {
          if (b2 > band.y1 && b1 < band.y2) {
            tops.push(Math.max(b1, band.y1));
            bots.push(Math.min(b2, band.y2));
          }
        });
      });
      const cTop = Math.min(...tops), cBot = Math.max(...bots);
      perCol.forEach(({ runs, brk }) => {
        if (!runs.length) return;
        let topLim = band.y1, botLim = band.y2;
        brk.forEach(([b1, b2]) => {
          if (b2 <= runs[0][0]) topLim = Math.max(topLim, b2);
          if (b1 >= runs[runs.length - 1][1]) botLim = Math.min(botLim, b1);
        });
        runs[0][0] = Math.max(cTop, topLim);
        runs[runs.length - 1][1] = Math.min(cBot, botLim);
      });
    }

    perCol.forEach(({ runs }, c) => runs.forEach(([y1, y2]) => elements.push({
      kind: 'text', label: null, band: b, col: c, span: 1,
      rect: { x1: columns[c].x1, y1, x2: columns[c].x2, y2 },
    })));
  }
  // breaker elements: one per merged breaker, anchored at its leftmost column
  breakerMerged.forEach(m => {
    const cc = coveredCols(m.rect);
    const col = cc.length ? cc[0] : textCols(m.rect)[0];
    let band = bands.findIndex(bd =>
      Math.min(m.rect.y2, bd.y2) - Math.max(m.rect.y1, bd.y1) > 0);
    if (band < 0) band = -1;   // breaker fully inside a cut (e.g. page-wide title)
    elements.push({ kind: 'breaker', label: m.label, band, col, span: Math.max(1, cc.length),
                    rect: m.rect, members: m.members });
  });

  // ── E. reading order + article grouping ────────────────────────────────────
  // order: bands top→bottom; page-wide breakers (band -1) slot by y between
  // bands; within band: columns left→right; within column: top→bottom.
  const bandOf = e => e.band >= 0 ? e.band
    : bands.findIndex(bd => bd.y1 >= e.rect.y2) >= 0
      ? bands.findIndex(bd => bd.y1 >= e.rect.y2) - 0.5 : bands.length - 0.5;
  elements.sort((a, b) =>
    bandOf(a) - bandOf(b) || a.col - b.col || a.rect.y1 - b.rect.y1);

  const textLabel = Object.keys(roles).find(l => roles[l] === 'text') || 'cikkszoveg';
  let order = 0, group = 0;
  const newShapes = [];
  for (const e of elements) {
    if (e.kind === 'breaker' && roles[e.label] === 'title') group++;
    const gid = group;   // 0 = carryover text before the page's first title
    if (e.kind === 'text') {
      newShapes.push({
        label: textLabel, shape_type: 'rectangle', flags: {}, group_id: gid,
        points: [[Math.round(e.rect.x1), Math.round(e.rect.y1)],
                 [Math.round(e.rect.x2), Math.round(e.rect.y2)]],
        flow_order: order, flow_band: Math.max(0, Math.floor(bandOf(e))),
        flow_column: e.col, flow_span: 1,
      });
    } else {
      // survivor = highest-score member (or first); resize to the merged rect
      const surv = e.members.reduce((b, s) =>
        (s.score ?? 0) >= (b.score ?? 0) ? s : b, e.members[0]);
      surv.points = [[Math.round(e.rect.x1), Math.round(e.rect.y1)],
                     [Math.round(e.rect.x2), Math.round(e.rect.y2)]];
      surv.group_id = gid; surv.flow_order = order;
      surv.flow_band = Math.max(0, Math.floor(bandOf(e)));
      surv.flow_column = e.col; surv.flow_span = e.span;
      e.survivor = surv;
    }
    order++;
  }

  // ── mutate pageData: drop originals, keep survivors + merged text ──────────
  const survivors = new Set(elements.filter(e => e.survivor).map(e => e.survivor));
  pageData.shapes = shapes.filter(s => {
    if (!valid(s)) return true;
    const role = roleOf(s);
    if (role === 'text') return false;                       // replaced by merges
    if (role === 'title' || role === 'breaker') return survivors.has(s);
    return true;                                             // furniture / ignore
  }).concat(newShapes);

  return { columns, bands, elements, articles: group };
}

// ── UI: workspace modal + runner ─────────────────────────────────────────────
function openNewsflowModal() {
  if (!pageData?.shapes?.length) { showToast('Load a page first'); return; }
  const roles = _newsflowRoles();
  const cont = document.getElementById('newsflow-role-rows');
  cont.innerHTML = Object.keys(roles).sort().map(l =>
    `<label style="display:flex;align-items:center;gap:8px;color:#ddd;font-size:13px;">
       <span style="min-width:140px;">${l}</span>
       <select data-nf-label="${l}" style="background:#0d1b35;border:1px solid #0f3460;border-radius:3px;color:#ccc;font-size:12px;padding:2px 6px;">` +
    NEWSFLOW_ROLES.map(r => `<option value="${r}" ${roles[l] === r ? 'selected' : ''}>${r}</option>`).join('') +
    `</select></label>`).join('');
  document.getElementById('newsflow-ws-cuts').checked =
    localStorage.getItem('newsflowWsCuts') !== '0';
  document.getElementById('newsflow-min-gap').value =
    localStorage.getItem('newsflowMinGap') || '40';
  document.getElementById('newsflow-extend-cols').checked =
    localStorage.getItem('newsflowExtendCols') !== '0';   // default ON
  document.getElementById('newsflow-modal').style.display = 'flex';
}

function closeNewsflowModal() {
  document.getElementById('newsflow-modal').style.display = 'none';
}

function _newsflowReadModalOpts() {
  const roles = {};
  try { Object.assign(roles, JSON.parse(localStorage.getItem('newsflowRoles') || '{}')); } catch (e) {}
  document.querySelectorAll('#newsflow-role-rows select[data-nf-label]').forEach(sel => {
    roles[sel.dataset.nfLabel] = sel.value;
  });
  const whitespaceCuts = document.getElementById('newsflow-ws-cuts').checked;
  const minGap = parseInt(document.getElementById('newsflow-min-gap').value) || 40;
  const extendCols = document.getElementById('newsflow-extend-cols').checked;
  localStorage.setItem('newsflowRoles', JSON.stringify(roles));
  localStorage.setItem('newsflowWsCuts', whitespaceCuts ? '1' : '0');
  localStorage.setItem('newsflowMinGap', String(minGap));
  localStorage.setItem('newsflowExtendCols', extendCols ? '1' : '0');
  return { roles, whitespaceCuts, minGap, extendCols };
}

async function runNewsflow() {
  const opts = _newsflowReadModalOpts();
  closeNewsflowModal();
  pushUndo();
  const res = _newsflowReconstruct(opts);
  await replaceAllShapes();
  drawOverlay(); updatePanel();
  const n = pageData.shapes.filter(s => s.flow_order != null).length;
  showToast(`Flow: ${n} element(s) in ${res.columns.length} column(s), `
            + `${res.bands.length} band(s), ${res.articles} article(s)`);
}
