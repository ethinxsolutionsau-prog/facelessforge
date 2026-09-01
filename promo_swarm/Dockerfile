FROM python:3.11-slim@sha256:d1e9ca7c4e78d1e8ecadb5d44bfc8e956e7a65b659a9950f569f243d72b326d0
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8095
HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD curl -f http://localhost:8095/health || exit 1
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8095"]
