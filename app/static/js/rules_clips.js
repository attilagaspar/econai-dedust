// Split from index.html — classic scripts share the global scope;
// load order in index.html is load-bearing. See knowledge_base/02_architecture.md.
// ── Row rules: arithmetic checks between lattice columns ────────────────────
let projectRules = [];   // [{expr: "1+2=4", name: "..."}]

async function loadRules() {
  try {
    const r = await fetch(`${API}/api/rules?folder=${encodeURIComponent(folder)}`);
    projectRules = r.ok ? ((await r.json()).rules || []) : [];
  } catch { projectRules = []; }
  populateRuleDiagOptions();
}

function populateRuleDiagOptions() {
  const sel = document.getElementById('diagnostic-select');
  [...sel.querySelectorAll('option[data-rule]')].forEach(o => o.remove());
  if (projectRules.length) {
    const all = document.createElement('option');
    all.value = 'rule:all';
    all.dataset.rule = '1';
    all.textContent = `ROW RULE: ⚖ all rules (${projectRules.length})`;
    sel.appendChild(all);
  }
  projectRules.forEach((rule, i) => {
    const o = document.createElement('option');
    o.value = `rule:${i}`;
    o.dataset.rule = '1';
    o.textContent = `ROW RULE: ${rule.name || rule.expr}`
                  + (rule.pattern ? `  [${rule.pattern}]` : '');
    sel.appendChild(o);
  });
  if (diagnosticMode.startsWith('rule:') && diagnosticMode !== 'rule:all'
      && !projectRules[parseInt(diagnosticMode.slice(5))]) {
    diagnosticMode = 'none';
    sel.value = 'none';
  }
  if (diagnosticMode === 'rule:all' && !projectRules.length) {
    diagnosticMode = 'none';
    sel.value = 'none';
  }
}

