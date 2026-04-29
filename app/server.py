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

# Keep uploaded files in memory (avoid Windows temp-file deletion errors)
from starlette.datastructures import UploadFile as _UploadFile
_UploadFile.spool_max_size = 256 * 1024 * 1024  # 256 MB

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
                    line = await loop.run_in_executor(pool, next, it)
                    payload = _json.dumps({"line": line.rstrip("\n")})
                    yield f"data: {payload}\n\n"
                except StopIteration:
                    yield "data: {\"done\": true}\n\n"
                    break
                except Exception as e:
                    yield f"data: {_json.dumps({'error': str(e)})}\n\n"
                    break

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _push_training_data(name: str, srv: dict, passphrase: str):
    """Push images + COCO JSON + config + scripts to the server."""
    from app import ssh_ops
    pdir   = project_dir(name)
    inter  = pdir / "intermediate"
    remote = srv["remote_path"].rstrip("/")

    results = {}

    # Push images
    r = ssh_ops.push_folder(srv["host"], srv["user"], srv["key_path"],
                            pdir / "annotations",
                            f"{remote}/{name}/images", passphrase)
    results["push_images"] = r
    if not r["ok"]:
        return results

    # Push COCO JSON
    r2 = ssh_ops.run_command(srv["host"], srv["user"], srv["key_path"],
                             f"mkdir -p {remote}/{name}", passphrase)
    import paramiko, io as _io
    c = ssh_ops._client(srv["host"], srv["user"], srv["key_path"], passphrase)
    sftp = c.open_sftp()
    sftp.put(str(inter / "annotations.json"),
             f"{remote}/{name}/annotations.json")

    # Push config yaml
    cfg_local = inter / "configs" / name / "fast_rcnn_R_50_FPN_3x.yaml"
    ssh_ops._sftp_mkdir_p(sftp, f"{remote}/layout-model-training/configs/{name}")
    sftp.put(str(cfg_local),
             f"{remote}/layout-model-training/configs/{name}/fast_rcnn_R_50_FPN_3x.yaml")

    # Push infer_layout.py script
    infer_script = Path(__file__).parent / "infer_layout.py"
    sftp.put(str(infer_script),
             f"{remote}/layout-model-training/tools/infer_layout.py")

    # Push training script
    ssh_ops._sftp_mkdir_p(sftp, f"{remote}/layout-model-training/scripts")
    sftp.put(str(inter / "train.sh"),
             f"{remote}/layout-model-training/scripts/{name}.sh")
    sftp.put(str(inter / "infer.sh"),
             f"{remote}/layout-model-training/scripts/{name}_infer.sh")

    sftp.close()
    c.close()
    results["push_data"] = {"ok": True}
    return results


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

        # Push everything to server
        push_results = _push_training_data(name, srv, passphrase)
        for k, v in push_results.items():
            if not v.get("ok"):
                raise HTTPException(status_code=500,
                                    detail=f"Push failed ({k}): {v.get('error')}")

        # Build docker command
        docker_cmd = (
            f"docker start detectron_training_container && "
            f"docker exec detectron_training_container "
            f"bash /workspace/layout-model-training/scripts/{name}.sh"
        )

        gen = ssh_ops.stream_command(srv["host"], srv["user"],
                                     srv["key_path"], docker_cmd, passphrase)
        return await _sse_stream(gen)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/infer")
async def api_infer(name: str, body: TrainRequest = TrainRequest()):
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

        gen = ssh_ops.stream_command(srv["host"], srv["user"],
                                     srv["key_path"], docker_cmd, passphrase)
        return await _sse_stream(gen)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/pull-predictions")
def api_pull_predictions(name: str, body: TrainRequest = TrainRequest()):
    """Pull predicted JSONs from server back to local annotations/ folder."""
    from app import ssh_ops
    try:
        srv    = _server_cfg(name)
        pdir   = project_dir(name)
        remote = srv["remote_path"].rstrip("/")
        result = ssh_ops.pull_folder(
            srv["host"], srv["user"], srv["key_path"],
            f"{remote}/{name}/predictions",
            pdir / "annotations",
            body.passphrase,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Root — redirect to dashboard
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/dashboard.html")
