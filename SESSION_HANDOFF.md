# Session Handoff — 2026-07-08 (Session 5 + 6)

## 当前目标 (Current Goal)

1. **CD2 根路径可配置化**：媒体库/已完结的根路径从硬编码改为通过配置页面设置
2. **残缺季雷达**：在年节目录下扫描所有剧集，自动检测集数不完整的 Season 文件夹
3. **完整性判定修复**：文件数 ≥ 预期集数应为"完整"，而非精确匹配
4. **Emby 僵尸清理**：CD2 删除后，Emby 因缩略图错误无法正常清理残留记录的问题
5. **路径转换可配置**：Emby ↔ CD2 路径互转的前缀从硬编码改为可配置
6. **✅ 综艺自动洗版特殊逻辑**（已完成）：只处理已完结中存在的 Season，Season 0 忽略

---

## 已完成工作 (What's Done)

### 1. CD2 根路径可配置化

#### 前端 (`EmbySettings.vue`)
- 新增「CD2 网盘设置」区块（Emby 连接设置与智能服务之间）
- 两个输入框：`cd2_media_dir`（左侧根路径）、`cd2_organized_dir`（右侧根路径）
- 新增「Emby 路径前缀」和「CD2 路径前缀」输入框（路径转换用）

#### 前端 (`TorrentCleanup.vue`)
- 硬编码常量 `CD2_MEDIA_BASE` / `CD2_ORGANIZED_BASE` → `ref()`
- `onMounted` 中先请求 `/api/config` 获取用户配置的路径，加载失败时 fallback 到默认值

### 2. 残缺季雷达（全栈）

#### 后端 (`backend/services/checker_service.py` — 新建)
- 复用现有组件保持 DRY：
  - `_SEASON_RE` (task_flow_service) — 提取季号
  - `_count_files_in_cd2_dir` (task_flow_service) — 统计视频文件
  - `get_tv_season_info` (organize_service) — TMDB 预期集数
  - `extract_tmdb_id_from_path` (path_utils) — 提取 TMDB ID
- 处理多版本同季号（如 `Season 1 - 4K` 和 `Season 1 - WEB` 各自独立比对）
- 温和限速 (`time.sleep(0.3)`) 避免 CD2 压力
- **🆕 Session 5**: 新增 `check_single_show()` 函数 — 单剧集核查，复用相同的 Season 检查逻辑

#### 后端 API (`cd2_router.py`)
- `POST /api/directories/check-incomplete` — 批量扫描整个年份目录
- **🆕 Session 5**: `POST /api/directories/check-show-incomplete` — 单剧集快速核查

#### 前端 UI (`TorrentCleanup.vue` — 内联标注版)
- **按钮**：左右两列 nav bar 中，仅在年份层级（depth=1）显示 `[🔍 核查残缺季]`
- **🆕 Session 5**: 每个剧集行尾新增 🔍 按钮（hover 可见），支持单剧快速核查
- **剧集级标注**：扫描完成后在目录列表项上显示 `✅ 完整`（绿色）/ `⚠️ 缺 N 季`（橙色）
- **Season 级标注**：点进剧集后，Season 文件夹显示 `8/10 缺 2 集`（红色 badge，隐藏原始 stats）
- **行高亮**：残缺剧集橙色左边框 + 浅橙背景
- **清除标注**：`✕ 清除标注` 按钮，一键清除
- 单剧核查结果与批量核查共用同一套 `seasonCheckData` 标注系统，可混合使用

### 3. 完整性判定修复 (`task_flow_service.py`)

- **已完结侧** (~line 750)：`count == expected_eps` → `count >= expected_eps`
- **媒体库侧** (~line 918)：`actual_files == expected_eps` → `actual_files >= expected_eps`
- 原因：43 > 42 时不应判定为残缺（多出的可能是第 0 集、花絮等）
- `checker_service.py` 初始就是正确的（`actual_count < expected_count`）

### 4. Emby 僵尸清理 — CD2 删除后延迟清理

