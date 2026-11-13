# FastAPI stack: Python 3.12 with a pinned, preinstalled toolset so every agent
# starts from the same versions and installs during the trial stay small.
FROM python:3.12.7-slim-bookworm

RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/stack/requirements.txt
RUN pip install --no-cache-dir -r /opt/stack/requirements.txt

# The agent runs as a non-root user: let it install extra packages to its own
# site-packages (~/.local) and find their scripts.
ENV PIP_USER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/home/agent/.local/bin:$PATH

WORKDIR /workspace
