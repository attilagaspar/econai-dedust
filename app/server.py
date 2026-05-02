"""
EconAI FastAPI backend.

Serves LabelMe JSON + images from any folder on disk.
Accepts corrections and writes them back to the JSON.

Run:
  python -m uvicorn app.server:app --reload --port 8000
or via econai.py (coming soon).
"""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List

from app.pipeline import (
    list_projects,
    load_config,
    load_pipeline,
    save_pipeline,
    stages_for,
    project_dir,
    advance_stage,
    set_stage,
)

app = FastAPI(title="EconAI", version="0.1.0")

# Allow the browser (same host, any port) to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the frontend from app/static/
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_folder(folder: str) -> Path:
    """Resolve a folder path that may be absolute or relative to cwd."""
    p = Path(folder)
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {p}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {p}")
    return p


def _find_image(folder: Path, stem: str) -> Optional[Path]:
    for ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
        candidate = folder / (stem + ext)
        if candidate.exists():
            return candidate
    return None


def _page_sort_key(name: str) -> tuple:
    """Sort page_1 < page_2 < ... < page_10 (natural sort)."""
    parts = re.split(r"(\d+)", name)
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)


# ---------------------------------------------------------------------------
# Routes — page listing
# ---------------------------------------------------------------------------

@app.get("/api/pages")
def list_pages(folder: str = Query(..., description="Absolute or relative path to the annotations folder")):
    """Return a sorted list of page stems that have both a JSON and an image."""
    d = _resolve_folder(folder)
    pages = []
    for jf in sorted(d.glob("*.json"), key=lambda p: _page_sort_key(p.stem)):
        img = _find_image(d, jf.stem)
        if img:
            pages.append({
                "stem": jf.stem,
                "json": jf.name,
                "image": img.name,
            })
    return {"folder": str(d), "pages": pages}


# ---------------------------------------------------------------------------
# Routes — serving files
# ---------------------------------------------------------------------------

@app.get("/api/image")
def get_image(folder: str = Query(...), stem: str = Query(...)):
    """Serve the image file for a given page stem."""
    d = _resolve_folder(folder)
    img = _find_image(d, stem)
    if img is None:
        raise HTTPException(status_code=404, detail=f"No image found for '{stem}'")
    return FileResponse(str(img), media_type="image/jpeg")


@app.get("/api/page")
def get_page(folder: str = Query(...), stem: str = Query(...)):
    """Return the LabelMe JSON for a page, with shape indices added."""
    d = _resolve_folder(folder)
    jf = d / f"{stem}.json"
    if not jf.exists():
        raise HTTPException(status_code=404, detail=f"JSON not found: {jf}")

    data = json.loads(jf.read_text(encoding="utf-8"))

    # Inject a stable index into each shape so the frontend can reference them
    for i, shape in enumerate(data.get("shapes", [])):
        shape["_idx"] = i

    return data


# ---------------------------------------------------------------------------
# Routes — saving corrections
# ---------------------------------------------------------------------------

class ShapeUpdate(BaseModel):
    human_corrected_text: Optional[str] = None
    points: Optional[list] = None          # [[x1,y1],[x2,y2]] for box edits
    label: Optional[str] = None            # label rename


@app.patch("/api/page/shape")
def update_shape(
    folder: str = Query(...),
    stem:   str = Query(...),
    idx:    int = Query(..., description="Shape index (_idx from GET /api/page)"),
    body:   ShapeUpdate = ...,
):
    """Patch one shape in the JSON and write the file back."""
    d  = _resolve_folder(folder)
    jf = d / f"{stem}.json"
    if not jf.exists():
        raise HTTPException(status_code=404, detail=f"JSON not found: {jf}")

    data   = json.loads(jf.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])

    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail=f"Shape index {idx} out of range (0–{len(shapes)-1})")

    shape = shapes[idx]

    if body.human_corrected_text is not None:
        if "human_output" not in shape:
            shape["human_output"] = {}
        shape["human_output"]["human_corrected_text"] = body.human_corrected_text

    if body.points is not None:
        shape["points"] = body.points

    if body.label is not None:
        shape["label"] = body.label

    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "idx": idx}


# ---------------------------------------------------------------------------
# Routes — add / delete shapes
# ---------------------------------------------------------------------------

class NewShape(BaseModel):
    label: str
    points: list
    shape_type: str = "rectangle"


