FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
COPY assistant_rag/requirements.txt /tmp/assistant-requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt -r /tmp/assistant-requirements.txt
COPY backend /app/backend
COPY knowledge_rag /app/knowledge_rag
COPY assistant_rag /app/assistant_rag
COPY nutrition_model /app/nutrition_model
COPY config /app/config
COPY deploy/1111_update_V2/services /app/deploy/1111_update_V2/services
COPY scripts/prepare_t11_evidence.py scripts/publish_t11_approved_evidence.py /app/scripts/
COPY docs/1111_update_V2 /app/docs/1111_update_V2
ENV PYTHONPATH=/app:/app/backend PYTHONUNBUFFERED=1
