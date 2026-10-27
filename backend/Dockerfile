FROM python:3.12-slim

WORKDIR /app/backend

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
# T07 起後端直接使用 T03 版本化規則；標準食材字典（FOOD 編號）由 T01 匯入資料庫
COPY nutrition_target /app/nutrition_target
# 共用設定（全是非機密的資料檔：模型價格、主食、同義詞、調味料、營養與食材修正表…）。
# 整個目錄複製：逐檔列出曾漏掉計算必需的檔（nutrition_as_is_categories 等），demo 生成因此全部失敗。
COPY config /app/config
ENV PYTHONPATH=/app
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations
# T12 整餐營養驗證使用 T04 TFDA 營養模組（平面匯入，由 meal_calculation 加入 sys.path）
COPY nutrition_model /app/nutrition_model

# T17：開發預設啟動時自動升級；正式環境 AUTO_MIGRATE=false，只由部署腳本在部署前備份後升級，
# 啟動前以 schema 閘門確認資料庫已在 head，否則拒絕啟動
CMD ["sh", "-c", "if [ \"${AUTO_MIGRATE:-true}\" = \"true\" ]; then alembic -c /app/alembic.ini upgrade head; else python -m app.core.schema_gate; fi && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]