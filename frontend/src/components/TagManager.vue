<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Search, Database, Check, SlidersHorizontal, Calendar,
  Loader2, Sparkles, Download, Clapperboard, Save, Plus, RefreshCw,
} from 'lucide-vue-next'

const API_URL = import.meta.env.VITE_API_URL || ''

// === 类型定义 ===
interface Config {
  emby_api_key?: string
}

interface Library {
  Id: string
  Name: string
}

interface LibraryItem {
  id: number
  name: string
  year?: number
  current_tags?: string[]
}

interface TagItem {
  id: number
  name: string
  year?: number
  current_tags?: string[]
  suggested_tags: string[]
  editing_tags: string[]
  inputVisible: boolean
  inputValue: string
  analyzing: boolean
  saving: boolean
  status: string
}

const config = reactive<Config>({})
const loading = ref(false)
const libraries = ref<Library[]>([])
const selectedLib = ref('')
const searchTerm = ref('')
const tableData = ref<TagItem[]>([])

// 筛选与分页
const filterStatus = ref('all')
const filterTag = ref('')
const filterYear = ref<string | number>('')
const currentPage = ref(1)
const pageSize = ref(50)

// 批量状态
const multipleSelection = ref<TagItem[]>([])
const isBatchRunning = ref(false)
const currentBatchAction = ref('')
const batchProgress = reactive({ total: 0, finished: 0, success: 0, fail: 0 })

onMounted(async () => {
  try {
    const res = await axios.get(`${API_URL}/api/config`)
    Object.assign(config, res.data)
    if(config.emby_api_key) connectEmby(true)
  } catch(e) {}
})

// === 计算属性 ===
const uniqueTags = computed(() => {
  const tags = new Set<string>()
  tableData.value.forEach(item => {
    if (item.current_tags) item.current_tags.forEach(t => tags.add(t))
    if (item.suggested_tags) item.suggested_tags.forEach(t => tags.add(t))
  })
  return Array.from(tags).sort()
})

const uniqueYears = computed(() => {
  const years = new Set<number>()
  tableData.value.forEach(item => { if (item.year) years.add(item.year) })
  return Array.from(years).sort((a, b) => b - a)
})

const filteredTableData = computed(() => {
  let data = tableData.value
  if (filterYear.value) data = data.filter(i => i.year == filterYear.value)
  if (filterStatus.value === 'yes') data = data.filter(i => i.current_tags && i.current_tags.length > 0)
  if (filterStatus.value === 'no') data = data.filter(i => !i.current_tags || i.current_tags.length === 0)
  if (filterTag.value) data = data.filter(i => i.current_tags && i.current_tags.includes(filterTag.value))
  return data
})

const pagedTableData = computed(() => {
  if (pageSize.value >= filteredTableData.value.length) return filteredTableData.value
  const start = (currentPage.value - 1) * pageSize.value
  return filteredTableData.value.slice(start, start + pageSize.value)
})

// === 选择逻辑 ===
const toggleSelect = (row: TagItem) => {
  if (isBatchRunning.value) return
  const idx = multipleSelection.value.findIndex(i => i.id === row.id)
  if (idx === -1) {
    multipleSelection.value.push(row)
  } else {
    multipleSelection.value.splice(idx, 1)
  }
}

const isSelected = (row: TagItem) => {
  return multipleSelection.value.some(i => i.id === row.id)
}

// === 方法 ===
const connectEmby = async (silent: boolean = false) => {
  try {
    const res = await axios.post(`${API_URL}/api/libraries`, config)
    libraries.value = res.data
    if(!silent) ElMessage.success('已连接 Emby')
  } catch (e) { if(!silent) ElMessage.error('连接失败: ' + (e instanceof Error ? e.message : String(e))) }
}