// ── Rule editor modal ──
function openRuleEditor() {
  if (!folder) { showToast('Load a folder first'); return; }
  renderRuleList();
  document.getElementById('rule-editor-modal').style.display = 'flex';
}
function closeRuleEditor() {
  document.getElementById('rule-editor-modal').style.display = 'none';
}
function renderRuleList() {
  const wrap = document.getElementById('rule-list');
  wrap.innerHTML = projectRules.length ? projectRules.map((r, i) => `
    <div style="display:flex;gap:6px;align-items:center;">
      <input class="rule-expr" data-i="${i}" value="${_escHtml(r.expr || '')}" placeholder="e.g. 1+2=4"
        style="width:110px;background:#0d1b35;border:1px solid #0f3460;color:#ccc;border-radius:4px;padding:5px 8px;font-size:0.85rem;font-family:monospace;">
      <input class="rule-name" data-i="${i}" value="${_escHtml(r.name || '')}" placeholder="name (e.g. male + female = all workers)"
        style="flex:1;background:#0d1b35;border:1px solid #0f3460;color:#ccc;border-radius:4px;padding:5px 8px;font-size:0.85rem;">
      <input class="rule-zeros" data-i="${i}" value="${_escHtml(r.zeros ?? DEFAULT_ZERO_CHARS)}"
        title="Zero characters: a cell consisting only of these counts as 0 (empty cells are always 0)"
        style="width:90px;background:#0d1b35;border:1px solid #0f3460;color:#888;border-radius:4px;padding:5px 8px;font-size:0.85rem;font-family:monospace;">
      <input class="rule-pattern" data-i="${i}" value="${_escHtml(r.pattern || '')}" placeholder="pages 1,0"
        title="Page pattern: 1 = rule applies, 0 = skip; cycles over pages in order. Blank = every page. E.g. 1,0,0 applies to every 3rd page."
        style="width:74px;background:#0d1b35;border:1px solid #0f3460;color:#ccc;border-radius:4px;padding:5px 8px;font-size:0.85rem;font-family:monospace;">
      <button onclick="deleteRule(${i})" title="Delete rule"
        style="background:none;border:1px solid #c04040;color:#fca5a5;border-radius:4px;padding:4px 8px;cursor:pointer;font-size:0.8rem;">✕</button>
    </div>`).join('')
    : '<div style="color:#666;font-size:0.8rem;">No rules yet — add one below.</div>';
}
function _collectRuleInputs() {
  document.querySelectorAll('#rule-list .rule-expr').forEach(el => { projectRules[+el.dataset.i].expr = el.value.trim(); });
  document.querySelectorAll('#rule-list .rule-name').forEach(el => { projectRules[+el.dataset.i].name = el.value.trim(); });
  document.querySelectorAll('#rule-list .rule-zeros').forEach(el => { projectRules[+el.dataset.i].zeros = el.value; });
  document.querySelectorAll('#rule-list .rule-pattern').forEach(el => { projectRules[+el.dataset.i].pattern = el.value.trim(); });
}
function addRule() {
  _collectRuleInputs();
  projectRules.push({expr: '', name: '', zeros: DEFAULT_ZERO_CHARS, pattern: ''});
  renderRuleList();
}
function deleteRule(i) {
  _collectRuleInputs();
  projectRules.splice(i, 1);
  renderRuleList();
}
async function saveRules() {
  _collectRuleInputs();
  projectRules = projectRules.filter(r => r.expr);
  for (const r of projectRules) {
    if (!/^[\d+\-*/().\s]+=[\d+\-*/().\s]+$/.test(r.expr)) {
      showToast(`Invalid expression: "${r.expr}" — use column numbers, + - * / ( ) and one =`);
      return;
    }
    if (r.pattern && !/^[01](\s*,\s*[01])*$/.test(r.pattern)) {
      showToast(`Invalid page pattern: "${r.pattern}" — use 1s and 0s separated by commas, e.g. 1,0,0`);
      return;
    }
  }
  const resp = await fetch(`${API}/api/rules?folder=${encodeURIComponent(folder)}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rules: projectRules}),
  });
  if (!resp.ok) { showToast(`Rule save failed: ${resp.status}`); return; }
  populateRuleDiagOptions();
  closeRuleEditor();
  showToast(`${projectRules.length} rule(s) saved`);
  refreshDiag(); drawOverlay();
}

// ── Rule evaluation ──
// Best layer per internal row: Human > LLM > OCR > PDF.  Cells without an
// internal row structure fall back to their best flat text split into lines.
function _bestRowValues(shape) {
  const rows = shape.row_struct?.rows;
  if (rows?.length) return rows.map(r =>
    (r.human || '').trim() || (r.llm || '').trim() || (r.ocr || '').trim() || (r.pdf || '').trim());
  const flat = shape.human_output?.human_corrected_text
            || shape.openai_output?.response
            || shape.tesseract_output?.ocr_text
            || shape.pdf_text || '';
  return flat.trim() ? flat.trim().split('\n').map(s => s.trim()) : [];
}

// Default "zero characters": tokens made only of these mean zero in the
// sources (dash variants, underscore, dots, commas…).  Per-rule overridable.
const DEFAULT_ZERO_CHARS = '-_–—―−‐‑‒·.,~';

function _ruleNum(s, zeroChars) {
  if (s == null) return 0;                       // empty / missing cell = 0
  s = String(s).trim();
  if (!s) return 0;
  const zset = zeroChars || DEFAULT_ZERO_CHARS;
  if ([...s].every(ch => zset.includes(ch))) return 0;
  s = s.replace(/\s+/g, '').replace(',', '.');
  return /^-?\d+(\.\d+)?$/.test(s) ? parseFloat(s) : NaN;
}

// Evaluate "1+2=4" with valueAt(col) → number|NaN.  Returns true/false;
// any unparseable value makes the rule fail (false).  null = malformed rule.
function _evalRuleExpr(expr, valueAt) {
  const sides = expr.split('=');
  if (sides.length !== 2) return null;
  const evalSide = (side) => {
    let ok = true;
    const replaced = side.replace(/\d+/g, m => {
      const v = valueAt(parseInt(m, 10));
      if (isNaN(v)) { ok = false; return '0'; }
      return `(${v})`;
    });
    if (!ok || !/^[\d+\-*/().\s]+$/.test(replaced)) return NaN;
    try {
      const v = Function('"use strict";return (' + replaced + ')')();
      return (typeof v === 'number' && isFinite(v)) ? v : NaN;
    } catch { return NaN; }
  };
  const L = evalSide(sides[0]), R = evalSide(sides[1]);
  if (isNaN(L) || isNaN(R)) return false;
  return Math.abs(L - R) <= 1e-6;
}

// Does this rule apply to the current page?  A rule's page pattern is a
// sequence of 1s/0s (e.g. "1,0,0") that cycles over the pages in order:
// 1 = rule applies, 0 = skip.  Blank pattern = applies to every page.
function _ruleAppliesToPage(rule) {
  const bits = (rule?.pattern || '').split(',').map(s => s.trim()).filter(s => s !== '');
  if (!bits.length) return true;
  if (pageIdx < 0) return true;
  return bits[((pageIdx % bits.length) + bits.length) % bits.length] === '1';
}

// ── Multiple lattices per page ──────────────────────────────────────────────
// A shape belongs to a lattice "table" (shape.table, default 0). super_row /
// super_column are scoped within a table. _rk() is the per-(table,row) grouping
// key used everywhere that used to group by super_row alone.
function _tableOf(s) {
  return (s && s.super_row != null && s.super_column != null) ? (s.table ?? 0) : null;
}
function _rk(s) { return `${s.table ?? 0}:${s.super_row}`; }
function _latticeTableIds() {
  const t = new Set();
  (pageData?.shapes || []).forEach(s => { const k = _tableOf(s); if (k != null) t.add(k); });
  return [...t].sort((a, b) => a - b);
}
function _activeTable() {
  const k = _tableOf(pageData?.shapes?.[selIdx]);
  if (k != null) return k;
  const ids = _latticeTableIds();
  return ids.length ? ids[0] : 0;
}

// Scan the page for violations of one rule.  Returns
// [{superRow, table, nRows, badRows:[k,...], cols:[{col, idx, values:[...]}]}]
function _ruleScanPage(rule) {
  const out = [];
  if (!rule?.expr || !pageData?.shapes) return out;
  if (!_ruleAppliesToPage(rule)) return out;   // pattern says skip this page
  const cols = [...new Set((rule.expr.match(/\d+/g) || []).map(Number))];
  if (!cols.length) return out;

  // Group lattice cells per (table,row): key → {cells:{super_column→{s,idx}}, sr, table}
  const rowMap = {};
  pageData.shapes.forEach((s, i) => {
    if (s.super_row == null || s.super_column == null) return;
    const g = (rowMap[_rk(s)] ??= {cells: {}, sr: s.super_row, table: s.table ?? 0});
    g.cells[s.super_column] = {s, i};
  });

  for (const key of Object.keys(rowMap)) {
    const {cells: entry, sr, table} = rowMap[key];
    // All referenced columns must exist in this lattice row (skips headers)
    if (!cols.every(c => entry[c])) continue;
    const vals = {};
    let nRows = 0;
    cols.forEach(c => {
      vals[c] = _bestRowValues(entry[c].s);
      nRows = Math.max(nRows, vals[c].length);
    });
    if (!nRows) continue;   // nothing to evaluate in this lattice row
    const badRows = [];
    for (let k = 0; k < nRows; k++) {
      if (_evalRuleExpr(rule.expr, c => _ruleNum(vals[c]?.[k], rule.zeros)) === false) badRows.push(k);
    }
    if (badRows.length) {
      out.push({superRow: sr, table, nRows, badRows,
                cols: cols.map(c => ({col: c, idx: entry[c].i, values: vals[c]}))});
    }
  }
  return out;
}

// which: a rule index, or 'all' to union every rule's violations.  Per-rule
// page patterns are respected (via _ruleScanPage).  When several rules flag
// the same cell, their violating internal rows are unioned.
function _computeRuleDiagnostic(which) {
  const rules = which === 'all'
    ? projectRules
    : (projectRules[which] ? [projectRules[which]] : []);
  rules.forEach(rule => {
    _ruleScanPage(rule).forEach(v => {
      v.cols.forEach(({idx}) => {
        diagnosticFlagged.add(idx);
        const set = (diagnosticRuleRows[idx] ??= new Set());
        v.badRows.forEach(k => set.add(k));
        diagnosticRowCounts[idx] = set.size;   // label = #violated internal rows
      });
    });
  });
}

// ── Rule fix: LLM-assisted correction of rule violations ────────────────────
const DEFAULT_RULEFIX_PROMPT =
`You are correcting digit-reading errors in a scanned historical statistical table.
For each violating line you receive one small image snippet per table column (labeled with its column and line number), and below, the values we currently read on those lines.
An arithmetic rule must hold between the columns on every line, but on these lines it fails — most likely because digits were misread (e.g. 5 vs 6, 50 vs 5O, a dropped digit). A dash or similar mark in the image means zero; an empty snippet also means zero.
Compare the readings with the image snippets and propose the most plausible minimal corrections that make the rule hold on each violating line.`;

let _rfState = null;   // {ruleIdx, rule, violations:[...], running}

function openRuleFix() {
  if (!diagnosticMode.startsWith('rule:')) {
    showToast('Select a ROW RULE in the Diagnose dropdown first'); return;
  }

  // Which rule(s) to fix: a single rule, or every rule when "all rules" is on.
  // Each violation carries its own .rule so the prompt / rule-check use it.
  const all = diagnosticMode === 'rule:all';
  const rules = all ? projectRules : [projectRules[parseInt(diagnosticMode.slice(5), 10)]];
  if (!rules[0]) { showToast('Rule not found'); return; }
  if (!all && !_ruleAppliesToPage(rules[0])) {
    showToast(`This rule does not apply to this page (pattern ${rules[0].pattern})`); return;
  }

  let violations = [];
  rules.forEach(rule => {
    if (!_ruleAppliesToPage(rule)) return;   // respect each rule's page pattern
    _ruleScanPage(rule).forEach(v =>
      violations.push({...v, rule, proposal: null, status: ''}));
  });

  _rfState = {all, violations, running: false};
  document.getElementById('rf-title').textContent = all
    ? `🛠 Rule fix — all rules (${rules.length})`
    : `🛠 Rule fix — ${rules[0].name || rules[0].expr} (${rules[0].expr})`;
  document.getElementById('rf-prompt').value =
    localStorage.getItem('ruleFixPrompt2') || DEFAULT_RULEFIX_PROMPT;
  document.getElementById('rf-chunk').value =
    parseInt(localStorage.getItem('ruleFixChunk'), 10) || 8;
  // Mirror the main model dropdown's options; default to the saved rule-fix
  // model, else the model currently selected in the main LLM panel
  const rfModel = document.getElementById('rf-model');
  rfModel.innerHTML = document.getElementById('llm-model').innerHTML;
  rfModel.value = localStorage.getItem('ruleFixModel')
               || document.getElementById('llm-model').value;
  if (!rfModel.value) rfModel.value = document.getElementById('llm-model').value;
  document.getElementById('rf-status').textContent =
    violations.length ? `${violations.length} violation(s) on this page` : 'No violations on this page 🎉';
  renderRuleFixList();
  document.getElementById('rule-fix-modal').style.display = 'flex';
}

function closeRuleFix() {
  if (_rfState) _rfState.running = false;
  document.getElementById('rule-fix-modal').style.display = 'none';
}

// Colour a rule-fix correction input by state:
//   amber  → edited / hand-typed (will be saved as a Human correction)
//   green  → showing the LLM's proposed value that differs from the reading
//   neutral→ unchanged
function _rfMark(inp) {
  const v = (inp.value || '').trim();
  const orig = (inp.dataset.orig || '').trim();
  const cur  = (inp.dataset.cur || '').trim();
  const edited = orig ? (v !== orig) : (v !== '' && v !== cur);
  if (edited) {
    inp.style.borderColor = '#eab308'; inp.style.background = '#3a2e0a'; inp.style.fontWeight = '700';
  } else if (inp.dataset.changed === '1') {
    inp.style.borderColor = '#22c55e'; inp.style.background = '#0e2a16'; inp.style.fontWeight = '700';
  } else {
    inp.style.borderColor = orig ? '#2a4a8e' : '#33405f'; inp.style.background = '#0d1b35'; inp.style.fontWeight = '400';
  }
}

function renderRuleFixList() {
  const wrap = document.getElementById('rf-list');
  if (!_rfState) { wrap.innerHTML = ''; return; }
  wrap.innerHTML = _rfState.violations.map((v, vi) => {
    const badSet = new Set(v.badRows);
    // Transposed layout: one table row per LINE; per lattice column a crop
    // slice + the reading (+ LLM proposal when it arrives)
    if (!v.badRows.length) return '';   // fully applied → drop from the list
    const _bd = 'border:1px solid #44557f;';   // bright cell edges
    const headerCells = v.cols.map(c =>
      `<td colspan="2" style="color:#aaa;padding:1px 8px;${_bd}">col ${c.col}</td>`).join('');
    // Only the violating lines are shown (they are all the LLM sees, too)
    const rows = v.badRows.map(k => {
      const bg  = 'background:rgba(255,0,80,0.10);';
      const cells = v.cols.map(c => {
        const prop = v.proposal?.[c.col]?.[k];
        const curVal = c.values[k] ?? '';
        const hasProp = prop != null;
        const orig = hasProp ? String(prop) : '';                 // the LLM proposal
        // LLM proposes a value that differs from the current best guess
        const changed = hasProp && String(prop).trim() !== String(curVal).trim();
        const inputVal = hasProp ? String(prop) : curVal;
        // "H" badge when this line already carries a human correction
        const _sh = pageData.shapes[c.idx];
        const _hrows = _sh?.row_struct?.rows;
        const humanVal = _hrows?.[k]
          ? (_hrows[k].human || '').trim()
          : ((_sh?.human_output?.human_corrected_text || '').split('\n')[k] || '').trim();
        const hBadge = humanVal
          ? `<span title="Already has a Human correction: ${_escHtml(humanVal)}" style="display:inline-block;background:#14532d;color:#bbf7d0;border-radius:3px;font-size:9px;font-weight:700;padding:0 3px;margin-left:4px;">H</span>`
          : '';
        // Old value: struck through when the LLM changes it, so old→new reads clearly
        const curStyle = changed ? 'color:#7a8599;text-decoration:line-through;' : 'color:#8aa;';
        // Editable correction field. Initial colour: GREEN+bold when the LLM
        // proposes a different value (draws the eye); neutral otherwise. Editing
        // it turns it amber (→ Human layer) via _rfMark.
        const inBorder = changed ? '#22c55e' : (hasProp ? '#2a4a8e' : '#33405f');
        const inBg     = changed ? '#0e2a16' : '#0d1b35';
        const inWeight = changed ? 'font-weight:700;' : '';
        return `<td style="padding:2px 4px;${_bd}${bg}">`
          + `<canvas class="rf-crop" data-idx="${c.idx}" data-k="${k}" data-n="${v.nRows}"`
          + ` style="display:block;background:#fff;border:1px solid #2a2a4a;border-radius:2px;"></canvas></td>`
          + `<td style="padding:1px 8px;${_bd}${bg}min-width:104px;">`
          + `<div style="${curStyle}">${_escHtml(curVal)}${hBadge}</div>`
          + `<input class="rf-prop" data-vi="${vi}" data-col="${c.col}" data-k="${k}" data-orig="${_escHtml(orig)}" `
          + `data-cur="${_escHtml(String(curVal))}" data-changed="${changed ? '1' : '0'}" `
          + `value="${_escHtml(inputVal)}" title="Leave as-is → LLM layer; edit → Human correction" `
          + `style="width:100%;min-width:92px;box-sizing:border-box;background:${inBg};border:1px solid ${inBorder};border-radius:3px;color:#cde;${inWeight}font:inherit;padding:2px 5px;margin-top:2px;" `
          + `oninput="_rfMark(this)">`
          + `</td>`;
      }).join('');
      // Per-line checkbox (default checked) — only checked lines get applied
      const chk = `<td style="${_bd}${bg}text-align:center;"><input type="checkbox" class="rf-rowcheck" `
        + `data-vi="${vi}" data-k="${k}" checked style="accent-color:#1f7a3d;cursor:pointer;"></td>`;
      return `<tr>${chk}<td style="padding:1px 8px;color:#ff5577;font-weight:bold;${_bd}">${k + 1}</td>${cells}</tr>`;
    }).join('');
    const fixOk = v.proposal ? _rfProposalOk(v) : null;
    return `<div style="border:1px solid #0f3460;border-radius:6px;padding:8px 10px;">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
        <b style="font-size:0.85rem;color:#ccc;">Lattice row ${v.superRow}${v.table ? ` · table ${v.table}` : ''}</b>
        ${_rfState.all ? `<span style="font-size:0.72rem;color:#8ab;" title="${_escHtml(v.rule.expr)}">[${_escHtml(v.rule.name || v.rule.expr)}]</span>` : ''}
        <span style="font-size:0.75rem;color:#ff5577;">violating line(s): ${v.badRows.map(k => k + 1).join(', ')}</span>
        <span style="font-size:0.75rem;color:#aaa;">${_escHtml(v.status || '')}</span>
        ${v.proposal ? `<span style="font-size:0.75rem;color:${fixOk ? '#4ade80' : '#fbbf24'};">${fixOk ? '✓ proposal satisfies the rule' : '⚠ proposal still violates the rule'}</span>` : ''}
        <span style="flex:1;"></span>
        <button onclick="applyRuleFix(${vi})" title="Apply the checked lines: a value left equal to the LLM proposal goes to the LLM layer; an edited (or hand-typed) value goes to Human correction. Applied lines are removed; the rest stay so you can re-ask." style="background:#1f7a3d;border:none;color:#fff;border-radius:4px;padding:3px 12px;cursor:pointer;font-size:0.78rem;">✓ Apply checked</button>
      </div>
      <div style="overflow-x:auto;">
        <table style="border-collapse:collapse;font-size:0.78rem;font-family:monospace;color:#ddd;">
          <tr><td style="color:#555;padding:1px 4px;text-align:center;">✓</td><td style="color:#555;padding:1px 6px;">line</td>${headerCells}</tr>
          ${rows}
        </table>
      </div>
    </div>`;
  }).join('') || '<div style="color:#666;font-size:0.8rem;">Nothing to fix.</div>';
  _rfDrawAllCrops();
}

// Draw each line's slice of the cell crops into the rule-fix tables.
// Crop images are fetched once per shape (/api/cell) and cached for the
// lifetime of the modal; band positions come from row_struct when present,
// otherwise the cell is split evenly into nRows bands.
function _rfDrawAllCrops() {
  if (!_rfState) return;
  _rfState.imgCache ??= {};
  const stem = pages[pageIdx].stem;
  const need = new Set();
  document.querySelectorAll('#rf-list canvas.rf-crop').forEach(cv => need.add(+cv.dataset.idx));
  need.forEach(idx => {
    let img = _rfState.imgCache[idx];
    if (!img) {
      img = new Image();
      img.onload = () => _rfDrawCropsFor(idx);
      img.src = `${API}/api/cell?folder=${encodeURIComponent(folder)}&stem=${encodeURIComponent(stem)}&idx=${idx}`;
      _rfState.imgCache[idx] = img;
    } else if (img.complete && img.naturalWidth) {
      _rfDrawCropsFor(idx);
    }
  });
}

function _rfDrawCropsFor(idx) {
  const img = _rfState?.imgCache?.[idx];
  if (!img?.naturalWidth) return;
  const shape = pageData.shapes[idx];
  if (!shape?.points) return;
  const ys   = shape.points.map(p => p[1]);
  const top  = Math.max(0, Math.min(...ys) - 4);   // /api/cell uses pad=4
  const rows = shape.row_struct?.rows;
  document.querySelectorAll(`#rf-list canvas.rf-crop[data-idx="${idx}"]`).forEach(cv => {
    const k = +cv.dataset.k;
    let relT, relB;
    if (rows?.[k]) {
      relT = rows[k].y0 - top;
      relB = rows[k].y1 - top;
    } else {
      const n = +cv.dataset.n || 1;
      relT = img.naturalHeight *  k      / n;
      relB = img.naturalHeight * (k + 1) / n;
    }
    relT = Math.max(0, relT);
    relB = Math.min(img.naturalHeight, Math.max(relB, relT + 1));
    const srcH  = relB - relT;
    const scale = Math.min(18 / srcH, 110 / img.naturalWidth);
    cv.width  = Math.max(1, Math.round(img.naturalWidth * scale));
    cv.height = Math.max(1, Math.round(srcH * scale));
    cv.getContext('2d').drawImage(img, 0, relT, img.naturalWidth, srcH, 0, 0, cv.width, cv.height);
  });
}

