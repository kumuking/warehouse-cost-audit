# 仓配费用核算与对账（warehouse-cost-audit）

把 3PL 和快递商的月度账单，用**系统真实数据和合同价**重算一遍，告诉你：**多收了多少钱、多收在哪一项、哪几单可以拿去申诉。**

面向跨境电商自营海外仓（美国 / 加拿大 / 英国）。数据源为领星 ERP 开放平台。

---

## 这套东西解决什么

3PL 和承运商的账单错误是常态：AHS 附加费误判、燃油用错周、退件费虚收、"账单里有但货没发"的幽灵单。看总额很难发现，逐单比对才能抓出来。

程序做三件事：

| 模块 | 算什么 | 输入 | 输出 |
|---|---|---|---|
| **操作费** | 出入库操作费（按单/件/公斤/首件续件/超尺寸/最低收费） | 出库单 + 备货单 + 操作费价表 | 分仓分单明细、单均成本 |
| **仓储费** | 日结存 × 库龄阶梯 × 单价（FIFO 回推批次） | 库存快照 + 仓位流水 + SKU 规格 + 仓储价表 | 分仓分 SKU 仓储费、库龄分段 |
| **运费对账** | 计费重 → 分区 → 基础运费 → AHS → 燃油，与账单逐单比差 | 出库单 + 承运商价表 + 燃油周表 + 账单 | 差异明细、多收合计、申诉清单 |

**核心设计：费率卡与代码分离。** 调价了只改 `assets/rates/*.json`，不动一行代码；新增一个仓就加一个 JSON。

---

## 5 分钟先跑通（不需要领星账号）

```bash
git clone https://github.com/kumuking/warehouse-cost-audit.git
cd warehouse-cost-audit
pip install -r requirements.txt

python examples/run_demo.py
```

会依次跑完自检 → 运费核算 → 操作费 → 仓储费 → 账单对账，用的是 `examples/` 下的脱敏假数据（**所有价格都是虚构的**）。跑通说明环境没问题，可以接真实数据了。

---

## 安装（让 AI 助手直接调用）

这个目录本身就是一个 WorkBuddy / CodeBuddy Skill。装到 skills 目录即可被自动识别。

### 方式一：git clone（推荐，可 `git pull` 升级）

**Windows（PowerShell）**
```powershell
git clone https://github.com/kumuking/warehouse-cost-audit.git "$env:USERPROFILE\.workbuddy\skills\warehouse-cost-audit"
```

**macOS / Linux**
```bash
git clone https://github.com/kumuking/warehouse-cost-audit.git ~/.workbuddy/skills/warehouse-cost-audit
```

### 方式二：下载 ZIP + 安装脚本

下载解压后，在解压目录里执行：

```powershell
# Windows
.\install.ps1
```
```bash
# macOS / Linux
./install.sh
```

安装脚本只复制代码、模板和文档，**不会覆盖你已有的 `assets/rates/` 费率卡**。

### 依赖

- Python 3.10+
- `requests`、`pycryptodome`（只有调领星 API 时需要）

---

## 首次配置（三件事）

### 1. 领星凭证

新建 `领星api.txt`（**不要放进仓库**）：

```
APP ID：你的appId
AppSecret：你的appSecret
```

### 2. IP 白名单

领星开放平台按出口 IP 放行。家庭宽带重拨后 IP 会变，表现为 **ping 通但 HTTPS 连不上**。

```bash
python scripts/healthcheck.py --cred ~/领星api.txt
```

连不通时先查当前公网 IP，再把它加进领星后台白名单。长期方案是走固定出口的云服务器（如腾讯云轻量）做代理。

### 3. 费率卡

复制模板，填成真实价格，放进 `assets/rates/`：

| 类型 | 模板 | 给谁填 |
|---|---|---|
| 操作费 | `assets/rates/_template_handling.json` | 每个 3PL 一张 |
| 仓储费 | `assets/rates/_template_storage.json` | 每个 3PL 一张 |
| 承运商运费 | `assets/rates/_template_carrier.json` | 每家承运商 × 每个服务各一张 |
| 燃油周表 | `assets/rates/_template_fuel.json` | 每家承运商一张 |

