FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates wget xz-utils libx11-6 libxi6 \
    libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libsm6 libice6 libgl1 libegl1 libglu1-mesa \
    && rm -rf /var/lib/apt/lists/*
RUN wget -q https://download.blender.org/release/Blender4.5/blender-4.5.15-linux-x64.tar.xz -O /tmp/b.tar.xz && mkdir /opt/blender \
    && tar -xJf /tmp/b.tar.xz -C /opt/blender --strip-components=1 && rm /tmp/b.tar.xz
ENTRYPOINT ["/opt/blender/blender"]