// Does the proposal make the rule hold on every line?
function _rfProposalOk(v) {
  const rule = v.rule;
  const val = (c, k) => {
    const prop = v.proposal?.[c]?.[k];
    const cur  = v.cols.find(x => x.col === c)?.values[k];
    return _ruleNum(prop != null ? prop : cur, rule.zeros);
  };
  for (let k = 0; k < v.nRows; k++) {
    if (_evalRuleExpr(rule.expr, c => val(c, k)) === false) return false;
  }
  return true;
}

function _buildRuleFixPrompt(userPrompt, rule, v, lines) {
  lines = lines ?? v.badRows;
  let s = userPrompt.trim() + '\n\n';
  s += `Rule that must hold on every line: ${rule.expr}`;
  if (rule.name) s += `  ("${rule.name}")`;
  s += `\nColumn numbers in the rule refer to the labeled image snippets.`;
  s += `\nViolating lines (1-based): ${lines.map(k => k + 1).join(', ')}\n`;
  s += `\nCurrent readings on the violating lines:\n`;
  lines.forEach(k => {
    s += `line ${k + 1}:  ` + v.cols.map(c => `col ${c.col} = "${c.values[k] ?? ''}"`).join('   ') + '\n';
  });
  s += `\nReply ONLY with a JSON object of the form {"<column>": {"<line>": "<corrected value>", ...}, ...} `
     + `covering the corrections you propose on these lines (omit values you would keep). `
     + `Example: {"${v.cols[0].col}": {"${(lines[0] ?? 0) + 1}": "345"}}. No explanation, no other text.`;
  return s;
}

