<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, VideoPlay, MagicStick, Check, Close, Plus, Filter, Download, Calendar, Loading } from '@element-plus/icons-vue'

const API_URL = ''

const config = reactive({})
const loading = ref(false)
const libraries = ref([])
const selectedLib = ref('')
const searchTerm = ref('')
const tableData = ref([])

// 筛选与分页
const filterStatus = ref('all')
const filterTag = ref('')
const filterYear = ref('')
const currentPage = ref(1)
const pageSize = ref(50)

// 批量状态
const multipleSelection = ref([])
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
  const tags = new Set()
  tableData.value.forEach(item => {
    if (item.current_tags) item.current_tags.forEach(t => tags.add(t))
    if (item.suggested_tags) item.suggested_tags.forEach(t => tags.add(t))
  })
  return Array.from(tags).sort()
})

const uniqueYears = computed(() => {
  const years = new Set()
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
const toggleSelect = (row) => {
  if (isBatchRunning.value) return
  const idx = multipleSelection.value.findIndex(i => i.id === row.id)
  if (idx === -1) {
    multipleSelection.value.push(row)
  } else {
    multipleSelection.value.splice(idx, 1)
  }
}

const isSelected = (row) => {
  return multipleSelection.value.some(i => i.id === row.id)
}

// === 方法 ===
const connectEmby = async (silent=false) => {
  try {
    const res = await axios.post(`${API_URL}/api/libraries`, config)
    libraries.value = res.data
    if(!silent) ElMessage.success('已连接 Emby')
  } catch (e) { if(!silent) ElMessage.error('连接失败: ' + e.message) }
}

const loadItems = async (loadAll) => {
  if(!selectedLib.value) return ElMessage.warning('请先选择媒体库')
  loading.value = true; tableData.value = []
  currentPage.value = 1; multipleSelection.value = []
  try {
    const res = await axios.post(`${API_URL}/api/library_items`, {
      ...config, library_id: selectedLib.value, limit: loadAll ? -1 : 50
    })
    processData(res.data.items)
    ElMessage.success(`已加载 ${res.data.items.length} 条数据`)
  } catch (e) { ElMessage.error(e.message) }
  finally { loading.value = false }
}

const searchItems = async () => {
  if(!searchTerm.value) return
  loading.value = true; tableData.value = []; currentPage.value = 1; multipleSelection.value = []
  try {
    const res = await axios.post(`${API_URL}/api/search_items`, { ...config, search_term: searchTerm.value })
    processData(res.data.items)
    if(res.data.items.length === 0) ElMessage.info('未找到相关内容')
  } catch (e) { ElMessage.error(e.message) }
  finally { loading.value = false }
}

const processData = (items) => {
  tableData.value = items.map(item => ({
    ...item,
    editing_tags: [...(item.current_tags || [])],
    suggested_tags: [],
    inputVisible: false, inputValue: '',
    analyzing: false, saving: false, status: ''
  }))
}

// 标签与AI逻辑
const removeTag = (row, tag) => { row.editing_tags = row.editing_tags.filter(t => t !== tag) }
const addTagInput = (row) => {
  if (row.inputValue && !row.editing_tags.includes(row.inputValue)) row.editing_tags.push(row.inputValue)
  row.inputVisible = false; row.inputValue = ''
}
const acceptAiTag = (row, tag) => { if (!row.editing_tags.includes(tag)) row.editing_tags.push(tag) }
const acceptAllAi = (row) => { if(row.suggested_tags) row.suggested_tags.forEach(t => acceptAiTag(row, t)) }

const generateAI = async (row, force = false) => {
  row.analyzing = true
  try {
    const res = await axios.post(`${API_URL}/api/ai_single`, { ...config, item_id: row.id, force_refresh: force })
    row.suggested_tags = res.data.suggested_tags
    row.status = res.data.source === 'database' ? '⚡️缓存' : '✅生成'
  } catch (e) { row.status = '❌失败' } finally { row.analyzing = false }
}

const saveTags = async (row) => {
  row.saving = true
  try {
    const res = await axios.post(`${API_URL}/api/save_tags`, {
      ...config, item_id: row.id, tags: row.editing_tags, overwrite: true
    })
    row.current_tags = [...res.data.tags]; row.editing_tags = [...res.data.tags]; row.status = '💾已存'
  } catch (e) { row.status = '❌错误' } finally { row.saving = false }
}

// 批量逻辑
const chunkArray = (arr, size) => Array.from({ length: Math.ceil(arr.length / size) }, (v, i) => arr.slice(i * size, i * size + size))

const runBatchQueue = async (tasks, taskFn, maxConcurrent, actionName) => {
  isBatchRunning.value = true; currentBatchAction.value = actionName
  batchProgress.total = multipleSelection.value.length; batchProgress.finished = 0; batchProgress.success = 0; batchProgress.fail = 0
  const queue = [...tasks]
  const next = async () => {
    if (queue.length === 0) return
    const chunk = queue.shift()
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
  const task = async (chunk) => {
    const res = await axios.post(`${API_URL}/api/ai_batch`, { ...config, item_ids: chunk.map(i=>i.id) })
    chunk.forEach(r => { if(res.data.results[r.id]) { r.suggested_tags = res.data.results[r.id]; r.status = '✅批量' } })
    return chunk.length
  }
  runBatchQueue(chunkArray(multipleSelection.value, 4), task, 4, '批量AI')
}

const batchSave = async () => {
  if (!multipleSelection.value.length) return ElMessage.warning('请先勾选')
  await ElMessageBox.confirm(`确定写入 ${multipleSelection.value.length} 部？`, '提示', { confirmButtonText: '写入' })
  const task = async (row) => {
    if (row.suggested_tags.length) acceptAllAi(row)
    await saveTags(row); return 1
  }
  runBatchQueue(multipleSelection.value.map(r=>[r]), async(c)=>await task(c[0]), 2, '批量写入')
}

const stopBatch = () => { isBatchRunning.value = false; ElMessage.info('停止中...') }
</script>

<template>
  <div class="manager-container">
    <!-- ==================== Toolbar ==================== -->
    <div class="toolbar">
      <div class="tool-group">
        <span class="tool-label">📚 媒体库</span>
        <el-select v-model="selectedLib" placeholder="请选择库" style="width:140px" :disabled="isBatchRunning">
          <el-option v-for="l in libraries" :key="l.Id" :label="l.Name" :value="l.Id"/>
        </el-select>
        <button class="btn-pill btn-pill-blue" @click="loadItems(false)" :disabled="isBatchRunning">
          <el-icon :size="14"><VideoPlay /></el-icon> 加载50条
        </button>
        <button class="btn-pill btn-pill-outline" @click="loadItems(true)" :disabled="loading || isBatchRunning">
          <el-icon v-if="loading" :size="14" class="is-loading"><Loading /></el-icon>
          <el-icon v-else :size="14"><Check /></el-icon> 全部
        </button>
      </div>

      <div class="tool-group">
        <span class="tool-label"><el-icon :size="14"><Filter /></el-icon> 筛选</span>
        <el-select v-model="filterStatus" style="width:100px" :disabled="isBatchRunning">
          <el-option label="全部" value="all"/><el-option label="无标签" value="no"/><el-option label="有标签" value="yes"/>
        </el-select>
        <el-select v-model="filterYear" filterable clearable placeholder="年份" style="width:100px" :disabled="isBatchRunning">
          <template #prefix><el-icon><Calendar /></el-icon></template>
          <el-option v-for="y in uniqueYears" :key="y" :label="y" :value="y"/>
        </el-select>
        <el-select v-model="filterTag" filterable clearable placeholder="搜标签" style="width:130px" :disabled="isBatchRunning">
          <el-option v-for="t in uniqueTags" :key="t" :label="t" :value="t"/>
        </el-select>
      </div>

      <div class="tool-group search-group">
        <div class="search-box">
          <el-icon :size="14" class="search-icon"><Search /></el-icon>
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

    <!-- ==================== Batch Action Bar ==================== -->
    <transition name="el-zoom-in-top">
      <div v-if="multipleSelection.length > 0 || isBatchRunning" class="batch-bar">
        <div class="batch-bar-inner">
          <div v-if="!isBatchRunning" class="batch-info">
            <el-icon :size="16"><Check /></el-icon>
            已选 <b>{{ multipleSelection.length }}</b> 项
          </div>
          <div v-else class="batch-info running">
            <el-icon :size="16" class="is-loading"><Loading /></el-icon>
            {{ currentBatchAction }} — {{ batchProgress.finished }}/{{ batchProgress.total }}
            (✅{{ batchProgress.success }} ❌{{ batchProgress.fail }})
          </div>
          <div class="batch-btns">
            <button v-if="isBatchRunning" class="btn-pill btn-pill-danger" @click="stopBatch">停止</button>
            <template v-else>
              <button class="btn-pill btn-pill-green" @click="batchAnalyze">
                <el-icon :size="14"><MagicStick /></el-icon> AI 分析
              </button>
              <button class="btn-pill btn-pill-blue" @click="batchSave">
                <el-icon :size="14"><Download /></el-icon> 写入 Emby
              </button>
            </template>
          </div>
        </div>
        <div v-if="isBatchRunning" class="batch-progress-track">
          <div class="batch-progress-fill" :style="{ width: Math.round((batchProgress.finished/batchProgress.total)*100) + '%' }"></div>
        </div>
      </div>
    </transition>

    <!-- ==================== Card List ==================== -->
    <div v-loading="loading" class="card-list">
      <div
        v-for="row in pagedTableData"
        :key="row.id"
        class="tag-card"
        :class="{ selected: isSelected(row) }"
      >
        <!-- Checkbox -->
        <div
          class="card-check"
          :class="{ checked: isSelected(row) }"
          @click.stop="toggleSelect(row)"
        >
          <el-icon v-if="isSelected(row)" :size="14" color="#fff"><Check /></el-icon>
        </div>

        <!-- Body -->
        <div class="card-body">
          <!-- Title row -->
          <div class="card-title-row">
            <span class="card-title">{{ row.name }}</span>
            <span class="card-year">{{ row.year }}</span>
            <span
              v-if="row.status"
              class="card-status"
              :class="{ done: row.status.includes('存') || row.status.includes('批量') }"
            >{{ row.status }}</span>
          </div>

          <!-- Current tags -->
          <div class="card-tags-row">
            <span class="tags-label">标签</span>
            <span
              v-for="tag in row.editing_tags"
              :key="tag"
              class="tag-chip tag-editing"
            >
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
            >+</button>
          </div>

          <!-- AI suggestions -->
          <div v-if="row.suggested_tags && row.suggested_tags.length" class="card-ai-row">
            <span class="ai-label">AI 建议</span>
            <span
              v-for="tag in row.suggested_tags"
              :key="tag"
              class="tag-chip tag-ai"
              @click.stop="acceptAiTag(row, tag)"
            >+ {{ tag }}</span>
            <button class="ai-link" @click.stop="acceptAllAi(row)">全收</button>
            <button class="ai-link ai-recalc" @click.stop="generateAI(row, true)">重算</button>
          </div>
          <button
            v-else
            class="ai-trigger-btn"
            :disabled="row.analyzing || isBatchRunning"
            @click.stop="generateAI(row, false)"
          >
            <el-icon v-if="row.analyzing" :size="13" class="is-loading"><Loading /></el-icon>
            <el-icon v-else :size="13"><MagicStick /></el-icon>
            AI 分析
          </button>
        </div>

        <!-- Save action -->
        <div class="card-save-col">
          <button
            class="save-btn"
            :disabled="row.saving || isBatchRunning"
            @click.stop="saveTags(row)"
          >
            <el-icon v-if="row.saving" :size="14" class="is-loading"><Loading /></el-icon>
            <template v-else>保存</template>
          </button>
        </div>
      </div>

      <!-- Empty state -->
      <div v-if="!loading && !tableData.length" class="empty-state">
        <div class="empty-icon-circle">
          <el-icon :size="36"><VideoPlay /></el-icon>
        </div>
        <p class="empty-title">{{ selectedLib ? '暂无数据' : '未选择媒体库' }}</p>
        <p class="empty-desc">{{ selectedLib ? '请加载数据或尝试搜索' : '请先选择媒体库并加载数据' }}</p>
      </div>
    </div>

    <!-- ==================== Pagination ==================== -->
    <div class="pagination-bar" v-if="filteredTableData.length > 0">
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

<style scoped>
/* ==================== Layout ==================== */
.manager-container {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  padding: 8px 16px;
}

/* ==================== Toolbar ==================== */
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  background: var(--bg-card);
  padding: 12px 16px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color);
  flex-shrink: 0;
}

.tool-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tool-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 4px;
}

