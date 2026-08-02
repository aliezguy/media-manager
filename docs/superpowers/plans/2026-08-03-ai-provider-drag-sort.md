# AI 模型配置 · 拖拽排序 + 增删 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「智能服务 · AI 模型配置」卡片支持拖拽排序 + 增删模型（上限 6 个），保存后后端严格按新顺序调用。

**Architecture:** 改动全部集中在前端 `frontend/src/components/EmbySettings.vue`：用已安装的 `vuedraggable` 包住现有 Provider 卡片列表，以 UI 临时字段 `_dragId` 作为稳定 key 与展开状态锚点，保存前剥离该字段。后端 `ai_providers` 数组顺序已驱动 Fallback（index 0 = 首选），无需功能性改动，仅用临时脚本验证。

**Tech Stack:** Vue 3.5 + `<script setup lang="ts">` + Element Plus 2.12 + lucide-vue-next + vuedraggable@^4.1.0（sortablejs 随附）。后端 Python FastAPI + `services/ai_translator.py`。

## Global Constraints

- **唯一被修改的仓库文件**：`frontend/src/components/EmbySettings.vue`。该文件工作区已有一个未提交改动 `<style scoped>` → `<style scoped lang="postcss">`（与本次改动相关且无害）；Task 2 提交时会一并包含，提交前先 `git diff` 确认无意外内容。
- **前端类型检查必须通过**：`cd frontend && npx vue-tsc --build`（项目用 `--build`，**不要**用 `--noEmit`；typescript 锁 5.9.3，TS7 与 vue-tsc 不兼容）。
- **不新增任何 npm 依赖**：`vuedraggable@^4.1.0`、`sortablejs` 已在 `node_modules`。
- **后端零功能性改动**：`backend/services/ai_translator.py`、`backend/config/settings.py` 均不改。顺序验证用临时脚本（Task 1，跑完即删），**不要**往 venv 安装 pytest（项目刻意保持零新增依赖）。
- **上限与标签**：`MAX_PROVIDERS = 6`；序号标签 `['首选','次选','三选','四选','五选','六选']`，超出回退 `'Provider ' + (idx + 1)`。
- **`_dragId` 是 UI 临时字段**：保存前从 `ai_providers` 每项剥离，**不得**落入 `config.json`。
- **删除允许删到 0**：空列表 = AI 禁用（与卡片内 `ai-note` 文案一致）。
- **名称联动规则**：重排后仅当某项 `name` 仍属于序数默认标签时更新为新位置标签；用户自定义名称（如"硅基流动"）不动。
- 设计文档：`docs/superpowers/specs/2026-08-02-ai-provider-drag-sort-design.md`（已提交）。

---

### Task 1: 后端顺序验证（临时脚本，证明零改动可行）

**Files:**
- Create: `backend/_verify_provider_order.py`（临时，跑完删除）
- 只读参考（不改）：`backend/services/ai_translator.py`、`backend/config/settings.py`

**Interfaces:**
- Consumes: `AITranslator._resolve_providers(cfg=None, log_invalid=True) -> List[dict]`（类方法，`backend/services/ai_translator.py:411`）；`get_primary_provider(cfg=None) -> Optional[dict]`（模块函数，`backend/services/ai_translator.py:805`）。
- Produces: 无（脚本为一次性验证，不产出供后续任务使用的接口）。

- [ ] **Step 1: 写临时验证脚本**

在 `backend/` 下创建 `_verify_provider_order.py`：

