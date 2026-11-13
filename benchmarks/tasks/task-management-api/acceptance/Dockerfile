# Tester image for the task-management-api acceptance suite. It talks to the
# app only over HTTP (BASE_URL), so it is the same for every stack.
FROM python:3.12.7-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir --disable-pip-version-check pytest==8.3.3 httpx==0.27.2