.search-group {
  margin-left: auto;
}

/* Search box */
.search-box {
  position: relative;
  display: flex;
  align-items: center;
  width: 200px;
}

.search-icon {
  position: absolute;
  left: 10px;
  color: var(--text-tertiary);
  pointer-events: none;
}

.search-input {
  width: 100%;
  padding: 7px 12px 7px 30px;
  background: var(--bg-input);
  border: none;
  border-radius: var(--radius-full);
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  outline: none;
  box-shadow: 0 0 0 1px var(--border-color);
  transition: box-shadow 0.2s;
}
.search-input::placeholder { color: var(--text-tertiary); }
.search-input:focus { box-shadow: 0 0 0 2px var(--accent-blue); }
.search-input:disabled { opacity: 0.5; }

/* Pill buttons (component-scoped) */
.btn-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 14px;
  border: none;
  border-radius: var(--radius-full);
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.btn-pill:disabled { opacity: 0.4; cursor: not-allowed; }

.btn-pill-blue {
  background: var(--accent-blue);
  color: #fff;
}
.btn-pill-blue:hover:not(:disabled) {
  background: #2563eb;
}

.btn-pill-outline {
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
}
.btn-pill-outline:hover:not(:disabled) {
  background: rgba(59, 130, 246, 0.3);
}

