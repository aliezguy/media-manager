<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Search, RefreshRight, VideoCamera, Picture, Loading, VideoPlay, Download,
  Tickets, DataAnalysis, MagicStick, Connection, Compass
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

const handleForceTranslate = async () => {
  const ids = checkedIds.value
  if (ids.length === 0) { ElMessage.warning('请至少勾选一个媒体项'); return }

  try {
    await ElMessageBox.confirm(
      '此操作将强制重新汉化选中的媒体，可能会覆盖您手动修改过的中文数据！是否继续？',
      '⚠️ 强制汉化警告',
      { confirmButtonText: '确认强制汉化', cancelButtonText: '取消', type: 'error' }
    )
  } catch { return }

  // ★ 打开统一汉化进度对话框
  sinicizeTaskPercent.value = 0
  sinicizeTaskMessage.value = '正在提交强制汉化任务...'
  sinicizeTaskDone.value = false
  sinicizeDialogVisible.value = true

  try {
    const res = await axios.post(API_URL + '/api/sync/force_translate_batch', { item_ids: ids })
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
const statusPillClass = (s) => ({
  pending: 'pill-pending',
  synced: 'pill-synced',
  locked: 'pill-locked',
  syncing: 'pill-syncing',
}[s] || 'pill-locked')

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

// ★ 最终展示行：演员名（已汉化优先取新名）+ 角色名，统一“名 饰 角色”格式（纯展示层，不改动业务逻辑）
const actorDisplayRows = (item) => {
  const details = (item.status === 'synced' && item.syncResult) ? (item.syncResult.details || []) : []
  if (details.length) {
    return details.map(d => ({ name: d.new_name || d.emby_name, role: d.new_role || '' }))
  }
  return (item.actors || []).map(a => ({ name: a.Name || a.name, role: a.Role || a.role }))
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
    <!-- ==================== Sticky 顶部操作区 ==================== -->
    <div class="sticky top-0 z-30 border-b border-white/5 bg-[#0B1120]/80 backdrop-blur-xl">
      <!-- 第一行：标题 + 筛选 -->
      <div class="flex flex-wrap items-center gap-3 px-5 py-3">
        <div class="flex items-center gap-3">
          <h1 class="whitespace-nowrap text-[17px] font-bold tracking-wide text-white">演职员中文化治理</h1>
          <div class="stats-pill">
            <span class="stats-dot" />
            <span>已汉化 <strong>{{ stats.synced }}</strong> / {{ stats.total }}</span>
          </div>
        </div>

        <div class="ml-auto flex flex-wrap items-center gap-2.5">
          <el-select v-model="selectedLibrary" placeholder="选择媒体库" class="hdr-select" size="default" :disabled="systemStatus.is_running" @change="currentPage=1;loadItems()">
            <el-option v-for="opt in libraryOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
          <el-select v-model="selectedStatus" placeholder="全部状态" class="hdr-select" size="default" @change="currentPage=1;loadItems()">
            <el-option v-for="opt in statusOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
          <el-input v-model="searchQuery" placeholder="搜索剧名..." :prefix-icon="Search" class="hdr-search" clearable size="default" />
          <div class="auto-switch">
            <span class="switch-label">自动化更新</span>
            <el-switch v-model="autoUpdateEnabled" :loading="autoUpdating" @change="handleToggleAuto" />
          </div>
        </div>
      </div>

      <!-- 第二行：选中信息 + 批量操作 -->
      <div v-if="filteredItems.length > 0" class="flex flex-wrap items-center gap-3 border-t border-white/5 px-5 py-2.5">
        <el-checkbox v-model="isAllChecked" :indeterminate="checkedIds.length > 0 && !isAllChecked" class="tech-checkbox select-all-check">
          全选 ({{ checkedIds.length }}/{{ filteredItems.length }})
        </el-checkbox>
        <span class="select-count">已选 {{ pendingCheckedIds.length }} 个待处理项</span>

        <div class="ml-auto flex flex-wrap items-center gap-2 hdr-actions">
          <el-button
            size="small"
            class="btn-ghost"
            :icon="Connection"
            :disabled="pendingCheckedIds.length === 0 || systemStatus.is_running"
            @click="handleBatchSync"
          >{{ pendingCheckedIds.length > 0 ? '同步选中项 (' + pendingCheckedIds.length + ')' : '批量执行中文化' }}</el-button>
          <el-button
            size="small"
            class="btn-ghost btn-danger-ghost"
            :icon="RefreshRight"
            :disabled="checkedIds.length === 0 || systemStatus.is_running"
            @click="handleForceTranslate"
          >强制汉化(覆盖)</el-button>
          <el-button
            size="small"
            class="btn-primary"
            :icon="MagicStick"
            :disabled="systemStatus.is_running || !selectedLibrary"
            @click="handleFullSync"
          >全量汉化</el-button>
          <el-button
            size="small"
            class="btn-ghost"
            :icon="Compass"
            :loading="auditLoading"
            :disabled="systemStatus.is_running || !selectedLibrary"
            @click="handleAuditLocal"
          >审计本地汉化状态</el-button>
          <el-button
            size="small"
            class="btn-ghost"
            :icon="DataAnalysis"
            :loading="auditingSelected"
            :disabled="checkedIds.length === 0 || systemStatus.is_running"
            @click="handleAuditSelected"
          >审计选中项</el-button>
        </div>
      </div>
    </div>

    <!-- ==================== 系统任务进度横幅 ==================== -->
    <div v-if="systemStatus.is_running" class="progress-banner mx-5 mt-4">
      <div class="progress-info">
        <el-icon class="is-loading" :size="16"><Loading /></el-icon>
        <span>后台正在执行汉化任务: <strong>{{ systemStatus.current_task || '处理中...' }}</strong></span>
        <span class="progress-count font-hud">({{ systemStatus.progress }}/{{ systemStatus.total }})</span>
      </div>
      <el-progress :percentage="systemStatus.total > 0 ? Math.round((systemStatus.progress / systemStatus.total) * 100) : 0" :stroke-width="4" :show-text="false" color="#00A3FF" />
    </div>

    <!-- ==================== 演员卡片网格 ==================== -->
    <div class="p-5">
      <div class="cards-grid">
        <!-- 加载态 -->
        <div v-if="loading" class="loading-overlay">
          <el-icon class="is-loading" :size="32"><Loading /></el-icon>
          <span>正在加载媒体数据...</span>
        </div>

        <!-- 卡片（横向左右结构） -->
        <div
          v-for="item in filteredItems"
          :key="item.id"
          class="media-card group"
          :class="{
            'is-locked': item.status === 'locked',
            'is-synced': item.status === 'synced',
            'is-checked': item.checked
          }"
          @click="item.checked = !item.checked"
        >
          <!-- ===== 左侧海报区：2:3 竖版海报（加宽恢复黄金比例，约占卡片 40%），h-full 撑满 ===== -->
          <div class="relative h-full w-[150px] shrink-0 overflow-hidden">
            <el-image :src="getPosterUrl(item)" fit="cover" class="poster-img absolute inset-0 h-full w-full" lazy>
              <template #placeholder><div class="poster-skeleton h-full w-full"></div></template>
              <template #error>
                <div class="poster-placeholder h-full w-full" :style="{ background: getPosterGradient(item.name) }">
                  <el-icon :size="24"><VideoCamera /></el-icon>
                </div>
              </template>
            </el-image>
            <!-- 关键高级感：右边缘向左弥散渐变，海报"融化"进深色背景 -->
            <div class="pointer-events-none absolute inset-y-0 right-0 w-12 bg-gradient-to-l from-[#0F172A]/90 to-transparent"></div>
          </div>

          <!-- ===== 右侧信息区（四列窄卡片下减白至 p-3） ===== -->
          <div class="relative flex flex-1 flex-col overflow-hidden p-3">
            <!-- 标题区：Checkbox 与大标题垂直居中，状态药丸紧贴最右侧 -->
            <div class="flex w-full items-center justify-between gap-2">
              <div class="flex min-w-0 items-center gap-2">
                <div class="shrink-0" @click.stop>
                  <el-checkbox v-model="item.checked" class="tech-checkbox card-check" @click.stop />
                </div>
                <div class="min-w-0">
                  <h3 class="truncate text-[15px] font-bold text-white">{{ item.name }}</h3>
                  <div class="mt-1 text-xs text-slate-500">
                    <span>{{ item.year || '—' }}</span>
                    <span class="text-slate-600"> | </span>
                    <span>{{ item.type === 'Movie' ? '电影' : '剧集' }}</span>
                    <template v-if="item.actors.length">
                      <span class="text-slate-600"> · </span>
                      <span class="truncate">{{ item.actors.length }} 位演员</span>
                    </template>
                  </div>
                </div>
              </div>
              <span class="status-pill shrink-0 origin-top-right scale-90" :class="statusPillClass(item.status)">{{ statusLabel(item.status) }}</span>
            </div>

            <!-- ===== 演员列表区：双列网格铺满右侧（窄卡片极限压榨 gap，固定高度内滚动） ===== -->
            <div class="actor-scroll mt-2 grid flex-1 grid-cols-2 gap-x-2 gap-y-1.5 overflow-y-auto pr-1">
              <!-- 抓取中 -->
              <div v-if="item.syncing" class="col-span-2 actor-syncing"><el-icon class="is-loading" :size="12"><Loading /></el-icon> 抓取中...</div>

              <!-- 单行演员数据：演员名 饰 角色名（已汉化优先取新名；min-w-0 + truncate 防溢出） -->
              <div
                v-for="(r, ri) in actorDisplayRows(item)"
                :key="'actor-' + ri"
                class="flex w-full min-w-0 items-center"
                :title="r.role ? (r.name + ' 饰 ' + r.role) : r.name"
              >
                <span class="max-w-[55%] shrink-0 truncate text-[12px] font-medium text-slate-100">{{ r.name }}</span>
                <span v-if="r.role" class="mx-1 shrink-0 text-[9px] font-light text-slate-600">饰</span>
                <span v-if="r.role" class="min-w-0 flex-1 truncate text-[11px] text-slate-400">{{ r.role }}</span>
              </div>

              <!-- 尾部信息 -->
              <div v-if="actorDisplayRows(item).length === 0" class="col-span-2 actor-empty">暂无演员</div>
              <div v-if="getSyncTag(item)" class="col-span-2 sync-tag">{{ getSyncTag(item) }}</div>
            </div>

            <!-- ===== 底部操作栏：沉底，细线贯穿整个右侧信息区（精简字号与间距） ===== -->
            <div v-if="item.type === 'Series'" class="mt-auto flex w-full items-center gap-3 border-t border-white/5 pt-2">
              <button type="button" class="card-action text-[11px] text-slate-400 transition-colors hover:text-blue-400" @click.stop="openDetailsDrawer(item.id)">
                <el-icon :size="12"><Tickets /></el-icon><span>分集海报</span>
              </button>
              <button type="button" class="card-action text-[11px] text-slate-400 transition-colors hover:text-blue-400" @click.stop="handleBatchEnrichEpisodes(item)">
                <el-icon :size="12"><Download /></el-icon><span>批量获取</span>
              </button>
            </div>
          </div>
        </div>

        <!-- 空状态 -->
        <div v-if="!loading && filteredItems.length === 0 && selectedLibrary" class="empty-state">
          <el-icon :size="48"><Picture /></el-icon>
          <p>暂无匹配的媒体项</p>
          <span>尝试调整筛选条件或搜索关键词</span>
        </div>
        <div v-if="!loading && !selectedLibrary" class="empty-state">
          <el-icon :size="48"><VideoPlay /></el-icon>
          <p>请先选择媒体库</p>
          <span>在上方下拉框中选择 Emby 媒体库后自动加载数据</span>
        </div>
      </div>
    </div>

    <!-- ==================== 分页 ==================== -->
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
/* ==================== 根容器 ==================== */
.studio-root {
  min-height: 100vh;
  background: #0B1120;
  color: #E2E8F0;
  padding-bottom: 40px;
}

/* ==================== Sticky 顶部操作区 ==================== */
.stats-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #64748B;
  padding: 4px 12px;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.06);
  white-space: nowrap;
}
.stats-pill strong { color: #60A5FA; font-weight: 600; }
.stats-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: #3B82F6;
  box-shadow: 0 0 8px rgba(59, 130, 246, 0.7);
}

