# EconAI — Development Progress

_Last updated: 2026-04-28_

---

## What this project is

A unified browser-based pipeline for digitizing historical economic documents at scale.
Two document types:
- **Type A** — tables (statistical yearbooks, census data): needs grid/superstructure detection
- **Type B** — structured text (company registers, strike records): needs LLM field extraction

Full pipeline: annotate layout → train layout model (GPU) → run inference (GPU) → correct predicted layouts → detect superstructure → OCR cells → LLM cleaning → human cell validation → export to Excel/CSV

---

## What has been built (as of this session)

### CLI (`econai.py`)
- `python econai.py new-project <name> --type A --labels label1 label2`
- `python econai.py list` — show all projects with stage and page count
- `python econai.py status <name>` — full pipeline view with stage markers
- `python econai.py advance <name>` / `set-stage <name> <stage>`
- `python econai.py serve [--port 8000]` — kills any old process on port, starts uvicorn, opens browser

### Backend (`app/server.py` — FastAPI)
- `GET /api/pages?folder=` — list all pages (stem names) in a folder
- `GET /api/image?folder=&stem=` — serve page image
- `GET /api/page?folder=&stem=` — serve LabelMe JSON (with injected `_idx` per shape)
- `PATCH /api/page/shape?folder=&stem=&idx=` — update one shape (text, points, label)
- `POST /api/page/shape` — add new shape
- `PUT /api/page/shapes` — replace entire shapes array (used by undo)
- `DELETE /api/page/shape?idx=` — remove shape
- `GET /api/cell?folder=&stem=&idx=&pad=` — return cropped cell image (PIL)

### Pipeline state machine (`app/pipeline.py`)
- Type A stages: `raw → annotating → training → predicting → correcting → superstructure → ocr → llm_cleaning → validating → exporting → done`
- Type B stages: same minus `superstructure`
- Each project lives in `projects/<name>/` with `annotations/`, `intermediate/`, `output/`, `config.json`, `pipeline.json`

### Web editor (`app/static/index.html`)
Single-page app with OpenSeadragon viewer + SVG overlay. Features:

**Review mode (default)**
- Pan and zoom the page image
- All detected boxes shown with fill color (label type) and border color (processing status)
- Border color legend:
  - Grey — raw box (no OCR, no LLM, no human)
  - Yellow — has OCR text
  - Blue — has LLM-cleaned text
  - Green — human-corrected (final)
  - Yellow outer + Blue inner — both OCR and LLM, no human yet
- Click a box → right panel shows: cell crop image, OCR text, LLM text, human correction field, label
- Ctrl+S / Save button — saves human correction to JSON
- Arrow keys — navigate pages
- Keyboard shortcut E — toggle edit mode

**Edit mode**
- OSD pan/zoom disabled; mouse draws/moves/resizes boxes
- Draw new box: click-drag on empty canvas area
- Move box: drag from center area
- Resize box: drag one of 8 handles (corners + midpoints)
- Right panel: label dropdown (editable), delete button
- Ctrl+Z — undo (up to 50 steps, snapshots full shapes array)
- Del key — delete selected shape

---

## File structure

```
econai-dedust/
  econai.py              # CLI entry point
  app/
    server.py            # FastAPI backend
    pipeline.py          # pipeline state machine
    static/
      index.html         # single-file web app
  projects/
    <name>/
      config.json        # type, labels, server SSH settings (to fill in)
      pipeline.json      # current stage + timestamps
      annotations/       # LabelMe JSONs + images go here
      intermediate/      # model outputs, OCR results etc.
      output/            # final Excel/CSV exports
  samples/
    kozponti_ertesito/   # 50 test pages (page_1.jpg + page_1.json, ...)
  PROGRESS.md            # this file
```

---

## Known issues / bugs fixed this session

- Windows terminal encoding: fixed by wrapping stdout/stderr with UTF-8 TextIOWrapper
- Git Bash mangling `/PID` flag for taskkill: use PowerShell to kill processes
- New box draw left old box "active": fixed by clearing `selIdx` on background mousedown
- Second draw failed (OSD re-captured mouse): fixed by only re-enabling OSD nav when NOT in edit mode
- 405 on POST /api/page/shape: server was running stale version; proper kill+restart fixed it

---

## What comes next

### Step 6 — GPU server SSH integration
The config.json for each project has a `server` block:
```json
{
  "server": {
    "host": "",
    "user": "",
    "key_file": "",
    "remote_path": ""
  }
}
```

Tasks to implement:
1. **Push annotations to server** — rsync/scp `annotations/` to remote
2. **Submit training job** — SSH → run Detectron2 training inside Docker container
3. **Poll job status** — check if training is still running, show progress in UI
4. **Pull predictions back** — rsync predicted JSONs from server to local `annotations/`
5. Same pattern for inference (predicting stage) and OCR stage

### Step 7 — Wire pipeline scripts as callable stages
Existing scripts in `econai-llmocr` repo:
- `layout_superstructure_detect.py` → superstructure stage
- `add_ocr_to_layout_jsons.py` → ocr stage
- `add_llm_cleaning_to_layout_jsons.py` → llm_cleaning stage
- `json_join_excel_export.py` → exporting stage

Each should be callable from the UI with a "Run" button that:
- Executes the script (locally or via SSH as appropriate)
- Shows stdout/stderr in a log panel
- Advances pipeline stage on success

### Step 8 — Project dashboard
Landing page (instead of jumping straight to the page viewer) showing all projects,
their current stage, page count, and quick-action buttons (Open, Advance, Export).

### Step 9 — Collaborator setup guide
Short written guide for research assistants: how to install, how to start the server,
how to use the annotation editor and validator.

---

## How to start the app

```bash
# In Git Bash or terminal, from econai-dedust/:
python econai.py serve

# Then open http://localhost:8000 in Chrome
# To view sample data, point folder= to the samples/kozponti_ertesito/ path
```

Requires: Python 3.10+, FastAPI, uvicorn, Pillow, psutil
Install: `pip install fastapi uvicorn pillow psutil`
