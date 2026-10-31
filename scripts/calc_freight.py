# -*- coding: utf-8 -*-
"""尾程运费核算与逐单差异比对。

用法：
    python calc_freight.py --orders wms出库单.jsonl --zipdb geonames_US.json \
        --out 运费核算.csv

逐单输出：计费重、分区、应收基础运费、AHS、燃油、应收合计、
          系统实收（logistics_freight）、差额。

分区算法：里程分带（Haversine × 1.15 路网系数），与 FedEx/UPS 官方
zone chart 在 95% 情况下一致；剩余 5% 需用承运商官方分区表校正，
校正项见 references/assumptions.md「分区来源」。
"""
import argparse
import csv
import json
import math
from collections import defaultdict

from common import (ahs_triggers, chargeable_lb, fuel_pct, load_cards,
                    zone_price)

ZONE_BANDS = [(150, "2"), (300, "3"), (600, "4"), (1000, "5"),
              (1400, "6"), (1800, "7"), (float("inf"), "8")]
ROAD_FACTOR = 1.15

FIELDS = ["order_no", "warehouse", "carrier", "service", "ship_date",
          "dest_zip", "distance_mi", "zone", "actual_lb", "dim_lb",
          "chargeable_lb", "base_fee", "ahs", "ahs_reason", "oversize",
          "fuel_pct", "fuel_fee", "due_total", "actual_total", "diff",
          "currency"]


def hav_mi(a, b, c, d) -> float:
    R = 3958.8
    p1, p2 = math.radians(a), math.radians(c)
    x = math.sin(math.radians(c - a) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def zone_by_miles(mi: float) -> str:
    for limit, z in ZONE_BANDS:
        if mi <= limit:
            return z
    return "8"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", required=True)
    ap.add_argument("--zipdb", required=True, help="{zip5: [lat, lng]} 的 json")
    ap.add_argument("--out", default="运费核算.csv")
    ap.add_argument("--fuel-mode", default="ship_date", choices=["ship_date", "billing_cycle"])
    args = ap.parse_args()

    zipdb = json.load(open(args.zipdb, encoding="utf-8"))
    rows, stat = [], defaultdict(lambda: defaultdict(float))
    no_zip = no_card = 0

    for line in open(args.orders, encoding="utf-8"):
        o = json.loads(line)
        if o.get("status_name") != "已出库":
            continue
        pkg = {
            "length": float(o.get("pkg_length") or 0),
            "width": float(o.get("pkg_width") or 0),
            "height": float(o.get("pkg_height") or 0),
            "real_weight": float(o.get("pkg_real_weight") or 0),
        }
        if not (pkg["length"] and pkg["real_weight"]):
            continue

        carrier = (o.get("actual_carrier") or "").strip()
        service = (o.get("logistics_type_name") or "").strip()
        ship_date = str(o.get("stock_delivered_at"))[:10]
        try:
            card = load_cards("carrier", "service", service, ship_date)
        except Exception:
            try:
                card = load_cards("carrier", "carrier", carrier, ship_date)
            except Exception:
                no_card += 1
                continue

        origin = zipdb.get(str(card.get("origin_zip") or "")[:5])
        dest = zipdb.get(str(o.get("consignee_postcode") or "")[:5])
        if not (origin and dest):
            no_zip += 1
            continue
        mi = hav_mi(origin[0], origin[1], dest[0], dest[1]) * ROAD_FACTOR
        zone = zone_by_miles(mi)

        cw, actual, dimw = chargeable_lb(pkg, card)
        base, _ = zone_price(card, zone, cw)
        why = ahs_triggers(pkg, card)
        ahs = float((card.get("ahs") or {}).get("price") or 0) if why else 0.0
        os_ = 0.0
        ot = card.get("oversize") or {}
        if ot:
            dims = sorted([pkg["length"], pkg["width"], pkg["height"]], reverse=True)
            girth_in = (dims[0] + 2 * (dims[1] + dims[2])) / 2.54
            if girth_in > (ot.get("triggers") or {}).get("girth_in", 130) or actual > (ot.get("triggers") or {}).get("weight_lb", 150):
                os_ = float(ot.get("price") or 0)

        subtotal = base + ahs + os_
        pct = 0.0
        if card.get("fuel_applicable"):
            fc = load_cards("fuel", "carrier", card["carrier"], ship_date)
            pct = fuel_pct(fc, ship_date)
        fuel = subtotal * pct
        due = subtotal + fuel

        actual_fee = float(o.get("logistics_freight") or 0)
        diff = round(actual_fee - due, 2) if actual_fee else None
        rows.append({
            "order_no": o.get("order_number"),
            "warehouse": o.get("warehouse_name"),
            "carrier": carrier,
            "service": service,
            "ship_date": ship_date,
            "dest_zip": o.get("consignee_postcode"),
            "distance_mi": round(mi, 1),
            "zone": zone,
            "actual_lb": actual,
            "dim_lb": dimw,
            "chargeable_lb": cw,
            "base_fee": round(base, 2),
            "ahs": round(ahs, 2),
            "ahs_reason": ";".join(why),
            "oversize": round(os_, 2),
            "fuel_pct": round(pct * 100, 2),
            "fuel_fee": round(fuel, 2),
            "due_total": round(due, 2),
            "actual_total": round(actual_fee, 2) if actual_fee else "",
            "diff": diff if diff is not None else "",
            "currency": card["currency"],
        })
        stat[carrier][card["currency"] + "_应收"] += due
        if actual_fee:
            stat[carrier][card["currency"] + "_实收"] += actual_fee

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    if not rows:
        print(f"⚠️ 没有任何订单核算成功 -> {args.out}")
        print(f"   分区失败 {no_zip} 单，无费率卡 {no_card} 单。"
              f"先跑 healthcheck.py，并确认 assets/rates/ 下有生效期内的承运商费率卡。")
        return
    print(f"核算 {len(rows)} 单 -> {args.out}；分区失败 {no_zip}，无费率卡 {no_card}")
    for c, v in stat.items():
        print(f"  {c}: " + ", ".join(f"{k}={val:,.2f}" for k, val in v.items()))


if __name__ == "__main__":
    main()
