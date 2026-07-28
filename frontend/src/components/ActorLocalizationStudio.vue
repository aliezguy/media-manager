<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Search, SwitchButton, CircleCheck, CircleClose, RefreshRight,
  Upload, VideoCamera, Picture, Lock, Loading, VideoPlay, Download
} from '@element-plus/icons-vue'

const API_URL = ''
const config = reactive({})
const libraries = ref([])
const items = ref([])
const loading = ref(false)
const batchLoading = ref(false)
const fullSyncLoading = ref(false)
const auditLoading = ref(false)
const auditingSelected = ref(false)
const autoUpdateEnabled = ref(false)
const autoUpdating = ref(false)

// 分集数据透视
const detailsDrawerVisible = ref(false)
const detailsLoading = ref(false)
const detailsData = ref({ series: null, episodes: [] })

// 系统状态轮询
const systemStatus = ref({ is_running: false, progress: 0, total: 0, current_task: '' })
let _pollTimer = null
const pollSystemStatus = async () => {
  try {
    const res = await axios.get(API_URL + '/api/system_status')
    const data = res.data || {}
    systemStatus.value = {
      is_running: data.is_running || false,
      progress: data.progress || 0,
      total: data.total || 0,
      current_task: data.current_task || ''
    }
  } catch (e) { /* silent */ }
}
const startPolling = () => {
  if (_pollTimer) return
  pollSystemStatus()
  _pollTimer = setInterval(pollSystemStatus, 3000)
}
const stopPolling = () => {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null }
}
const searchQuery = ref('')
let _searchTimer = null

// ★ 服务端全局搜索：输入防抖 350ms 后自动触发 loadItems
watch(searchQuery, (newVal) => {
  if (_searchTimer) clearTimeout(_searchTimer)
  _searchTimer = setTimeout(() => {
    currentPage.value = 1
    loadItems()
  }, 350)
})

// 分集批量富化进度对话框
const enrichDialogVisible = ref(false)
const enrichTaskId = ref('')
const enrichTaskPercent = ref(0)
const enrichTaskMessage = ref('')
const enrichTaskDone = ref(false)
const enrichTargetItem = ref(null)
let _enrichTimer = null

// ★ 统一审计进度对话框（审计选中项 + 审计本地汉化状态共用）
const auditDialogVisible = ref(false)
const auditTaskId = ref('')
const auditTaskPercent = ref(0)
const auditTaskMessage = ref('')
const auditTaskDone = ref(false)
let _auditTimer = null

// ★ 统一汉化进度对话框（同步选中项 + 全量汉化共用）
const sinicizeDialogVisible = ref(false)
const sinicizeTaskId = ref('')
const sinicizeTaskPercent = ref(0)
const sinicizeTaskMessage = ref('')
const sinicizeTaskDone = ref(false)
let _sinicizeTimer = null

const selectedLibrary = ref('')
const selectedStatus = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const totalItems = ref(0)

const libraryOptions = computed(() => [
  { label: '全部媒体库', value: '' },
  ...libraries.value.map(l => ({ label: l.Name, value: l.ItemId || l.Id }))
])

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '未汉化', value: 'pending' },
  { label: '已汉化', value: 'synced' },
  { label: '已锁定', value: 'locked' },
]

const filteredItems = computed(() => {
  // 搜索和状态筛选均已移至服务端处理，此处直接透传服务端返回的结果
  return items.value
})

const stats = computed(() => {
  const total = items.value.length
  const synced = items.value.filter(i => i.status === 'synced' || i.status === 'locked').length
  return { total, synced }
})

const isAllChecked = computed({
  get: () => filteredItems.value.length > 0 && filteredItems.value.every(i => i.checked),
  set: (val) => { filteredItems.value.forEach(i => { i.checked = val }) }
})

const checkedIds = computed(() => items.value.filter(i => i.checked).map(i => i.id))
const pendingCheckedIds = computed(() => items.value.filter(i => i.checked && i.status === 'pending').map(i => i.id))

const connectEmby = async () => {
  try {
    const res = await axios.post(API_URL + '/api/libraries', config)
    libraries.value = res.data || []
  } catch (e) {
    ElMessage.error('连接 Emby 失败: ' + (e.response?.data?.detail || e.message))
  }
}

