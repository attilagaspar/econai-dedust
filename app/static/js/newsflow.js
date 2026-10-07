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

  // Furniture (page header / masthead) spans the full page by definition —
  // widen its boxes to the columns' joint extent; the y range stays detected.
  if (columns.length) {
    const fullL = Math.round(columns[0].x1);
    const fullR = Math.round(columns[columns.length - 1].x2);
    furniture.forEach(s => {
      const r = _nfRect(s);
      s.points = [[fullL, Math.round(r.y1)], [fullR, Math.round(r.y2)]];
    });
  }

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
  // Furniture detections (fejléc especially) are often much too tall — text
  // and titles ALWAYS take precedence: a furniture cut only removes the parts
  // of its y-interval where no text/title/breaker coverage exists, so an
  // oversized fejléc can never eat the first lines of the columns.
  const contentIv = _nfMergeIntervals(
    [...textShapes, ...breakerShapes]
      .map(s => { const r = _nfRect(s); return [r.y1, r.y2]; }));
  furniture.forEach(s => {
    const r = _nfRect(s);
    _nfSubtractIntervals([[r.y1, r.y2]], contentIv)
      .filter(iv => iv[1] - iv[0] >= 8)
      .forEach(iv => cutIntervals.push(iv));
  });
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
  // Horror vacui inside a continuous column: a band × column is divided into
  // free segments by the breakers; every free segment that contains ANY text
  // coverage becomes exactly ONE element, and where the segment is bounded by
  // a breaker the element extends flush to it. So text-gap-text (no breaker
  // between) merges into one element, and in a text-title-text flow the text
  // touches the title on both sides. At band edges (fold, page top/bottom) the
  // detected extent is kept — the extend-columns pass below aligns those.
  const elements = [];   // {kind:'text'|'breaker', label, rect, band, col, span, shape?}
  for (let b = 0; b < bands.length; b++) {
    const band = bands[b];
    const perCol = columns.map((_, c) => {
      const cover = _nfMergeIntervals(
        textShapes.filter(s => textCols(_nfRect(s)).includes(c))
                  .map(s => { const r = _nfRect(s); return [r.y1, r.y2]; }));
      const brk = _nfMergeIntervals(
        breakerMerged.filter(m => coveredCols(m.rect).includes(c))
                     .map(m => [m.rect.y1, m.rect.y2]));
      const runs = [];
      _nfSubtractIntervals([[band.y1, band.y2]], brk).forEach(([s1, s2]) => {
        const inside = cover
          .map(i => [Math.max(i[0], s1), Math.min(i[1], s2)])
          .filter(i => i[1] - i[0] > 1);
        if (!inside.length) return;                      // never invent elements
        const covTop = inside[0][0], covBot = inside[inside.length - 1][1];
        if (covBot - covTop < 15) return;                // drop sub-line slivers
        runs.push([s1 > band.y1 + 1 ? s1 : covTop,       // flush to breaker above
                   s2 < band.y2 - 1 ? s2 : covBot]);     // flush to breaker below
      });
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

  // Label of the merged text elements: user override, else the first label
  // mapped to the text role (the flow is used on non-newspaper sources too —
  // nothing should coerce a newspaper-specific label onto them).
  const textLabel = (opts.outputLabel || '').trim()
    || Object.keys(roles).find(l => roles[l] === 'text') || 'cikkszoveg';
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
  document.getElementById('newsflow-out-label').value =
    localStorage.getItem('newsflowOutLabel') || '';
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
  const outputLabel = document.getElementById('newsflow-out-label').value.trim();
  localStorage.setItem('newsflowRoles', JSON.stringify(roles));
  localStorage.setItem('newsflowWsCuts', whitespaceCuts ? '1' : '0');
  localStorage.setItem('newsflowMinGap', String(minGap));
  localStorage.setItem('newsflowExtendCols', extendCols ? '1' : '0');
  localStorage.setItem('newsflowOutLabel', outputLabel);
  return { roles, whitespaceCuts, minGap, extendCols, outputLabel };
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
  _nfShowGridBtns(true);
}

// ── Flow grid (view + edit) ───────────────────────────────────────────────────
// A lattice-style grid for flow-reconstructed pages, with one structural
// difference: horizontal boundaries are COLUMN-LOCAL (no shared rows, no row
// numbering — reading order lives in flow_order). Everything is DERIVED live
// from the stamped shapes; nothing new is persisted.
//   view : column gutters (vertical), band cuts (dashed, full width),
//          per-column shared edges between consecutive elements
//   edit : drag a boundary (moves both neighbours' shared edge), drag a
//          gutter (moves both columns' shared edge), ✂ split mode (click
//          inside an element to cut it in two), Alt+click a boundary to
//          merge the two elements. Reading order renumbers automatically
//          after split/merge (same band → column → y rule as the
//          reconstruction); article numbers are preserved.

let flowGridVisible = false;
let flowSplitMode   = false;

function _nfFlowItems() {
  return (pageData?.shapes || [])
    .map((s, i) => ({ s, i }))
    .filter(x => x.s.flow_order != null && (x.s.points?.length || 0) >= 2);
}

// Shared edges between vertically consecutive elements of the same column.
// Only flush pairs (≤3px apart) form a draggable boundary — stacked titles
// with a real printed margin keep their own edges.
function _nfGridBoundaries() {
  const groups = {};
  _nfFlowItems().forEach(x => {
    const key = `${x.s.flow_band || 0}|${x.s.flow_column || 0}`;
    (groups[key] ??= []).push(x);
  });
  const bounds = [];
  Object.values(groups).forEach(list => {
    list.sort((a, b) => _nfRect(a.s).y1 - _nfRect(b.s).y1);
    for (let k = 0; k + 1 < list.length; k++) {
      const a = _nfRect(list[k].s), b = _nfRect(list[k + 1].s);
      if (Math.abs(a.y2 - b.y1) <= 3) {
        bounds.push({ y: (a.y2 + b.y1) / 2,
                      x1: Math.min(a.x1, b.x1), x2: Math.max(a.x2, b.x2),
                      upper: list[k].i, lower: list[k + 1].i });
      }
    }
  });
  return bounds;
}

// Column extents (single-span elements only — wide breakers don't define
// column edges) and the gutters between adjacent columns.
function _nfGridColumns() {
  const cols = {};
  _nfFlowItems().forEach(x => {
    if ((x.s.flow_span || 1) > 1) return;
    const c = x.s.flow_column || 0;
    const r = _nfRect(x.s);
    const e = cols[c] ??= { x1: Infinity, x2: -Infinity,
                            y1: Infinity, y2: -Infinity };
    e.x1 = Math.min(e.x1, r.x1); e.x2 = Math.max(e.x2, r.x2);
    e.y1 = Math.min(e.y1, r.y1); e.y2 = Math.max(e.y2, r.y2);
  });
  return cols;
}

// Band extents, for the dashed full-width cut lines between bands.
function _nfGridBands() {
  const bands = {};
  _nfFlowItems().forEach(x => {
    const b = x.s.flow_band || 0;
    const r = _nfRect(x.s);
    const e = bands[b] ??= { y1: Infinity, y2: -Infinity,
                             x1: Infinity, x2: -Infinity };
    e.y1 = Math.min(e.y1, r.y1); e.y2 = Math.max(e.y2, r.y2);
    e.x1 = Math.min(e.x1, r.x1); e.x2 = Math.max(e.x2, r.x2);
  });
  return bands;
}

// Re-stamp flow_order from current geometry (band → column → top y), the same
// ordering rule the reconstruction uses. Article numbers are left untouched.
function _nfRenumber() {
  const items = _nfFlowItems();
  items.sort((a, b) =>
    (a.s.flow_band || 0) - (b.s.flow_band || 0)
    || (a.s.flow_column || 0) - (b.s.flow_column || 0)
    || _nfRect(a.s).y1 - _nfRect(b.s).y1);
  items.forEach((x, k) => { x.s.flow_order = k; });
}

function _nfSetRect(shape, r) {
  shape.points = [[Math.round(r.x1), Math.round(r.y1)],
                  [Math.round(r.x2), Math.round(r.y2)]];
}

function _nfShowGridBtns(hasFlow) {
  ['newsflow-grid-btn', 'newsflow-split-btn'].forEach(id => {
    const b = document.getElementById(id);
    if (b) { b.style.display = hasFlow ? '' : 'none'; b.disabled = !hasFlow; }
  });
  if (!hasFlow) { flowGridVisible = false; flowSplitMode = false; }
  _nfUpdateGridBtns();
}

function toggleFlowGrid() {
  flowGridVisible = !flowGridVisible;
  if (!flowGridVisible) flowSplitMode = false;
  _nfUpdateGridBtns();
  drawOverlay();
}

function toggleFlowSplitMode() {
  flowSplitMode = !flowSplitMode;
  if (flowSplitMode && !flowGridVisible) flowGridVisible = true;
  _nfUpdateGridBtns();
  drawOverlay();
}

function _nfUpdateGridBtns() {
  const g = document.getElementById('newsflow-grid-btn');
  if (g) { g.textContent = flowGridVisible ? '▦ Hide flow grid' : '▦ Flow grid';
           g.classList.toggle('active', flowGridVisible); }
  const sp = document.getElementById('newsflow-split-btn');
  if (sp) sp.classList.toggle('active', flowSplitMode);
}

// generic screen→image drag helper for grid lines
let _nfDragging = false;
function _nfDragStart(e, onMove, onDone) {
  if (_nfDragging) return;      // one drag at a time
  _nfDragging = true;
  e.preventDefault(); e.stopPropagation();
  const p0 = imgToScreen(0, 0), p1 = imgToScreen(100, 100);
  const sx = (p1.x - p0.x) / 100, sy = (p1.y - p0.y) / 100;
  const startX = e.clientX, startY = e.clientY;
  const move = ev => { onMove((ev.clientX - startX) / sx, (ev.clientY - startY) / sy); drawOverlay(); };
  const up = async () => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
    _nfDragging = false;
    await onDone();
    drawOverlay(); if (typeof updatePanel === 'function') updatePanel();
  };
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
}

async function _nfMergeBoundary(b) {
  const up = pageData.shapes[b.upper], lo = pageData.shapes[b.lower];
  if (!up || !lo) return;
  pushUndo();
  const ru = _nfRect(up), rl = _nfRect(lo);
  _nfSetRect(up, { x1: Math.min(ru.x1, rl.x1), y1: ru.y1,
                   x2: Math.max(ru.x2, rl.x2), y2: rl.y2 });
  // the upper element survives (its text layers and article number win);
  // the lower one — and any text it carried — is absorbed
  pageData.shapes.splice(pageData.shapes.indexOf(lo), 1);
  _nfRenumber();
  await replaceAllShapes();
  drawOverlay(); updatePanel();
  showToast('Elements merged — re-run OCR on the merged element if it had text');
}

async function _nfSplitAt(idx, yImg) {
  const s = pageData.shapes[idx];
  const r = _nfRect(s);
  if (yImg < r.y1 + 10 || yImg > r.y2 - 10) {
    showToast('Split point too close to the element edge'); return;
  }
  pushUndo();
  const clone = {
    label: s.label, shape_type: 'rectangle', flags: {},
    group_id: s.group_id ?? 0,
    points: [[r.x1, Math.round(yImg)], [r.x2, r.y2]],
    flow_band: s.flow_band ?? 0, flow_column: s.flow_column ?? 0,
    flow_span: s.flow_span || 1,
    flow_order: (s.flow_order ?? 0) + 0.5,   // provisional; renumber fixes it
  };
  _nfSetRect(s, { x1: r.x1, y1: r.y1, x2: r.x2, y2: Math.round(yImg) });
  pageData.shapes.push(clone);   // the clone starts with NO text layers
  _nfRenumber();
  await replaceAllShapes();
  drawOverlay(); updatePanel();
  showToast('Element split — the lower half starts empty (upper keeps any text)');
}

// Draw the grid + wire the interactions. Called from drawOverlay.
function _newsflowDrawGrid() {
  if (!flowGridVisible || !svgOverlay || !pageData) return;
  const items = _nfFlowItems();
  if (!items.length) return;
  const NS = 'http://www.w3.org/2000/svg';

  const line = (x1i, y1i, x2i, y2i, color, dash, cursor, hook) => {
    const a = imgToScreen(x1i, y1i), b = imgToScreen(x2i, y2i);
    if (!a || !b) return;
    const vis = document.createElementNS(NS, 'line');
    vis.setAttribute('x1', a.x); vis.setAttribute('y1', a.y);
    vis.setAttribute('x2', b.x); vis.setAttribute('y2', b.y);
    vis.setAttribute('stroke', color);
    vis.setAttribute('stroke-width', '1.6');
    if (dash) vis.setAttribute('stroke-dasharray', dash);
    vis.style.pointerEvents = 'none';
    svgOverlay.appendChild(vis);
    if (hook) {
      const hit = document.createElementNS(NS, 'line');
      hit.setAttribute('x1', a.x); hit.setAttribute('y1', a.y);
      hit.setAttribute('x2', b.x); hit.setAttribute('y2', b.y);
      hit.setAttribute('stroke', 'rgba(0,0,0,0)');
      hit.setAttribute('stroke-width', '9');
      hit.style.pointerEvents = 'stroke';
      hit.style.cursor = cursor;
      hook(hit);
      svgOverlay.appendChild(hit);
    }
  };

  // band cuts (visual only)
  const bands = _nfGridBands();
  const bkeys = Object.keys(bands).map(Number).sort((a, b) => a - b);
  for (let k = 0; k + 1 < bkeys.length; k++) {
    const y = (bands[bkeys[k]].y2 + bands[bkeys[k + 1]].y1) / 2;
    const x1 = Math.min(bands[bkeys[k]].x1, bands[bkeys[k + 1]].x1);
    const x2 = Math.max(bands[bkeys[k]].x2, bands[bkeys[k + 1]].x2);
    line(x1, y, x2, y, '#f59e0b', '9,5', null, null);
  }

  // column gutters (draggable: both columns' shared edge moves together)
  const cols = _nfGridColumns();
  const ckeys = Object.keys(cols).map(Number).sort((a, b) => a - b);
  for (let k = 0; k + 1 < ckeys.length; k++) {
    const L = cols[ckeys[k]], R = cols[ckeys[k + 1]];
    const x = (L.x2 + R.x1) / 2;
    const y1 = Math.min(L.y1, R.y1), y2 = Math.max(L.y2, R.y2);
    const cL = ckeys[k], cR = ckeys[k + 1];
    line(x, y1, x, y2, '#38bdf8', null, 'col-resize', hit => {
      hit.addEventListener('mousedown', e => {
        if (e.button !== 0) return;
        pushUndo();
        const left  = _nfFlowItems().filter(z => (z.s.flow_span || 1) === 1 && (z.s.flow_column || 0) === cL);
        const right = _nfFlowItems().filter(z => (z.s.flow_span || 1) === 1 && (z.s.flow_column || 0) === cR);
        const orig = new Map();
        [...left, ...right].forEach(z => orig.set(z.i, _nfRect(z.s)));
        const lo = Math.max(...left.map(z => orig.get(z.i).x1 + 15));
        const hi = Math.min(...right.map(z => orig.get(z.i).x2 - 15));
        _nfDragStart(e, (dx) => {
          const v = Math.max(lo, Math.min(hi, x + dx));
          left.forEach(z  => { const r = orig.get(z.i); _nfSetRect(z.s, { ...r, x2: v }); });
          right.forEach(z => { const r = orig.get(z.i); _nfSetRect(z.s, { ...r, x1: v }); });
        }, async () => { await replaceAllShapes(); });
      });
    });
  }

  // per-column element boundaries (drag = resize both; Alt+click = merge)
  _nfGridBoundaries().forEach(b => {
    line(b.x1, b.y, b.x2, b.y, '#22c55e', null, 'row-resize', hit => {
      hit.addEventListener('mousedown', e => {
        if (e.button !== 0) return;
        if (e.altKey) { _nfMergeBoundary(b); return; }
        pushUndo();
        const up = pageData.shapes[b.upper], lo2 = pageData.shapes[b.lower];
        const ru = _nfRect(up), rl = _nfRect(lo2);
        const minY = ru.y1 + 10, maxY = rl.y2 - 10;
        _nfDragStart(e, (dx, dy) => {
          const v = Math.max(minY, Math.min(maxY, b.y + dy));
          _nfSetRect(up,  { ...ru, y2: v });
          _nfSetRect(lo2, { ...rl, y1: v });
        }, async () => { await replaceAllShapes(); });
      });
    });
  });

  // split mode: a transparent capture layer takes the next click
  if (flowSplitMode) {
    const rect = document.createElementNS(NS, 'rect');
    rect.setAttribute('x', 0); rect.setAttribute('y', 0);
    rect.setAttribute('width', '100%'); rect.setAttribute('height', '100%');
    rect.setAttribute('fill', 'rgba(34,197,94,0.04)');
    rect.style.pointerEvents = 'all';
    rect.style.cursor = 'crosshair';
    rect.addEventListener('mousedown', e => {
      e.stopPropagation();
      const box = svgOverlay.getBoundingClientRect();
      const p0 = imgToScreen(0, 0), p1 = imgToScreen(100, 100);
      const xi = (e.clientX - box.left - p0.x) / ((p1.x - p0.x) / 100);
      const yi = (e.clientY - box.top  - p0.y) / ((p1.y - p0.y) / 100);
      const hitEl = _nfFlowItems().find(z => {
        const r = _nfRect(z.s);
        return xi >= r.x1 && xi <= r.x2 && yi >= r.y1 && yi <= r.y2;
      });
      if (hitEl) _nfSplitAt(hitEl.i, yi);
      else showToast('Click inside a flow element to split it');
    });
    svgOverlay.appendChild(rect);
  }
}