@app.post("/api/page/shape")
def add_shape(
    folder: str = Query(...),
    stem:   str = Query(...),
    body:   NewShape = ...,
):
    """Append a new shape to the JSON and return its index."""
    d  = _resolve_folder(folder)
    jf = d / f"{stem}.json"
    if not jf.exists():
        raise HTTPException(status_code=404, detail=f"JSON not found: {jf}")

    data = json.loads(jf.read_text(encoding="utf-8"))
    new_shape = {
        "label":      body.label,
        "points":     body.points,
        "group_id":   None,
        "shape_type": body.shape_type,
        "flags":      {},
    }
    data["shapes"].append(new_shape)
    new_idx = len(data["shapes"]) - 1
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "idx": new_idx}


class ShapesReplace(BaseModel):
    shapes: list


@app.put("/api/page/shapes")
def replace_shapes(
    folder: str = Query(...),
    stem:   str = Query(...),
    body:   ShapesReplace = ...,
):
    """Replace the entire shapes array (used by undo)."""
    d  = _resolve_folder(folder)
    jf = d / f"{stem}.json"
    if not jf.exists():
        raise HTTPException(status_code=404, detail=f"JSON not found: {jf}")
    data = json.loads(jf.read_text(encoding="utf-8"))
    data["shapes"] = body.shapes
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "count": len(body.shapes)}


@app.delete("/api/page/shape")
def delete_shape(
    folder: str = Query(...),
    stem:   str = Query(...),
    idx:    int = Query(...),
):
    """Remove one shape from the JSON by index."""
    d  = _resolve_folder(folder)
    jf = d / f"{stem}.json"
    if not jf.exists():
        raise HTTPException(status_code=404, detail=f"JSON not found: {jf}")

    data   = json.loads(jf.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail=f"Shape index {idx} out of range")

    shapes.pop(idx)
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "remaining": len(shapes)}


# ---------------------------------------------------------------------------
# Routes — cell image crop
# ---------------------------------------------------------------------------

@app.get("/api/cell")
def get_cell(
    folder: str = Query(...),
    stem:   str = Query(...),
    idx:    int = Query(...),
    pad:    int = Query(4, description="Padding in pixels"),
):
    """Return a cropped image of a single cell (for the right panel zoom)."""
    from PIL import Image
    import io
    from fastapi.responses import StreamingResponse

    d   = _resolve_folder(folder)
    jf  = d / f"{stem}.json"
    img_path = _find_image(d, stem)

    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if img_path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    data   = json.loads(jf.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail="Shape index out of range")

    pts = shapes[idx]["points"]
    xs  = [p[0] for p in pts]
    ys  = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

    img = Image.open(str(img_path))
    w, h = img.size
    crop = img.crop((
        max(0, int(x1) - pad),
        max(0, int(y1) - pad),
        min(w, int(x2) + pad),
        min(h, int(y2) + pad),
    ))

    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/jpeg")


@app.post("/api/page/shape/ocr")
def api_ocr_cell(
    folder: str = Query(...),
    stem:   str = Query(...),
    idx:    int = Query(...),
    lang:   str = Query("hun", description="Tesseract language code"),
):
    """Run Tesseract OCR on a single cell and store the result in the page JSON."""
    import pytesseract
    from PIL import Image, ImageOps
    # Ensure Tesseract binary is found on Windows even if not on PATH
    import shutil
    if not shutil.which("tesseract"):
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    d        = _resolve_folder(folder)
    jf       = d / f"{stem}.json"
    img_path = _find_image(d, stem)

    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if img_path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    data   = json.loads(jf.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail="Shape index out of range")

    pts = shapes[idx]["points"]
    xs  = [p[0] for p in pts]
    ys  = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

    img = Image.open(str(img_path)).convert("RGB")
    w, h = img.size
    pad  = 4
    crop = img.crop((
        max(0, int(x1) - pad), max(0, int(y1) - pad),
        min(w, int(x2) + pad), min(h, int(y2) + pad),
    ))

    # Light preprocessing: greyscale + auto-contrast
    crop_grey = ImageOps.autocontrast(crop.convert("L"))

    tess = pytesseract.image_to_data(
        crop_grey, lang=lang,
        config="--psm 4",          # single column, preserves line breaks
        output_type=pytesseract.Output.DICT,
    )

    # Group confident words by (block, paragraph, line) to preserve layout.
    # Also track the minimum top-coordinate of each line so we can sort by
    # actual pixel position (Tesseract block_num order ≠ visual top-to-bottom order).
    from collections import defaultdict
    line_words: dict = defaultdict(list)
    line_top:   dict = defaultdict(lambda: float("inf"))
    conf_values: list = []
    for txt, conf, blk, par, ln, top in zip(
        tess["text"], tess["conf"],
        tess["block_num"], tess["par_num"], tess["line_num"],
        tess["top"],
    ):
        c = int(conf)
        if txt.strip() and c > 0:
            key = (blk, par, ln)
            line_words[key].append(txt)
            line_top[key] = min(line_top[key], int(top))
            conf_values.append(c)

    # Sort lines by their top pixel coordinate (visual reading order)
    sorted_keys = sorted(line_words.keys(), key=lambda k: line_top[k])
    lines = [" ".join(line_words[k]) for k in sorted_keys]
    ocr_text  = "\n".join(lines).strip()
    mean_conf = round(sum(conf_values) / len(conf_values), 1) if conf_values else 0.0

    shapes[idx]["tesseract_output"] = {
        "ocr_text":  ocr_text,
        "mean_conf": mean_conf,
        "lang":      lang,
    }
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"ocr_text": ocr_text, "mean_conf": mean_conf}