const loadItems = async (loadAll: boolean) => {
  if(!selectedLib.value) return ElMessage.warning('请先选择媒体库')
  loading.value = true; tableData.value = []
  currentPage.value = 1; multipleSelection.value = []
  try {
    const res = await axios.post(`${API_URL}/api/library_items`, {
      ...config, library_id: selectedLib.value, limit: loadAll ? -1 : 50
    })
    processData(res.data.items)
    ElMessage.success(`已加载 ${res.data.items.length} 条数据`)
  } catch (e) { ElMessage.error(e instanceof Error ? e.message : String(e)) }
  finally { loading.value = false }
}

const searchItems = async () => {
  if(!searchTerm.value) return
  loading.value = true; tableData.value = []; currentPage.value = 1; multipleSelection.value = []
  try {
    const res = await axios.post(`${API_URL}/api/search_items`, { ...config, search_term: searchTerm.value })
    processData(res.data.items)
    if(res.data.items.length === 0) ElMessage.info('未找到相关内容')
  } catch (e) { ElMessage.error(e instanceof Error ? e.message : String(e)) }
  finally { loading.value = false }
}

const processData = (items: LibraryItem[]) => {
  tableData.value = items.map(item => ({
    ...item,
    editing_tags: [...(item.current_tags || [])],
    suggested_tags: [] as string[],
    inputVisible: false, inputValue: '',
    analyzing: false, saving: false, status: ''
  }))
}

// 标签与AI逻辑
const removeTag = (row: TagItem, tag: string) => { row.editing_tags = row.editing_tags.filter(t => t !== tag) }
const addTagInput = (row: TagItem) => {
  if (row.inputValue && !row.editing_tags.includes(row.inputValue)) row.editing_tags.push(row.inputValue)
  row.inputVisible = false; row.inputValue = ''
}
const acceptAiTag = (row: TagItem, tag: string) => { if (!row.editing_tags.includes(tag)) row.editing_tags.push(tag) }
const acceptAllAi = (row: TagItem) => { if(row.suggested_tags) row.suggested_tags.forEach(t => acceptAiTag(row, t)) }

const generateAI = async (row: TagItem, force = false) => {
  row.analyzing = true
  try {
    const res = await axios.post(`${API_URL}/api/ai_single`, { ...config, item_id: row.id, force_refresh: force })
    row.suggested_tags = res.data.suggested_tags
    row.status = res.data.source === 'database' ? '⚡️缓存' : '✅生成'
  } catch (e) { row.status = '❌失败' } finally { row.analyzing = false }
}

const saveTags = async (row: TagItem) => {
  row.saving = true
  try {
    const res = await axios.post(`${API_URL}/api/save_tags`, {
      ...config, item_id: row.id, tags: row.editing_tags, overwrite: true
    })
    row.current_tags = [...res.data.tags]; row.editing_tags = [...res.data.tags]; row.status = '💾已存'
  } catch (e) { row.status = '❌错误' } finally { row.saving = false }
}

// 批量逻辑
const chunkArray = (arr: TagItem[], size: number) => Array.from({ length: Math.ceil(arr.length / size) }, (_, i) => arr.slice(i * size, i * size + size))

const runBatchQueue = async (tasks: TagItem[][], taskFn: (chunk: TagItem[]) => Promise<number>, maxConcurrent: number, actionName: string) => {
  isBatchRunning.value = true; currentBatchAction.value = actionName
  batchProgress.total = multipleSelection.value.length; batchProgress.finished = 0; batchProgress.success = 0; batchProgress.fail = 0
  const queue = [...tasks]
  const next = async () => {
    if (queue.length === 0) return
    const chunk = queue.shift() as TagItem[]
    try { const c = await taskFn(chunk); batchProgress.success += c } catch { batchProgress.fail += chunk.length }
    finally { batchProgress.finished += chunk.length; if (isBatchRunning.value) await next() }
  }
  await Promise.all(Array.from({ length: Math.min(tasks.length, maxConcurrent) }, () => next()))
  isBatchRunning.value = false
  ElMessage[batchProgress.fail === 0 ? 'success' : 'warning'](`${actionName} 完成`)
  if(batchProgress.fail === 0) multipleSelection.value = []
}

