# -*- coding: utf-8 -*-
"""出库/入库操作费核算。

用法：
    python calc_handling.py --orders wms出库单.jsonl --inbound 备货单.json --out 操作费明细.csv

只统计 status_name == '已出库' 的单据（未出库不产生操作费）。
"""
import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from common import chargeable_kg, load_cards, to_lb

FIELDS = ["type", "warehouse", "order_no", "inbound_no", "ship_date",
          "receive_date", "pieces", "planned_qty", "received_qty",
          "chargeable_kg", "order_fee", "piece_fee", "kg_fee",
          "oversize_fee", "total", "currency"]


def fee_from_tier(card_sec: dict, chargeable_kg_val: float) -> dict:
    """按计费重档取费率字段，未命中原样返回顶层字段。"""
    for tier in card_sec.get("tiers") or []:
        if chargeable_kg_val <= tier["max_kg"]:
            return {**card_sec, **{k: v for k, v in tier.items() if k != "max_kg"}}
    return card_sec


def calc_outbound(order: dict, card: dict) -> dict:
    pieces = sum(int(it.get("count") or 0) for it in (order.get("product_info") or []))
    pkg = {
        "length": float(order.get("pkg_length") or 0),
        "width": float(order.get("pkg_width") or 0),
        "height": float(order.get("pkg_height") or 0),
        "real_weight": float(order.get("pkg_real_weight") or 0),
    }
    cw_kg = 0.0
    if pkg["length"] and pkg["width"] and pkg["height"]:
        cw_kg, _, _ = chargeable_kg(pkg)

    sec = fee_from_tier(card["outbound"], cw_kg)
    # 首件 + 续件 或 单价 × 件数
    if sec.get("per_piece_first"):
        piece_fee = float(sec["per_piece_first"]) + max(0, pieces - 1) * float(sec.get("per_piece_extra") or 0)
    else:
        piece_fee = float(sec.get("per_piece") or 0) * pieces

    order_fee = float(sec.get("per_order") or 0)
    kg_fee = float(sec.get("per_kg") or 0) * cw_kg

    longest = max(pkg["length"], pkg["width"], pkg["height"])
    oversize_fee = 0.0
    for os_tier in card["outbound"].get("oversize") or []:
        if longest <= os_tier["max_long_cm"]:
            oversize_fee = float(os_tier["surcharge"])
            break

    total = order_fee + piece_fee + kg_fee + oversize_fee
    mn, mx = sec.get("min_charge"), sec.get("max_charge")
    if mn and total < float(mn):
        total = float(mn)
    if mx and total > float(mx):
        total = float(mx)

    return {
        "warehouse": order.get("warehouse_name"),
        "order_no": order.get("order_number") or order.get("wo_number"),
        "ship_date": str(order.get("stock_delivered_at") or order.get("create_at"))[:10],
        "pieces": pieces,
        "chargeable_kg": round(cw_kg, 3),
        "order_fee": round(order_fee, 2),
        "piece_fee": round(piece_fee, 2),
        "kg_fee": round(kg_fee, 2),
        "oversize_fee": round(oversize_fee, 2),
        "total": round(total, 2),
        "currency": card["currency"],
    }


def calc_inbound(ib: dict, card: dict) -> dict:
    lines = ib.get("products") or []
    received = sum(int(p.get("receive_num") or 0) for p in lines)
    planned = sum(int(p.get("stock_num") or 0) for p in lines)
    sec = card["inbound"]
    total = float(sec.get("per_order") or 0) + float(sec.get("per_piece") or 0) * received
    mn = sec.get("min_charge")
    if mn and total < float(mn):
        total = float(mn)
    return {
        "warehouse": ib.get("r_wname"),
        "inbound_no": ib.get("inbound_order_no"),
        "receive_date": str(ib.get("receive_good_handle_time") or "")[:10],
        "planned_qty": planned,
        "received_qty": received,
        "total": round(total, 2),
        "currency": card["currency"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", required=True, help="wms出库单 jsonl")
    ap.add_argument("--inbound", help="备货单 json")
    ap.add_argument("--out", default="操作费明细.csv")
    args = ap.parse_args()

    out_rows, skipped, no_card = [], 0, 0
    by_wh = defaultdict(lambda: defaultdict(float))

    for line in open(args.orders, encoding="utf-8"):
        o = json.loads(line)
        if o.get("status_name") != "已出库" or not o.get("stock_delivered_at"):
            skipped += 1
            continue
        wh = o.get("warehouse_name")
        d = str(o.get("stock_delivered_at"))[:10]
        try:
            card = load_cards("handling", "warehouse_names", wh, d)
        except Exception as e:
            no_card += 1
            if no_card == 1:
                print(f"⚠️ 首个无费率卡：仓库={wh} 日期={d} -> {e}")
            continue
        r = calc_outbound(o, card)
        out_rows.append({"type": "出库", **r})
        by_wh[wh][r["currency"]] += r["total"]

    if args.inbound and Path(args.inbound).exists():
        for ib in json.load(open(args.inbound, encoding="utf-8")):
            d = str(ib.get("receive_good_handle_time") or "")[:10]
            if not d:
                continue
            wh = ib.get("r_wname")
            try:
                card = load_cards("handling", "warehouse_names", wh, d)
            except Exception:
                no_card += 1
                continue
            r = calc_inbound(ib, card)
            out_rows.append({"type": "入库", **r})
            by_wh[wh][r["currency"]] += r["total"]

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)

    print(f"明细 {len(out_rows)} 行 -> {args.out}；跳过未出库 {skipped} 单；无费率卡 {no_card} 单")
    for wh, cur in by_wh.items():
        print(f"  {wh}: " + ", ".join(f"{v:,.2f} {c}" for c, v in cur.items()))


if __name__ == "__main__":
    main()
