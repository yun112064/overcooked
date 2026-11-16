"""來源轉接器：discover() 列出候選文章、parse() 把文章頁轉成統一紀錄。"""
import hashlib
from datetime import datetime, timezone

PARSER_VERSION = "assistant-source-v1"


def finish_record(record: dict) -> dict:
    """補上全文、字數、內容雜湊與抓取時間（兩個來源共用的統一格式）。"""
    content = "\n".join(p for s in record["sections"] for p in s["paragraphs"])
    record.update(
        content=content,
        char_count=len(content),
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        parser_version=PARSER_VERSION,
    )
    return record