.btn-pill-green {
  background: var(--accent-green-soft);
  color: var(--accent-green);
}
.btn-pill-green:hover:not(:disabled) {
  background: rgba(16, 185, 129, 0.3);
}

.btn-pill-danger {
  background: var(--accent-red-soft);
  color: var(--accent-red);
}
.btn-pill-danger:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.3);
}

/* ==================== Batch Bar ==================== */
.batch-bar {
  background: var(--bg-card);
  border: 1px solid var(--accent-blue);
  border-radius: var(--radius-lg);
  padding: 12px 16px;
  flex-shrink: 0;
}

.batch-bar-inner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.batch-info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-primary);
}
.batch-info b { color: var(--accent-blue); }
.batch-info.running { color: var(--accent-yellow); font-weight: 500; }

.batch-btns {
  display: flex;
  gap: 6px;
}

.batch-progress-track {
  width: 100%;
  height: 4px;
  background: var(--border-color);
  border-radius: 2px;
  margin-top: 10px;
  overflow: hidden;
}

.batch-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent-blue), #60a5fa);
  border-radius: 2px;
  transition: width 0.3s;
}

/* ==================== Card List ==================== */
.card-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* ==================== Tag Card ==================== */
.tag-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid transparent;
  transition: all 0.2s;
}
.tag-card:hover {
  border-color: var(--border-color);
  background: var(--bg-card-hover);
}
.tag-card.selected {
  border-color: var(--accent-blue);
  box-shadow: 0 0 0 1px var(--accent-blue);
}

