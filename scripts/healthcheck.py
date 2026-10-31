# -*- coding: utf-8 -*-
"""跑数前自检：不过关就停下，不硬算。

用法：
    python healthcheck.py --cred ~/领星api.txt --rates ../assets/rates

检查项：
  1. 领星 API 连通（IP 白名单失效会在这里暴露，而不是跑到一半报错）
  2. 费率卡覆盖（每家 3PL / 承运商是否都有生效期内的卡）
  3. 数据完整性（计费重、尺寸、运费的缺失率）
"""
import argparse
import json
import socket
import sys
from datetime import date
from pathlib import Path

OK, WARN, FAIL = "✅", "⚠️", "❌"


def check_api(cred_path: str) -> bool:
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from lingxing import Lingxing
        lx = Lingxing.from_cred(cred_path)
        ws = lx.warehouses("3")
        print(f"{OK} 领星 API 连通，海外仓 {len(ws)} 个")
        return True
    except Exception as e:
        msg = str(e)
        if "timeout" in msg.lower() or "Max retries" in msg or "Connection" in msg:
            try:
                ip = socket.gethostbyname("openapi.lingxing.com")
                print(f"{FAIL} 领星 API 连不通（DNS 解析到 {ip}，但 TCP 443 被拒）")
                print("   → 大概率是 IP 白名单失效：家庭宽带重拨后出口 IP 变了")
                print("   → 去领星开放平台后台把当前公网 IP 加入白名单后重试")
            except Exception:
                print(f"{FAIL} 领星域名解析失败，检查网络")
        else:
            print(f"{FAIL} 鉴权失败：{msg}")
        return False


def check_rates(rates_dir: str) -> bool:
    d = Path(rates_dir)
    today = date.today()
    ok = True
    for kind in ("handling", "storage", "carrier", "fuel"):
        files = [p for p in d.glob(f"{kind}*.json") if not p.name.startswith("_")]
        live = []
        for p in files:
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
                f = date.fromisoformat(c["effective_from"])
                t = date.fromisoformat(c["effective_to"]) if c.get("effective_to") else date(2099, 12, 31)
                if f <= today <= t:
                    live.append(p.name)
            except Exception:
                print(f"{WARN} {p.name} 字段缺失或格式错误")
        if not live:
            print(f"{WARN} {kind} 类型没有生效期内的费率卡（共 {len(files)} 个文件）")
            ok = False
        else:
            print(f"{OK} {kind} 生效卡 {len(live)} 张：{', '.join(live)}")
    return ok


def check_data(orders_path: str) -> bool:
    if not orders_path or not Path(orders_path).exists():
        print(f"{WARN} 未提供出库单文件，跳过数据完整性检查")
        return True
    total = dim_ok = freight_ok = 0
    for line in open(orders_path, encoding="utf-8"):
        r = json.loads(line)
        total += 1
        if float(r.get("pkg_length") or 0) and float(r.get("pkg_real_weight") or 0):
            dim_ok += 1
        if float(r.get("logistics_freight") or 0):
            freight_ok += 1
    print(f"  出库单 {total}：尺寸/实重完整 {dim_ok/total*100:.1f}%，运费有值 {freight_ok/total*100:.1f}%")
    if freight_ok / total < 0.8:
        print(f"{WARN} 运费有值率低于 80%，对账覆盖率会偏低")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cred", help="领星凭证 txt")
    ap.add_argument("--rates", default=str(Path(__file__).parent.parent / "assets" / "rates"))
    ap.add_argument("--orders", help="wms出库单 jsonl，用于完整性检查")
    args = ap.parse_args()

    print("== 仓配费用核算 · 启动自检 ==")
    ok = True
    if args.cred:
        ok = check_api(args.cred) and ok
    ok = check_rates(args.rates) and ok
    check_data(args.orders)
    print("\n结论：", "可以跑数" if ok else "有阻塞项，先补齐再跑")


if __name__ == "__main__":
    main()
