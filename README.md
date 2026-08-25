# COS 机会大师 · Phase M1

只读 CLI：列出腾讯云 COS 存储桶，拉取账户级 COS 账单与按桶应付排行，写入本地缓存，打印与线框 **C5 桶排行** 对齐的表格。

本仓库是 **M1**。不做 Web 看板、PDF/Excel 导出、节省规则引擎（M2/M3）。相关接口已留在 `cos_cost.ext`。

## M1 做什么 / 不做什么

| 做 | 不做 |
| --- | --- |
| GET Service（List Buckets） | List Objects / GetBucket（列对象） |
| HeadBucket（仅地域缺失时） | 任何 Put / Delete / 生命周期写入 |
| `DescribeBillSummaryByProduct`（COS 应付、Ready） | 翻页 `DescribeBillDetail` |
| `DescribeBillResourceSummary`（按桶应付，`BusinessCode=p_cos`） | 修改账单或资源 |
| `GetMonitorData`（容量 / 标准% / 外网，可选） | 监控写、告警改配 |
| 文件系统 JSON 缓存 | 把 SecretKey 写入缓存或日志 |
| `--mock` fixture，无 AK/SK | 规则引擎、配置灯真值、导出 |

账单未出账（`Ready=0`）时仍返回排行，并标记 **暂估**。  
缺监控权限：排行照出，容量 / 标准% / 外网为 `—`。  
缺账单权限：仍列桶，应付为 `—`。进程不崩溃。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

需要 Python 3.11+。

## Mock（无密钥、无网络）

```bash
python -m cos_cost rank --mock
python -m cos_cost rank --mock --month 2026-07 --json
python -m cos_cost collect --mock --month 2026-07
```

默认账期是 **UTC+8 上一自然月**。fixture 覆盖 `2026-07`（已出账）、`2026-06`（环比）、`2025-07`（同比）、`2026-08`（Ready=0 暂估）。

### 示例输出（`python -m cos_cost rank --mock --month 2026-07`）

```
COS 机会大师 · 桶排行    账期 2026-07    账单已出账 (Ready=1)    [mock]

KPI
  COS 应付          ¥ 186,420    环比 +8.3%    同比 +21%
  可优化金额        —（M3 规则引擎未接入）
  标准存储占比      68%
  外网下行          12.4 TB
  请求费            —（M1 未拆分计费项）
  数据就绪          账单已出账 (Ready=1) · 桶 5/5

桶排行
桶                       地域          应付       环比   容量    标准%  外网    机会  配置灯
img-cdn-1250000000       ap-shanghai   ¥ 78,400  +6%    18 TB   70%   8.1 TB  —  生命周期○ 碎片○ CDN○ 版本○ 备份○
logs-prod-1250000000     ap-guangzhou  ¥ 62,100  +18%   240 TB  91%   0.2 TB  —  生命周期○ 碎片○ CDN○ 版本○ 备份○
backup-1250000000        ap-chengdu    ¥ 32,100  -2%    90 TB   40%   0       —  生命周期○ 碎片○ CDN○ 版本○ 备份○
archive-cold-1250000000  ap-beijing    ¥ 10,820  +10%   50 TB   5%    0       —  生命周期○ 碎片○ CDN○ 版本○ 备份○
tmp-scratch-1250000000   ap-guangzhou  ¥ 3,000   -1.8%  2 TB    100%  4.1 TB  —  生命周期○ 碎片○ CDN○ 版本○ 备份○
```

列与账户线框 **C5 桶排行** 一致：桶、地域、应付、环比、容量、标准%、外网、机会、配置灯。应付按 `RealTotalCost` 降序。机会与配置灯为 M2/M3 占位。

## 线上模式（子用户只读密钥）

1. 在 CAM 创建子用户，**不要**授予 `GetBucket` / 对象读写。  
2. 可挂系统策略 `QcloudFinanceBillReadOnlyAccess`、`QcloudMonitorReadOnlyAccess`，再叠加 [docs/cam-m1.json](docs/cam-m1.json)（`GetService` + `HeadBucket` + `finance:DescribeBill*` / `DescribeResourceBill*` / `DescribeDosage*` + `monitor:GetMonitorData`）。  
3. 复制 `.env.example` 为 `.env`（已被 gitignore）：

```bash
COS_SECRET_ID=AKIDxxxxxxxx
COS_SECRET_KEY=xxxxxxxx
# COS_TOKEN=          # 临时密钥才需要
# COS_APPID=1250000000
# COS_CACHE_DIR=./cache
```

4. 采集并排行：

```bash
python -m cos_cost collect --month 2026-07
python -m cos_cost rank --month 2026-07
python -m cos_cost rank --month 2026-07 --json
```

第二次运行走缓存，不会再打账单 / 监控 / 列桶接口（账单 `Ready=1` 后该月不可变；桶列表 1 小时；监控 30 分钟）。`--force` 强制回源。

**切勿**把 SecretKey 打到日志、工单或缓存目录。本工具在写缓存前会拒绝含 `secret_key` 字段或 SecretKey 原文的内容。

## 创建 CAM 子用户（最小只读）

1. 访问管理 → 用户 → 新建用户 → 自定义创建。  
2. 编程访问，拿到 `SecretId` / `SecretKey`。  
3. 授权：  
   - 预设：`QcloudFinanceBillReadOnlyAccess` + `QcloudMonitorReadOnlyAccess`  
   - 自定义：粘贴 `docs/cam-m1.json`（把 `${appid}` 换成主账号 APPID，或 `HeadBucket` 的 resource 先用 `*`）  
4. **不要**勾选 `QcloudCOSFullAccess`，**不要**授予 `name/cos:GetBucket`（列对象）。

账单接口走 `billing.tencentcloudapi.com`，版本 `2018-07-09`，与地域无关。  
监控 `GetMonitorData` 的 Region **必须** `ap-guangzhou`，Namespace `QCE/COS`，维度 `bucket` = 带 APPID 后缀的完整桶名。存储指标取 **last**，流量取 **sum**，`Period=86400`。

## 缓存

目录默认 `./cache/<account>/<data_kind>[_YYYY-MM].json`，键为 `(appid 或 uin, month, data_kind)`。

| data_kind | TTL |
| --- | --- |
| `buckets` | 1 小时 |
| `bill_summary` / `bill_resources` | `Ready=1` 后该月不可变；`Ready=0` 按 1 小时刷新 |
| `monitor` | 30 分钟 |

应付金额使用官方字段 **`RealTotalCost`**（优惠后总价）。产品过滤 **`BusinessCode=p_cos`**。无法识别的资源会把原始 `ResourceId` / `ResourceName` 留在 JSON 的 `raw_resource_*` 里。

`DescribeBillResourceSummary` 按 5 次/秒节流，`Limit=1000` 翻页。M1 **不**调用 `DescribeBillDetail`。

## 测试

```bash
pytest
```

全部用例在 mock / MagicMock 下运行，不需要真实 AK/SK，不访问公网。

## 给 M2 / M3 的接口

- `cos_cost.ext.opportunity.OpportunityEngine` — 规则与可优化金额  
- `cos_cost.ext.config_lights.ConfigLightProvider` — 生命周期 / 碎片 / CDN / 版本 / 备份  
- `cos_cost.ext.export.RankingExporter` — PDF / Excel  

M1 实现为占位：机会列为 `—`，配置灯为 `○`。
