<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  DataAnalysis, Clock, CircleCheck, CircleClose, VideoPlay,
  RefreshLeft, View, Loading, InfoFilled, WarningFilled,
  SuccessFilled, RemoveFilled, Document, Search, Delete
} from '@element-plus/icons-vue'

// ==================== API 层 ====================
const API_URL = ''

const fetchStats = async () => {
  const res = await axios.get(`${API_URL}/api/tasks/stats`)
  return res.data
}

const fetchTasks = async (page, pageSize, statusFilterArr) => {
  const params = { page, page_size: pageSize }
  if (statusFilterArr && statusFilterArr.length > 0) {
    params.status = statusFilterArr.join(',')
  }
  const res = await axios.get(`${API_URL}/api/tasks`, { params })
  return res.data
}

const fetchTaskLogs = async (taskId) => {
  const res = await axios.get(`${API_URL}/api/tasks/${taskId}/logs`)
  return res.data
}

const fetchDeleteTask = async (taskId) => {
  const res = await axios.delete(`${API_URL}/api/tasks/${taskId}`)
  return res.data
}

const fetchBatchDelete = async (taskIds) => {
  const res = await axios.post(`${API_URL}/api/tasks/batch-delete`, {
    task_ids: taskIds
  })
  return res.data
}

const fetchForceMoveSeason = async (taskId, season) => {
  const res = await axios.post(`${API_URL}/api/tasks/${taskId}/force-move-season`, {
    season: season
  })
  return res.data
}

// ==================== 状态 ====================
const stats = ref({ total: 0, completed: 0, failed: 0, waiting: 0, init: 0 })
const tasks = ref([])
const totalTasks = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const statusFilter = ref([])
const tableLoading = ref(false)
const statsLoading = ref(false)

// 多选
const selectedRows = ref([])

// 时间线抽屉
const drawerVisible = ref(false)
const drawerTitle = ref('')
const timelineLoading = ref(false)
const timelineLogs = ref([])
const currentTaskId = ref(null)
const skippedIncompleteSeasons = ref([])
const forceMovingSeason = ref(null)  // 正在执行强制移动的 season number

