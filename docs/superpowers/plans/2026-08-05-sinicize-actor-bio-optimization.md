# 汉化电视剧演员简介优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 按用户偏好使用严格 TDD（先写失败测试→确认 RED→GREEN→全量回归→commit），每 Task 后停下等用户 Review。

**Goal:** 汉化电视剧时不再逐演员触发 LLM 简介补全（慢路径），改为「演员身份/头像解析」与「简介 LLM 补全」解耦——简介统一交给演员库更新；新增演员仅建记录；角色名翻译保留；并审计/修复审批流程的同样问题。

**Architecture:** 在 `resolve_actor_profile` / `_llm_enrich_existing` / `ensure_profiles_for_people` / `save_media_to_db` 线程化透传 `skip_llm_enrich: bool | None = None` 参数：
- `None`（默认）→ 内部读取配置 `actor_bio_inline_enabled`（默认 `False`）决定是否内联补简介：`skip = not inline_enabled`。汉化/审计路径一律不传，受配置开关控制（切回旧行为只需改 config）。
- `True` → 强制跳过 LLM 简介补全（显式）。
- `False` → 强制不跳过（演员库刷新/修复路径显式传，LLM 补全始终开启，不受配置影响）。

**Tech Stack:** Python FastAPI / SQLAlchemy / SQLite(MySQL 方言) / pytest (monkeypatch + Boom 探针)

## Global Constraints

- 严格 TDD：先写失败测试 → 确认 RED → 最小实现 → GREEN → 全量回归 → commit
- 每 Task 独立 commit，完成后停下等用户 Review
- `skip_llm_enrich=None` 语义是「跟随配置」，必须与「显式 True/False」严格区分（用三元判断，勿用 `or`），避免演员库路径被配置误伤
- 新增配置项 `actor_bio_inline_enabled`（默认 `False`）：False=汉化/审计不内联补简介（快）；True=切回旧行为（内联补）
- 不得删除/削弱现有角色名翻译链路（顶层 `_match_and_update` + 分集 `_localize_episode_people` + 缺失角色 `_infer_missing_roles_via_ai`）
- 防伪红线不放松：即使 skip 简介，TMDB/豆瓣 免费元数据照常入库；LLM 结果仍须经 `is_valid_chinese_translation` 校验
- 测试手段偏好：Boom 探针（断言 0 次调用）、monkeypatch 隔离网络与 LLM

---

## 背景审计（本轮已完成）

### 1. 慢路径根因

汉化电视剧入口 `DoubanSinizer.sinicize()`（`backend/services/douban_service.py:137`）：

- 顶层演员：`save_media_to_db(..., light_profiles=True)`（:393）→ `ensure_profiles_for_people`（`db_crud.py:208`）→ 逐演员 `resolve_actor_profile(light_mode=True)`
- 分集演员：前置去重批处理 `ensure_profiles_for_people(ep_db, unique_people, light_mode=True)`（:509）

`resolve_actor_profile`（`actor_profile_service.py:917`）**四条路径全部触发 LLM 简介/出生地补全**：

| # | 路径 | 触发点 | 行号 |
|---|------|--------|------|
| 1 | L0 数据库极速命中 | `_llm_enrich_existing` | :1011 |
| 2 | L0 硬盘嗅探命中 | `_llm_enrich_existing` | :1061 |
| 3 | 头像冷却期内 | `_llm_enrich_existing` | :1105 |
| 4 | 完整网络路径 | `enrich_actor_metadata` | :1434-1445 |

`enrich_actor_metadata`（`actor_profile_ai.py:328`）对每位演员最多 3 次 LLM 调用：简介空→生成 / 非中文→翻译（`_MAX_TOKENS_BIO=2000`）、出生地翻译/知识生成、生日提取。

**规模放大**：首次汉化所有演员 `llm_check_status=0` → 冷静期不生效 → 全员全字段打满 LLM。一部剧 50 位顶层演员 + 全剧分集去重演员（可能上百）→ **数百次 LLM 调用**。这是进度慢的主因。

### 2. 审批/审计流程审计（Requirement 3）—— **有同样问题，且更重**

- 入口：`audit_local`（`sync_actions.py:731`）、`audit_selected`（:830）、`batch_audit`（:1417）、`sinicize_selected`（:2326）、`sinicize_all`（:2370）→ 全部汇聚到 `_sync_and_audit_single_item`（:613）→ `_audit_and_save_single_item`（:449）→ `save_media_to_db(light_profiles=False)`（:512、:559）
- 审计用 **light_profiles=False**（每演员全量 TMDB 上半场网络请求）+ 逐演员 LLM 简介补全 → 比 sinicize 更重
- 另有 `_batch_enrich`（/api/episodes/batch-enrich, :1318）与 `_batch_audit_task`（:2019）的 guest stars 漏斗 → `ensure_profiles_for_people` → 同样触发 LLM 简介