const batchAnalyze = async () => {
  if (!multipleSelection.value.length) return ElMessage.warning('请先勾选')
  await ElMessageBox.confirm(`选中 ${multipleSelection.value.length} 部，开始 AI 分析？`, '提示', { confirmButtonText: '开始' })
  const task = async (chunk: TagItem[]) => {
    const res = await axios.post(`${API_URL}/api/ai_batch`, { ...config, item_ids: chunk.map(i=>i.id) })
    chunk.forEach(r => { if(res.data.results[r.id]) { r.suggested_tags = res.data.results[r.id]; r.status = '✅批量' } })
    return chunk.length
  }
  runBatchQueue(chunkArray(multipleSelection.value, 4), task, 4, '批量AI')
}

const batchSave = async () => {
  if (!multipleSelection.value.length) return ElMessage.warning('请先勾选')
  await ElMessageBox.confirm(`确定写入 ${multipleSelection.value.length} 部？`, '提示', { confirmButtonText: '写入' })
  const task = async (row: TagItem) => {
    if (row.suggested_tags.length) acceptAllAi(row)
    await saveTags(row); return 1
  }
  runBatchQueue(multipleSelection.value.map(r=>[r]), async(c)=>await task(c[0]), 2, '批量写入')
}

const stopBatch = () => { isBatchRunning.value = false; ElMessage.info('停止中...') }
</script>