// Accepts the per-line mapping format {"4": {"8": "123"}} (preferred) and the
// legacy full-vector format {"4": ["12", ...]}.  Returns proposal[col] as a
// sparse array of length nRows (only proposed positions filled).
function _parseRuleFixReply(text, v) {
  const a = text.indexOf('{'), b = text.lastIndexOf('}');
  if (a < 0 || b <= a) return null;
  let obj;
  try { obj = JSON.parse(text.slice(a, b + 1)); } catch { return null; }
  const out = {};
  for (const c of v.cols) {
    const entry = obj[String(c.col)] ?? obj[c.col];
    if (entry == null) continue;
    const arr = new Array(v.nRows);
    if (Array.isArray(entry)) {
      entry.forEach((val, k) => { if (k < v.nRows && val != null) arr[k] = String(val).trim(); });
    } else if (typeof entry === 'object') {
      Object.entries(entry).forEach(([line, val]) => {
        const k = parseInt(line, 10) - 1;
        if (k >= 0 && k < v.nRows && val != null) arr[k] = String(val).trim();
      });
    } else {
      continue;
    }
    if (arr.some(x => x != null)) out[c.col] = arr;
  }
  return Object.keys(out).length ? out : null;
}

async function runRuleFixLlm() {
  if (!_rfState) return;
  const btn = document.getElementById('rf-run-btn');
  if (_rfState.running) { _rfState.running = false; btn.textContent = '…'; return; }
  const prompt = document.getElementById('rf-prompt').value;
  localStorage.setItem('ruleFixPrompt2', prompt);
  const model = document.getElementById('rf-model').value;
  localStorage.setItem('ruleFixModel', model);
  _rfState.running = true;
  btn.textContent = '■ Stop';
  const statusEl = document.getElementById('rf-status');
  // Eligible = still has violating lines and no current (unapplied) proposal
  const todo = _rfState.violations.filter(v => v.badRows.length && !v.proposal);
  let done = 0;
  for (const v of _rfState.violations) {
    if (!_rfState.running) break;
    if (!v.badRows.length || v.proposal) continue;
    v.status = '⟳ asking LLM…'; renderRuleFixList();
    statusEl.textContent = `${done + 1}/${todo.length}`;

    // Split the violating lines into chunks of at most rf-chunk lines —
    // chunk size 1 means one line per request
    const chunkSize = Math.max(1, parseInt(document.getElementById('rf-chunk').value, 10) || 8);
    localStorage.setItem('ruleFixChunk', String(chunkSize));
    const chunks = [];
    for (let a = 0; a < v.badRows.length; a += chunkSize) {
      chunks.push(v.badRows.slice(a, a + chunkSize));
    }

    let merged = null, failures = 0;
    for (const [ci, lines] of chunks.entries()) {
      if (!_rfState.running) break;
      if (chunks.length > 1) {
        v.status = `⟳ asking LLM… (part ${ci + 1}/${chunks.length})`;
        renderRuleFixList();
      }
      try {
        // One band snippet per (line, column) of this chunk
        const crops = [];
        lines.forEach(k => {
          v.cols.forEach(c => {
            const shape = pageData.shapes[c.idx];
            const rsRows = shape.row_struct?.rows;
            let y0, y1;
            if (rsRows?.[k]) {
              y0 = rsRows[k].y0; y1 = rsRows[k].y1;
            } else {
              const ysAll = shape.points.map(p => p[1]);
              const t = Math.min(...ysAll), b = Math.max(...ysAll);
              y0 = t + (b - t) *  k      / v.nRows;
              y1 = t + (b - t) * (k + 1) / v.nRows;
            }
            crops.push({idx: c.idx, y0, y1,
                        label: `Column ${c.col}, line ${k + 1}:`});
          });
        });
        const params = new URLSearchParams({folder, stem: pages[pageIdx].stem, model});
        const r = await fetch(`${API}/api/page/rule-llm?${params}`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            prompt: _buildRuleFixPrompt(prompt, v.rule, v, lines),
            crops,
          }),
        });
        if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 80)}`);
        const data = await r.json();
        const part = _parseRuleFixReply(data.response, v);
        if (!part) { failures++; continue; }
        // Merge this chunk's sparse proposal into the violation's proposal
        merged ??= {};
        Object.entries(part).forEach(([col, arr]) => {
          merged[col] ??= new Array(v.nRows);
          arr.forEach((val, k) => { if (val != null) merged[col][k] = val; });
        });
      } catch (e) {
        failures++;
        v.status = `✕ ${e.message}`;
      }
    }
    v.proposal = merged;
    if (merged) v.status = failures ? `⚠ ${failures}/${chunks.length} part(s) failed` : '';
    else if (!v.status.startsWith('✕')) v.status = '✕ unparseable LLM reply';
    done++;
    renderRuleFixList();
  }
  _rfState.running = false;
  btn.textContent = '▶ Ask LLM';
  statusEl.textContent = 'done';
}

async function applyRuleFix(vi) {
  const v = _rfState?.violations[vi];
  if (!v) return;

  // Which displayed lines are checked (default checked)
  const checked = new Set(
    [...document.querySelectorAll(`#rf-list .rf-rowcheck[data-vi="${vi}"]`)]
      .filter(cb => cb.checked).map(cb => +cb.dataset.k));

  // Read the editable correction inputs: per column, per line → {nv, orig}.
  // nv === orig (the LLM proposal) → LLM layer; otherwise (edited / hand-typed)
  // → Human layer.
  const inputsByCol = {};
  v.cols.forEach(c => {
    const m = {};
    document.querySelectorAll(`#rf-list input.rf-prop[data-vi="${vi}"][data-col="${c.col}"]`)
      .forEach(inp => { m[+inp.dataset.k] = {nv: (inp.value || '').trim(), orig: (inp.dataset.orig || '').trim()}; });
    inputsByCol[c.col] = m;
  });

  const applied = new Set();   // lines that actually received a change
  let humanCount = 0, llmCount = 0, saveOk = true;
  for (const c of v.cols) {
    const byK = inputsByCol[c.col] || {};
    const shape = pageData.shapes[c.idx];
    const rows  = _rsRows(shape);
    if (rows) {
      // Per-row routing: accepted LLM proposal → LLM layer (rule-fix flag);
      // an edited / hand-typed value → Human layer (wins by layer priority).
      let changed = false;
      rows.forEach((r, k) => {
        if (!checked.has(k)) return;
        const e = byK[k]; if (!e || !e.nv) return;
        const cur = ((r.human || '').trim() || (r.llm || '').trim()
                  || (r.ocr || '').trim()   || (r.pdf || '').trim());
        if (e.nv === cur) return;                         // no change
        if (e.orig && e.nv === e.orig) { r.llm = e.nv; r.llm_fixed = true; llmCount++; }
        else                           { r.human = e.nv; humanCount++; }
        changed = true; applied.add(k);
      });
      if (changed) saveOk = (await saveRowStruct(c.idx)) && saveOk;
    } else {
      // No internal rows: merge the checked changes over the current values.
      // If any checked change was edited by hand → write the merged cell to the
      // Human layer; otherwise write it as the flat LLM response (rule-fix).
      let changed = false, anyHuman = false;
      const merged = c.values.map((cur, k) => {
        if (!checked.has(k)) return cur ?? '';
        const e = byK[k]; if (!e || !e.nv || e.nv === (cur ?? '').trim()) return cur ?? '';
        applied.add(k); changed = true;
        if (!(e.orig && e.nv === e.orig)) anyHuman = true;
        return e.nv;
      });
      if (changed) {
        if (anyHuman) {
          if (!shape.human_output) shape.human_output = {};
          shape.human_output.human_corrected_text = merged.join('\n');
          humanCount++;
        } else {
          if (!shape.openai_output) shape.openai_output = {};
          shape.openai_output.response = merged.join('\n');
          shape.openai_output.mode     = 'rulefix';
          llmCount++;
        }
        saveOk = (await replaceAllShapes()) && saveOk;
      }
    }
  }

  // If a write didn't land (e.g. a transient lock), keep the lines so the user
  // can re-apply — never remove a line we failed to persist.
  if (applied.size && !saveOk) {
    v.proposal = null;
    v.status = '✕ save failed — click Apply checked again';
    renderRuleFixList(); refreshDiag(); drawOverlay();
    if (v.cols.some(c => c.idx === selIdx)) updatePanel();
    showToast(`Lattice row ${v.superRow}: save FAILED — nothing removed, click Apply again`, 6000);
    return;
  }

  // Remove the applied lines; drop the proposal so the remaining (unchecked /
  // unfixed) lines are eligible for a fresh "Ask LLM" pass
  v.badRows  = v.badRows.filter(k => !applied.has(k));
  v.proposal = null;
  v.status   = applied.size
    ? (v.badRows.length ? `applied ${applied.size} — ask LLM again for the rest` : `applied ${applied.size}`)
    : 'nothing applied';
  renderRuleFixList();
  refreshDiag(); drawOverlay();
  if (v.cols.some(c => c.idx === selIdx)) updatePanel();
  showToast(applied.size
    ? `Lattice row ${v.superRow}: applied ${applied.size} line(s)`
      + (humanCount ? ` · ${humanCount} → Human` : '')
      + (llmCount ? ` · ${llmCount} → LLM` : '')
    : `Lattice row ${v.superRow}: no checked line had a change to apply`);
}