字段规范见 `references/rate_card_schema.md`。程序按文件名里的类型词扫描（`carrier_*.json`、`3pl-handling_*.json` 都能识别）；`_` 开头的是模板，不参与匹配。

匹配规则是**唯一命中**：同一供应商 + 同一日期命中 0 张或多张都会直接报错，不会猜用哪个价。

#### 不想手抄价表？用映射层（推荐）

各家 3PL 的价表格式和收费项叫法都不一样，手抄又慢又容易抄错。走映射层，原始 Excel 直接转成费率卡：

```bash
# 1. 先看结构，不抽价
python scripts/rate_import.py inspect 供应商报价.xlsx --out 指纹.md

# 2. 照指纹写一份 mapping（复制 assets/rates/mapping/_template_mapping.json）

# 3. 抽价建卡，一张表自动拆成 handling / storage / carrier 多张
python scripts/rate_import.py build 供应商报价.xlsx \
    --mapping assets/rates/mapping/mapping_示例仓LA.json --out assets/rates
```

`build` 会吐出四份诊断，**必须逐条看完再核账**：

| 文件 | 说明 |
|---|---|
| `_unmapped.csv` | 表里有数值但没被规则接住 |
| `_conflicts.csv` | 同一字段抽到多个不同数值 |
| `_ambiguous.csv` | 一个标签同时咬中多个字段 |
| `_unit_mismatch.csv` | 名称对但计量基准错（如把「0.35 USD/件」当成「0.35 USD/单」） |

最后一项最要命——数字都对，量纲错一位，整月操作费会被低估到零头。卡里 `_meta.needs_review` 非空即视为未定稿，不得用于对账。完整口径清单见 `references/vendor_mapping_guide.md`。

---

## 每月标准流程

```bash
# 0. 自检（API 通不通、费率卡齐不齐、数据完整度）
python scripts/healthcheck.py --cred ~/领星api.txt

# 1. 拉数（一次拉齐出库单/备货单/库存快照/仓位流水）
python scripts/pull_month.py --cred ~/领星api.txt \
    --start 2026-10-01 --end 2026-10-31 --outdir data/2026-10

# 2. 三类费用
python scripts/calc_handling.py --orders data/2026-10/wms_出库单_*.jsonl \
    --inbound data/2026-10/备货单_*.json --out output/操作费_2026-10.csv

python scripts/calc_storage.py --snapshot data/2026-10/snapshot_*.jsonl \
    --flows data/2026-10/flows_*.jsonl --specs data/SKU规格.json \
    --month 2026-10 --out output/仓储费_2026-10.csv

python scripts/calc_freight.py --orders data/2026-10/wms_出库单_*.jsonl \
    --zipdb assets/geonames_US.json --out output/运费核算_2026-10.csv

# 3. 与账单对账（列名各家不同，用参数映射）
python scripts/reconcile.py --calc output/运费核算_2026-10.csv \
    --bill data/2026-10/FedEx_invoice.csv \
    --bill-key "Tracking Number" --bill-amount "Net Charge" \
    --bill-type "Charge Type" --out output/差异_2026-10.csv
```

拉数耗时参考：3 万单约 150 页 × 1.1 秒 ≈ **3 分钟**（领星限流令牌桶为 1，脚本已内置间隔）。

---

## 输出物

| 文件 | 内容 |
|---|---|
| `操作费_YYYY-MM.csv` | 逐单/逐入库单的操作费构成（单费/件费/公斤费/超尺寸） |
| `仓储费_YYYY-MM.csv` | 分仓分 SKU 仓储费（按日结存 × 库龄档位累加） |
| `运费核算_YYYY-MM.csv` | 逐单计费重、分区、基础运费、AHS、燃油、应收 vs 实收、差额 |
| `差异_YYYY-MM.csv` | 与账单的差异清单，含归因和申诉建议 |

`reconcile.py` 的容差默认「绝对 $1 或相对 2%」，超过才列为差异，避免几分钱噪音。

---

## 口径与已知限制

跑之前请务必读 `references/assumptions.md`，其中标 `[待确认]` 的项必须先跟业务对齐。最容易出错的三个：

