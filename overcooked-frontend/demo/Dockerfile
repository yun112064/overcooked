FROM meal-planner-backend:latest
WORKDIR /app/backend
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend /app/backend
COPY nutrition_target /app/nutrition_target
COPY nutrition_planning /app/nutrition_planning
COPY nutrition_model /app/nutrition_model
COPY knowledge_rag /app/knowledge_rag
COPY ops /app/ops
COPY config /app/config
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations
COPY overcooked-frontend/demo /app/demo
ENV PYTHONPATH=/app:/app/backend:/app/demo
CMD ["uvicorn", "demo_app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