```python
"""
临时验证脚本：证明后端已按 ai_providers 数组顺序驱动优先级，无需功能改动。
运行：cd backend && venv/bin/python _verify_provider_order.py
跑完后删除本文件（不属于仓库交付物）。
"""
import sys

from services.ai_translator import AITranslator, get_primary_provider


def make(name, model, key, url="https://x/v1"):
    return {"name": name, "base_url": url, "alt_base_url": "", "api_key": key, "model_name": model}


failures = []


def check(label, got, want):
    ok = got == want
    print(("✅" if ok else "❌"), label, "→", got)
    if not ok:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# 1) 顺序 = 配置顺序（index 0 即首选）
cfg1 = {"ai_providers": [
    make("A", "m-a", "k-a"),
    make("B", "m-b", "k-b"),
    make("C", "m-c", "k-c"),
]}
check("顺序", [p["name"] for p in AITranslator._resolve_providers(cfg=cfg1, log_invalid=False)], ["A", "B", "C"])
check("首选", (get_primary_provider(cfg=cfg1) or {}).get("name"), "A")

# 2) 打乱顺序后，首选随之变化（模拟前端拖拽重排）
cfg2 = {"ai_providers": [cfg1["ai_providers"][2], cfg1["ai_providers"][0], cfg1["ai_providers"][1]]}
check("重排后顺序", [p["name"] for p in AITranslator._resolve_providers(cfg=cfg2, log_invalid=False)], ["C", "A", "B"])
check("重排后首选", (get_primary_provider(cfg=cfg2) or {}).get("name"), "C")

# 3) 无效项（缺 api_key）跳过，其余顺序保持
cfg3 = {"ai_providers": [
    make("A", "m-a", ""),     # 无效
    make("B", "m-b", "k-b"),
    make("C", "m-c", "k-c"),
]}
check("跳过无效", [p["name"] for p in AITranslator._resolve_providers(cfg=cfg3, log_invalid=False)], ["B", "C"])

# 4) 空列表 → 回退旧字段单 Provider
cfg4 = {"ai_providers": [], "sf_api_key": "legacy-key", "llm_base_url": "https://legacy/v1", "llm_model_name": "legacy-model"}
check("空列表回退", [p["name"] for p in AITranslator._resolve_providers(cfg=cfg4, log_invalid=False)], ["Legacy(旧字段)"])

# 5) 全空配置 → 无可用 Provider（AI 禁用）
check("空配置", AITranslator._resolve_providers(cfg={"ai_providers": []}, log_invalid=False), [])

print()
if failures:
    print("FAILED:", *failures, sep="\n  - ")
    sys.exit(1)
print("ALL PASSED —— 后端顺序逻辑无需改动")
```

- [ ] **Step 2: 运行脚本**

Run: `cd /Users/jiangkai/project/emby-ai-manager/backend && venv/bin/python _verify_provider_order.py`
Expected: 全部 `✅`，末尾 `ALL PASSED —— 后端顺序逻辑无需改动`，退出码 0。若脚本报 `ModuleNotFoundError`，确认 cwd 是 `backend/`（脚本在 `backend/` 下时其目录会自动进入 `sys.path`）。

- [ ] **Step 3: 删除临时脚本**

```bash
rm /Users/jiangkai/project/emby-ai-manager/backend/_verify_provider_order.py
```

- [ ] **Step 4: 提交（无仓库文件变更，仅记录验证结论于计划）**

此任务不产生仓库改动，无需 commit。验证结论记录在 Task 3 的最终检查清单中。

---

### Task 2: EmbySettings.vue — 拖拽排序 + 增删模型

**Files:**
- Modify: `frontend/src/components/EmbySettings.vue`

**Interfaces:**
- Consumes: `config.ai_providers: AIProvider[]`（现有 reactive 数据）；`providerStatus(p)`（现有，不变）；后端 `POST /api/config`（保存整份 config，`backend/routers/system.py:28`）。
- Produces:
  - `MAX_PROVIDERS`（=6）、`ORDINAL_LABELS`（`string[]`）
  - `genDragId(): number`、`getProviderKey(p): number`
  - `openIds: Ref<Set<number>>`、`isOpen(p: AIProvider): boolean`、`toggleProvider(p: AIProvider): void`
  - `atCap(): boolean`、`addProvider(): void`、`removeProvider(p: AIProvider): void`
  - `onDragChange(): void`（vuedraggable `@change`，无参，避免 noUnusedParameters）
  - `saveConfig()` 改为发送剥离 `_dragId` 的 payload