/* Checkbox */
.card-check {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  border: 2px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
  margin-top: 2px;
}
.card-check:hover { border-color: var(--accent-blue); }
.card-check.checked {
  background: var(--accent-blue);
  border-color: var(--accent-blue);
}

/* Body */
.card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* Title */
.card-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.card-year {
  font-size: 12px;
  color: var(--text-tertiary);
}

.card-status {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-blue);
  background: var(--accent-blue-soft);
  padding: 2px 8px;
  border-radius: var(--radius-full);
}
.card-status.done {
  color: var(--accent-green);
  background: var(--accent-green-soft);
}

/* Tags */
.card-tags-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.tags-label {
  font-size: 11px;
  color: var(--text-tertiary);
  font-weight: 500;
  margin-right: 2px;
}

.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  font-size: 12px;
  font-weight: 500;
}

.tag-editing {
  background: rgba(100, 116, 139, 0.15);
  color: var(--text-secondary);
}

.tag-close {
  cursor: pointer;
  margin-left: 2px;
  font-size: 14px;
  line-height: 1;
  opacity: 0.6;
  transition: opacity 0.15s;
}
.tag-close:hover { opacity: 1; color: var(--accent-red); }

.tag-add-btn {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 1px dashed var(--border-color);
  background: transparent;
  color: var(--text-tertiary);
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}
.tag-add-btn:hover {
  border-color: var(--accent-blue);
  color: var(--accent-blue);
  background: var(--accent-blue-soft);
}