# ---------------------------------------------------------------------------
# Routes — LLM cleaner
# ---------------------------------------------------------------------------

class LlmRequest(BaseModel):
    prompt: str


@app.post("/api/page/shape/llm")
def api_llm_cell(
    folder: str = Query(...),
    stem:   str = Query(...),
    idx:    int = Query(...),
    model:  str = Query("gpt-4o-mini"),
    mode:   str = Query("image", description="image | image+ocr | ocr | linebyline"),
    body:   LlmRequest = ...,
):
    """Send a cell to OpenAI and store the result in the page JSON."""
    import os, base64, io, datetime
    from openai import OpenAI
    from PIL import Image as PILImage

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY environment variable is not set")

    d        = _resolve_folder(folder)
    jf       = d / f"{stem}.json"
    img_path = _find_image(d, stem)

    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if img_path is None and mode in ("image", "image+ocr"):
        raise HTTPException(status_code=404, detail="Image not found")

    data   = json.loads(jf.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail="Shape index out of range")

    shape = shapes[idx]
    pts   = shape["points"]
    xs    = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

    # Build the message content
    content: list = []

    if mode in ("image", "image+ocr"):
        img = PILImage.open(str(img_path)).convert("RGB")
        w, h = img.size
        pad  = 4
        crop = img.crop((
            max(0, int(x1) - pad), max(0, int(y1) - pad),
            min(w, int(x2) + pad), min(h, int(y2) + pad),
        ))
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=92)
        b64 = base64.b64encode(buf.getvalue()).decode()
        content.append({
            "type":      "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
        })

    prompt_text = body.prompt
    if mode == "image+ocr":
        ocr_text = shape.get("tesseract_output", {}).get("ocr_text", "")
        if ocr_text:
            prompt_text = f"{body.prompt}\n\nOCR text:\n{ocr_text}"
    elif mode == "ocr":
        ocr_text = shape.get("tesseract_output", {}).get("ocr_text", "")
        prompt_text = f"{body.prompt}\n\n{ocr_text}"

    content.append({"type": "text", "text": prompt_text})

    # For text-only mode, skip the content list and use a plain string
    if mode == "ocr":
        messages = [{"role": "user", "content": prompt_text}]
    else:
        messages = [{"role": "user", "content": content}]

    try:
        client   = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model, messages=messages, max_tokens=1024,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    result    = response.choices[0].message.content.strip()
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"

    shape["openai_output"] = {
        "response":  result,
        "model":     model,
        "mode":      mode,
        "timestamp": timestamp,
    }
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"response": result, "model": model, "mode": mode, "timestamp": timestamp}


# ---------------------------------------------------------------------------
# Routes — project management
# ---------------------------------------------------------------------------

@app.get("/api/projects")
def api_list_projects():
    return {"projects": list_projects()}