**为什么 script 与 template 必须同一任务完成**：`tsconfig.app.json` 开了 `noUnusedLocals` / `noUnusedParameters`，若只加 script（导入 `draggable`、`GripVertical`、`Plus`、`Trash2`）而 template 未用，类型检查会失败。因此本任务一次性完成 script + template + 样式。

- [ ] **Step 1: 改 `<script setup lang="ts">` 头部（导入 + 常量 + 接口）**

将第 2-3 行替换为：

```ts
import { reactive, ref, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import draggable from 'vuedraggable'
import {
  Server, Cloud, Sparkles, Save, PlugZap, Loader2,
  ChevronDown, KeyRound, UserRound, Globe, Link2,
  FolderTree, Folder, HardDrive, Cpu, CheckCircle2, XCircle,
  GripVertical, Plus, Trash2
} from 'lucide-vue-next'
```

将 `interface AIProvider`（第 12-18 行）替换为：

```ts
// AI Provider 配置项（与后端 config.json 的 ai_providers 结构保持一致）
interface AIProvider {
  name: string
  base_url: string
  alt_base_url: string
  api_key: string
  model_name: string
  _dragId?: number  // UI 临时字段：vuedraggable 稳定 key + 展开状态锚点；保存前剥离，不落盘
}

const MAX_PROVIDERS = 6
const ORDINAL_LABELS = ['首选', '次选', '三选', '四选', '五选', '六选']

// UI 临时唯一 id 生成（镜像 MpConfig.vue 的 _dragId 模式）
let _uidCounter = 0
const genDragId = () => ++_uidCounter
```

- [ ] **Step 2: 给 reactive 的 `ai_providers` 补类型标注**

在 `const config = reactive({` 块内，将 `ai_providers` 数组字面量的 `]` 后加 `as AIProvider[]`：

```ts
  ai_providers: [
    { name: '首选', base_url: '', alt_base_url: '', api_key: '', model_name: '' },
    { name: '次选', base_url: '', alt_base_url: '', api_key: '', model_name: '' },
    { name: '三选', base_url: '', alt_base_url: '', api_key: '', model_name: '' }
  ] as AIProvider[]
```

（若不加此标注，`config.ai_providers` 的元素类型不含 `_dragId`，后续 `p._dragId` 会报 TS 错。）

- [ ] **Step 3: 替换 `priorityLabel` 与展开状态逻辑**

将 `const priorityLabel = ...`（第 46 行）替换为：

```ts
const priorityLabel = (idx: number) => ORDINAL_LABELS[idx] || ('Provider ' + (idx + 1))
```

将 `openIdx` 声明（第 25 行 `const openIdx = ref([0, 1, 2])`）删除，并把 `isOpen` / `toggleProvider`（第 55-61 行）替换为：

```ts
// 展开状态按 _dragId 锚定（重排 / 删除后不错位）
const openIds = ref<Set<number>>(new Set())
const isOpen = (p: AIProvider) => p._dragId != null && openIds.value.has(p._dragId)
const toggleProvider = (p: AIProvider) => {
  if (p._dragId == null) return
  const next = new Set(openIds.value)
  if (next.has(p._dragId)) next.delete(p._dragId)
  else next.add(p._dragId)
  openIds.value = next
}
const openAll = () => {
  openIds.value = new Set(
    (config.ai_providers as AIProvider[])
      .map((p) => p._dragId)
      .filter((id): id is number => id != null)
  )
}
```

- [ ] **Step 4: 替换 `onMounted` 与 `saveConfig`，新增增删/排序/保存辅助函数**

将 `onMounted`（第 63-76 行）替换为：

```ts
onMounted(async () => {
  try {
    const res = await axios.get(`${API_URL}/api/config`)
    Object.assign(config, res.data)
    // 归一化 Provider 列表：兼容旧配置数据，backfill alt_base_url 与 UI 临时 _dragId
    if (!Array.isArray(config.ai_providers)) config.ai_providers = []
    config.ai_providers = (config.ai_providers as AIProvider[]).map((p) => ({
      ...p,
      alt_base_url: p.alt_base_url || '',
      _dragId: p._dragId ?? genDragId()
    }))
    openAll()
  } catch (e) {}
})
```

