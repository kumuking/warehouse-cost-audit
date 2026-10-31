# 领星开放平台接口清单（费用核算相关）

## 鉴权

1. `POST /api/auth-server/oauth/access-token`（form: appId, appSecret）→ `access_token`
2. 业务接口带 query：`access_token / app_key / timestamp / sign`
3. `sign` = 全部参数 ASCII 排序拼接 `k=v&...`（空值跳过）→ MD5 大写 → AES/ECB/PKCS5 加密（密钥=appId）→ base64

**两个必踩的坑：**
- 数组/字典参数必须以**紧凑 JSON**（`separators=(',',':')`）参与签名，否则报签名错误
- 请求必须绕过系统代理（`session.trust_env = False`），否则国内域名 timeout

**限流**：令牌桶容量 1，脚本按 1.1s 间隔串行调用。

**IP 白名单**：家庭宽带重拨后出口 IP 变化会导致 TCP 443 直接丢包（ping 通但连不上）。这是最常见的"接口坏了"假象，先查 IP。

## 接口与用途

| 用途 | Path | 关键参数 | 备注 |
|---|---|---|---|
| 仓库清单 | `/erp/sc/data/local_inventory/warehouse` | `type`: 1 本地仓 / 2 FBA / 3 海外仓 / 4 AWD | 取 `wid` 与仓名 |
| 出库单 | `/erp/sc/routing/wms/order/wmsOrderList` | `start_date` / `end_date` / `wids` | 参数是 `_date` 不是 `_time` |
| 备货单（入库） | `/erp/sc/routing/owms/inbound/listInbound` | `start_date` / `end_date` | 含 `receive_num` 实收数 |
| 仓位流水 | `/erp/sc/routing/data/local_inventory/wareHouseBinStatement` | `wids` / `start_date` / `end_date` / `type` | 重建日结存的唯一数据源 |
| 库存快照 | `/erp/sc/routing/data/local_inventory/inventoryDetails` | `wid` / `offset` / `length`（≤800） | 含 `third_inventory` 调拨在途 |
| 产品规格 | `batchGetProductInfo`（100个/次） | sku 列表 | 尺寸重量来源 |

## 出库单关键字段（费用核算用）

| 字段 | 含义 | 用途 |
|---|---|---|
| `warehouse_name` / `wid` | 实体仓 | 匹配费率卡 |
| `stock_delivered_at` | 实际出库时间 | 归属月份、燃油周次 |
| `status_name` | 已出库 / 待出库 / 已截单 | **只对"已出库"计操作费** |
| `pkg_length/width/height` | 包裹尺寸（cm） | 计费重、AHS 判定 |
| `pkg_real_weight` | 实重（g） | 计费重 |
| `pkg_fee_weight` | 计费重（g） | **约 91% 为 0，不可用** |
| `pkg_volume` | 体积 | 仓储费校验 |
| `product_info[].sku` / `.count` | SKU 与件数 | 首件/续件、SKU 级分摊 |
| `actual_carrier` / `logistics_type_name` | 承运商 / 服务渠道 | 匹配承运商费率卡 |
| `logistics_freight` | 系统运费（实收口径） | **对账基准，约 15% 为空** |
| `logistics_estimated_freight` | 预估运费 | 仅参考，不作对账基准 |
| `consignee_postcode` | 收货邮编 | 分区计算 |
| `delivery_status` | 配送状态 | 判断是否妥投/退回 |

## 备货单关键字段

| 字段 | 含义 |
|---|---|
| `inbound_order_no` | 入库单号 |
| `r_wname` / `r_wid` | 目的仓 |
| `products[].stock_num` | 计划/发货数量 |
| `products[].receive_num` | **实收数量**（入库操作费计费依据） |
| `receive_good_handle_time` | 收货时间（费用归属月） |
| `real_delivery_time` | 实际到仓时间 |

## 仓位流水字段

`wid / sku / num`（正入负出）`/ type / type_text / opt_time / whb_name`

常用 type：`22` 采购入库、`24` 调拨入库、`26` 退货入库、`33` 盘亏出库、`37` FBA出库、`38` FBM出库、`41` 调拨出库、`100` 库存调整。

## 已知数据陷阱

1. **PII 脱敏**：出库单的收件人地址约 30 天后被脱敏，邮编也会逐步消失。历史数据必须 30 天内拉取，过期不可回溯。
2. **币种混杂**：同一批出库单里可能有 USD / CAD / CNY / GBP，且 1,260 单币种为空，不能直接求和。
3. **虚拟仓**：领星里有"虚拟仓库"等镜像仓，数值是残差（0-1 件），做仓级分析时应剔除，避免与实体仓重复计数。
4. **WFS/日本仓**：平台仓（如 WFS）与日本仓是特殊渠道，若不做该渠道分析需显式排除。
