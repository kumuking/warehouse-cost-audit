# -*- coding: utf-8 -*-
"""把各家 3PL / 承运商格式各异的原始价表，转成统一口径的费率卡。

两步走，先看再抽，全程不猜价：

  1) inspect —— 只读不判，输出价表「结构指纹」：有哪些标签、每个标签右边/下边是什么数，
     有哪些单位线索。看完指纹再写 mapping，避免凭空想象价表长什么样。

  2) build —— 按 mapping 抽取，产出 canonical 费率卡，同时输出三份诊断：
     _unmapped.csv（有数值但没被任何规则接住的标签）
     _conflicts.csv（同一字段命中多个不同数值）
     _pending.md（口径存疑、必须人工确认的项）

用法：
    python rate_import.py inspect 价表.xlsx --out 指纹.md
    python rate_import.py build 价表.xlsx \\
        --mapping ../assets/rates/mapping/mapping_示例仓LA.json \\
        --out ../assets/rates/3pl-handling_示例仓LA_202611.json

设计原则：
    名称差异交给别名表自动处理；口径差异必须人工确认一次并固化进卡；
    接不住的项一律进 unmapped 报告，绝不静默丢弃、绝不用默认值兜底。
"""
import argparse
import csv
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------- 统一名称字典
# 这是脚本内部唯一认的「收费明细名称」。各家价表叫什么不重要，映射到这些 code 即可。
CANONICAL = {
    "handling": {
        "inbound.per_order": ("入库操作费，按单据", "金额/单"),
        "inbound.per_piece": ("入库操作费，按件", "金额/件"),
        "inbound.per_carton": ("入库操作费，按箱", "金额/箱"),
        "inbound.per_kg": ("入库操作费，按重量", "金额/kg"),
        "inbound.per_cbm": ("入库操作费，按体积", "金额/cbm"),
        "inbound.unload_fee": ("卸货费/装柜费", "金额/柜或/托"),
        "inbound.min_charge": ("入库最低收费", "金额/单"),
        "outbound.per_order": ("出库操作费，按单据（一件代发处理费）", "金额/单"),
        "outbound.per_piece": ("出库操作费，按件", "金额/件"),
        "outbound.per_piece_first": ("出库首件费", "金额/件"),
        "outbound.per_piece_extra": ("出库续件费", "金额/件"),
        "outbound.per_kg": ("出库操作费，按重量", "金额/kg"),
        "outbound.per_cbm": ("出库操作费，按体积", "金额/cbm"),
        "outbound.min_charge": ("出库最低收费", "金额/单"),
        "outbound.max_charge": ("出库封顶收费", "金额/单"),
        "outbound.oversize_surcharge": ("出库超尺寸附加费", "金额/件"),
        "outbound.return_fee": ("退件处理费/退货入库费", "金额/件"),
        "value_added": ("增值服务（贴标/换标/组套/拍照/质检等，名称自定）", "按项"),
    },
    "storage": {
        "storage.price_per_unit_month": ("仓储费单价（按月）", "金额/计费单位/月"),
        "storage.billing_unit": ("计费单位（cbm/cft/piece/pallet/bin）", "枚举"),
        "storage.free_days": ("免仓天数", "天"),
        "storage.min_charge_monthly": ("月度最低收费", "金额/月"),
        "storage.inbound_fee": ("入库上架费/入仓费", "金额/件或/托"),
        "storage.peak_multiplier": ("旺季附加系数", "倍"),
        "storage.age_tiers": ("库龄阶梯价（矩阵，用 matrix 抽取）", "按段"),
    },
    "carrier": {
        "carrier.base": ("基础运费（分区×重量表，用 matrix 抽取）", "金额/件"),
        "carrier.fuel_pct": ("燃油附加费率", "比例"),
        "carrier.residential_surcharge": ("住宅派送附加费", "金额/件"),
        "carrier.das": ("偏远地区附加费（DAS/Extended DAS）", "金额/件"),
        "carrier.ahs": ("AHS 超尺寸/超重附加费", "金额/件"),
        "carrier.oversize": ("Oversize 超大件附加费", "金额/件"),
        "carrier.return_fee": ("退件费/Return Charge", "金额/件"),
        "carrier.dim_divisor": ("体积重除数（139/5000/6000）", "数值"),
        "carrier.address_correction": ("地址更正费", "金额/件"),
        "carrier.delivery_signature": ("签收服务费", "金额/件"),
    },
}

