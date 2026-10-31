# -*- coding: utf-8 -*-
"""仓储费核算：日结存 × 库龄阶梯 × 单价。

核心思路：
  1. 以当前库存快照为锚点，用仓位流水向前回推出月初结存；
  2. 再逐日前推，入仓形成批次、出仓按 FIFO 消耗批次，得到每日每个批次的库龄；
  3. 日费用 = Σ(批次数量 × 单件体积 × 档位单价 ÷ 当月天数)，月末累加。

用法：
    python calc_storage.py --snapshot 库存快照.jsonl --flows 流水.jsonl \
        --specs SKU规格.json --month 2026-10 --out 仓储费明细.csv

注意：FIFO 是行业通用近似。若 3PL 按"先进先出"计库龄，结果一致；
      若按"平均库龄"或"整批不拆分"，需在 assumptions.md 中改口径后调整本脚本。
"""
import argparse
import csv
import json
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta

from common import load_cards

INBOUND_TYPES = {22, 24, 26, 71, 72}   # 采购/调拨/退货入库、上架
OUTBOUND_TYPES = {37, 38, 41, 39}       # FBA/FBM/调拨/退货出库


def d(s: str) -> date:
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def unit_volume_cbm(spec: dict) -> float:
    """单件体积（立方米），按产品包装尺寸。"""
    L = float(spec.get("length") or 0) / 100.0
    W = float(spec.get("width") or 0) / 100.0
    H = float(spec.get("height") or 0) / 100.0
    return L * W * H


def pick_price(card: dict, volume: float, age_days: int) -> float:
    """按库龄取档，返回该单位（cbm/cft/piece/pallet）的月单价。"""
    for t in card.get("age_tiers") or []:
        hi = t.get("max_days")
        if age_days >= t["min_days"] and (hi is None or age_days <= hi):
            base = float(t.get("price_per_unit_month") or card.get("price_per_unit_month") or 0)
            unit = card.get("billing_unit")
            if unit == "cft":
                return base * volume * 35.3147      # cbm -> cft
            if unit == "cbm":
                return base * volume
            return base                              # piece / pallet 按件
    return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True, help="当前库存快照 jsonl（含 wid/sku/product_valid_num）")
    ap.add_argument("--flows", required=True, help="仓位流水 jsonl（含 wid/sku/num/type/opt_time）")
    ap.add_argument("--specs", required=True, help="SKU 规格 json：{sku: {length,width,height}} 单位 cm")
    ap.add_argument("--month", required=True, help="核算月份 YYYY-MM")
    ap.add_argument("--out", default="仓储费明细.csv")
    args = ap.parse_args()

    y, m = map(int, args.month.split("-"))
    m_start = date(y, m, 1)
    m_end = date(y, m, monthrange(y, m)[1])
    days_in_month = monthrange(y, m)[1]

    specs = json.load(open(args.specs, encoding="utf-8"))
    snapshot = [json.loads(l) for l in open(args.snapshot, encoding="utf-8")]
    flows = [json.loads(l) for l in open(args.flows, encoding="utf-8")]

    # 1) 快照日的每个 (wid, sku) 结存
    snap_day = max(d(f.get("opt_time", "2000-01-01")) for f in flows) if flows else m_end
    snap_qty = defaultdict(int)
    wh_of = {}
    for r in snapshot:
        k = (str(r.get("wid")), r.get("sku"))
        snap_qty[k] += int(r.get("product_valid_num") or 0)
        wh_of[str(r.get("wid"))] = r.get("warehouse_name") or ""

    # 2) 按月内流水组织
    in_flows = defaultdict(lambda: defaultdict(int))    # (wid,sku) -> day -> qty
    for f in flows:
        t = int(f.get("type") or 0)
        if t not in INBOUND_TYPES and t not in OUTBOUND_TYPES:
            continue
        k = (str(f.get("wid")), f.get("sku"))
        day = d(f.get("opt_time"))
        qty = int(f.get("num") or 0)
        in_flows[k][day] += qty

    # 3) 回推月初结存：快照日结存 − 快照日之后的净变动
    opening = {}
    for k, q in snap_qty.items():
        delta = sum(v for day, v in in_flows[k].items() if day > m_start)
        opening[k] = q - delta

    # 4) 逐日模拟，FIFO 消耗批次
    batches = defaultdict(list)          # (wid,sku) -> [[qty, recv_date], ...]
    for k, q in opening.items():
        if q > 0:
            batches[k].append([q, m_start - timedelta(days=30)])   # 月初存量按 30 天前入仓近似

    daily_fee = defaultdict(float)       # (wh, sku) -> amount
    cur = m_start
    while cur <= m_end:
        for k, flows_today in in_flows.items():
            net = flows_today.get(cur, 0)
            if net > 0:
                batches[k].append([net, cur])
            elif net < 0:
                need = -net
                while need > 0 and batches[k]:
                    b = batches[k][0]
                    take = min(need, b[0])
                    b[0] -= take
                    need -= take
                    if b[0] == 0:
                        batches[k].pop(0)
        for k, bs in batches.items():
            if not bs:
                continue
            wid, sku = k
            wh = wh_of.get(wid, wid)
            vol = unit_volume_cbm(specs.get(sku) or {})
            if vol <= 0:
                continue
            try:
                card = load_cards("storage", "warehouse_names", wh, cur.isoformat())
            except Exception:
                continue
            for qty, recv in bs:
                if qty <= 0:
                    continue
                age = (cur - recv).days
                if age < int(card.get("free_days") or 0):
                    continue
                daily_fee[(wh, sku)] += qty * pick_price(card, vol, age) / days_in_month
        cur += timedelta(days=1)

    rows = [
        {"warehouse": wh, "sku": sku, "storage_fee": round(amt, 2),
         "billing_unit": load_cards("storage", "warehouse_names", wh, m_end.isoformat()).get("billing_unit")}
        for (wh, sku), amt in sorted(daily_fee.items(), key=lambda x: -x[1])
    ]
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["warehouse", "sku", "storage_fee", "billing_unit"])
        w.writeheader()
        w.writerows(rows)

    tot = defaultdict(float)
    for r in rows:
        tot[r["warehouse"]] += r["storage_fee"]
    print(f"{args.month} 仓储费 {len(rows)} 条 -> {args.out}")
    for wh, v in sorted(tot.items(), key=lambda x: -x[1]):
        print(f"  {wh}: {v:,.2f}")


if __name__ == "__main__":
    main()