<template>
  <div class="manager-container">
    <!-- ==================== 顶部全息控制台 ==================== -->
    <div class="hud-header">
      <!-- 媒体库组 -->
      <div class="tool-group">
        <span class="tool-label"><Database class="w-3.5 h-3.5" /> 媒体库</span>
        <el-select
          v-model="selectedLib"
          placeholder="请选择库"
          style="width: 150px"
          :disabled="isBatchRunning"
        >
          <el-option v-for="l in libraries" :key="l.Id" :label="l.Name" :value="l.Id" />
        </el-select>
        <button class="ghost-btn" @click="loadItems(false)" :disabled="isBatchRunning">
          <Database />
          加载50条
        </button>
        <button class="ghost-btn soft" @click="loadItems(true)" :disabled="loading || isBatchRunning">
          <Loader2 v-if="loading" class="animate-spin" />
          <Check v-else />
          全部
        </button>
      </div>

      <!-- 筛选组 -->
      <div class="tool-group">
        <span class="tool-label"><SlidersHorizontal class="w-3.5 h-3.5" /> 筛选</span>
        <el-select v-model="filterStatus" style="width: 100px" :disabled="isBatchRunning">
          <el-option label="全部" value="all" />
          <el-option label="无标签" value="no" />
          <el-option label="有标签" value="yes" />
        </el-select>
        <el-select v-model="filterYear" filterable clearable placeholder="年份" style="width: 110px" :disabled="isBatchRunning">
          <template #prefix><Calendar class="w-3.5 h-3.5" /></template>
          <el-option v-for="y in uniqueYears" :key="y" :label="y" :value="y" />
        </el-select>
        <el-select v-model="filterTag" filterable clearable placeholder="搜标签" style="width: 140px" :disabled="isBatchRunning">
          <el-option v-for="t in uniqueTags" :key="t" :label="t" :value="t" />
        </el-select>
      </div>

      <!-- 搜索 -->
      <div class="tool-group search-group">
        <div class="search-box">
          <Search class="search-icon" />
          <input
            v-model="searchTerm"
            type="text"
            placeholder="搜索剧名..."
            class="search-input"
            :disabled="isBatchRunning"
            @keyup.enter="searchItems"
          />
        </div>
      </div>
    </div>

    <!-- ==================== 批量操作全息条 ==================== -->
    <transition name="el-zoom-in-top">
      <div v-if="multipleSelection.length > 0 || isBatchRunning" class="batch-bar">
        <div class="batch-bar-inner">
          <div v-if="!isBatchRunning" class="batch-info">
            <Check class="w-4 h-4 text-blue-400" />
            <span>已选 <b>{{ multipleSelection.length }}</b> 项</span>
          </div>
          <div v-else class="batch-info running">
            <Loader2 class="w-4 h-4 animate-spin" />
            <span>{{ currentBatchAction }} — {{ batchProgress.finished }}/{{ batchProgress.total }}</span>
            <span class="batch-counts">✅{{ batchProgress.success }} ❌{{ batchProgress.fail }}</span>
          </div>
          <div class="batch-btns">
            <button v-if="isBatchRunning" class="hud-btn hud-btn-red" @click="stopBatch">停止</button>
            <template v-else>
              <button class="ai-btn" @click="batchAnalyze">
                <Sparkles class="w-3.5 h-3.5" /> AI 分析
              </button>
              <button class="hud-btn hud-btn-green" @click="batchSave">
                <Download class="w-3.5 h-3.5" /> 写入 Emby
              </button>
            </template>
          </div>
        </div>
        <div v-if="isBatchRunning" class="batch-progress-track">
          <div
            class="batch-progress-fill"
            :style="{ width: Math.round((batchProgress.finished / batchProgress.total) * 100) + '%' }"
          ></div>
        </div>
      </div>
    </transition>

    <!-- ==================== 情报卡片列表 ==================== -->
    <div v-loading="loading" class="card-list">
      <div
        v-for="row in pagedTableData"
        :key="row.id"
        class="tag-card"
        :class="{ selected: isSelected(row) }"
      >
        <!-- 头部行：Checkbox + 标题 + 年份 + 状态 -->
        <div class="card-header-row">
          <div
            class="card-check"
            :class="{ checked: isSelected(row) }"
            @click.stop="toggleSelect(row)"
          >
            <Check v-if="isSelected(row)" class="w-3 h-3 text-white" :stroke-width="3" />
          </div>
          <span class="card-title">{{ row.name }}</span>
          <span class="card-year">{{ row.year }}</span>
          <span
            v-if="row.status"
            class="card-status"
            :class="{ done: row.status.includes('存') || row.status.includes('批量') }"
          >{{ row.status }}</span>
        </div>

        <!-- 标签药丸 -->
        <div class="card-tags-row">
          <span class="tags-label">标签</span>
          <span v-for="tag in row.editing_tags" :key="tag" class="tag-chip">
            {{ tag }}
            <span class="tag-close" @click.stop="removeTag(row, tag)">&times;</span>
          </span>
          <el-input
            v-if="row.inputVisible"
            v-model="row.inputValue"
            size="small"
            class="tag-inline-input"
            @blur="addTagInput(row)"
            @keyup.enter="addTagInput(row)"
          />
          <button
            v-else
            class="tag-add-btn"
            @click.stop="row.inputVisible = true"
            :disabled="isBatchRunning"
          >
            <Plus />
          </button>
        </div>

        <!-- AI 建议 -->
        <div v-if="row.suggested_tags && row.suggested_tags.length" class="card-ai-row">
          <span class="ai-label"><Sparkles class="w-3 h-3" /> AI 建议</span>
          <span
            v-for="tag in row.suggested_tags"
            :key="tag"
            class="tag-chip tag-ai"
            @click.stop="acceptAiTag(row, tag)"
          >+ {{ tag }}</span>
          <button class="ai-link" @click.stop="acceptAllAi(row)">全收</button>
        </div>

        <!-- 底部操作区 -->
        <div class="card-actions">
          <button
            v-if="!row.suggested_tags || !row.suggested_tags.length"
            class="ai-btn"
            :disabled="row.analyzing || isBatchRunning"
            @click.stop="generateAI(row, false)"
          >
            <Loader2 v-if="row.analyzing" class="w-3.5 h-3.5 animate-spin" />
            <Sparkles v-else class="w-3.5 h-3.5" />
            {{ row.analyzing ? '分析中…' : 'AI 分析' }}
          </button>
          <span v-else class="ai-suggested-hint">
            <Sparkles class="w-3 h-3 text-indigo-400" />
            {{ row.suggested_tags.length }} 条建议
            <button
              class="ai-link ai-recalc"
              :disabled="row.analyzing || isBatchRunning"
              @click.stop="generateAI(row, true)"
            >
              <RefreshCw class="w-3 h-3" /> 重算
            </button>
          </span>
          <button
            class="save-btn"
            :disabled="row.saving || isBatchRunning"
            @click.stop="saveTags(row)"
          >
            <Loader2 v-if="row.saving" class="w-3.5 h-3.5 animate-spin" />
            <template v-else><Save class="w-3.5 h-3.5" /> 保存</template>
          </button>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-if="!loading && !tableData.length" class="empty-state">
        <div class="empty-icon-circle"><Clapperboard class="w-8 h-8" /></div>
        <p class="empty-title">{{ selectedLib ? '暂无数据' : '未选择媒体库' }}</p>
        <p class="empty-desc">{{ selectedLib ? '请加载数据或尝试搜索' : '请先选择媒体库并加载数据' }}</p>
      </div>
    </div>

    <!-- ==================== 分页 ==================== -->
    <div v-if="filteredTableData.length > 0" class="pagination-bar">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="[50, 100, 200]"
        layout="total, sizes, prev, pager, next"
        :total="filteredTableData.length"
        background
        small
      />
    </div>
  </div>
