# MoviePilot 洗版结果回查设计

## 背景

Emby AI Manager 在完结订阅触发洗版时，通过 `POST /api/v1/subscribe/`
创建 MoviePilot 洗版订阅。当前实现只在 HTTP 200 响应包含
`success: true` 或 `code: 0` 时判定成功，其余情况一律写入
“洗版API请求失败”。

`地球超新鲜` S02 的实际情况是 MoviePilot 已创建洗版订阅，但任务历史仍
记录失败。这说明一次 POST 的返回结果不足以证明最终状态，系统需要在结果
不明确时验证 MoviePilot 中是否已经形成目标订阅。

## 目标

- 同时在后端日志和 `wash_history.wash_params` 中记录 MoviePilot POST 的
  HTTP 状态与响应正文。
- 响应正文最多保存 4 KB，并对敏感字段脱敏。
- POST 未明确成功时，使用请求中的 TMDB ID 和季号回查 MoviePilot。
- 只有回查到同一 TMDB、同一季且具有洗版标志的订阅时，才将任务判为成功。
- 保留现有洗版策略、Payload 和历史页面的数据结构兼容性。

## 非目标

- 不修改 MoviePilot 本身。
- 不自动删除、重建或重复提交已有订阅。
- 不修改 `wash_history` 表结构。
- 不改变洗版策略匹配、站点、下载器或质量规则。

## 方案

### 结构化调用结果

`add_wash_subscription()` 不再只返回布尔值，而是返回一个结构化结果，至少
包含：

- `success`: 最终是否成功。
- `http_status`: MoviePilot POST 的 HTTP 状态；请求未获得响应时为 `None`。
- `response_body`: 脱敏并截断后的响应正文。
- `error`: 网络、解析或回查错误；无错误时为 `None`。
- `verified_by_lookup`: 是否通过回查确认成功。
- `subscription_id`: 回查到的 MoviePilot 订阅 ID；未找到时为 `None`。

该结果用于统一驱动日志和洗版历史，避免调用层丢失诊断信息。

### POST 判定

1. 请求前无法获得 MoviePilot Host 或 Token 时，直接返回失败。由于 POST 没有
   发出，不进行回查。
2. POST 返回 2xx 且 JSON 明确包含 `success: true` 或 `code: 0` 时，直接返回
   成功。
3. 其余已发出 POST 但未明确成功的情况均视为“不确定”，包括：
   - 非 2xx 响应；
   - 2xx 但成功标志缺失或为非成功值；
   - 响应不是合法 JSON；
   - POST 超时或连接在发送后中断。
4. 不确定结果进入回查，不再次 POST。

### 回查规则

回查函数使用 `tmdbid` 与 `season` 查询 MoviePilot 订阅，并验证：

- `tmdbid` 与请求一致；
- `season` 与请求一致；
- `best_version == 1`；
- `best_version_full == 1`。

四项全部满足即认为目标洗版订阅已经存在，最终结果为成功，并设置
`verified_by_lookup=true`。普通追更订阅、其他季订阅或其他 TMDB 订阅不能
把失败转换为成功。

优先使用 MoviePilot 的媒体订阅查询接口；为兼容不同版本，如直接查询未返回
可验证对象，可读取订阅列表并执行相同的严格筛选。查询本身只读。

### HTTP 正文记录与脱敏

响应正文经过统一处理后再写日志或数据库：

1. 最多保存 4096 个字符，超出部分追加截断标记。
2. JSON 响应递归脱敏以下键（大小写不敏感）：
   `token`、`access_token`、`password`、`cookie`、`api_key`、`apikey`、
   `authorization`。
3. 非 JSON 正文使用同一敏感字段名单进行文本脱敏。
4. 日志与 `wash_params.response_body` 使用同一个已处理值，禁止一处脱敏、
   另一处保存原文。

## 数据流

1. `run_wash_process()` 构造现有洗版 Payload。
2. `add_wash_subscription()` 发出 POST 并捕获 HTTP 状态、正文和异常。
3. 明确成功则返回结构化成功结果。
4. 未明确成功则按 Payload 的 TMDB ID 和季号回查。
5. 回查命中严格洗版订阅则返回“回查确认成功”，否则返回失败。
6. `run_wash_process()` 根据最终结果写入 `WashHistory.status/message`，并将
   HTTP 与回查诊断字段合并到现有 `wash_params`。

## 历史记录字段

在现有 `wash_params` JSON 中新增：

- `http_status`
- `response_body`
- `error`
- `verified_by_lookup`
- `subscription_id`

现有 `scheme`、`downloader`、`filter_groups`、`quality`、`sites` 和
`keywords` 字段保持不变。无需数据库迁移。

历史消息保持简洁：

- POST 明确成功：`已触发洗版重订阅`
- 回查确认成功：`洗版订阅已创建（回查确认）`
- 最终失败：`洗版API请求失败`

## 日志行为

- 每次 POST 记录 HTTP 状态和已处理的响应正文。
- POST 未得到响应时记录异常类型和文本。
- 进入回查时记录 TMDB ID 与季号。
- 回查成功时记录订阅 ID；回查失败时记录未匹配原因。
- 不记录认证 Token 或未经处理的响应正文。

## 测试设计

使用单元测试隔离 HTTP 调用和数据库写入，覆盖：

1. 2xx + `success: true`：直接成功，不回查。
2. 2xx + `code: 0`：直接成功，不回查。
3. 2xx 未知响应：回查到严格匹配洗版订阅，最终成功。
4. 非 2xx：回查到严格匹配洗版订阅，最终成功并保留原始 HTTP 状态。
5. POST 异常：回查到严格匹配洗版订阅，最终成功并保留异常。
6. 回查只找到普通追更订阅：最终失败。
7. 回查找到错误季或错误 TMDB：最终失败。
8. 回查也发生异常：最终失败并保留 POST 与回查诊断。
9. JSON 与文本响应中的敏感字段被脱敏。
10. 超过 4096 字符的响应被截断。
11. `run_wash_process()` 把 HTTP、正文和回查字段写入 `wash_params`，并根据
    最终结果选择正确的状态和消息。

## 验收标准

- 模拟 `地球超新鲜` 场景：POST 结果不明确，但 MoviePilot 中存在
  `TMDB 296202 + S02 + best_version=1 + best_version_full=1` 时，历史必须记
  为成功且标记 `verified_by_lookup=true`。
- MoviePilot 中只有普通追更订阅时不能误判成功。
- 日志和任务历史都能看到 HTTP 状态与脱敏、截断后的响应正文。
- 现有测试与新增回归测试全部通过。
