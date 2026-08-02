<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import type { Component } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  DataAnalysis, Clock, CircleCheck, CircleClose,
  RefreshLeft, View, Loading, WarningFilled, InfoFilled,
  Document, Delete, Close, ArrowDown, ArrowUp
} from '@element-plus/icons-vue'

// ==================== 类型定义 ====================
interface TaskStats {
  total: number
  completed: number
  failed: number
  waiting: number
  init: number
}

interface TaskItem {
  id: number
  tmdb_id: number
  title: string
  task_type: string
  status: string
  retry_count: number
  error_message: string | null
  created_at: string | null
  updated_at: string | null
}

interface TaskLog {
  id: number
  task_id: number
  tmdb_id: number
  title: string
  action_type: string
  target_name: string
  target_path: string | null
  reason: string | null
  detail: Record<string, unknown> | null
  created_at: string | null
}

interface SeasonInfo {
  season: number
  organized_dir_name: string
  organized_path: string
  organized_file_count: number
  expected_file_count: number
  media_path: string
  media_file_count: number
}

interface TimelineConfig {
  color: string
  label: string
}

interface ApiError {
  response?: {
    data?: {
      detail?: string
    }
  }
  message?: string
}

// ==================== API 层 ====================
const API_URL = ''

const fetchStats = async () => {
  const res = await axios.get(`${API_URL}/api/tasks/stats`)
  return res.data
}

const fetchTasks = async (page: number, pageSize: number, statusFilterArr: string[]) => {
  const params: Record<string, number | string> = { page, page_size: pageSize }
  if (statusFilterArr && statusFilterArr.length > 0) {
    params.status = statusFilterArr.join(',')
  }
  const res = await axios.get(`${API_URL}/api/tasks`, { params })
  return res.data
}

const fetchTaskLogs = async (taskId: number) => {
  const res = await axios.get(`${API_URL}/api/tasks/${taskId}/logs`)
  return res.data
}

const fetchDeleteTask = async (taskId: number) => {
  const res = await axios.delete(`${API_URL}/api/tasks/${taskId}`)
  return res.data
}

const fetchBatchDelete = async (taskIds: number[]) => {
  const res = await axios.post(`${API_URL}/api/tasks/batch-delete`, {
    task_ids: taskIds
  })
  return res.data
}

const fetchForceMoveSeason = async (taskId: number, season: number) => {
  const res = await axios.post(`${API_URL}/api/tasks/${taskId}/force-move-season`, {
    season: season
  })
  return res.data
}

// ==================== 状态 ====================
const stats = ref<TaskStats>({ total: 0, completed: 0, failed: 0, waiting: 0, init: 0 })
const tasks = ref<TaskItem[]>([])
const totalTasks = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const statusFilter = ref<string[]>([])
const tableLoading = ref(false)
const statsLoading = ref(false)

// 多选
const selectedRows = ref<TaskItem[]>([])

// 时间线抽屉
const drawerVisible = ref(false)
const drawerTitle = ref('')
const timelineLoading = ref(false)
const timelineLogs = ref<TaskLog[]>([])
const currentTaskId = ref<number | null>(null)
const skippedIncompleteSeasons = ref<SeasonInfo[]>([])
const forceMovingSeason = ref<number | null>(null)  // 正在执行强制移动的 season number

// ==================== 状态色 → Tailwind 令牌映射 ====================
const cardTones = {
  blue: {
    bar: 'bg-electric shadow-[0_0_12px_rgba(59,130,246,0.6)]',
    num: 'text-electric [text-shadow:0_0_18px_rgba(59,130,246,0.30)]',
    icon: 'text-electric',
    glow: 'hover:border-electric/40 hover:shadow-[0_0_18px_rgba(59,130,246,0.18)]',
  },
  green: {
    bar: 'bg-neon shadow-[0_0_12px_rgba(16,185,129,0.6)]',
    num: 'text-neon [text-shadow:0_0_18px_rgba(16,185,129,0.30)]',
    icon: 'text-neon',
    glow: 'hover:border-neon/40 hover:shadow-[0_0_18px_rgba(16,185,129,0.18)]',
  },
  yellow: {
    bar: 'bg-warn shadow-[0_0_12px_rgba(245,158,11,0.6)]',
    num: 'text-warn [text-shadow:0_0_18px_rgba(245,158,11,0.30)]',
    icon: 'text-warn',
    glow: 'hover:border-warn/40 hover:shadow-[0_0_18px_rgba(245,158,11,0.18)]',
  },
  red: {
    bar: 'bg-danger shadow-[0_0_12px_rgba(239,68,68,0.6)]',
    num: 'text-danger [text-shadow:0_0_18px_rgba(239,68,68,0.30)]',
    icon: 'text-danger',
    glow: 'hover:border-danger/40 hover:shadow-[0_0_18px_rgba(239,68,68,0.18)]',
  },
}

