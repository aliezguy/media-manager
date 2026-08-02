# AI Translator 多级 API 瀑布流 Fallback 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重写 `backend/services/ai_translator.py`，实现数据驱动的多级 API 瀑布流 Fallback + 指数退避重试 + 严格翻译规则与健壮 JSON 解析。

**Architecture:** 每次调用动态读取 `load_config()`，按优先级解析有序 Provider 列表（新增 `ai_providers`，向后兼容旧 `sf_api_key`/`llm_base_url`/`llm_model_name`），逐级尝试：单 Provider 内指数退避重试 → 耗尽后无缝切换到下一 Provider → 全部失败返回原文映射。解析层用三层防御（正则去 Markdown 围栏 → `_safe_json_loads` 智能修复 → `{...}` 片段提取）。

**Tech Stack:** Python 3.13, openai==2.11.0, FastAPI（无新增依赖；不引入 tenacity，手写轻量重试装饰器，避免 venv 安装负担）。

## Global Constraints

- **绝不修改** `_references/` 目录下任何文件。
- 保持既有公开 API 不变（`douban_service.py` / `sync_actions.py` 依赖）：`get_translator()`、`AITranslator.is_available()`、`translate_names(names, context="")`、`translate_roles(roles, context="")`、模块级 `_is_rate_limit_error(exc)`、`_rate_limit_sleep(label="")`。
- 保留旧配置键 `sf_api_key`/`llm_base_url`/`llm_model_name` 的向后兼容（`emby.py`、`organize_service.py` 等仍直接读取，不能删）。
- 每次调用重新读取 config（热切换，无需重启）。
- 配置缺失核心字段（api_key / model_name）→ 优雅跳过该 Provider，不抛异常。
- 返回格式：键=原文，值=译文；解析失败兜底返回原文映射，不抛异常。

---

## 任务 1: 重写 `backend/services/ai_translator.py`

**Files:**
- Rewrite: `backend/services/ai_translator.py`

**Interfaces:**
- Consumes: `config.settings.load_config()`（返回 dict，含 `ai_providers`/`sf_api_key`/`llm_base_url`/`llm_model_name`/`ai_request_timeout`/`ai_max_retries`/`ai_batch_size`/`ai_request_interval`）
- Produces:
  - `get_translator() -> AITranslator`（模块单例）
  - `AITranslator.is_available() -> bool`
  - `AITranslator.translate_names(names: list[str], context: str = "") -> dict[str, str]`
  - `AITranslator.translate_roles(roles: list[str], context: str = "") -> dict[str, str]`
  - `_is_rate_limit_error(exc) -> bool`
  - `_rate_limit_sleep(label: str = "") -> None`
  - 内部：`_resolve_providers(cfg, log_invalid) -> list[dict]`、`_sanitize_provider(p) -> dict|None`、`_build_client(provider) -> OpenAI|None`、`_chat_complete_with_fallback(system_prompt, user_prompt, temperature, max_tokens) -> str|None`、`_parse_result_dict(content) -> dict|None`、`_safe_json_loads(text) -> dict|None`

### 模块结构

```
模块头（docstring + logger）
─────────────────────────────
常量: 默认 Provider、正则（JSON 围栏、中文检测）、重试默认值
─────────────────────────────
依赖导入: json / re / time / random / logging / OpenAI SDK（guarded）
─────────────────────────────
_safe_json_loads()            # 智能修复解析（移植自参考）
_parse_result_dict()          # 三层防御：去围栏→safe_loads→{...}提取
_is_rate_limit_error()        # 429 / rate limit 检测（保留导出）
_is_retryable_error()         # 429/5xx/超时/断连 → 可重试
_is_response_format_unsupported()  # 400 response_format 不支持 → 降级重试
_rate_limit_sleep()           # 随机 1.5~3.0s 打散（保留导出）
_retry_with_backoff()         # 手写指数退避 + jitter
─────────────────────────────
class AITranslator:
    _resolve_providers()      # 数据驱动 + 向后兼容回退
    _sanitize_provider()      # 校验核心字段，空值跳过
    _build_client()           # 单 Provider → OpenAI client
    _chat_complete_with_fallback()  # 瀑布流核心
    is_available()
    translate_names() / translate_roles()  # 分块 + 兜底原文
─────────────────────────────
get_translator()             # 单例
```