// ==================== 统计卡片配置 ====================
const statCards = computed(() => [
  { label: '总任务数', value: stats.value.total, icon: DataAnalysis, color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' },
  { label: '已完成', value: stats.value.completed, icon: CircleCheck, color: '#10b981', bg: 'rgba(16,185,129,0.12)' },
  { label: '等待回调', value: stats.value.waiting, icon: Clock, color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
  { label: '失败', value: stats.value.failed, icon: CircleClose, color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
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
const openTimeline = async (task) => {
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

// ==================== 删除任务 ====================
const handleDelete = async (task) => {
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

const handleSelectionChange = (rows) => {
  selectedRows.value = rows
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
const handleForceMove = async (seasonInfo) => {
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
      const msg = e?.response?.data?.detail || e?.message || '强制移动失败'
      ElMessage.error(msg)
    }
  } finally {
    forceMovingSeason.value = null
  }
}

// ==================== 状态标签样式映射 ====================
const getStatusType = (status) => {
  const map = {
    INIT: '',
    COMPLETED: 'success',
    WAITING_FOR_DELETE_WEBHOOK: 'warning',
    FAILED: 'danger',
  }
  return map[status] || 'info'
}

const getStatusLabel = (status) => {
  const map = {
    INIT: '初始化',
    COMPLETED: '已完成',
    WAITING_FOR_DELETE_WEBHOOK: '等待回调',
    FAILED: '失败',
  }
  return map[status] || status
}

// ==================== 时间线节点配置 ====================
const getTimelineConfig = (actionType) => {
  const configs = {
    DELETE_TORRENT: {
      color: '#ef4444',
      icon: RemoveFilled,
      bg: 'rgba(239,68,68,0.1)',
      border: 'rgba(239,68,68,0.3)',
      label: '删除种子',
    },
    DELETE_MEDIA: {
      color: '#f97316',
      icon: WarningFilled,
      bg: 'rgba(249,115,22,0.1)',
      border: 'rgba(249,115,22,0.3)',
      label: '清理媒体库',
    },
    DELETE_ORGANIZED: {
      color: '#f97316',
      icon: WarningFilled,
      bg: 'rgba(249,115,22,0.1)',
      border: 'rgba(249,115,22,0.3)',
      label: '清理已完结',
    },
    MOVE_FOLDER: {
      color: '#3b82f6',
      icon: SuccessFilled,
      bg: 'rgba(59,130,246,0.1)',
      border: 'rgba(59,130,246,0.3)',
      label: '移入媒体库',
    },
    SKIP_FOLDER: {
      color: '#64748b',
      icon: InfoFilled,
      bg: 'rgba(100,116,139,0.1)',
      border: 'rgba(100,116,139,0.3)',
      label: '跳过目录',
    },
    KEEP_MEDIA: {
      color: '#10b981',
      icon: SuccessFilled,
      bg: 'rgba(16,185,129,0.1)',
      border: 'rgba(16,185,129,0.3)',
      label: '保留媒体库',
    },
    KEEP_ORGANIZED: {
      color: '#14b8a6',
      icon: InfoFilled,
      bg: 'rgba(20,184,166,0.1)',
      border: 'rgba(20,184,166,0.3)',
      label: '保留已完结',
    },
    KEEP_TORRENT: {
      color: '#eab308',
      icon: Clock,
      bg: 'rgba(234,179,8,0.1)',
      border: 'rgba(234,179,8,0.3)',
      label: '保留种子',
    },
  }
  return configs[actionType] || {
    color: '#94a3b8',
    icon: Document,
    bg: 'rgba(148,163,184,0.1)',
    border: 'rgba(148,163,184,0.3)',
    label: actionType,
  }
}

const formatTime = (isoStr) => {
  if (!isoStr) return '-'
  const d = new Date(isoStr)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// ==================== JSON 详情格式化 ====================
const formatDetailJson = (detail) => {
  if (!detail || (typeof detail === 'object' && Object.keys(detail).length === 0)) return null
  return JSON.stringify(detail, null, 2)
}

// 切换折叠 panel
const expandedDetails = ref({})
const toggleDetail = (logId) => {
  expandedDetails.value[logId] = !expandedDetails.value[logId]
}

// ==================== 生命周期 ====================
onMounted(() => {
  loadStats()
  loadTasks()
})

watch(statusFilter, () => {
  currentPage.value = 1
  loadTasks()
})

const handlePageChange = (page) => {
  currentPage.value = page
  loadTasks()
}

const handleSizeChange = (size) => {
  pageSize.value = size
  currentPage.value = 1
  loadTasks()
}
</script>

<template>
  <div class="dashboard-container">
    <!-- ==================== 顶部标题栏 ==================== -->
    <div class="dashboard-header">
      <div class="header-left">
        <span class="header-icon"><el-icon :size="20"><DataAnalysis /></el-icon></span>
        <span class="header-title">自动化大盘</span>
      </div>
      <div class="header-right">
        <button class="btn-pill btn-pill-refresh" @click="() => { loadStats(); loadTasks(); }">
          <el-icon :size="14"><RefreshLeft /></el-icon>
          <span>刷新</span>
        </button>
      </div>
    </div>

    <!-- ==================== 统计卡片 ==================== -->
    <div class="stats-grid">
      <div
        v-for="card in statCards"
        :key="card.label"
        class="stat-card"
        :style="{ '--card-color': card.color, '--card-bg': card.bg }"
      >
        <div class="stat-icon-wrap">
          <el-icon :size="22"><component :is="card.icon" /></el-icon>
        </div>
        <div class="stat-body">
          <span class="stat-value">{{ statsLoading ? '-' : card.value }}</span>
          <span class="stat-label">{{ card.label }}</span>
        </div>
      </div>
    </div>

    <!-- ==================== 筛选 & 表格 ==================== -->
    <div class="table-section">
      <div class="table-toolbar">
        <div class="filter-group">
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
        <div class="toolbar-right">
          <button
            v-if="selectedRows.length > 0"
            class="btn-pill btn-pill-batch-delete"
            @click="handleBatchDelete"
          >
            <el-icon :size="14"><Delete /></el-icon>
            <span>删除 ({{ selectedRows.length }})</span>
          </button>
          <span class="table-count">共 {{ totalTasks }} 条记录</span>
        </div>
      </div>

      <el-table
        :data="tasks"
        v-loading="tableLoading"
        stripe
        style="width: 100%"
        :header-cell-style="{ background: 'var(--bg-card)', color: 'var(--text-secondary)', fontWeight: 600 }"
        row-class-name="task-row"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="45" align="center" />
        <el-table-column prop="id" label="ID" width="70" align="center" />
        <el-table-column prop="tmdb_id" label="TMDB" width="85" align="center" />
        <el-table-column prop="title" label="剧集名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="task-title">{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="task_type" label="类型" width="120" align="center" />
        <el-table-column prop="status" label="状态" width="140" align="center">
          <template #default="{ row }">
            <el-tag
              :type="getStatusType(row.status)"
              effect="dark"
              size="small"
              :disable-transitions="true"
            >
              {{ getStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="170" align="center">
          <template #default="{ row }">
            <span class="time-cell">{{ formatTime(row.updated_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" align="center">
          <template #default="{ row }">
            <div class="action-cell">
              <button class="btn-pill btn-pill-view" @click.stop="openTimeline(row)">
                <el-icon :size="14"><View /></el-icon>
                <span>时间线</span>
              </button>
              <button class="btn-pill btn-pill-delete" @click.stop="handleDelete(row)">
                <el-icon :size="14"><Delete /></el-icon>
              </button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-wrap" v-if="totalTasks > 0">
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

    <!-- ==================== 时间线抽屉 ==================== -->
    <el-drawer
      v-model="drawerVisible"
      :title="drawerTitle"
      direction="rtl"
      size="520px"
      :close-on-click-modal="true"
      class="timeline-drawer"
    >
      <div class="timeline-wrap" v-loading="timelineLoading">
        <!-- 跳过 Season 手动处理区 -->
        <div v-if="!timelineLoading && skippedIncompleteSeasons.length > 0" class="skipped-seasons-section">
          <div class="skipped-header">
            <el-icon :size="16" color="#f59e0b"><WarningFilled /></el-icon>
            <span>以下 Season 因已完结不完整被跳过，可手动强制移动</span>
          </div>
          <div
            v-for="s in skippedIncompleteSeasons"
            :key="'skipped-' + s.season"
            class="skipped-row"
          >
            <div class="skipped-info">
              <span class="skipped-label">Season {{ s.season }}</span>
              <span class="skipped-stats">
                已完结 {{ s.organized_file_count }}/{{ s.expected_file_count }}
                &nbsp;|&nbsp;
                媒体库 {{ s.media_file_count }}/{{ s.expected_file_count }}
              </span>
            </div>
            <button
              class="btn-pill btn-pill-force"
              :disabled="forceMovingSeason === s.season"
              @click="handleForceMove(s)"
            >
              <el-icon :size="13" v-if="forceMovingSeason === s.season"><Loading /></el-icon>
              <span>{{ forceMovingSeason === s.season ? '移动中...' : '强制移动' }}</span>
            </button>
          </div>
        </div>

        <div v-if="!timelineLoading && timelineLogs.length === 0 && skippedIncompleteSeasons.length === 0" class="timeline-empty">
          <el-icon :size="40"><Document /></el-icon>
          <p>暂无操作日志</p>
          <span>该任务可能尚未产生任何自动化操作</span>
        </div>

        <div class="timeline-list" v-if="timelineLogs.length > 0">
          <div
            v-for="log in timelineLogs"
            :key="log.id"
            class="timeline-node"
            :style="{
              '--node-color': getTimelineConfig(log.action_type).color,
              '--node-bg': getTimelineConfig(log.action_type).bg,
              '--node-border': getTimelineConfig(log.action_type).border,
            }"
          >
            <!-- 时间线竖线 + 节点 -->
            <div class="timeline-rail">
              <div class="timeline-dot">
                <el-icon :size="14">
                  <component :is="getTimelineConfig(log.action_type).icon" />
                </el-icon>
              </div>
            </div>

            <!-- 内容卡片 -->
            <div class="timeline-card">
              <!-- 头部：时间 + 动作标签 -->
              <div class="card-header">
                <span class="card-time">{{ formatTime(log.created_at) }}</span>
                <span
                  class="card-badge"
                  :style="{ background: getTimelineConfig(log.action_type).bg, color: getTimelineConfig(log.action_type).color }"
                >
                  {{ getTimelineConfig(log.action_type).label }}
                </span>
              </div>

              <!-- 操作对象 -->
              <div class="card-object">
                <el-icon :size="14" :color="getTimelineConfig(log.action_type).color">
                  <component :is="getTimelineConfig(log.action_type).icon" />
                </el-icon>
                <span class="object-name">{{ log.target_name }}</span>
              </div>

              <!-- 原因 -->
              <div class="card-reason" v-if="log.reason">
                <span class="reason-label">原因：</span>
                <span class="reason-text">{{ log.reason }}</span>
              </div>

              <!-- 路径 -->
              <div class="card-path" v-if="log.target_path">
                <span class="path-label">路径：</span>
                <code class="path-text">{{ log.target_path }}</code>
              </div>

              <!-- 校验详情 (MOVE_FOLDER) -->
              <div v-if="log.detail && (log.detail.source_stats || Object.keys(log.detail).length > 0)" class="card-detail">
                <div
                  class="detail-toggle"
                  @click="toggleDetail(log.id)"
                >
                  <span>校验详情</span>
                  <el-icon :size="14" :class="{ rotated: expandedDetails[log.id] }">
                    <RemoveFilled v-if="expandedDetails[log.id]" />
                    <InfoFilled v-else />
                  </el-icon>
                </div>
                <div v-show="expandedDetails[log.id]" class="detail-body">
                  <pre class="detail-json">{{ formatDetailJson(log.detail) }}</pre>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.dashboard-container {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}

/* ==================== Header ==================== */
.dashboard-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-icon {
  color: var(--accent-blue);
  display: flex;
  align-items: center;
}
.header-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0.5px;
}
.header-right {
  display: flex;
  gap: 8px;
}

/* ==================== 通用按钮 ==================== */
.btn-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 16px;
  border-radius: var(--radius-full);
  border: none;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
  white-space: nowrap;
}
.btn-pill-refresh {
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
}
.btn-pill-refresh:hover {
  background: var(--accent-blue);
  color: #fff;
}
.btn-pill-view {
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
  padding: 5px 14px;
  font-size: 12px;
}
.btn-pill-view:hover {
  background: var(--accent-blue);
  color: #fff;
}
.btn-pill-delete {
  background: var(--accent-red-soft);
  color: var(--accent-red);
  padding: 5px 10px;
  font-size: 12px;
}
.btn-pill-delete:hover {
  background: var(--accent-red);
  color: #fff;
}
.btn-pill-batch-delete {
  background: var(--accent-red-soft);
  color: var(--accent-red);
  padding: 7px 16px;
  font-size: 13px;
  font-weight: 600;
}
.btn-pill-batch-delete:hover {
  background: var(--accent-red);
  color: #fff;
}

/* 操作列布局 */
.action-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

/* ==================== 统计卡片 ==================== */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}
.stat-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  display: flex;
  align-items: center;
  gap: 14px;
  transition: all 0.25s ease;
  cursor: default;
  position: relative;
  overflow: hidden;
}
.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 4px;
  height: 100%;
  background: var(--card-color);
  border-radius: 0 3px 3px 0;
  transition: width 0.2s ease;
}
.stat-card:hover {
  border-color: var(--card-color);
  box-shadow: 0 4px 20px rgba(0,0,0,0.25), 0 0 0 1px var(--card-color);
  transform: translateY(-2px);
}
.stat-card:hover::before {
  width: 6px;
}
.stat-icon-wrap {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-md);
  background: var(--card-bg);
  color: var(--card-color);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1;
}
.stat-label {
  font-size: 13px;
  color: var(--text-tertiary);
  font-weight: 500;
}

/* ==================== 表格区域 ==================== */
.table-section {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border-color);
}
.filter-group {
  display: flex;
  align-items: center;
  gap: 10px;
}
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.table-count {
  font-size: 13px;
  color: var(--text-tertiary);
}

/* 表格行 hover 交互 */
:deep(.task-row) {
  cursor: pointer;
}
.task-title {
  font-weight: 500;
  color: var(--text-primary);
}
.time-cell {
  font-size: 12px;
  color: var(--text-tertiary);
  font-family: 'SF Mono', Menlo, monospace;
}

/* 分页 */
.pagination-wrap {
  padding: 14px 18px;
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid var(--border-color);
}

/* ==================== 时间线抽屉 ==================== */
:deep(.timeline-drawer) {
  --el-drawer-bg-color: var(--bg-primary);
}

.timeline-wrap {
  min-height: 200px;
}

.timeline-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  color: var(--text-tertiary);
  gap: 8px;
  text-align: center;
}
.timeline-empty p {
  font-size: 15px;
  color: var(--text-secondary);
  font-weight: 500;
  margin-top: 4px;
}
.timeline-empty span {
  font-size: 13px;
  color: var(--text-tertiary);
}

/* 跳过 Season 手动处理区 */
.skipped-seasons-section {
  background: rgba(245, 158, 11, 0.06);
  border: 1px solid rgba(245, 158, 11, 0.2);
  border-radius: var(--radius-md);
  padding: 14px;
  margin-bottom: 16px;
}
.skipped-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 10px;
}
.skipped-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  background: var(--bg-primary);
  border-radius: var(--radius-sm);
  margin-top: 6px;
}
.skipped-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.skipped-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}
.skipped-stats {
  font-size: 12px;
  color: var(--text-tertiary);
}
.btn-pill-force {
  background: rgba(245, 158, 11, 0.15);
  color: #d97706;
  padding: 6px 14px;
  font-size: 12px;
  flex-shrink: 0;
}
.btn-pill-force:hover:not(:disabled) {
  background: #f59e0b;
  color: #fff;
}
.btn-pill-force:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* 时间线列表 */
.timeline-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* 时间线节点 */
.timeline-node {
  display: flex;
  gap: 14px;
  padding-bottom: 4px;
}

