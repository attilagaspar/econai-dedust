# Authorities

Controlled vocabularies / gazetteers used to resolve raw OCR'd strings to
**stable canonical IDs** at annotation time, so different data sources join on
IDs instead of fuzzy name matching later. Authorities are project-independent
and shared across projects.

## What lives here

One file per authority, named `<name>.authority.json` — e.g.

- `places_hu.authority.json` — Hungarian county / district / settlement
  gazetteer (spine = GIStA `IDTel1910`).
- `industries_hu.authority.json` — 1900-census industry classification
  (spine = `industry_code_1900_cleaned`). 159 industries (2 with subcodes),
  English label as the display `name`, every Hungarian raw spelling as an
  alias (matching is Hungarian; English is also an alias). Built by
  `build_authority_industries_1900.py` from `industry_schema_1900.csv`, which
  live with the source data in
  `kuk-industry-policy/data-sources/firms_per_settlements_from_census/data_release1.0/`.

Future: `firms.authority.json` (entity DB built from multiple sources).

**The `*.authority.json` data files are tracked in the repo** so every clone
resolves against the same canonical IDs. Builders live with their source data,
not here. (If a file grows very large or churns often, move it to Git LFS.)

## Schema (temporal, v0.2)

```jsonc
{
  "authority": "places_hu",
  "version": "0.2",
  "entity_types": ["county", "district", "settlement"],
  "slices_present": [1910],            // source-years currently merged in
  "query_strip": ["rtv", "tjv"],       // optional: trailing query tokens to drop
                                       //   before matching (admin-status suffixes
                                       //   like rendezett tanácsú / törvényhatósági
                                       //   jogú város). Authority-specific; omit = none.
  "entities": [
    {
      "id": "M0101001",                // stable identity (here: GIStA IDTel1910)
      "name": "Albertfalu",            // current display name
      "aliases": [{"name": "...", "source": "..."}],
      "xref": {"idtel1910": "M0101001"},
      "attrs": {                       // TIMELESS facts only
        "lat": 45.41, "lon": 18.44, "region": "...",
        "modern_name": "Grabovac", "modern_country": "Horvátország"
      },
      "slices": [                      // time-bound facts, one per source-year
        { "as_of": 1910, "source": "GIStA",
          "type": "settlement", "parent": "M0101", "name": "Albertfalu",
          "attrs": {"county_name": "...", "district_name": "...",
                    "county_seat": false, "district_seat": false} }
      ]
    }
  ]
}
```

- `type`, `parent`, and `name` are **time-bound** — they live in `slices[]`,
  one snapshot per source-year. A place's history (village → neighborhood,
  county reassignment after Trianon, …) is just multiple slices on one `id`.
- Hierarchy is by reference: a slice's `parent` points to another entity's `id`.
- Adding a source-year = append a slice to each surviving entity and mint new
  nodes (first slice = that year) for places not present earlier.

## How the places authority was built

`places_hu.authority.json` is the output of `build_authority_1910.py`, which
reads the GIStA Access DB (`8_MO_1865_1910_settlement.accdb`). That builder and
its source DB live with the source data, not in this repo:
`…/techxtremism/data/raw/gista/`. Re-run it there and copy the result here.
The 1933 Helységnévtár layer (digitized with this very tool) will append a
`1933` slice per entity and add körjegyzőség + sub-place nodes.

**1930 slice — COMPLETE rebuild (2026-07-17).** The 1930 leg is a Trianon-Hungary
settlement **roster** from the KSH ethnicity/religion census panel (Sebők-style,
modern-name organized). It was first added 2026-07-14 from the 2019 scrape, but
that scrape was truncated (the source site caps results at 200 rows/page, so
large counties lost their alphabetical tail — see the `census_rescrape` project).
It has now been rebuilt from the **complete** re-scrape:

- **3,555 settlements** get a 1930 slice (was 3,120 → +435 recovered villages,
  overwhelmingly Baranya/Zala/Somogy names). Built via
  `…/census_rescrape/code/python/06_build_authority_1930_v2.py` (roster: D1
  name-dedup, D2 county-from-kód, D3 IDTel1910 bridge) →
  `07_update_places_authority.py` (writes the leg here).
- **Identity only, no data.** The slice records that a place *existed as a unit*
  in 1930, with attrs `{county_modern, ksh_panel_kod}`. **`pop_1930` and all
  census composition were deliberately left out** — the authority is an identity
  spine, not a fact table. Population/ethnicity/religion live in
  `census_rescrape/data/clean/census_panel_canonical.csv`; **join by
  `ksh_panel_kod` (= panel kód) when you need them.**
- **Backward compatible.** Every pre-existing entity id is unchanged (0 id
  changes, 0 regressions, verified); the 1910 leg is untouched; the 1910 slice
  stays `slices[0]` so the editor's display is unaffected. The prior build's
  `pop_1930` key was removed (it had zero readers).

Caveats unchanged: `name` is the panel's (modern-organized) name, so post-1930
renames show their modern form (the contemporary name survives as the entity's
1910/display name + alias); `county_modern` is the modern county from the kód
prefix, NOT the 1930 administrative county — hence slice `parent` is null. Three
villages the source site omits from its name-dropdown (Kömlő, Kömörő, Böde) carry
`attrs.provenance = "old_panel_2019_splice"`. `slices_present` is `[1910, 1930]`.

The original 2026-07-14 builder (`…/csendorkarton/02_add_1930_slice_to_authority.py`,
reading the truncated `authority_1930.csv`) is superseded; do not re-run it.
