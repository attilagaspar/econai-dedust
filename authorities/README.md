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

**The data files are git-ignored** (large, regenerated, and grow over time —
same policy as `projects/`). Only this README is tracked. Obtain or rebuild the
data and drop it in here; the app loads authorities from this directory.

## Schema (temporal, v0.2)

```jsonc
{
  "authority": "places_hu",
  "version": "0.2",
  "entity_types": ["county", "district", "settlement"],
  "slices_present": [1910],            // source-years currently merged in
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
