# COS 机会大师 · M1 CLI + M2 看板

只读工具：列出腾讯云 COS 存储桶，拉取账户级 COS 账单与按桶应付，写入本地缓存，并提供 **账号全局 / 桶页** 看板（对齐线框 C1–C11）。

**M3 不做**：节省规则引擎真值、PDF/Excel 导出、把生命周期应用到桶。

## 做什么 / 不做什么

| 做 | 不做 |
| --- | --- |
| GET Service（List Buckets） | List Objects / GetBucket（列对象） |
| HeadBucket（仅地域缺失时） | 任何 Put / Delete / 生命周期写入 |
| `DescribeBillSummaryByProduct`（COS 应付、Ready） | 翻页 `DescribeBillDetail` |
| `DescribeBillResourceSummary`（按桶应付，`BusinessCode=p_cos`） | 修改账单或资源 |
| `GetMonitorData`（容量 / 流量 / 请求，可选） | 监控写、告警改配 |
| 文件系统 JSON 缓存 + FastAPI 看板 | 把 SecretKey 写入缓存、日志或前端 |
| `--mock` fixture，无 AK/SK | 规则引擎落地、导出、应用到桶 |

账单 `Ready=0` → 横幅 **暂估**，仍出排行。  
缺监控权限：容量 / 外网为 `—`。缺账单权限：仍列桶，应付为 **无权限 / —**。  
前缀热点固定空态：「清单未就绪，对象级建议不可用」。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

需要 Python 3.11+。

## Mock UI（无密钥、无网络）

```bash
python -m cos_cost serve --mock --port 18765
```

浏览器打开：

- 账号全局：<http://127.0.0.1:18765/>
- 桶页：<http://127.0.0.1:18765/b/logs-prod-1250000000>
- JSON：<http://127.0.0.1:18765/api/account?month=2026-07>

默认账期是 **UTC+8 上一自然月**（今天为 2026-08 时即 `2026-07`）。可用筛选 pill：时间 / 地域 / 桶名搜索。

C5 排行数字与 M1 mock 一致：`img-cdn-1250000000` ¥ 78,400、`logs-prod-1250000000` ¥ 62,100、`backup-1250000000` ¥ 32,100。点击树图或表格行进入桶页。抽屉只有 **复制草稿**，没有「应用到桶」。

### CLI 仍然可用

```bash
python -m cos_cost rank --mock
python -m cos_cost rank --mock --month 2026-07 --json
python -m cos_cost collect --mock --month 2026-07
```

## 线上模式（子用户只读 + 缓存）

1. 按下方 CAM 创建只读子用户，写入 `.env`（gitignore）：

```bash
COS_SECRET_ID=AKIDxxxxxxxx
COS_SECRET_KEY=xxxxxxxx
# COS_CACHE_DIR=./cache
```

2. 先采集（可反复跑，命中缓存）：

```bash
python -m cos_cost collect --month 2026-07
```

3. 看板读同一缓存目录，浏览器不接触腾讯云：

```bash
python -m cos_cost serve --cache-dir ./cache --port 18765
```

第二次 collect/serve 走缓存：账单 `Ready=1` 后该月不可变；桶列表 1 小时；监控 30 分钟。`--force` 强制回源。

**切勿**把 SecretKey 打到日志、工单、缓存或前端 HTML。

## 创建 CAM 子用户（最小只读）

1. 访问管理 → 用户 → 新建用户 → 编程访问。  
2. 预设：`QcloudFinanceBillReadOnlyAccess` + `QcloudMonitorReadOnlyAccess`。  
3. 自定义：粘贴 [docs/cam-m1.json](docs/cam-m1.json)（`GetService` + `HeadBucket` + `finance:DescribeBill*` / `DescribeResourceBill*` / `DescribeDosage*` + `monitor:GetMonitorData`）。  
4. **不要**授予 `name/cos:GetBucket`。

账单：`billing.tencentcloudapi.com`，版本 `2018-07-09`。  
监控：Region **必须** `ap-guangzhou`，Namespace `QCE/COS`，维度 `bucket` = 带 APPID 的全名。

## 路由

| 路径 | 说明 |
| --- | --- |
| `/` | 账号全局：KPI、C1 趋势、C2 树图、C3/C4 构成、C5 排行、机会三列 |
| `/b/{bucket}` | 桶页：面包屑、C7/C8、C6/C9/C10、配置体检、前缀空态、机会 + 抽屉 |
| `/api/account` | 账号 JSON（供筛选刷新 / 联调） |
| `/api/buckets/{bucket}` | 桶 JSON |

## 缓存

`./cache/<account>/<data_kind>[_YYYY-MM].json`，键 `(appid, month, data_kind)`。应付用官方字段 **`RealTotalCost`**，产品 **`p_cos`**。

C1 会尝试回看最多 6 个已缓存账期；只有一个月时仍画该月并提示去 collect 更多月份。C6 的「应付日均摊」= 月应付 ÷ 天数，**不会**编造按日账单。

## 测试

```bash
pytest
```

全部 mock / MagicMock，不需要真实 AK/SK。

## 给 M3 的接口

- `cos_cost.ext.opportunity.OpportunityEngine` / `PlaceholderOpportunityEngine`  
- `cos_cost.ext.config_lights.ConfigLightProvider`  
- `cos_cost.ext.export.RankingExporter`  

M2 机会卡与配置灯由 `fixtures/mock_placeholders.json` 喂布局；复制草稿不会调用任何写接口。
