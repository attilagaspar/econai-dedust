# GPU scaffold — bootstrap any GPU server from this repo alone

Everything a fresh GPU host needs to serve Dedust training/inference, with
**no copying from any existing server**. Provenance of the vendored scripts:

| File | Origin | License |
|---|---|---|
| `layout-model-training/tools/train_net.py` | [Layout-Parser/layout-model-training](https://github.com/Layout-Parser/layout-model-training) | Apache-2.0 |
| `layout-model-training/utils/cocosplit.py` | same repo (itself adapted from [akarazniewicz/cocosplit](https://github.com/akarazniewicz/cocosplit)) | Apache-2.0 |

Only these two must pre-exist on a GPU host. Everything else arrives at run
time: the Dedust app pushes per-project configs (patched from
`samples/ertesito2/fast_rcnn_R_50_FPN_3x.yaml`), the generated train/infer
scripts, `infer_layout.py`, images and annotations; detectron2 downloads the
COCO-pretrained backbone from the public model zoo on first training.

## New GPU server recipe (Ubuntu)

```bash
# 1. Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER

# 2. NVIDIA container toolkit (driver must already be installed — on Azure
#    use the "NVIDIA GPU Driver Extension" at VM creation)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker

# 3. Workspace scaffold (from a clone of this repo)
git clone https://github.com/attilagaspar/econai-dedust.git
bash econai-dedust/gpu_scaffold/bootstrap.sh          # default workspace ~/econai
```

Then in the Dedust dashboard: **GPU Server card → new profile** (host, user,
key or password, `remote_path` = the workspace dir) → **Docker settings →
Build (predict / train)** — this builds the `dedust-layout` image and creates
the per-workspace containers on the host. Train / Infer / Fine-tune work from
that point exactly as on any other GPU backend.
