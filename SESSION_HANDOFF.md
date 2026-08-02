# Emby-AI-Manager 会话交接文档 (Session Handoff)

**项目背景**：本项目是一个针对 Emby 媒体库的演职员中文化治理工具。采用前后端分离架构（Vue 3 + Python FastAPI + SQLite）。核心目标是实现 Emby 演职员数据的深度刮削、基于 TMDB ID 等绝对锚点的跨库灾备恢复、以及通过 LLM 实现的智能角色补全。

**最后更新**: 2026-07-19 (Session 3-5)

---

## 0. 本次会话完成的重构（2026-07-19 Session 3-5）

### 0.8 防弹级错误兜底修复 — ★ 核心稳定性

#### 0.8.1 task_manager.py 增强 (utils/task_manager.py)

`update_progress()` 新增 `status` 参数，支持运行中途将任务状态标记为 `"error"`，前端立即感知异常。

#### 0.8.2 _batch_sinicize_task 终极防线 (routers/sync_actions.py)

**问题**：`_batch_sinicize_task` 没有外层 `try...except...finally`，循环中任何未捕获异常直接导致 `complete_task` 永不调用 → 前端永久轮询死锁。

**修复**：
```python
try:
    sinizer = DoubanSinizer()
    for ... in item_ids:       # 逐项 try/except 已存在
        ...
except Exception as e:         # ★ 终极防线
    logger.error(完整堆栈)
    update_progress(status="error")
finally:
    complete_task(...)          # ★ 无论如何强制终结任务
```

#### 0.8.3 DoubanSinizer 分集循环逐集隔离 (services/douban_service.py)

三重加固：

| 加固层 | 机制 |
|--------|------|
| 进度反馈 | `task_id` 传入 `sinicize()`，分集循环内调 `task_manager.update_progress(message="正在高速回写分集: xxx (5/50)")` |
| 逐集隔离 | `for ep in episodes` 内部 `try/except` → 单集脏数据 rollback → `continue` 下一集 |
| 逐集提交 | `ep_db.commit()` 每集一次 → 已成功的分集不因后续失败而丢失 |
| 外层兜底 | Session 创建失败等基础设施异常仍被捕获 |

#### 0.8.4 前端三种轮询全部增加异常状态阻断 (ActorLocalizationStudio.vue)

审计/汉化/富化三种进度轮询统一增加 `failed` 状态匹配：
```javascript
} else if (s.status === 'error' || s.status === 'failed') {
    stopPolling()
    ElMessage.error(`任务异常终止: ${s.message || '未知错误'}`)
    dialogVisible.value = false    // ★ 立即释放弹窗
    await loadItems()              // ★ 刷新列表
}
```

---

### 0.9 DoubanCelebrityId 数据断链修复 — ★ 打通全链路

**问题**：汉化映射引擎匹配 Emby 演员与豆瓣演员时，只注入了 `DoubanAvatarUrl`，未注入 `DoubanCelebrityId`。导致绝大部分演员降级到 L2 TMDB 搜索而非 L1 豆瓣精准查询。

**修复**：11 个注入点，覆盖从 Frodo API → 映射引擎 → 分集中文化 → L1 漏斗的全链路：

| # | 位置 | 变更 |
|---|------|------|
| 1 | `_fetch_actors_frodo()` | ★ 新增提取 `item.get("id")` |
| 2 | `_parse_celebrity_item()` | ★ HTML 降级从 `<a href="/celebrity/1234567/">` 提取 ID |
| 3 | `_match_and_update()` 匹配 | 注入 `DoubanCelebrityId` |
| 4 | `_match_and_update()` 新增 | 注入 `DoubanCelebrityId` + `DoubanAvatarUrl` |
| 5 | `_build_douban_match_map()` | 新增 `douban_id` 字段 |
| 6 | `sinicize()` 分集前置批处理 | 从 match_map 注入 `DoubanCelebrityId` |
| 7 | `_localize_episode_people()` 直匹配 | 注入 `DoubanCelebrityId` |
| 8 | `_localize_episode_people()` AI复应用 | 注入 `DoubanCelebrityId` |
| 9 | sync_actions `_build_douban_actor_map()` | 新增 `douban_id` 字段 |
| 10 | sync_actions `_localize_episode_people()` 直匹配 | 注入 `DoubanCelebrityId` |
| 11 | sync_actions `_localize_episode_people()` AI复应用 | 注入 `DoubanCelebrityId` |