### 3. 角色名翻译现状（Requirement 2）—— **已存在且解耦，保留即可**

- 顶层：`_match_and_update` → `translate_roles` 批处理（`douban_service.py:290-322`）+ `_infer_missing_roles_via_ai` 缺失角色批推理（:346-370）
- 分集：`_localize_episode_people` 四级漏斗（:1831）+ AI 批翻译
- **关键**：角色翻译是批处理（每批 1 次 LLM），且完全独立于 `resolve_actor_profile` 的简介补全 → 本次跳过简介不会影响角色翻译

---

## 设计

### 核心机制：线程化 `skip_llm_enrich` 参数（三态）

```
resolve_actor_profile(actor_name, db, context_info, force_refresh, light_mode, skip_llm_enrich=None)
  ├─ None（默认）→ cfg 决定: skip = not cfg.get("actor_bio_inline_enabled", False)
  ├─ True → 跳过 3 处 _llm_enrich_existing 调用（:1011/:1061/:1105）+ 末尾 enrich_actor_metadata 块（:1434-1445）
  └─ False → 始终补全（演员库路径显式传，不受配置影响）

_llm_enrich_existing(actor_name, existing, db, skip_llm_enrich=False)
  └─ True 时直接 return None（不触发任何 LLM）

ensure_profiles_for_people(db, people, light_mode=False, skip_llm_enrich=None) → 透传 resolve

save_media_to_db(db, ..., skip_profiles, light_profiles, skip_llm_enrich=None) → 透传 ensure_profiles_for_people
```

**skip=True 时的语义**（汉化/审计默认路径）：
- 不存在的演员 **仍会新增** ActorProfile 记录（identity + 头像 + TMDB/豆瓣 免费元数据，如 bio 原样取自 TMDB/豆瓣，但不触发 LLM 生成/翻译）
- 已存在的演员 **不动**（不尝试 LLM 覆盖）
- 角色名翻译完全不受影响（独立链路）

**skip=False 的语义**（演员库路径，显式传，行为不变）：
- `/actors/{actor_name}/refresh`（`actor_router.py:170`）
- `/actors/repair_missing` → `_batch_repair_task`（`actor_router.py:302`）
- `/actors/repair_birthplace` → `_repair_birthplace_task`
- 新增 `/actors/repair_overview` → `_repair_overview_task`

### 调用点改造（skip=None，跟随 `actor_bio_inline_enabled` 配置）

| 调用点 | 位置 | 说明 |
|--------|------|------|
| sinicize 顶层入库 | `douban_service.py:393` | 系列汉化顶层 |
| sinicize 分集前置批处理 | `douban_service.py:509` | 全剧唯一演员漏斗 |
| 审计已汉化分支入库 | `sync_actions.py:512` | `_audit_and_save_single_item` |
| 审计未汉化分支入库 | `sync_actions.py:559` | 同上 |
| batch-enrich guest stars | `sync_actions.py:1318` | TMDB 整季富化 |
| batch_audit guest stars | `sync_actions.py:2019` | 批量审计 |

这些调用点**不显式传参**（None）→ 默认受配置 `actor_bio_inline_enabled=False` 控制即跳过简介；用户把配置改 `True` 即可切回旧行为。

### 演员库「一键补简介」新端点 `/actors/repair_overview`（Requirement 1 闭环）

新建独立端点，不动 `repair_missing` 语义：

1. `POST /actors/repair_overview` → 后台任务 `_repair_overview_task`（模式对齐 `_repair_birthplace_task`，try/except/finally 保证任务不悬挂）
2. 查询条件：`overview` 为空 **或** 非中文（`is_valid_chinese_translation` 判定，仅查可判定的空/明确非中文，避免 SQL 中文判断复杂化；MySQL 兼容用空串判定 + 应用层中文率过滤）
3. 逐演员 `resolve_actor_profile(actor_name, db, skip_llm_enrich=False)` —— 走 L0 缓存命中 + `_llm_enrich_existing` 补简介（零网络），无缓存才落到网络路径；`llm_check_status=2` 冷静期天然限流冷门演员
4. 每演员独立 try/except + savepoint 隔离，单个失败不影响整体；进度经 task_manager 上报

---

## 任务分解（严格 TDD，每 Task 独立 commit + 用户 Review）

