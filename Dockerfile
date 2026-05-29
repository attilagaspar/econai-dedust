# EconAI GPU container — Detectron2 + layout inference/training
# Build:  docker build -t econai-layout .
# Create: docker create --name <container_name> --gpus all \
#           --shm-size=8g -v /path/on/server:/workspace econai-layout
# Start:  docker start <container_name>

FROM nvidia/cuda:12.1.0-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-dev \
    git wget curl \
    ffmpeg libsm6 libxext6 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# PyTorch with CUDA 12.1
RUN pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# Detectron2 dependencies
RUN pip3 install \
    opencv-python-headless \
    cython \
    pyyaml \
    scipy \
    tqdm \
    tensorboard \
    future \
    tabulate \
    matplotlib \
    pandas \
    numpy \
    fvcore \
    iopath

# Detectron2 (from source — always gets the right CUDA/torch-compatible version)
RUN pip3 install 'git+https://github.com/facebookresearch/detectron2.git'

# PDF / image utilities used by pipeline scripts
RUN pip3 install pdf2image img2pdf pymupdf layoutparser funcy scikit-learn

WORKDIR /workspace
CMD ["bash"]