#### 路径转换可配置 (`settings.py` + `path_utils.py`)
- 新增配置项 `emby_prefix`（默认 `/volume3/emby影院/115网盘_3588/`）和 `cd2_media_prefix`（默认 `/80003588/emby库/`）
- `path_utils.py` 中硬编码前缀 → 从配置读取
- 新增 `cd2_path_to_emby_path()` 反向转换函数

#### Emby API (`emby_service.py` — 3 个新函数)
| 函数 | 用途 |
|------|------|
| `search_series_by_name(name)` | GET `/emby/Items` 按名称搜索 Series |
| `delete_emby_item(item_id)` | DELETE `/emby/Items/{id}` 删数据库记录 |
| `cleanup_emby_zombie(cd2_path)` | 编排：路径转换 → 提取剧名 → 搜索 → 删除 |

#### 延迟清理 (`cd2_router.py`)
- CD2 删除成功后，daemon 线程 sleep 180 秒后调 `cleanup_emby_zombie`
- 3 分钟延迟是为了让 symedia 的 webhook 通知流程有机会正常完成
- 如果 symedia 已成功清理，Emby search 返回空，无需操作（幂等）

### 5. 前端配置新增字段汇总

Emby 页面新增的配置项：
- `cd2_media_dir` — 媒体库根路径
- `cd2_organized_dir` — 已完结根路径
- `emby_prefix` — Emby 路径前缀
- `cd2_media_prefix` — CD2 路径前缀

### 6. 🆕 CD2 分类独立下拉框 (Session 5)

#### 问题
CD2 头部的分类显示为静态文本（`› 综艺`），无法切换。顶部筛选下拉的选项来自 qB 实例 API，未选 qB 实例时下拉为空，导致 CD2 分类也无法切换。

#### 后端 (`routers/system.py`)
- 新增 `GET /api/categories` 端点，读取 `backend/data/category.yaml`
- 返回 `{ movie: [...], tv: [...], all: [...] }` 三级结构

#### 前端 (`TorrentCleanup.vue`)
| 变更 | 说明 |
|------|------|
| `presetCategories` ref | 从 `GET /api/categories` 动态获取，替代硬编码数组 |
| `cd2Category` ref | 独立的 CD2 分类状态，与顶部 torrent 筛选解耦 |
| `categoryOptions` | 合并「预设 + qB 动态」，未选 qB 实例也有选项 |
| CD2 头部 `el-select` | 替换原来的静态 `› 综艺` 文本 |
| `selectedCategory` → `cd2Category` 单向同步 | 顶部选分类 → CD2 跟随；CD2 改分类不影响顶部 |

### 7. 🆕 TMDB 网络容错 (Session 5)

#### 问题
`api.themoviedb.org` 在国内频繁 SSL 中断（`SSLEOFError: EOF occurred in violation of protocol`），导致残缺季核查中大量 Season 被跳过，最终返回空结果。

#### 修复

| 层次 | 变更 |
|------|------|
| 域名 | `api.themoviedb.org` → `api.tmdb.org`（国内 CDN 路由更稳定） |
| URL 可配置 | `settings.py` 新增 `tmdb_base_url`，默认 `https://api.tmdb.org/3` |
| 重试机制 | `_tmdb_get()` 函数：最多 3 次尝试，指数退避（1.5s → 3s） |
| 覆盖范围 | `organize_service.py`（search_tv / tv_details / season_info）+ `tmdb_service.py`（get_tmdb_info） |

### 8. 🆕 综艺自动洗版特殊逻辑 + Season 0 豁免 (Session 6 + 修正)

#### 业务背景
综艺节目的 TMDB 季数/集数元数据经常不准确（例如实际只有 3 季，TMDB 记录 10 季；或单季集数标注错误）。如果按常规剧集逻辑走，会把用户辛苦收集的、实际完整的综艺季误判并错删。

#### 核心设计：6 个精确切入点（Session 6 修正后）

所有修改集中在 `backend/services/task_flow_service.py` 的 `auto_process_show()` 函数。

