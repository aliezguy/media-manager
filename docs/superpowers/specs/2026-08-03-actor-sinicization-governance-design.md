# 演员中文化治理规划 — 设计文档

日期：2026-08-03
状态：已确认
范围：演员中文化全链路治理，覆盖 5 个独立问题（豆瓣请求优化 / 集数同步+计数bug / 分集查找 / 前端UI / MySQL迁移）。实施拆分为 4 个顺序阶段，每阶段可独立发布与回归。

## 背景与目标

「演员中文化」功能从 Emby 拉取媒体元数据 → 豆瓣匹配 → 中文名/角色本地化 → 回写 Emby + SQLite 三表入库。
当前存在 5 类问题阻碍日常使用与规模化治理：

1. **豆瓣请求量失控风险**：系列汉化时每名唯一演员都走 TMDB「上半场」详情抓取，一部 28 人剧集最多产生 ~56 次 TMDB 调用；无按系列/批次的请求预算。
2. **SQLite 单机瓶颈**：数据落在 `backend/data/emby_ai.db`，需迁至 MySQL（192.168.31.135:3008 / media-ai）。
3. **集数不同步**：Episode webhook 只审计单集，从不刷新父 Series；实际 12 集只入库 7 集；BatchAudit 摘要用 TMDB 整季数（30）冒充实际数（12）。
4. **前端按钮 bug**：「同步选中项 (N)」永不显示——`pendingCheckedIds` 严格过滤 `status === 'pending'`，而真实数据含 `failed` / `NULL`。
5. **分集豆瓣查找失败**：单集被直接汉化时按集名搜豆瓣（如「第 26 集」）必然失败。

### 明确不做（YAGNI）

- 不做双库持续同步（迁移期双向同步）——用户确认纯切换。
- 不做「一个电视剧只发一次请求」的字面实现——以「轻量头像 + cast 缓存 + 请求预算」逼近，保留必要的 Emby 分集读取。
- 不重写审计/汉化编排为统一管道（方案 C 被否）——沿现有函数做定点改造。
- 不做每集独立豆瓣页的搜索增强——豆瓣 Frodo search 不索引单集页，此路不通。

## 已确认决策（澄清问答汇总）

| # | 问题 | 决策 |
|---|------|------|
| 范围 | 本轮组织 | 一份总设计，分批实施（4 阶段） |
| 整体方案 | 结构 | 方案 A：正确性优先 → 前端 → 请求治理 → 基础设施 |
| 2 | MySQL 后 SQLite | 纯切换（生产弃用 SQLite，测试仍可用 sqlite :memory:） |
| 3 | webhook 集数同步 | 轻量对账父 Series，有缺口才全量同步+汉化 |
| 4 | 「同步选中项」语义 | 对齐后端：`status !== 'synced'` 即算待汉化 |
| 1 | 头像解析深度 | 汉化时轻量头像（L0/L0.5/复用豆瓣直链，跳过 TMDB 每演员），无头像走演员库后补 |
| 5 | 分集查找 | 单集一律走父 Series 豆瓣页，不再按集名搜豆瓣 |

## 关键现状发现（决定方案走向）

来自代码勘察，均验证到行号：

