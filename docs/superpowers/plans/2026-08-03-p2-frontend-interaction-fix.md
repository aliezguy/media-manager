# P2 前端交互修正 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复「同步选中项 (N)」按钮永不显示的 bug —— 让 `failed` / `NULL` 状态条目可被选中并批量汉化，同时让 API 层的 `sync_status` 不再返回 `null`。

**Architecture:** 前端 `pendingCheckedIds` 从「严格 `status === 'pending'`」改为「对齐后端语义 `status !== 'synced'`」（含 `failed` / `NULL` / 未审计），并补全 `failed` 状态的类型与展示（药丸标签/配色）；后端 `get_actor_items` 两处 `sync_status` 注入改为 `rec.get("status") or "pending"`，把 NULL 列归一化为 `pending`。两处改动点独立可回归，各自带测试。

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy / SQLite(:memory: 测试) / pytest / Vue3 + TypeScript (vue-tsc 验证)

## Global Constraints

- 语义对齐唯一标准：**`status !== 'synced'` 即算待汉化**（含 `failed` / `NULL` / `pending` / 未审计），来自设计文档「问题 4」决策表。
- 后端 `sinicize_selected` 行为**不改**（传什么 ID 处理什么）；已汉化(synced) 条目要强制重跑仍走「强制汉化(覆盖)」按钮。
- 不改前端 `item.sync_status || 'pending'`（line 269）—— 该行已把 NULL 兜底为 `pending`；后端归一化是让 API 契约干净，二者都保留。
- `locked` 为前端保留状态（后端源码无任何 `'locked'` 写入），`status !== 'synced'` 会将其计入待处理数，但实际数据流中永不出现，按设计文档字面实现。
- 测试运行目录：`cd backend`，venv 解释器 `venv/bin/python -m pytest`。前端验证：`cd frontend && npx vue-tsc --build`（勿用 `--noEmit`，typescript 锁 5.9.3）。

## 背景：根因定位（已在代码中验证到行号）

后端 `MediaSyncStatus.status` 实际取值：`'synced'` / `'pending'` / `'failed'` / `NULL`。

- `'failed'` 由 `douban_service.py:390` 在 Emby 回写失败时写入（`"synced" if write_ok else "failed"`）。
- `NULL` 来自从未审计/历史脏数据（`models.py:162` `status = Column(String, default="pending")`，`to_dict()` 会带出 `None`）。

bug 链条：
1. `ActorLocalizationStudio.vue:243` `pendingCheckedIds` 严格过滤 `status === 'pending'` → **`failed` 条目被排除** → 勾选后按钮仍显示「批量执行中文化」且 `disabled`，无法重试失败项。
2. 后端 `emby.py:637/682` `rec.get("status", "pending")` —— `to_dict()` 的 `status` 键恒存在（值为 None），`.get` 的默认值只在键缺失时生效 → **返回 `sync_status: null`**。

> 说明：`NULL` 条目经前端 line 269 兜底已变 `pending` 能被计数，但 API 契约层仍返回 `null`，故后端归一化与前端语义对齐两处都要改。

---

### Task 1: 后端 `sync_status` NULL 归一化（TDD）

**Files:**
- Modify: `backend/routers/emby.py:637`（status_filter 分支）与 `backend/routers/emby.py:682`（无筛选分支）
- Test: `backend/tests/test_actor_items_sync_status.py`

**Interfaces:**
- 不变更任何函数签名。只改两行：
  - `it["sync_status"] = rec.get("status", "pending")` → `it["sync_status"] = rec.get("status") or "pending"`
- 行为契约：`get_actor_items` 返回的每个 item 的 `sync_status` 字段**永不为 None**；`failed` / `synced` 原样透传，`NULL` → `"pending"`。

- [ ] **Step 1: 编写失败测试**

