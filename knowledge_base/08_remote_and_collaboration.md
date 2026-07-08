# Remote access & multi-user collaboration — plan

**STATUS (2026-07-08): Phase A + A′ app-side work is IMPLEMENTED** (token guard
middleware + `/api/login` + login page, `_resolve_folder` caging via the
`_REMOTE_REQ` contextvar, `POST /api/review/accept` as the single review-decision
endpoint with the desktop strip migrated to it, `app/static/review.html` mobile
PWA + manifest + service worker + icons, `serve --host` with a refusal to bind
non-locally without `ECONAI_TOKEN`). Tests: `tests/test_remote_guard.py`,
`tests/test_review_accept.py`. The guard is inert without the env var — local
workflow unchanged. The network layer (Cloudflare Tunnel + Access on the user's
domain) is the user's setup step; instructions were handed over. The user chose
the **domain + Cloudflare** variant (RAs can't be asked to install Tailscale).

Two user goals (2026-07-08): (1) a real multi-user server where several people
process the same data; (2) reach the home server from anywhere via the user's
own domain — including a phone-friendly review mode for commutes. They are
phases of one road, not separate projects. **No rewrite is needed**: the
single-process FastAPI + JSON-files architecture already has per-page write
serialization, merge-safe LLM writes, atomic saves, and page status/assignee
flags — the missing pieces are auth, identity, and page-level locking.

## Critical prerequisite (before ANY exposure)

The API accepts arbitrary absolute paths in the `folder` query parameter
(`_resolve_folder`). An internet-exposed instance without hardening =
read/write access to the host filesystem. Phase A must include:
1. restrict `folder` resolution to the `projects/` root (reject absolute /
   traversal paths; keep a legacy escape hatch behind a localhost check),
2. an app-level shared token (header or cookie) as a second layer behind the
   edge auth.

## Phase A (Tailscale variant — NOT chosen; kept as the fallback)

Originally considered when no domain was in play. Public-IP port-forwarding was rejected (plain
HTTP, dynamic IP, port scanners). Instead:
- **Tailscale** on PC + phone + laptop (free tier): stable private 100.x IP /
  MagicDNS name reachable from anywhere, WireGuard-encrypted, no router
  changes, nothing publicly exposed.
- `tailscale serve 8000` → `https://<pc>.tailXXXX.ts.net` with an automatic
  real certificate — required for the PWA (service workers need HTTPS).
- App hardening is IDENTICAL to the domain variant (folder caging, ECONAI_TOKEN
  + login cookie, --host 0.0.0.0, CORS, non-local warning) and ships first.
- Mobile accept goes through a NEW endpoint `POST /api/review/accept`
  ({stem, idx, row, value}: Human write + empty→blank rule server-side, later
  verified_by) so accept semantics live in one place; desktop strip migrates
  to it too. Mobile UI: `app/static/review.html`, card loop (zoomable band
  snippet, inputmode=decimal, ✓/↓/↩/∅ thumb buttons), PWA manifest + minimal
  service worker; v1 online-only.
- If a domain appears later, swap the network layer for Cloudflare Tunnel +
  Access with zero app changes.

## Phase A (domain variant — CHOSEN 2026-07-08) — expose via Cloudflare Tunnel

- `cloudflared` tunnel from the home PC to the user's domain: free, no port
  forwarding, automatic TLS, survives IP changes.
- **Cloudflare Access** (Zero Trust, free ≤50 users) in front: Google/e-mail
  SSO before any request reaches the app — auth without app changes.
- App hardening as above. CORS tightened from `*` to the domain.
- Operational rules: home PC stays on; ONE source of truth — when the home
  machine serves a project, nobody edits that project via Dropbox-synced
  copies elsewhere (the old per-RA-local-server mode and the hosted mode must
  not be mixed for the same project).
- WAN performance is fine: images are served as JPEGs; OCR/LLM run server-side.

## Phase A′ — mobile review PWA (~1–2 sessions, rides on A)

- `app/static/review.html`: phone-shaped page reusing the existing endpoints
  (`POST /api/review/queue`, `/api/cell` band crops, saveRowStruct/patchShape
  paths). Big snippet image, numeric-friendly input, thumb-sized Accept /
  Skip / Undo, same empty→blank semantics as the desktop strip.
- PWA manifest + service worker → installable icon, full screen. v1 is
  online-only; offline batch (download N items, sync later) is a possible v2.
- No native app / app store needed.

## Phase B — multi-user on the same server (~2–3 sessions)

- **Identity**: read the Cloudflare Access user header (or a minimal login) →
  stamp `verified_by` on Human saves and review accepts (this is also what
  the P4 audit wants for provenance/double-entry).
- **Advisory page locks**: opening a page in edit takes a soft lock
  (`flags.lock = {user, ts}`, TTL ~5 min, heartbeat); others see "open by X —
  read-only". Collaboration partitions by page, which is how table
  digitization actually parallelizes. NOT building real-time same-cell
  co-editing (CRDT — out of proportion for a 2–5 person team).
- `assignee` flag becomes enforceable (warn when opening someone else's page);
  dashboard shows per-person progress from the status scan.
- Keep uvicorn workers=1 (the in-process asyncio page locks assume one
  process; fine for ≤10 users). If that ever limits, move locks to file locks.

## Phase C — move off the home PC (optional, ~1–2 sessions)

- Dockerize the webapp (separate from the existing GPU-training Dockerfile);
  run on an always-on box (TK server or small VPS; EasyOCR/torch wants ~4 GB
  RAM; Tesseract is a small binary; GPU training stays remote via SSH exactly
  as today).
- Project files live on the server disk = single source of truth; git and/or
  scheduled backup replaces Dropbox as the sync mechanism (this REMOVES the
  current Dropbox-conflict failure mode between user and RAs).
- Cloudflare tunnel simply moves to that machine; nothing else changes.

## Sequencing & interactions

A → A′ → B → C; each phase is independently useful and none blocks P2 (tidy
export) or P4 (audit) — B's `verified_by` actually feeds P4. Suggested: do
Phase A + A′ when commute-review is wanted; B when a second simultaneous
user actually appears; C when "PC must stay on" hurts.