// ==================== 统计卡片配置 ====================
const statCards = computed(() => [
  { label: '总任务数', value: stats.value.total, icon: DataAnalysis, tone: cardTones.blue },
  { label: '已完成', value: stats.value.completed, icon: CircleCheck, tone: cardTones.green },
  { label: '等待回调', value: stats.value.waiting, icon: Clock, tone: cardTones.yellow },
  { label: '失败', value: stats.value.failed, icon: CircleClose, tone: cardTones.red },
])

// ==================== 状态筛选选项 ====================
const statusOptions = [
  { label: '初始化', value: 'INIT' },
  { label: '已完成', value: 'COMPLETED' },
  { label: '等待回调', value: 'WAITING_FOR_DELETE_WEBHOOK' },
  { label: '失败', value: 'FAILED' },
]

// ==================== 数据加载 ====================
const loadStats = async () => {
  statsLoading.value = true
  try {
    stats.value = await fetchStats()
  } catch (e) {
    ElMessage.error('获取统计数据失败')
  } finally {
    statsLoading.value = false
  }
}

const loadTasks = async () => {
  tableLoading.value = true
  try {
    const data = await fetchTasks(currentPage.value, pageSize.value, statusFilter.value)
    tasks.value = data.items || []
    totalTasks.value = data.total || 0
  } catch (e) {
    ElMessage.error('获取任务列表失败')
    tasks.value = []
  } finally {
    tableLoading.value = false
  }
}

// ==================== 时间线抽屉 ====================
const openTimeline = async (task: TaskItem) => {
  currentTaskId.value = task.id
  drawerTitle.value = `${task.title} — 操作时间线`
  drawerVisible.value = true
  timelineLoading.value = true
  timelineLogs.value = []
  try {
    const data = await fetchTaskLogs(task.id)
    timelineLogs.value = data.items || []
    skippedIncompleteSeasons.value = data.skipped_incomplete_seasons || []
  } catch (e) {
    ElMessage.error('获取操作日志失败')
  } finally {
    timelineLoading.value = false
  }
}

const closeDrawer = () => {
  drawerVisible.value = false
}

// ESC 关闭 + 打开时锁定背景滚动
const handleKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Escape' && drawerVisible.value) closeDrawer()
}

watch(drawerVisible, (v) => {
  const scroller = document.querySelector<HTMLElement>('.app-main')
  if (scroller) scroller.style.overflow = v ? 'hidden' : ''
  else document.body.style.overflow = v ? 'hidden' : ''
})

// ==================== 删除任务 ====================
const handleDelete = async (task: TaskItem) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除「${task.title}」(TMDB:${task.tmdb_id}) 的任务记录吗？关联的操作日志将一并删除，此操作不可撤销。`,
      '确认删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
    await fetchDeleteTask(task.id)
    ElMessage.success(`已删除「${task.title}」`)
    loadStats()
    loadTasks()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      ElMessage.error('删除失败')
    }
  }
}

// ==================== 自定义多选 ====================
const isSelected = (id: number) => selectedRows.value.some(r => r.id === id)

const isAllSelected = computed(() => tasks.value.length > 0 && selectedRows.value.length === tasks.value.length)

const isIndeterminate = computed(() => selectedRows.value.length > 0 && selectedRows.value.length < tasks.value.length)

const toggleSelectAll = (e: Event) => {
  if ((e.target as HTMLInputElement).checked) selectedRows.value = tasks.value.slice()
  else selectedRows.value = []
}

const toggleRowSelect = (row: TaskItem) => {
  const idx = selectedRows.value.findIndex(r => r.id === row.id)
  if (idx > -1) selectedRows.value.splice(idx, 1)
  else selectedRows.value.push(row)
}

const handleBatchDelete = async () => {
  if (selectedRows.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedRows.value.length} 条任务记录吗？关联的操作日志将一并删除，此操作不可撤销。`,
      '批量删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
    const ids = selectedRows.value.map(r => r.id)
    const result = await fetchBatchDelete(ids)
    ElMessage.success(`已删除 ${result.deleted_tasks} 条记录`)
    selectedRows.value = []
    loadStats()
    loadTasks()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      ElMessage.error('批量删除失败')
    }
  }
}