// ── Clips: link the same data unit across pages via a colored flag id ───────
let clipMode  = false;
let clipIndex = {};     // {num: [{stem, idx}]}  — document-wide
let clipMax   = 0;      // highest clip number in use
let _clipArmed = null;  // a clip number "picked up" for click-to-place
let _clipDrag  = null;  // {num, ghost, moved} while dragging a flag

const CLIP_PALETTE = ['#e6194B','#3cb44b','#f58231','#4363d8','#911eb4','#42d4f4',
                      '#f032e6','#bfef45','#fabed4','#469990','#9A6324','#800000',
                      '#808000','#000075','#e6beff','#aaffc3'];
function clipColor(n) { return CLIP_PALETTE[((n - 1) % CLIP_PALETTE.length + CLIP_PALETTE.length) % CLIP_PALETTE.length]; }

async function loadClips() {
  try {
    const r = await fetch(`${API}/api/clips?folder=${encodeURIComponent(folder)}`);
    const d = r.ok ? await r.json() : {clips: {}, max: 0};
    clipIndex = d.clips || {}; clipMax = d.max || 0;
  } catch { clipIndex = {}; clipMax = 0; }
  renderClipTray();
}

// Keep the local index in sync after an assign/remove (no full rescan)
function _clipIndexSet(stem, idx, num) {
  for (const k of Object.keys(clipIndex)) {
    clipIndex[k] = clipIndex[k].filter(m => !(m.stem === stem && m.idx === idx));
    if (!clipIndex[k].length) delete clipIndex[k];
  }
  if (num != null) {
    (clipIndex[String(num)] ??= []).push({stem, idx});
    if (num > clipMax) clipMax = num;
  }
}