</template>

<style scoped lang="postcss">
/* ==================== 全息情报终端容器 ==================== */
.manager-container {
  @apply flex flex-col gap-3 h-full p-3 md:p-4;
}

/* ==================== 顶部全息控制台 ==================== */
.hud-header {
  @apply sticky top-0 z-30 flex flex-wrap items-center gap-3 p-4 bg-[#0B1120]/80 backdrop-blur-xl border-b border-white/10 flex-shrink-0;
}

.tool-group {
  @apply flex items-center gap-2.5;
}

.tool-label {
  @apply flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400 whitespace-nowrap;
}

.search-group {
  @apply ml-auto;
}

/* 高科技幽灵主按钮 —— “加载50条” */
.ghost-btn {
  @apply inline-flex items-center gap-2 h-9 px-4 rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/30 hover:bg-blue-500/20 hover:shadow-[0_0_15px_rgba(59,130,246,0.2)] transition-all duration-200 text-sm font-medium cursor-pointer whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed;
}
.ghost-btn svg {
  @apply w-4 h-4;
}
.ghost-btn.soft {
  @apply bg-white/[0.03] text-slate-300 border-white/10 hover:bg-white/[0.06] hover:text-white hover:border-white/20 hover:shadow-none;
}

/* 搜索框 —— 暗色半透明 + 聚焦蓝光 */
.search-box {
  @apply relative flex items-center h-9 w-56;
}
.search-icon {
  @apply absolute left-3 w-4 h-4 text-slate-500 pointer-events-none;
}
.search-input {
  @apply w-full h-9 pl-9 pr-3 rounded-lg bg-white/[0.03] border border-white/10 text-white text-sm font-mono outline-none transition-all duration-200 placeholder:text-slate-600 disabled:opacity-50;
}
.search-input:focus {
  @apply border-blue-400/60 bg-blue-500/[0.06];
  box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.5), 0 0 12px rgba(59, 130, 246, 0.25);
}

/* el-select 统一 h-9 暗色触发头 + 聚焦蓝光 */
.hud-header :deep(.el-select__wrapper) {
  @apply h-9 rounded-lg bg-white/[0.03] transition-all duration-200;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.09);
}
.hud-header :deep(.el-select__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.22);
}
.hud-header :deep(.el-select__wrapper.is-focused) {
  @apply bg-blue-500/[0.06];
  box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.6), 0 0 12px rgba(59, 130, 246, 0.3);
}
.hud-header :deep(.el-select__placeholder) {
  @apply text-slate-500;
}
.hud-header :deep(.el-select__selected-item) {
  @apply text-slate-200 text-sm;
}
.hud-header :deep(.el-select__caret),
.hud-header :deep(.el-select__prefix) {
  @apply text-slate-500;
}