// ==================== 强制移动 ====================
const handleForceMove = async (seasonInfo: SeasonInfo) => {
  if (!currentTaskId.value) return
  const sn = seasonInfo.season
  try {
    await ElMessageBox.confirm(
      `Season ${sn} 已完结目录仅有 ${seasonInfo.organized_file_count}/${seasonInfo.expected_file_count} 文件（不完整），` +
      `媒体库现有 ${seasonInfo.media_file_count}/${seasonInfo.expected_file_count} 文件。` +
      `\n\n确认强制将已完结目录移动到媒体库？`,
      `强制移动 Season ${sn}`,
      {
        type: 'warning',
        confirmButtonText: '确认移动',
        cancelButtonText: '取消',
      }
    )
    forceMovingSeason.value = sn
    const result = await fetchForceMoveSeason(currentTaskId.value, sn)
    ElMessage.success(result.message || `Season ${sn} 强制移动成功`)
    // 刷新时间线
    const data = await fetchTaskLogs(currentTaskId.value)
    timelineLogs.value = data.items || []
    skippedIncompleteSeasons.value = data.skipped_incomplete_seasons || []
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      const err = e as ApiError
      const msg = err?.response?.data?.detail || err?.message || '强制移动失败'
      ElMessage.error(msg)
    }
  } finally {
    forceMovingSeason.value = null
  }
}

// ==================== 状态药丸（Tailwind 令牌） ====================
const getStatusLabel = (status: string) => {
  const map: Record<string, string> = {
    INIT: '初始化',
    COMPLETED: '已完成',
    WAITING_FOR_DELETE_WEBHOOK: '等待回调',
    FAILED: '失败',
  }
  return map[status] || status
}

const getStatusClass = (status: string) => {
  const map: Record<string, string> = {
    INIT: 'bg-electric/10 text-electric border-electric/25 shadow-[0_0_10px_rgba(59,130,246,0.12)]',
    COMPLETED: 'bg-neon/10 text-neon border-neon/25 shadow-[0_0_10px_rgba(16,185,129,0.12)]',
    WAITING_FOR_DELETE_WEBHOOK: 'bg-warn/10 text-warn border-warn/25 shadow-[0_0_10px_rgba(245,158,11,0.12)]',
    FAILED: 'bg-danger/10 text-danger border-danger/25 shadow-[0_0_10px_rgba(239,68,68,0.12)]',
  }
  return map[status] || 'bg-slate-500/10 text-slate-400 border-slate-500/25'
}

// ==================== 时间线节点配置 ====================
const getTimelineConfig = (actionType: string) => {
  const configs: Record<string, TimelineConfig> = {
    DELETE_TORRENT:  { color: '#ef4444', label: '删除种子' },
    DELETE_MEDIA:    { color: '#f97316', label: '清理媒体库' },
    DELETE_ORGANIZED:{ color: '#f97316', label: '清理已完结' },
    MOVE_FOLDER:     { color: '#3b82f6', label: '移入媒体库' },
    SKIP_FOLDER:     { color: '#64748b', label: '跳过目录' },
    KEEP_MEDIA:      { color: '#10b981', label: '保留媒体库' },
    KEEP_ORGANIZED:  { color: '#14b8a6', label: '保留已完结' },
    KEEP_TORRENT:    { color: '#eab308', label: '保留种子' },
  }
  return configs[actionType] || { color: '#94a3b8', label: actionType }
}

const getTimelineIcon = (actionType: string) => {
  const icons: Record<string, Component> = {
    DELETE_TORRENT: Delete,
    DELETE_MEDIA: WarningFilled,
    DELETE_ORGANIZED: WarningFilled,
    MOVE_FOLDER: CircleCheck,
    SKIP_FOLDER: InfoFilled,
    KEEP_MEDIA: CircleCheck,
    KEEP_ORGANIZED: CircleCheck,
    KEEP_TORRENT: Clock,
  }
  return icons[actionType] || Document
}

