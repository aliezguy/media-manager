# AI 模型配置 · 拖拽排序 + 增删 — 设计文档

日期：2026-08-02
状态：已确认
范围：前端 [EmbySettings.vue](frontend/src/components/EmbySettings.vue)「智能服务 · AI 模型配置」卡片；后端零功能性改动（仅验证）。

## 背景与目标

系统设置页「智能服务 · AI 模型配置」当前固定展示 3 个模型卡片（首选 / 次选 / 三选），
无法调整先后顺序、无法增删模型。用户希望通过拖拽自由调整模型优先级，且支持添加/删除模型，
上限 6 个。保存后，后续 AI 接口（多级 Fallback）须严格按用户最新保存的顺序调用。

### 关键发现（决定方案走向）

后端**已经按 `ai_providers` 数组顺序驱动优先级**，无需功能性改动：

- 配置存于 `backend/data/config.json`，`ai_providers` 为有序数组；`save_config()`
  （`backend/config/settings.py:82-87`）用 `dict.update` 整体替换，**JSON 数组天然保序**。
- `AITranslator._resolve_providers()`（`backend/services/ai_translator.py:411-459`）
  按数组顺序解析，index 0 为首选，跳过缺失 `api_key` / `model_name` 的无效项。
- `_chat_complete_with_fallback()`（`ai_translator.py:601-650`）按解析结果逐级瀑布降级。
- `get_primary_provider()`（`ai_translator.py:805-813`）返回 `providers[0]`，
  供打标 / 推理 / 解析等单模型调用方使用。
- 每次调用均 `load_config()` 热读最新配置 —— 保存后下一次调用立即按新顺序执行。
- N>3 与 N=0 后端本就支持：空列表回退旧字段（`sf_api_key` / `llm_base_url` / `llm_model_name`）或禁用 AI。

结论：**改动集中在前端**；后端只做验证、不改功能代码。

## 需求范围

1. 拖拽调整模型先后顺序（深色主题、流畅动画）。
2. 支持添加 / 删除模型，上限 6 个；序号标签：首选/次选/三选/四选/五选/六选，超出回退 `Provider N`。
3. 保存后后端严格按新顺序调用（现状已满足，验证即可）。
4. 视觉与现有深色主题管理后台一致。

### 明确不做（YAGNI）

- 键盘无障碍移动按钮（↑/↓）——拖拽已覆盖主场景，未来可补。
- 保存前的唯一性 / 去重校验——现有「跳过无效项」机制已兜底。
- 后端新增 reorder API / 保序显式化——`save_config` 整数组替换已满足，避免画蛇添足。

## 设计方案（方案 A：vuedraggable）

改动全部集中在 `frontend/src/components/EmbySettings.vue`。
`vuedraggable@^4.1.0` + `sortablejs` 已安装；拖拽范式镜像自 `frontend/src/components/MpConfig.vue`
（`_dragId` 稳定 key、`.drag-handle` / `.ghost-card` / `.drag-card` 深色主题样式）。

### Script 层

| 项 | 说明 |
|----|------|
| 常量 | `MAX_PROVIDERS = 6`；`ORDINAL_LABELS = ['首选','次选','三选','四选','五选','六选']` |
| `AIProvider` | 增加 `_dragId?: number`（vuedraggable `:item-key` + 展开状态锚点，UI 临时字段，不落盘） |
| key 生成 | 模块级 `_uidCounter` + `genDragId()`，镜像 `MpConfig.vue:24-41` |
| 展开状态 | `openIdx: ref<number[]>` → `openIds: ref<Set<number>>`（按 `_dragId`）；重排 / 删除后展开状态不错位 |
| `priorityLabel(idx)` | `ORDINAL_LABELS[idx] || 'Provider ' + (idx + 1)` |
| `addProvider()` | 长度 < 6 时 push 空白 Provider：`name` = 新位置序数标签，其余字段空串，分配 `_dragId`，展开；达上限时按钮禁用 + `ElMessage` 提示 |
| `removeProvider(idx)` | 从 `openIds` 移除该项 `_dragId`，`splice` 数组；允许删到 0（空列表 = AI 禁用，与 `ai-note` 文案一致） |
| `onDragChange()` | 重排后名称联动：仅当某项 `name` 仍属于 `ORDINAL_LABELS` 时更新为新位置标签；自定义名称不动 |
| `saveConfig()` | 发送前 `config.ai_providers.map(({ _dragId, ...rest }) => rest)` 剥离 UI 临时字段（刻意避免 MpConfig 将 `_dragId` 落入 config.json 的既有瑕疵） |
| `getProviderKey(p)` | 返回 `p._dragId`，供 `<draggable :item-key>` |

### Template 层

- 用 `<draggable>` 包住现有 provider-card 的 `v-for`：
  `v-model="config.ai_providers" :animation="250" ghost-class="drag-ghost" drag-class="drag-live" handle=".drag-handle" :item-key="getProviderKey" @change="onDragChange"`，
  `<template #item="{ element: p, index }">`。
- 卡片头部改造（保留折叠交互，删除/手柄处 `@click.stop`）：
  - 最左：拖拽手柄（`GripVertical` 图标，`.drag-handle`）。
  - 中部：`priority-badge` 显示 `priorityLabel(index)` + `provider-model` + `provider-state` 不变。
  - 最右：删除按钮（`Trash2` 图标，`.provider-remove`，hover 变红）+ 折叠 `chevron`。
- 列表下方：全宽虚线「＋ 添加模型」按钮（`.provider-add`，达 `MAX_PROVIDERS` 禁用并提示）。
- 空列表：`v-if="!config.ai_providers.length"` 空态引导块（提示 + 添加按钮）。

### 样式层

镜像 `MpConfig.vue:763-779` 并适配本卡：
- `.drag-handle { cursor: grab; user-select: none; }`，`:active { cursor: grabbing; }`。
- `:deep(.drag-ghost)`：半透明 + 蓝色虚线边框占位。
- `:deep(.drag-live)`：拖起缩放 `scale(1.04)` + 深色阴影 + 蓝色光晕。
- `.provider-remove`：默认低调，hover 变红。
- `.provider-add`：虚线边框、hover 高亮、`disabled` 态置灰。

## 后端

**零功能性改动。** 存储与调用已按数组顺序工作（见「关键发现」），本次仅验证。

## 验证计划

| 步骤 | 方法 | 期望 |
|------|------|------|
| 类型检查 | `cd frontend && npx vue-tsc --build` | 无类型错误 |
| 构建 | `npm run build`（vue-tsc + vite） | 构建成功 |
| 后端顺序单测 | 临时脚本构造不同顺序的 `ai_providers`，断言 `_resolve_providers()` 返回顺序、`get_primary_provider()` 返回首位 | 顺序与配置一致 |
| 端到端 | 启动后端 + 前端；拖拽重排 → 保存 → 读 `config.json` 确认顺序 → 触发一次翻译，观察日志 `🚀 尝试 Provider[?]` | 日志顺序 = 新保存顺序 |
| 回归 | 现有 3 个真实 Provider 配置加载正常；增删保存不丢字段；`_dragId` 不落盘；N=0 空态与「添加」可用 | 全部通过 |

## 开放问题

无（已全部澄清）。

## 相关文件

- `frontend/src/components/EmbySettings.vue`（唯一改动文件）
- `frontend/src/components/MpConfig.vue`（拖拽范式参考，不改动）
- `backend/services/ai_translator.py`（验证对象，不改动）
- `backend/config/settings.py`（验证对象，不改动）