| # | 切入点 | 变更内容 | 影响范围 |
|---|--------|---------|---------|
| ① | Step 1 后 | 新增 `is_variety = (category == "综艺")` 标记 | 函数级变量 |
| ② | Step 2a 后 | **Season 0 豁免**：从 `season_dir_map` 中移除 Season 0 | **所有分类** |
| ③ | Step 2b 前 | ★ **快照原始 Season 集合**：`_organized_season_nums_original = set(season_dir_map.keys())`，供 Step 3a 使用 | 仅综艺 |
| ④ | Step 2b | 综艺 `seasons_to_check` 使用 `season_dir_map.keys()` 而非 TMDB range | 仅综艺 |
| ⑤ | Step 2c | ★ 综艺 `season_dir_map` 为空时 **不提前返回**，继续进入媒体库评估 | 仅综艺 |
| ⑥ | Step 3a | S0 跳过（通用）+ 综艺使用 `_organized_season_nums_original` 判断非对齐 Season | 所有分类 + 综艺 |
| ⑦ | Case B | ★ **移除 `not is_variety` 守卫**，综艺同样可进入 Case B | 仅综艺 |

> **关键修正 (Session 6 修订)**：初版在 Case B 前置了 `not is_variety` 一刀切守卫，导致新综艺两端仅有 Season 1 且均不完整时，无法触发整删重洗。修正后通过 ③ + ⑤ + ⑦ 三个注入点实现正确的"共享 Season 评估 → Case B 放行"链路。

#### 修正后的决策树逻辑

```
Step 2b 前: 快照 _organized_season_nums_original (原始已完结 Season 集合)
Step 2b:   综艺仅校验已完结中存在的 Season
           不完整的 Season 从 season_dir_map 移除（但仍在快照中）
Step 2c:   综艺 season_dir_map 为空 → 不 return，继续评估媒体库
Step 3a:   综艺媒体库 Season:
             - 在快照中 → 评估完整性，进入 media_season_state
             - 不在快照中 → 跳过（不触碰、不判定、不删除）
           → media_season_state 仅包含「两端共有」的 Season
Case B:    无守卫，all_media_incomplete 基于两端共有 Season 判定
           → 新综艺两端均残缺 → Case B 整删重洗 ✓
           → 老综艺存在非对齐 Season → 已被 Step 3a 跳过，不参与判定
```

#### 场景验证矩阵（修正后）

| 场景 | 预期行为 | 状态 |
|------|---------|------|
| **新综艺** organized(S1残缺) media(S1残缺) — 用户核心场景 | Case B 整删重洗，organized S1 保留 | ✅ |
| 综艺 organized(S1完整,S3完整) media(S1残缺,S2完整,S3残缺,S4完整) | S1/S3 判定，S2/S4 跳过。Case B 整删（⚠️ S2/S4 也被删） | ⚠️ 见下方说明 |
| 综艺 organized(S1完整) media(S1完整,S2完整非对齐) | Cases C/D 逐季对比，S2 不处理 | ✅ |
| 综艺 organized 空 → media 有内容 | 不做任何处理 | ✅ |
| 综艺 media 目录不存在 (Case A) | 全部 organized Season 导入 | ✅ |
| 常规剧 + organized 有 Season 0 | S0 跳过，其余正常处理 | ✅ |
| 非综艺 + 所有 media Season 残缺 (Case B) | 整剧目录删除，行为不变 | ✅ |

> ⚠️ **已知权衡**：当综艺存在非对齐的媒体库 Season（已完结中不存在的 Season）且所有对齐 Season 均残缺时，Case B 仍会删除整个媒体库目录（包括非对齐 Season）。这是因为 Case B 的操作粒度是「整个剧集目录」而非单个 Season。该场景在实际中较少见（非对齐 Season 通常意味着用户手动整理了额外的季），如需要可按 Season 粒度拆分 Case B，当前暂不处理。

---

## 避坑记录 (Failed Approaches)

1. **残缺季核查第一版用弹窗展示结果**
   - 用户反馈：应该内联标注到目录列表上，点进去能看到具体缺失数
   - 解决：重构为内联标注模式，按钮放在 nav bar，结果反写到文件列表项