数据流：
```
Frodo API (celebrity.id) → _match_and_update / _build_douban_match_map
  → sinicize() / _localize_episode_people()
  → save_media_to_db() → ensure_profiles_for_people()
  → resolve_actor_profile(ctx={"douban_id": "1234567"})
  → L1 豆瓣漏斗: DoubanApi.celebrity_details(douban_id)
```

---

### 0.10 L0.5: Emby 原生头像优先 + 即时试探 + 极速熔断 — ★ 新增漏斗层

#### 0.10.1 核心逻辑 (services/actor_profile_service.py)

在 L0（本地缓存）之后、L1（豆瓣）之前，新增 **L0.5** 层，受配置开关 `enable_emby_avatar_first` 控制。

完整漏斗优先级：
```
L0   — DB 极速查询 → 物理硬盘嗅探 → 冷却期拦截
L0.5 — Emby 原生头像 (enable_emby_avatar_first=true 且数据齐全时)
        即时 _download_image 试探 → 成功则阻断 L1/L2，失败则平滑降级
L1   — 豆瓣外链 (上下文自带 或 douban_id 主动 API)
L2   — TMDB 搜索 + 详情
```

#### 0.10.2 即时网络试探 (Eager Download)

L0.5 不是仅设置 `download_url` 然后等底部统一下载，而是**当场调用 `_download_image`**：
- 成功 → 同时赋值 `download_url` + `local_path`，L1/L2 被 `if not download_url:` 阻断
- 失败（跨网段不可达/无缓存）→ `download_url` 保持 `""`，平滑降级到 L1/L2
- 底部统一下载段改为 `if download_url and not local_path:` 避免重复下载

#### 0.10.3 极速熔断 (Fast-Fail)

`_download_image` 新增 `connect_timeout` 和 `read_timeout` 参数（默认 10s/30s）。
L0.5 调用时传入 `connect_timeout=2.0, read_timeout=3.0`：
- Emby 是本地/近端服务，超过 3 秒拿不到图一律熔断
- L1/L2 外网来源不传参数，使用默认 (10s/30s)
- L0.5 额外包裹 `try/except Exception`，任何异常都降级不放行

#### 0.10.4 上下文注入 (ensure_profiles_for_people)

```python
ctx = {
    ...
    "emby_person_id": p.get("Id"),
    "emby_image_tag": p.get("PrimaryImageTag") or (
        p.get("ImageTags", {}).get("Primary") if isinstance(...) else None
    ),
}
```

#### 0.10.5 前端开关 (EmbySettings.vue)

"智能服务"分区新增 `el-switch`：
- `enable_emby_avatar_first: false`（默认关闭）
- 开关旁实时显示状态文字
- 下方提示适用场景（TMDB 代理不稳定/503）

#### 0.10.6 TMDB 函数异常双保险

三条 TMDB 函数 (`_search_tmdb_person`, `_fetch_person_by_tmdb_id`, `fetch_tmdb_person_details`) 均增加显式 `except requests.exceptions.RequestException` 处理器：
```
except RequestException → 网络层 (ProxyError/ConnectionError/SSLError/Timeout) → 打印类型名 + 简短消息
except Exception        → 非网络层 (JSON 解析失败等) → 完整堆栈
```

---

## 1. 核心系统架构

### 1.1 全维度演员数据中心 (ActorProfile)

