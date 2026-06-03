# EconAI — Historical Document Digitization Pipeline

A browser-based tool for turning large collections of scanned historical documents (statistical tables, company registers, census pages) into clean, structured data. It combines layout detection, OCR, and LLM-based cleaning, with human-in-the-loop correction at every step.

**What you need:**
- A Windows/Mac/Linux laptop to run the web app
- A GPU server with SSH access (for training the layout model and running inference)
- An OpenAI API key (optional — only for LLM cleaning step)

---

## Screenshots

### Project dashboard

![Dashboard — project list and pipeline](illustrations/1.png)

![Dashboard — GPU training and server settings](illustrations/2.png)

### Annotation editor

![Annotation editor](illustrations/3.png)

---

## How it works

The pipeline takes you from raw scanned pages to a clean Excel/CSV file:

```
(1) Import pages → (2) Annotate layout → (3) Train GPU model → (4) Run inference
→ (5) Correct predictions → (6) Detect table structure → (7) OCR cells
→ (8) LLM cleaning → (9) Validate → (10) Export
```

Two document types are supported:
- **Type A** — tables (statistical yearbooks, census counts): the tool detects a grid layout
- **Type B** — structured text (company registers, directories): the tool extracts fields via LLM

---

## Setup

### 1. Install Python

You need Python 3.10 or later. Check with:
```bash
python --version
```
If you don't have it, download from [python.org](https://www.python.org/downloads/).

### 2. Clone the repository

```bash
git clone https://github.com/attilagaspar/econai-dedust.git
cd econai-dedust
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

This installs the web server, image processing libraries, OCR engines, and SSH tools.

> **Tip:** If you want an isolated environment (recommended), create a virtual environment first:
> ```bash
> python -m venv .venv
> .venv\Scripts\activate      # Windows
> source .venv/bin/activate   # Mac/Linux
> pip install -r requirements.txt
> ```

### 4. Install Tesseract (OCR binary)

Tesseract is an OCR engine that needs to be installed separately from Python.

- **Windows:** Download and run the installer from [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki). During install, tick "Add to PATH".
- **Mac:** `brew install tesseract`
- **Linux:** `sudo apt install tesseract-ocr`

### 5. Start the app

```bash
python econai.py serve
```

This starts a local web server and prints a URL. Open it in your browser (usually `http://localhost:8000`). Keep this terminal open while you work — closing it stops the server.

---

## First-time workflow

### Step 1: Create a project

On the dashboard, click **+ New**. Give it a name (e.g., `machines1935`), choose the document type (A for tables, B for text), and enter the label names that describe what's on the page (e.g., `table header figure`).

### Step 2: Import pages

In the project detail panel, click **Select & Import files…** and choose your PDF or image files. PDFs are automatically split into individual pages. Pages are stored in the project's `annotations/` folder.

### Step 3: Annotate a sample

Click **Open Editor** to open the annotation editor. Draw bounding boxes around the regions you care about (tables, headers, figures) and assign labels. You only need to annotate a sample — typically 20–50 pages is enough to train a good model.

