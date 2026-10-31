# -*- coding: utf-8 -*-
"""与 3PL / 承运商账单对账，产出差异清单与申诉建议。

用法：
    python reconcile.py --calc 运费核算.csv --bill bill.csv \
        --calc-key order_no --bill-key Reference --bill-amount Amount \
        --bill-type ChargeType --out 差异明细.csv

账单列名各承运商不同（FedEx: Tracking Number / Net Charge；
UPS: Invoice Line / Billed Amount），用参数映射，不改代码。
"""
import argparse
import csv
from collections import defaultdict

# 差异判定阈值（可按供应商调整）
ABS_TOLERANCE = 1.00      # 绝对容差（原币）
PCT_TOLERANCE = 0.02      # 相对容差 2%


def read_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def f(v, default=0.0):
    try:
        return float(str(v).replace(",", "").replace("$", "") or default)
    except ValueError:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calc", required=True)
    ap.add_argument("--bill", required=True)
    ap.add_argument("--calc-key", default="order_no")
    ap.add_argument("--bill-key", required=True)
    ap.add_argument("--bill-amount", required=True)
    ap.add_argument("--bill-type", help="账单费用类型列，用于分项比对")
    ap.add_argument("--out", default="差异明细.csv")
    args = ap.parse_args()

    calc = {r[args.calc_key]: r for r in read_csv(args.calc) if r.get(args.calc_key)}

    # 账单按单聚合（一单可能多行：基础/燃油/AHS）
    bill = defaultdict(lambda: defaultdict(float))
    for r in read_csv(args.bill):
        k = r.get(args.bill_key)
        if not k:
            continue
        t = r.get(args.bill_type) if args.bill_type else "合计"
        bill[k][t] += f(r.get(args.bill_amount))

    out = []
    matched = 0
    for k, b in bill.items():
        c = calc.get(k)
        bill_total = sum(b.values())
        if not c:
            out.append({"order_no": k, "issue": "账单有、核算无", "bill_total": round(bill_total, 2),
                        "due_total": "", "diff": "", "suggestion": "缺计费依据，索取该单明细或确认是否误计"})
            continue
        matched += 1
        due = f(c.get("due_total"))
        diff = round(bill_total - due, 2)
        if abs(diff) <= max(ABS_TOLERANCE, due * PCT_TOLERANCE):
            continue
        # 归因：先看 AHS，再看燃油，最后看基础
        reason = "金额差异"
        sug = "复核计费重与分区"
        ahs_due = f(c.get("ahs"))
        if ahs_due == 0 and bill_total - due > 0:
            reason = "疑似多收附加费（AHS/Oversize）"
            sug = "该单尺寸重量未达 AHS 触发条件，要求撤销该附加费"
        elif ahs_due > 0 and bill_total - due < 0:
            reason = "疑似少收 AHS"
            sug = "该单已触发 AHS 但账单未计，留意后续补收"
        elif f(c.get("fuel_pct")) > 0:
            reason = "燃油或基础运费差异"
            sug = "核对当期燃油率与基础费率版本"
        out.append({
            "order_no": k, "issue": "金额不符", "bill_total": round(bill_total, 2),
            "due_total": round(due, 2), "diff": diff,
            "ahs_due": ahs_due, "fuel_pct": c.get("fuel_pct"),
            "chargeable_lb": c.get("chargeable_lb"), "zone": c.get("zone"),
            "suggestion": sug, "reason": reason,
        })

    with open(args.out, "w", newline="", encoding="utf-8-sig") as fo:
        w = csv.DictWriter(fo, fieldnames=list(out[0].keys()) if out else ["order_no", "issue"])
        w.writeheader()
        w.writerows(out)

    over = sum(r["diff"] for r in out if isinstance(r.get("diff"), (int, float)) and r["diff"] > 0)
    under = sum(r["diff"] for r in out if isinstance(r.get("diff"), (int, float)) and r["diff"] < 0)
    print(f"账单 {len(bill)} 单，匹配 {matched}，差异 {len(out)} 单 -> {args.out}")
    print(f"  多收合计 {over:,.2f} / 少收合计 {under:,.2f} / 净额 {over + under:,.2f}")


if __name__ == "__main__":
    main()