### 配置 Schema（新增，可选）

```json
{
  "ai_providers": [
    {"name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
     "api_key": "sk-xxx", "model_name": "deepseek-ai/DeepSeek-V3",
     "timeout": 60, "max_retries": 2},
    {"name": "OpenAI官方", "base_url": "", "api_key": "sk-yyy",
     "model_name": "gpt-4o-mini"}
  ]
}
```

优先级 = 列表顺序（Primary=index0, Secondary=index1, Tertiary=index2…）。列表为空或全部无效时，回退到旧字段 `sf_api_key` + `llm_base_url` + `llm_model_name` 构建单 Provider。

### 瀑布流伪码

```python
def _chat_complete_with_fallback(self, *, system_prompt, user_prompt, temperature, max_tokens):
    providers = self._resolve_providers(log_invalid=True)
    if not providers:
        logger.info("未配置任何可用 AI Provider")
        return None
    last_exc = None
    for idx, provider in enumerate(providers):
        pname = provider["name"]
        client = self._build_client(provider)
        if client is None:
            logger.warning("Provider[%s] 客户端构建失败，跳过", pname); continue
        try:
            resp = _retry_with_backoff(
                lambda: self._chat_once(client, provider, system_prompt, user_prompt, temperature, max_tokens),
                max_retries=provider["max_retries"], base_delay=1.0, max_delay=8.0,
                should_retry=_is_retryable_error,
                on_retry=lambda a, e, d: logger.warning("Provider[%s] 第%d次重试(%s)…", pname, a, e))
            return resp.choices[0].message.content
        except Exception as exc:
            last_exc = exc
            logger.warning("Provider[%s] 重试耗尽，降级到下一个 API: %s", pname, exc)
    logger.error("全部 Provider 均失败，最后一次异常: %s", last_exc)
    return None
```

### 验证清单（verification-before-completion）

- [ ] `translate_names` / `translate_roles` 空输入 → 返回 `{}`
- [ ] 无任何 Provider 配置 → 返回原文映射，不抛异常，打 info 日志
- [ ] 某 Provider 缺 api_key / model_name → 跳过，log warning，继续下一个
- [ ] 429 / 500 / 超时 → 指数退避重试（max_retries 次）→ 耗尽切换下一 Provider
- [ ] 全部失败 → 返回原文映射 + log error
- [ ] 正则剔除外层 ```json / ``` 后再 `json.loads`
- [ ] 解析失败 → 原文映射兜底
- [ ] `_is_rate_limit_error` / `_rate_limit_sleep` 仍可被 douban_service 导入
- [ ] `sf_api_key`/`llm_base_url`/`llm_model_name` 旧键未删除，emby.py 等不受影响
- [ ] `is_available()` 不触发客户端构建、不刷告警日志

### 验证方式

```bash
cd backend && source venv/bin/activate
python3 -c "import ast; ast.parse(open('services/ai_translator.py').read())"   # 语法检查
python3 -c "from services.ai_translator import get_translator, _is_rate_limit_error, _rate_limit_sleep; print('imports ok')"
python3 -c "
from services.ai_translator import _parse_result_dict
assert _parse_result_dict('{\"A\":\"B\"}') == {'A':'B'}
assert _parse_result_dict('\`\`\`json\n{\"A\":\"B\"}\n\`\`\`') == {'A':'B'}
assert _parse_result_dict('好的，这是结果：{\"A\":\"B\"} 多余文字') == {'A':'B'}
assert _parse_result_dict(None) is None
print('parse tests ok')
"
python3 -c "from services.ai_translator import AITranslator; t=AITranslator(); print('available:', t.is_available()); print(t.translate_names([])); print(t.translate_names(['Zhao Wenlong']))"
```

## 自检结论

- **Spec 覆盖**：瀑布流（Task1）覆盖动态调度/空值跳过/重试降级/正则清理/原文兜底/向后兼容；参考文件翻译规则通过 system prompt 实现。
- **占位符扫描**：无 TODO/TBD。
- **类型一致**：`translate_names/translate_roles` 签名与调用方（sync_actions.py:290,312、douban_service.py:206,1371,1391）完全一致。