/* ==================== 批量操作全息条 ==================== */
.batch-bar {
  @apply relative flex flex-col gap-2.5 px-4 py-3 rounded-xl bg-[#0B1120]/80 backdrop-blur-xl border border-blue-500/30 flex-shrink-0;
  box-shadow: 0 0 24px rgba(59, 130, 246, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.05);
}
.batch-bar::before {
  content: '';
  @apply absolute left-3 top-2 bottom-2 w-px bg-gradient-to-b from-transparent via-blue-400/60 to-transparent pointer-events-none;
}

.batch-bar-inner {
  @apply flex items-center justify-between gap-3;
}

.batch-info {
  @apply flex items-center gap-2 text-[13px] text-slate-300 font-mono;
}
.batch-info b {
  @apply text-blue-400 font-semibold;
}
.batch-info.running {
  @apply text-yellow-400/90 font-medium;
}
.batch-counts {
  @apply text-slate-400;
}

.batch-btns {
  @apply flex items-center gap-2;
}

/* 通用 HUD 幽灵按钮 */
.hud-btn {
  @apply inline-flex items-center gap-1.5 h-8 px-3.5 rounded-lg text-xs font-medium border transition-all duration-200 cursor-pointer whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed;
}
.hud-btn svg {
  @apply w-3.5 h-3.5;
}
.hud-btn-green {
  @apply bg-emerald-500/10 text-emerald-400 border-emerald-500/25 hover:bg-emerald-500/20 hover:text-emerald-300 hover:shadow-[0_0_12px_rgba(16,185,129,0.25)];
}
.hud-btn-red {
  @apply bg-red-500/10 text-red-400 border-red-500/25 hover:bg-red-500/20 hover:text-red-300 hover:shadow-[0_0_12px_rgba(239,68,68,0.25)];
}

/* AI 专属按钮 —— 靛蓝 · Sparkles */
.ai-btn {
  @apply inline-flex items-center gap-1.5 text-xs font-medium text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 px-3 py-1.5 rounded-lg hover:bg-indigo-500/20 hover:text-indigo-300 hover:shadow-[0_0_10px_rgba(99,102,241,0.3)] transition-all duration-200 cursor-pointer whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed;
}
.ai-btn svg {
  @apply w-3.5 h-3.5;
}

/* 批量进度条 —— 霓虹渐变 */
.batch-progress-track {
  @apply w-full h-1 bg-white/5 rounded-full overflow-hidden;
}
.batch-progress-fill {
  @apply h-full rounded-full transition-all duration-300;
  background: linear-gradient(90deg, #3b82f6, #8b5cf6, #06b6d4);
  box-shadow: 0 0 8px rgba(59, 130, 246, 0.6);
}

/* ==================== 情报卡片列表 ==================== */
.card-list {
  @apply flex-1 overflow-y-auto flex flex-col py-2 pr-1;
}
.card-list :deep(.el-loading-mask) {
  @apply bg-[#0B1120]/50;
  backdrop-filter: blur(2px);
}

/* 毛玻璃悬浮卡片 */
.tag-card {
  @apply flex flex-col gap-3 p-4 mb-3 bg-white/[0.02] border border-white/5 rounded-xl hover:bg-white/[0.04] hover:border-blue-500/30 hover:shadow-lg transition-all duration-300;
}
.tag-card.selected {
  @apply bg-blue-500/[0.06] border-blue-400/40;
  box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.4), 0 0 20px rgba(59, 130, 246, 0.15);
}

/* 头部行 */
.card-header-row {
  @apply flex items-center gap-2.5;
}

/* Checkbox —— 垂直居中 */
.card-check {
  @apply w-5 h-5 rounded-md border-2 border-slate-600 flex items-center justify-center cursor-pointer transition-all duration-200 flex-shrink-0;
}
.card-check:hover {
  @apply border-blue-400/70;
  box-shadow: 0 0 8px rgba(59, 130, 246, 0.25);
}
.card-check.checked {
  @apply bg-blue-500/80 border-blue-400;
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.5);
}

.card-title {
  @apply text-white text-[15px] font-bold tracking-wide;
}
.card-year {
  @apply text-slate-500 text-xs font-mono ml-3;
}
.card-status {
  @apply ml-auto text-[10px] font-mono tracking-wider px-2 py-0.5 rounded-md text-blue-300 bg-blue-500/10 border border-blue-500/20 whitespace-nowrap;
}
.card-status.done {
  @apply text-emerald-300 bg-emerald-500/10 border-emerald-500/20;
}

/* 标签区 */
.card-tags-row {
  @apply flex items-center gap-2 flex-wrap;
}
.tags-label {
  @apply text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500;
}

/* 微发光数据药丸 */
.tag-chip {
  @apply inline-flex items-center px-2.5 py-1 rounded-md bg-blue-900/20 text-blue-300 border border-blue-500/30 text-xs font-mono transition-colors hover:bg-blue-800/40 hover:border-blue-400;
}
.tag-close {
  @apply ml-1.5 cursor-pointer text-sm leading-none opacity-60 transition-opacity hover:opacity-100 hover:text-red-400;
}

/* 添加标签 —— 虚线幽灵 */
.tag-add-btn {
  @apply inline-flex items-center justify-center w-6 h-6 rounded-md border border-dashed border-white/20 text-slate-400 hover:text-white hover:border-white/50 cursor-pointer transition-all duration-200 disabled:opacity-40;
}
.tag-add-btn svg {
  @apply w-3.5 h-3.5;
}

/* 行内添加标签输入框 */
.tag-inline-input {
  @apply w-24;
}
.card-tags-row :deep(.el-input__wrapper) {
  @apply rounded-md bg-white/[0.03] transition-all duration-200;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.1);
}
.card-tags-row :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.6), 0 0 10px rgba(59, 130, 246, 0.3);
}
.card-tags-row :deep(.el-input__inner) {
  @apply text-white text-xs font-mono;
}

