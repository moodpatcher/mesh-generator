FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0+PTX" \
    PIP_DEFAULT_TIMEOUT=180 PIP_RETRIES=10
RUN apt-get update && apt-get install -y --no-install-recommends git wget ca-certificates python3 python3-pip \
    python3-dev python-is-python3 ninja-build build-essential libgl1 libglib2.0-0 libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
RUN pip install -U pip "setuptools>=69,<76" wheel packaging ninja cmake scikit-build
RUN git clone https://github.com/VAST-AI-Research/TripoSR.git /opt/triposr
ARG CUDA_ARCHS="8.0;8.6;8.9;9.0+PTX"
RUN pip install scikit-build-core pybind11 \
    && TORCH_CUDA_ARCH_LIST="$CUDA_ARCHS" pip install --no-build-isolation git+https://github.com/tatsy/torchmcubes.git
RUN pip install omegaconf==2.3.0 einops==0.7.0 transformers==4.35.0 "trimesh>=4" "huggingface_hub<0.26" Pillow \
    fast_simplification
ENV PYTHONPATH=/opt/triposr HF_HOME=/cache/hf PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
