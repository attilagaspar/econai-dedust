# Workflow: raw scan → final data

## Stage by stage

1. **Create project** (dashboard `+ New`): name, type A/B, labels.
2. **Import** PDFs/images → split into page JPEGs under `annotations/<batch>/`.
3. **Annotate a sample** (~20–50 pages) in the editor: boxes + labels. Stamping (P/O keys), Ctrl+arrow cloning and rubber-band selection make grids fast.
4. **Prepare + Train** (dashboard): LabelMe→COCO, `train.sh` generated with solver params (iterations, LR, batch, workers), uploaded via SFTP, run detached in a Docker container on the GPU server; live log streams back; container auto-stopped afterwards. *Train always re-prepares data first* (a stale `train.sh` once silently capped iterations).
5. **Infer + Pull predictions**: model applied to all pages remotely; JSONs pulled into `predictions/`; "Apply to empty pages" seeds uncorrected pages.
6. **Correct layout** in the editor (edit mode `E`).
7. **Lattice** (Type A): auto-detect grid, fix separators, snap. Multi-table pages: carve/split/delete.
8. **Text extraction**: PDF text layer + OCR (Tesseract/EasyOCR), usually **line-by-line** (creates `row_struct`) or **anchored** (project a reference cell's dividers across the row).
9. **LLM cleaning**: per-cell or Batch; text mode writes the LLM layer, JSON mode writes `structured`.
10. **Validate**: row rules (e.g. `1+2=4` per internal row) → violations shaded red; **🛠 Fix rule** sends violating lines (with image snippets) to the LLM, human reviews diffs, applies to LLM layer or edits into Human.
11. **Resolve authorities**: per cell / per column / batch across pages; ditto marks inherit; manual dropdown with live search.
12. **Export**: Excel (layout + Resolved + Structured) or JSON records → then Stata/R downstream.

## The ⚙ Batch modal

One modal drives cross-page operations with shared filters (ordered stems, 1/0 page pattern, column filter, condition): overlap removal, lattice correction, OCR, LLM (text or JSON), anchored runs, convert-to-internal-rows, `resolve_authority`, `json_export`.

## GPU / Docker specifics

- Config per project (`config.json → server`) + Docker card on dashboard. SSH key auth (never ask for the user's key/passphrase).
- Build button uploads the repo `Dockerfile` (CUDA 12.1 + PyTorch + Detectron2) and creates containers.
- Training runs detached (nohup); reopening Train re-attaches to the log; browser disconnect never kills it; server verifies the PID is gone before `docker stop`.
- Known pitfalls: containers created before `--shm-size=8g` fail EasyOCR/dataloader with bus errors (workaround: workers=0, or `docker rm` + rebuild); a predict container can be mounted on the wrong host dir (`docker inspect <c> --format '{{json .Mounts}}'` to check).

## Operational habits

- Restart the server after backend edits; hard-reload (check `[econai] build-N` in console) after frontend edits.
- Data syncs via Dropbox; code via git (GitHub `attilagaspar/econai-dedust`).
- Typical debugging: throwaway uvicorn on a spare port + fixture pages; headless check via `.claude/launch.json` "static-check".
