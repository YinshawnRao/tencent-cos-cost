# COS 机会大师 · M1 CLI + M2 看板 + M3 规则引擎

只读工具：列出腾讯云 COS 存储桶，拉取账户级 COS 账单与按桶应付，读取生命周期 / 版本 / 清单 / 日志配置与监控，用规则引擎计算机会卡与「可优化金额」，并提供账号全局 / 桶页看板、一页 PDF / 五表 Excel、模板问答。

**不会** List Objects（GetBucket）、**不会** PUT 生命周期或任何写接口。抽屉按钮只有「复制草稿」。

## 做什么 / 不做什么

| 做 | 不做 |
| --- | --- |
| GET Service（List Buckets） | List Objects / GetBucket（列对象） |
| HeadBucket（仅地域缺失时） | 任何 Put / Delete / 生命周期写入 |
| `GetBucketLifecycle` / `GetBucketVersioning` / `GetBucketLogging` / `GetBucketInventory` | 读清单 CSV、对全桶列对象 |
| `DescribeBillSummaryByProduct`（COS 应付、Ready） | 翻页 `DescribeBillDetail` |
| `DescribeBillResourceSummary`（按桶应付，`BusinessCode=p_cos`） | 修改账单或资源 |
| `GetMonitorData`（容量 / 流量 / 请求 / 分块） | 监控写、告警改配 |
| 规则引擎 R01/R02/R03/R04/R10/R11/R12（+ R06 备注） | 把草稿应用到桶 |
| PDF / Excel 导出、`POST /api/ask` | 模型编造金额 |
| `--mock` fixture，无 AK/SK | 把 SecretKey 写入缓存、日志或前端 |

账单 `Ready=0` → 横幅 **暂估**，PDF 加水印，仍出排行。  
前缀热点固定空态：「清单未就绪，对象级建议不可用」。M3 **不读** 清单 CSV。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

需要 Python 3.11+。

## 本机测试（网页填 AK/SK，不用 CLI / .env）

**仅限本机。不要把 `serve` 暴露到公网。**

```bash
python -m cos_cost serve --mock --port 18765
```

浏览器打开 <http://127.0.0.1:18765/> → 右上角 **密钥**（mock 时面板默认打开）→ 粘贴只读子用户 SecretId / SecretKey（可选 COS_TOKEN、账期）→ **保存并拉取**。服务端会 collect，然后刷新排行。

- 密钥存在 **内存** 与 gitignore 的 **`.local-creds.json`**（chmod 600）。
- **不会** 写入 `cache/` JSON、HTML、日志、PDF/Excel。页面只显示掩码 SecretId（`AKID****abcd`）和「已保存」。
- **改用 mock 数据** / **清除密钥**：清掉本地密钥，回到 fixture。
- 之后 `python -m cos_cost serve`（可不加 `--mock`）会读 `.local-creds.json`，刷新浏览器不必再贴密钥。
- `--mock` 仍可无密钥启动；保存并拉取会把**正在运行的服务**切到 live（内存覆盖）。

CAM：子用户只读，粘贴 [docs/cam-m1.json](docs/cam-m1.json)。**不要**授予 `name/cos:GetBucket`。

问答仍是模板，模型 API Key 字段会存盘但 **M3 未使用**。

### 300+ 桶：首拉做什么、怎么停

账号 **保存并拉取** 不会对每个桶打 4 次配置 GET。第一遍只做：

1. **列桶** `GET Service`（List Buckets）
2. **账单** 汇总 + 按资源排行（费用中心，约 5 次/秒）
3. **监控** 账号列需要的容量 / 外网 / 请求（`GetMonitorData` 按批串行，默认每批 50 个实例，不开一桶一线程）
4. **前 N 配置** 按应付取 Top **30**（`COS_CONFIG_TOP_N`）读 Lifecycle / Versioning / Logging / Inventory

打开 `/b/{bucket}` 时才懒加载该桶的 4 个只读配置 GET。不要对全桶 List Objects。

