# -*- coding: utf-8 -*-
"""领星开放平台鉴权与取数（仓配费用核算专用）

用法：
    from lingxing import Lingxing
    lx = Lingxing.from_cred("~/path/领星api.txt")
    rows = lx.pull_wms_orders("2026-10-01", "2026-10-31")

凭证文件格式（txt，两行即可，正则容错）：
    APP ID：xxxxxxxx
    AppSecret：xxxxxxxx
"""
import base64
import hashlib
import json
import re
import time
from pathlib import Path

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

BASE = "https://openapi.lingxing.com"
RATE_LIMIT_SLEEP = 1.1  # 令牌桶容量 1，稳妥按 1.1s 间隔


class Lingxing:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.s = requests.Session()
        self.s.trust_env = False  # 绕过系统代理，否则会 timeout
        self.token = self._get_token()

    # ---------- 鉴权 ----------
    @classmethod
    def from_cred(cls, path: str):
        text = Path(path).expanduser().read_text(encoding="utf-8")
        app_id = re.search(r"APP\s*ID[：:]\s*(\S+)", text).group(1)
        secret = re.search(r"AppSecret[：:]\s*(\S+)", text).group(1)
        return cls(app_id, secret)

    def _get_token(self) -> str:
        r = self.s.post(
            f"{BASE}/api/auth-server/oauth/access-token",
            data={"appId": self.app_id, "appSecret": self.app_secret},
            timeout=30,
        )
        j = r.json()
        if str(j.get("code")) != "200":
            raise RuntimeError(f"获取 token 失败: {j}")
        return j["data"]["access_token"]

    def _sign(self, params: dict) -> str:
        """数组/字典参数必须以紧凑 JSON 参与签名，否则签名校验失败。"""
        def fmt(v):
            if isinstance(v, (list, dict)):
                return json.dumps(v, separators=(",", ":"), ensure_ascii=False)
            return v
        items = sorted((k, fmt(v)) for k, v in params.items() if v not in (None, ""))
        qs = "&".join(f"{k}={v}" for k, v in items)
        md5_hex = hashlib.md5(qs.encode("utf-8")).hexdigest().upper()
        cipher = AES.new(self.app_id.encode("utf-8"), AES.MODE_ECB)
        return base64.b64encode(cipher.encrypt(pad(md5_hex.encode(), 16))).decode()

    def call(self, path: str, body: dict | None = None, method: str = "POST") -> dict:
        params = {
            "access_token": self.token,
            "app_key": self.app_id,
            "timestamp": str(int(time.time())),
        }
        params["sign"] = self._sign({**params, **(body or {})})
        url = f"{BASE}{path}"
        if method == "POST":
            r = self.s.post(url, params=params, json=body or {}, timeout=90)
        else:
            r = self.s.get(url, params=params, timeout=90)
        time.sleep(RATE_LIMIT_SLEEP)
        j = r.json()
        if str(j.get("code")) not in ("0", "200"):
            raise RuntimeError(f"{path} 返回异常: {j.get('code')} {j.get('message') or j.get('msg')}")
        return j

    def paginate(self, path: str, body: dict, page_size: int = 200,
                 max_pages: int = 4000, mode: str = "dual",
                 progress_every: int = 50):
        """通用翻页，返回拼接后的 list。

        ⚠️ 领星各接口的翻页参数并不统一（实测 2026-11）：
           - WMS 出库单  : 只认 page + page_size（offset 被忽略，单页上限 200）
           - 备货单      : 认 page，page_size 被钳到 50
           - 仓位流水    : 只认 offset + length（单页上限 200）
           - 库存明细    : offset + length，length 可到 800
        所以 mode 必须按接口指定，传错会静默丢数据。

        mode: "page" | "offset" | "dual"（同时传，让服务端各取所需）
        """
        out, sigs, page1_len = [], set(), None
        for page in range(1, max_pages + 1):
            b = dict(body)
            if mode == "offset":
                b.update({"offset": len(out), "length": page_size})
            elif mode == "page":
                b.update({"page": page, "page_size": page_size})
            else:
                b.update({"page": page, "page_size": page_size,
                          "offset": len(out), "length": page_size})

            j = self.call(path, b)
            data = j.get("data")
            rows = data if isinstance(data, list) else (data or {}).get("list") or []
            if not rows:
                break

            # 防死循环：服务端若忽略翻页参数会反复返回同一页
            sig = hashlib.md5(
                json.dumps(rows[0], sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if sig in sigs:
                print(f"  ⚠️ {path.split('/')[-1]} 第 {page} 页与之前重复，提前停止"
                      f"（翻页参数可能不被支持，已拉 {len(out)} 条）")
                break
            sigs.add(sig)

            out.extend(rows)
            if page1_len is None:
                page1_len = len(rows)

            total = j.get("total")
            if isinstance(total, int) and total and len(out) >= total:
                break
            if len(rows) < page1_len:
                break
            if progress_every and page % progress_every == 0:
                print(f"  … {path.split('/')[-1]} 第 {page} 页，累计 {len(out)} 条",
                      flush=True)
        return out

    # ---------- 业务取数 ----------
    def warehouses(self, wtype: int = 3):
        """仓库清单。type: 1 本地仓 / 2 FBA / 3 海外仓 / 4 AWD。

        注意：type 必须是整型，传字符串 "3" 会返回 102 参数不合法。
        """
        j = self.call("/erp/sc/data/local_inventory/warehouse", {"type": int(wtype)})
        d = j.get("data")
        return d if isinstance(d, list) else (d or {}).get("list") or []

    def wms_orders(self, start: str, end: str, wids: str | None = None):
        """WMS 出库单（带 consignee / 尺寸 / 重量 / 运费）。日期格式 YYYY-MM-DD。

        注意：参数是 start_date / end_date，不是 start_time / end_time。
        """
        body = {"start_date": start, "end_date": end}
        if wids:
            body["wids"] = wids
        # 该接口只认 page/page_size，offset 会被忽略
        return self.paginate("/erp/sc/routing/wms/order/wmsOrderList", body,
                             page_size=200, mode="page")

    def inbound_orders(self, start: str, end: str):
        """海外仓备货单/入库单（含 receive_num 实收件数与收货时间）。"""
        body = {"start_date": start, "end_date": end}
        return self.paginate("/erp/sc/routing/owms/inbound/listInbound", body,
                             page_size=200, mode="page")

    def bin_statement(self, wid: str, start: str, end: str, types: str | None = None):
        """仓位流水（重建每日库存的基准数据）。

        type 常用：22 采购入库 / 24 调拨入库 / 26 退货入库 / 33 盘亏出库
                  37 FBA出库 / 38 FBM出库 / 41 调拨出库 / 100 库存调整
        """
        body = {"wids": str(wid), "start_date": start, "end_date": end}
        if types:
            body["type"] = types
        # 该接口只认 offset/length
        return self.paginate("/erp/sc/routing/data/local_inventory/wareHouseBinStatement",
                             body, page_size=200, mode="offset")

    def inventory_snapshot(self, wid: str | None = None):
        """当前库存快照（含可用/次品/锁定 + third_inventory 调拨在途）。"""
        body = {}
        if wid:
            body["wid"] = str(wid)
        return self.paginate("/erp/sc/routing/data/local_inventory/inventoryDetails",
                             body, page_size=800, mode="offset")