/* 竖线 + 圆点 */
.timeline-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
  flex-shrink: 0;
  width: 36px;
}
.timeline-rail::after {
  content: '';
  position: absolute;
  top: 36px;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 2px;
  background: var(--border-color);
}
.timeline-node:last-child .timeline-rail::after {
  display: none;
}
.timeline-dot {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--node-bg);
  border: 2px solid var(--node-border);
  color: var(--node-color);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1;
  flex-shrink: 0;
}

/* 内容卡片 */
.timeline-card {
  flex: 1;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  margin-bottom: 16px;
  transition: border-color 0.2s;
}
.timeline-card:hover {
  border-color: var(--node-border);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.card-time {
  font-size: 12px;
  color: var(--text-tertiary);
  font-family: 'SF Mono', Menlo, monospace;
}
.card-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  letter-spacing: 0.3px;
}

.card-object {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
.object-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  word-break: break-all;
}

.card-reason,
.card-path {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  margin-top: 4px;
  font-size: 13px;
  line-height: 1.5;
}
.reason-label,
.path-label {
  color: var(--text-tertiary);
  flex-shrink: 0;
}
.reason-text {
  color: var(--text-secondary);
}
.path-text {
  color: var(--text-secondary);
  background: var(--bg-primary);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  word-break: break-all;
}

