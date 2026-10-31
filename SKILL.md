---
name: warehouse-cost-audit
description: 跨境电商海外仓（3PL）仓内费用核算与账单对账。当需要核算或核对月度入库操作费、出库操作费、仓储费（含库龄阶梯）、尾程快递费（基础运费/燃油附加/AHS/退件费），或需要把 3PL 与承运商账单与系统数据逐单比对、产出多收少收差异与申诉清单时使用。适用于美国、加拿大、英国的第三方海外仓与 FedEx/UPS/Purolator/GoFo/OnTrac/Canada Post 等承运商。
agent_created: true
---

# 仓配费用核算与对账

按「费率卡 + 系统数据」重算 3PL 仓内费用与尾程运费，并与供应商账单逐单比对，定位多收、少收和不可核实的收费项。

## 何时使用

- 月度 3PL 账单到了，需要核对操作费、仓储费是否与合同价一致
- 需要知道每个仓每月的仓内成本、单均操作费、单件仓储成本
- 承运商（FedEx/UPS 等）账单疑似多收 AHS 附加费、燃油计算有误
- 需要判断退件费收取时包裹是否真实退回
- 需要横向比较多个 3PL 的性价比

## 运行前提（缺一不可）

1. **费率卡**：`assets/rates/` 下每个 3PL、每个承运商各一张，格式见 `references/rate_card_schema.md`。缺卡时脚本必须报错终止，不得使用默认价。
2. **计费口径确认**：`references/assumptions.md` 中标 `[待确认]` 的项必须先与业务确认，尤其是体积重除数、仓储计费单位、燃油基准。
3. **领星 API 可达**：家庭宽带出口 IP 变更会导致白名单失效，用 `healthcheck.py` 先验。

## 工作流

### 步骤 0：启动自检

```bash
python scripts/healthcheck.py --cred ~/领星api.txt --orders wms出库单.jsonl
```

自检不过（API 不通、费率卡缺失）立即停止，不要带着缺口跑数。API 不通优先排查 IP 白名单。

### 步骤 1：取数

```bash
python scripts/pull_month.py --cred ~/领星api.txt \
    --start 2026-10-01 --end 2026-10-31 --outdir data/2026-10
```

一次拉齐出库单、备货单、库存快照、仓位流水并落成本地文件；后续计费脚本只读本地文件，避免重复调 API（限流 1 req/s）。

需要单独取数时用 `lingxing.py`：

```python
from lingxing import Lingxing
lx = Lingxing.from_cred("~/领星api.txt")
lx.wms_orders("2026-10-01", "2026-11-01")          # 出库单：尺寸/重量/邮编/运费
lx.inbound_orders("2026-10-01", "2026-11-01")      # 备货单：实收件数/收货时间
lx.bin_statement(wid, "2026-09-01", "2026-11-01")  # 仓位流水：重建日结存
lx.inventory_snapshot(wid)                          # 当前快照：回推锚点
```

**各接口翻页方式不同**（出库单只认 `page`，仓位流水只认 `offset`），务必用 `paginate(mode=...)`，不要自己拼翻页——传错会静默丢数据。

### 步骤 2：算三类费用

```bash
python scripts/calc_handling.py --orders 出库单.jsonl --inbound 备货单.json --out 操作费.csv
python scripts/calc_storage.py --snapshot 快照.jsonl --flows 流水.jsonl --specs SKU规格.json --month 2026-10 --out 仓储费.csv
python scripts/calc_freight.py --orders 出库单.jsonl --zipdb geonames_US.json --out 运费核算.csv
```

### 步骤 3：与账单对账

```bash
python scripts/reconcile.py --calc 运费核算.csv --bill bill.csv \
  --bill-key "Tracking Number" --bill-amount "Net Charge" --out 差异明细.csv
```

账单列名各供应商不同，一律用参数映射，不改代码。

### 步骤 4：出报告

汇总为「分仓分月费用表 + 差异明细 + 申诉清单」，按供应商和费用类型分组，标注多收合计、少收合计、净额。

## 硬性规则

- **不跨币种相加**。同一张表里 USD、CAD、GBP 必须分行或分列，汇总时按币种分列。
- **费率卡命中必须唯一**。命中 0 张或多张即报错终止，绝不猜测用哪一版价格。
- **`[待确认]` 口径不得静默取默认值**，必须在输出中带警告标记。
- **`logistics_freight` 无值（约 15%）的订单**标为「无实收值」，不计入差异统计，单独列示。
- **领星只有运费总额，没有基础/燃油/AHS 拆分**。要逐项核实必须有承运商账单；只有价表时只能核总额，必须在结论中明确说明这一限制。
- **退件费必须三方交叉验证**：承运商轨迹 + 退货入库流水 + 退货单，三者缺一即标为「待举证」，不得直接认定为真实退回。
- **脚本只输出聚合结果，不打印明细**。任何超过 200 行的中间结果必须落盘为 CSV/JSONL，stdout 只打印汇总与异常摘要。3 万单的逐单明细若直接打印，会把上百万 token 灌进上下文，既昂贵又无助于核算——算术交给 Python，判断才交给模型。
- **模型不做逐单数值计算**。zone 判定、计费重、阶梯价、库龄累加一律由 `scripts/` 完成；模型只负责读汇总、判异常、写结论、给申诉建议。

## 数据已知缺口

- 计费重 `pkg_fee_weight` 约 91% 为 0，必须用「实重 vs 尺寸体积重取大」自算
- `track_record` 基本为空，判断真实退回需另接轨迹接口
- 仓位流水自领星启用 ERP 起才有记录，更早的仓储费只能靠期初盘点倒推

## 参考文件

- `references/rate_card_schema.md` — 四类费率卡字段规范与匹配规则
- `references/assumptions.md` — 全部口径约定、待确认项、每次跑数必查的完整性校验
- `references/lingxing_api.md` — 领星接口清单、字段映射、已知坑