See the [Editor section](#annotation-editor) below for keyboard shortcuts.

### Step 4: Configure the GPU server

In the **GPU Server** card on the dashboard, fill in:
- **Host** — the server's address (e.g., `gpu.university.edu`)
- **User** — your SSH username
- **Key file** — the full path to your SSH private key (e.g., `C:\Users\you\.ssh\id_rsa`)
- **Remote path** — a folder on the server where data will be stored (e.g., `/home/you/econai`)

Click **Save**, then **Test connection** to verify it works.

### Step 5: Set up Docker containers on the server

In the **Docker Containers** card, enter names for the predict and train containers (or leave the defaults). Click **Check status** to see if they already exist. If not, click **Build** — this uploads the Dockerfile and compiles everything on the server (takes 10–30 minutes the first time).

### Step 6: Train the layout model

Click **Prepare training data** (converts your annotations to the format Detectron2 expects), then **Train model**. A live log will stream in a popup. Training typically takes 30–90 minutes.

### Step 7: Run inference

Click **Run inference** to apply the trained model to all your pages. This uploads the images and runs the model in Docker on the GPU server. When done, click **Pull predictions** to download the results.

### Step 8: Correct predictions

Open the editor and check the predicted boxes. Fix any mistakes, adjust boundaries, and assign correct labels. Use **Apply to empty pages** on the dashboard to seed uncorrected pages with the model's predictions before you start.

### Step 9: OCR and LLM cleaning

Once the layout is correct, use the toolbar in the editor to run OCR on each cell (or batch-process all pages). The LLM cleaning step uses the OpenAI API to normalize numbers, fix OCR errors, and extract structured fields. Your OpenAI key goes in the settings panel in the editor.

### Step 10: Export

Click **Export** in the editor toolbar. This generates an `.xlsx` file preserving the table structure, one row per text line in each cell.

---

## Annotation editor

Open with **Open Editor** on the dashboard, or navigate directly to `/static/index.html`.

### Navigation
| Action | How |
|---|---|
| Pan | Click-drag on empty canvas (when not in draw mode) |
| Zoom | Scroll wheel |
| Previous / next page | **M** / **N** |
| Navigate lattice cells | ← → ↑ ↓ arrow keys (when a lattice cell is selected) |
| Toggle edit / review mode | **E** |

### Drawing boxes
| Action | How |
|---|---|
| Draw a box | Click-drag on empty canvas (in draw mode) |
| Draw a full table | Draw the outline, then add column and row separators using the toolbar |
| Select a box | Click it |
| Rubber-band select | Ctrl+drag on empty canvas — selects all boxes in the rectangle |
| Add to selection | Ctrl+click |

### Editing boxes
| Action | How |
|---|---|
| Move | Drag the box |
| Copy to a new position | Right-drag |
| Clone flush-adjacent | Ctrl + arrow key (fills a grid fast) |
| Delete | Delete key |
| Change label | Use the dropdown in the right panel |
| Undo | Ctrl+Z (up to 50 steps) |

### Copy-pasting across pages
| Action | How |
|---|---|
| Copy selection | Ctrl+C |
| Paste (offset by 10px) | Ctrl+V |
| Stamp onto previous page | **P** |
| Stamp 2 pages back | **O** |

Stamping is useful when many pages share the same table structure — annotate one page fully, then stamp to neighbours.

### Lattice (table grid) tools

Once you have a rough layout, the lattice tools let you define the precise grid:

| Tool | What it does |
|---|---|
| **Lattice** | Auto-detect the row/column grid from existing boxes |
| **Show Grid** | Toggle the blue grid overlay |
| **Col sep / Row sep** | Click inside the grid to split a column or row |
| **Del sep** | Click a separator to merge the adjacent columns/rows |
| **Snap** | Snap all cells to the exact grid boundaries |
| **Row fill / Col fill** | Propagate a label across a whole row or column |

### Batch operations

The toolbar has a **Batch** button that lets you run operations (overlap removal, lattice correction, OCR, LLM cleaning) across all pages at once with a live progress log.

---

## GPU server — what actually happens

When you click **Train** or **Infer**, the app:
1. Converts your annotations to COCO JSON format
2. Uploads images, config, and shell scripts to the server via SFTP
3. SSH's into the server, starts the Docker container, and runs the script inside it
4. Streams the live log back to your browser

Training runs detached (with `nohup`), so you can close the browser and it keeps going. Clicking **Train** again while training is running re-attaches to the live log.

### Docker setup

The `Dockerfile` at the repo root defines the GPU container:
- Base: NVIDIA CUDA 12.1 + Ubuntu 22.04
- Includes: PyTorch (cu121), Detectron2 (from source), pdf2image, pymupdf, layoutparser

If the container doesn't exist yet, the **Build** button in the Docker card handles everything: uploads the Dockerfile, builds the image, and creates the container with the right GPU and volume settings.

---

## File structure

```
econai-dedust/
  econai.py              — CLI entry point
  requirements.txt       — Python dependencies for the web app
  Dockerfile             — GPU container definition (for the server)
  app/
    server.py            — FastAPI backend (all API routes)
    docker_config.py     — Docker container name config (saved to docker_config.json)
    pipeline.py          — Pipeline stage machine
    page_import.py       — PDF/image import
    coco_convert.py      — LabelMe → COCO JSON
    infer_layout.py      — Runs on the GPU server
    ssh_ops.py           — SSH/SFTP helpers
    static/
      dashboard.html     — Project dashboard
      index.html         — Annotation editor
      validator.html     — Data lab / batch cleaning
  projects/
    <name>/
      config.json        — Project type, labels, server settings
      pipeline.json      — Current stage
      annotations/       — Page images + LabelMe JSONs  [not in git]
      intermediate/      — COCO JSON, training scripts  [not in git]
      predictions/       — Model output JSONs           [not in git]
      output/            — Exported Excel/CSV files     [not in git]
```

---

## CLI reference

```bash
python econai.py serve [--port 8000]                          # start the web app
python econai.py new-project <name> --type A --labels l1 l2  # create a project
python econai.py list                                          # list all projects
python econai.py status <name>                                 # show pipeline state
python econai.py advance <name>                               # move to next stage
python econai.py set-stage <name> <stage>                     # set stage manually
```

---

## Pipeline stages

**Type A (tables):**
`raw → annotating → training → predicting → correcting → superstructure → ocr → llm_cleaning → validating → exporting → done`

**Type B (structured text):**
same, without the `superstructure` step

---

## Keyboard shortcuts (annotation editor)

### Page navigation
| Key | Action |
|---|---|
| **N** | Next page |
| **M** | Previous page |

### Lattice cell navigation
| Key | Action |
|---|---|
| **→** | Move selection to the cell to the right (same lattice row) |
| **←** | Move selection to the cell to the left (same lattice row) |
| **↓** | Move selection to the cell below (same lattice column) |
| **↑** | Move selection to the cell above (same lattice column) |

Arrow keys only move within the lattice. If no lattice cell is selected, or there is no neighbour in that direction, the key does nothing.

### Editing
| Key | Action |
|---|---|
| **E** | Toggle edit / review mode |
| **A** | Select all shapes on the page |
| **Delete** / **Backspace** | Delete selected shape(s) |
| **Ctrl+Z** | Undo (up to 50 steps) |
| **Ctrl+S** | Save human correction |
| **Ctrl+C** | Copy selected shape(s) |
| **Ctrl+V** | Paste (offset by 10 px) |
| **Ctrl + ←/→/↑/↓** | Clone selected shape flush-adjacent in that direction |

### Stamping across pages
| Key | Action |
|---|---|
| **P** | Stamp selection onto the previous page |
| **O** | Stamp selection 2 pages back |

### Modals & modes
| Key | Action |
|---|---|
| **Escape** | Close open modal / cancel current drawing mode |
