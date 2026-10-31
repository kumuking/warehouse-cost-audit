# -*- coding: utf-8 -*-
"""用脱敏示例数据跑通全流程，验证环境是否装好。

不需要领星账号、不需要真实费率卡，直接运行：
    python examples/run_demo.py

所有示例费率卡都是虚构价格，只用于验证脚本能跑，不代表任何供应商报价。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"
OUT = EX / "output"

ENV = {**os.environ, "WCA_RATES_DIR": str(EX / "rates")}
PY = sys.executable


def run(title, args):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")
    print("$ " + " ".join(args))
    r = subprocess.run([PY] + args, cwd=str(ROOT), env=ENV)
    if r.returncode != 0:
        print(f"\n❌ 上一步失败（退出码 {r.returncode}）")
        sys.exit(r.returncode)


def show_csv(path, limit=8):
    import csv
    if not Path(path).exists():
        return
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"\n--- {Path(path).name}（{len(rows)} 行，显示前 {min(limit, len(rows))} 行）---")
    for r in rows[:limit]:
        print("  " + " | ".join(f"{k}={v}" for k, v in r.items() if v != ""))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print("仓配费用核算 · 演示模式")
    print(f"费率卡目录：{EX / 'rates'}")
    print(f"输出目录：  {OUT}")

    run("步骤 0 · 启动自检", ["scripts/healthcheck.py", "--rates", str(EX / "rates")])

    freight = str(OUT / "运费核算.csv")
    run("步骤 1 · 尾程运费核算",
        ["scripts/calc_freight.py", "--orders", str(EX / "orders_sample.jsonl"),
         "--zipdb", str(EX / "zipdb_sample.json"), "--out", freight])
    show_csv(freight)

    handling = str(OUT / "操作费明细.csv")
    run("步骤 2 · 仓内操作费",
        ["scripts/calc_handling.py", "--orders", str(EX / "orders_sample.jsonl"),
         "--out", handling])
    show_csv(handling)

    storage = str(OUT / "仓储费明细.csv")
    run("步骤 3 · 仓储费（日结存 × 库龄阶梯）",
        ["scripts/calc_storage.py", "--snapshot", str(EX / "snapshot_sample.jsonl"),
         "--flows", str(EX / "flows_sample.jsonl"), "--specs", str(EX / "specs_sample.json"),
         "--month", "2026-10", "--out", storage])
    show_csv(storage)

    diff = str(OUT / "差异明细.csv")
    run("步骤 4 · 与承运商账单对账",
        ["scripts/reconcile.py", "--calc", freight,
         "--bill", str(EX / "bill_sample.csv"),
         "--bill-key", "Tracking Number", "--bill-amount", "Amount",
         "--bill-type", "ChargeType", "--out", diff])
    show_csv(diff)

    print(f"\n{'=' * 62}")
    print("✅ 演示跑通，环境可用。下一步：")
    print("   1) 把真实费率卡放进 assets/rates/（参考 assets/rates/_template_*.json）")
    print("   2) 配置领星凭证后，按 README 的「每月标准流程」跑真实数据")
    print("=" * 62)


if __name__ == "__main__":
    main()
