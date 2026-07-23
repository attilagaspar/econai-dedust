#!/bin/bash
# Bootstrap a NEW GPU host's workspace for Dedust training/inference.
# Run ON the GPU host, from a clone of this repo:
#   bash gpu_scaffold/bootstrap.sh [workspace_dir]     (default: ~/econai)
#
# Everything comes from THIS repo + public downloads — nothing is copied from
# any previous GPU server. The Dedust app pushes the per-project pieces
# (config yaml, train/infer scripts, infer_layout.py, images, annotations)
# itself at run time; only train_net.py and cocosplit.py must pre-exist.
set -e
WS="${1:-$HOME/econai}"
HERE="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$WS/layout-model-training"/{tools,utils,scripts,logs,configs,outputs}
cp -f "$HERE/layout-model-training/tools/train_net.py"  "$WS/layout-model-training/tools/"
cp -f "$HERE/layout-model-training/utils/cocosplit.py"  "$WS/layout-model-training/utils/"

echo "✓ Workspace scaffolded at $WS"
echo
echo "Remaining one-time host setup (if not done yet):"
echo "  1. Docker:                curl -fsSL https://get.docker.com | sudo sh"
echo "  2. NVIDIA toolkit:        see gpu_scaffold/README.md (3 commands)"
echo "  3. In the Dedust dashboard: create a GPU profile pointing at this host"
echo "     (remote_path = $WS), then Docker settings -> Build (predict/train)."
echo "The first training run downloads its pretrained backbone from the public"
echo "detectron2 model zoo automatically."