function toggleClipMode() {
  clipMode = !clipMode;
  _clipArmed = null;
  document.getElementById('clip-btn').classList.toggle('active', clipMode);
  renderClipTray();
  drawOverlay();
}

function renderClipTray() {
  const tray = document.getElementById('clip-tray');
  if (!tray) return;
  if (!clipMode) { tray.style.display = 'none'; return; }
  tray.style.display = 'flex';
  const stem    = pages[pageIdx]?.stem;
  const showAll = tray.dataset.showAll === '1';

  // Flags worth offering on this page: clips with a member elsewhere but none
  // here.  Default = "dangling" (exactly one member); show-all = every clip.
  const items = [];
  for (const [num, members] of Object.entries(clipIndex)) {
    if (members.some(m => m.stem === stem)) continue;     // already placed here
    if (!showAll && members.length !== 1) continue;       // only dangling by default
    items.push({num: +num, count: members.length});
  }
  items.sort((a, b) => a.num - b.num);

  let html = `<b style="color:#e94560;">🚩 Clips</b>`
    + `<button onclick="clipMintArm()" title="Mint a new clip, then click/drag onto an annotation"
        style="background:#1f7a3d;border:none;color:#fff;border-radius:4px;padding:2px 9px;cursor:pointer;font-weight:600;">➕ new (${clipMax + 1})</button>`
    + `<label style="display:flex;align-items:center;gap:3px;cursor:pointer;">
        <input type="checkbox" ${showAll ? 'checked' : ''} onchange="clipToggleShowAll(this.checked)"> show all</label>`
    + (_clipArmed != null
        ? `<span style="color:#ffe119;">armed: clip ${_clipArmed} — click an annotation (Esc to cancel)</span>` : '')
    + `<span style="flex:1"></span>`;

  if (!items.length) {
    html += `<span style="color:#667;">${showAll ? 'no clips yet' : 'no dangling clips for this page'}</span>`;
  } else {
    items.forEach(it => {
      html += `<span class="clip-flag" data-num="${it.num}" title="${it.count} member(s) — drag onto an annotation, or click to pick up"
        style="display:inline-flex;align-items:center;gap:4px;background:${clipColor(it.num)};color:#fff;
               border-radius:4px;padding:2px 8px;cursor:grab;font-weight:700;user-select:none;
               ${_clipArmed === it.num ? 'outline:2px solid #ffe119;' : ''}">🚩 ${it.num}`
        + (it.count > 1 ? `<span style="font-weight:400;opacity:.8;">×${it.count}</span>` : '')
        + `</span>`;
    });
  }
  tray.innerHTML = html;
  tray.querySelectorAll('.clip-flag').forEach(el =>
    el.addEventListener('mousedown', e => _clipFlagMouseDown(e, +el.dataset.num)));
}