/* 校验详情 panel */
.card-detail {
  margin-top: 10px;
  border-top: 1px solid var(--border-color);
  padding-top: 8px;
}
.detail-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 4px 0;
  transition: color 0.2s;
  user-select: none;
}
.detail-toggle:hover {
  color: var(--accent-blue);
}
.detail-json {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: 12px;
  font-size: 12px;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  color: var(--text-secondary);
  overflow-x: auto;
  white-space: pre;
  line-height: 1.6;
  margin-top: 6px;
}

/* ==================== 响应式 ==================== */
@media screen and (max-width: 768px) {
  .dashboard-container {
    padding: 8px;
  }
  .dashboard-header {
    flex-wrap: wrap;
    gap: 8px;
  }
  .header-right {
    width: 100%;
  }
  .header-right .btn-pill-refresh {
    width: 100%;
    justify-content: center;
  }
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }
  .stat-card {
    padding: 12px 14px;
    gap: 10px;
  }
  .stat-value {
    font-size: 24px;
  }
  .table-toolbar {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
  .toolbar-right {
    width: 100%;
    justify-content: space-between;
  }
  .filter-group {
    width: 100%;
  }
  .filter-group .el-select {
    width: 100% !important;
  }
  /* 表格水平滚动 — 防止操作列遮挡内容 */
  .table-section {
    overflow-x: auto;
  }
  .table-section :deep(.el-table) {
    min-width: 700px;
  }
  .action-cell {
    gap: 4px;
  }
  .action-cell .btn-pill {
    padding: 4px 8px;
    font-size: 11px;
  }
  :deep(.timeline-drawer) {
    --el-drawer-size: 90vw !important;
  }
}
</style>
