"""The Models view's data: what trained models sit on a GPU server.

A model is a project's output folder on the host,
`<remote_path>/layout-model-training/outputs/<name>/fast_rcnn_R_50_FPN_3x/`
with `model_final.pth` (the weights) and `metrics.json` (Detectron2's log,
whose last `bbox/AP` line is the model's own evaluation). One SSH command
lists them all together with the GPU's state and the Dedust containers;
`parse_listing` turns its output into records, and the server adds what it
knows locally (the project, its trained classes, its training ledger).
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
echo "@@END"
'''


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
    "partial": [{name, folder_kb}]} — `partial` are output folders without
    final weights (a run that is still going, or one that died)."""
    out = {"gpu": None, "procs": [], "containers": [], "disk": None, "models": [], "partial": [], "complete": False}
    section = None
    for raw in (stdout or "").splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line == "@@END":
            out["complete"] = True
            break
        if line in ("@@GPU", "@@PROCS", "@@CONTAINERS", "@@DISK", "@@MODELS"):
            section = line[2:].lower()
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


def power_state(statuses) -> str:
    """An Azure instance view's power state: running / deallocated / stopped /
    starting / deallocating / stopping / unknown."""
    for s in statuses or []:
        code = getattr(s, "code", None) or (s.get("code") if isinstance(s, dict) else None) or ""
        if code.startswith("PowerState/"):
            return code.split("/", 1)[1]
    return "unknown"