- **`actor_profiles` 表**（[models.py](backend/models.py#L189-L207)）：以演员中文名 `name` 为主键，存储全维度生平数据：
  - `local_image_path`：本地头像相对路径（正斜杠格式，如 `张/张译-tmdb-12345/folder.png`）
  - `image_url`：外部直链兜底（豆瓣/TMDB/Emby）
  - `source`：数据来源（douban / tmdb / emby / local）
  - `tmdb_id`、`imdb_id`、`douban_id`：跨平台 ID 锚点
  - `birth_date`、`birth_place`、`overview`：生平数据

- **数据库规范化**：`actor_records` 已瘦身为纯关联表（[models.py](backend/models.py#L210-L226)），仅保留 `emby_item_id`、`name`、`role`、`type`、`sort_order`、`update_time`。已彻底删除废弃的 `image_url` 列及其所有业务引用。

### 1.2 超级漏斗头像解析 (actor_profile_service.py) — ★ 已重构

[backend/services/actor_profile_service.py](backend/services/actor_profile_service.py) — 核心入口 `resolve_actor_profile(name, db, context_info)`：

**L0 终极极速本地缓存拦截（数据库优先 → 物理硬盘兜底）**：

| 步骤 | 策略 | 说明 |
|------|------|------|
| **1. DB 极速查询** | 主键索引查 `actor_profiles` | O(1) SQLite 查找 |
| **2. 最快命中** | DB 有记录 + `local_image_path` + 物理文件存在 | 直接返回，零 I/O、零网络 |
| **3. 硬盘嗅探兜底** | `_find_local_avatar()` 扫描 `people/{首字}/` | 兼容用户手动放入文件 |
| **4. 冷却期拦截 ★** | `existing.update_time` 距当前 < 7 天 → 直接返回 | 无头像演员 7 天内不重复网络请求 |
| **5. 冷却期已过** | 超过 7 天 → 允许重试 L0.5/L1/L2 | 给头像源足够时间更新 |

**★ L0.5 Emby 原生头像优先（新增，受配置开关控制）**：
- 读取 `ctx["emby_person_id"]` + `ctx["emby_image_tag"]` → 拼接 Emby 直链
- **即时网络试探**：当场调 `_download_image(connect=2.0s, read=3.0s)` 下载
- 成功 → 阻断 L1/L2，source="emby"
- 失败/超时/异常 → 平滑降级到 L1/L2，不产生坏数据
- 受前端 `enable_emby_avatar_first` 开关控制，默认关闭

**L1 豆瓣漏斗（★ 已增强）**：
| 条件 | 行为 |
|------|------|
| `douban_avatar` 上下文自带 | 直接使用，零额外网络 |
| `douban_id` 存在 | **主动调用** `DoubanApi().celebrity_details(douban_id)` → 提取高清头像 + 生平/出生地兜底 |

**L2 TMDB 兜底**：精准 ID 拦截 → 名字搜索（智能优选三级梯队）→ Get Details + external_ids

**关键技术点**：
- **L0 本地嗅探**：`_find_local_avatar(actor_name)` 缓存结果到 `_local_sniff_cache`
- **标准化落盘**：`_build_standard_path()` — 有 TMDB ID 时目录名为 `{name}-tmdb-{id}`，仅豆瓣 ID 时为 `{name}-douban-{id}`，无 ID 时为 `{name}`
- **三级缓存**：`_local_sniff_cache`（L0 嗅探）、`_tmdb_search_cache`（搜索）、`_tmdb_detail_cache`（详情）
- **防盗链修复**：`_download_image()` 强制 `Referer: https://movie.douban.com/`
- **可配置超时**：`_download_image(url, save_path, connect_timeout=10.0, read_timeout=30.0)`
- **极速熔断**：L0.5 调用 `_download_image(connect=2.0, read=3.0)`，Emby 卡死最多等 5 秒
- **TMDB 异常双保险**：三条 TMDB 函数均显式捕获 `RequestException` + 通用 `Exception`

### 1.3 静态资源挂载与防盗链

- **`main.py`**（[main.py](backend/main.py#L112-L118)）：`people/` 通过绝对路径挂载到 `/static_actors`
- **前端修复**：`<img>` 标签使用 `:src="act.local_image_url || act.image_url"` + `referrerpolicy="no-referrer"` 彻底绕过豆瓣 403
- **API 拼接**：`GET /api/media/{item_id}/details` 通过 `Request.base_url` 动态拼接完整的 `local_image_url`

### 1.4 豆瓣 Frodo API 集成 (douban_api.py + douban_service.py)

- **DoubanApi 客户端**（[douban_api.py](backend/services/douban_api.py)）：完整的 Frodo API v2 封装 — HMAC-SHA1 签名、冷却限流、Session 复用
  - `celebrity_details(id)` — 演员详情（头像 + 生平 + 出生地）
  - `match_info(name, imdbid, mtype, year)` — 智能匹配豆瓣条目 ID
- **豆瓣演员 ID 全链路**（★ 已修复数据断链）：
  - `_fetch_actors_frodo()` 提取 `item["id"]` → `_match_and_update()` 注入 `DoubanCelebrityId`
  - `_build_douban_match_map()` 传递 `douban_id` → 分集前置批处理 + `_localize_episode_people()` 全量注入
  - 最终流入 `resolve_actor_profile(ctx={"douban_id": ...})` → L1 豆瓣精准查询

### 1.5 分集数据透视 (Episode Data Perspective)

- **后端**：`GET /api/media/{item_id}/details`（[sync_actions.py](backend/routers/sync_actions.py#L818-L920)）
  - 通过 `name` 批量 JOIN `actor_profiles`（一次 `IN` 查询）
  - 返回 `local_image_url`（优先）、`image_url`（兜底）、生平数据
- **前端**：`ActorLocalizationStudio.vue` — Series 卡片底部"分集透视"按钮 → 600px Drawer

### 1.6 分集批量富化引擎 (Batch Enrich)

- **`GET /api/tasks/{task_id}`**：前端轮询后台任务进度
- **`POST /api/episodes/batch-enrich`**：接收 `{"item_id": "series_id"}`，立即返回 `task_id`
- **后台引擎核心流程**：TMDB 整季 API 一次请求拿到全季数据 → guest_stars 去重 → `ensure_profiles_for_people` 批量漏斗

### 1.7 任务状态管理器 (task_manager.py) — ★ 已增强

- `create_task(total, message, metadata)` → `task_id`（UUID hex 12位）
- `update_progress(task_id, current, message, increment, total, status)` — ★ 新增 `status` 参数
- `complete_task(task_id, message, success)` → 自动将 `current` 对齐到 `total`
- `get_status(task_id)` → `{status, total, current, message, metadata}` | None
- `cleanup_expired()` → 已完成任务 10 分钟后自动清理
- 所有字典操作均受 `threading.Lock()` 保护

### 1.8 ★ 统一批量审计引擎 (POST /api/audit/batch)

- TMDB Season API 整季批处理，绝对禁止逐集循环查询
- sentinel 模式 + `finally` 块保证 `complete_task` 一定被调用
- Phase 1 — 逐项状态检查 + UPSERT 入库
- Phase 2 — 整季 TMDB 批处理（按季推进进度）

### 1.9 ★ 统一批量汉化引擎 (POST /api/douban/sinicize_selected + sinicize_all)

- `_batch_sinicize_task`：外层 `try/except/finally` 全局防线，保证任务永不被悬挂
- `sinicize()` 分集循环：逐集 try/except 隔离 + 颗粒度进度反馈 + 逐集提交
- 前端三种进度轮询统一 error/failed 异常状态阻断 + 强制关闭弹窗

### 1.10 前端：防锁死弹窗 + 异步审计 — ★ 已修复

- 对话框 `:show-close="true"` + 常驻关闭按钮，彻底消除用户锁死
- 三种轮询（审计/汉化/富化）统一增加 `failed` 状态即时关闭弹窗 + 刷新列表
- 轮询 API 异常时立即停止并释放弹窗

### 1.11 入库流程 (db_crud.py)

`save_media_to_db` — 新增 `skip_profiles: bool = False` 参数：
1. `skip_profiles=False`（默认）：先调 `ensure_profiles_for_people(db, people)` → 触发超级漏斗
2. `skip_profiles=True`：跳过漏斗（调用方已提前批量处理），仅建立 `actor_records` 关联

---

## 2. people/ 目录结构标准

```
people/
  张/
    张译-tmdb-12345/
      folder.png     ← 演员头像
    张颂文-tmdb-67890/
      folder.png
  宋/
    宋威龙-tmdb-112233/
      folder.png
```

**目录命名规则**：
- 有 TMDB ID：`{actor_name}-tmdb-{tmdb_id}`
- 仅豆瓣 ID：`{actor_name}-douban-{douban_id}`
- 无任何 ID：`{actor_name}`

**挂载**：`app.mount("/static_actors", StaticFiles(directory=PEOPLE_DIR))`

---

## 3. 关键文件索引

| 文件 | 用途 |
|------|------|
| [backend/models.py](backend/models.py) | ActorProfile + ActorRecord + MediaSyncStatus + MediaMetadata |
| [backend/services/actor_profile_service.py](backend/services/actor_profile_service.py) | ★ 超级漏斗：L0(DB/硬盘) → L0.5(Emby 即时试探+熔断) → L1(豆瓣+主动API) → L2(TMDB) |
| [backend/services/actor_image_service.py](backend/services/actor_image_service.py) | 双源漏斗 URL 解析（已移除 Emby L3，仅保留豆瓣 + TMDB） |
| [backend/services/douban_api.py](backend/services/douban_api.py) | DoubanApi Frodo API 客户端（HMAC-SHA1 签名、冷却限流） |
| [backend/services/db_crud.py](backend/services/db_crud.py) | save_media_to_db — 入库 + 可选 Profile 预处理（skip_profiles） |
| [backend/services/douban_service.py](backend/services/douban_service.py) | DoubanSinizer — ★ 豆瓣演员 ID 全链路 + 分集前置去重批处理 + 逐集隔离 |
| [backend/routers/sync_actions.py](backend/routers/sync_actions.py) | ★ GET /details + POST /api/audit/batch + POST /api/douban/sinicize_* + ★ try/except/finally 全局防线 |
| [backend/utils/task_manager.py](backend/utils/task_manager.py) | ★ 线程安全 TaskManager 单例，支持动态 total + status + 自动对齐 current |
| [backend/main.py](backend/main.py) | FastAPI 入口 + /static_actors 挂载 |
| [backend/database.py](backend/database.py) | SQLite 连接 + 增量迁移 |
| [frontend/src/components/ActorLocalizationStudio.vue](frontend/src/components/ActorLocalizationStudio.vue) | ★ 演职员治理页面 + 分集透视 Drawer + 三重异步进度弹窗 + ★ 异常状态阻断 |
| [frontend/src/components/EmbySettings.vue](frontend/src/components/EmbySettings.vue) | ★ Emby 连接设置 + L0.5 enable_emby_avatar_first 开关 |

---

## 4. 环境与配置

- **后端**: Python FastAPI (port 8000)，SQLite 数据库位于 `backend/data/emby_ai.db`
- **前端**: Vue 3 + Element Plus + Vite
- **关键配置项** (`config.json`)：
  - `emby_host`、`emby_api_key`、`emby_user_id`
  - `tmdb_api_key`、`tmdb_base_url`
  - `sf_api_key` (AI 翻译)
  - `max_actors_per_media` (默认 50)
  - `enable_emby_avatar_first` (★ 新增，默认 false)
  - `cd2_media_dir`、`cd2_organized_dir`
  - `emby_prefix`、`cd2_media_prefix`
- **外部依赖**: httpx (0.28.1)、pypinyin、BeautifulSoup4、requests

---

## 5. Session 1-2 重构记录（2026-07-19 上午/中午）

参见 git history 和之前的 SESSION_HANDOFF 版本。核心包括：
- DoubanApi 正式接入
- 图片下载器防盗链修复
- L1 豆瓣漏斗主动抓取
- skip_profiles 参数
- 汉化引擎分集前置去重批处理
- 统一批量汉化后台 API
- 豆瓣 ID 查找引擎重构（废弃 HTML 爬虫）
- 前端按钮异步化

---

## 6. 下一步开发方向

### 6.1 前端增强
- sinicize 进度弹窗增加失败项明细展示（如"3/5 成功，2 失败：id1、id2"）
- 分集批量富化按钮增加权限校验（仅已审计且 status=synced 的 Series 可触发）

### 6.2 性能优化
- `_find_local_avatar()` 策略 2 的 `os.listdir(_PEOPLE_DIR)` 可缓存目录列表
- TMDB API 请求考虑加入限流（rate limiter），避免触发 TMDB 429
- DoubanApi 可考虑在 DoubanSinizer 中复用同一实例（避免重复初始化 Session）
- L0.5 成功后可缓存 Emby URL 到 actor_profiles 避免重复试探

### 6.3 功能扩展
- 分集批量富化增加 AI 翻译 Overview 的选项（调用已有的 `ai_translator`）
- 支持按季选择（目前全季处理），允许用户指定特定季号
- `_batch_sinicize_task` 可考虑并行处理多个 item（当前逐项串行）

### 6.4 已废弃/待清理
- `actor_images/` 目录 — 旧的头像存储路径，确认迁移完成后可删除
- `actor_image_service.py` — 已被 `actor_profile_service.py` 完全取代，可评估是否彻底移除
- `DOUBAN_FRODO_*` 常量 — `_frodo_get` 中仍在使用，后续可考虑迁移至 `DoubanApi` 统一管理
