FROM nvidia/cuda:12.1.0-devel-ubuntu22.04

# Install system dependencies
RUN apt update && apt install -y \
    python3 python3-pip python3-dev \
    git wget curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch (make sure this matches your CUDA version)
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# Install Detectron2 dependencies
RUN pip install opencv-python cython pyyaml scipy tqdm tensorboard future tabulate matplotlib pandas numpy
RUN pip install fvcore iopath
RUN pip install 'git+https://github.com/facebookresearch/detectron2.git'

# Install for layout parser script dependencies
RUN apt update && apt install -y poppler-utils
RUN pip install pdf2image img2pdf layoutparser pymupdf   funcy scikit-learn
#frontend

# Set working directory
WORKDIR /workspace

# Default command
CMD ["bash"]

# This is how you mount
# docker run --gpus all -it --rm -v C:/Users/agaspar/Dropbox/research/leporolt_adatok/econai/:/workspace detectron2
# docker run --shm-size=8g --gpus all -it --rm -v C:/Users/agaspar/Dropbox/research/leporolt_adatok/econai/:/workspace detectron2