1. **「演过什么剧→找头像」链路已不存在**：actor_profile_service.py 两条豆瓣 L1 路径被 `and False` 硬禁用（[:1020](backend/services/actor_profile_service.py#L1020) 盲搜+作品溯源、[:1269](backend/services/actor_profile_service.py#L1269) celebrity_details）。真正请求大头是**每演员 TMDB 上半场**（[:1165-1198](backend/services/actor_profile_service.py#L1165-L1198)，始终执行）。
2. **webhook 只审计单集**：`_handle_library_new_for_sinicize`（[emby.py:776-830](backend/routers/emby.py#L776-L830)）对 Episode 只走单集审计 + sinicize，从不刷新父 Series `recursive_item_count` 也不全量同步分集。
3. **「30 vs 12」根因**：[sync_actions.py:1892-1929](backend/routers/sync_actions.py#L1892-L1929) 的 BatchAudit 摘要用 TMDB 整季 `len(episodes)`（30）喂用户可见消息，而 Phase 1 的 `episodes_processed`（11）才是 Emby 实际分集。`recursive_item_count` 来自 Emby `RecursiveItemCount`（含季数且易 stale）。
4. **无空集检测**：Emby→DB 同步流没有「应有但缺失」逻辑；CD2 文件系统检查（checker_service）是另一套，不与 Emby/DB 联动。
5. **前端按钮根因**：[ActorLocalizationStudio.vue:243](frontend/src/components/ActorLocalizationStudio.vue#L243) `status === 'pending'` 严格过滤；后端 `rec.get("status","pending")`（[emby.py:633](backend/routers/emby.py#L633)/[:678](backend/routers/emby.py#L678)）对 NULL 列返回 None。MediaSyncStatus.status 实际取值 `synced` / `pending` / `failed` / NULL。
6. **translation_cache / translation_utils 已接入**：今天的缓存重构已落地并活跃使用（lookup_actor_name / lookup_role_name / upsert_actor_translation 在 douban_service 全链路调用），本次不重复。
7. **豆瓣限流现状**：DoubanApi 全局 1.5s 冷却（类级锁）+ `_frodo_get` 随机 0.3-0.8s 睡眠 + `_http_get` 0.5-1.5s 睡眠；**无按系列/批次请求上限**。
8. **MySQL 迁移障碍**：`_run_migrations` 用 4 处裸 `PRAGMA table_info()`（SQLite 专属）；`check_same_thread` 连接参数 SQLite 专属；多列 `Column(String)` 无长度（MySQL 索引列必填）；DB URL 硬编码无 env 覆盖。

## 总体架构与阶段划分

| 阶段 | 覆盖问题 | 改动域 |
|------|---------|--------|
| P1 集数同步与计数正确性 | 3 + 5 | `emby.py` / `sync_actions.py` / `douban_service.py` / `db_crud.py` |
| P2 前端交互修正 | 4 | `ActorLocalizationStudio.vue` / `emby.py` |
| P3 豆瓣请求治理 | 1 | `actor_profile_service.py` / `douban_service.py` / 新增 `request_budget.py` |
| P4 MySQL 迁移 | 2 | `database.py` / `models.py` / `config/settings.py` / 新增直迁脚本 |

依赖关系：P1 先定「什么会触发汉化」，P3 的请求预算才有依据；P4 基础设施放最后，避免迁移期与功能改动风险窗口重叠。各阶段改动边界独立，可分别回归。

---

## Phase 1 — 集数同步与计数正确性（问题 3 + 5）

### 1a. Webhook → 父 Series 轻量对账（修复「12 集只入库 7 集」）

改动点：[emby.py:776-830](backend/routers/emby.py#L776-L830) `_handle_library_new_for_sinicize` Episode 分支。

```
Episode library.new
  → 取出 series_id（payload.ParentId / SeriesId）
  → reconcile_series_episodes(series_id)   # 新增服务函数
       1. 拉 Emby 全部分集列表（轻量字段：Id / IndexNumber / ParentIndexNumber）
          —— 复用 sync_actions.py:1517 _fetch_episodes_light
       2. 与 DB 中 MediaMetadata(parent_id=series_id) 对比 → 得出：新增集 + 内部空集缺口
       3. 分支：
          - 存在【内部空集缺口】→ 一次性全量同步（_process_episodes 已存在，一次遍历补齐）
            → 触发系列级汉化 DoubanSinizer.sinicize(series_id)（整体汉化，按笔记原话）
          - 仅【尾部新增集】、无内部缺口 → 仅同步新增集到 DB + 对该新增集走父系列汉化
            + 刷新计数，不重扫全剧（在播剧逐集触发时的成本上限）
       4. 用实际 Emby 分集数刷新父 Series 的 recursive_item_count（不再信任 stale 字段）
```

**请求成本**：对账只用 1 次轻量 Emby 分集拉取；无缺口时不做全量同步。已汉化剧重触发时，系列汉化靠缓存（`douban_match_map`、演员名缓存、仅变更集才回写）控制成本。

### 1b. 单集汉化一律走父 Series（修复「第 26 集」搜索失败）

改动点：[douban_service.py:132-624](backend/services/douban_service.py#L132-L624) `sinicize` 顶部。

- 检测 `item_type == "Episode"` → 不再对集名执行 `_find_douban_id`（Frodo search 不索引单集页，必然失败）。
- 定位父 Series：该集 Emby 数据 `SeriesId`（或 DB `parent_id`）→ `_get_emby_item(series_id)`。
- 父 Series 豆瓣 ID：优先查 DB `MediaSyncStatus.douban_id`；没有则做一次系列级查找并回写缓存。
- 用系列豆瓣 cast 构建 `douban_match_map`，走现有 `_localize_episode_people` 本地化该分集演员。
- 效果：单集汉化 = 迷你系列汉化，复用全部现有机制。父系列查找失败 → 降级 AI-only 本地化该集（现有兜底），不崩溃。

### 1c. 「30 vs 12」计数修复（两处根因）

**根因 A — BatchAudit 摘要用 TMDB 数**（[sync_actions.py:1892-1929](backend/routers/sync_actions.py#L1892-L1929)）：
- 新增 `total_eps_actual` 计数器：以 Phase 1 实际同步入库的分集数（`episodes_processed`）为准。
- 最终摘要 `分集 {total_eps_actual} 集`；当 `total_eps_enriched`（TMDB）不同时显示 `{actual}/{tmdb} 集（实际/TMDB）`。
- 单季日志 `《%s》S%02d 完成: %d 集` 的 `%d` 改为实际数，TMDB 数放括号参考。

**根因 B — 前端用 stale 的 `recursive_item_count`**（[ActorLocalizationStudio.vue:916](frontend/src/components/ActorLocalizationStudio.vue#L916)）：
- `save_media_to_db` 对 Series 的 `recursive_item_count` 改为由分集列表实算（当前取 Emby `RecursiveItemCount`，含季数且可能 stale）。
- 前端详情默认显示实际入库分集数 `detailsData.episodes.length`，`recursive_item_count` 仅作回退。

### 1d. 空集补齐

缺口对比源是 **Emby 实际分集列表**（Emby 有什么，DB 应有什么），不拿 TMDB 期望数当标尺，避免在播剧「永远有缺口」的误报。发现缺口时 `_process_episodes` 补齐缺失分集 DB 记录，随后系列级汉化为这些分集做演员本地化。

---

## Phase 2 — 前端交互修正（问题 4）

改动点 1：[ActorLocalizationStudio.vue:243](frontend/src/components/ActorLocalizationStudio.vue#L243)，过滤条件对齐后端语义：

```js
// 改前：严格 pending，failed/NULL 被排除 → 按钮永不显示「同步选中项」
const pendingCheckedIds = computed(() =>
  items.value.filter(i => i.checked && i.status === 'pending').map(i => i.id))
// 改后：status !== 'synced' 即算待汉化（含 failed / NULL / 未审计）
const pendingCheckedIds = computed(() =>
  items.value.filter(i => i.checked && i.status !== 'synced').map(i => i.id))
```

改动点 2：[emby.py:633](backend/routers/emby.py#L633) / [:678](backend/routers/emby.py#L678)，NULL 归一化：

```js
it["sync_status"] = rec.get("status", "pending")  →  rec.get("status") or "pending"
```

> 说明：后端 `sinicize_selected` 语义本身正确（传什么 ID 处理什么），本次不改后端行为。已汉化(synced)条目想强制重跑仍走「强制汉化(覆盖)」。

---

## Phase 3 — 豆瓣请求治理（问题 1）

### 3a. 轻量头像路径

改动点：[actor_profile_service.py:1165-1198](backend/services/actor_profile_service.py#L1165-L1198) 上半场 TMDB 提取。

- `resolve_actor_profile` 增加 `light_mode: bool = False` 参数；`light_mode=True` 时**跳过整个 TMDB 上半场**（每演员 0-2 次请求的大头）。
- 轻量路径只走：L0 本地 → L0.5 Emby 原生 → L1 复用豆瓣演员表直链（零新请求）→ L2 仅提升已缓存头像。
- 找不到头像 → 依赖已有 7 天无头像冷却（[:932-953](backend/services/actor_profile_service.py#L932-L953)），后续自动不重打。
- 调用方：`douban_service.py:489` 的 `ensure_profiles_for_people` 传 `light_mode=True`；**演员库**（ActorLibrary refresh / 强制刷新）保持 `light_mode=False` 走完整漏斗——头像后补走演员库，正是分工本意。

### 3b. 系列级豆瓣 cast 缓存（逼近「一部剧尽可能只发一次请求」）

现状：每次系列汉化都重拉 `_fetch_douban_actors`（2 次豆瓣请求），已汉化剧重触发时浪费。

- 新增：`MediaSyncStatus.douban_cast_cache`（JSON 列，存该系列 douban cast：`name → {avatar, douban_id, role}`）。
- 系列汉化时：cast 缓存新鲜（< 7 天）→ 直接复用，**0 次豆瓣请求**；过期/缺失 → 拉一次并回写。
- 与 `translation_cache`（演员名/角色名）互补：前者缓「名字」，这里缓「cast 清单 + 头像直链」。

### 3c. 批次请求预算（新增 `services/request_budget.py`）

轻量的进程级每 Provider 令牌桶（不重写现有冷却，只在其上加强制上限）：

- config.json 新节 `request_budget`：`douban_per_series`（默认 30）、`tmdb_per_min`（默认 60）、`emby_writeback_per_series`（默认 50）。
- 接入点：`DoubanApi.__invoke/__post`、actor_profile_service TMDB 抓取、`_write_back_episode`。
- 超限策略：**排队等待**（有超时，如 30s）→ 仍超时则**跳过并记日志**，不把一次 webhook 触发的系列汉化变成打爆 Provider 的元凶。

### 请求量前后对比（一部已汉化的 28 人剧集重触发）

| 来源 | 现状 | 改后 |
|------|------|------|
| 豆瓣 | 2-4（重拉 cast） | **0-2**（cast 缓存命中即 0） |
| TMDB | 0-56（每演员上半场） | **0**（轻量头像） |
| Emby | 系列 GET + 分集拉取 + 变更回写 | 不变（本就必要） |
| AI | 0-3 | 不变（仅未命中时） |

---

## Phase 4 — MySQL 迁移（问题 2，纯切换）

**目标**：192.168.31.135:3008（root/root）`media-ai` 库，一次性直迁，切流后生产弃用 SQLite。

### 4a. 方言兼容的迁移层
- `_run_migrations` 中 4 处裸 `PRAGMA table_info()`（[database.py:42](backend/database.py#L42) / :56 / :116 / :135）全部替换为 SQLAlchemy `inspect(engine).get_columns()` —— 方言无关。
- 移除 `connect_args={"check_same_thread": False}`，按 `engine.url.get_backend_name()` 区分方言传参。
- 保留 `ALTER TABLE ... ADD COLUMN`（MySQL 兼容）。

### 4b. 连接配置
- DB URL 解析顺序：环境变量 `DATABASE_URL` → config.json `database_url` → 默认 SQLite 路径。
- 生产部署设 `DATABASE_URL` 指向 MySQL；测试仍可 `sqlite:///:memory:`（inspect 层兼容，两者都跑得通）。

### 4c. models.py 补长度
- MySQL 要求索引列 `VARCHAR(n)` 显式长度。给全量 `Column(String)` 补长度（主键/索引列必须，如 `emby_item_id`、`ActorProfile.name`、`role` 等，统一 `String(255)`）。
- JSON 列（`MediaTag.tags`、`TaskActionLog.detail`、`AutoTaskFlow.context`、`WashHistory.wash_params`、`ScanRunLog.details`）MySQL 原生 JSON，SQLAlchemy 透明处理。

### 4d. 一次性直迁脚本（新增 `backend/scripts/migrate_sqlite_to_mysql.py`）
- **保留显式主键**：`media_metadata.emby_item_id`、`ActorProfile.id` 等原样搬入，保证 `parent_id`、actor 关联不漂移。
- **utf8mb4**：`CREATE DATABASE media-ai CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`，连接串带 `charset=utf8mb4`，避免中文乱码。
- 幂等 + `--dry-run`（先打印每表行数对比，不写库）。
- 迁移执行序：建库 → `_run_migrations` 建 MySQL 表 → 直迁 → 切 `DATABASE_URL` 重启 → 行数对比验证 → 保留 SQLite 备份文件至验证通过。

---

## 跨阶段：错误处理 / 测试 / 风险

### 错误处理
- webhook 对账：沿袭现有「逐集 try/except + 独立 commit」模式，单集失败不阻塞 webhook 200。
- 请求预算：超时→跳过+记日志，绝不让一次系列汉化打爆 Provider。
- MySQL 连接失败：启动即报错并给出明确提示（纯切换，不静默回退 SQLite；开发可用 env 指回 sqlite）。
- 单集走父系列：父系列豆瓣查找失败 → 降级为 AI-only 本地化该集（现有兜底），不崩溃。

### 测试
- **P1**：`reconcile_series_episodes` 缺口检测单测（mock Emby 分集列表 vs DB 行）；BatchAudit 摘要断言用实际数；单集汉化断言委派父系列。
- **P2**：`cd frontend && npx vue-tsc --build` + 浏览器手工勾选验证按钮文案。
- **P3**：`request_budget` 令牌桶单测（超限排队/跳过）；light_mode 断言跳过 TMDB；cast 缓存命中/过期单测。
- **P4**：直迁脚本对 SQLite 副本 `--dry-run`；迁移层 sqlite :memory: 单测仍过；MySQL 集成测试可选门控。

### 风险与缓解
1. **在播剧逐集 webhook 触发全量汉化偏重** → 精化触发策略（见 1a 分支）：仅有内部空集才全量同步+整体汉化；仅尾部新增集时只补库+对新增集走父系列汉化+刷新计数。
2. **MySQL 中文乱码** → utf8mb4 全链路显式（建库 + 连接串）。
3. **迁移期间 SQLite 数据持续增长** → 直迁脚本幂等，重启前随时可重跑。
4. **单集直接汉化场景减少** → P1 的父系列对账本就覆盖该入口。

---

## 实施顺序与依赖

P1 → P2 → P3 → P4（方案 A，已确认）。每阶段完成后可独立发布与回归；P3 的请求预算依赖 P1 确定的「汉化触发面」，P4 基础设施放最后避免与功能改动风险窗口重叠。

## 开放问题

无（已全部澄清）。

## 相关文件

- `backend/routers/emby.py`（webhook 对账入口 / sync_status 归一化）
- `backend/routers/sync_actions.py`（BatchAudit 计数 / 分集同步）
- `backend/services/douban_service.py`（单集走父系列 / cast 缓存 / light_mode 接线）
- `backend/services/actor_profile_service.py`（light_mode / 轻量头像）
- `backend/services/request_budget.py`（新增，请求预算）
- `backend/database.py` + `backend/models.py`（MySQL 方言兼容 + String 长度）
- `backend/scripts/migrate_sqlite_to_mysql.py`（新增，一次性直迁）
- `backend/config/settings.py`（database_url / request_budget 配置）
- `frontend/src/components/ActorLocalizationStudio.vue`（按钮语义修复）
- 前置依赖：`docs/superpowers/plans/2026-08-03-translation-cache-refactor.md`（已落地，本次复用其缓存设施）
