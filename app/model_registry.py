"""The Models view's data: what trained models sit on a GPU server.

A model is a project's output folder on the host,
`<remote_path>/layout-model-training/outputs/<name>/fast_rcnn_R_50_FPN_3x/`
with `model_final.pth` (the weights) and `metrics.json` (Detectron2's log,
whose last `bbox/AP` line is the model's own evaluation). One SSH command
lists them all together with the GPU's state and the Dedust containers;
`parse_listing` turns its output into records, and the server adds what it
knows locally (the project, its trained classes, its training ledger).

The same listing reports what else Dedust has put on the host, by project
(the `@@WS` rows): the page images pushed for training or inference, the
predicted JSONs, the parent files, the scoring runs (`eval/`), the model's
output folder and its configs / scripts — with sizes, so the Models view
can show the server's storage and remove any of it (`remove_script`).
Nothing on the host is ever removed otherwise: deleting a project in
Dedust is local, and every push and inference adds files.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

# Markers the listing script prints between its sections.
_SCRIPT = r'''
R="__REMOTE__/layout-model-training"
echo "@@GPU"; nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null
echo "@@PROCS"; nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null
echo "@@CONTAINERS"; docker ps -a --format '{{.Names}}|{{.Status}}' 2>/dev/null | grep -E '^(__TRAIN__|__PREDICT__)\|'
echo "@@DISK"; df -k --output=avail,pcent "$R" 2>/dev/null | tail -1
echo "@@MODELS"
for d in "$R"/outputs/*/; do
  [ -d "$d" ] || continue
  n=$(basename "$d"); w="$d/fast_rcnn_R_50_FPN_3x/model_final.pth"; m="$d/fast_rcnn_R_50_FPN_3x/metrics.json"
  if [ -f "$w" ]; then
    echo "@@MODEL $n|$(stat -c %Y "$w")|$(stat -c %s "$w")|$(du -sk "$d" 2>/dev/null | cut -f1)|$(stat -c %Y "$m" 2>/dev/null)"
    [ -f "$m" ] && tail -c 40000 "$m" | grep 'bbox/AP' | tail -1
  else
    echo "@@PARTIAL $n|$(du -sk "$d" 2>/dev/null | cut -f1)"
  fi
done
echo "@@WORKSPACE"
W="__REMOTE__"
kb() { if [ -e "$1" ]; then du -sk "$1" 2>/dev/null | cut -f1; else echo 0; fi; }
nf() { if [ -d "$1" ]; then find "$1" -maxdepth 1 -type f 2>/dev/null | wc -l; else echo 0; fi; }
{ for d in "$W"/*/ "$R"/outputs/*/ "$R"/configs/*/; do [ -d "$d" ] && basename "$d"; done; } | sort -u | while read -r n; do
  [ "$n" = "layout-model-training" ] && continue
  p="$W/$n"
  sc=$(ls -d "$R/configs/$n" "$R/scripts/$n.sh" "$R/scripts/$n"_infer.sh "$R/scripts/$n"_finetune_from_*.sh 2>/dev/null | wc -l)
  echo "@@WS $n|$(kb "$p/images")|$(nf "$p/images")|$(kb "$p/predictions")|$(nf "$p/predictions")|$(kb "$p/parents")|$(kb "$p/eval")|$(kb "$p")|$(kb "$R/outputs/$n")|$sc"
done
echo "@@END"
'''

# What the Models view can remove from a server, per project. `data` is the
# project's whole data folder (images + predictions + parents + eval and
# anything else under <remote>/<project>/); `model` is the output folder
# together with the project's configs and scripts (a push writes them again).
REMOVE_KINDS = ("images", "predictions", "parents", "eval", "data", "model")
_DATA_PARTS = ("images", "predictions", "parents", "eval")
NAME_RE = re.compile(r"[A-Za-z0-9_.\-]+")


def remove_paths(remote: str, roots: list[str], project: str, kinds: list[str]) -> tuple[list[str], list[str]]:
    """The host paths removing `kinds` of one project means: (exact paths,
    glob patterns). When every data part is asked for, the whole data folder
    goes instead (it may hold more than the four known subfolders). `roots`
    are the workspace roots that carry layout-model-training (the training
    workspace, and the predict one when it differs)."""
    if not NAME_RE.fullmatch(project) or project in (".", "..", "layout-model-training"):
        raise ValueError(f"bad project name: {project!r}")
    bad = [k for k in kinds if k not in REMOVE_KINDS]
    if bad or not kinds:
        raise ValueError(f"bad kind(s): {bad or kinds}")
    ks = set(kinds)
    if ks.issuperset(_DATA_PARTS):
        ks -= set(_DATA_PARTS); ks.add("data")
    exact, globs = [], []
    if "data" in ks:
        exact.append(f"{remote}/{project}")
    for k in _DATA_PARTS:
        if k in ks and "data" not in ks:
            exact.append(f"{remote}/{project}/{k}")
    if "model" in ks:
        for r in roots:
            lm = f"{r}/layout-model-training"
            exact += [f"{lm}/outputs/{project}", f"{lm}/configs/{project}",
                      f"{lm}/scripts/{project}.sh", f"{lm}/scripts/{project}_infer.sh"]
            globs.append(f"{lm}/scripts/{project}_finetune_from_*.sh")
    return exact, globs


def remove_script(remote: str, roots: list[str], items: list[dict]) -> str:
    """One shell script that removes every item ({project, kinds}) and
    reports each path as `@@OK`, `@@MISSING` or `@@FAIL`; a project's data
    folder left empty by removing its parts is dropped too."""
    import shlex
    lines = ["set +e"]
    for it in items:
        exact, globs = remove_paths(remote, roots, it["project"], it["kinds"])
        for p in exact:
            q = shlex.quote(p)
            lines.append(f'if [ -e {q} ]; then rm -rf {q} && echo "@@OK {p}" || echo "@@FAIL {p}"; else echo "@@MISSING {p}"; fi')
        for g in globs:
            lines.append(f'for f in {g}; do [ -e "$f" ] && {{ rm -f "$f" && echo "@@OK $f" || echo "@@FAIL $f"; }}; done')
        pd = f"{remote}/{it['project']}"
        lines.append(f"rmdir {shlex.quote(pd)} 2>/dev/null")
    lines.append('echo "@@DONE"')
    return "\n".join(lines) + "\n"


def parse_removal(stdout: str) -> dict:
    out = {"removed": [], "missing": [], "failed": [], "complete": False}
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if line == "@@DONE":
            out["complete"] = True
        elif line.startswith("@@OK "):
            out["removed"].append(line[5:])
        elif line.startswith("@@MISSING "):
            out["missing"].append(line[10:])
        elif line.startswith("@@FAIL "):
            out["failed"].append(line[7:])
    return out


def listing_script(remote_path: str, train_container: str, predict_container: str) -> str:
    """The shell script that lists one server; `remote_path` is the profile's
    workspace (the host directory the train container mounts as /workspace)."""
    dot = chr(92) + "."          # a literal backslash-dot for grep -E
    return (_SCRIPT.replace("__REMOTE__", remote_path.rstrip("/"))
                   .replace("__TRAIN__", train_container.replace(".", dot))
                   .replace("__PREDICT__", predict_container.replace(".", dot)))


def _ts(epoch: str) -> str | None:
    try:
        return datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def _ap_of(line: str) -> dict | None:
    """The bbox scores of a Detectron2 metrics line, rounded; None when the
    line is not one (or holds no finite AP)."""
    try:
        d = json.loads(line)
    except Exception:
        return None
    out = {}
    for k, v in d.items():
        if k.startswith("bbox/") and isinstance(v, (int, float)) and v == v:
            out[k[len("bbox/"):]] = round(float(v), 2)
    return out or None


def parse_listing(stdout: str) -> dict:
    """The listing script's output → {"gpu": {...} | None, "procs": [...],
    "containers": [{name, status, running}], "disk": {avail_gb, used_pct} | None,
    "models": [{name, trained, weights_bytes, folder_kb, metrics_at, ap}],
    "partial": [{name, folder_kb}], "workspace": [{name, images_kb, images_n,
    predictions_kb, predictions_n, parents_kb, eval_kb, data_kb, other_kb,
    model_kb, scripts_n}]} — `partial` are output folders without final
    weights (a run that is still going, or one that died); `workspace` is
    every project's footprint on the host."""
    out = {"gpu": None, "procs": [], "containers": [], "disk": None, "models": [], "partial": [],
           "workspace": [], "complete": False}
    section = None
    for raw in (stdout or "").splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line == "@@END":
            out["complete"] = True
            break
        if line in ("@@GPU", "@@PROCS", "@@CONTAINERS", "@@DISK", "@@MODELS", "@@WORKSPACE"):
            section = line[2:].lower()
            continue
        if line.startswith("@@WS "):
            f = line[len("@@WS "):].split("|")
            n = lambda i: int(f[i]) if len(f) > i and f[i].strip().isdigit() else 0
            data_kb = n(7)
            known = n(1) + n(3) + n(5) + n(6)
            out["workspace"].append({"name": f[0], "images_kb": n(1), "images_n": n(2),
                                     "predictions_kb": n(3), "predictions_n": n(4), "parents_kb": n(5),
                                     "eval_kb": n(6), "data_kb": data_kb, "other_kb": max(0, data_kb - known),
                                     "model_kb": n(8), "scripts_n": n(9)})
            continue
        if line.startswith("@@MODEL "):
            parts = line[len("@@MODEL "):].split("|")
            name = parts[0]
            out["models"].append({
                "name": name,
                "trained": _ts(parts[1]) if len(parts) > 1 else None,
                "weights_bytes": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None,
                "folder_kb": int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None,
                "metrics_at": _ts(parts[4]) if len(parts) > 4 and parts[4] else None,
                "ap": None,
            })
            section = "model"
            continue
        if line.startswith("@@PARTIAL "):
            parts = line[len("@@PARTIAL "):].split("|")
            out["partial"].append({"name": parts[0],
                                   "folder_kb": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None})
            section = None
            continue
        if section == "model":
            if out["models"] and line.lstrip().startswith("{"):
                out["models"][-1]["ap"] = _ap_of(line)
            continue
        if section == "gpu":
            f = [x.strip() for x in line.split(",")]
            if len(f) >= 4 and out["gpu"] is None:
                num = lambda s: int(re.sub(r"\D", "", s) or 0)
                out["gpu"] = {"name": f[0], "mem_used_mb": num(f[1]), "mem_total_mb": num(f[2]), "util_pct": num(f[3])}
        elif section == "procs":
            f = [x.strip() for x in line.split(",")]
            if len(f) >= 2:
                out["procs"].append({"pid": f[0], "name": f[1], "mem": f[2] if len(f) > 2 else ""})
        elif section == "containers":
            name, _, status = line.partition("|")
            out["containers"].append({"name": name, "status": status,
                                      "running": status.lower().startswith("up")})
        elif section == "disk":
            f = line.split()
            if len(f) >= 2 and f[0].isdigit():
                out["disk"] = {"avail_gb": round(int(f[0]) / 1024 / 1024, 1),
                               "used_pct": int(re.sub(r"\D", "", f[1]) or 0)}
    return out