创建 `backend/tests/test_actor_items_sync_status.py`：
```python
"""get_actor_items sync_status 归一化测试 — NULL → 'pending'，failed 原样透传。

对应设计文档 Phase 2 改动点 2：rec.get("status", "pending") → rec.get("status") or "pending"
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import MediaSyncStatus
import routers.emby as emby


class _FakeResp:
    """模拟 requests.get 返回值。"""
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _make_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(emby, "SessionLocal", TestSession)
    return TestSession


def _seed(db):
    # s1: status 列 NULL（从未审计）；s2: failed；s3: synced
    db.add(MediaSyncStatus(emby_item_id="s1", library_id="lib1", title="A", status=None))
    db.add(MediaSyncStatus(emby_item_id="s2", library_id="lib1", title="B", status="failed"))
    db.add(MediaSyncStatus(emby_item_id="s3", library_id="lib1", title="C", status="synced"))
    db.commit()
    db.close()


def _items(ids):
    return [{"Id": i, "Name": n, "Type": "Series", "People": [], "ProviderIds": {}}
            for i, n in zip(ids, ("A", "B", "C"))]


def _req(status_filter=None, limit=-1):
    return emby.ActorItemsRequest(
        emby_host="http://emby.test", emby_api_key="k", emby_user_id="u",
        library_id="lib1", limit=limit, start_index=0,
        status_filter=status_filter, search=None,
    )


def test_status_filter_path_null_to_pending(monkeypatch):
    TestSession = _make_session(monkeypatch)
    _seed(TestSession())
    # status_filter='pending' → DB 筛出 s1(NULL) + s2(failed)，Emby 仅按这些 ID 拉取
    monkeypatch.setattr(
        emby.requests, "get",
        lambda *a, **k: _FakeResp({"Items": _items(["s1", "s2"])}),
    )

    result = emby.get_actor_items(_req(status_filter="pending"))
    status_map = {it["id"]: it["sync_status"] for it in result["items"]}

    assert status_map["s1"] == "pending"  # NULL → pending（修复前为 None）
    assert status_map["s2"] == "failed"   # failed 原样透传


def test_nofilter_path_null_to_pending(monkeypatch):
    TestSession = _make_session(monkeypatch)
    _seed(TestSession())
    monkeypatch.setattr(
        emby.requests, "get",
        lambda *a, **k: _FakeResp({"Items": _items(["s1", "s2", "s3"]), "TotalRecordCount": 3}),
    )

    result = emby.get_actor_items(_req(status_filter=None, limit=-1))
    status_map = {it["id"]: it["sync_status"] for it in result["items"]}

    assert status_map["s1"] == "pending"  # NULL → pending（修复前为 None）
    assert status_map["s2"] == "failed"
    assert status_map["s3"] == "synced"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_actor_items_sync_status.py -v`
Expected: 2 FAIL —— `assert status_map["s1"] == "pending"`，实际为 `None`（`rec.get("status", "pending")` 对键存在值为 None 时返回 None）。

- [ ] **Step 3: 实现 NULL 归一化**

在 `backend/routers/emby.py` 两处各改一行。

第一处（status_filter 分支，原 :637）：
```python
                it["sync_status"] = rec.get("status", "pending")
```
改为：
```python
                it["sync_status"] = rec.get("status") or "pending"
```

第二处（无筛选分支，原 :682）：
```python
                    it["sync_status"] = rec.get("status", "pending")
```
改为：
```python
                    it["sync_status"] = rec.get("status") or "pending"
```

