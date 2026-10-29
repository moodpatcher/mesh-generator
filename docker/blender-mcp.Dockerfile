# The MCP server of ahujasid/mcp-for-blender (MIT), pinned. It runs here so that the host needs no uv / Python packages.
# It talks to the add-on inside the Blender window on the host (host.docker.internal). Telemetry is off.
FROM python:3.12-slim
ARG MCP_FOR_BLENDER=2.2.1
RUN pip install --no-cache-dir "mcp-for-blender==${MCP_FOR_BLENDER}"
ENV DISABLE_TELEMETRY=true \
    BLENDER_HOST=host.docker.internal \
    BLENDER_PORT=9876
ENTRYPOINT ["mcp-for-blender"]
