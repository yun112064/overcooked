FROM python:3.12-slim

WORKDIR /app/backend

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
# T07 起後端直接使用 T03 版本化規則；標準食材字典（FOOD 編號）由 T01 匯入資料庫
COPY nutrition_target /app/nutrition_target
# T10 付費呼叫預留與結算用的模型價格表
COPY config/model_pricing.json /app/config/model_pricing.json
# T09 YOLO 類別名稱對照（待 T05 定版）
COPY config/recognition_labels.json /app/config/recognition_labels.json
# T02 不查價食材（如水）
COPY config/price_exempt_ingredients.json /app/config/price_exempt_ingredients.json
# 同義食材對照（過敏全套用、已有食材與查價只用已確認組）
COPY config/ingredient_synonyms.json /app/config/ingredient_synonyms.json
# T01 正式資料只從版本化 SQL seed 安裝；與 /app/data 執行資料分開，避免 volume 遮蔽
COPY data/recipe_data_manifest.json /app/recipe_seed/recipe_data_manifest.json
COPY data/recipe_data_1027.sql.gz /app/recipe_seed/recipe_data_1027.sql.gz
COPY scripts/bootstrap_recipe_data.py /app/scripts/bootstrap_recipe_data.py
ENV PYTHONPATH=/app
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations

CMD ["sh", "-c", "alembic -c /app/alembic.ini upgrade head && python /app/scripts/bootstrap_recipe_data.py --manifest /app/recipe_seed/recipe_data_manifest.json && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
