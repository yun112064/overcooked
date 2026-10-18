FROM python:3.12-slim

WORKDIR /app/backend

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
# T07 起後端直接使用 T03 版本化規則與 T04 共用食材字典
COPY nutrition_target /app/nutrition_target
COPY nutrition_model/data/ingredient_id_master.json /app/nutrition_model/data/ingredient_id_master.json
ENV PYTHONPATH=/app
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations

CMD ["sh", "-c", "alembic -c /app/alembic.ini upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]