# 常见别名种子：inspect 给出建议映射时用，mapping 里也可以直接引用
ALIAS_SEED = {
    "outbound.per_order": ["出库操作费", "出库费", "订单处理费", "一件代发", "操作费", "handling fee",
                           "outbound handling", "fulfillment fee", "pick and pack", "pick & pack",
                           "order processing", "processing fee", "per order"],
    "outbound.per_piece": ["出库按件", "按件收费", "每件操作费", "per piece", "per unit", "additional unit"],
    "outbound.per_piece_first": ["首件", "first item", "first unit", "first piece"],
    "outbound.per_piece_extra": ["续件", "加件", "additional", "extra item", "each additional"],
    "outbound.per_kg": ["按重量", "per kg", "per lb", "重量费"],
    "outbound.min_charge": ["最低收费", "起步价", "minimum charge", "min charge"],
    "outbound.oversize_surcharge": ["超长", "超尺寸", "oversize", "over size", "超规"],
    "outbound.return_fee": ["退件", "退货处理", "return fee", "return handling"],
    "inbound.per_order": ["入库费", "入库操作费", "inbound handling", "receiving fee", "收货费"],
    "inbound.per_piece": ["入库按件", "上架费", "per piece inbound", "putaway"],
    "inbound.unload_fee": ["卸货", "装柜", "unloading", "unload"],
    "storage.price_per_unit_month": ["仓储费", "仓租", "storage fee", "warehousing", "storage"],
    "storage.inbound_fee": ["入仓费", "上架费", "inbound storage"],
    "storage.min_charge_monthly": ["仓储最低", "月度最低", "minimum monthly"],
    "carrier.fuel_pct": ["燃油", "fuel", "fuel surcharge"],
    "carrier.residential_surcharge": ["住宅", "residential"],
    "carrier.das": ["偏远", "das", "delivery area", "extended"],
    "carrier.ahs": ["ahs", "超重附加", "additional handling"],
    "carrier.oversize": ["oversize", "超大件"],
    "carrier.return_fee": ["退件费", "return charge"],
    "carrier.dim_divisor": ["体积重", "dimensional weight", "dim divisor", "volumetric"],
}

VALID_VALUE_FROM = {"right", "right_all", "below", "inline"}

# ---------------------------------------------------------------- 读取价表


