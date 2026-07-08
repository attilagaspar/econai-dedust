# EconAI knowledge base

Orientation documents for humans and AI assistants working on **econai-dedust**.
Read `01_project_overview.md` first; the critique and roadmap are the actionable part.

| File | Contents |
|---|---|
| [01_project_overview.md](01_project_overview.md) | What the project is, who uses it, research context |
| [02_architecture.md](02_architecture.md) | Code layout, backend/frontend, how things talk to each other |
| [03_data_model.md](03_data_model.md) | The on-disk JSON data model: shapes, layers, row_struct, lattice, authority, structured, clips |
| [04_workflow.md](04_workflow.md) | The end-to-end pipeline from scanned PDF to exported data, incl. GPU training |
| [05_subsystems.md](05_subsystems.md) | Deep dives: authority/gazetteer resolver, structured (JSON) extraction, row rules & rule-fix, batch operations |
| [06_critique.md](06_critique.md) | What is not right — big-picture, UX, engineering, data-quality weaknesses |
| [07_improvement_roadmap.md](07_improvement_roadmap.md) | Concrete plans to make human–machine interaction faster and shorten raw-data → final-product time |
| [08_remote_and_collaboration.md](08_remote_and_collaboration.md) | Plan: expose the server via Cloudflare Tunnel + domain, mobile review PWA, multi-user identity & page locks, optional hosted deployment |

Conventions for AI assistants:
- The editor is `app/static/index.html` (HTML skeleton) + `app/static/js/*.js` — nine ordered classic scripts sharing one global scope (load order in index.html is load-bearing; no load-time calls into later files). Styles in `app/static/css/editor.css`. The backend is `app/server.py` (~5.4k lines). Grep across `js/` before you assume something doesn't exist — most features do exist but are hard to find.
- Static files are served with `Cache-Control: no-cache`, so a plain reload picks up edits — no hard-reload ritual needed.
- Page data lives in LabelMe-style JSONs next to the page image; the server rewrites the whole file on every save. Client-side writes are serialized through `_serializeWrite` — never bypass it.
- `PATCH /api/page/shape/rows` rebuilds rows from a whitelist of fields; any new per-row field must be added to that whitelist or it silently disappears.
- The user runs the server locally on Windows (`python econai.py serve`), data lives in Dropbox, code in git. Restart the server after backend edits; hard-reload after frontend edits.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Never request the user's SSH private key or passphrase.
