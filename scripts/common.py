# -*- coding: utf-8 -*-
"""公共工具：费率卡加载、单位换算、计费重与 AHS 判定。"""
import json
import os
from datetime import date, datetime
from pathlib import Path

# 费率卡目录：默认 skill 内的 assets/rates，可用环境变量 WCA_RATES_DIR 覆盖
# （演示、多客户隔离、共享盘存放费率卡时用得上）
RATES_DIR = Path(
    os.environ.get("WCA_RATES_DIR")
    or Path(__file__).resolve().parent.parent / "assets" / "rates"
)
CM_PER_IN = 2.54
G_PER_LB = 453.59237


class RateCardError(RuntimeError):
    pass


def load_cards(kind: str, key_field: str, key_value: str, on_date: str) -> dict:
    """按供应商 + 生效期取唯一费率卡。

    kind: 'handling' | 'storage' | 'carrier' | 'fuel'
    文件命名：{kind}_{key}_{yyyymm}.json  —— 不强制，按内容字段匹配。
    命中 0 张或多张直接报错，绝不猜测。
    """
    d = datetime.strptime(on_date, "%Y-%m-%d").date()
    hits = []
    # 用 *{kind}* 匹配，兼容 3pl-handling_xxx.json / carrier_FedEx.json 两种命名
    for p in sorted(RATES_DIR.glob(f"*{kind}*.json")):
        if p.name.startswith("_"):
            continue  # 跳过模板
        if p.parent.name == "mapping":
            continue  # 跳过映射表
        try:
            card = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            raise RateCardError(f"{p.name} 解析失败: {e}")
        if card.get(key_field) != key_value and key_value not in (card.get(key_field) or []):
            continue
        f = datetime.strptime(card["effective_from"], "%Y-%m-%d").date()
        t = card.get("effective_to")
        t = datetime.strptime(t, "%Y-%m-%d").date() if t else date(2099, 12, 31)
        if f <= d <= t:
            hits.append((p.name, card))
    if len(hits) != 1:
        raise RateCardError(
            f"{kind} 费率卡命中 {len(hits)} 张（{key_field}={key_value}, 日期={on_date}），必须恰好 1 张"
        )
    return hits[0][1]


def to_in(x_cm: float) -> float:
    return float(x_cm) / CM_PER_IN


def to_lb(x_g: float) -> float:
    return float(x_g) / G_PER_LB


def round_up(value: float, step: float) -> float:
    if not step:
        return value
    return -(-value // step) * step


def chargeable_lb(pkg: dict, card: dict) -> tuple[float, float, float]:
    """返回 (chargeable_lb, actual_lb, dim_lb)。

    pkg: {length, width, height (cm), real_weight (g)}
    card: 承运商卡，需含 dim_divisor / dim_unit
    """
    L, W, H = to_in(pkg["length"]), to_in(pkg["width"]), to_in(pkg["height"])
    dim_lb = (L * W * H) / float(card.get("dim_divisor") or 139)
    actual_lb = to_lb(pkg["real_weight"])
    cw = max(actual_lb, dim_lb)
    step = (card.get("round_up") or {}).get("weight_lb") or 1.0
    return round_up(cw, step), round_up(actual_lb, step), round(dim_lb, 3)


def chargeable_kg(pkg: dict, divisor: float = 5000) -> tuple[float, float, float]:
    """公制口径：返回 (chargeable_kg, actual_kg, vol_kg)。"""
    L, W, H = pkg["length"], pkg["width"], pkg["height"]
    vol_kg = (L * W * H) / float(divisor)
    actual_kg = pkg["real_weight"] / 1000.0
    return max(actual_kg, vol_kg), actual_kg, round(vol_kg, 3)


def ahs_triggers(pkg: dict, card: dict) -> list[str]:
    """判定 AHS 触发原因，返回原因列表（空列表 = 不触发）。"""
    t = (card.get("ahs") or {}).get("triggers") or {}
    if not t:
        return []
    dims = sorted([to_in(pkg["length"]), to_in(pkg["width"]), to_in(pkg["height"])], reverse=True)
    lb = to_lb(pkg["real_weight"])
    why = []
    if t.get("max_long_in") and dims[0] > t["max_long_in"]:
        why.append(f"最长边{dims[0]:.1f}in>{t['max_long_in']}in")
    if t.get("second_long_in") and dims[1] > t["second_long_in"]:
        why.append(f"次长边{dims[1]:.1f}in>{t['second_long_in']}in")
    if t.get("actual_weight_lb") and lb > t["actual_weight_lb"]:
        why.append(f"实重{lb:.1f}lb>{t['actual_weight_lb']}lb")
    return why


def zone_price(card: dict, zone: str, lb: float):
    """按 zone + 计费重查基础运费，返回 (price, 命中重量上限)。"""
    table = (card.get("zones") or {}).get(str(zone))
    if not table:
        raise RateCardError(f"{card.get('carrier')} 缺少 zone {zone} 的费率")
    for max_lb, price in table:
        if lb <= max_lb:
            return float(price), float(max_lb)
    raise RateCardError(f"{card.get('carrier')} zone {zone} 计费重 {lb}lb 超出费率表上限")


def fuel_pct(fuel_card: dict, ship_date: str) -> float:
    """按发货日所在周取燃油率；周表按 week_start 升序。"""
    d = datetime.strptime(ship_date, "%Y-%m-%d").date()
    pct = 0.0
    for row in fuel_card.get("weekly") or []:
        if datetime.strptime(row["week_start"], "%Y-%m-%d").date() <= d:
            pct = float(row["pct"])
        else:
            break
    return pct