def _norm(s) -> str:
    """标签归一：全角转半角、去空白、小写。只用于匹配，不用于输出。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s))).lower()


def to_num(v):
    """把单元格转成数字。'$0.35/件' -> 0.35；'15.25%' -> 0.1525；'—' -> None。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in {"-", "—", "–", "/", "NA", "N/A", "n/a", "无"}:
        return None
    s = s.replace(",", "").replace("，", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    n = float(m.group())
    return n / 100 if "%" in s else n


def strict_num(v):
    """只认真正的数值单元格，不从长文本里抠数字。

    价表里「超长附加费（>120cm）」这类标签本身带数字，若用 to_num 宽松解析
    会被当成价格 120，把整张卡带歪。below 定位一律走这个严格版。
    """
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    m = re.fullmatch(r"-?\d+(?:\.\d+)?%?", s)
    if not m:
        return None
    t = m.group()
    return float(t.rstrip("%")) / 100 if t.endswith("%") else float(t)


def read_grid(path: Path):
    """返回 [(sheet_name, grid)]，grid 为二维列表，已按合并单元格左上值展开。"""
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        try:
            from openpyxl import load_workbook
        except ImportError:
            sys.exit("缺少 openpyxl：pip install openpyxl")
        wb = load_workbook(path, data_only=True)
        out = []
        for ws in wb.worksheets:
            grid = [[c.value for c in row] for row in ws.iter_rows()]
            # 合并单元格：把左上角的值铺到整个区域，否则标签只存在于第一格
            for rng in ws.merged_cells.ranges:
                top = ws.cell(rng.min_row, rng.min_col).value
                for r in range(rng.min_row - 1, rng.max_row):
                    for c in range(rng.min_col - 1, rng.max_col):
                        if r < len(grid) and c < len(grid[r]):
                            grid[r][c] = top
            out.append((ws.title, grid))
        return out
    if suffix in {".csv", ".txt"}:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        return [(path.stem, [list(r) for r in rows])]
    sys.exit(f"不支持的格式：{suffix}（支持 .xlsx/.xlsm/.csv）")


def col_letter(i: int) -> str:
    """0-based 列号 -> A/B/AA。"""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def is_label(v) -> bool:
    """是否为文本标签（非空、非纯数字、长度合理）。"""
    if v is None:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return False
    s = str(v).strip()
    return bool(s) and len(s) <= 60 and re.search(r"[A-Za-z\u4e00-\u9fff]", s) is not None


# ---------------------------------------------------------------- inspect


UNIT_HINTS = {
    "currency": r"USD|CAD|GBP|RMB|CNY|美元|加元|英镑|人民币|\$|￥|¥",
    "weight": r"\bkg\b|\bKG\b|\blb\b|\blbs\b|公斤|千克|磅",
    "volume": r"\bcbm\b|\bcft\b|立方|方|材积",
    "pallet": r"\bpallet\b|\bplt\b|托盘|托\b",
    "piece": r"\bpc\b|\bpcs\b|\bunit\b|件|个|支",
    "order": r"\border\b|\bso\b|单|票|订单",
    "time": r"\bday\b|\bmonth\b|天|日|月",
}


def cmd_inspect(path: Path, out_path: Path | None, max_rows: int):
    grids = read_grid(path)
    lines = [f"# 价表结构指纹：{path.name}", ""]
    all_text = []

    for sheet, grid in grids:
        rows_used = [r for r in grid if any(c is not None and str(c).strip() for c in r)]
        width = max((len(r) for r in grid), default=0)
        lines.append(f"## Sheet「{sheet}」 {len(grid)} 行 × {width} 列（有效行 {len(rows_used)}）")
        lines.append("")
        lines.append("| 位置 | 标签内容 | 右侧数值 | 下方数值 |")
        lines.append("|---|---|---|---|")
        shown = 0
        for ri, row in enumerate(grid):
            for ci, v in enumerate(row):
                if not is_label(v):
                    continue
                # 紧跟在标签右侧的短文本是取值（如 A15=计费单位 B15=cbm），不是标签
                if ci > 0 and is_label(row[ci - 1]) and len(str(v).strip()) <= 6:
                    continue
                label = str(v).strip()
                all_text.append(label)

                right = []
                for cj in range(ci + 1, len(row)):
                    n = to_num(row[cj])
                    if n is not None:
                        right.append(n)
                    elif right:  # 已经取到数字后又遇文本，停止（避免串到下一列项目）
                        break
                    if len(right) >= 3:
                        break

                below = None
                for rj in range(ri + 1, min(ri + 6, len(grid))):
                    if ci < len(grid[rj]):
                        n = strict_num(grid[rj][ci])
                        if n is not None:
                            below = n
                            break

                if not right and below is None:
                    continue  # 纯标题行，不占篇幅
                shown += 1
                if shown > max_rows:
                    continue
                pos = f"{sheet}!{col_letter(ci)}{ri + 1}"
                rt = ", ".join(f"{x:g}" for x in right) if right else ""
                lines.append(f"| {pos} | {label} | {rt} | {'' if below is None else f'{below:g}'} |")
        if shown > max_rows:
            lines.append(f"| … | 另有 {shown - max_rows} 行含数值的标签未展示 | | |")
        lines.append("")

    joined = "\n".join(all_text)
    lines.append("## 单位线索")
    lines.append("")
    for k, pat in UNIT_HINTS.items():
        hits = sorted(set(re.findall(pat, joined, flags=re.I)))[:8]
        lines.append(f"- **{k}**：{'、'.join(h.strip() for h in hits) if hits else '未发现'}")
    lines.append("")
    lines.append("> 只做了结构识别，没有推断任何价格。下一步照此写 mapping 再 build。")

    text = "\n".join(lines)
    if out_path:
        out_path.write_text(text, encoding="utf-8")
        print(f"结构指纹 {len(lines)} 行 -> {out_path}")
    else:
        print(text)


# ---------------------------------------------------------------- build


def suggest_field(label: str) -> tuple[str, float]:
    """给未匹配标签一个候选字段 + 粗略置信度，帮人工确认，不自动采用。"""
    n = _norm(label)
    best, score = "", 0.0
    for code, kws in ALIAS_SEED.items():
        for kw in kws:
            if _norm(kw) in n or n in _norm(kw):
                s = 0.9 if len(_norm(kw)) > 3 else 0.7
                if s > score:
                    best, score = code, s
    if best:
        return best, score
    pool = {c: f"{c} {d[0]}" for c, d in CANONICAL["handling"].items()}
    pool.update({c: f"{c} {d[0]}" for c, d in CANONICAL["storage"].items()})
    pool.update({c: f"{c} {d[0]}" for c, d in CANONICAL["carrier"].items()})
    m = difflib_get_close(label, pool)
    return (m, 0.45) if m else ("", 0.0)


def difflib_get_close(label: str, pool: dict) -> str:
    import difflib
    cand = difflib.get_close_matches(label, list(pool.values()), n=1, cutoff=0.55)
    if not cand:
        return ""
    for code, desc in pool.items():
        if desc == cand[0]:
            return code
    return ""


def build_rules(mapping: dict):
    """展开成 (归一关键词, rule) 并按关键词长度降序，保证「出库操作费(最低收费)」优先于「出库操作费」。"""
    pairs = []
    for rule in mapping.get("rules") or []:
        field = rule["field"]
        vf = rule.get("value_from", "right")
        if vf not in VALID_VALUE_FROM:
            sys.exit(f"value_from 非法：{vf}（可选 {sorted(VALID_VALUE_FROM)}）")
        for kw in rule.get("match") or []:
            pairs.append((_norm(kw), rule))
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return pairs


def place(cards: dict, field: str, value):
    """canonical code -> 对应费率卡的真实位置。

    按 field 前缀分段，而不是按 mapping 声明的 kind：一家 3PL 的同一张价表
    往往既有操作费又有仓储费，硬按单一 kind 落卡会把 storage.* 写成顶层
    字面键，生成的卡静默失效。
    """
    head, _, key = field.partition(".")
    if head in {"inbound", "outbound"}:
        cards["handling"].setdefault(head, {})[key] = value
    elif head == "storage":
        c = cards["storage"]
        if field == "storage.age_tiers":
            c["age_tiers"] = value
        elif field == "storage.peak_multiplier":
            c.setdefault("peak_surcharge", {})["multiplier"] = value
        else:
            c[key] = value
    else:
        c = cards["carrier"]
        if field == "carrier.base":
            c["zones"] = value
        else:
            c[key] = value


# 计费基准关键词：用来抓「名称映射对了、但计费单位其实是另一回事」的隐性错误
UNIT_BY_SUFFIX = {
    "per_order": (("单", "票", "order"), "按单"),
    "per_piece": (("件", "个", "pc", "pcs", "unit", "支", "套"), "按件"),
    "per_piece_first": (("件", "个", "pc", "unit", "支", "套"), "按件（首件）"),
    "per_piece_extra": (("件", "个", "pc", "unit", "支", "套"), "按件（续件）"),
    "per_kg": (("kg", "公斤", "千克", "lb", "磅"), "按重量"),
    "per_cbm": (("cbm", "立方", "方", "m3"), "按体积"),
    "per_carton": (("箱", "carton", "ctn"), "按箱"),
    "per_pallet": (("托", "pallet", "plt"), "按托"),
}


def unit_conflict(field: str, unit_text: str):
    """标签语义与单位列是否矛盾。返回冲突描述，无冲突返回 None。

    这是最容易静默算错钱的一类错：把「0.35 USD/件」当成「0.35 USD/单」，
    名称映射全对、数字也抽对了，但整套操作费会被低估到零头。
    """
    suffix = field.split(".")[-1]
    if suffix not in UNIT_BY_SUFFIX or not unit_text.strip():
        return None
    kws, label = UNIT_BY_SUFFIX[suffix]
    n = _norm(unit_text)
    if any(_norm(k) in n for k in kws):
        return None
    for other, (okws, olabel) in UNIT_BY_SUFFIX.items():
        if other == suffix:
            continue
        if any(_norm(k) in n for k in okws):
            return f"字段要求{label}，但单位列是「{unit_text.strip()}」（{olabel}）"
    return None


def match_rule(nlabel: str, rules):
    """返回 (最优 rule, 命中其它字段的候选)。

    纯字符串匹配一定会在「出库操作费」与「出库操作费（最低收费）」这类
    同根不同义的标签上翻车。所以排序用「完全相等 > 关键词覆盖率」，并且
    只要同时命中多个字段就回落歧义报告，由人确认，不自动定夺。
    """
    cands = []
    for nkw, rule in rules:
        if not nkw or nkw not in nlabel:
            continue
        cands.append((
            1 if nkw == nlabel else 0,
            round(len(nkw) / max(len(nlabel), 1), 4),
            len(nkw), rule, nkw,
        ))
    if not cands:
        return None, []
    cands.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    best = cands[0][3]
    best_cover = cands[0][1]
    # 只报「咬得比较紧」的候选，避免「出库操作费(最低收费)」这类从属标签每次都刷告警
    others = [{"field": c[3]["field"], "keyword": c[4], "coverage": c[1]}
              for c in cands[1:]
              if c[3]["field"] != best["field"] and c[1] >= best_cover * 0.75]
    return best, others


SECTION_RE = re.compile(r"^(?:[一二三四五六七八九十]+[、.．]|[（(][一二三四五六七八九十\d]+[）)]|\d+[、.．])")


def cmd_build(src: Path, mapping_path: Path, outdir: Path):
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    rules = build_rules(mapping)
    only_sheet = mapping.get("sheet")
    scale = float(mapping.get("scale") or 1)
    vendor = mapping["vendor"]
    eff = mapping["effective_from"]

    header = {
        "currency": mapping.get("currency"),
        "effective_from": eff,
        "effective_to": mapping.get("effective_to"),
    }
    cards = {
        "handling": {"vendor": vendor,
                     "warehouse_names": mapping.get("warehouse_names") or [vendor], **header},
        "storage": {"vendor": vendor,
                    "warehouse_names": mapping.get("warehouse_names") or [vendor], **header},
        "carrier": {"carrier": vendor, **header},
    }

    found: dict[str, list] = {}
    unmapped, conflicts, pending, ambiguous, unit_mismatch = [], [], [], [], []
    need_review: dict[str, list] = {}
    units_seen: dict[str, str] = {}

    for sheet, grid in read_grid(src):
        if only_sheet and sheet != only_sheet:
            continue
        for ri, row in enumerate(grid):
            for ci, v in enumerate(row):
                if not is_label(v):
                    continue
                if ci > 0 and is_label(row[ci - 1]) and len(str(v).strip()) <= 6:
                    continue  # 标签右侧的取值短文本，不是新项目
                label = str(v).strip()
                nlabel = _norm(label)

                right_nums = []
                unit_text_parts = []
                for cj in range(ci + 1, len(row)):
                    cell = row[cj]
                    n = to_num(cell)
                    if n is not None:
                        right_nums.append(n)
                        if len(right_nums) >= 6:
                            break
                    elif right_nums:
                        if cell not in (None, ""):
                            unit_text_parts.append(str(cell).strip())
                        if len(unit_text_parts) >= 2:
                            break
                    elif cell not in (None, ""):
                        # 数值前的文本，通常是单位列（如 "USD/件" 写在价格左边）
                        unit_text_parts.append(str(cell).strip())

                hit, others = match_rule(nlabel, rules)
                if hit and others:
                    ambiguous.append({
                        "sheet": sheet, "cell": f"{col_letter(ci)}{ri + 1}",
                        "label": label, "picked": hit["field"],
                        "also_matched": " | ".join(f"{o['field']}({o['keyword']})" for o in others),
                    })
                if not hit:
                    if right_nums or to_num(label) is not None:
                        f, s = suggest_field(label)
                        unmapped.append({
                            "sheet": sheet, "cell": f"{col_letter(ci)}{ri + 1}", "label": label,
                            "right_values": right_nums,
                            "suggested_field": f, "confidence": s,
                        })
                    continue

                field = hit["field"]
                vf = hit.get("value_from", "right")
                is_text = hit.get("value_type") == "text"
                if is_text:  # billing_unit 这类枚举取值，不参与数字缩放
                    val = next((str(row[cj]).strip() for cj in range(ci + 1, len(row))
                                if row[cj] not in (None, "")), None)
                    if val is not None and strict_num(val) is not None:
                        pending.append(f"`{sheet}!{col_letter(ci)}{ri + 1}` 「{label}」应为文本枚举，"
                                       f"取到的是数字 {val}，列可能错位")
                        continue
                elif vf == "right":
                    val = right_nums[0] if right_nums else None
                elif vf == "right_all":
                    val = right_nums
                elif vf == "below":
                    val = None
                    for rj in range(ri + 1, min(ri + 8, len(grid))):
                        if ci < len(grid[rj]):
                            val = strict_num(grid[rj][ci])
                            if val is not None:
                                break
                else:  # inline
                    val = to_num(label)

                if val is None:
                    if not SECTION_RE.match(label):
                        pending.append(
                            f"`{sheet}!{col_letter(ci)}{ri + 1}` 「{label}」命中规则 {field}，但取不到数值")
                    continue
                if hit.get("unit"):
                    units_seen[field] = hit["unit"]
                conf = unit_conflict(field, " ".join(unit_text_parts))
                if conf:
                    unit_mismatch.append({
                        "sheet": sheet, "cell": f"{col_letter(ci)}{ri + 1}",
                        "label": label, "field": field, "unit_text": " ".join(unit_text_parts),
                        "issue": conf,
                    })
                    pending.append(f"`{sheet}!{col_letter(ci)}{ri + 1}` 「{label}」{conf}——"
                                   f"计量基准可能整项搞错，务必核对合同")
                    need_review.setdefault(field.partition(".")[0], []).append(field)
                if not is_text:
                    if isinstance(val, list):
                        val = [round(x * (hit.get("scale", scale)), 6) for x in val]
                    else:
                        val = round(val * (hit.get("scale", scale)), 6)
                found.setdefault(field, []).append((f"{sheet}!{col_letter(ci)}{ri + 1}", val, label))

    # ---- matrix：阶梯表/分区表这类二维结构，必须显式声明行列，不自动猜
    for mx in mapping.get("matrix") or []:
        if mx.get("_disabled"):
            continue
        field = mx["field"]
        target_sheet = mx.get("sheet") or only_sheet
        grid = next((g for s, g in read_grid(src) if s == target_sheet), None)
        if grid is None:
            pending.append(f"matrix {field} 指定的 sheet「{target_sheet}」不存在")
            continue
        rows = []
        for r in range(mx["row_start"] - 1, mx["row_end"]):
            if r >= len(grid):
                break
            item = {}
            for key, col in (mx["col_map"] or {}).items():
                if len(col) != 1 or not col.isalpha():
                    pending.append(f"matrix {field} 列「{col}」只支持单字母列（A/B/AA 之外的合并列请手工填卡）")
                    continue
                ci = ord(col.upper()) - 65
                cell = grid[r][ci] if ci < len(grid[r]) else None
                num = to_num(cell)
                item[key] = num if num is not None else cell
            if all(v is None for v in item.values()):
                continue
            rows.append(item)
        if rows:
            found.setdefault(field, []).append((f"{target_sheet}!{mx['row_start']}-{mx['row_end']}", rows, field))

    # ---- 落卡：同一字段多值 -> 冲突；同值 -> 只留一次
    for field, hits in found.items():
        vals = [h[1] for h in hits]
        uniq = []
        for v in vals:
            if v not in uniq:
                uniq.append(v)
        if field.startswith("value_added"):
            pending.append(f"增值服务项「{hits[0][2]}」= {uniq[0]}：增值项名称与单位各家差异极大，"
                           f"不自动入卡，请人工补到 value_added 数组")
            continue
        if len(uniq) > 1:
            conflicts.append({"field": field, "values": [str(u) for u in uniq],
                              "cells": [h[0] for h in hits]})
            pending.append(f"`{field}` 命中 {len(uniq)} 个不同数值 {uniq}，已取第一个 {uniq[0]}，需确认")
            need_review.setdefault(field.partition(".")[0], []).append(field)
        place(cards, field, uniq[0])

    # ---- 口径校验：缺关键字段不生成该段卡，避免「看起来能用」的错卡
    def body(card: dict) -> dict:
        return {k: v for k, v in card.items()
                if k not in {"vendor", "carrier", "warehouse_names", "currency",
                             "effective_from", "effective_to"}}

    blockers = []
    if not mapping.get("currency"):
        blockers.append("currency 未声明（不跨币种相加是硬规则，必须写清）")
    if body(cards["handling"]) and not cards["handling"].get("outbound"):
        blockers.append("抽到了操作费字段但 outbound 段为空，出库操作费无从算起")
    if body(cards["storage"]):
        if not cards["storage"].get("billing_unit"):
            blockers.append("billing_unit 未确认（cbm/cft/piece/pallet/bin 决定整套算法量纲）")
        if cards["storage"].get("price_per_unit_month") is None:
            blockers.append("storage 抽到字段但 price_per_unit_month 未取到")
    if body(cards["carrier"]):
        if not cards["carrier"].get("dim_divisor"):
            blockers.append("dim_divisor 未确认（139/5000/6000）")
        if not cards["carrier"].get("zones"):
            blockers.append("zones 分区费率表未抽取（用 matrix 段声明行列）")
    if not any(body(c) for c in cards.values()):
        blockers.append("一条规则都没抓到值，检查 sheet 名与 rules 关键词")

    meta = {
        "source_file": src.name,
        "mapping": mapping_path.name,
        "imported_on": date.today().isoformat(),
        "units": units_seen,
        "unmapped_count": len(unmapped),
        "pending_confirmation": pending,
    }

    # ---- 诊断落盘
    outdir.mkdir(parents=True, exist_ok=True)
    safe_vendor = re.sub(r"[\\/:*?\"<>|\s]+", "_", vendor)
    diag = outdir / f"_import_{safe_vendor}"
    if unmapped:
        p = Path(f"{diag}_unmapped.csv")
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["sheet", "cell", "label", "right_values",
                                              "suggested_field", "confidence"])
            w.writeheader()
            for u in unmapped:
                w.writerow({**u, "right_values": ",".join(f"{x:g}" for x in u["right_values"])})
        print(f"⚠️ 未匹配 {len(unmapped)} 项 -> {p.name}（有数值但没进卡，必须逐条确认）")
    if conflicts:
        p = Path(f"{diag}_conflicts.csv")
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["field", "values", "cells"])
            w.writeheader()
            for c in conflicts:
                w.writerow({"field": c["field"], "values": " | ".join(c["values"]),
                            "cells": " | ".join(c["cells"])})
        print(f"⚠️ 字段冲突 {len(conflicts)} 处 -> {p.name}")
    if ambiguous:
        p = Path(f"{diag}_ambiguous.csv")
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["sheet", "cell", "label", "picked", "also_matched"])
            w.writeheader()
            w.writerows(ambiguous)
        print(f"⚠️ 一个标签同时命中多个字段 {len(ambiguous)} 处 -> {p.name}（口径存疑，务必核对）")
    if unit_mismatch:
        p = Path(f"{diag}_unit_mismatch.csv")
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["sheet", "cell", "label", "field", "unit_text", "issue"])
            w.writeheader()
            w.writerows(unit_mismatch)
        print(f"❌ 计费基准与单位列矛盾 {len(unit_mismatch)} 处 -> {p.name}（这类错会静默低估费用）")

    if blockers:
        print("❌ 有阻塞项，未生成费率卡：")
        for b in blockers:
            print(f"   - {b}")
        return 2

    ym = eff.replace("-", "")[:6]
    prefix = {"handling": "3pl-handling", "storage": "3pl-storage", "carrier": "carrier"}
    written = []
    for kind, card in cards.items():
        if not body(card):
            continue
        card["_meta"] = {**meta, "needs_review": sorted(set(need_review.get(kind, [])))}
        path = outdir / f"{prefix[kind]}_{safe_vendor}_{ym}.json"
        path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append((kind, path, card))

    print(f"✅ 生成 {len(written)} 张费率卡（{outdir}）：")
    for kind, path, card in written:
        print(f"\n── {kind} · {path.name}")
        for k, v in card.items():
            if k.startswith("_") or k in {"vendor", "carrier", "warehouse_names",
                                          "currency", "effective_from", "effective_to"}:
                continue
            print(f"   {k}: {json.dumps(v, ensure_ascii=False)}")
    print(f"\n待确认 {len(pending)} 条")
    for x in pending:
        print(f"  · {x}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="原始价表 -> 统一口径费率卡")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("inspect", help="只看结构，不抽价")
    p1.add_argument("src", type=Path)
    p1.add_argument("--out", type=Path)
    p1.add_argument("--max-rows", type=int, default=200)

    p2 = sub.add_parser("build", help="按 mapping 抽价生成费率卡")
    p2.add_argument("src", type=Path)
    p2.add_argument("--mapping", type=Path, required=True)
    p2.add_argument("--out", type=Path, required=True,
                    help="费率卡输出目录，文件名按 schema 规范自动生成")

    args = ap.parse_args()
    if args.cmd == "inspect":
        cmd_inspect(args.src, args.out, args.max_rows)
    else:
        sys.exit(cmd_build(args.src, args.mapping, args.out))


if __name__ == "__main__":
    main()
