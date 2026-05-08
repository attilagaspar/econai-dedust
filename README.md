# EconAI — Historical Document Digitization Pipeline

A browser-based tool for digitizing historical economic documents at scale using layout detection, OCR, and LLM cleaning. Built for researchers who need to turn large collections of scanned statistical tables and registers into structured data — without sending everything to a commercial API.

The pipeline takes you from raw scanned pages all the way to Excel/CSV, with human-in-the-loop correction at every stage.

---

## Screenshots

### Project dashboard

![Dashboard — project list and pipeline](illustrations/1.png)

All projects are listed in the sidebar with their type, page count, and current pipeline stage. Selecting a project shows the full pipeline as a progress indicator, with one-click actions to open the editor, import pages, or advance to the next stage.

![Dashboard — GPU training and server settings](illustrations/2.png)

The dashboard also handles the GPU training workflow: prepare training data (LabelMe → COCO conversion), push to a remote GPU server, run Detectron2 training or inference inside Docker, and pull predictions back — all with a live streaming log. SSH connection settings are configured here.

### Annotation editor

![Annotation editor](illustrations/3.png)

The editor is a full-featured browser-based annotation tool built on OpenSeadragon. It shows the scanned page at full resolution with zoomable pan, and overlays bounding box annotations with label colors. The right panel shows the selected cell's OCR, LLM-cleaned, and human-validated text side by side. The toolbar provides tools for layout detection, lattice grid editing, row/column fill, and diagnostics.

---

## What it does

Provides a full pipeline from raw scanned pages to structured Excel/CSV:

```
annotate layout → train GPU model → run inference → correct predictions
→ detect superstructure → OCR cells → LLM cleaning → validate → export
```

Two document types:
- **Type A** — tables (statistical yearbooks, census data): grid/layout detection
- **Type B** — structured text (company registers): LLM field extraction

---

## How to start

```bash
# From the econai-dedust/ directory:
python econai.py serve

# Opens http://localhost:8000 in your browser automatically.
```

Dependencies: `pip install fastapi uvicorn pillow psutil pymupdf paramiko`

---

## Project dashboard (`/static/dashboard.html`)

Landing page listing all projects with their current pipeline stage and page count.

- **New project** — choose name, type (A/B), and label set
- **Open editor** — jump into the annotation editor for a project
- **Import pages** — load PDFs or images from a local folder; converts PDF pages to JPEG automatically
- **Server settings** — configure SSH host, user, key file, and remote path for GPU training
- **Prepare training data** — converts LabelMe annotations to COCO JSON format
- **Train model** — pushes data to the GPU server and runs Detectron2 training inside Docker, streaming all output live
- **Run inference** — pushes updated scripts and runs inference on the GPU server, streaming output live
- **Pull predictions** — downloads predicted layout JSONs back to the local annotations folder
- All server operations show real-time progress in a popup log (SSE stream)

---

## Annotation editor (`/static/index.html`)

Single-page app with OpenSeadragon viewer and SVG overlay.

### Navigation
- **Arrow keys** — previous / next page
- **Pan and zoom** — mouse drag / scroll wheel (when not in edit mode)
- **E** — toggle edit mode on/off

### Drawing (edit mode)
- **Click-drag on empty canvas** — draw a new bounding box
- **Ctrl+drag on empty canvas** — rubber-band select: selects all boxes that overlap the drawn rectangle

### Single selection
- **Click a box** — select it; right panel shows label, cell image crop, OCR/LLM/human text
- **Delete / Del key** — delete selected box
- **Left-drag a box** — move it
- **Right-drag a box** — drag out a copy at the new position
- **Ctrl+Arrow keys** — clone the selected box flush-adjacent in that direction (for filling table grids)

### Multi-selection
- **Ctrl+click** — add/remove a box from the selection
- **Ctrl+drag on empty canvas** — rubber-band: select all boxes inside the rectangle (additive)
- All operations (move, copy-drag, Ctrl+Arrow clone, delete, label change, N/P stamp) apply to the entire selection

### Clipboard
- **Ctrl+C** — copy selected box(es) to clipboard
- **Ctrl+V** — paste clipboard boxes offset by 10 px

### Cross-page stamping
- **N** — copy current selection to the same coordinates on the next page
- **P** — copy current selection to the same coordinates on the previous page
  (useful for propagating a table grid structure across many identical pages)

### Labels
- Label dropdown in the right panel is pre-populated with the project's configured labels
- Changing the label in the panel updates all selected boxes simultaneously

### Undo
- **Ctrl+Z** — undo up to 50 steps (snapshots full shapes array per step)

### Lattice grid tools
- **Lattice** — auto-detect the table superstructure (row/column grid) from existing annotations
- **Show Grid** — toggle the blue lattice overlay showing detected grid lines
- **Col sep / Row sep** — click inside the grid to insert a new column or row separator, splitting existing cells
- **Del sep** — click a separator line to merge the two adjacent rows or columns
- **Snap** — snap all lattice cell annotations to the exact detected grid boundaries
- **Row fill / Col fill** — propagate cell labels across an entire row or column

---

## GPU training pipeline

Training and inference run on a remote GPU server via SSH/SFTP. The entire flow — file transfer, Docker container startup, training output — is visible in a live popup log.

### What gets pushed to the server
| Local path | Remote path |
|---|---|
| `annotations/*.jpg` | `<remote_path>/<project>/images/` |
| `intermediate/annotations.json` | `<remote_path>/<project>/annotations.json` |
| `intermediate/configs/<project>/fast_rcnn_R_50_FPN_3x.yaml` | `<remote_path>/layout-model-training/configs/<project>/` |
| `app/infer_layout.py` | `<remote_path>/layout-model-training/tools/` |
| `intermediate/train.sh` | `<remote_path>/layout-model-training/scripts/<project>.sh` |
| `intermediate/infer.sh` | `<remote_path>/layout-model-training/scripts/<project>_infer.sh` |

Shell scripts are uploaded with Unix line endings (CRLF stripped) regardless of the local OS.

### Docker setup expected on the server
- Container named `detectron_training_container` with `/home/<user>/econai/koren` mounted as `/workspace`
- Container named `detectron_predicting_container` with the same mount

---

## File structure

```
econai-dedust/
  econai.py                  # CLI entry point (serve, new-project, list, status, advance)
  requirements.txt
  app/
    server.py                # FastAPI backend
    pipeline.py              # pipeline state machine
    page_import.py           # PDF/image import to annotations/
    coco_convert.py          # LabelMe → COCO JSON conversion
    infer_layout.py          # inference helper (runs on GPU server)
    ssh_ops.py               # paramiko SSH/SFTP helpers + stream_command
    static/
      dashboard.html         # project dashboard
      index.html             # annotation editor
  projects/
    <name>/
      config.json            # type, labels, server SSH settings
      pipeline.json          # current stage + timestamps
      annotations/           # LabelMe JSONs + page images
      intermediate/          # COCO JSON, train/infer scripts, configs
      output/                # final Excel/CSV exports
  samples/                   # sample pages for testing
```

---

## CLI reference

```bash
python econai.py serve [--port 8000]        # start the web app
python econai.py new-project <name> --type A --labels label1 label2
python econai.py list                        # all projects with stage and page count
python econai.py status <name>               # full pipeline view
python econai.py advance <name>              # move to next stage
python econai.py set-stage <name> <stage>   # set stage explicitly
```

---

## Pipeline stages

**Type A (tables):**
`raw → annotating → training → predicting → correcting → superstructure → ocr → llm_cleaning → validating → exporting → done`

**Type B (structured text):**
same, minus `superstructure`
