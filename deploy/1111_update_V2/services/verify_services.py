"""Readiness checks are real authenticated HTTP calls, not process-only checks."""
import json
import os
import sys
import time

import httpx


def main():
    checks = (
        ("yolo", "http://yolo:8765", "INFERENCE_TOKEN"),
        ("t11", "http://t11:8091", "KNOWLEDGE_SERVICE_TOKEN"),
        ("assistant", "http://assistant:8093", "ASSISTANT_SERVICE_TOKEN"),
    )
    report = {}
    for name, url, variable in checks:
        try:
            response = httpx.get(url + "/v1/health", timeout=180,
                                headers={"Authorization": "Bearer " + os.environ[variable]})
            body = response.json()
            ready = response.status_code == 200 and body.get("status") == "ok"
            report[name] = {"ready": ready, "http_status": response.status_code, "details": body}
        except Exception as exc:
            report[name] = {"ready": False, "error": type(exc).__name__}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(item["ready"] for item in report.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