function clipToggleShowAll(on) {
  document.getElementById('clip-tray').dataset.showAll = on ? '1' : '0';
  renderClipTray();
}
function clipMintArm() { _clipArmed = clipMax + 1; renderClipTray(); }

async function assignClip(num, idx) {
  if (idx == null || !pageData?.shapes?.[idx]) return;
  pageData.shapes[idx].clip = num;
  await replaceAllShapes();
  _clipIndexSet(pages[pageIdx].stem, idx, num);
  _clipArmed = null;
  drawOverlay(); renderClipTray();
  showToast(`Clip ${num} → annotation ${idx}`);
}

async function removeClip(idx) {
  const s = pageData?.shapes?.[idx];
  if (!s || s.clip == null) return;
  const old = s.clip;
  if (!confirm(`Remove clip ${old} from this annotation?`)) return;
  delete s.clip;
  await replaceAllShapes();
  _clipIndexSet(pages[pageIdx].stem, idx, null);
  drawOverlay(); renderClipTray();
  showToast(`Clip ${old} removed`);
}

// Topmost (smallest-area) shape whose bbox contains an image-space point
function _clipShapeAt(imgX, imgY) {
  let best = -1, bestArea = Infinity;
  (pageData?.shapes || []).forEach((s, i) => {
    const p = s.points; if (!p || p.length < 2) return;
    const x1 = Math.min(p[0][0], p[1][0]), x2 = Math.max(p[0][0], p[1][0]);
    const y1 = Math.min(p[0][1], p[1][1]), y2 = Math.max(p[0][1], p[1][1]);
    if (imgX < x1 || imgX > x2 || imgY < y1 || imgY > y2) return;
    const area = (x2 - x1) * (y2 - y1);
    if (area < bestArea) { bestArea = area; best = i; }
  });
  return best;
}

