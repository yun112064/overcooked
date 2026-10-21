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

COPY config/staples.json /app/config/staples.json
# 決策 #29：知識說明的營養關鍵字依據檢查（與團隊電腦爬蟲前置過濾共用）
COPY config/nutrition_keywords.json /app/config/nutrition_keywords.json
ENV PYTHONPATH=/app
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations
# T12 整餐營養驗證使用 T04 TFDA 營養模組（平面匯入，由 meal_calculation 加入 sys.path）
COPY nutrition_model /app/nutrition_model

# T17：開發預設啟動時自動升級；正式環境 AUTO_MIGRATE=false，只由部署腳本在部署前備份後升級，
# 啟動前以 schema 閘門確認資料庫已在 head，否則拒絕啟動
CMD ["sh", "-c", "if [ \"${AUTO_MIGRATE:-true}\" = \"true\" ]; then alembic -c /app/alembic.ini upgrade head; else python -m app.core.schema_gate; fi && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]