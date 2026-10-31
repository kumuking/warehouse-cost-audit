# 演示数据（全部脱敏）

这里的订单、账单、库存、费率**全部是虚构的**，只用于验证脚本能跑、输出长什么样。**不要把这些价格当作任何供应商的真实报价。**

不需要领星账号，直接运行：

```bash
python examples/run_demo.py
```

产出写到 `examples/output/`，该目录已在 `.gitignore` 中。

## 文件说明

| 文件 | 模拟什么 |
|---|---|
| `orders_sample.jsonl` | 4 张 WMS 出库单，覆盖 4 个分区（Zone 2/3/4/5）与 2 种 AHS 触发（超长、超重） |
| `bill_sample.csv` | 承运商账单：1 单多行（基础/燃油/AHS）合并、1 单多收 $5.50、1 单「账单有但核算无」 |
| `snapshot_sample.jsonl` | 月末库存快照，2 个 SKU |
| `flows_sample.jsonl` | 当月仓位流水：1 次入库 + 1 次出库（验证 FIFO 批次与在库天数） |
| `specs_sample.json` | SKU 规格（长宽高，单位 cm） |
| `zipdb_sample.json` | 收发地邮编 → 经纬度，用于算里程和分区 |
| `rates/carrier_UPS_DEMO.json` | 承运商价表：分区×重量段、AHS 阈值、体积重除数 |
| `rates/fuel_UPS_DEMO.json` | 燃油周表（按发货日取值） |
| `rates/handling_DEMOWH.json` | 操作费：首件续件 + 公斤档 + 超尺寸 + 最低收费 |
| `rates/storage_DEMOWH.json` | 仓储费：立方英尺/月 + 3 档库龄阶梯 + 7 天免仓期 |

## 演示预期结果

- **运费**：AMZ-1002 被多收 **$5.50**，归因为「疑似多收附加费（AHS/Oversize）」——该单实际未触发 AHS
- **对账**：AMZ-9999 属于「账单有、核算无」，提示索取计费依据
- **操作费**：AMZ-1003 因最长边 130cm 触发超尺寸附加 $3.50
- **仓储费**：TS102C05 月初存量按 30 天前入仓计，逐日累加到 188.65

换真实数据时，把 `assets/rates/` 里的模板填成真价，其余脚本不用改。
