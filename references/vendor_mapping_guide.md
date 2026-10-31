# 供应商价表映射指南

各家 3PL、承运商的价表格式和收费明细叫法都不一样，这是常态，不是例外。
本 skill 的处理方式是：**名称差异交给映射表自动处理，口径差异必须人工确认一次并固化进卡**，
两者都不靠模型临场猜。

## 一、先分清三类差异，只有第一类能全自动

| 类型 | 表现 | 处理方式 |
|---|---|---|
| **A. 纯名称差异** | 同一件事，叫法不同：「出库操作费 / Outbound Handling / Fulfillment Fee / Pick & Pack / 一件代发处理费」 | 写进 mapping 的 `match` 别名数组，自动归一 |
| **B. 同根不同义** | 「出库操作费」和「出库操作费（最低收费）」是两个字段；「入库费」有的按单、有的按件、有的按柜 | 一个标签一条显式规则；口径必须确认后写进卡 |
| **C. 该仓独有项** | 「超区派送加价」「旺季爆仓附加」「旺季人力附加」「退件销毁费」 | 不自动入卡，进 `_unmapped.csv`，人工决定建模或忽略 |

**关键认知：A 类占八成，看起来最麻烦，其实最省事；真正会算错钱的是 B 类。**
名称对不上，脚本会报未匹配，你看得见；口径理解错了，数字照样算出来，你不会发现。

## 二、统一名称（canonical code）

脚本内部只认这套名字，各家价表叫什么无所谓，映射到这些 code 即可。

### 操作费 handling

| code | 含义 | 单位 |
|---|---|---|
| `inbound.per_order` | 入库操作费，按单据 | 金额/单 |
| `inbound.per_piece` | 入库操作费，按件 | 金额/件 |
| `inbound.per_carton` | 入库操作费，按箱 | 金额/箱 |
| `inbound.per_kg` | 入库操作费，按重量 | 金额/kg |
| `inbound.per_cbm` | 入库操作费，按体积 | 金额/cbm |
| `inbound.unload_fee` | 卸货费 / 装柜费 | 金额/柜或/托 |
| `inbound.min_charge` | 入库最低收费 | 金额/单 |
| `outbound.per_order` | 出库操作费，按单据 | 金额/单 |
| `outbound.per_piece` | 出库操作费，按件（均一价） | 金额/件 |
| `outbound.per_piece_first` | 出库首件费 | 金额/件 |
| `outbound.per_piece_extra` | 出库续件费 | 金额/件 |
| `outbound.per_kg` | 出库操作费，按重量 | 金额/kg |
| `outbound.per_cbm` | 出库操作费，按体积 | 金额/cbm |
| `outbound.min_charge` | 出库最低收费 | 金额/单 |
| `outbound.max_charge` | 出库封顶收费 | 金额/单 |
| `outbound.oversize_surcharge` | 超尺寸附加 | 金额/件 |
| `outbound.return_fee` | 退件处理 / 退货入库 | 金额/件 |

### 仓储费 storage

| code | 含义 |
|---|---|
| `storage.price_per_unit_month` | 仓储单价（按计费单位/月） |
| `storage.billing_unit` | 计费单位，**文本枚举**：cbm / cft / piece / pallet / bin |
| `storage.free_days` | 免仓天数 |
| `storage.min_charge_monthly` | 月度最低收费 |
| `storage.inbound_fee` | 入库上架 / 入仓费 |
| `storage.peak_multiplier` | 旺季附加系数 |
| `storage.age_tiers` | 库龄阶梯价（二维表，用 `matrix` 抽） |

### 承运商 carrier

| code | 含义 |
|---|---|
| `carrier.base` | 分区×重量费率表（二维表，落成 `zones`） |
| `carrier.fuel_pct` | 燃油附加费率 |
| `carrier.residential_surcharge` | 住宅派送附加 |
| `carrier.das` | 偏远地区附加（DAS / Extended DAS） |
| `carrier.ahs` | AHS 超尺寸/超重附加 |
| `carrier.oversize` | Oversize 超大件附加 |
| `carrier.return_fee` | 退件费 |
| `carrier.dim_divisor` | 体积重除数（139 / 5000 / 6000） |
| `carrier.address_correction` | 地址更正费 |
| `carrier.delivery_signature` | 签收服务费 |

## 三、mapping 怎么写

一个供应商一份 `assets/rates/mapping/mapping_<供应商>.json`。

```json
{
  "vendor": "示例仓LA",
  "kind": "handling",
  "warehouse_names": ["示例仓LA", "示例仓LA-2"],
  "currency": "USD",
  "effective_from": "2026-11-01",
  "effective_to": "2026-12-31",
  "sheet": "报价明细",
  "scale": 1,
  "rules": [
    {"match": ["出库操作费（最低收费）"], "field": "outbound.min_charge", "unit": "USD/单"},
    {"match": ["出库操作费", "Outbound Handling", "Fulfillment Fee"], "field": "outbound.per_piece", "unit": "USD/件"},
    {"match": ["计费单位", "Billing Unit"], "field": "storage.billing_unit", "value_type": "text"}
  ],
  "matrix": [],
  "ignore": ["备注", "说明", "Note"]
}
```

字段说明：

- `match`：标签关键词数组，命中任意一个即算这个字段。比较前会做全角转半角、去空格、转小写。
- `value_from`：`right`（默认，同行右侧第一个数值）/ `below`（同列下方第一个数值）/ `inline`（从标签文字里抠数字）/ `right_all`（右侧全部数值，得到数组）。
- `value_type`：默认 `number`；写 `text` 表示取文本（只有 `storage.billing_unit` 需要）。
- `scale`：整表或单条规则的缩放系数。价表按「每 100 件」报价就写 `0.01`。
- `unit`：原表单位，只记进 `_meta.units` 供核对，不参与计算。
- `sheet`：只扫指定 sheet，避免把「历史报价」「附件」页一起抽进来。
- `matrix`：二维表（重量段价格、分区费率、库龄阶梯）必须显式声明行列区间：

