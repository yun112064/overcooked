"""Print a T01 review payload for one iCook recipe as UTF-8 JSON."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.source_adapters.icook import ICookError, fetch_recipe, parse_recipe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--html", type=Path, help="Replay a previously downloaded HTML page")
    args = parser.parse_args()
    try:
        if args.html is None:
            result = fetch_recipe(args.url)
        else:
            result = parse_recipe(args.url, args.html.read_bytes())
    except (ICookError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
