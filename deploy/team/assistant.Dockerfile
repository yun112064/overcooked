# T17 正式環境：T11 營養知識 RAG 與飲食小幫手（R14）共用映像。
# 嵌入與回答模型在主機的 Ollama（GPU），重排在 TEI 容器；這裡只放檢索與服務程式。
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt /app/requirements.txt
COPY assistant_rag/requirements.txt /app/assistant_rag/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -r /app/assistant_rag/requirements.txt

COPY knowledge_rag /app/knowledge_rag
COPY assistant_rag /app/assistant_rag
# 小幫手斷詞用的 TFDA 食材標準名／別名（只讀）
COPY nutrition_model/data/ingredient_master.json nutrition_model/data/ingredient_id_master.json /app/nutrition_model/data/
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 PYTHONUTF8=1

CMD ["python", "-m", "assistant_rag.server"]