const loadItems = async () => {
  if (!selectedLibrary.value) return
  loading.value = true
  items.value = []
  try {
    const startIndex = (currentPage.value - 1) * pageSize.value
    const payload = {
      ...config, library_id: selectedLibrary.value, limit: pageSize.value, start_index: startIndex
    }
    if (selectedStatus.value) payload.status_filter = selectedStatus.value
    if (searchQuery.value) payload.search = searchQuery.value
    const res = await axios.post(API_URL + '/api/actor_items', payload)
    items.value = (res.data.items || []).map(item => ({
      id: item.id, name: item.name, year: item.year, type: item.type,
      actors: item.actors || [], poster_url: item.poster_url || null, provider_ids: item.provider_ids || {},
      status: item.sync_status || 'pending',
      sync_matched: item.sync_matched || 0,
      sync_total: item.sync_total || 0,
      checked: false, syncing: false, syncResult: null
    }))
    totalItems.value = res.data.total || 0
    ElMessage.success('已加载 ' + items.value.length + ' 个媒体项（共 ' + totalItems.value + '）')
  } catch (e) {
    ElMessage.error('加载失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

const handleSyncItem = async (item) => {
  if (item.status === 'locked') { ElMessage.warning('该项目已锁定，无法同步'); return }
  item.syncing = true; item.syncResult = null
  try {
    const res = await axios.post(API_URL + '/api/douban/sinicize', { item_id: item.id })
    if (res.data.success) {
      item.status = 'synced'; item.syncResult = res.data
      try {
        const dr = await axios.post(API_URL + '/api/actor_items', { ...config, library_id: selectedLibrary.value, limit: 20 })
        const ref = (dr.data.items || []).find(i => i.id === item.id)
        if (ref) { item.actors = ref.actors || []; item.provider_ids = ref.provider_ids || {} }
      } catch (e) {}
      ElMessage.success('《' + item.name + '》同步成功：匹配 ' + res.data.matched + '/' + res.data.total_actors + ' 位演员')
    } else {
      ElMessage.error('《' + item.name + '》同步失败')
    }
  } catch (e) {
    item.syncResult = { success: false, error: e.response?.data?.detail || e.message }
    ElMessage.error('《' + item.name + '》同步异常')
  } finally { item.syncing = false }
}

const handleBatchSync = async () => {
  const ids = pendingCheckedIds.value
  if (ids.length === 0) { ElMessage.warning('请至少勾选一个未汉化状态的媒体项'); return }

  // ★ 打开统一汉化进度对话框
  sinicizeTaskPercent.value = 0
  sinicizeTaskMessage.value = '正在提交批量汉化任务...'
  sinicizeTaskDone.value = false
  sinicizeDialogVisible.value = true

  try {
    const res = await axios.post(API_URL + '/api/douban/sinicize_selected', { item_ids: ids })
    sinicizeTaskId.value = res.data.task_id
    sinicizeTaskMessage.value = res.data.message
    startSinicizePolling(res.data.task_id)
  } catch (e) {
    sinicizeTaskMessage.value = '提交任务失败'
    sinicizeTaskDone.value = true
    ElMessage.error('提交失败: ' + (e.response?.data?.detail || e.message))
  }
}

const handleFullSync = async () => {
  if (!selectedLibrary.value) { ElMessage.warning('请先选择媒体库'); return }
  try {
    await ElMessageBox.confirm(
      '将对当前媒体库的所有未汉化媒体项执行全量汉化，可能需要较长时间。确定继续？',
      '全量汉化确认',
      { confirmButtonText: '开始全量汉化', cancelButtonText: '取消', type: 'warning' }
    )
  } catch { return }

  // ★ 打开统一汉化进度对话框
  sinicizeTaskPercent.value = 0
  sinicizeTaskMessage.value = '正在提交全量汉化任务...'
  sinicizeTaskDone.value = false
  sinicizeDialogVisible.value = true

  try {
    const res = await axios.post(API_URL + '/api/douban/sinicize_all', { library_id: selectedLibrary.value })
    sinicizeTaskId.value = res.data.task_id
    sinicizeTaskMessage.value = res.data.message
    startSinicizePolling(res.data.task_id)
  } catch (e) {
    sinicizeTaskMessage.value = '提交任务失败'
    sinicizeTaskDone.value = true
    ElMessage.error('提交失败: ' + (e.response?.data?.detail || e.message))
  }
}

// ==========================================
// ★ 统一审计进度轮询（审计选中项 + 审计本地汉化状态共用）
// ==========================================

const stopAuditPolling = () => {
  if (_auditTimer) { clearInterval(_auditTimer); _auditTimer = null }
}

const startAuditPolling = (taskId) => {
  stopAuditPolling()
  _auditTimer = setInterval(async () => {
    try {
      const res = await axios.get(API_URL + '/api/tasks/' + taskId)
      const s = res.data
      auditTaskMessage.value = s.message || ''

      if (s.status === 'completed') {
        // ★ 核心修复：完成时强制 100%，无论后端 current 是否对齐
        stopAuditPolling()
        auditTaskDone.value = true
        auditTaskPercent.value = 100
        ElMessage.success(s.message || '审计完成')
        await loadItems()
        items.value.forEach(i => { i.checked = false })
        // ★ 延迟 1.5 秒自动关闭弹窗，避免用户困惑
        setTimeout(() => {
          if (auditDialogVisible.value) auditDialogVisible.value = false
        }, 1500)
      } else if (s.status === 'error' || s.status === 'failed') {
        // ★★★ 异常状态阻断：后端任务崩溃，强制释放弹窗 + 刷新列表 ★★★
        stopAuditPolling()
        ElMessage.error(`审计任务异常终止: ${s.message || '未知错误'}`)
        auditDialogVisible.value = false
        await loadItems()
      } else {
        // ★ 安全计算：Math.floor 防越界，total=0 时显示 0
        auditTaskPercent.value = s.total > 0
          ? Math.min(99, Math.floor((s.current / s.total) * 100))
          : 0
      }
    } catch (e) {
      // ★ 轮询 API 失败 → 立即停止，释放弹窗，允许用户手动关闭
      stopAuditPolling()
      auditTaskDone.value = true
      auditTaskMessage.value = '无法获取任务进度（网络异常），请手动关闭窗口'
    }
  }, 1000)
}

const closeAuditDialog = () => {
  stopAuditPolling()
  auditDialogVisible.value = false
}

// ==========================================
// ★ 统一汉化进度轮询（同步选中项 + 全量汉化共用）
// ==========================================

const stopSinicizePolling = () => {
  if (_sinicizeTimer) { clearInterval(_sinicizeTimer); _sinicizeTimer = null }
}

const startSinicizePolling = (taskId) => {
  stopSinicizePolling()
  _sinicizeTimer = setInterval(async () => {
    try {
      const res = await axios.get(API_URL + '/api/tasks/' + taskId)
      const s = res.data
      sinicizeTaskMessage.value = s.message || ''

      if (s.status === 'completed') {
        stopSinicizePolling()
        sinicizeTaskDone.value = true
        sinicizeTaskPercent.value = 100
        ElMessage.success(s.message || '汉化完成')
        await loadItems()
        items.value.forEach(i => { i.checked = false })
        setTimeout(() => {
          if (sinicizeDialogVisible.value) sinicizeDialogVisible.value = false
        }, 1500)
      } else if (s.status === 'error' || s.status === 'failed') {
        // ★★★ 异常状态阻断：后端任务崩溃，强制释放弹窗 + 刷新列表 ★★★
        stopSinicizePolling()
        ElMessage.error(`汉化任务异常终止: ${s.message || '未知错误'}`)
        sinicizeDialogVisible.value = false
        await loadItems()
      } else {
        sinicizeTaskPercent.value = s.total > 0
          ? Math.min(99, Math.floor((s.current / s.total) * 100))
          : 0
      }
    } catch (e) {
      stopSinicizePolling()
      sinicizeTaskDone.value = true
      sinicizeTaskMessage.value = '无法获取任务进度（网络异常），请手动关闭窗口'
    }
  }, 1000)
}

const closeSinicizeDialog = () => {
  stopSinicizePolling()
  sinicizeDialogVisible.value = false
}

const handleAuditLocal = async () => {
  if (!selectedLibrary.value) { ElMessage.warning('请先选择媒体库'); return }
  try {
    await ElMessageBox.confirm(
      '是否扫描 Emby 库中已经汉化的数据并同步到本地状态表？将通过 TMDB 整季 API 批量处理剧集分集数据，进度可实时查看。',
      '同步本地状态',
      { confirmButtonText: '开始扫描', cancelButtonText: '取消', type: 'info' }
    )
  } catch { return }

  // 打开进度对话框
  auditTaskPercent.value = 0
  auditTaskMessage.value = '正在提交审计任务...'
  auditTaskDone.value = false
  auditDialogVisible.value = true

  try {
    const res = await axios.post(API_URL + '/api/audit/batch', {
      library_id: selectedLibrary.value,
      item_ids: [],
    })
    auditTaskId.value = res.data.task_id
    auditTaskMessage.value = res.data.message
    startAuditPolling(res.data.task_id)
  } catch (e) {
    auditTaskMessage.value = '提交任务失败'
    auditTaskDone.value = true
    ElMessage.error('提交失败: ' + (e.response?.data?.detail || e.message))
  }
}

const handleAuditSelected = async () => {
  const ids = checkedIds.value
  if (ids.length === 0) { ElMessage.warning('请至少勾选一个媒体项'); return }

  // 打开进度对话框
  auditTaskPercent.value = 0
  auditTaskMessage.value = '正在提交审计任务...'
  auditTaskDone.value = false
  auditDialogVisible.value = true

  try {
    const res = await axios.post(API_URL + '/api/audit/batch', {
      item_ids: ids,
      library_id: '',
    })
    auditTaskId.value = res.data.task_id
    auditTaskMessage.value = res.data.message
    startAuditPolling(res.data.task_id)
  } catch (e) {
    auditTaskMessage.value = '提交任务失败'
    auditTaskDone.value = true
    ElMessage.error('提交失败: ' + (e.response?.data?.detail || e.message))
  }
}

const handleToggleAuto = async (val) => {
  autoUpdating.value = true
  try {
    autoUpdateEnabled.value = val
    ElMessage.success(val ? '自动化更新已开启' : '自动化更新已关闭')
  } catch (e) { ElMessage.error('切换失败'); autoUpdateEnabled.value = !val }
  finally { autoUpdating.value = false }
}

const statusLabel = (s) => ({ pending: '未汉化', synced: '已汉化', locked: '已锁定', syncing: '汉化中' }[s] || s)
const statusColor = (s) => ({ pending: '#ef4444', synced: '#10b981', locked: '#64748b', syncing: '#3b82f6' }[s] || '#94a3b8')

const getPosterGradient = (name) => {
  const g = [
    'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)',
    'linear-gradient(135deg, #1a1a2e 0%, #1a1a2e 50%, #533483 100%)',
    'linear-gradient(135deg, #16213e 0%, #0f3460 50%, #16213e 100%)',
    'linear-gradient(135deg, #1a1a2e 0%, #2d3436 50%, #1a1a2e 100%)',
    'linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%)',
  ]
  let h = 0
  for (let i = 0; i < name.length; i++) h = name.charCodeAt(i) + ((h << 5) - h)
  return g[Math.abs(h) % g.length]
}

const getPosterUrl = (item) => {
  if (!config.emby_host || !config.emby_api_key || !item.id) return null
  return config.emby_host + '/emby/Items/' + item.id + '/Images/Primary?api_key=' + config.emby_api_key
}

const getSyncTag = (item) => {
  if (!item.syncResult) return null
  if (item.syncResult.success) return '✓ 匹配 ' + item.syncResult.matched + '/' + item.syncResult.total_actors
  return '✘ 失败'
}

const openDetailsDrawer = async (itemId) => {
  detailsDrawerVisible.value = true
  detailsLoading.value = true
  detailsData.value = { series: null, episodes: [] }
  try {
    const res = await axios.get(API_URL + '/api/media/' + itemId + '/details')
    detailsData.value = res.data
  } catch (e) {
    ElMessage.error('加载分集详情失败: ' + (e.response?.data?.detail || e.message))
    detailsDrawerVisible.value = false
  } finally {
    detailsLoading.value = false
  }
}

// ==========================================
// 分集批量富化 — 后台任务 + 进度轮询
// ==========================================

const stopEnrichPolling = () => {
  if (_enrichTimer) { clearInterval(_enrichTimer); _enrichTimer = null }
}

const handleBatchEnrichEpisodes = async (item) => {
  if (!item || item.type !== 'Series') {
    ElMessage.warning('仅剧集 (Series) 支持分集批量富化')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将对《${item.name}》执行分集批量富化：通过 TMDB 获取所有分集的简介和客串演员，并自动走本地头像漏斗。确定继续？`,
      '分集批量富化',
      { confirmButtonText: '开始执行', cancelButtonText: '取消', type: 'info' }
    )
  } catch { return }

  // 打开进度对话框
  enrichTaskPercent.value = 0
  enrichTaskMessage.value = '正在提交任务...'
  enrichTaskDone.value = false
  enrichTargetItem.value = item
  enrichDialogVisible.value = true

  try {
    const res = await axios.post(API_URL + '/api/episodes/batch-enrich', { item_id: item.id })
    enrichTaskId.value = res.data.task_id
    enrichTaskMessage.value = res.data.message

    // 启动轮询（每秒一次）
    _enrichTimer = setInterval(async () => {
      try {
        const statusRes = await axios.get(API_URL + '/api/tasks/' + enrichTaskId.value)
        const s = statusRes.data
        enrichTaskMessage.value = s.message || ''

        if (s.status === 'completed') {
          stopEnrichPolling()
          enrichTaskDone.value = true
          enrichTaskPercent.value = 100
          ElMessage.success(s.message || '分集批量富化完成')
          await loadItems()
          // ★ 延迟 1.5 秒自动关闭
          setTimeout(() => {
            if (enrichDialogVisible.value) enrichDialogVisible.value = false
          }, 1500)
        } else if (s.status === 'error' || s.status === 'failed') {
          // ★★★ 异常状态阻断：后端任务崩溃，强制释放弹窗 + 刷新列表 ★★★
          stopEnrichPolling()
          ElMessage.error(`分集富化任务异常终止: ${s.message || '未知错误'}`)
          enrichDialogVisible.value = false
          await loadItems()
        } else {
          enrichTaskPercent.value = s.total > 0
            ? Math.min(99, Math.floor((s.current / s.total) * 100))
            : 0
        }
      } catch (e) {
        // ★ 轮询 API 失败 → 立即停止，释放弹窗
        stopEnrichPolling()
        enrichTaskDone.value = true
        enrichTaskMessage.value = '无法获取任务进度（网络异常），请手动关闭窗口'
      }
    }, 1000)
  } catch (e) {
    enrichTaskMessage.value = '提交任务失败'
    enrichTaskDone.value = true
    ElMessage.error('提交失败: ' + (e.response?.data?.detail || e.message))
  }
}

const closeEnrichDialog = () => {
  stopEnrichPolling()
  enrichDialogVisible.value = false
}

onMounted(async () => {
  try {
    const res = await axios.get(API_URL + '/api/config')
    Object.assign(config, res.data)
    if (config.emby_api_key) await connectEmby()
  } catch (e) {}
})

onUnmounted(() => {
  stopPolling()
  stopEnrichPolling()
  stopAuditPolling()
  stopSinicizePolling()
})


</script>
<template>
  <div class="studio-root">
    <div class="header-bar">
      <div class="header-left">
        <h1 class="page-title">演职员中文化治理</h1>
        <div class="stats-badge"><span class="stats-dot" /><span>已汉化 <strong>{{ stats.synced }}</strong> / {{ stats.total }}</span></div>
      </div>
      <div class="header-center">
        <el-select v-model="selectedLibrary" placeholder="选择媒体库" class="header-select" size="default" :disabled="systemStatus.is_running" @change="currentPage=1;loadItems()">
          <el-option v-for="opt in libraryOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>
        <el-select v-model="selectedStatus" placeholder="全部状态" class="header-select" size="default" @change="currentPage=1;loadItems()">
          <el-option v-for="opt in statusOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>
      </div>
      <div class="header-right">
        <el-input v-model="searchQuery" placeholder="搜索剧名..." :prefix-icon="Search" class="search-input" clearable size="default" />
        <div class="auto-switch-group"><span class="switch-label">自动化更新</span><el-switch v-model="autoUpdateEnabled" :loading="autoUpdating" @change="handleToggleAuto" /></div>
      </div>
    </div>

    <div v-if="systemStatus.is_running" class="progress-banner">
      <div class="progress-info"><el-icon class="is-loading" :size="14"><Loading /></el-icon><span>🚀 后台正在执行汉化任务: <strong>{{ systemStatus.current_task || '处理中...' }}</strong></span><span class="progress-count">({{ systemStatus.progress }}/{{ systemStatus.total }})</span></div>
      <el-progress :percentage="systemStatus.total > 0 ? Math.round((systemStatus.progress / systemStatus.total) * 100) : 0" :stroke-width="4" :show-text="false" color="#00A3FF" />
    </div>

    <div class="select-all-row" v-if="filteredItems.length > 0">
      <el-checkbox v-model="isAllChecked" :indeterminate="checkedIds.length > 0 && !isAllChecked">全选 ({{ checkedIds.length }}/{{ filteredItems.length }})</el-checkbox>
      <span class="select-info">已选 {{ pendingCheckedIds.length }} 个待处理项</span>
      <el-button type="primary" size="small" class="btn-batch-inline" :disabled="pendingCheckedIds.length === 0 || systemStatus.is_running" @click="handleBatchSync">{{ pendingCheckedIds.length > 0 ? '同步选中项 (' + pendingCheckedIds.length + ')' : '批量执行中文化' }}</el-button>
      <el-button type="warning" size="small" class="btn-batch-inline" :disabled="systemStatus.is_running || !selectedLibrary" @click="handleFullSync">全量汉化</el-button>
      <el-button size="small" class="btn-audit-local" :loading="auditLoading" :disabled="systemStatus.is_running || !selectedLibrary" @click="handleAuditLocal">审计本地汉化状态</el-button>
      <el-button type="info" size="small" class="btn-audit-selected" :loading="auditingSelected" :disabled="checkedIds.length === 0 || systemStatus.is_running" @click="handleAuditSelected">审计选中项</el-button>
    </div>

    <div class="cards-grid">
      <div v-if="loading" class="loading-overlay"><el-icon class="is-loading" :size="32"><Loading /></el-icon><span>正在加载媒体数据...</span></div>
      <div v-for="item in filteredItems" :key="item.id" class="media-card" :class="{ 'is-locked': item.status === 'locked', 'is-synced': item.status === 'synced', 'is-checked': item.checked }" @click="item.checked = !item.checked">
        <div class="card-check" @click.stop><el-checkbox v-model="item.checked" @click.stop /></div>
        <div class="card-poster" :style="{ background: getPosterGradient(item.name) }">
          <el-image :src="getPosterUrl(item)" class="poster-img" lazy fit="cover">
            <template #placeholder><div class="poster-skeleton"></div></template>
            <template #error><div class="poster-placeholder"><el-icon :size="20"><VideoCamera /></el-icon></div></template>
          </el-image>
          <span class="poster-type">{{ item.type === 'Movie' ? '电影' : '剧集' }}</span>
        </div>
        <div class="card-body">
          <div class="card-header"><span class="card-title">{{ item.name }}</span><span class="card-year">{{ item.year }}</span><span class="card-status" :style="{ color: statusColor(item.status) }">{{ statusLabel(item.status) }}</span></div>
          <div class="actor-compare">
            <div class="compare-col compare-emby">
              <div class="compare-label">Emby 当前</div>
              <div class="actor-list">
                <div v-for="(a, ai) in item.actors.slice(0, 5)" :key="'emby-' + ai" class="actor-row"><span class="actor-name">{{ a.Name || a.name }}</span><span class="actor-role">{{ a.Role || a.role }}</span></div>
                <div v-if="item.actors.length > 5" class="actor-more">+{{ item.actors.length - 5 }} 更多</div>
                <div v-if="item.actors.length === 0" class="actor-empty">暂无演员</div>
              </div>
            </div>
            <div class="compare-divider"></div>
            <div class="compare-col compare-douban">
              <div class="compare-label">豆瓣同步</div>
              <div class="actor-list" v-if="item.syncing"><div class="actor-syncing"><el-icon class="is-loading" :size="12"><Loading /></el-icon> 抓取中...</div></div>
              <div class="actor-list" v-else-if="item.status === 'synced' && item.syncResult">
                <div v-for="(d, di) in (item.syncResult.details || []).slice(0, 5)" :key="'detail-' + di" class="actor-row"><span class="actor-name">{{ d.new_name || d.emby_name }}</span><span class="actor-role">{{ d.new_role || '' }}</span></div>
                <div class="sync-tag">{{ getSyncTag(item) }}</div>
              </div>
              <div class="actor-list" v-else><div class="actor-empty hint">未同步</div></div>
            </div>
          </div>
          <div v-if="item.type === 'Series'" class="card-actions">
            <el-button size="small" class="btn-details" @click.stop="openDetailsDrawer(item.id)">分集透视</el-button>
            <el-button size="small" class="btn-enrich" :icon="Download" :loading="false" @click.stop="handleBatchEnrichEpisodes(item)">批量获取分集</el-button>
          </div>
        </div>
      </div>
      <div v-if="!loading && filteredItems.length === 0 && selectedLibrary" class="empty-state"><el-icon :size="48"><Picture /></el-icon><p>暂无匹配的媒体项</p><span>尝试调整筛选条件或搜索关键词</span></div>
      <div v-if="!loading && !selectedLibrary" class="empty-state"><el-icon :size="48"><VideoPlay /></el-icon><p>请先选择媒体库</p><span>在上方下拉框中选择 Emby 媒体库后自动加载数据</span></div>
    </div>

    <div class="pagination-bar">
      <el-pagination v-model:current-page="currentPage" v-model:page-size="pageSize" :total="totalItems" :page-sizes="[10, 20, 50]" layout="total, sizes, prev, pager, next, jumper" background small @current-change="loadItems" @size-change="currentPage=1;loadItems()" />
    </div>

    <!-- 分集数据透视抽屉 -->
    <el-drawer v-model="detailsDrawerVisible" title="分集数据透视" direction="rtl" size="600px">
      <div v-loading="detailsLoading" class="details-container">
        <!-- 剧集信息 -->
        <div v-if="detailsData.series" class="details-series-info">
          <h3 class="details-series-title">{{ detailsData.series.title }}</h3>
          <p class="details-series-overview">{{ detailsData.series.overview || '暂无简介' }}</p>
          <div class="details-series-meta">
            <span>共 {{ detailsData.series.recursive_item_count || detailsData.episodes.length }} 个子项</span>
            <span v-if="detailsData.series.actors.length">常驻演员 {{ detailsData.series.actors.length }} 人</span>
          </div>
        </div>

        <!-- 常驻演员 -->
        <div v-if="detailsData.series && detailsData.series.actors.length" class="details-section">
          <h4 class="details-section-title">常驻演员</h4>
          <div class="actor-avatar-list">
            <div v-for="(act, idx) in detailsData.series.actors" :key="'sa-' + idx" class="actor-avatar-item">
              <div class="actor-avatar-box" style="width:56px;height:56px">
                <img v-if="act.local_image_url || act.image_url"
                     :src="act.local_image_url || act.image_url"
                     referrerpolicy="no-referrer" class="actor-avatar-img" />
                <span v-else class="actor-avatar-txt" style="font-size:22px">{{ act.name ? act.name.charAt(0) : '?' }}</span>
              </div>
              <div class="actor-avatar-name">{{ act.name }}</div>
              <div class="actor-avatar-role">{{ act.role || '演员' }}</div>
            </div>
          </div>
        </div>

        <!-- 分集详情 -->
        <div v-if="detailsData.episodes.length" class="details-section">
          <h4 class="details-section-title">分集详情 ({{ detailsData.episodes.length }})</h4>
          <el-collapse accordion>
            <el-collapse-item v-for="ep in detailsData.episodes" :key="'ep-' + ep.emby_item_id">
              <template #title>
                <span class="ep-title-label">
                  第 {{ ep.index_number }} 集: {{ ep.title || '未命名' }}
                </span>
              </template>
              <div class="ep-content">
                <div class="ep-overview">{{ ep.overview || '暂无简介' }}</div>
                <div v-if="ep.actors.length" class="ep-actors">
                  <div class="actor-avatar-list">
                    <div v-for="(act, idx) in ep.actors" :key="'epa-' + idx" class="actor-avatar-item">
                      <div class="actor-avatar-box" style="width:48px;height:48px">
                        <img v-if="act.local_image_url || act.image_url"
                             :src="act.local_image_url || act.image_url"
                             referrerpolicy="no-referrer" class="actor-avatar-img" />
                        <span v-else class="actor-avatar-txt" style="font-size:18px">{{ act.name ? act.name.charAt(0) : '?' }}</span>
                      </div>
                      <div class="actor-avatar-name">{{ act.name }}</div>
                      <div class="actor-avatar-role">{{ act.role || '演员' }}</div>
                    </div>
                  </div>
                </div>
                <div v-else class="ep-no-actors">暂无演员数据</div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>

        <div v-if="!detailsLoading && detailsData.series && !detailsData.episodes.length" class="details-empty">
          该剧集暂无分集数据
        </div>
      </div>
    </el-drawer>

    <!-- 分集批量富化进度对话框 -->
    <el-dialog
      v-model="enrichDialogVisible"
      title="分集批量富化"
      width="500px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="true"
      @close="closeEnrichDialog"
    >
      <div class="enrich-dialog-body">
        <div class="enrich-target-name" v-if="enrichTargetItem">
          <el-icon :size="16"><VideoCamera /></el-icon>
          <strong>{{ enrichTargetItem.name }}</strong>
        </div>
        <div class="enrich-message">{{ enrichTaskMessage }}</div>
        <el-progress
          :percentage="enrichTaskPercent"
          :text-inside="true"
          :stroke-width="20"
          :status="enrichTaskDone ? (enrichTaskPercent === 100 ? 'success' : 'exception') : ''"
          :color="enrichTaskDone ? '' : '#00A3FF'"
        />
      </div>
      <template #footer>
        <el-button
          type="primary"
          @click="closeEnrichDialog"
        >
          {{ enrichTaskDone ? '关闭' : '后台运行 / 关闭' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ★ 统一审计进度对话框（审计选中项 + 审计本地汉化状态共用） -->
    <el-dialog
      v-model="auditDialogVisible"
      title="媒体库审计"
      width="500px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="true"
      @close="closeAuditDialog"
    >
      <div class="enrich-dialog-body">
        <div class="enrich-message">{{ auditTaskMessage }}</div>
        <el-progress
          :percentage="auditTaskPercent"
          :text-inside="true"
          :stroke-width="20"
          :status="auditTaskDone ? (auditTaskPercent === 100 ? 'success' : 'exception') : ''"
          :color="auditTaskDone ? '' : '#00A3FF'"
        />
      </div>
      <template #footer>
        <el-button
          type="primary"
          @click="closeAuditDialog"
        >
          {{ auditTaskDone ? '关闭' : '后台运行 / 关闭' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ★ 统一汉化进度对话框（同步选中项 + 全量汉化共用） -->
    <el-dialog
      v-model="sinicizeDialogVisible"
      title="演员中文化"
      width="500px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="true"
      @close="closeSinicizeDialog"
    >
      <div class="enrich-dialog-body">
        <div class="enrich-message">{{ sinicizeTaskMessage }}</div>
        <el-progress
          :percentage="sinicizeTaskPercent"
          :text-inside="true"
          :stroke-width="20"
          :status="sinicizeTaskDone ? (sinicizeTaskPercent === 100 ? 'success' : 'exception') : ''"
          :color="sinicizeTaskDone ? '' : '#00A3FF'"
        />
      </div>
      <template #footer>
        <el-button
          type="primary"
          @click="closeSinicizeDialog"
        >
          {{ sinicizeTaskDone ? '关闭' : '后台运行 / 关闭' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>
<style scoped>
.studio-root{--s-bg:#0a0a0a;--s-card:#1a1a1a;--s-card-hv:#222;--s-accent:#00A3FF;--s-accent-soft:rgba(0,163,255,.12);--s-border:#2a2a2a;--s-text:#e0e0e0;--s-text2:#888;--s-text3:#555;background:var(--s-bg);min-height:100vh;padding:20px 24px;color:var(--s-text)}
.header-bar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 20px;background:var(--s-card);border:1px solid var(--s-border);border-radius:10px;margin-bottom:14px;flex-wrap:wrap}
.header-left{display:flex;align-items:center;gap:14px}
.page-title{font-size:18px;font-weight:700;color:#fff;white-space:nowrap}
.stats-badge{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--s-text2);padding:4px 10px;background:rgba(255,255,255,.04);border-radius:20px}
.stats-badge strong{color:var(--s-accent);font-weight:600}
.stats-dot{width:7px;height:7px;border-radius:50%;background:var(--s-accent);box-shadow:0 0 6px rgba(0,163,255,.5)}
.header-center{display:flex;align-items:center;gap:10px}
.header-select{width:150px}
.header-right{display:flex;align-items:center;gap:10px}
.search-input{width:180px}
.auto-switch-group{display:flex;align-items:center;gap:6px;padding:4px 10px;background:rgba(255,255,255,.03);border-radius:8px;border:1px solid var(--s-border)}
.switch-label{font-size:12px;color:var(--s-text2);white-space:nowrap}
.select-all-row{display:flex;align-items:center;gap:14px;padding:6px 16px;margin-bottom:12px;font-size:13px;color:var(--s-text2)}
.select-info{font-size:12px;color:var(--s-accent)}
.btn-batch-inline{font-weight:500;border-radius:6px;font-size:12px;margin-left:auto;background:var(--s-accent)!important;border-color:var(--s-accent)!important}
.btn-batch-inline:hover{background:#0090e0!important;border-color:#0090e0!important}
.btn-audit-local{border-radius:6px;font-size:12px;color:var(--s-text2);border-color:var(--s-border)}
.btn-audit-selected{border-radius:6px;font-size:12px}

.cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:16px}
@media(min-width:1800px){.cards-grid{grid-template-columns:repeat(4,1fr)}}
@media(max-width:1600px){.cards-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:1200px){.cards-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:768px){.cards-grid{grid-template-columns:1fr}}

.loading-overlay{grid-column:1/-1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:80px 20px;gap:14px;color:var(--s-text2);font-size:14px}
.loading-overlay .el-icon{color:var(--s-accent)}
.empty-state{grid-column:1/-1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:80px 20px;color:var(--s-text2)}
.empty-state p{font-size:16px;margin:14px 0 6px;color:var(--s-text)}
.empty-state span{font-size:13px;color:var(--s-text3)}

.media-card{display:flex;gap:0;padding:0;background:var(--s-card);border:1px solid var(--s-border);border-radius:10px;transition:all .2s;cursor:pointer;overflow:hidden;min-width:0;position:relative}
.media-card:hover{background:var(--s-card-hv);border-color:#444}
.media-card.is-checked{border-color:var(--s-accent)!important;box-shadow:0 0 0 1px var(--s-accent)}
.media-card.is-locked{opacity:.65}
.media-card.is-synced{border-color:rgba(16,185,129,.2)}
.card-check{position:absolute;top:10px;left:10px;z-index:3}
.card-check :deep(.el-checkbox__inner){background:rgba(0,0,0,.5);border-color:rgba(255,255,255,.3)}

.card-poster{width:130px;aspect-ratio:2/3;border-radius:10px 0 0 10px;display:flex;flex-direction:column;align-items:center;justify-content:center;flex-shrink:0;position:relative;overflow:hidden}
.poster-img{position:absolute;inset:0;width:100%;height:100%}
.poster-img :deep(img){object-fit:cover;width:100%;height:100%;transition:opacity .5s ease}
.poster-img :deep(.el-image__placeholder),.poster-img :deep(.el-image__error){position:absolute;inset:0}
.poster-skeleton{position:absolute;inset:0;background:linear-gradient(90deg,#1a1a2e 25%,#222 50%,#1a1a2e 75%);background-size:200% 100%;animation:shimmer 1.8s infinite}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.poster-placeholder{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:rgba(255,255,255,.15)}
.poster-type{font-size:9px;color:rgba(255,255,255,.5);position:absolute;bottom:4px;right:4px;z-index:2;background:rgba(0,0,0,.6);padding:1px 5px;border-radius:3px}

.card-body{flex:1;min-width:0;display:flex;flex-direction:column;padding:14px 16px;gap:10px}
.card-header{display:flex;align-items:baseline;gap:6px;flex-wrap:wrap;min-width:0}
.card-title{font-size:14px;font-weight:600;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}
.card-year{font-size:11px;color:var(--s-text3);flex-shrink:0}
.card-status{font-size:10px;font-weight:500;white-space:nowrap;flex-shrink:0;margin-left:auto;padding:1px 6px;border-radius:10px;background:rgba(255,255,255,.05)}

.actor-compare{display:grid;grid-template-columns:minmax(0,1fr) 1px minmax(0,1fr);gap:16px;flex:1;min-width:0;max-width:340px}
.compare-col{min-width:0;overflow:hidden}
.compare-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.3px;color:var(--s-text3);margin-bottom:4px;padding-bottom:3px;border-bottom:1px solid var(--s-border);white-space:nowrap}
.compare-douban .compare-label{color:var(--s-accent)}
.compare-divider{width:1px;background:var(--s-border);align-self:stretch}
.actor-list{display:flex;flex-direction:column;gap:1px}
.actor-row{display:flex;align-items:center;justify-content:space-between;padding:2px 0;min-width:0}
.actor-name{font-size:12px;color:var(--s-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0;margin-right:6px}
.actor-role{font-size:11px;color:var(--s-text3);text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:50%;flex-shrink:0}
.actor-more{font-size:10px;color:var(--s-text3);padding-top:2px}
.actor-empty{font-size:11px;color:var(--s-text3);padding:4px 0}
.actor-empty.hint{color:#444;font-style:italic;font-size:11px;padding:6px 0}
.actor-syncing{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--s-text3);padding:6px 0}
.sync-tag{font-size:10px;padding:2px 6px;border-radius:4px;margin-top:4px;color:#10b981;background:rgba(16,185,129,.1)}

.header-select :deep(.el-input__wrapper),.search-input :deep(.el-input__wrapper){background:var(--s-card)!important;border-radius:8px!important;box-shadow:0 0 0 1px var(--s-border)!important}
.header-select :deep(.el-input__wrapper:hover),.search-input :deep(.el-input__wrapper:hover){box-shadow:0 0 0 1px #444!important}
.header-select :deep(.el-input__wrapper.is-focus),.search-input :deep(.el-input__wrapper.is-focus){box-shadow:0 0 0 1px var(--s-accent)!important}
.header-select :deep(.el-input__inner),.search-input :deep(.el-input__inner){color:var(--s-text)}
.auto-switch-group :deep(.el-switch.is-checked .el-switch__core){background-color:var(--s-accent);border-color:var(--s-accent)}
.select-all-row :deep(.el-checkbox__label),.card-check :deep(.el-checkbox__label){color:var(--s-text2)!important}
.progress-banner{background:rgba(0,163,255,.08);border:1px solid rgba(0,163,255,.2);border-radius:8px;padding:10px 16px;margin-bottom:12px;display:flex;flex-direction:column;gap:8px}
.progress-info{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--s-text)}
.progress-info strong{color:var(--s-accent)}
.progress-count{font-size:12px;color:var(--s-text2);margin-left:auto}
.btn-audit-local{border-radius:6px;font-size:12px;color:var(--s-text2);border-color:var(--s-border)}

.pagination-bar{display:flex;justify-content:center;margin-top:24px;padding-bottom:20px}

/* 卡片操作区 */
.card-actions{padding-top:6px;border-top:1px solid var(--s-border);display:flex;gap:8px}
.btn-details{font-size:11px;border-radius:4px;padding:3px 10px;color:var(--s-accent);border-color:rgba(0,163,255,.3);background:rgba(0,163,255,.06)}
.btn-details:hover{background:rgba(0,163,255,.15);border-color:var(--s-accent)}

/* 分集透视抽屉 */
.details-container{padding:0 4px}
.details-series-info{margin-bottom:20px}
.details-series-title{font-size:18px;font-weight:700;color:#fff;margin:0 0 8px}
.details-series-overview{font-size:13px;color:#999;line-height:1.7;margin:0 0 10px}
.details-series-meta{display:flex;gap:16px;font-size:12px;color:var(--s-text3)}

.details-section{margin-bottom:18px}
.details-section-title{font-size:14px;font-weight:600;color:#ccc;margin:0 0 10px;padding-bottom:6px;border-bottom:1px solid var(--s-border)}

.actor-avatar-list{display:flex;flex-wrap:wrap;gap:12px}
.actor-avatar-item{display:flex;flex-direction:column;align-items:center;width:72px}
.actor-avatar-name{font-size:12px;color:#ccc;margin-top:5px;text-align:center;max-width:72px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.actor-avatar-role{font-size:10px;color:var(--s-text3);margin-top:1px;text-align:center;max-width:72px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* 演员头像原生 img（替代 el-avatar，支持 referrerpolicy 防盗链） */
.actor-avatar-box{border-radius:50%;overflow:hidden;background:#2a2a2a;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.actor-avatar-img{width:100%;height:100%;object-fit:cover;display:block}
.actor-avatar-txt{color:#888;font-weight:600;line-height:1;user-select:none}

.ep-content{padding:8px 0}
.ep-overview{font-size:13px;color:#999;line-height:1.6;margin-bottom:12px}
.ep-actors{padding-top:8px;border-top:1px solid rgba(255,255,255,.04)}
.ep-no-actors{font-size:12px;color:var(--s-text3);padding:8px 0}

.ep-title-label{font-size:14px;font-weight:500;color:#ddd}

.details-empty{text-align:center;padding:40px 0;font-size:13px;color:var(--s-text3)}

/* 抽屉暗色主题覆盖 */
:deep(.el-drawer){background:#141414!important}
:deep(.el-drawer__header){color:#fff;margin-bottom:16px;border-bottom:1px solid #2a2a2a;padding-bottom:14px}
:deep(.el-drawer__close-btn){color:#888}
:deep(.el-drawer__close-btn:hover){color:#fff}
:deep(.el-collapse-item__header){background:#1a1a1a;border-color:#2a2a2a;color:#ddd;padding:0 12px;height:42px}
:deep(.el-collapse-item__wrap){background:#1a1a1a;border-color:#2a2a2a}
:deep(.el-collapse-item__content){color:#ccc;padding:12px}

@media(max-width:1024px){.header-bar{flex-direction:column;align-items:stretch}.header-center,.header-right{flex-wrap:wrap;justify-content:flex-start}}

/* 分集批量富化按钮 */
.btn-enrich{margin-left:6px;font-size:11px;border-radius:6px;background:rgba(16,185,129,.12);border:1px solid rgba(16,185,129,.25);color:#10b981}
.btn-enrich:hover{background:rgba(16,185,129,.2);border-color:rgba(16,185,129,.4)}

/* 进度对话框 */
.enrich-dialog-body{display:flex;flex-direction:column;gap:16px;padding:8px 0}
.enrich-target-name{display:flex;align-items:center;gap:8px;font-size:14px;color:var(--s-text)}
.enrich-target-name strong{color:#fff}
.enrich-message{font-size:13px;color:var(--s-text2);min-height:20px}
.enrich-footer-hint{font-size:12px;color:var(--s-text3)}
</style>