.tag-inline-input {
  width: 80px;
}

/* AI row */
.card-ai-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.ai-label {
  font-size: 11px;
  color: var(--accent-purple);
  font-weight: 500;
  margin-right: 2px;
}

.tag-ai {
  background: rgba(239, 68, 68, 0.12);
  color: var(--accent-red);
  border: 1px solid rgba(239, 68, 68, 0.25);
  cursor: pointer;
  font-size: 11px;
  transition: all 0.2s;
}
.tag-ai:hover {
  background: rgba(239, 68, 68, 0.25);
  transform: scale(1.05);
}

.ai-link {
  border: none;
  background: none;
  font-size: 11px;
  font-weight: 500;
  color: var(--accent-blue);
  cursor: pointer;
  padding: 0;
  font-family: inherit;
  transition: color 0.15s;
}
.ai-link:hover { color: #60a5fa; }
.ai-link.ai-recalc {
  color: var(--text-tertiary);
}
.ai-link.ai-recalc:hover { color: var(--accent-yellow); }

/* AI trigger button */
.ai-trigger-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  border: 1px dashed var(--border-color);
  border-radius: var(--radius-full);
  background: transparent;
  color: var(--text-tertiary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  width: fit-content;
}
.ai-trigger-btn:hover:not(:disabled) {
  border-color: var(--accent-purple);
  color: var(--accent-purple);
  background: rgba(139, 92, 246, 0.08);
}
.ai-trigger-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* Save column */
.card-save-col {
  flex-shrink: 0;
  display: flex;
  align-items: flex-start;
  padding-top: 2px;
}

.save-btn {
  padding: 7px 18px;
  border: none;
  border-radius: var(--radius-full);
  background: var(--accent-blue);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.save-btn:hover:not(:disabled) {
  background: #2563eb;
  box-shadow: var(--shadow-glow-blue);
}
.save-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ==================== Empty State ==================== */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  text-align: center;
}

.empty-icon-circle {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: var(--bg-card);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-tertiary);
  margin-bottom: 16px;
}

.empty-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 4px;
}

.empty-desc {
  font-size: 13px;
  color: var(--text-tertiary);
  margin: 0;
}

/* ==================== Pagination ==================== */
.pagination-bar {
  display: flex;
  justify-content: center;
  padding: 10px 0 4px;
  flex-shrink: 0;
}

/* ==================== Mobile ==================== */
@media screen and (max-width: 768px) {
  .manager-container {
    padding: 4px;
    gap: 6px;
  }

  .toolbar {
    padding: 10px;
    gap: 8px;
    flex-direction: column;
    align-items: stretch;
  }

  .tool-group {
    flex-wrap: wrap;
  }

  .search-group {
    margin-left: 0;
    width: 100%;
  }

  .search-box {
    width: 100%;
  }

  .tag-card {
    padding: 10px 12px;
    gap: 8px;
    flex-wrap: wrap;
  }

  .card-save-col {
    width: 100%;
    justify-content: flex-end;
  }

  .batch-bar-inner {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .batch-btns {
    width: 100%;
  }

  .batch-btns .btn-pill {
    flex: 1;
    justify-content: center;
  }
}
</style>
