FROM python:3.15.2-slim-trixie

RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/stack/requirements.txt
RUN pip install --no-cache-dir -r /opt/stack/requirements.txt

ENV PIP_USER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/home/agent/.local/bin:$PATH

WORKDIR /workspace
