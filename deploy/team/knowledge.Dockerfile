FROM python:3.12-slim

WORKDIR /app
COPY knowledge_rag/requirements.txt /app/knowledge_rag/requirements.txt
RUN pip install --no-cache-dir -r /app/knowledge_rag/requirements.txt
COPY knowledge_rag /app/knowledge_rag

CMD ["python", "-m", "knowledge_rag.server"]