/* AI 建议区 */
.card-ai-row {
  @apply flex items-center gap-2 flex-wrap;
}
.ai-label {
  @apply inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-[0.18em] text-purple-400;
}
.tag-ai {
  @apply inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-purple-500/10 text-purple-300 border border-purple-500/25 text-xs font-mono cursor-pointer transition-all duration-200 hover:bg-purple-500/20 hover:border-purple-400 hover:shadow-[0_0_8px_rgba(139,92,246,0.3)];
}
.ai-link {
  @apply inline-flex items-center gap-1 text-xs font-medium text-purple-400 bg-transparent border-none p-0 font-mono cursor-pointer transition-colors hover:text-purple-300 disabled:opacity-40 disabled:cursor-not-allowed;
}
.ai-suggested-hint {
  @apply inline-flex items-center gap-1.5 text-xs text-indigo-300/80 font-mono;
}
.ai-recalc {
  @apply text-slate-500 hover:text-yellow-400;
}

/* 底部操作区 */
.card-actions {
  @apply flex items-center justify-between mt-1 pt-3 border-t border-white/5;
}

/* 极客保存按钮 */
.save-btn {
  @apply inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-300 bg-emerald-500/10 border border-emerald-500/25 px-3.5 py-1.5 rounded-lg hover:bg-emerald-500/20 hover:text-emerald-200 hover:shadow-[0_0_10px_rgba(16,185,129,0.25)] transition-all duration-200 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed;
}
.save-btn svg {
  @apply w-3.5 h-3.5;
}