// hex (#3b82f6) → rgba(59,130,246,a)
const hexToRgba = (hex: string, alpha: number) => {
  const h = hex.replace('#', '')
  const full = h.length === 3 ? h.split('').map(c => c + c).join('') : h
  const n = parseInt(full, 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`
}

// 预计算时间线节点的全部发光变量
const logCfg = (log: TaskLog) => {
  const cfg = getTimelineConfig(log.action_type)
  const color = cfg.color
  return {
    ...cfg,
    icon: getTimelineIcon(log.action_type),
    bg: hexToRgba(color, 0.10),
    ring: hexToRgba(color, 0.16),
    glow: hexToRgba(color, 0.50),
    badgeBorder: hexToRgba(color, 0.30),
  }
}

const timelineNodes = computed(() => timelineLogs.value.map(log => ({ log, cfg: logCfg(log) })))

const formatTime = (isoStr: string | null | undefined) => {
  if (!isoStr) return '-'
  const d = new Date(isoStr)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// ==================== JSON 详情格式化 ====================
const formatDetailJson = (detail: Record<string, unknown> | null | undefined) => {
  if (!detail || (typeof detail === 'object' && Object.keys(detail).length === 0)) return null
  return JSON.stringify(detail, null, 2)
}

// 切换折叠 panel
const expandedDetails = ref<Record<number, boolean>>({})
const toggleDetail = (logId: number) => {
  expandedDetails.value[logId] = !expandedDetails.value[logId]
}

// ==================== 生命周期 ====================
onMounted(() => {
  loadStats()
  loadTasks()
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
  const scroller = document.querySelector<HTMLElement>('.app-main')
  if (scroller) scroller.style.overflow = ''
})

watch(statusFilter, () => {
  currentPage.value = 1
  loadTasks()
})

const handlePageChange = (page: number) => {
  currentPage.value = page
  loadTasks()
}

const handleSizeChange = (size: number) => {
  pageSize.value = size
  currentPage.value = 1
  loadTasks()
}
</script>

<template>
  <div class="mx-auto max-w-[1200px] p-5">
    <!-- ==================== 顶部标题栏 + 实时监控指示 ==================== -->
    <div class="mb-5 flex items-center justify-between gap-3">
      <div class="flex items-center gap-2.5">
        <span class="flex h-[34px] w-[34px] items-center justify-center rounded-[10px] border border-electric/25 bg-electric/10 text-electric shadow-[0_0_14px_rgba(59,130,246,0.25)]">
          <el-icon :size="18"><DataAnalysis /></el-icon>
        </span>
        <div>
          <h1 class="text-xl font-bold tracking-wide text-white">自动化大盘</h1>
          <p class="font-hud mt-0.5 text-[10px] uppercase tracking-[0.14em] text-slate-500">Automation · Operations Timeline</p>
        </div>
      </div>

      <div class="flex items-center gap-2.5">
        <!-- 雷达式实时监控指示灯 -->
        <div class="flex items-center gap-1.5 rounded-full border border-neon/25 bg-neon/10 px-2.5 py-1">
          <span class="relative flex h-2 w-2">
            <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-neon opacity-60"></span>
            <span class="relative inline-flex h-2 w-2 rounded-full bg-neon shadow-[0_0_8px_rgba(52,211,153,0.9)]"></span>
          </span>
          <span class="font-hud text-[10px] font-bold uppercase tracking-[0.1em] text-neon">Live</span>
        </div>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-4 py-2 text-[13px] font-medium text-slate-400 backdrop-blur-md transition-all duration-200 hover:border-electric/50 hover:bg-electric/10 hover:text-electric hover:shadow-[0_0_14px_rgba(59,130,246,0.3)]"
          @click="() => { loadStats(); loadTasks(); }"
        >
          <el-icon :size="14"><RefreshLeft /></el-icon>
          <span>刷新</span>
        </button>
      </div>
    </div>

    <!-- ==================== 统计卡片（状态色发光） ==================== -->
    <div class="mb-5 grid grid-cols-2 gap-4 lg:grid-cols-4">
      <div
        v-for="card in statCards"
        :key="card.label"
        class="relative overflow-hidden rounded-xl border border-white/10 bg-white/5 p-5 backdrop-blur-md transition-all duration-200"
        :class="card.tone.glow"
      >
        <!-- 左侧状态色发光指示条 -->
        <span class="absolute bottom-0 left-0 top-0 w-[3px]" :class="card.tone.bar"></span>
        <!-- 低透明度背景 Icon -->
        <el-icon
          :size="92"
          class="pointer-events-none absolute -bottom-3 -right-2 -rotate-6 opacity-[0.07]"
          :class="card.tone.icon"
        >
          <component :is="card.icon" />
        </el-icon>
        <div class="relative">
          <div class="font-hud text-[34px] font-extrabold leading-none tracking-wide" :class="card.tone.num">
            {{ statsLoading ? '--' : card.value }}
          </div>
          <div class="mt-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500">{{ card.label }}</div>
        </div>
      </div>
    </div>

    <!-- ==================== 筛选 & 数据列表 ==================== -->
    <div class="overflow-hidden rounded-2xl border border-white/10 bg-white/5 backdrop-blur-md">
      <div class="flex flex-col items-start justify-between gap-3 border-b border-white/5 px-4 py-3.5 sm:flex-row sm:items-center">
        <div class="flex items-center gap-2.5">
          <el-select
            v-model="statusFilter"
            placeholder="筛选状态"
            size="default"
            style="width: 220px"
            multiple
            collapse-tags
            collapse-tags-tooltip
            clearable
          >
            <el-option
              v-for="opt in statusOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </div>
        <div class="flex items-center gap-3">
          <button
            v-if="selectedRows.length > 0"
            type="button"
            class="inline-flex items-center gap-1.5 rounded-full border border-danger/30 bg-danger/10 px-4 py-2 text-[13px] font-semibold text-danger transition-all duration-200 hover:bg-danger hover:text-white hover:shadow-[0_0_14px_rgba(239,68,68,0.4)]"
            @click="handleBatchDelete"
          >
            <el-icon :size="14"><Delete /></el-icon>
            <span>删除 ({{ selectedRows.length }})</span>
          </button>
          <span class="font-hud text-[13px] text-slate-500">共 {{ totalTasks }} 条记录</span>
        </div>
      </div>

      <!-- 无竖向分割线数据列表 -->
      <div class="data-table" v-loading="tableLoading">
        <div class="dt-head grid items-center grid-cols-[45px_70px_85px_minmax(200px,1fr)_120px_140px_170px_180px] border-b border-white/5 bg-white/5">
          <div class="px-3 py-3 text-center">
            <label class="cb">
              <input
                type="checkbox"
                class="cb-input"
                :checked="isAllSelected"
                :indeterminate.prop="isIndeterminate"
                @change="toggleSelectAll"
              />
              <span class="cb-box" :class="{ indeterminate: isIndeterminate }"></span>
            </label>
          </div>
          <div class="dt-head-cell">ID</div>
          <div class="dt-head-cell">TMDB</div>
          <div class="dt-head-cell text-left">剧集名称</div>
          <div class="dt-head-cell">类型</div>
          <div class="dt-head-cell">状态</div>
          <div class="dt-head-cell">更新时间</div>
          <div class="dt-head-cell">操作</div>
        </div>

        <div class="dt-body">
          <div
            v-for="row in tasks"
            :key="row.id"
            class="dt-row grid items-center grid-cols-[45px_70px_85px_minmax(200px,1fr)_120px_140px_170px_180px] transition-colors duration-150 even:bg-white/[0.02] hover:bg-white/5"
          >
            <div class="px-3 py-3 text-center">
              <label class="cb">
                <input
                  type="checkbox"
                  class="cb-input"
                  :checked="isSelected(row.id)"
                  @change="toggleRowSelect(row)"
                />
                <span class="cb-box"></span>
              </label>
            </div>
            <div class="px-3 py-3 text-center text-[13px] text-slate-400">{{ row.id }}</div>
            <div class="px-3 py-3 text-center text-[13px] text-slate-400">{{ row.tmdb_id }}</div>
            <div class="px-3 py-3 text-left text-[13px] font-medium text-slate-100">{{ row.title }}</div>
            <div class="px-3 py-3 text-center text-xs tracking-wide text-slate-500">{{ row.task_type }}</div>
            <div class="px-3 py-3 text-center">
              <span
                class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1 text-xs font-semibold"
                :class="getStatusClass(row.status)"
              >
                <span class="h-1.5 w-1.5 rounded-full bg-current shadow-[0_0_6px_currentColor]"></span>
                {{ getStatusLabel(row.status) }}
              </span>
            </div>
            <div class="px-3 py-3 text-center">
              <span class="font-hud whitespace-nowrap text-xs text-slate-500">{{ formatTime(row.updated_at) }}</span>
            </div>
            <div class="px-3 py-3 text-center">
              <div class="flex items-center justify-center gap-2">
                <button
                  type="button"
                  class="inline-flex items-center gap-1 rounded-full border border-slate-400/20 px-3 py-1.5 text-xs font-medium text-slate-400 transition-all duration-200 hover:border-electric/50 hover:bg-electric/10 hover:text-electric hover:shadow-[0_0_12px_rgba(59,130,246,0.35)]"
                  @click="openTimeline(row)"
                  aria-label="查看操作时间线"
                >
                  <el-icon :size="14"><View /></el-icon>
                  <span>时间线</span>
                </button>
                <button
                  type="button"
                  class="flex h-[30px] w-[30px] items-center justify-center rounded-lg border border-slate-400/20 text-slate-400 transition-all duration-200 hover:border-danger/50 hover:bg-danger/10 hover:text-danger hover:shadow-[0_0_12px_rgba(239,68,68,0.35)]"
                  @click="handleDelete(row)"
                  aria-label="删除任务"
                >
                  <el-icon :size="14"><Delete /></el-icon>
                </button>
              </div>
            </div>
          </div>

          <div v-if="!tableLoading && tasks.length === 0" class="flex flex-col items-center justify-center gap-2 px-5 py-14 text-center">
            <el-icon :size="40" class="text-slate-500"><Document /></el-icon>
            <p class="text-[15px] font-medium text-slate-400">暂无任务数据</p>
            <span class="text-[13px] text-slate-600">调整筛选条件或等待新的洗版任务</span>
          </div>
        </div>
      </div>

      <!-- 科技感分页器 -->
      <div v-if="totalTasks > 0" class="flex justify-end border-t border-white/5 px-4 py-3.5">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="totalTasks"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </div>

    <!-- ==================== 右侧时间线抽屉 ==================== -->
    <transition name="fade">
      <div v-if="drawerVisible" class="drawer-mask fixed inset-0 z-[1000] bg-black/40 backdrop-blur-sm" @click.self="closeDrawer"></div>
    </transition>

    <transition name="slide">
      <aside
        v-if="drawerVisible"
        class="fixed bottom-0 right-0 top-0 z-[1001] flex max-w-full w-[520px] flex-col border-l border-white/10 bg-[#0F172A]/80 backdrop-blur-xl shadow-[-24px_0_60px_rgba(0,0,0,0.5)]"
        role="dialog"
        aria-modal="true"
        aria-label="操作时间线"
      >
        <header class="flex flex-shrink-0 items-center justify-between gap-3 border-b border-white/5 px-5 py-[18px]">
          <div class="flex min-w-0 items-center gap-2.5">
            <span class="flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded-lg border border-electric/25 bg-electric/10 text-electric shadow-[0_0_12px_rgba(59,130,246,0.25)]">
              <el-icon :size="15"><DataAnalysis /></el-icon>
            </span>
            <span class="truncate text-[15px] font-bold text-white">{{ drawerTitle }}</span>
          </div>
          <button
            type="button"
            class="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-slate-400 transition-all duration-200 hover:border-danger/40 hover:bg-danger/10 hover:text-danger hover:shadow-[0_0_12px_rgba(239,68,68,0.25)]"
            @click="closeDrawer"
            aria-label="关闭时间线"
          >
            <el-icon :size="17"><Close /></el-icon>
          </button>
        </header>

        <div class="drawer-body flex-1 overflow-y-auto overflow-x-hidden p-5" v-loading="timelineLoading">
          <!-- 跳过 Season 手动处理区 -->
          <div v-if="!timelineLoading && skippedIncompleteSeasons.length > 0" class="mb-4 rounded-xl border border-warn/25 bg-gradient-to-br from-warn/10 to-warn/5 p-3.5 shadow-[0_0_16px_rgba(245,158,11,0.06)]">
            <div class="mb-2.5 flex items-center gap-2 text-xs font-semibold text-warn">
              <el-icon :size="15"><WarningFilled /></el-icon>
              <span>以下 Season 因已完结不完整被跳过，可手动强制移动</span>
            </div>
            <div
              v-for="s in skippedIncompleteSeasons"
              :key="'skipped-' + s.season"
              class="mt-2 flex items-center justify-between gap-3 rounded-[10px] border border-white/5 bg-black/20 p-2.5"
            >
              <div class="flex min-w-0 flex-col gap-0.5">
                <span class="text-[13px] font-bold text-slate-100">Season {{ s.season }}</span>
                <span class="font-hud text-[11px] text-slate-500">
                  已完结 {{ s.organized_file_count }}/{{ s.expected_file_count }}
                  &nbsp;|&nbsp;
                  媒体库 {{ s.media_file_count }}/{{ s.expected_file_count }}
                </span>
              </div>
              <button
                type="button"
                class="inline-flex flex-shrink-0 items-center gap-1.5 rounded-full border border-warn/40 bg-warn/15 px-3.5 py-1.5 text-xs font-semibold text-warn transition-all duration-200 hover:bg-warn hover:text-[#0B1120] hover:shadow-[0_0_14px_rgba(245,158,11,0.4)] disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="forceMovingSeason === s.season"
                @click="handleForceMove(s)"
              >
                <el-icon :size="13" v-if="forceMovingSeason === s.season"><Loading /></el-icon>
                <span>{{ forceMovingSeason === s.season ? '移动中...' : '强制移动' }}</span>
              </button>
            </div>
          </div>

          <div v-if="!timelineLoading && timelineLogs.length === 0 && skippedIncompleteSeasons.length === 0" class="flex flex-col items-center justify-center gap-2 px-5 py-16 text-center">
            <el-icon :size="40" class="text-slate-500"><Document /></el-icon>
            <p class="text-[15px] font-medium text-slate-400">暂无操作日志</p>
            <span class="text-[13px] text-slate-600">该任务可能尚未产生任何自动化操作</span>
          </div>

          <!-- 发光时间线 -->
          <div v-if="timelineNodes.length > 0" class="timeline">
            <div
              v-for="{ log, cfg } in timelineNodes"
              :key="log.id"
              class="timeline-item"
              :style="{
                '--node-color': cfg.color,
                '--node-bg': cfg.bg,
                '--node-ring': cfg.ring,
                '--node-glow': cfg.glow,
              }"
            >
              <!-- 发光圆点（ring 发光效果） -->
              <span class="timeline-dot"></span>

              <!-- 事件卡片 -->
              <div class="ev-card">
                <div class="flex items-center justify-between gap-2.5">
                  <span class="font-hud text-[11px] text-slate-500">{{ formatTime(log.created_at) }}</span>
                  <span
                    class="whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.06em]"
                    :style="{ color: cfg.color, background: cfg.bg, borderColor: cfg.badgeBorder }"
                  >
                    {{ cfg.label }}
                  </span>
                </div>

                <div class="mb-2 flex items-center gap-1.5">
                  <el-icon :size="13" :color="cfg.color"><component :is="cfg.icon" /></el-icon>
                  <span class="break-all text-[14px] font-semibold leading-snug text-slate-100">{{ log.target_name }}</span>
                </div>

                <div v-if="log.reason" class="mt-1.5 flex items-start gap-2 text-xs leading-relaxed">
                  <span class="min-w-7 flex-shrink-0 text-slate-500">原因:</span>
                  <span class="break-all text-slate-300">{{ log.reason }}</span>
                </div>

                <div v-if="log.target_path" class="mt-1.5 flex items-start gap-2 text-xs leading-relaxed">
                  <span class="min-w-7 flex-shrink-0 text-slate-500">路径:</span>
                  <code class="break-all rounded border border-white/5 bg-white/5 px-1.5 py-0.5 font-hud text-[11px] text-slate-300">{{ log.target_path }}</code>
                </div>

                <!-- 校验详情 (MOVE_FOLDER) -->
                <div v-if="log.detail && (log.detail.source_stats || Object.keys(log.detail).length > 0)" class="mt-2.5 border-t border-white/5 pt-2">
                  <div class="flex cursor-pointer items-center justify-between py-1 text-xs text-slate-500 transition-colors duration-200 hover:text-electric select-none" @click="toggleDetail(log.id)">
                    <span>校验详情</span>
                    <el-icon :size="13" :class="{ rotated: expandedDetails[log.id] }">
                      <ArrowDown v-if="!expandedDetails[log.id]" />
                      <ArrowUp v-else />
                    </el-icon>
                  </div>
                  <div v-show="expandedDetails[log.id]" class="mt-1.5">
                    <pre class="overflow-x-auto whitespace-pre rounded-lg border border-white/5 bg-black/30 p-3 font-hud text-xs leading-relaxed text-slate-400">{{ formatDetailJson(log.detail) }}</pre>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </aside>
    </transition>
  </div>
</template>

<style scoped>
/* ==================== 数据列表响应式（移动端水平滚动） ==================== */
.data-table { width: 100%; }

/* ==================== 表头单元格 ==================== */
.dt-head-cell {
  padding: 12px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text-tertiary);
  text-align: center;
  white-space: nowrap;
}

/* ==================== 自定义复选框 ==================== */
.cb {
  display: inline-flex;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.cb-input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}
.cb-box {
  width: 16px;
  height: 16px;
  border-radius: 5px;
  border: 1px solid rgba(148, 163, 184, 0.3);
  background: rgba(255, 255, 255, 0.02);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  position: relative;
  transition: all 0.15s ease;
  flex-shrink: 0;
}
.cb-input:checked + .cb-box {
  background: var(--accent-blue);
  border-color: var(--accent-blue);
  box-shadow: 0 0 8px rgba(59, 130, 246, 0.5);
}
.cb-input:checked + .cb-box::after {
  content: '';
  width: 7px;
  height: 4px;
  border-left: 2px solid #fff;
  border-bottom: 2px solid #fff;
  transform: rotate(-45deg) translateY(-1px);
}
.cb-box.indeterminate {
  background: var(--accent-blue);
  border-color: var(--accent-blue);
  box-shadow: 0 0 8px rgba(59, 130, 246, 0.5);
}
.cb-box.indeterminate::after {
  content: '';
  width: 8px;
  height: 2px;
  border-radius: 1px;
  background: #fff;
}
.cb-input:focus-visible + .cb-box {
  outline: 2px solid var(--accent-blue);
  outline-offset: 2px;
}

/* ==================== 抽屉过渡动画 ==================== */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.25s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
.slide-enter-active,
.slide-leave-active {
  transition: transform 0.32s cubic-bezier(0.16, 1, 0.3, 1);
}
.slide-enter-from,
.slide-leave-to {
  transform: translateX(100%);
}

/* ==================== 时间线 ==================== */
.timeline {
  position: relative;
}
.timeline::before {
  content: '';
  position: absolute;
  left: 15px;
  top: 6px;
  bottom: 6px;
  width: 1px;
  background: linear-gradient(to bottom, rgba(148, 163, 184, 0.30), rgba(148, 163, 184, 0.06));
}
.timeline-item {
  position: relative;
  padding-left: 44px;
  padding-bottom: 18px;
}
.timeline-item:last-child {
  padding-bottom: 0;
}

/* 发光圆点：ring 发光效果 = 4px 柔光圈 + 弥散光晕 */
.timeline-dot {
  position: absolute;
  left: 9px;
  top: 18px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--node-color);
  box-shadow:
    0 0 0 4px var(--node-ring),
    0 0 14px var(--node-glow);
}

/* ==================== 事件卡片 ==================== */
.ev-card {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 12px;
  padding: 12px 14px;
  transition: border-color 0.18s ease, background 0.18s ease;
}
.ev-card:hover {
  border-color: rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.05);
}

.detail-toggle .el-icon.rotated {
  transform: rotate(180deg);
  transition: transform 0.2s ease;
}

/* ==================== 响应式 ==================== */
@media (max-width: 768px) {
  .data-table {
    overflow-x: auto;
  }
  .dt-head,
  .dt-row {
    min-width: 980px;
  }
  .drawer-panel {
    width: 100vw;
  }
  .drawer-body {
    padding: 14px;
  }
}

/* ==================== 尊重系统减弱动效偏好 ==================== */
@media (prefers-reduced-motion: reduce) {
  .timeline-dot,
  .ev-card,
  .cb-box,
  .drawer-panel,
  .drawer-mask {
    transition: none !important;
    animation: none !important;
  }
}
</style>