```json
{"field": "outbound.tiers", "sheet": "报价", "row_start": 20, "row_end": 24,
 "col_map": {"max_kg": "A", "per_order": "B", "per_piece_first": "C"}}
```

**匹配优先级**：标签完全相等 > 关键词覆盖率（命中字数 / 标签总字数）> 关键词长度。
所以「出库操作费（最低收费）」会命中专属规则，不会掉到「出库操作费」上。
但如果两条规则咬得很紧，脚本不会替你决定，会在 `_ambiguous.csv` 里列出来。

## 四、必须人工确认的口径（这是真正的雷区）

同一句话在不同供应商的合同里含义不同。以下每一条都至少要确认一次，确认后写进卡并注明依据：

1. **计费基准**：按单、按件、按 kg、按 cbm、按托、按库位、按箱——这是量纲，错了整月全错。
2. **首件+续件，还是均一价**：拆成两项时用 `per_piece_first` + `per_piece_extra`，一项全包时用 `per_piece`。
3. **出库费是否含包材**：有的含，有的另收 `material fee`，后者要归到增值项。
4. **最低收费的粒度**：按单收、按票（含多单）收、还是按天收。`min_charge` 只支持按单。
5. **体积重除数**：139（in³/lb）、166、5000、6000（cm³/kg）都有，且要确认尺寸单位。
6. **重量进位**：整磅、半磅、0.5kg、0.1kg，差一档就是差几毛到几块。
7. **仓储计费单位**：cbm / cft / 托 / 库位 / 件。还要问清是「按实际占用体积」还是「按库位数量」。
8. **库龄起算点**：到仓日、上架日、入库单创建日，三者可能差一周。本 skill 默认按流水到仓日。
9. **免仓期口径**：按批次分别计，还是按 SKU 首次入仓统一计。
10. **旺季附加**：倍数、固定加价、还是单独一套阶梯价。`peak_multiplier` 只支持倍数。
11. **AHS 阈值**：FedEx/UPS 都是 48in / 30in / 50lb，但部分合约会改；触发即收，不叠加则不收第二条。
12. **燃油基数**：是否含 AHS 等附加费、按周还是按月、按发货日还是账单周期取值。
13. **退件费**：真实退回才收，还是生成退件单就收——这一条直接决定申诉能不能成立。
14. **入库费是否含卸货**：含则不要再单列 `unload_fee`，否则重复计费。
15. **贴标/换标**：按件、按 SKU、还是按批次有最低收费。

确认结果写进卡是唯一可靠的记忆方式。只写在聊天里，下个月换个人接手就会重新踩一遍。

## 五、操作流程

```bash
# 1. 先看结构，不抽价。确认 sheet 名、标签长什么样、单位写在哪一列
python scripts/rate_import.py inspect 供应商报价.xlsx --out 指纹.md

# 2. 照指纹写 mapping_<供应商>.json（从 _template_mapping.json 复制）

# 3. 抽价，按段落自动生成 handling / storage / carrier 多张卡
python scripts/rate_import.py build 供应商报价.xlsx \
    --mapping ../assets/rates/mapping/mapping_示例仓LA.json \
    --out ../assets/rates
```

`build` 会产出三份诊断，**必须逐条看完再投入核账**：

| 文件 | 含义 | 处理 |
|---|---|---|
| `_import_<vendor>_unmapped.csv` | 表里有数值，但没被任何规则接住 | 逐条决定：补规则 / 归增值项 / 确认忽略 |
| `_import_<vendor>_conflicts.csv` | 同一字段抽到多个不同数值（多半是标签有从属关系） | 改 mapping 拆开，或确认取哪个 |
| `_import_<vendor>_ambiguous.csv` | 一个标签同时咬中多个字段 | 一定要人看，这类错会静默算错钱 |

卡里的 `_meta.pending_confirmation` 和 `_meta.needs_review` 记录了未决项。
**有 `needs_review` 非空的卡不得直接用于对账**——它意味着某个费率取了「第一个值」但存在竞争值。

## 六、一个真实的对照示例

同一件事，三家写法：

| 项目 | A 仓（中文表） | B 仓（英文表） | C 仓（混合表） |
|---|---|---|---|
| 出库操作 | 出库操作费 0.35/件 | Fulfillment Fee $0.42/order + $0.18/additional unit | 一件代发处理费：首件 0.5，续件 0.25 |
| 最低收费 | 出库操作费（最低收费）1.5/单 | Minimum Charge $2.00 | 每票最低 1.8 |
| 仓储 | 仓储费 0.85 USD/cbm/月 | Storage $0.62/cft/month | 仓租：0.9/立方/月，计费单位 cbm |
| 计费重 | 未注明 | DIM divisor 139 | 体积重按 5000 折算 |

映射后分别是：

- A：`outbound.per_piece=0.35`，`outbound.min_charge=1.5`
- B：`outbound.per_order=0.42`，`outbound.per_piece_extra=0.18`，`outbound.min_charge=2.00`，`carrier.dim_divisor=139`
- C：`outbound.per_piece_first=0.5`，`outbound.per_piece_extra=0.25`，`outbound.min_charge=1.8`，`billing_unit=cbm`

注意 B 的 `/cft/month` 与 A、C 的 `/cbm/month` 量纲不同，**不能横向直接比价**（1 cbm ≈ 35.31 cft）。
比较单价前先统一到 cbm，否则「B 仓最便宜」这种结论完全是错的。
