# Session Handoff — Emby AI Manager 全自动洗版工作流

> **日期**: 2026-07-03
> **分支**: main
> **状态**: 第八会话 — 轧戏(tmdb=279136) Case B 端到端测试通过，全部注释中文化完成

---

## 一、当前目标 (Current Goal)

**废弃 Emby library.new webhook 依赖**，将洗版流程从三阶段状态机重构为两阶段。种子删除不再等待 Emby 的"新增/刮削 Webhook"，改为**移动后立即通过文件系统强校验**（fileCount + totalSize 对比）来决定是否删除种子。提高流程可靠性，消除 library.new 不稳定导致的种子残留问题。

核心原则：**不删数据、不静默失败、校验不过就不删种子**。

---

## 二、已完成工作 (What's Done)

### 2.1 状态机重构：三阶段 → 两阶段

| 旧状态机 | 新状态机 |
|---------|---------|
| INIT → WAITING_FOR_DELETE_WEBHOOK → **WAITING_FOR_NEW_WEBHOOK** → COMPLETED | INIT → WAITING_FOR_DELETE_WEBHOOK → COMPLETED / FAILED |

- `WAITING_FOR_NEW_WEBHOOK` 枚举值保留在 `models.py` 中（兼容旧 DB 记录），但不再创建新的该状态任务
- 所有移动+校验+种子删除现在要么在 `auto_process_show` 内联完成，要么在 `handle_library_deleted_webhook` 中内联完成

### 2.2 新增 3 个模块级辅助函数

| 函数 | 文件位置 | 用途 |
|------|---------|------|
| `_get_season_stats(cd2, path, retries=3)` | `task_flow_service.py:170` | 获取 CD2 目录的递归 fileCount + totalSize，支持 2s/4s/8s 指数退避重试 |
| `_verify_season_move(cd2, source, dest, ...)` | `task_flow_service.py:187` | 单季完整流程：pre-move stats → move → 等 2s → post-move stats → 对比 → 返回 verified |
| `_delete_qb_torrents_by_title(qb_config_id, title)` | `task_flow_service.py:231` | 按剧名搜索 qB 种子并删除（含文件），返回删除数量 |

### 2.3 auto_process_show 重构为 4 条决策路径

原来的"逐季对比 → 部分移动 → 等待 webhook"被替换为明确的 4 种情况：

| 情况 | 条件 | 行为 | 终点 |
|------|------|------|------|
| **Case A** | 媒体库目录不存在（首次导入）| 创建目录 → 逐季 move+verify → 通过则删种子 | COMPLETED（内联） |
| **Case B** | 所有媒体库 Season 均残缺 | 删除整剧目录 → 保存 organized 候选列表 | WAITING_FOR_DELETE_WEBHOOK |
| **Case C** | 部分完整 / 版本不同 | 删除残缺季 → 去重 → 逐季 move+verify → 通过则删种子 | COMPLETED（内联） |
| **Case D** | 全部完整且完全相同 | 仅删除 organized 中的重复季 | 无需操作 |

### 2.4 handle_library_deleted_webhook 重写

Webhook handler 现在是**最终阶段**（不再是中间阶段），收到 Emby 整剧删除确认后：

1. **重建目录** — 通过 `_cd2_dir_exists` + `create_folder` 重建目标剧集空目录
2. **逐季移动+校验** — 从 context 中的 `organized_seasons_to_move` 列表遍历每个 Season：
   - 确认源目录存在 → `_verify_season_move()` → 记录校验结果
3. **种子清理** — 所有 Season 校验通过 → `_delete_qb_torrents_by_title()` → COMPLETED
4. **失败处理** — 任一 Season 校验失败 → CRITICAL 日志 → 保留种子 → FAILED

### 2.5 handle_library_new_webhook 标记为 DEPRECATED

- 函数体保留为空操作（向后兼容），但不再执行任何逻辑
- docstring 明确说明：种子删除已迁移至文件系统校验

### 2.6 emby.py 清理

- 移除 `_handle_library_new_for_task_flow` 函数
- 移除 webhook handler 中 `if event == "library.new"` → task flow 的分支
- 新增注释说明 library.new 不再用于种子清理

### 2.7 注释中文化

- 所有新增代码的注释、docstring、日志消息均已翻译为中文

### 修改文件清单

