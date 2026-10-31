"""換算係數批次推估（decisions #19）與 TFDA 對照（decisions #28）及覆核：python -m app.estimation <command>

run       掃描缺口並以 gpt-5.4-mini 推估（--dry-run 只列出；--refresh 重新推估；--limit N；
          --kind tfda_mapping|cooking|unit；不指定時先跑 tfda_mapping 再重新掃描 cooking／unit）
list      列出快取（--kind、--status、--food）
approve   人工核可一列：approve <id> --reviewer 名稱 [--note]
reject    人工駁回一列（之後永不使用）：reject <id> --reviewer 名稱 [--note]
override  人工覆寫值（新增一列，優先於 LLM）：
          override --kind cooking --food FOOD000100 --key 炒 --yield 0.8 --energy 1 --protein 0.95 --reviewer A
          override --kind unit --food FOOD001541 --key 匙 --grams 15 --reviewer A
          override --kind tfda_mapping --food FOOD000509 --key tfda --sample E5600101 --reviewer A
          override --kind nutrient_fill --food N0100101 --key protein_g --value 0 --reviewer A
            （decisions #32／#45：--food 為 TFDA 樣本 ID、--key 為 energy_kcal／protein_g／carbohydrate_g／fat_g／
             sugars_g／dietary_fiber_g／sodium_mg、--value 為每 100 g（鈉為 mg）；只能補 TFDA 空白、且不適用
             config/nutrient_category_zero.json 類別 0 規則的營養素）

run --kind nutrient_fill：掃描合格食譜實際用到的 TFDA 樣本（含主食、人工／LLM 對照、熟樣本），7 種營養素空白、
          且不適用類別 0 規則的組合（decisions #32／#45）。--dry-run 回報缺口數與費用上限 estimated_max_cost_twd。
"""
import argparse
import json
import sys
from decimal import Decimal

from app.estimation import batch, store

_DEFAULT = object()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.estimation", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--refresh", action="store_true")
    r.add_argument("--limit", type=int)
    r.add_argument("--kind", choices=store.KINDS)
    ls = sub.add_parser("list")
    ls.add_argument("--kind", choices=store.KINDS)
    ls.add_argument("--status", choices=("llm_estimated", "approved", "rejected", "overridden"))
    ls.add_argument("--food")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("estimate_id")
        p.add_argument("--reviewer", required=True)
        p.add_argument("--note")
    o = sub.add_parser("override")
    o.add_argument("--kind", choices=store.KINDS, required=True)
    o.add_argument("--food", required=True)
    o.add_argument("--key", required=True, help="烹調方式（cooking）或單位（unit）")
    o.add_argument("--yield", dest="yield_factor", type=Decimal)
    o.add_argument("--energy", type=Decimal, help="熱量保留率 0–1")
    o.add_argument("--protein", type=Decimal, help="蛋白質保留率 0–1")
    o.add_argument("--grams", type=Decimal, help="每單位克數")
    o.add_argument("--sample", help="TFDA 樣本 ID（tfda_mapping）")
    o.add_argument("--value", type=Decimal, help="每 100 g 營養值（nutrient_fill）")
    o.add_argument("--reviewer", required=True)
    o.add_argument("--note")
    return parser


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv=None, *, factory=None, responder=_DEFAULT) -> int:
    args = _parser().parse_args(argv)
    if factory is None:
        from app.db.session import SessionLocal
        factory = SessionLocal
    try:
        if args.command == "run":
            if responder is _DEFAULT or responder is None:
                from app.services.llm_client import default_responder
                responder = default_responder()
            kinds = (args.kind,) if args.kind else store.KINDS
            _print(batch.run(factory, responder=responder, dry_run=args.dry_run, refresh=args.refresh,
                             limit=args.limit, kinds=kinds))
        elif args.command == "list":
            with factory() as db:
                _print(store.list_estimates(db, kind=args.kind, status=args.status, food_id=args.food))
        elif args.command in ("approve", "reject"):
            with factory() as db, db.begin():
                store.review(db, args.estimate_id, action=args.command, reviewer=args.reviewer, note=args.note)
            print(f"{args.command}d {args.estimate_id}")
        else:
            retention = None
            if args.kind == "cooking":
                if args.energy is None or args.protein is None:
                    raise ValueError("cooking override requires --energy and --protein")
                retention = {"energy_kcal": args.energy, "protein_g": args.protein}
            elif args.kind == "tfda_mapping":
                if not args.sample:
                    raise ValueError("tfda_mapping override requires --sample")
            elif args.kind == "nutrient_fill":
                if args.value is None:
                    raise ValueError("nutrient_fill override requires --value")
            elif args.grams is None:
                raise ValueError("unit override requires --grams")
            with factory() as db, db.begin():
                new_id = store.override(db, kind=args.kind, food_id=args.food, key=args.key,
                                        reviewer=args.reviewer, note=args.note, yield_factor=args.yield_factor,
                                        retention=retention, grams_per_unit=args.grams,
                                        tfda_sample_id=args.sample if args.kind == "tfda_mapping" else None,
                                        nutrient_per_100g=args.value if args.kind == "nutrient_fill" else None)
            print(f"overridden {new_id}")
    except batch.EstimationUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except LookupError as exc:
        print(f"estimate not found: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
