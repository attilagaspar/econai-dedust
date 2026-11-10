# 13. Cross-source entity linkage — the firm authority as first instance

**Status (2026-11-10): PLAN, nothing built.** Discussed and agreed with
Attila on 2026-11-10. Builds on the authority system (05 §authority), the
temporal authority schema (03: slices), the P12 entity index (07) and the
Phase H records (09 §H5). First instance: firms across the Compass volumes.
Nothing in the design is Compass-specific; "firm" is one entity type of one
authority file, like "settlement" in `places_hu`.

## 1. Goal

A record in one source resolves to a stable entity ID **at annotation
time**, against the entities already known from earlier sources — or mints a
new entity, or is declared the successor of an old one. Once records are
resolved, one entity can be browsed across every source and year it appears
in, with provenance down to the page and the fragment, and exported as one
row per entity × source-year.

The research instance: Compass firm directories, volumes five years apart
(1877, 1882, 1887, 1892, …). Year one's verified records seed the list; each
later volume's records are matched to it as they are extracted, before any
field extraction or export. Later: officers, founding year, profits attach
to the entity-year; balance sheets are a separate step that attaches to the
same entity-year.

## 2. Design

### 2.1 The authority is the spine, the record is the unit

- **One authority file** per entity family in `authorities/` (e.g.
  `firms_hu.authority.json`), shared across projects and sources the way
  `places_hu` is. Same schema as every other authority (03): `{id, name,
  aliases[], xref, attrs, slices[]}`; `entity_types: ["firm"]`.
- **The unit of resolution is the record**, not the cell: for Phase-H
  projects, the record (group of fragments behind one start box, see 09 §H5)
  gets the entity; the key string is the start box's best text, available
  right after detection + OCR. For table projects it stays the cell/row, as
  today. Storage: `records.json` entries gain `authority` in the same shape
  shapes carry it (`{id, name, type, source, ts}`); the start shape mirrors
  it so the editor shows the badge.
- **Aliases carry a language tag** (`{name, source, lang}`): a Compass entry
  often prints the German and the Hungarian name; both become aliases of one
  entity. The display name is the first-seen name; the ID never changes.
- **Attributes that help matching** live on the entity, as xrefs where an
  authority exists: seat (`xref.place → places_hu id`), founding year, legal
  form, industry (`xref.industry → industries_hu id`). Per-year facts
  (name as printed, seat, capital) go into **slices** keyed by source and
  year, so an entity's history is readable without opening the pages.
- **Provenance is mandatory**: every slice records `{project, stem,
  group_id}` (or `{project, stem, shape idx, row}` for tables). The entity
  view opens the fragment in the editor from it.

### 2.2 Three outcomes per record

Decided up front so the data model never needs a rework:

| outcome | what is written |
|---|---|
| **same** | record → existing entity; a new alias if the printed name differs; a slice for this source-year |
| **new** | a new entity minted from the record (ID from the authority's id scheme, name = printed name, slice as above) |
| **successor** | a new entity **plus a typed succession edge** `{from, to, type: rename\|merger\|split\|legal_form\|successor, as_of, source, note}` |

Succession edges are the second temporal mechanism named in 07 §P12 item 4
(slices for attribute change, edges for identity change). They live in the
authority file (`edges: []`) and are rare for places, the norm for firms.
"Ceased" is not an outcome: absence is recorded by the **coverage** flag
(§2.5), not by an edge.

Two missing primitives against today's resolver: **mint** (create an
entity from a record) and **edge** (link two entities). Everything else is
the existing matcher + accept/reject flow.

### 2.3 Matching: more than the name

- The resolver stays the generic one (`_authority_match`: exact-normalised
  priority, fuzzy ratio, context boost, accept floor). Additions are
  authority-declared, never hard-coded:
  - **`query_strip` generalised to normalisation rules** declared in the
    authority file: legal-form tokens (`részvénytársaság`, `r.-t.`,
    `Actiengesellschaft`, `A.-G.` …), punctuation, language-pair folding.
  - **Blocking keys as soft boosts**, like the place parent today: seat
    (resolved against `places_hu`), founding year, legal form. A name match
    with the same seat and founding year beats a better name match with a
    different seat.
  - **Sequence prior** (new, generic): sources list entities in a stable
    order (Compass: by section, then roughly alphabetically). When record n
    resolved to entity E, candidates near E in the *previous source-year's
    sequence* are boosted for record n+1. This is ditto inheritance lifted
    to the level of sources; it resolves the boring majority without a
    lookup. Needs per-slice `seq` (the record's position in its source),
    which the sweep already provides as `group_id`.
- **LLM disambiguation** for near-ties exists in the unresolved worklist
  (05); it gets the candidate's slices as context (seat, year, previous
  names) instead of just the strings.