> `rec.get("status") or "pending"` 同时覆盖两种情况：键缺失（`{}`）与键值为 `None`，两者都归一化为 `"pending"`。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_actor_items_sync_status.py -v`
Expected: 2 PASS。

- [ ] **Step 5: 回归现有后端测试**

Run: `cd backend && venv/bin/python -m pytest tests/ -q`
Expected: 全部 PASS（含 P1 的 3 个系列对账测试等）。

- [ ] **Step 6: 提交**

```bash
cd /Users/jiangkai/project/emby-ai-manager && git add backend/routers/emby.py backend/tests/test_actor_items_sync_status.py
git commit -m "fix: get_actor_items 的 sync_status 做 NULL 归一化（null→pending），API 契约不再返回 null"
```

---

### Task 2: 前端 `pendingCheckedIds` 语义对齐 + `failed` 展示一致性

**Files:**
- Modify: `frontend/src/components/ActorLocalizationStudio.vue`（line 27 类型、line 243 过滤、line 532-538 展示映射、style 区新增 `.pill-failed`）

**Interfaces:**
- `MediaStatus` 类型扩展为 `'pending' | 'synced' | 'locked' | 'syncing' | 'failed'`。
- `pendingCheckedIds` 语义：`checked && status !== 'synced'` 的 item id 列表。
- `statusLabel` / `statusPillClass` 新增 `failed` 分支。
- 消费方（无需改动，自动生效）：`handleBatchSync`（:284）、按钮文案与 `disabled`（:736-742）、「已选 N 个待处理项」（:733）。

- [ ] **Step 1: 扩展 `MediaStatus` 类型**

`frontend/src/components/ActorLocalizationStudio.vue` line 27：
```ts
type MediaStatus = 'pending' | 'synced' | 'locked' | 'syncing'
```
改为：
```ts
type MediaStatus = 'pending' | 'synced' | 'locked' | 'syncing' | 'failed'
```

- [ ] **Step 2: 对齐过滤语义**

line 243：
```ts
const pendingCheckedIds = computed(() => items.value.filter(i => i.checked && i.status === 'pending').map(i => i.id))
```
改为：
```ts
const pendingCheckedIds = computed(() => items.value.filter(i => i.checked && i.status !== 'synced').map(i => i.id))
```

- [ ] **Step 3: 补 `failed` 状态展示映射**

line 532 `statusLabel`：
```ts
const statusLabel = (s: MediaStatus): string => ({ pending: '未汉化', synced: '已汉化', locked: '已锁定', syncing: '汉化中' }[s] || s)
```
改为：
```ts
const statusLabel = (s: MediaStatus): string => ({ pending: '未汉化', synced: '已汉化', locked: '已锁定', syncing: '汉化中', failed: '汉化失败' }[s] || s)
```

line 533-538 `statusPillClass`：
```ts
const statusPillClass = (s: MediaStatus): string => ({
  pending: 'pill-pending',
  synced: 'pill-synced',
  locked: 'pill-locked',
  syncing: 'pill-syncing',
}[s] || 'pill-locked')
```
改为：
```ts
const statusPillClass = (s: MediaStatus): string => ({
  pending: 'pill-pending',
  synced: 'pill-synced',
  locked: 'pill-locked',
  syncing: 'pill-syncing',
  failed: 'pill-failed',
}[s] || 'pill-locked')
```

> 说明：`MediaStatus` 加入 `'failed'` 后，若两处映射不同步补 `failed` 键，`[s]` 索引会因 `s` 不在键集中触发 TS 编译错误（TS7053），故 Step 1/3 必须同一提交内完成。

- [ ] **Step 4: 新增 `.pill-failed` 样式**

在 `.pill-syncing` 样式块（约 line 1293-1299）之后、`@keyframes pulse-glow`（line 1300）之前插入：
```css
.pill-failed {
  color: #F87171;
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.26);
}
```

- [ ] **Step 5: 前端类型检查**

Run: `cd frontend && npx vue-tsc --build`
Expected: 无 TS 错误，退出码 0。

- [ ] **Step 6: 提交**

```bash
cd /Users/jiangkai/project/emby-ai-manager && git add frontend/src/components/ActorLocalizationStudio.vue
git commit -m "fix: pendingCheckedIds 对齐 status!=='synced'（含 failed/NULL），并补 failed 状态药丸展示"
```

---

### Task 3: 可选增强 — 「汉化失败」状态筛选（超出设计文档两处改动点，供 Review 决策）

> **此任务超出设计文档 Phase 2 列明的两处改动点，纯为 UX 增强：** 修复后用户能在「未汉化」筛选里看到 failed 条目并重试，但无法单独筛出 failed。此任务让筛选下拉新增「汉化失败」项。**如 Review 认为 YAGNI，直接删除本任务，不影响 Task 1/2 完整性。**

**Files:**
- Modify: `backend/routers/emby.py:596-602`（status_filter 分支）
- Modify: `frontend/src/components/ActorLocalizationStudio.vue:219-224`（statusOptions）
- Test: `backend/tests/test_actor_items_sync_status.py`（追加一个测试）

**Interfaces:**
- `status_filter='failed'` → 后端只返回 `status == 'failed'` 的项；`'pending'` 语义不变（`!= 'synced'` 或 NULL）。
- `statusOptions` 新增 `{ label: '汉化失败', value: 'failed' }`。

- [ ] **Step 1: 追加后端失败测试**

在 `backend/tests/test_actor_items_sync_status.py` 末尾追加：
```python
def test_status_filter_failed_returns_only_failed(monkeypatch):
    TestSession = _make_session(monkeypatch)
    _seed(TestSession())
    # status_filter='failed' → DB 只筛出 s2，Emby 按该 ID 拉取
    monkeypatch.setattr(
        emby.requests, "get",
        lambda *a, **k: _FakeResp({"Items": _items(["s2"])}),
    )

    result = emby.get_actor_items(_req(status_filter="failed"))
    ids = [it["id"] for it in result["items"]]

    assert ids == ["s2"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_actor_items_sync_status.py::test_status_filter_failed_returns_only_failed -v`
Expected: FAIL —— 当前 `else` 分支把 `'failed'` 当 `'pending'` 处理，返回 s1 + s2。

- [ ] **Step 3: 扩展后端 status_filter 分支**

`backend/routers/emby.py` line 596-602：
```python
            if req.status_filter == 'synced':
                base_q = base_q.filter(MediaSyncStatus.status == 'synced')
            else:
                # pending: status 为 'pending' 或 NULL（尚未审计过）
                base_q = base_q.filter(
                    (MediaSyncStatus.status != 'synced') | (MediaSyncStatus.status == None)
                )
```
改为：
```python
            if req.status_filter == 'synced':
                base_q = base_q.filter(MediaSyncStatus.status == 'synced')
            elif req.status_filter == 'failed':
                base_q = base_q.filter(MediaSyncStatus.status == 'failed')
            else:
                # pending: status 为 'pending' 或 NULL（尚未审计过）
                base_q = base_q.filter(
                    (MediaSyncStatus.status != 'synced') | (MediaSyncStatus.status == None)
                )
```

- [ ] **Step 4: 前端 statusOptions 新增选项**

`frontend/src/components/ActorLocalizationStudio.vue` line 219-224：
```ts
const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '未汉化', value: 'pending' },
  { label: '已汉化', value: 'synced' },
  { label: '已锁定', value: 'locked' },
]
```
改为：
```ts
const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '未汉化', value: 'pending' },
  { label: '已汉化', value: 'synced' },
  { label: '汉化失败', value: 'failed' },
  { label: '已锁定', value: 'locked' },
]
```

- [ ] **Step 5: 验证**

Run: `cd backend && venv/bin/python -m pytest tests/test_actor_items_sync_status.py -v`（3 PASS）
Run: `cd frontend && npx vue-tsc --build`（无错误）

- [ ] **Step 6: 提交**

```bash
cd /Users/jiangkai/project/emby-ai-manager && git add backend/routers/emby.py backend/tests/test_actor_items_sync_status.py frontend/src/components/ActorLocalizationStudio.vue
git commit -m "feat: 状态筛选支持「汉化失败」，便于定位并重试 failed 条目"
```

---

### Task 4: 端到端手工验证（设计文档要求的浏览器验证）

**Files:** 无代码改动，仅验证。

- [ ] **Step 1: 启动前后端**

```bash
# 终端 A — 后端 (port 8000)
cd /Users/jiangkai/project/emby-ai-manager/backend && ./venv/bin/python main.py

