# MoviePilot 服务日志持久化设计

## 背景

MoviePilot 洗版请求已经记录 HTTP 状态和脱敏后的响应正文，但这些自定义日志当前使用 `uvicorn` logger。Uvicorn 的默认配置将该 logger 设为 `propagate=False`，并只挂载控制台处理器；项目的轮转文件处理器则挂在 root logger。因此，网络库的请求状态可出现在 `backend/logs/app.log`，MoviePilot 服务自己的请求、响应、回查和错误日志却只进入启动终端。

## 目标

- 将 `backend/services/mp_service.py` 中的全部自定义日志同时写入控制台和 `backend/logs/app.log`。
- 保留现有文件轮转策略：每个文件 10 MB，最多 5 个备份。
- 保留洗版响应正文的敏感字段脱敏和 4 KB 截断。
- 不改变洗版结果判断、回查逻辑、数据库结构、API 契约或其他服务的日志范围。

## 方案

将 MoviePilot 服务 logger 从 Uvicorn 专用 logger 改为模块 logger：

```python
logger = logging.getLogger(__name__)
```

模块 logger 不自行安装处理器，保持向 root logger 传播。项目入口 `backend/main.py` 已为 root logger 配置控制台处理器和 `RotatingFileHandler`，因此 MoviePilot 服务现有日志调用会自然进入两个输出目标，不需要复制或改写每条日志。

不采用以下方案：

- 不给 `uvicorn` logger 追加文件处理器，避免把所有使用该 logger 的模块一起扩大到文件日志，并避免重复输出。
- 不新增独立的 `moviepilot.log`，避免增加轮转和运维配置；现有 `app.log` 已是项目统一诊断入口。

## 数据流

1. MoviePilot 服务产生模块日志，包括订阅更新、洗版 POST、响应、回查和异常。
2. 响应正文继续先经过 `_sanitize_response_body`，完成敏感字段脱敏和长度限制。
3. `services.mp_service` logger 将记录传播给 root logger。
4. root logger 同时交给控制台处理器和 `backend/logs/app.log` 的轮转文件处理器。

## 错误与安全

- 日志文件写入失败时沿用 Python logging 的既有行为，不影响洗版主流程。
- 不新增 Token、密码、Cookie 或 API Key 的日志输出。
- 洗版响应正文仍使用现有脱敏规则，单条最多 4096 字符。
- 本次修改不触发真实 MoviePilot 请求。

## 测试与验收

1. 新增回归测试，断言 MoviePilot 服务使用模块 logger，而不是 `uvicorn` logger，并保持向 root logger 传播。
2. 测试为 root logger 临时挂载文件处理器，通过真实 logging 调用验证 MoviePilot 日志能够写入临时文件。
3. 先运行测试确认在旧实现下失败，再做最小修改使其通过。
4. 运行 MoviePilot 洗版定向测试、后端完整测试和 Python 语法检查。
5. 确认 Git 工作区仅包含预期修改。

验收标准：测试日志能够进入临时文件；运行实例重载后，MoviePilot 服务的自定义日志（包括脱敏后的洗版响应正文）能够出现在 `backend/logs/app.log`。
