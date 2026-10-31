# 费率卡字段规范

费率卡与代码完全分离：调价只改 JSON，不动脚本。所有金额保留两位小数，币种在卡内声明，**禁止跨币种相加**。

存放位置：`assets/rates/`
命名规范：`{类型}_{供应商}_{生效年月}.json`
- `3pl-handling_科奈思LA_202607.json`
- `3pl-storage_ACRO多伦多_202607.json`
- `carrier_FedEx_202601.json`
- `fuel_FedEx_2026W40.json`

匹配规则：按 `warehouse_names` / `carrier` + 订单日期落在 `[effective_from, effective_to]` 区间选卡。同一供应商允许存在多张卡（不同生效期），加载时按日期取唯一命中项；命中 0 张或多张即报错，不允许猜测。

---

## 一、操作费卡（3PL Handling）

```json
{
  "vendor": "科奈思LA",
  "warehouse_names": ["科奈思LA"],
  "currency": "USD",
  "effective_from": "2026-07-01",
  "effective_to": "2026-12-31",
  "inbound": {
    "per_order": 0.0,
    "per_piece": 0.0,
    "per_carton": 0.0,
    "per_kg": 0.0,
    "per_cbm": 0.0,
    "min_charge": 0.0,
    "max_charge": null,
    "tiers": [
      {"max_kg": 5.0, "per_piece": 0.0},
      {"max_kg": 15.0, "per_piece": 0.0}
    ],
    "value_added": [
      {"name": "贴标", "unit": "per_piece", "price": 0.0}
    ]
  },
  "outbound": {
    "per_order": 0.0,
    "per_piece_first": 0.0,
    "per_piece_extra": 0.0,
    "per_kg": 0.0,
    "min_charge": 0.0,
    "max_charge": null,
    "tiers": [
      {"max_kg": 5.0, "per_order": 0.0, "per_piece_first": 0.0, "per_piece_extra": 0.0},
      {"max_kg": 15.0, "per_order": 0.0, "per_piece_first": 0.0, "per_piece_extra": 0.0}
    ],
    "oversize": [
      {"max_long_cm": 120.0, "surcharge": 0.0},
      {"max_long_cm": 175.0, "surcharge": 0.0}
    ],
    "value_added": []
  }
}
```

字段语义：
- `per_order` 按出库单收；`per_piece_first/extra` 首件/续件；`per_kg` 按计费重；`per_cbm` 按体积。
- `tiers` 按 **计费重档** 覆盖同名字段，命中第一个 `chargeable_kg <= max_kg` 的档；未命中任何档则用顶层字段。
- `min_charge` 为该单据（单张出库单）最低收费；`max_charge` 为封顶，`null` 表示不封顶。
- `oversize` 按最长边命中第一个档，叠加收取。

---

## 二、仓储费卡（3PL Storage）

```json
{
  "vendor": "ACRO多伦多",
  "warehouse_names": ["ACRO多伦多"],
  "currency": "CAD",
  "effective_from": "2026-07-01",
  "effective_to": "2026-12-31",
  "billing_unit": "cbm",
  "volume_basis": "product",
  "free_days": 0,
  "min_charge_monthly": 0.0,
  "price_per_unit_month": 0.0,
  "age_tiers": [
    {"min_days": 0,   "max_days": 30,  "price_per_unit_month": 0.0},
    {"min_days": 31,  "max_days": 90,  "price_per_unit_month": 0.0},
    {"min_days": 91,  "max_days": 180, "price_per_unit_month": 0.0},
    {"min_days": 181, "max_days": null, "price_per_unit_month": 0.0}
  ],
  "peak_surcharge": {"months": [11, 12], "multiplier": 1.0},
  "min_billable_qty": 0
}
```

字段语义：
- `billing_unit`：`cbm` 立方米 / `cft` 立方英尺 / `piece` 每件 / `pallet` 每托。**这一项决定整套算法的量纲，必须先确认。**
- `volume_basis`：`product` 用产品包装尺寸算体积 / `carton` 用外箱尺寸。
- `free_days`：到仓后免仓天数，从入库流水日期起算。
- `age_tiers`：按 **库龄天数** 取档；库龄 = 核算日 − 该批次到仓日。命中 `min_days <= age <= max_days`。
- 月度金额 = Σ(每日结存量 × 当日单价 ÷ 当月自然日数)，逐日累加后取整，避免单日四舍五入放大误差。
- `peak_surcharge` 在指定月份对最终金额乘系数。

---

## 三、承运商费率卡（Carrier）

```json
{
  "carrier": "UPS",
  "service": "Ground Residential",
  "currency": "USD",
  "effective_from": "2026-01-01",
  "effective_to": "2026-12-31",
  "origin_zip": "08817",
  "dim_divisor": 139,
  "dim_unit": "in",
  "round_up": {"weight_lb": 1.0, "dim_in": 1.0},
  "zones": {
    "2": [[1, 9.10], [2, 9.85], [5, 11.20], [10, 14.30]],
    "3": [[1, 9.60], [2, 10.40], [5, 12.10], [10, 15.80]]
  },
  "residential_surcharge": 0.0,
  "delivery_area_surcharge": 0.0,
  "remote_surcharge": 0.0,
  "fuel_applicable": true,
  "ahs": {
    "triggers": {
      "max_long_in": 48.0,
      "second_long_in": 30.0,
      "actual_weight_lb": 50.0,
      "non_corrugated": true
    },
    "price": 0.0,
    "applies_per_package": true
  },
  "oversize": {
    "triggers": {"longest_in": 96.0, "girth_in": 130.0, "weight_lb": 150.0},
    "price": 0.0
  },
  "dimensional_weight_min": false
}
```

字段语义：
- `zones` 为 `zone -> [[重量上限lb, 价格], ...]` 升序数组，命中第一个 `chargeable_lb <= 上限`。
- 分区由 `origin_zip` 与收货邮编计算（见 `scripts/zone_table.py`）。
- `dim_divisor`：英制用 139（in³/lb），公制用 5000 或 6000（cm³/kg）。`dim_unit` 声明尺寸量纲。
- `round_up`：UPS/FedEx 均按整磅进位、尺寸按整英寸进位；不填则按实际值。
- `fuel_applicable` 为 true 时，燃油 = (基础运费 + AHS + 其他附加) × 当期燃油%。

---

## 四、燃油表（Fuel）

```json
{
  "carrier": "FedEx",
  "service_group": "Ground",
  "currency": "USD",
  "weekly": [
    {"week_start": "2026-10-05", "pct": 0.1525},
    {"week_start": "2026-10-12", "pct": 0.1550}
  ]
}
```

- 燃油按 **发货日** 落在哪一周取值（若账单按发票周期，则切到 `billing_cycle` 模式，见 `references/assumptions.md`）。
- 不同 `service_group`（Ground / Express / Home Delivery）燃油率不同，必须分开取。