# 终端 B — 前端 (Vite dev，/api 代理到 127.0.0.1:8000)
cd /Users/jiangkai/project/emby-ai-manager/frontend && npm run dev
```

- [ ] **Step 2: 构造一条 `failed` 状态数据（复现按钮 bug 的前置条件）**

任选一个已入库的 media item，直接改库制造失败态：
```bash
cd /Users/jiangkai/project/emby-ai-manager/backend && ./venv/bin/python -c "
import sqlite3
con = sqlite3.connect('data/emby_ai.db')
# 找一个 Series 项置为 failed（替换为实际存在的 emby_item_id）
con.execute(\"UPDATE media_sync_status SET status='failed' WHERE emby_item_id=(SELECT emby_item_id FROM media_sync_status LIMIT 1)\")
con.commit()
print('done')
con.close()
"
```
> 若已存在自然产生的 failed 条目（Emby 回写失败时写入），可跳过此步。

- [ ] **Step 3: 验证按钮计数**

浏览器打开 Vite 地址（默认 http://localhost:5173）→ 选择媒体库 → 状态筛选选「未汉化」：
- 勾选一个显示「汉化失败」药丸的条目 → 顶部按钮应显示 **「同步选中项 (1)」**（修复前为「批量执行中文化」且禁用）。
- 勾选一个「未汉化」条目 → 计数累加。
- 取消勾选已汉化(synced)条目验证被排除：先勾选一个「已汉化」条目，确认按钮不计入（0 计数、显示「批量执行中文化」）。

- [ ] **Step 4: 点击按钮验证任务流**

点击「同步选中项 (N)」→ 统一汉化进度对话框打开并轮询 → 完成后列表刷新、勾选清空。

- [ ] **Step 5: 回归其它按钮**

「强制汉化(覆盖)」对任意勾选（含 synced）仍可用；「审计选中项」不受影响；分集抽屉、批量获取不回退。

---

## 风险与注意事项

1. **`locked` 被计入待处理数**：`status !== 'synced'` 含 `locked`，但后端源码无任何 `'locked'` 写入（已 grep 全量 Python 验证），实际数据流中永不出现，影响为零。若未来引入锁定语义，需改为 `!== 'synced' && !== 'locked'`。
2. **Task 3 是否纳入**：设计文档 Phase 2 仅列两处改动点，Task 3 属增强，由 Review 决定取舍。
3. **NULL 条目在「未汉化」筛选下的可见性**：后端 `status_filter='pending'` 本就返回 `status != 'synced'` 或 NULL（emby.py:600-602），故 NULL/failed 条目前端早已可见、仅不可选中；本计划修复的正是「可选中 + 可重试」。

## 自检（Self-Review）

- **Spec 覆盖**：设计文档 Phase 2 改动点 1（`:243` 过滤语义）→ Task 2；改动点 2（`:633/:678` NULL 归一化）→ Task 1；测试要求（`vue-tsc --build` + 浏览器手工验证）→ Task 2 Step 5 / Task 4。✓
- **Placeholder 扫描**：所有步骤含完整代码与命令，无 TBD/TODO。✓
- **类型一致性**：`MediaStatus` 五值联合在 Task 2 的 `statusLabel`/`statusPillClass`/`.pill-failed` 同步扩展；`statusOptions` 的 `'failed'` 值与 Task 3 后端 `status_filter='failed'` 分支值一致。✓