将 `saveConfig`（第 78-83 行）替换为，并在其后新增三个辅助函数与 `cleanPayload`：

```ts
const atCap = () => (config.ai_providers || []).length >= MAX_PROVIDERS

const addProvider = () => {
  if (atCap()) {
    ElMessage.warning(`最多支持 ${MAX_PROVIDERS} 个模型，请先删除再添加`)
    return
  }
  const dragId = genDragId()
  config.ai_providers.push({
    name: priorityLabel(config.ai_providers.length),
    base_url: '',
    alt_base_url: '',
    api_key: '',
    model_name: '',
    _dragId: dragId
  })
  openIds.value = new Set(openIds.value).add(dragId)
}

const removeProvider = (p: AIProvider) => {
  if (p._dragId != null) {
    const next = new Set(openIds.value)
    next.delete(p._dragId)
    openIds.value = next
  }
  const i = config.ai_providers.findIndex((x) => x._dragId === p._dragId)
  if (i >= 0) config.ai_providers.splice(i, 1)
}

// 重排后：默认序数名称跟随新位置；用户自定义名称（如"硅基流动"）不动
const onDragChange = () => {
  config.ai_providers.forEach((p, idx) => {
    if (typeof p.name === 'string' && ORDINAL_LABELS.includes(p.name)) {
      p.name = priorityLabel(idx)
    }
  })
}

// vuedraggable 稳定 key（onMounted / addProvider 保证每项都有 _dragId）
const getProviderKey = (p: AIProvider) => p._dragId ?? -1

// 保存前剥离 UI 临时字段 _dragId，避免污染 config.json
const cleanPayload = () => {
  const { ai_providers, ...rest } = config
  return {
    ...rest,
    ai_providers: (ai_providers || []).map(({ _dragId, ...p }) => p)
  }
}

const saveConfig = async () => {
  try {
    await axios.post(`${API_URL}/api/config`, cleanPayload())
    ElMessage.success('配置已保存')
  } catch (e) { ElMessage.error('保存失败') }
}
```

- [ ] **Step 5: 替换 template 的 Provider 列表块**

将 `<div class="provider-list">...</div>`（第 246-294 行，即 v-for 卡片循环整体）替换为：

```html
          <!-- Provider 可折叠子卡片（拖拽排序 + 增删，上限 6 个） -->
          <div class="provider-list">
            <!-- 空态：未配置任何模型 -->
            <div v-if="!(config.ai_providers || []).length" class="provider-empty">
              <p class="provider-empty-title">尚未配置任何 AI 模型</p>
              <p class="provider-empty-sub">所有 AI 功能（翻译 / 推荐 / 推理 / 打标）将禁用。点击下方「添加模型」开始配置。</p>
            </div>

            <draggable
              v-if="(config.ai_providers || []).length"
              v-model="config.ai_providers"
              :animation="250"
              ghost-class="drag-ghost"
              drag-class="drag-live"
              handle=".drag-handle"
              :item-key="getProviderKey"
              @change="onDragChange"
            >
              <template #item="{ element: p, index }">
                <div class="provider-card" :class="{ 'is-open': isOpen(p) }">
                  <div class="provider-head" @click="toggleProvider(p)">
                    <span class="drag-handle" title="拖拽调整优先级" @click.stop>
                      <GripVertical :size="15" />
                    </span>
                    <span class="priority-badge" :class="'badge-' + (index % 3)">
                      <i></i>{{ priorityLabel(index) }}
                    </span>
                    <span class="provider-model">{{ p.model_name || '未配置模型' }}</span>
                    <span class="provider-state" :class="'st-' + providerStatus(p).tone">
                      <i></i>{{ providerStatus(p).text }}
                    </span>
                    <span class="provider-remove" title="删除该模型" @click.stop="removeProvider(p)">
                      <Trash2 :size="14" />
                    </span>
                    <span class="chevron" :class="{ rotated: isOpen(p) }">
                      <ChevronDown :size="16" />
                    </span>
                  </div>

                  <div class="provider-body" :class="{ hidden: !isOpen(p) }">
                    <div class="provider-body-inner">
                      <!-- 内部字段区保持不变：名称 / 模型名 / Base URL / Alt Base URL / API Key -->
                    </div>
                  </div>
                </div>
              </template>
            </draggable>

            <!-- 添加模型按钮（达上限禁用） -->
            <button
              type="button"
              class="provider-add"
              :disabled="atCap()"
              @click="addProvider"
            >
              <Plus :size="15" />
              {{ atCap() ? `已达上限（${MAX_PROVIDERS} 个）` : '添加模型' }}
            </button>
          </div>
```