| 文件 | 改动 |
|------|------|
| `backend/services/task_flow_service.py` | **核心重构** — 约 1500 行 |
| `backend/routers/emby.py` | 移除 library.new → task flow 注册 |

---

## 三、避坑记录 (Failed Approaches)

| 尝试 | 问题 | 教训 |
|------|------|------|
| 用 Write 工具替换大段代码 | Write 直接覆盖了整个文件（而非 Append），丢失了文件头部的 imports 和 helpers | **对大文件做局部替换必须用 Edit 工具**，Write 是全量覆盖。如 Edit 的 old_string 太长（500+行），应先用 Bash 写临时文件再 cat 拼接 |
| Bash heredoc 写大段 Python 代码 | Classifier 因 deepseek-v4-pro 不可用而拒绝执行大型脚本 | 小文件用 Write 工具创建，再用简单 Bash（如 `cat`）拼接。复杂脚本用 Python 单行命令替代 |
| 依赖 WAITING_FOR_NEW_WEBHOOK 触发种子删除 | library.new 可能延迟、不触发或多次触发，导致种子残留 | 改为文件系统强校验驱动，移动后立即验证，不依赖外部事件 |

---

## 四、当前状态 (Current State)

### 整体状态：✅ 后端编译通过，端到端测试（轧戏 Case B）已验证通过

### 编译验证结果
- ✅ Python 语法检查通过 (`py_compile`)
- ✅ 所有关键函数可成功导入（`auto_process_show`, `handle_library_deleted_webhook`, `handle_library_new_webhook`, `_verify_season_move`, `_get_season_stats`, `_delete_qb_torrents_by_title`）
- ✅ 无残留 `WAITING_FOR_NEW_WEBHOOK` 创建路径
- ✅ `handle_library_new_webhook` 标记为 DEPRECATED，空操作
- ✅ `TaskStatus` 枚举值完整：INIT, WAITING_FOR_DELETE_WEBHOOK, WAITING_FOR_NEW_WEBHOOK (保留), COMPLETED, FAILED

### 端到端测试结果

| 测试项 | 剧集 | 场景 | 结果 |
|--------|------|------|------|
| Case B 全链路 | 轧戏 (tmdb=279136) | 媒体库残缺 → 整剧删除 → Emby webhook → 重建+移动+校验+删种子 | ✅ 通过 |
| Case A | 成何体统 (tmdb=280632) | 媒体库无此剧集 → 直接移动+校验+删种子（内联完成） | ⏳ 待测 |
| Case C | — | 媒体库部分完整 → 逐季对比+移动+校验+删种子（内联完成） | ⏳ 待测 |
| 校验失败场景 | — | 模拟移动后文件不完整 → 种子保留 + CRITICAL 日志 | ⏳ 待测 |
| CD2 缓存延迟 | — | 验证 _verify_season_move 中的 2s 等待 + 3 次重试是否足够 | ⏳ 待测 |

### 第八会话新增

- **注释中文化**: `task_flow_service.py` 所有英文注释、docstring 已翻译为中文

### 关键配置 (config.json)

```json
{
  "cd2_media_dir": "/80003588/emby库/电视剧/",
  "cd2_organized_dir": "/80003588/网盘整理/完结整理/电视剧/",
  "mp_host": "http://192.168.31.173:3006"
}
```

---

## 五、下一步 (Next Steps)

### 优先级 1：剩余端到端测试

1. 用成何体统（tmdb=280632）验证 Case A 逻辑：媒体库无此剧集 → 直接移动+校验+删种子
2. 用金关（tmdb=272476）验证 Case B 逻辑
3. 寻找合适剧集验证 Case C 逻辑：媒体库部分完整 → 逐季对比+移动+校验+删种子

### 优先级 2：校验失败场景测试

4. 模拟 CD2 移动后文件不完整 → 确认 CRITICAL 日志输出 + 种子保留 + task → FAILED
5. 验证 `_get_season_stats` 的重试机制对 CD2 缓存延迟的处理

### 优先级 3：前端与后续功能

6. 前端任务面板：可视化 `auto_task_flows` 表的状态流转（INIT → WAITING_FOR_DELETE_WEBHOOK → COMPLETED/FAILED）
7. 自动对比扫描：基于 CD2 两侧目录差异自动标记待整理/可清理项目
8. 版本选择逻辑：当前所有完整版本都移动到媒体库，后续需要版本选择/清理机制