- **Gold set first**: hand-link ~100 firms between two verified volumes
  before tuning the matcher, so matching quality is measured (precision,
  recall, mint-when-should-match, match-when-should-mint), not felt.

### 2.4 UX

- **Record browser** (09 §H5) gains an entity column: badge (resolved /
  unresolved / new / successor), click → candidate list with each
  candidate's last known slice (name, seat, year, the source it came from)
  and its crop from that source; buttons *same* / *new* / *successor of…*.
- **Batch resolve records** as a ⚙ Batch operation (like `resolve_authority`
  for cells): pages pattern, authority, use sequence prior y/n, auto-accept
  above the floor, everything else to the worklist.
- **Unresolved worklist** (exists for cells) lists records too, grouped by
  folded name, with the crop; *new entity* is one click there.
- **Entity view** (= P12 entity index, item 2): one entity → its slices in
  source-year order, each with the crop strip of the record and a link into
  the editor; succession edges drawn as a small chain (A → renamed → B).
  Read-only; corrections happen on the record.
- **Bootstrap**: a batch operation "mint entities from all verified
  records" for the first source of a family, so year one does not need
  clicking through.

### 2.5 Coverage

Per project: `coverage: complete | partial` (config), set when the whole
source was processed. The entity view and the export say "absent in 1887"
only for complete sources; partial sources yield "not digitised".

### 2.6 Export

One row per **entity × source-year** (long) with: entity id, display name,
name as printed, lang, seat id + name, founding year, legal form, the
structured fields extracted for that record (09 §H6 / 05 structured), and
provenance (project, stem, group_id). A companion **edges** sheet. This is
the H6 records export with the ID attached, and the P12 cross-project tidy
export specialised to one authority.

## 3. What exists, what is missing

| piece | state |
|---|---|
| authority files, resolver, worklist, batch resolve for cells | built (05) |
| temporal slices in the authority schema | built (03; places 1910 + 1930 slices) |
| records with group_id, fragments, crops, record browser | built (09 §H5, §H8) |
| structured extraction per annotation | built (05) |
| **mint entity** from a record / worklist | missing |
| **succession edges** in the schema + resolver + view | missing |
| record-level `authority` (records.json + start shape mirror) | missing |
| normalisation rules + blocking boosts + sequence prior | missing (query_strip exists) |
| entity view across projects (P12 item 2) | missing |
| coverage flag | missing |
| entity × source-year export | missing (H6 not built either) |

## 4. Phasing

| step | what | effort |
|---|---|---|
| E1 | authority schema additions (`lang` on aliases, `edges[]`, normalisation rules, id scheme); mint + edge endpoints; `firms_hu` file created empty | 1 session |
| E2 | gold set: hand-link ~100 records 1877 ↔ 1882 in a throwaway sheet; matcher scorer script | user ½ day + ½ session |
| E3 | record-level resolve: records.json `authority`, record browser column + candidate panel with *same/new/successor*, batch op, worklist for records, bootstrap op | 2 sessions |
| E4 | matcher additions: normalisation rules, seat/year/legal-form boosts, sequence prior; measured against E2 | 1 session |
| E5 | entity view (P12 item 2) with slices, crops, edges | 1–2 sessions |
| E6 | coverage flag + entity × source-year export + edges sheet | 1 session |

Order of use on the research side: bootstrap from the first verified volume
(E3), resolve the second volume with the sequence prior (E4), check against
the gold set (E2), then extend officers/profits via structured extraction on
the resolved records — the identity layer does not change when fields are
added.

## 5. Open questions (decide before E3)

1. **ID scheme** for minted entities: opaque sequential (`F000123`) vs
   source-derived (`compass1877-0412`). Opaque is cleaner; source-derived is
   citeable without a lookup. Suggest opaque ID + the first slice's
   provenance printed next to it everywhere.
2. **Where does the hand decision live** when it overrides the matcher —
   on the record (like `shape.authority.source = human`) is consistent with
   cells; the authority file only ever receives entities, aliases, edges.
3. **Merging two entities minted by mistake**: a `duplicate_of` edge that the
   index follows, rather than rewriting IDs already exported.
4. **Granularity of "same"**: a firm that changes legal form (Kft → Rt) —
   same entity with a new slice, or successor edge? Suggest: same entity
   when the printed name is otherwise unchanged; edge when the name changes
   too. Record the rule in the authority file's README so RAs apply it
   consistently.