> 注意：`<div class="provider-body-inner">` 内的 5 个字段块（名称、模型名、Base URL、Alt Base URL、API Key）原样保留，只把外层 `v-for` 结构换成 `draggable` 的 `#item` 模板。

- [ ] **Step 6: 追加样式**

在 `<style scoped lang="postcss">` 块末尾（`.chevron.rotated` 之后即可，加到 AI Provider 子卡片样式区）追加：

```css
/* ==================== AI Provider 拖拽排序 + 增删 ==================== */
.drag-handle {
  display: inline-flex;
  align-items: center;
  color: rgba(255, 255, 255, 0.35);
  cursor: grab;
  user-select: none;
  transition: color 0.2s ease;
}
.drag-handle:hover { color: rgba(255, 255, 255, 0.7); }
.drag-handle:active { cursor: grabbing; }

.provider-remove {
  display: inline-flex;
  align-items: center;
  color: rgba(255, 255, 255, 0.35);
  cursor: pointer;
  transition: color 0.2s ease;
}
.provider-remove:hover { color: #f87171; }

:deep(.drag-ghost) {
  opacity: 0.3;
  background: rgba(59, 130, 246, 0.12) !important;
  border: 1px dashed #3b82f6 !important;
  border-radius: 16px;
}
:deep(.drag-live) {
  transform: scale(1.04) !important;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5), 0 0 20px rgba(59, 130, 246, 0.3) !important;
  z-index: 1000 !important;
  cursor: grabbing !important;
  background: rgba(255, 255, 255, 0.08) !important;
  border-color: #3b82f6 !important;
}

.provider-add {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  margin-top: 12px;
  padding: 10px 0;
  border: 1px dashed rgba(255, 255, 255, 0.15);
  border-radius: 12px;
  background: transparent;
  color: rgba(255, 255, 255, 0.55);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
}
.provider-add:hover:not(:disabled) {
  border-color: rgba(59, 130, 246, 0.6);
  color: rgba(255, 255, 255, 0.85);
  background: rgba(59, 130, 246, 0.08);
}
.provider-add:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.provider-empty {
  padding: 24px 0;
  text-align: center;
  border: 1px dashed rgba(255, 255, 255, 0.12);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.02);
}
.provider-empty-title {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.8);
}
.provider-empty-sub {
  margin: 0;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.4);
}
```

- [ ] **Step 7: 类型检查**

Run: `cd /Users/jiangkai/project/emby-ai-manager/frontend && npx vue-tsc --build`
Expected: 无错误、退出码 0。

- [ ] **Step 8: 构建**

Run: `cd /Users/jiangkai/project/emby-ai-manager/frontend && npm run build`
Expected: `vue-tsc --build && vite build` 均成功。

- [ ] **Step 9: 提交**