/* 极暗无边框筛选框 — Focus 时电光蓝发光 */
.hdr-select { width: 150px; }
.hdr-search { width: 190px; }
.hdr-select :deep(.el-input__wrapper),
.hdr-search :deep(.el-input__wrapper) {
  background: rgba(255, 255, 255, 0.03) !important;
  border-radius: 10px !important;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.08) !important;
  transition: box-shadow 0.2s ease, background 0.2s ease;
}
.hdr-select :deep(.el-input__wrapper:hover),
.hdr-search :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.18) !important;
}
.hdr-select :deep(.el-input__wrapper.is-focus),
.hdr-search :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.7), 0 0 14px rgba(59, 130, 246, 0.28) !important;
  background: rgba(255, 255, 255, 0.05) !important;
}
.hdr-select :deep(.el-input__inner),
.hdr-search :deep(.el-input__inner) { color: #E2E8F0; }
.hdr-select :deep(.el-input__inner::placeholder),
.hdr-search :deep(.el-input__inner::placeholder) { color: #475569; }

/* 自动化更新开关 */
.auto-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
}
.auto-switch .switch-label { font-size: 12px; color: #64748B; white-space: nowrap; }
.auto-switch :deep(.el-switch.is-checked .el-switch__core) {
  background-color: #3B82F6;
  border-color: #3B82F6;
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.4);
}

/* 选中信息 */
.select-count { font-size: 12px; color: #3B82F6; }

/* ==================== 顶部操作按钮 ==================== */
.hdr-actions .el-button {
  height: 30px;
  padding: 0 14px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 500;
  transition: all 0.2s ease;
}
/* 幽灵按钮 */
.btn-ghost {
  background: transparent !important;
  border: 1px solid rgba(255, 255, 255, 0.1) !important;
  color: #94A3B8 !important;
}
.btn-ghost:hover:not(.is-disabled) {
  background: rgba(255, 255, 255, 0.05) !important;
  border-color: rgba(255, 255, 255, 0.2) !important;
  color: #FFFFFF !important;
}
/* 危险幽灵按钮 */
.btn-danger-ghost {
  color: rgba(248, 113, 113, 0.85) !important;
  border-color: rgba(248, 113, 113, 0.22) !important;
}
.btn-danger-ghost:hover:not(.is-disabled) {
  background: rgba(239, 68, 68, 0.08) !important;
  border-color: rgba(248, 113, 113, 0.45) !important;
  color: #F87171 !important;
}
/* 主操作：电光蓝渐变 + 微发光 */
.btn-primary {
  background: linear-gradient(135deg, #3B82F6 0%, #6366F1 100%) !important;
  border: 1px solid rgba(59, 130, 246, 0.5) !important;
  color: #FFFFFF !important;
  box-shadow:
    0 0 16px rgba(59, 130, 246, 0.35),
    inset 0 1px 0 rgba(255, 255, 255, 0.18) !important;
}
.btn-primary:hover:not(.is-disabled) {
  box-shadow:
    0 0 26px rgba(59, 130, 246, 0.55),
    inset 0 1px 0 rgba(255, 255, 255, 0.18) !important;
  filter: brightness(1.08);
}
.hdr-actions .el-button.is-disabled { opacity: 0.45; }

/* ==================== 系统任务进度横幅 ==================== */
.progress-banner {
  background: rgba(59, 130, 246, 0.06);
  border: 1px solid rgba(59, 130, 246, 0.18);
  border-radius: 12px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  backdrop-filter: blur(8px);
  box-shadow: 0 0 20px rgba(59, 130, 246, 0.06);
}
.progress-info { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #E2E8F0; }
.progress-info .el-icon { color: #60A5FA; }
.progress-info strong { color: #93C5FD; }
.progress-count { font-size: 12px; color: #60A5FA; margin-left: auto; }

/* ==================== 卡片网格 ==================== */
/* 横向卡片：1列 → md:2 → lg:3 → 2xl:4 */
.cards-grid {
  @apply grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-4 md:gap-6;
}

/* 横向左右结构卡片 */
.media-card {
  @apply flex flex-row bg-[#0B1120]/60 border border-white/5 backdrop-blur-xl rounded-2xl shadow-xl hover:border-blue-500/30 hover:shadow-blue-900/20 hover:-translate-y-1 transition-all duration-300 overflow-hidden h-[240px] cursor-pointer;
}
.media-card.is-checked {
  border-color: rgba(59, 130, 246, 0.55) !important;
  box-shadow: 0 0 20px rgba(59, 130, 246, 0.22) !important;
}
.media-card.is-locked { opacity: 0.55; }
.media-card.is-synced { border-color: rgba(16, 185, 129, 0.15); }

/* 演员列表极细滚动条 */
.actor-scroll {
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.15) transparent;
}
.actor-scroll::-webkit-scrollbar { width: 4px; }
.actor-scroll::-webkit-scrollbar-track { background: transparent; }
.actor-scroll::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 2px;
}
.actor-scroll::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.3); }

/* 海报：2:3 竖版海报完美填充左侧区域，不变形 */
.poster-img :deep(img) {
  @apply w-full h-full object-cover object-center;
  display: block;
  transition: transform 0.45s cubic-bezier(0.4, 0, 0.2, 1);
}
.media-card:hover .poster-img :deep(img) { transform: scale(1.045); }
.poster-img :deep(.el-image__placeholder),
.poster-img :deep(.el-image__error) { position: absolute; inset: 0; }
.poster-skeleton {
  background: linear-gradient(90deg, #111827 25%, #1F2937 50%, #111827 75%);
  background-size: 200% 100%;
  animation: shimmer 1.8s infinite;
}
@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
.poster-placeholder { display: flex; align-items: center; justify-content: center; color: rgba(255, 255, 255, 0.18); }

/* ==================== 发光药丸状态徽章 ==================== */
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2.5px 10px;
  border-radius: 9999px;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.2px;
  backdrop-filter: blur(8px);
  white-space: nowrap;
}
.status-pill::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
}
.pill-synced {
  color: #34D399;
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.28);
  box-shadow: 0 0 10px rgba(16, 185, 129, 0.18), inset 0 0 8px rgba(16, 185, 129, 0.05);
}
.pill-pending {
  color: #FBBF24;
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.24);
  box-shadow: 0 0 10px rgba(245, 158, 11, 0.14);
}
.pill-locked {
  color: #94A3B8;
  background: rgba(148, 163, 184, 0.08);
  border: 1px solid rgba(148, 163, 184, 0.18);
}
.pill-syncing {
  color: #60A5FA;
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.3);
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.22);
  animation: pulse-glow 1.6s ease-in-out infinite;
}
@keyframes pulse-glow {
  0%, 100% { box-shadow: 0 0 6px rgba(59, 130, 246, 0.12); }
  50% { box-shadow: 0 0 14px rgba(59, 130, 246, 0.38); }
}