2. **残缺季核查按钮放在 CD2 段标题右侧**
   - 用户反馈：应该放在左侧 nav bar 的 `←上一级 📂/2026/` 位置，仅在年份层级显示
   - 解决：移到 nav bar 中 `cd2MediaDepth === 1` 时显示

3. **Emby 僵尸清理：全库扫描方案被否决**
   - 原因：用户库很大，全扫 `POST /emby/Library/Refresh` 太慢
   - 解决：改用精准方案 — 搜索单 Item + `DELETE /emby/Items/{id}`，不触发扫描

4. **路径转换前缀硬编码**
   - 用户要求：前缀应可配置，方便更换设备/环境
   - 解决：在 settings.py 新增 `emby_prefix` / `cd2_media_prefix` 配置项，前端可编辑

5. **操作时间线日志用 `task_id=None`（Session 3 遗留）**
   - 预分析阶段（season validation）的 SKIP_FOLDER/KEEP_ORGANIZED/KEEP_MEDIA 共 4 处仍使用 `task_id=None`
   - 未修复 — 信息性日志，非阻塞

6. **🆕 年份跳转 `type="number"` 导致按钮永远禁用 (Session 5)**
   - 原因：Vue 3 中 `<input type="number">` 自动将 v-model 值转为 number 类型，`.trim()` 报错
   - 解决：改为 `type="text"` + `inputmode="numeric"`，所有 .trim() 调用前加 `String()` 安全转换

7. **🆕 CD2 分类预设数据硬编码 (Session 5)**
   - 第一版用 `PRESET_CATEGORIES = ['国产剧', '综艺', ...]` 写死在代码里
   - 用户要求：使用 `backend/data/category.yaml` 中的分类
   - 解决：新增 `GET /api/categories` 端点读取 YAML，前端 `onMounted` 动态获取

---

## 当前状态 (Current State)

- ✅ CD2 根路径可配置化完成，前后端构建通过
- ✅ 残缺季雷达全栈完成（批量 + 单剧核查 + 内联标注 UI）
- ✅ 完整性判定 `>=` 修复完成
- ✅ Emby 僵尸清理（3 分钟延迟 + 精准 Delete API）完成
- ✅ 路径转换前缀可配置化完成
- ✅ CD2 分类独立下拉框完成（category.yaml 驱动）
- ✅ TMDB 网络容错（域名切换 + 重试机制）完成
- ✅ 年份跳转按钮修复
- ✅ **综艺自动洗版特殊逻辑 + Season 0 豁免完成**（6 个注入点 + 1 次 Case B 逻辑修正，后端语法检查通过）
- ✅ 前端构建通过，后端语法检查通过
- ✅ 完整扫描流程（BFS + 自动触发洗版 → 大盘时间线）基本跑通
- ⚠️ Emby 僵尸清理尚未联调验证（需在有剧集可删除的环境下测试完整链路）
- ⚠️ 预分析阶段 4 处日志 `task_id=None` 仍未修复（约 lines 760/767/934/941）
- ⚠️ **综艺逻辑尚未联调验证**（需在实际综艺目录上测试完整链路）

---

## 下一步 (Next Steps)

### 优先级 1 — 部署验证

1. **部署验证**：
   ```bash
   cd backend && source venv/bin/activate && python main.py
   cd frontend && npm run dev
   ```
   - 验证 CD2 根路径配置 → 种子清理页面路径生效
   - 验证残缺季雷达 → 导航到某个年份目录 → 点击「核查残缺季」→ 标注正确显示
   - 验证单剧核查 → 点击剧集行的 🔍 按钮
   - 验证 CD2 分类下拉 → 切换分类 → CD2 目录正确跳转
   - 验证 Emby 僵尸清理 → CD2 删除一个目录 → 等 3 分钟 → 检查 Emby
   - **🆕 验证综艺逻辑** → 对一个综艺目录触发洗版 → 确认 Season 0 跳过 → 确认非对齐 Season 保留 → 确认仅两边共有的 Season 参与对比

### 优先级 2 — 可选

2. **预分析阶段日志修复**（Session 3 遗留）
3. **可选增强**：扫描任务支持多个目录路径、扫描日志清理/归档、定时扫描通知
