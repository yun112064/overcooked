"""T18 每月總費用試算（AC25／R11）：主機、模型、向量化、資料、備份、稅費與幣別換算，上限 NT$2,000。

輸入是人工填寫的真實報價檔（範本 quote.example.json）。全部以 Decimal 計算；
缺任何金額、稅率或匯率一律判定 incomplete，不以 0 代替。上限只能收緊、不能放寬。
tokens 計費的品項依 config/model_pricing.json（T10 預留同一份價格表）計價，未列價的模型拒絕。

用法：python scripts/load/monthly_cost.py my-quote.json [--out scripts/load/results]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TEMPLATE = HERE / "quote.example.json"
PRICING_PATH = ROOT / "config" / "model_pricing.json"
MONTHLY_CAP_TWD = Decimal("2000")
REQUIRED_CATEGORIES = ("host", "model", "embedding", "data", "backup")
CENT = Decimal("0.01")
MILLION = Decimal(1_000_000)


class CapRaiseRefused(ValueError):
    """月上限只能是 NT$2,000 或更低；產品條件變更須回規劃。"""


def load_pricing(path: Path = PRICING_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dec(value, path: str, missing: list) -> Decimal | None:
    if value is None or value == "":
        missing.append(path)
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        missing.append(path)
        return None


def _base_amount(item: dict, path: str, pricing: dict, missing: list) -> Decimal | None:
    billing = item.get("billing")
    if billing == "monthly":
        return _dec(item.get("amount"), f"{path}.amount", missing)
    if billing == "yearly":
        amount = _dec(item.get("amount"), f"{path}.amount", missing)
        return None if amount is None else amount / 12
    if billing == "usage":
        price = _dec(item.get("unit_price"), f"{path}.unit_price", missing)
        qty = _dec(item.get("monthly_quantity"), f"{path}.monthly_quantity", missing)
        return None if price is None or qty is None else price * qty
    if billing == "tokens":
        entry = pricing.get("models", {}).get(item.get("model"))
        if entry is None:
            missing.append(f"{path}.model")  # 未列價的模型不能以 0 計價
            return None
        if item.get("currency") != pricing.get("currency", "USD"):
            missing.append(f"{path}.currency")
            return None
        tin = _dec(item.get("monthly_input_tokens"), f"{path}.monthly_input_tokens", missing)
        tout = _dec(item.get("monthly_output_tokens"), f"{path}.monthly_output_tokens", missing)
        if tin is None or tout is None:
            return None
        return (Decimal(entry["input"]) * tin + Decimal(entry["output"]) * tout) / MILLION
    missing.append(f"{path}.billing")
    return None


def calculate(quote: dict, pricing: dict, *, cap_twd: Decimal = MONTHLY_CAP_TWD) -> dict:
    cap_twd = Decimal(cap_twd)
    if cap_twd > MONTHLY_CAP_TWD:
        raise CapRaiseRefused(f"Monthly cap may not exceed NT${MONTHLY_CAP_TWD}.")
    missing: list[str] = []
    fx = (quote.get("fx") or {})
    usd_twd = None
    lines = []
    for idx, item in enumerate(quote.get("items") or []):
        path = f"items[{idx}]"
        currency = item.get("currency")
        base = _base_amount(item, path, pricing, missing)
        tax = _dec(item.get("tax_rate"), f"{path}.tax_rate", missing)
        rate = None
        if currency == "TWD":
            rate = Decimal(1)
        elif currency == "USD":
            if usd_twd is None:
                usd_twd = _dec(fx.get("USD_TWD"), "fx.USD_TWD", missing)
            rate = usd_twd
        else:
            missing.append(f"{path}.currency")
        twd = None if None in (base, tax, rate) else base * (1 + tax) * rate
        lines.append({"category": item.get("category"), "name": item.get("name"), "billing": item.get("billing"),
                      "currency": currency, "monthly_twd": twd, "source": item.get("source"),
                      "checked_on": item.get("checked_on")})
    present = {line["category"] for line in lines}
    missing += [f"category:{c}" for c in REQUIRED_CATEGORIES if c not in present]
    missing = list(dict.fromkeys(missing))

    def up(value: Decimal) -> Decimal:
        return value.quantize(CENT, rounding=ROUND_CEILING)

    result = {"quote_date": quote.get("quote_date"), "fx": fx, "pricing_version": pricing.get("pricing_version"),
              "cap_twd": cap_twd, "lines": [{**l, "monthly_twd": up(l["monthly_twd"]) if l["monthly_twd"] is not None
                                            else None} for l in lines],
              "missing": missing}
    if missing:
        return {**result, "status": "incomplete", "total_twd": None, "by_category": {}, "fixed_twd": None,
                "variable_twd": None, "headroom_twd": None}
    total = sum((l["monthly_twd"] for l in lines), Decimal(0))
    by_category: dict[str, Decimal] = {}
    for l in lines:
        by_category[l["category"]] = by_category.get(l["category"], Decimal(0)) + l["monthly_twd"]
    fixed = sum((l["monthly_twd"] for l in lines if l["billing"] != "tokens"), Decimal(0))
    total_up = up(total)
    return {**result, "status": "within_cap" if total <= cap_twd else "over_cap", "total_twd": total_up,
            "by_category": {k: up(v) for k, v in by_category.items()},
            # T10 committed_services_twd 至少要涵蓋固定費用，剩下的才是模型／向量化預留額度
            "fixed_twd": up(fixed), "variable_twd": up(total - fixed), "headroom_twd": cap_twd - total_up}


def render_markdown(r: dict) -> str:
    def f(v):
        return "—" if v is None else str(v)
    lines = ["# T18 每月總費用試算", "", f"- 報價日期：{f(r['quote_date'])}",
             f"- 匯率 USD→TWD：{f(r['fx'].get('USD_TWD'))}（{f(r['fx'].get('source'))}，{f(r['fx'].get('checked_on'))}）",
             f"- 模型價格表版本：{f(r['pricing_version'])}", f"- 上限：NT${r['cap_twd']}",
             f"- 結論：**{r['status']}**", f"- 合計（含稅，月）：NT${f(r['total_twd'])}",
             f"- 固定費用（建議 committed_services_twd 不低於此值）：NT${f(r['fixed_twd'])}",
             f"- 剩餘額度：NT${f(r['headroom_twd'])}", "",
             "| 類別 | 品項 | 計費 | 幣別 | 月費 NT$（含稅） | 報價來源 | 查核日 |", "|---|---|---|---|---|---|---|"]
    for l in r["lines"]:
        lines.append(f"| {f(l['category'])} | {f(l['name'])} | {f(l['billing'])} | {f(l['currency'])} "
                     f"| {f(l['monthly_twd'])} | {f(l['source'])} | {f(l['checked_on'])} |")
    lines += ["", "## 缺漏", ""] + ([f"- {m}" for m in r["missing"]] or ["- （無）"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="T18 每月總費用試算")
    p.add_argument("quote", type=Path)
    p.add_argument("--pricing", type=Path, default=PRICING_PATH)
    p.add_argument("--out", type=Path, default=HERE / "results")
    args = p.parse_args(argv)
    quote = json.loads(args.quote.read_text(encoding="utf-8"), parse_float=Decimal)
    result = calculate(quote, load_pricing(args.pricing))
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path, md_path = args.out / f"t18-cost-{stamp}.json", args.out / f"t18-cost-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(f"monthly cost: {result['status']} total={result['total_twd']}\n{json_path}\n{md_path}")
    return 0 if result["status"] == "within_cap" else 1


if __name__ == "__main__":
    sys.exit(main())