def provisioning_time(statuses):
    """The `time` of the ProvisioningState status — when the VM last finished
    starting or deallocating — or None."""
    for s in statuses or []:
        code = getattr(s, "code", None) or (s.get("code") if isinstance(s, dict) else None) or ""
        if code.startswith("ProvisioningState/"):
            return getattr(s, "time", None) or (s.get("time") if isinstance(s, dict) else None)
    return None


def _code(s) -> str:
    return getattr(s, "code", None) or (s.get("code") if isinstance(s, dict) else None) or ""


def power_state(statuses) -> str:
    """An Azure instance view's power state: running / deallocated / stopped /
    starting / deallocating / stopping; "failed" when there is no power state
    but the last provisioning operation failed (a start Azure could not
    fulfil, e.g. AllocationFailed); else unknown."""
    for s in statuses or []:
        if _code(s).startswith("PowerState/"):
            return _code(s).split("/", 1)[1]
    for s in statuses or []:
        if _code(s).startswith("ProvisioningState/failed"):
            return "failed"
    return "unknown"


def provisioning_message(statuses) -> str | None:
    """Why the last provisioning operation ended as it did: the failure code
    (AllocationFailed, …) with Azure's message, or None when it succeeded."""
    for s in statuses or []:
        code = _code(s)
        if code.startswith("ProvisioningState/failed"):
            reason = code.split("/", 2)[2] if code.count("/") >= 2 else "failed"
            msg = getattr(s, "message", None) or (s.get("message") if isinstance(s, dict) else None) or ""
            return f"{reason}: {msg}".rstrip(": ") if msg else reason
    return None
