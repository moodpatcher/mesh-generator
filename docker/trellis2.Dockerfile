FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0+PTX" \
    PIP_DEFAULT_TIMEOUT=180 PIP_RETRIES=10
RUN apt-get update && apt-get install -y --no-install-recommends git wget ca-certificates python3 python3-pip \
    python3-dev python-is-python3 ninja-build build-essential libgl1 libglib2.0-0 libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
RUN apt-get update && apt-get install -y --no-install-recommends libeigen3-dev && rm -rf /var/lib/apt/lists/*
RUN printf '#!/bin/sh\nfor i in 1 2 3 4 5 6; do sh -c "$1" && exit 0; echo "attempt $i failed - retrying in $((i * 20)) s"; sleep $((i * 20)); done; exit 1\n' \
      > /usr/local/bin/retry && chmod +x /usr/local/bin/retry
RUN retry "rm -rf /opt/trellis2 && git clone -b main https://github.com/microsoft/TRELLIS.2.git /opt/trellis2" \
    && rm -rf /opt/trellis2/o-voxel/third_party/eigen && cp -r /usr/include/eigen3 /opt/trellis2/o-voxel/third_party/eigen \
    && sed -i '/@ git+https/d' /opt/trellis2/o-voxel/pyproject.toml
RUN mkdir -p /tmp/extensions && cd /tmp/extensions \
    && retry "rm -rf FlexGEMM && git clone https://github.com/JeffreyXiang/FlexGEMM.git FlexGEMM" \
    && retry "rm -rf CuMesh && git clone https://github.com/JeffreyXiang/CuMesh.git CuMesh" \
    && cd CuMesh && retry "git submodule update --init third_party/cubvh" \
    && rm -rf third_party/cubvh/third_party/eigen && cp -r /usr/include/eigen3 third_party/cubvh/third_party/eigen
WORKDIR /opt/trellis2
ARG CUDA_ARCHS="8.0;8.6;8.9;9.0+PTX"
ARG MAX_JOBS=4
RUN pip install -U pip "setuptools>=69,<76" wheel packaging psutil ninja
RUN printf '#!/bin/sh\n[ "$1" = apt ] && exit 0\nexec "$@"\n' > /usr/local/bin/sudo && chmod +x /usr/local/bin/sudo \
    && printf '#!/bin/sh\necho "build-time stub (no GPU while building)"\n' > /usr/local/bin/nvidia-smi \
    && chmod +x /usr/local/bin/nvidia-smi \
    && export TORCH_CUDA_ARCH_LIST="$CUDA_ARCHS" MAX_JOBS="$MAX_JOBS" \
    && pip install flash-attn==2.7.3 --no-build-isolation \
    && bash -c ". ./setup.sh --basic --flash-attn --cumesh --o-voxel --flexgemm"; \
    rc=$?; rm -f /usr/local/bin/nvidia-smi; pip list 2>/dev/null | grep -i -E "flash|cumesh|voxel|flex|utils3d|unknown"; \
    [ $rc = 0 ] || exit $rc; \
    for p in "flash.?attn" cumesh "o.?voxel" "flex.?gemm" utils3d; do \
      pip list 2>/dev/null | grep -qi -E "^$p " || { echo "TRELLIS.2 extension missing after setup.sh: $p"; exit 1; }; \
    done
RUN pip install huggingface_hub tqdm trimesh "timm>=1.0.20"
ENV PYTHONPATH=/opt/trellis2 HF_HOME=/cache/hf PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
