# -*- coding: utf-8 -*-
"""按月份拉取核算所需的全部原始数据，落成本地文件。

用法：
    python scripts/pull_month.py --cred ~/领星api.txt --start 2026-10-01 --end 2026-10-31 --outdir data

产出（都在 --outdir 下）：
    warehouses.json                海外仓清单（wid -> 名称）
    wms_出库单_{start}_{end}.jsonl  出库单（尺寸/重量/邮编/运费）
    备货单_{start}_{end}.json       入库单（实收件数/收货时间）
    snapshot_{日期}.jsonl           库存快照（每仓一份，追加）
    flows_{start}_{end}.jsonl       仓位流水（每仓一份，追加）

注意：
  - 领星限流为令牌桶 1，脚本按 1.1s 间隔 sleep，仓多时会慢，属正常。
  - 出库单收件地址约 30 天后会被领星脱敏，历史月份请尽早拉取。
"""
import argparse
import json
from datetime import date
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
from lingxing import Lingxing  # noqa: E402


def write_jsonl(path: Path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, rows):
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cred", required=True, help="领星凭证 txt（含 APP ID / AppSecret）")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--wids", help="只拉指定仓库，逗号分隔；默认全部海外仓")
    ap.add_argument("--skip-flows", action="store_true", help="跳过仓位流水（只算操作费和运费时用）")
    args = ap.parse_args()

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    print("== 连接领星 ==")
    lx = Lingxing.from_cred(args.cred)
    whs = lx.warehouses("3")
    (out / "warehouses.json").write_text(
        json.dumps(whs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 海外仓 {len(whs)} 个 -> warehouses.json")

    if args.wids:
        targets = [{"wid": w.strip(), "name": w.strip()} for w in args.wids.split(",")]
    else:
        targets = whs

    print("== 拉取出库单 ==")
    orders = lx.wms_orders(args.start, args.end)
    p = out / f"wms_出库单_{args.start}_{args.end}.jsonl"
    write_jsonl(p, orders)
    print(f"✅ 出库单 {len(orders)} 单 -> {p.name}")

    print("== 拉取备货单 ==")
    inbound = lx.inbound_orders(args.start, args.end)
    p = out / f"备货单_{args.start}_{args.end}.json"
    p.write_text(json.dumps(inbound, ensure_ascii=False), encoding="utf-8")
    print(f"✅ 备货单 {len(inbound)} 张 -> {p.name}")

    snap_path = out / f"snapshot_{date.today().isoformat()}.jsonl"
    if snap_path.exists():
        snap_path.unlink()

    flows_path = out / f"flows_{args.start}_{args.end}.jsonl"
    if flows_path.exists():
        flows_path.unlink()

    print(f"== 逐仓取快照{' + 流水' if not args.skip_flows else ''}（{len(targets)} 个仓）==")
    for i, w in enumerate(targets, 1):
        wid, name = str(w.get("wid")), w.get("name") or str(w.get("wid"))
        try:
            snap = lx.inventory_snapshot(wid)
            for r in snap:
                r.setdefault("warehouse_name", name)
            append_jsonl(snap_path, snap)
            extra = ""
            if not args.skip_flows:
                flows = lx.bin_statement(wid, args.start, args.end)
                for r in flows:
                    r.setdefault("warehouse_name", name)
                append_jsonl(flows_path, flows)
                extra = f"，流水 {len(flows)} 笔"
            print(f"  [{i}/{len(targets)}] {name}: 快照 {len(snap)} 条{extra}")
        except Exception as e:
            print(f"  [{i}/{len(targets)}] ⚠️ {name} 失败：{e}")

    print("\n✅ 完成。下一步：")
    print(f"   python scripts/calc_freight.py --orders {out}/wms_出库单_{args.start}_{args.end}.jsonl "
          f"--zipdb assets/geonames_US.json --out output/运费核算.csv")


if __name__ == "__main__":
    main()
