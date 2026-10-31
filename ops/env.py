"""維運指令共用：讀取 .env 設定檔、把例外轉成可安全印出的訊息（只用標準函式庫）。

印到終端機或排程器紀錄的錯誤不可帶連線字串中的帳密、密碼、權杖（T06／T17 秘密規則）。
"""
import os
import re
from pathlib import Path

_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/@\"']+@")
_BEARER = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)
_KEY_VALUE = re.compile(
    r"(?i)(?P<key>password|passwd|pwd|token|secret|api[_-]?key|authorization|access[_-]?key)"
    r"(?P<sep>\s*[:=]\s*)(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;&]+)")


def redact(text: str) -> str:
    text = _URL_CREDENTIALS.sub(lambda m: m.group("scheme") + "***@", text)
    text = _BEARER.sub("Bearer ***", text)
    return _KEY_VALUE.sub(lambda m: m.group("key") + m.group("sep") + "***", text)


def safe_error(exc: BaseException) -> str:
    """例外類型＋經遮蔽的訊息（不含堆疊）。"""
    message = redact(str(exc))
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def load_env(env_file: Path | str | None) -> dict:
    """環境變數優先，再補上 .env 檔的值（支援註解、export 前綴與引號）。"""
    env = dict(os.environ)
    if env_file and Path(env_file).is_file():
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env.setdefault(key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'"))
    return env
