"""讓 `python -m unittest tests.test_xxx` 與 discover／pytest 一樣可用。

測試模組彼此以頂層名稱匯入（db_support、test_t11_recipe_retrieval 等），
所以把 tests/ 放進 sys.path。設定物件要求 DATABASE_URL；這裡只放不會連線的
占位值，CI 與 .env 已有設定時不覆蓋。需要資料庫的測試仍只看 TEST_DATABASE_URL。
"""
import os
import sys
from pathlib import Path

_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")