### Task 1: `skip_llm_enrich` 三态参数进入 `_llm_enrich_existing` 与 `resolve_actor_profile`
- 文件：`actor_profile_service.py`
- 测试：`tests/test_actor_profile_ai.py` 新增（或新 `tests/test_bio_skip.py`）
  - RED: `resolve_actor_profile(skip_llm_enrich=True)` L0 缓存命中时 **0 次** `_llm_enrich_existing`/`enrich_actor_metadata`（Boom 探针）
  - RED: `resolve_actor_profile(skip_llm_enrich=True)` 完整网络路径时 0 次 `enrich_actor_metadata`（Boom）
  - RED: `resolve_actor_profile(skip_llm_enrich=None)` + config `actor_bio_inline_enabled=False` → 同 skip=True（跟随配置）
  - RED: `resolve_actor_profile(skip_llm_enrich=None)` + config `actor_bio_inline_enabled=True` → 同 skip=False（内联补，切回旧行为）
  - RED: `resolve_actor_profile(skip_llm_enrich=False)`（演员库路径）→ 始终补全
- GREEN: 加三态参数 + 3 处 guard + 末尾 guard；默认 None 跟随配置，配置默认 False；全量回归不红

### Task 2: `skip_llm_enrich` 透传 `ensure_profiles_for_people` 与 `save_media_to_db`
- 文件：`actor_profile_service.py`、`db_crud.py`
- 测试：
  - RED: `ensure_profiles_for_people(skip_llm_enrich=X)` 透传给 resolve（monkeypatch `resolve_actor_profile` 探针断言入参 X 原样）
  - RED: `save_media_to_db(skip_llm_enrich=X)` 透传给 ensure_profiles_for_people（同探针）
- GREEN: 加参数 + 透传（含 `light_mode` 组合）

### Task 3: sinicize 两个调用点改为跟随配置（不显式传参）
- 文件：`douban_service.py:393`、`:509`
- 测试：`tests/test_light_mode.py` 或新 `tests/test_sinicize_bio_skip.py`
  - RED: 汉化一个 Series（模拟）后，新增演员的 `overview` 为空/仅 TMDB 免费值，且 `enrich_actor_metadata` 0 次被调用；同时 `actor_profiles` 中记录 **已新增**（默认 config 下即跳过）
  - RED: 断言角色名翻译仍触发（`translate_roles`/`_infer_missing_roles_via_ai` 的调用计数 > 0 或结果回填）——证明 Requirement 2 保留
- GREEN: 确认调用点默认不传参即生效（若调用点显式传值需移除，确保跟随配置）

### Task 4: 审计/审批流程调用点改为跟随配置
- 文件：`sync_actions.py:512`、`:559`、`:1318`、`:2019`
- 测试：`tests/test_audit_series_count.py` 或新测试
  - RED: `_audit_and_save_single_item` 已汉化分支 → `save_media_to_db` 不显式传 skip（探针断言传 None/未传）且 config 默认 False 时 LLM 0 次
  - RED: batch-enrich / batch_audit guest stars 同样探针
- GREEN: 确认四处调用点默认不传参即生效

### Task 5: 新增 `/actors/repair_overview` 端点 + `_repair_overview_task`
- 文件：`actor_router.py`（+ `models.py` 无改动；`actor_profile_service` 已具备能力）
- 测试：
  - RED: 构造 3 位演员（缺 overview / 非中文 overview / 完整中文）→ 前两位进入修复列表，完整者跳过
  - RED: `_repair_overview_task` 对缺简介演员调用 `resolve_actor_profile(skip_llm_enrich=False)`（探针断言显式 False，即使 config=True 也强制补全）
  - RED: 单演员异常不炸整体（try/except 隔离）
- GREEN: 端点 + 后台任务实现，模式对齐 `_repair_birthplace_task`

### Task 6: 全量回归 + 文档 + 记忆 + commit
- 全量 pytest，确认 0 失败
- 更新记忆文件（演员简介解耦 + config 开关 + repair_overview 入口）
- 提交

---

## 决策点（用户已确认 2026-08-05）

| 决策 | 选择 |
|------|------|
| D1 新增演员是否跳过简介 LLM | **全部跳过** — 汉化/审计对新旧演员一律不内联补简介；新演员只建身份+TMDB/豆瓣免费元数据 |
| D2 审计是否一并切 light_mode | **只砍 LLM 简介** — 审计保持全量漏斗语义不变，仅跳过逐演员 LLM 简介 |
| D3 配置开关 | **加 config 开关** — `actor_bio_inline_enabled`（默认 `False`），汉化/审计跟随配置，可切回旧行为；演员库路径显式 `skip_llm_enrich=False` 不受配置影响 |
| D4 补简介入口 | **另开新端点** `/actors/repair_overview` — 不动 `repair_missing` 语义 |

---

## 交付物清单
- 6 个 Task 的代码改动 + 对应测试文件
- 新增配置项 `actor_bio_inline_enabled`（默认 False）+ 前端配置展示（如有配置页面联动）
- 新增端点 `/actors/repair_overview`
- 全量回归通过记录（含 commit hash）
- 记忆文件更新（actors 简介解耦决策 + config 开关 + repair_overview 入口）