限速（可用环境变量覆盖）：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `COS_CONFIG_TOP_N` | 30 | 账号首拉配置桶数 |
| `COS_QPS` | 8 | COS Head/配置 全局 QPS |
| `COS_MAX_INFLIGHT` | 4 | COS 配置/Head 最大并发 |
| `COS_BILLING_QPS` | 5 | 账单分页 |
| `COS_MONITOR_BATCH` | 50 | GetMonitorData 单次实例数 |

进度在页面显示阶段（列桶 / 账单 / 监控 / 前N配置）和计数。点 **停止拉取** 会 set cancel；采集在独立线程，不堵 FastAPI 事件循环，Ctrl+C 应能退出 `serve`。限速 sleep 按 ≤0.2s 切片检查取消。腾讯云 SDK 某一次 HTTP 仍可能短暂不可中断。

若进程仍卡住，最后手段：

```bash
kill -9 $(lsof -tiTCP:18765)
```

### Mock 看板 + 导出 + 问答（无密钥）

```bash
python -m cos_cost serve --mock --port 18765
```

浏览器：

- 账号全局：<http://127.0.0.1:18765/>
- 桶页：<http://127.0.0.1:18765/b/logs-prod-1250000000>
- JSON：<http://127.0.0.1:18765/api/account?month=2026-07>
- 问答：`POST /api/ask` `{"q":"这个月为什么贵","month":"2026-07"}`
- 导出：<http://127.0.0.1:18765/export/pdf?month=2026-07> 与 `/export/xlsx?month=2026-07`

默认账期是 **UTC+8 上一自然月**（今天为 2026-08 时即 `2026-07`）。

机会卡由 **RuleEngine** 根据 mock 监控 + `bucket_config`（生命周期 / 版本 / 清单 / 日志）算出，不是静态 JSON 卡片。`fixtures/mock_placeholders.json` 只留布局对照。

### 导出 CLI

```bash
python -m cos_cost export --mock --month 2026-07 --pdf out.pdf --xlsx out.xlsx
```

- PDF：A4 横向一页，6 个 KPI、C1/C2 简单柱、Top5 桶、Top5 机会、页脚口径。`Ready=0` 水印「暂估」。
- Excel 五表：`汇总` / `按桶` / `按计费项` / `机会` / `口径`。
- 按桶应付合计对账 COS 应付（`RealTotalCost`），容差 **0.05 元**（见「口径」表）。
- UIN / 账户默认掩码。不含密钥、不含对象 Key。

### 问答示例

```bash
curl -s http://127.0.0.1:18765/api/ask \
  -H 'content-type: application/json' \
  -d '{"q":"这个月为什么贵","month":"2026-07"}'
```

三个意图：

| 问法 | 行为 |
| --- | --- |
| 这个月为什么贵 | 用缓存 KPI + 头部桶解释，数字来自引擎 |
| `{bucket} 怎么省` | 该桶机会卡（rule_id / net / confidence / why） |
| 导出上月一页 | 返回 `/export/pdf` `/export/xlsx` 链接 |

模板作答，**不编造金额**。若以后接 LLM，只允许改写 `copy` 字段。

### CLI 仍然可用

```bash
python -m cos_cost rank --mock
python -m cos_cost rank --mock --month 2026-07 --json
python -m cos_cost collect --mock --month 2026-07
```

## 线上模式（.env 仍可用，不是必填）

本地测试请优先走上面的网页填钥。若仍想用环境变量：

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

第二次 collect/serve 走缓存：账单 `Ready=1` 后该月不可变；桶列表 / 配置 1 小时；监控 30 分钟。`--force` 强制回源。

**切勿**把 SecretKey 打到日志、工单、缓存或前端 HTML。

## 创建 CAM 子用户（最小只读）

1. 访问管理 → 用户 → 新建用户 → 编程访问。  
2. 预设：`QcloudFinanceBillReadOnlyAccess` + `QcloudMonitorReadOnlyAccess`。  
3. 自定义：粘贴 [docs/cam-m1.json](docs/cam-m1.json)（`GetService` + `HeadBucket` + `GetBucketLifecycle` / `Versioning` / `Logging` / `Inventory` + `finance:DescribeBill*` + `monitor:GetMonitorData`）。  
4. **不要**授予 `name/cos:GetBucket`（列对象）或任何 Put。