/* ==================== 空状态 ==================== */
.empty-state {
  @apply flex flex-col items-center justify-center py-16 text-center;
}
.empty-icon-circle {
  @apply w-16 h-16 rounded-2xl bg-white/[0.03] border border-white/10 flex items-center justify-center text-slate-500 mb-4;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
}
.empty-title {
  @apply text-slate-300 text-base font-semibold mb-1;
}
.empty-desc {
  @apply text-slate-600 text-[13px] font-mono;
}

/* ==================== 分页 ==================== */
.pagination-bar {
  @apply flex justify-center pt-3 pb-1 flex-shrink-0;
}
.pagination-bar :deep(.el-pagination) {
  --el-pagination-bg-color: rgba(255, 255, 255, 0.05);
  --el-pagination-button-bg-color: rgba(255, 255, 255, 0.05);
  --el-pagination-hover-color: #60a5fa;
  --el-pagination-text-color: #94a3b8;
}
.pagination-bar :deep(.el-pagination__total) {
  @apply text-slate-500 font-mono text-xs;
}
.pagination-bar :deep(.el-pager li) {
  @apply text-slate-400 rounded-lg;
  background: rgba(255, 255, 255, 0.05);
  margin: 0 2px;
}
.pagination-bar :deep(.el-pager li.is-active) {
  @apply text-white bg-blue-500;
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.5);
}
.pagination-bar :deep(.el-pagination .btn-prev),
.pagination-bar :deep(.el-pagination .btn-next) {
  @apply text-slate-400 rounded-lg;
  background: rgba(255, 255, 255, 0.05);
}
.pagination-bar :deep(.el-pagination .btn-prev:hover:not(:disabled)),
.pagination-bar :deep(.el-pagination .btn-next:hover:not(:disabled)) {
  @apply text-blue-300;
  box-shadow: 0 0 8px rgba(59, 130, 246, 0.2);
}
.pagination-bar :deep(.el-pagination .btn-prev:disabled),
.pagination-bar :deep(.el-pagination .btn-next:disabled) {
  @apply opacity-40;
}
.pagination-bar :deep(.el-pagination .el-select__wrapper) {
  @apply h-8 rounded-lg bg-white/[0.03] transition-all;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.09);
}
.pagination-bar :deep(.el-pagination .el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.6), 0 0 10px rgba(59, 130, 246, 0.3);
}
.pagination-bar :deep(.el-pagination .el-select__selected-item) {
  @apply text-slate-200 text-xs;
}

/* ==================== 下拉弹层 —— 全息暗色（popper 被传送到 body，需全局） ==================== */
:global(.el-select__popper) {
  --el-select-dropdown-bg-color: #0b1120;
}
:global(.el-select__popper .el-select-dropdown) {
  background: #0b1120;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.6), 0 0 20px rgba(59, 130, 246, 0.08);
  overflow: hidden;
}
:global(.el-select__popper .el-select-dropdown__item) {
  color: #94a3b8;
}
:global(.el-select__popper .el-select-dropdown__item.hover),
:global(.el-select__popper .el-select-dropdown__item:hover) {
  background: rgba(59, 130, 246, 0.12);
  color: #93c5fd;
}
:global(.el-select__popper .el-select-dropdown__item.is-selected) {
  color: #60a5fa;
  font-weight: 600;
}
:global(.el-select__popper .el-select-dropdown__item.is-disabled) {
  color: #475569;
}

/* ==================== 移动端 ==================== */
@media screen and (max-width: 768px) {
  .manager-container {
    @apply p-2 gap-2;
  }
  .hud-header {
    @apply flex-col items-stretch;
  }
  .tool-group {
    @apply flex-wrap;
  }
  .search-group {
    @apply ml-0 w-full;
  }
  .search-box {
    @apply w-full;
  }
  .tag-card {
    @apply p-3;
  }
  .batch-bar-inner {
    @apply flex-col items-start gap-2;
  }
  .batch-btns {
    @apply w-full;
  }
  .batch-btns .ai-btn,
  .batch-btns .hud-btn {
    @apply flex-1 justify-center;
  }
}
</style>