@app.get("/api/project/{name}")
def api_get_project(name: str):
    try:
        cfg   = load_config(name)
        state = load_pipeline(name)
        pdir  = project_dir(name)
        ann   = pdir / "annotations"
        n_json = len(list(ann.glob("*.json"))) if ann.exists() else 0
        n_img  = len(list(ann.glob("*.png")) + list(ann.glob("*.jpg"))) if ann.exists() else 0
        return {
            "config":  cfg,
            "pipeline": state,
            "stats":   {"json": n_json, "images": n_img},
            "stages":  stages_for(cfg["type"]),
            "annotations_path": str(ann),
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


class ConfigUpdate(BaseModel):
    server: Optional[dict] = None
    llm:    Optional[dict] = None
    labels: Optional[list] = None


@app.patch("/api/project/{name}/config")
def api_update_config(name: str, body: ConfigUpdate):
    try:
        cfg  = load_config(name)
        pdir = project_dir(name)
        if body.server is not None:
            cfg["server"] = {**cfg.get("server", {}), **body.server}
        if body.llm is not None:
            cfg["llm"] = {**cfg.get("llm", {}), **body.llm}
        if body.labels is not None:
            cfg["labels"] = body.labels
        (pdir / "config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {"ok": True, "config": cfg}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


class NewProject(BaseModel):
    name:   str
    type:   str = "A"
    labels: list = []

@app.post("/api/project/new")
def api_new_project(body: NewProject):
    from app.pipeline import create_project
    try:
        pdir = create_project(body.name, body.type, body.labels)
        return {"ok": True, "path": str(pdir)}
    except (FileExistsError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/project/{name}/advance")
def api_advance(name: str):
    try:
        new_stage = advance_stage(name)
        return {"ok": True, "stage": new_stage}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/project/{name}/set-stage")
def api_set_stage(name: str, stage: str = Query(...)):
    try:
        set_stage(name, stage)
        return {"ok": True, "stage": stage}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Routes — SSH / server operations
# ---------------------------------------------------------------------------

def _server_cfg(name: str) -> dict:
    cfg = load_config(name)
    srv = cfg.get("server", {})
    if not srv.get("host"):
        raise HTTPException(status_code=400, detail="Server host not configured")
    if not srv.get("user"):
        raise HTTPException(status_code=400, detail="Server user not configured")
    if not srv.get("key_path"):
        raise HTTPException(status_code=400, detail="Server key_path not configured")
    return srv


class SshRequest(BaseModel):
    passphrase: Optional[str] = None

class SshPullRequest(BaseModel):
    passphrase: Optional[str] = None
    subfolder:  str = "annotations"

class JobSubmit(BaseModel):
    command:    str
    passphrase: Optional[str] = None


@app.post("/api/project/{name}/server/test")
def api_server_test(name: str, body: SshRequest = SshRequest()):
    from app import ssh_ops
    try:
        srv = _server_cfg(name)
        return ssh_ops.test_connection(srv["host"], srv["user"], srv["key_path"], body.passphrase)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/server/push")
def api_server_push(name: str, body: SshRequest = SshRequest()):
    """Upload the project's annotations/ folder to the server."""
    from app import ssh_ops
    try:
        srv  = _server_cfg(name)
        pdir = project_dir(name)
        local_ann  = pdir / "annotations"
        remote_ann = posixpath.join(srv["remote_path"], name, "annotations")
        if not local_ann.exists():
            raise HTTPException(status_code=400, detail="annotations/ folder does not exist")
        return ssh_ops.push_folder(
            srv["host"], srv["user"], srv["key_path"],
            local_ann, remote_ann, body.passphrase,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/server/pull")
def api_server_pull(name: str, body: SshPullRequest = SshPullRequest()):
    """Download a subfolder (default: annotations/) back from the server."""
    from app import ssh_ops
    try:
        srv  = _server_cfg(name)
        pdir = project_dir(name)
        remote = posixpath.join(srv["remote_path"], name, body.subfolder)
        local  = pdir / body.subfolder
        return ssh_ops.pull_folder(
            srv["host"], srv["user"], srv["key_path"],
            remote, local, body.passphrase,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/job/submit")
def api_job_submit(name: str, body: JobSubmit):
    """Submit an arbitrary shell command as a background job on the server."""
    from app import ssh_ops
    import time as _time
    try:
        srv      = _server_cfg(name)
        log_path = posixpath.join(
            srv["remote_path"], name, f"job_{int(_time.time())}.log"
        )
        result = ssh_ops.submit_job(
            srv["host"], srv["user"], srv["key_path"],
            body.command, log_path, body.passphrase,
        )
        if result["ok"]:
            state = load_pipeline(name)
            state["job"] = {"pid": result["pid"], "log_path": log_path,
                            "command": body.command}
            save_pipeline(name, state)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/project/{name}/job/status")
def api_job_status(name: str, passphrase: Optional[str] = Query(None)):
    """Poll the status of the last submitted background job."""
    from app import ssh_ops
    try:
        srv   = _server_cfg(name)
        state = load_pipeline(name)
        job   = state.get("job")
        if not job:
            return {"ok": True, "running": False, "log_tail": "(no job submitted yet)"}
        return ssh_ops.job_status(
            srv["host"], srv["user"], srv["key_path"],
            job.get("pid"), job["log_path"], passphrase,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Routes — page import
# ---------------------------------------------------------------------------

class ImportRequest(BaseModel):
    source_path: str  # local folder or single file path

@app.post("/api/project/{name}/import")
def api_import_pages(name: str, body: ImportRequest):
    """Import PDFs/images from a local path directly — no upload needed."""
    from app.page_import import import_from_path
    try:
        pdir    = project_dir(name)
        ann_dir = pdir / "annotations"
        results = import_from_path(Path(body.source_path), ann_dir)
        return {"ok": True, "pages": results, "total": len(results)}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())


@app.post("/api/project/{name}/upload-pages")
async def api_upload_pages(name: str, files: List[UploadFile] = File(...)):
    """Accept uploaded PDF/image files, convert them and add to the project's annotations/ folder."""
    import tempfile
    from app.page_import import _import_pdf, _save_image_file, _sanitize, IMAGE_EXTS

    try:
        pdir    = project_dir(name)
        ann_dir = pdir / "annotations"
        ann_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for uf in files:
            filename  = uf.filename or "upload"
            orig_stem = Path(filename).stem
            ext       = Path(filename).suffix.lower()

            # Read file content and write to a temp file
            content = await uf.read()
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = Path(tmp.name)

                if ext == ".pdf":
                    base  = _sanitize(orig_stem)
                    pages = _import_pdf(tmp_path, ann_dir, base=base)
                    results.extend(pages)
                elif ext in IMAGE_EXTS:
                    stem = _sanitize(orig_stem)
                    info = _save_image_file(tmp_path, ann_dir, stem)
                    results.append(info)
                else:
                    results.append({"stem": filename, "error": f"Unsupported extension: {ext}"})
            except Exception as e:
                import traceback
                results.append({"stem": filename, "error": traceback.format_exc()})
            finally:
                if tmp_path and tmp_path.exists():
                    tmp_path.unlink()

        return {"ok": True, "pages": results, "total": len(results)}
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())


# ---------------------------------------------------------------------------
# Routes — prepare training data (LabelMe → COCO)
# ---------------------------------------------------------------------------

BASE_YAML = Path(__file__).parent.parent / "samples" / "ertesito2" / "fast_rcnn_R_50_FPN_3x.yaml"

@app.post("/api/project/{name}/prepare")
def api_prepare(name: str):
    """Convert annotated LabelMe JSONs → COCO JSON + generate training scripts."""
    from app.coco_convert import prepare_training_data
    try:
        cfg  = load_config(name)
        pdir = project_dir(name)
        result = prepare_training_data(
            project_name    = name,
            ann_dir         = pdir / "annotations",
            labels          = cfg["labels"],
            intermediate_dir= pdir / "intermediate",
            base_yaml_path  = BASE_YAML,
        )
        return {"ok": True, **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Routes — train / infer with SSE streaming
# ---------------------------------------------------------------------------

class TrainRequest(BaseModel):
    passphrase: Optional[str] = None

class InferRequest(BaseModel):
    passphrase:          Optional[str] = None
    skip_image_upload:   bool = False


_SSE_DONE = object()


def _safe_next(it):
    """Return next item, or _SSE_DONE sentinel — never raises StopIteration."""
    try:
        return next(it)
    except StopIteration:
        return _SSE_DONE


async def _sse_stream(gen):
    """Wrap a sync generator as an SSE response."""
    import asyncio
    import json as _json

    async def event_gen():
        loop = asyncio.get_event_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            it = iter(gen)
            while True:
                try:
                    line = await loop.run_in_executor(pool, _safe_next, it)
                    if line is _SSE_DONE:
                        yield "data: {\"done\": true}\n\n"
                        break
                    payload = _json.dumps({"line": line.rstrip("\n")})
                    yield f"data: {payload}\n\n"
                except Exception as e:
                    yield f"data: {_json.dumps({'error': str(e)})}\n\n"
                    break

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _push_training_data_gen(name: str, srv: dict, passphrase: str):
    """Generator: push images + COCO JSON + config + scripts, yielding progress lines."""
    from app import ssh_ops
    pdir   = project_dir(name)
    inter  = pdir / "intermediate"
    remote = srv["remote_path"].rstrip("/")

    yield f"[push] remote_path = {remote}"
    yield f"[push] Connecting to {srv['host']} as {srv['user']}..."
    c = ssh_ops._client(srv["host"], srv["user"], srv["key_path"], passphrase)
    sftp = c.open_sftp()
    try:
        dirs = [
            f"{remote}/{name}/images",
            f"{remote}/layout-model-training/configs/{name}",
            f"{remote}/layout-model-training/tools",
            f"{remote}/layout-model-training/scripts",
        ]
        for d in dirs:
            yield f"[push] mkdir -p {d}"
            ssh_ops._sftp_mkdir_p(sftp, d)

        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
        images = [p for p in (pdir / "annotations").iterdir()
                  if p.suffix.lower() in IMAGE_EXTS]
        total = len(images)
        yield f"[push] Uploading {total} image(s) → {remote}/{name}/images/"
        for i, img in enumerate(images, 1):
            dest = f"{remote}/{name}/images/{img.name}"
            sftp.put(str(img), dest)
            if i % 5 == 0 or i == total:
                yield f"[push]   {i}/{total}  {img.name} → {dest}"

        dest = f"{remote}/{name}/annotations.json"
        yield f"[push] {inter/'annotations.json'} → {dest}"
        sftp.put(str(inter / "annotations.json"), dest)

        cfg_local = inter / "configs" / name / "fast_rcnn_R_50_FPN_3x.yaml"
        dest = f"{remote}/layout-model-training/configs/{name}/fast_rcnn_R_50_FPN_3x.yaml"
        yield f"[push] {cfg_local} → {dest}"
        sftp.put(str(cfg_local), dest)

        dest = f"{remote}/layout-model-training/tools/infer_layout.py"
        yield f"[push] infer_layout.py → {dest}"
        sftp.put(str(Path(__file__).parent / "infer_layout.py"), dest)

        def sftp_put_lf(local: Path, remote_dest: str):
            """Upload a text file with LF line endings (strip Windows CRLF)."""
            content = local.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            with sftp.open(remote_dest, "wb") as fh:
                fh.write(content)

        dest_train = f"{remote}/layout-model-training/scripts/{name}.sh"
        dest_infer = f"{remote}/layout-model-training/scripts/{name}_infer.sh"
        yield f"[push] train.sh → {dest_train}"
        sftp_put_lf(inter / "train.sh", dest_train)
        yield f"[push] infer.sh → {dest_infer}"
        sftp_put_lf(inter / "infer.sh", dest_infer)

        yield "[push] All files uploaded successfully."
        yield f"[push] Verifying: ls {remote}/layout-model-training/scripts/"
        _, stdout, _ = c.exec_command(f"ls -1 {remote}/layout-model-training/scripts/")
        for ln in stdout.read().decode().splitlines():
            yield f"[push]   {ln}"
    finally:
        sftp.close()
        c.close()


@app.post("/api/project/{name}/train")
async def api_train(name: str, body: TrainRequest = TrainRequest()):
    """Push data to server then run training inside Docker. Streams log via SSE."""
    from app import ssh_ops

    try:
        cfg = load_config(name)
        srv = _server_cfg(name)
        passphrase = body.passphrase
        remote = srv["remote_path"].rstrip("/")

        # Ensure intermediate data is prepared
        pdir  = project_dir(name)
        inter = pdir / "intermediate"
        if not (inter / "annotations.json").exists():
            raise HTTPException(status_code=400,
                                detail="Run 'Prepare training data' first")

        # Build docker command
        docker_cmd = (
            f"docker start detectron_training_container && "
            f"docker exec detectron_training_container "
            f"bash /workspace/layout-model-training/scripts/{name}.sh"
        )

        def full_gen():
            yield from _push_training_data_gen(name, srv, passphrase)
            yield "[push] Starting Docker training..."
            yield from ssh_ops.stream_command(srv["host"], srv["user"],
                                              srv["key_path"], docker_cmd, passphrase)

        return await _sse_stream(full_gen())

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _push_infer_data_gen(name: str, srv: dict, passphrase: str, skip_images: bool = False):
    """Generator: push images + scripts + config for inference (no annotations.json needed).

    Uses predict_remote_path (if set) as the Docker-container root for placing
    the infer script — necessary when the predict container mounts a parent
    directory compared to the training container.
    skip_images=True skips the (slow) image upload and only pushes config/scripts.
    """
    from app import ssh_ops
    import posixpath as pp
    pdir         = project_dir(name)
    inter        = pdir / "intermediate"
    remote       = srv["remote_path"].rstrip("/")        # where data lives (training ws root on host)
    predict_root = srv.get("predict_remote_path", remote).rstrip("/")  # predict container ws root on host

    # Derive the prefix that maps from predict /workspace to the data directory
    # e.g. remote=/home/.../koren, predict_root=/home/.../econai → prefix=koren
    if remote.startswith(predict_root + "/"):
        ws_prefix = remote[len(predict_root) + 1:]  # e.g. "koren"
    else:
        ws_prefix = ""

    yield f"[push] remote_path (data)    = {remote}"
    yield f"[push] predict_remote_path   = {predict_root}"
    yield f"[push] container ws prefix   = '{ws_prefix}'"
    yield f"[push] Connecting to {srv['host']} as {srv['user']}..."
    c = ssh_ops._client(srv["host"], srv["user"], srv["key_path"], passphrase)
    sftp = c.open_sftp()
    try:
        # All paths that must be visible inside the predict container go under predict_root.
        # Images live under remote (data workspace, mounted into both containers).
        dirs = [
            f"{remote}/{name}/images",
            f"{predict_root}/layout-model-training/configs/{name}",
            f"{predict_root}/layout-model-training/tools",
            f"{predict_root}/layout-model-training/scripts",
        ]
        for d in dirs:
            yield f"[push] mkdir -p {d}"
            ssh_ops._sftp_mkdir_p(sftp, d)

        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
        if skip_images:
            images = [p for p in (pdir / "annotations").iterdir()
                      if p.suffix.lower() in IMAGE_EXTS]
            yield f"[push] Skipping image upload ({len(images)} images already on server)."
        else:
            images = [p for p in (pdir / "annotations").iterdir()
                      if p.suffix.lower() in IMAGE_EXTS]
            total = len(images)
            yield f"[push] Uploading {total} image(s) → {remote}/{name}/images/"
            for i, img in enumerate(images, 1):
                dest = f"{remote}/{name}/images/{img.name}"
                sftp.put(str(img), dest)
                if i % 10 == 0 or i == total:
                    yield f"[push]   {i}/{total}  {img.name}"

        cfg_local = inter / "configs" / name / "fast_rcnn_R_50_FPN_3x.yaml"
        dest = f"{predict_root}/layout-model-training/configs/{name}/fast_rcnn_R_50_FPN_3x.yaml"
        yield f"[push] config YAML → {dest}"
        sftp.put(str(cfg_local), dest)

        dest = f"{predict_root}/layout-model-training/tools/infer_layout.py"
        yield f"[push] infer_layout.py → {dest}"
        sftp.put(str(Path(__file__).parent / "infer_layout.py"), dest)

        def sftp_put_lf(local: Path, remote_dest: str):
            content = local.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            with sftp.open(remote_dest, "wb") as fh:
                fh.write(content)

        def sftp_put_infer_sh(local: Path, remote_dest: str):
            """Upload infer.sh, patching data paths to include ws_prefix if needed.
            The script is generated with /workspace as root, but the predict container
            maps predict_root → /workspace.  Data (images, weights, predictions) lives
            under remote = predict_root/ws_prefix, so those paths need the extra prefix.
            Tool/config/script paths stay as-is (already uploaded under predict_root).
            """
            content = local.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            if ws_prefix:
                pfx = ws_prefix.encode()
                # data paths: images, predictions, model weights
                content = (content
                    .replace(f"/workspace/{name}/".encode(),
                             f"/workspace/{pfx.decode()}/{name}/".encode())
                    .replace(b"/workspace/layout-model-training/outputs/",
                             f"/workspace/{pfx.decode()}/layout-model-training/outputs/".encode()))
            with sftp.open(remote_dest, "wb") as fh:
                fh.write(content)

        dest_infer = f"{predict_root}/layout-model-training/scripts/{name}_infer.sh"
        yield f"[push] infer.sh → {dest_infer}" + (f" (ws_prefix='{ws_prefix}')" if ws_prefix else "")
        sftp_put_infer_sh(inter / "infer.sh", dest_infer)

        yield "[push] All files uploaded successfully."
        yield f"[push] Verifying scripts dir ({predict_root}/layout-model-training/scripts/):"
        _, stdout, _ = c.exec_command(f"ls -la {predict_root}/layout-model-training/scripts/")
        for ln in stdout.read().decode().splitlines():
            yield f"[push]   {ln}"
    finally:
        sftp.close()
        c.close()


@app.post("/api/project/{name}/infer")
async def api_infer(name: str, body: InferRequest = InferRequest()):
    """Run inference on the server. Streams log via SSE."""
    from app import ssh_ops
    try:
        srv        = _server_cfg(name)
        passphrase = body.passphrase
        remote     = srv["remote_path"].rstrip("/")

        docker_cmd = (
            f"docker start detectron_predicting_container && "
            f"docker exec detectron_predicting_container "
            f"bash /workspace/layout-model-training/scripts/{name}_infer.sh"
        )

        def full_gen():
            yield from _push_infer_data_gen(name, srv, passphrase,
                                            skip_images=body.skip_image_upload)
            yield "[push] Starting Docker inference..."
            yield from ssh_ops.stream_command(srv["host"], srv["user"],
                                              srv["key_path"], docker_cmd, passphrase)

        return await _sse_stream(full_gen())

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/pull-predictions")
def api_pull_predictions(name: str, body: TrainRequest = TrainRequest()):
    """Pull predicted JSONs from server into local predictions/ folder (never touches annotations/)."""
    from app import ssh_ops
    try:
        srv       = _server_cfg(name)
        pdir      = project_dir(name)
        remote    = srv["remote_path"].rstrip("/")
        pred_dir  = pdir / "predictions"
        pred_dir.mkdir(parents=True, exist_ok=True)
        result = ssh_ops.pull_folder(
            srv["host"], srv["user"], srv["key_path"],
            f"{remote}/{name}/predictions",
            pred_dir,
            body.passphrase,
        )
        return {**result, "local_path": str(pred_dir)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/apply-predictions")
def api_apply_predictions(name: str):
    """Copy predicted shapes into annotation JSONs that have no shapes yet."""
    import json as _json
    pdir     = project_dir(name)
    pred_dir = pdir / "predictions"
    ann_dir  = pdir / "annotations"
    if not pred_dir.exists():
        raise HTTPException(status_code=400, detail="No predictions pulled yet")
    applied = skipped = 0
    for pred_file in pred_dir.glob("*.json"):
        ann_file = ann_dir / pred_file.name
        if not ann_file.exists():
            continue
        ann = _json.loads(ann_file.read_text(encoding="utf-8"))
        if ann.get("shapes"):  # already has hand annotations — skip
            skipped += 1
            continue
        pred = _json.loads(pred_file.read_text(encoding="utf-8"))
        ann["shapes"] = pred.get("shapes", [])
        ann_file.write_text(_json.dumps(ann, indent=2, ensure_ascii=False), encoding="utf-8")
        applied += 1
    return {"applied": applied, "skipped_had_annotations": skipped}


# ---------------------------------------------------------------------------
# Routes — perspective correction
# ---------------------------------------------------------------------------

class PerspectiveRequest(BaseModel):
    folder: str
    stem:   str
    points: list   # [[x,y], [x,y], [x,y], [x,y]] — four corners in any order
    save:   bool = False


@app.post("/api/page/perspective")
def api_perspective(body: PerspectiveRequest):
    """
    Apply perspective (trapezoid→rectangle) correction to a page image.
    save=False → return base64 JPEG preview.
    save=True  → overwrite image on disk, clear shapes, update JSON dimensions.
    """
    import base64, io, math
    import numpy as np
    from PIL import Image

    d        = _resolve_folder(body.folder)
    img_path = _find_image(d, body.stem)
    jf       = d / f"{body.stem}.json"

    if img_path is None:
        raise HTTPException(status_code=404, detail="Image not found")
    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if len(body.points) != 4:
        raise HTTPException(status_code=400, detail="Exactly 4 points required")

    pts = [tuple(float(v) for v in p) for p in body.points]

    # Sort corners into TL, TR, BR, BL regardless of click order.
    # Strategy: top 2 (smallest y) sorted by x → TL, TR;
    #           bottom 2 (largest y) sorted by x → BL, BR.
    by_y = sorted(pts, key=lambda p: p[1])
    top    = sorted(by_y[:2], key=lambda p: p[0])
    bottom = sorted(by_y[2:], key=lambda p: p[0])
    tl, tr = top[0],    top[1]
    bl, br = bottom[0], bottom[1]

    # Output width = max of top & bottom edge; height = max of left & right edge
    w_top  = math.dist(tl, tr)
    w_bot  = math.dist(bl, br)
    h_left = math.dist(tl, bl)
    h_right= math.dist(tr, br)
    dst_w  = int(max(w_top, w_bot))
    dst_h  = int(max(h_left, h_right))

    if dst_w < 2 or dst_h < 2:
        raise HTTPException(status_code=400, detail="Degenerate quadrilateral")

    src = np.float32([tl, tr, br, bl])
    dst = np.float32([[0, 0], [dst_w, 0], [dst_w, dst_h], [0, dst_h]])

    # Solve perspective transform with numpy (no OpenCV needed)
    def _perspective_coeffs(src_pts, dst_pts):
        A = []
        for (sx, sy), (dx, dy) in zip(src_pts, dst_pts):
            A.append([sx, sy, 1, 0, 0, 0, -dx*sx, -dx*sy])
            A.append([0, 0, 0, sx, sy, 1, -dy*sx, -dy*sy])
        A = np.array(A, dtype=np.float64)
        b = dst_pts.flatten().astype(np.float64)
        coeffs, *_ = np.linalg.lstsq(A, b, rcond=None)
        return coeffs.tolist() + [1.0]

    # PIL PERSPECTIVE uses the inverse transform (dst→src)
    coeffs = _perspective_coeffs(dst, src)

    try:
        img = Image.open(str(img_path)).convert("RGB")
        out = img.transform((dst_w, dst_h), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    except Exception as exc:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())

    if body.save:
        fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG",
               "tif": "TIFF", "tiff": "TIFF"}.get(img_path.suffix.lstrip(".").lower(), "JPEG")
        save_kw = {"quality": 92} if fmt == "JPEG" else {}
        out.save(str(img_path), format=fmt, **save_kw)
        data = json.loads(jf.read_text(encoding="utf-8"))
        data["shapes"] = []
        data["imageWidth"]  = dst_w
        data["imageHeight"] = dst_h
        jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "width": dst_w, "height": dst_h}
    else:
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=88)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        return {"ok": True, "preview": b64, "width": dst_w, "height": dst_h}


# ---------------------------------------------------------------------------
# Root — redirect to dashboard
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/dashboard.html")
