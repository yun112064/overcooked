"""路徑與識別設定。原文資料一律放在 Data Root（Git 之外），不寫進專案目錄。"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = ("overcooked-diet-assistant/0.1 "
              "(non-commercial student project; +https://github.com/yun112064/overcooked)")


def data_dir(explicit: str | os.PathLike | None = None) -> Path:
    """ASSISTANT_DATA_DIR，或 PROJECT_DATA_ROOT/assistant_rag。不可位於專案（Git）目錄內。"""
    value = explicit or os.getenv("ASSISTANT_DATA_DIR")
    if value:
        path = Path(value)
    elif os.getenv("PROJECT_DATA_ROOT"):
        path = Path(os.environ["PROJECT_DATA_ROOT"]) / "assistant_rag"
    else:
        raise RuntimeError("請設定 ASSISTANT_DATA_DIR 或 PROJECT_DATA_ROOT（原文資料不可放進 Git）")
    path = path.resolve()
    if path == PROJECT_ROOT or PROJECT_ROOT in path.parents:
        raise RuntimeError(f"資料目錄不可位於專案目錄內：{path}")
    return path
