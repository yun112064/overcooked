FROM python:3.12-slim

WORKDIR /app/backend

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
# T07 起後端直接使用 T03 版本化規則；標準食材字典（FOOD 編號）由 T01 匯入資料庫
COPY nutrition_target /app/nutrition_target
# T12 exact calculations and the versioned TFDA catalog/mapping dependencies.
COPY nutrition_planning /app/nutrition_planning
COPY nutrition_model /app/nutrition_model
# Runtime policy and mapping files used by T02–T20.
COPY config /app/config
# T01 正式資料只從版本化 SQL seed 安裝；與 /app/data 執行資料分開，避免 volume 遮蔽
COPY data/recipe_data_manifest.json /app/recipe_seed/recipe_data_manifest.json
COPY data/recipe_data_1027.sql.gz /app/recipe_seed/recipe_data_1027.sql.gz
COPY scripts/bootstrap_recipe_data.py /app/scripts/bootstrap_recipe_data.py
ENV PYTHONPATH=/app
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations

CMD ["sh", "-c", "alembic -c /app/alembic.ini upgrade head && python /app/scripts/bootstrap_recipe_data.py --manifest /app/recipe_seed/recipe_data_manifest.json && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