```bash
git add frontend/src/components/EmbySettings.vue
git diff --cached --stat          # 确认只含本文件；diff 中应包含既有 lang="postcss" 改动
git commit -m "feat: AI 模型配置支持拖拽排序与增删（上限6个）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 端到端验证 + 回归

**Files:**
- 无仓库改动（纯验证）。
- 参考：`backend/data/config.json`（只读）、`backend/services/ai_translator.py`（只读）。

**Interfaces:**
- Consumes: Task 2 产出的前端交互；后端 `POST /api/config` 与 `AITranslator` 瀑布流日志。

- [ ] **Step 1: 重启后端并确认配置加载正常**

Run:
```bash
cd /Users/jiangkai/project/emby-ai-manager/backend && venv/bin/python main.py
```
Expected: 服务启动于 `0.0.0.0:8000`，无配置解析异常；`curl -s http://127.0.0.1:8000/api/config` 返回的 `ai_providers` 仍为当前 3 个真实 Provider 且顺序不变。

- [ ] **Step 2: 前端 dev 启动 + 手动拖拽清单**

Run: `cd /Users/jiangkai/project/emby-ai-manager/frontend && npm run dev`，浏览器打开设置页「智能服务 · AI 模型配置」。逐项核对：

- [ ] 三个卡片头部最左出现拖拽手柄 `⠿`，最右出现删除图标 `🗑`。
- [ ] 拖动"次选"卡片到首位，卡片平滑滑动换位，松手后序号徽章显示 `首选`（原次选）/ `次选`（原首选）。
- [ ] 点击卡片标题（非手柄/删除处）仍可折叠/展开，且重排后展开状态跟随卡片本身不错位。
- [ ] 点击删除图标移除卡片（`@click.stop` 不触发折叠）。
- [ ] 点「添加模型」追加第 4 个卡片（默认名"四选"，自动展开）；加到第 6 个后按钮变为 `已达上限（6 个）` 且禁用。
- [ ] 全部删除后显示空态文案与「添加模型」按钮；点按钮可重新添加。

- [ ] **Step 3: 保存并核对持久化与顺序**

- [ ] 重排后点「保存」，`ElMessage.success('配置已保存')` 出现。
- [ ] 读取 `config.json`：`ai_providers` 顺序与拖拽后一致；**每项均不含 `_dragId`**（`grep -c _dragId backend/data/config.json` 应为 0）。
- [ ] 刷新页面后顺序保持、字段完整（name / base_url / alt_base_url / api_key / model_name）。

- [ ] **Step 4: 触发一次 AI 调用，核对 Fallback 顺序日志**

在任一触发翻译的场景（如演职员中文化）观察 uvicorn 日志：`🚀 [AI翻译] 尝试 Provider[...]` 的出现顺序 = 刚保存的顺序。若首个 Provider 正常返回则只出现一个；可临时把首选 Provider 的 `base_url` 改成不可达地址，确认日志依次降级到次选、三选（验证完成后还原）。

- [ ] **Step 5: 回归现有功能**

- [ ] 现有 3 个真实 Provider 配置加载无报错，AI 功能可用。
- [ ] 增删后保存不丢失字段；空列表状态下后端 `_resolve_providers` 回退旧字段或禁用 AI（已在 Task 1 用脚本验证逻辑）。
- [ ] 前端 `npx vue-tsc --build` 与 `npm run build` 仍通过。

---

## Self-Review 记录

- **Spec 覆盖**：拖拽排序（Task 2）、增删 + 上限 6 + 序数标签（Task 2）、保存后顺序生效（Task 1 证明后端已按顺序 + Task 3 Step 3/4 端到端核对）、深色主题一致（Task 2 Step 6 样式镜像 MpConfig）、`_dragId` 不落盘（Task 2 `cleanPayload`）、后端零改动（Task 1）。设计文档「明确不做」项均未引入。
- **占位符**：无 TBD/TODO；所有代码块为完整可执行代码。
- **类型一致性**：`getProviderKey` / `isOpen` / `toggleProvider` / `addProvider` / `removeProvider` / `onDragChange` / `atCap` / `cleanPayload` 在 script 定义、template 引用，签名与使用一致；`priorityLabel(idx)` 各处一致；`AIProvider._dragId?: number` 在接口、reactive 标注、cleanPayload 剥离三处一致。