// Drag a flag from the tray; a short press without movement = pick-up (arm)
function _clipFlagMouseDown(e, num) {
  e.preventDefault(); e.stopPropagation();
  _clipDrag = {num, moved: false, ghost: null};
  const move = ev => {
    if (!_clipDrag) return;
    _clipDrag.moved = true;
    if (!_clipDrag.ghost) {
      const g = document.createElement('div');
      g.textContent = `🚩 ${num}`;
      g.style.cssText = `position:fixed;z-index:9999;pointer-events:none;background:${clipColor(num)};`
        + `color:#fff;font-weight:700;border-radius:4px;padding:2px 8px;font-size:12px;opacity:.9;`;
      document.body.appendChild(g);
      _clipDrag.ghost = g;
    }
    _clipDrag.ghost.style.left = (ev.clientX + 8) + 'px';
    _clipDrag.ghost.style.top  = (ev.clientY + 8) + 'px';
  };
  const up = ev => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
    const d = _clipDrag; _clipDrag = null;
    if (d.ghost) d.ghost.remove();
    if (d.moved) {
      const pt = screenToImg(ev.clientX, ev.clientY);
      if (pt) { const hit = _clipShapeAt(pt.x, pt.y); if (hit >= 0) assignClip(num, hit); }
    } else {
      // treat as click = pick up / toggle
      _clipArmed = (_clipArmed === num) ? null : num;
      renderClipTray();
    }
  };
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && _clipArmed != null) { _clipArmed = null; renderClipTray(); }
});