/* ==================== 科技感 Checkbox ==================== */
.tech-checkbox :deep(.el-checkbox__inner) {
  width: 16px;
  height: 16px;
  background: rgba(11, 17, 32, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.22);
  border-radius: 5px;
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.4);
  transition: all 0.2s ease;
}
.tech-checkbox :deep(.el-checkbox__inner:hover) { border-color: rgba(59, 130, 246, 0.65); }
.tech-checkbox :deep(.el-checkbox__input.is-checked .el-checkbox__inner),
.tech-checkbox :deep(.el-checkbox__input.is-indeterminate .el-checkbox__inner) {
  background: #3B82F6;
  border-color: #3B82F6;
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.65), inset 0 0 6px rgba(255, 255, 255, 0.15);
}
.card-check :deep(.el-checkbox__label) { display: none; }
.select-all-check :deep(.el-checkbox__label) { color: #94A3B8; font-size: 12.5px; }

/* ==================== 演职员列表 ==================== */
.actor-syncing { display: flex; align-items: center; gap: 6px; font-size: 11px; color: #64748B; padding: 4px 0; }
.actor-empty { font-size: 11px; color: #475569; padding: 2px 0; }
.sync-tag {
  display: inline-block;
  margin-top: 4px;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 6px;
  color: #34D399;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.2);
}

/* ==================== 卡片底部极简操作 ==================== */
/* 颜色/字号由 Tailwind 类控制：text-slate-400 hover:text-blue-400 text-xs */
.card-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 3px 6px;
  border-radius: 6px;
}
.card-action:hover { background: rgba(59, 130, 246, 0.06); }

/* ==================== 加载 & 空状态 ==================== */
.loading-overlay,
.empty-state {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 20px;
  gap: 10px;
  color: #64748B;
}
.loading-overlay .el-icon { color: #60A5FA; }
.empty-state .el-icon { color: #3F4A5A; }
.empty-state p { font-size: 15px; margin: 12px 0 4px; color: #E2E8F0; }
.empty-state span { font-size: 12px; color: #475569; }

/* ==================== 分页 ==================== */
.pagination-bar {
  display: flex;
  justify-content: center;
  margin-top: 8px;
  padding: 0 24px 8px;
}

/* ==================== 分集透视抽屉 ==================== */
.details-container { padding: 0 4px; }
.details-series-info { margin-bottom: 20px; }
.details-series-title { font-size: 18px; font-weight: 700; color: #fff; margin: 0 0 8px; }
.details-series-overview { font-size: 13px; color: #94A3B8; line-height: 1.7; margin: 0 0 10px; }
.details-series-meta { display: flex; gap: 16px; font-size: 12px; color: #475569; }

.details-section { margin-bottom: 18px; }
.details-section-title {
  font-size: 14px;
  font-weight: 600;
  color: #CBD5E1;
  margin: 0 0 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.actor-avatar-list { display: flex; flex-wrap: wrap; gap: 12px; }
.actor-avatar-item { display: flex; flex-direction: column; align-items: center; width: 72px; }
.actor-avatar-name { font-size: 12px; color: #CBD5E1; margin-top: 5px; text-align: center; max-width: 72px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.actor-avatar-role { font-size: 10px; color: #475569; margin-top: 1px; text-align: center; max-width: 72px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 演员头像原生 img（替代 el-avatar，支持 referrerpolicy 防盗链） */
.actor-avatar-box { border-radius: 50%; overflow: hidden; background: #1E293B; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.actor-avatar-img { width: 100%; height: 100%; object-fit: cover; display: block; }
.actor-avatar-txt { color: #64748B; font-weight: 600; line-height: 1; user-select: none; }

.ep-content { padding: 8px 0; }
.ep-overview { font-size: 13px; color: #94A3B8; line-height: 1.6; margin-bottom: 12px; }
.ep-actors { padding-top: 8px; border-top: 1px solid rgba(255, 255, 255, 0.04); }
.ep-no-actors { font-size: 12px; color: #475569; padding: 8px 0; }
.ep-title-label { font-size: 14px; font-weight: 500; color: #D1D5DB; }
.details-empty { text-align: center; padding: 40px 0; font-size: 13px; color: #475569; }

/* 抽屉 / 折叠面板暗色覆盖 */
:deep(.el-drawer) { background: #0F172A !important; }
:deep(.el-drawer__header) {
  color: #F1F5F9;
  margin-bottom: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  padding-bottom: 14px;
}
:deep(.el-drawer__close-btn) { color: #64748B; }
:deep(.el-drawer__close-btn:hover) { color: #fff; }
:deep(.el-collapse-item__header) {
  background: rgba(255, 255, 255, 0.03);
  border-color: rgba(255, 255, 255, 0.06);
  color: #D1D5DB;
  padding: 0 12px;
  height: 42px;
  border-radius: 8px;
}
:deep(.el-collapse-item__wrap) { background: rgba(255, 255, 255, 0.02); border-color: rgba(255, 255, 255, 0.06); }
:deep(.el-collapse-item__content) { color: #CBD5E1; padding: 12px; }

/* ==================== 进度对话框 ==================== */
.enrich-dialog-body { display: flex; flex-direction: column; gap: 16px; padding: 8px 0; }
.enrich-target-name { display: flex; align-items: center; gap: 8px; font-size: 14px; color: #94A3B8; }
.enrich-target-name strong { color: #fff; }
.enrich-message { font-size: 13px; color: #94A3B8; min-height: 20px; }

/* 窄屏适配 */
@media (max-width: 1024px) {
  .hdr-search { width: 150px; }
  .hdr-select { width: 130px; }
}
</style>
