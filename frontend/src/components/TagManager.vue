<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, VideoPlay, MagicStick, Check, Close, Plus, Filter, Download, Calendar, Loading } from '@element-plus/icons-vue'

const API_URL = 'http://127.0.0.1:8000'

const config = reactive({})
const loading = ref(false)
const libraries = ref([])
const selectedLib = ref('')
const searchTerm = ref('')
const tableData = ref([])
const tableRef = ref(null)

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

// 初始化加载配置
onMounted(async () => {
  try {
    const res = await axios.get(`${API_URL}/api/config`)
    Object.assign(config, res.data)
    if(config.emby_api_key) connectEmby(true) // 自动尝试获取库列表
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
  currentPage.value = 1
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
  loading.value = true; tableData.value = []; currentPage.value = 1
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
const handleSelectionChange = (val) => { multipleSelection.value = val }
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
  if(batchProgress.fail === 0) tableRef.value.clearSelection()
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
    <el-card shadow="never" class="toolbar-card">
      <div class="toolbar">
        <div class="tool-group">
          <span class="label">📚 媒体库:</span>
          <el-select v-model="selectedLib" placeholder="请选择库" style="width:140px" :disabled="isBatchRunning">
            <el-option v-for="l in libraries" :key="l.Id" :label="l.Name" :value="l.Id"/>
          </el-select>
          <el-button-group>
            <el-button @click="loadItems(false)" :icon="VideoPlay" :disabled="isBatchRunning">加载50条</el-button>
            <el-button @click="loadItems(true)" :icon="Check" :loading="loading" :disabled="isBatchRunning">全部</el-button>
          </el-button-group>
        </div>
        
        <div class="tool-group">
          <span class="label"><el-icon><Filter/></el-icon> 筛选:</span>
          <el-select v-model="filterStatus" style="width:100px" :disabled="isBatchRunning"><el-option label="全部" value="all"/><el-option label="无标签" value="no"/><el-option label="有标签" value="yes"/></el-select>
          <el-select v-model="filterYear" filterable clearable placeholder="年份" style="width:100px" :disabled="isBatchRunning"><template #prefix><el-icon><Calendar/></el-icon></template><el-option v-for="y in uniqueYears" :key="y" :label="y" :value="y"/></el-select>
          <el-select v-model="filterTag" filterable clearable placeholder="搜标签" style="width:120px" :disabled="isBatchRunning"><el-option v-for="t in uniqueTags" :key="t" :label="t" :value="t"/></el-select>
        </div>

        <div class="tool-group search-group">
          <el-input v-model="searchTerm" placeholder="搜索剧名..." @keyup.enter="searchItems" :disabled="isBatchRunning">
            <template #append><el-button :icon="Search" @click="searchItems"/></template>
          </el-input>
        </div>
      </div>
    </el-card>

    <transition name="el-zoom-in-top">
      <el-alert v-if="multipleSelection.length > 0 || isBatchRunning" type="success" :closable="false" class="batch-alert">
        <template #default>
          <div class="batch-content">
            <div v-if="!isBatchRunning">已选 <b>{{ multipleSelection.length }}</b> 项</div>
            <div v-else class="running-info">
              <el-icon class="is-loading"><Loading /></el-icon> {{ currentBatchAction }}进度: {{ batchProgress.finished }}/{{ batchProgress.total }} (✅{{batchProgress.success}} ❌{{batchProgress.fail}})
            </div>
            <div class="batch-btns">
              <el-button v-if="isBatchRunning" type="danger" size="small" @click="stopBatch">停止</el-button>
              <template v-else>
                <el-button type="success" size="small" @click="batchAnalyze" :icon="MagicStick">AI 分析</el-button>
                <el-button type="primary" size="small" @click="batchSave" :icon="Download">写入 Emby</el-button>
              </template>
            </div>
          </div>
          <el-progress v-if="isBatchRunning" :percentage="Math.round((batchProgress.finished/batchProgress.total)*100) || 0" :stroke-width="6" :show-text="false" class="batch-progress"/>
        </template>
      </el-alert>
    </transition>

    <el-card shadow="hover" class="table-card">
      <el-table ref="tableRef" :data="pagedTableData" border stripe height="calc(100vh - 240px)" @selection-change="handleSelectionChange">
        <el-table-column type="selection" width="45" align="center" :selectable="()=>!isBatchRunning"/>
        <el-table-column label="剧集" width="180">
          <template #default="{row}">
            <div class="title">{{ row.name }}</div>
            <div class="meta">{{ row.year }} <span class="status-tag" :class="{'done': row.status.includes('存')}">{{ row.status }}</span></div>
          </template>
        </el-table-column>
        <el-table-column label="当前标签" min-width="300">
          <template #default="{row}">
            <div class="tags-box">
              <el-tag v-for="tag in row.editing_tags" :key="tag" closable type="info" size="small" @close="removeTag(row, tag)">{{ tag }}</el-tag>
              <el-input v-if="row.inputVisible" v-model="row.inputValue" size="small" style="width:70px" @blur="addTagInput(row)" @keyup.enter="addTagInput(row)" ref="InputRef"/>
              <el-button v-else size="small" :icon="Plus" circle class="add-btn" @click="row.inputVisible=true"/>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="AI 建议" min-width="200">
          <template #default="{row}">
            <div v-if="row.suggested_tags.length">
              <el-tag v-for="tag in row.suggested_tags" :key="tag" type="danger" effect="plain" size="small" class="ai-tag" @click="acceptAiTag(row, tag)">+ {{ tag }}</el-tag>
              <div class="ai-actions"><el-button link type="primary" size="small" @click="acceptAllAi(row)">全收</el-button><el-button link type="warning" size="small" @click="generateAI(row, true)">重算</el-button></div>
            </div>
            <el-button v-else size="small" text bg :icon="MagicStick" :loading="row.analyzing" @click="generateAI(row, false)">AI 分析</el-button>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center">
          <template #default="{row}"><el-button type="primary" size="small" :loading="row.saving" @click="saveTags(row)" :disabled="isBatchRunning">保存</el-button></template>
        </el-table-column>
      </el-table>

      <div class="pagination-bar">
        <el-pagination v-model:current-page="currentPage" v-model:page-size="pageSize" :page-sizes="[50, 100, 200]" layout="total, sizes, prev, pager, next" :total="filteredTableData.length"/>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.manager-container { display: flex; flex-direction: column; gap: 15px; height: 100%; }
.toolbar-card { border: none; background: transparent; :deep(.el-card__body) { padding: 0; } }
.toolbar { display: flex; flex-wrap: wrap; gap: 15px; align-items: center; background: #fff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 12px 0 rgba(0,0,0,0.05); }
.tool-group { display: flex; align-items: center; gap: 8px; }
.label { font-size: 13px; font-weight: 600; color: #606266; }
.search-group { margin-left: auto; }

.batch-alert { margin-bottom: 0; border-radius: 8px; }
.batch-content { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.batch-progress { margin-top: 5px; }
.running-info { display: flex; align-items: center; gap: 8px; font-weight: 500; }

.table-card { border: none; flex: 1; display: flex; flex-direction: column; :deep(.el-card__body) { padding: 0; flex: 1; display: flex; flex-direction: column; } }
.title { font-weight: bold; font-size: 14px; color: #303133; }
.meta { font-size: 12px; color: #909399; margin-top: 4px; display: flex; justify-content: space-between; }
.status-tag { font-weight: bold; color: #409EFF; }
.status-tag.done { color: #67C23A; }

.tags-box { display: flex; flex-wrap: wrap; gap: 4px; }
.add-btn { width: 20px; height: 20px; font-size: 12px; }
.ai-tag { cursor: pointer; transition: all 0.2s; }
.ai-tag:hover { transform: scale(1.05); }
.ai-actions { margin-top: 4px; }

.pagination-bar { background: #fff; padding: 10px; border-top: 1px solid #EBEEF5; display: flex; justify-content: flex-end; }
</style>