1. **体积重除数**：5000（公制）/ 139（in³/lb）/ 6000 三种口径并存，选错会让运费差 15% 以上。
2. **仓储计费单位**：立方英尺/月、每件/月、每托/月——决定整个算法，必须先问清。
3. **燃油基准**：按发货日所在周，还是账单周期。

其他已知缺口：

- 领星只有运费**总额**（`logistics_freight`），没有基础/燃油/AHS 拆分。**只给价表只能核总额，必须给账单才能逐项核**。
- 计费重 `pkg_fee_weight` 约 91% 为 0，程序用「实重 vs 尺寸体积重取大」自算。
- 出库单收件地址约 **30 天后被领星脱敏**，历史月份请尽早拉。
- 仓位流水自启用 ERP 起才有记录，更早的仓储费只能靠期初盘点倒推。
- 分区用里程近似（Haversine × 1.15 路网系数），与 FedEx/UPS 官方 zone chart 约 95% 一致；差异集中在州界附近，可用官方分区表校正。

---

## 常见报错

| 现象 | 原因 | 处理 |
|---|---|---|
| `Timeout` / 连不上，但 ping 通 | IP 白名单失效 | 把当前公网 IP 加进领星后台 |
| `签名错误`（code 100/101） | 数组参数没按紧凑 JSON 参与签名 | 用 `lingxing.py` 里的 `_sign`，别自己拼 |
| `102 参数不合法` | `type` 传了字符串 | 仓库接口要求整型 |
| 出库单只拉到 20 单 | 翻页参数用错（该接口忽略 `offset`） | v1.0.1 已修，`git pull` |
| `ModuleNotFoundError: Crypto` | 缺依赖 | `pip install -r requirements.txt` |
| `PermissionError` | 输出文件正被 Excel 打开 | 关掉再跑 |

---

## 安全

- **凭证、真实费率卡、账单、订单明细一律不进仓库。** `.gitignore` 已覆盖 `assets/rates/*.json`（模板除外）、`*.csv`、`*.jsonl`、`*.xlsx`。
- 提交前建议自查：`git status` 里不应出现任何价格或客户数据文件。
- 含公司议价信息的费率卡请通过共享盘 / 私有渠道分发，不要提交到任何 Git 仓库。

---

## 目录结构

```
warehouse-cost-audit/
├── SKILL.md                    # Skill 定义（触发条件 + 工作流 + 硬性规则）
├── scripts/
│   ├── lingxing.py             # 鉴权与取数（含分页、签名、代理三个坑的处理）
│   ├── pull_month.py           # 按月拉取全部原始数据
│   ├── common.py               # 费率卡加载、计费重、AHS 判定、分区查价
│   ├── rate_import.py          # 供应商原始价表 -> 统一口径费率卡（inspect / build）
│   ├── calc_handling.py        # 操作费
│   ├── calc_storage.py         # 仓储费
│   ├── calc_freight.py         # 运费核算
│   ├── reconcile.py            # 与账单对账
│   └── healthcheck.py          # 跑数前自检
├── references/
│   ├── vendor_mapping_guide.md # 价表映射：统一名称字典 + 口径陷阱清单
│   ├── rate_card_schema.md     # 四类费率卡字段规范
│   ├── assumptions.md          # 口径约定与待确认项
│   └── lingxing_api.md         # 接口清单、字段映射、已知坑
├── assets/rates/               # 费率卡（真实卡不入库，只留 _template_*）
│   └── mapping/                # 供应商价表映射（真实映射不入库，只留模板）
└── examples/                   # 脱敏演示数据 + run_demo.py
```

---

## 关于 token 成本

算术全部由 Python 完成，模型只读汇总、判异常、写结论。所以**月结的 token 成本与单量基本无关**：3 万单一个月约 140 万输入 token，低谷时段约 $0.05。真正需要模型介入的是「异常单复核」，通常几百单级别。

由此有两条硬性规则（写在 `SKILL.md` 里）：脚本只输出聚合结果、超过 200 行必须落盘；模型不做逐单数值计算。

---

## 版本

见 `CHANGELOG.md`。建议每月跑之前先 `git pull`。

## License

内部使用授权，详见 `LICENSE`。