账单：`billing.tencentcloudapi.com`，版本 `2018-07-09`。  
监控：Region **必须** `ap-guangzhou`，Namespace `QCE/COS`，维度 `bucket` = 带 APPID 的全名。

## 规则引擎（Phase-1）

单价：有账单用量时用 `RealTotalCost / usage`，否则用刊例并在证据中标注「刊例」。

硬约束：`<64KB` 不计入沉降 GB（无清单时用 20% 保守折扣代替）；建议 IA/ARCHIVE/DEEP 天数 ≥30/90/180；不建议 GetBucket。

| 规则 | 触发 | 金额 | 列 |
| --- | --- | --- | --- |
| R03 | 未完成分块 ≥1GB 或 ≥1% 桶，或缺少 Abort | `GB × P_class`，置信度 ~0.9 | 本月可回收 |
| R11 | 4xx+5xx 占比 >10% 且请求费 ≥50 | `failed_wan × P_get` | 本月可回收 |
| R01 | 大额 STANDARD + 低请求密度；无清单则桶级保守 | `0.2 × STANDARD × (P_std−P_ia)`，低置信 | 下月起稳态 |
| R02 | 无 Transition / 无 Abort / 版本开而无 Noncurrent* | 配置项，金额记在 R01/R03/R04 | 下月起稳态 |
| R04 | 版本开而无非当前版本过期；无清单不编造 GB | 仅配置 | 下月起稳态 |
| R10 | IA&lt;30 / ARCHIVE&lt;90 / DEEP&lt;180 | 避免提前删除风险 | 下月起稳态 |
| R12 | Inventory / Logging 目标桶 | 纳入账号排行 | 需业务改造 |
| R06 | 外网远大于 CDN 回源 | **COS 侧备注**，标题含「不含 CDN 下行」，**不进 KPI** | 需业务改造 |

**可优化 KPI** = 净节省 ≥ 50 元/月且无强阻断。不计入：R05/R07/R08/R09 金额、R06、备份桶（名称含 backup）。

## 路由

| 路径 | 说明 |
| --- | --- |
| `/` | 账号全局：KPI、C1–C5、机会三列、问答框 |
| `/b/{bucket}` | 桶页：图表、配置体检、机会 + 抽屉（复制草稿） |
| `/api/account` | 账号 JSON |
| `/api/buckets/{bucket}` | 桶 JSON |
| `/api/ask` | `POST {q, month}` → `{answer, numbers[], links[]}` |
| `/api/settings/status` | `{mode, secret_id_masked, month, last_collect_error?}` |
| `/api/settings/credentials` | `POST {secret_id, secret_key, token?, month?}` 保存并**异步** collect，立刻 `{status: running}` |
| `/api/settings/job` | `{done, buckets_done, buckets_total, phase, error}` |
| `/api/settings/job/cancel` | 停止拉取 |
| `/api/settings/mock` | 清除密钥，回到 mock fixture |
| `/export/pdf` `/export/xlsx` | 下载报表 |

## 缓存

`./cache/<account>/<data_kind>[_YYYY-MM].json`，键 `(appid, month, data_kind)`。应付用官方字段 **`RealTotalCost`**，产品 **`p_cos`**。配置缓存 `bucket_config.json`（1 小时）。

## 测试

```bash
pytest
```

全部 mock / MagicMock，不需要真实 AK/SK。覆盖：各规则在 fixture 上命中、PDF/xlsx 存在、按桶合计对账 186420、问答返回 mock KPI、源码不含 GetBucket / 写接口、本机密钥 API 不回传 SecretKey、`.local-creds.json` 被 gitignore、80+ 桶账号采集不对每个桶打 lifecycle、cancel Event 能停、settings job poll。

## 给后续阶段

- `cos_cost.ext.opportunity.RuleEngine`  
- `cos_cost.ext.config_lights.SnapshotConfigLights`  
- `cos_cost.ext.export.ReportExporter`  
- `cos_cost.ext.chat.answer_question`  

清单 CSV 前缀级 R01、以及把草稿应用到桶，都不在 M3。
