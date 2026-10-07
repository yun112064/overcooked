"""Local T03 handoff runner; does not start an HTTP service or write health logs."""

import argparse
import json
import sys
from decimal import Decimal

from .service import NutritionTargetService, TargetInputError


def main() -> int:
    parser = argparse.ArgumentParser(description="T03 nutrition calculation module")
    parser.add_argument("--rules", required=True, help="Explicit versioned rule JSON path")
    parser.add_argument("--input", required=True, help="Member JSON path; '-' reads stdin")
    parser.add_argument("--operation", choices=("energy", "daily", "meal", "aggregate"), required=True)
    parser.add_argument("--meal", choices=("breakfast", "lunch", "dinner"))
    args = parser.parse_args()
    try:
        if args.input == "-":
            data = json.load(sys.stdin, parse_float=Decimal)
        else:
            with open(args.input, encoding="utf-8") as source:
                data = json.load(source, parse_float=Decimal)
        service = NutritionTargetService(args.rules)
        if args.operation == "energy":
            result = service.energy_baseline(data)
        elif args.operation == "daily":
            result = service.daily_target(data)
        elif args.operation == "meal":
            result = service.meal_target(data, args.meal)
        else:
            result = service.aggregate_meal_targets(data)
    except TargetInputError as error:
        print(json.dumps({"error": error.as_dict()}, ensure_ascii=False))
        return 2
    except (OSError, json.JSONDecodeError):
        print(json.dumps({"error": {"code": "INVALID_INPUT", "message": "Cannot read input or rule JSON."}}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
