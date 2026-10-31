"""JSON adapter for the common knowledge record contract; no DB/model calls."""
import json
from pathlib import Path

from knowledge_rag.store import validate_record


def load_records(path):
    """Read/validate a whole file before mutation; sheet status never approves it."""
    path = Path(path)
    if path.suffix.lower() == '.json':
        with path.open(encoding='utf-8-sig') as handle:
            document = json.load(handle)
        records = document.get('records') if isinstance(document, dict) else None
    else:
        raise ValueError('Knowledge import supports versioned .json only')
    if not isinstance(records, list) or not records:
        raise ValueError('At least one knowledge record required')
    for number, record in enumerate(records, start=1):
        try:
            validate_record(record)
        except ValueError as exc:
            raise ValueError(f'Record {number}: {exc}') from None
    return records
