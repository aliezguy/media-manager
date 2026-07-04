<script setup>
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, Refresh, Delete, View, Edit, Timer, VideoPlay,
  Clock, CircleCheck, CircleClose, InfoFilled, WarningFilled,
  SuccessFilled, RemoveFilled, Document, Setting,
} from '@element-plus/icons-vue'

// ==================== API 层 ====================
const API_URL = ''

const fetchTasks = async () => {
  const res = await axios.get(`${API_URL}/api/scheduled-tasks`)
  return res.data
}

const fetchCreateTask = async (data) => {
  const res = await axios.post(`${API_URL}/api/scheduled-tasks`, data)
  return res.data
}

const fetchUpdateTask = async (id, data) => {
  const res = await axios.put(`${API_URL}/api/scheduled-tasks/${id}`, data)
  return res.data
}

const fetchDeleteTask = async (id) => {
  const res = await axios.delete(`${API_URL}/api/scheduled-tasks/${id}`)
  return res.data
}

const fetchRunTask = async (id) => {
  const res = await axios.post(`${API_URL}/api/scheduled-tasks/${id}/run`)
  return res.data
}

const fetchTaskLogs = async (id, limit = 50) => {
  const res = await axios.get(`${API_URL}/api/scheduled-tasks/${id}/logs`, { params: { limit } })
  return res.data
}

// ==================== 状态 ====================
const tasks = ref([])
const tableLoading = ref(false)

// 表单弹窗
const dialogVisible = ref(false)
const dialogTitle = ref('新建定时任务')
const isEditing = ref(false)
const editingTaskId = ref(null)
const formLoading = ref(false)
const form = ref({
  directory_path: '',
  cron_expression: '',
  is_active: true,
})

// 日志抽屉
const logDrawerVisible = ref(false)
const logDrawerTitle = ref('')
const logLoading = ref(false)
const logs = ref([])
const currentLogTaskId = ref(null)

// 日志详情展开
const expandedLogIds = ref({})

// ==================== 数据加载 ====================
const loadTasks = async () => {
  tableLoading.value = true
  try {
    tasks.value = await fetchTasks()
  } catch (e) {
    ElMessage.error('获取定时任务列表失败')
    tasks.value = []
  } finally {
    tableLoading.value = false
  }
}

// ==================== 表单操作 ====================
const resetForm = () => {
  form.value = {
    directory_path: '',
    cron_expression: '',
    is_active: true,
  }
  isEditing.value = false
  editingTaskId.value = null
}

const openCreateDialog = () => {
  resetForm()
  dialogTitle.value = '新建定时任务'
  dialogVisible.value = true
}

const openEditDialog = (task) => {
  resetForm()
  dialogTitle.value = '编辑定时任务'
  isEditing.value = true
  editingTaskId.value = task.id
  form.value = {
    directory_path: task.directory_path,
    cron_expression: task.cron_expression,
    is_active: task.is_active,
  }
  dialogVisible.value = true
}

const handleFormSubmit = async () => {
  if (!form.value.directory_path.trim()) {
    ElMessage.warning('请输入 CD2 扫描目录路径')
    return
  }
  if (!form.value.cron_expression.trim()) {
    ElMessage.warning('请输入 Cron 表达式')
    return
  }

  formLoading.value = true
  try {
    if (isEditing.value) {
      await fetchUpdateTask(editingTaskId.value, form.value)
      ElMessage.success('任务已更新')
    } else {
      await fetchCreateTask(form.value)
      ElMessage.success('任务已创建')
    }
    dialogVisible.value = false
    loadTasks()
  } catch (e) {
    const detail = e.response?.data?.detail || '操作失败'
    ElMessage.error(typeof detail === 'string' ? detail : '操作失败')
  } finally {
    formLoading.value = false
  }
}

// ==================== 任务操作 ====================
const handleRun = async (task) => {
  try {
    await ElMessageBox.confirm(
      `确定要手动触发「${task.directory_path}」的扫描吗？`,
      '手动执行',
      { type: 'info', confirmButtonText: '执行', cancelButtonText: '取消' }
    )
    await fetchRunTask(task.id)
    ElMessage.success('扫描已触发，请稍后查看日志')
    loadTasks()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      ElMessage.error('执行失败')
    }
  }
}

const handleDelete = async (task) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除「${task.directory_path}」的定时任务吗？关联的扫描日志将一并删除，此操作不可撤销。`,
      '确认删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
    await fetchDeleteTask(task.id)
    ElMessage.success('任务已删除')
    loadTasks()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      ElMessage.error('删除失败')
    }
  }
}

// ==================== 开关切换 ====================
const handleSwitchChange = async (task, value) => {
  try {
    await fetchUpdateTask(task.id, { is_active: value })
    task.is_active = value
    ElMessage.success(value ? '任务已启用' : '任务已停用')
  } catch (e) {
    ElMessage.error('状态更新失败')
    // 不 revert — 用户看到 error 即可知道失败
    loadTasks()
  }
}

// ==================== 日志抽屉 ====================
const openLogDrawer = async (task) => {
  currentLogTaskId.value = task.id
  logDrawerTitle.value = `${task.directory_path} — 扫描日志`
  logDrawerVisible.value = true
  logLoading.value = true
  logs.value = []
  expandedLogIds.value = {}
  try {
    logs.value = await fetchTaskLogs(task.id)
  } catch (e) {
    ElMessage.error('获取扫描日志失败')
  } finally {
    logLoading.value = false
  }
}

const toggleLogDetail = (logId) => {
  expandedLogIds.value[logId] = !expandedLogIds.value[logId]
}

// ==================== 格式化 ====================
const formatTime = (isoStr) => {
  if (!isoStr) return '-'
  const d = new Date(isoStr)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const getTriggerTypeLabel = (type) => {
  const map = { CRON: '定时触发', MANUAL: '手动触发' }
  return map[type] || type
}

const getTriggerTypeColor = (type) => {
  return type === 'CRON' ? '#3b82f6' : '#8b5cf6'
}

const getLogStatusType = (status) => {
  const map = { SUCCESS: 'success', FAILED: 'danger', RUNNING: 'warning' }
  return map[status] || 'info'
}

const getLogStatusIcon = (status) => {
  if (status === 'SUCCESS') return SuccessFilled
  if (status === 'FAILED') return CircleClose
  return Clock
}

const formatDetailJson = (detail) => {
  if (!detail || (typeof detail === 'object' && Object.keys(detail).length === 0)) return null
  // 截断 items 数组，只展示摘要
  const d = JSON.parse(JSON.stringify(detail))
  if (d.items && Array.isArray(d.items) && d.items.length > 0) {
    return JSON.stringify(d, null, 2)
  }
  return JSON.stringify(d, null, 2)
}

// ==================== 生命周期 ====================
onMounted(() => {
  loadTasks()
})
</script>

<template>
  <div class="scheduler-root">
    <!-- ==================== 页面标题栏 ==================== -->
    <div class="page-header">
      <div class="header-left">
        <h2 class="page-title">
          <el-icon :size="20"><Timer /></el-icon>
          定时扫描
        </h2>
        <span class="subtitle">按 Cron 表达式定期扫描 CD2 目录，自动触发洗版流程</span>
      </div>
      <div class="header-right">
        <el-button @click="loadTasks" :loading="tableLoading">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button type="primary" @click="openCreateDialog">
          <el-icon><Plus /></el-icon>
          新建任务
        </el-button>
      </div>
    </div>

    <!-- ==================== 任务列表表格 ==================== -->
    <div class="table-card">
      <el-table
        :data="tasks"
        v-loading="tableLoading"
        stripe
        style="width: 100%"
        empty-text="暂无定时任务，点击右上角「新建任务」创建"
      >
        <el-table-column label="ID" prop="id" width="65" align="center" />

        <el-table-column label="CD2 扫描目录" prop="directory_path" min-width="260">
          <template #default="{ row }">
            <div class="cell-path">
              <code class="path-code">{{ row.directory_path }}</code>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="Cron 表达式" prop="cron_expression" width="160">
          <template #default="{ row }">
            <code class="cron-code">{{ row.cron_expression }}</code>
          </template>
        </el-table-column>

        <el-table-column label="启用" prop="is_active" width="80" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.is_active"
              size="small"
              @change="(val) => handleSwitchChange(row, val)"
            />
          </template>
        </el-table-column>

        <el-table-column label="上次执行" prop="last_run_at" width="170">
          <template #default="{ row }">
            <span class="time-text">{{ formatTime(row.last_run_at) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="创建时间" prop="created_at" width="170">
          <template #default="{ row }">
            <span class="time-text">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <div class="action-btns">
              <el-button size="small" type="primary" link @click="openEditDialog(row)">
                <el-icon><Edit /></el-icon>
                编辑
              </el-button>
              <el-button size="small" type="success" link @click="handleRun(row)">
                <el-icon><VideoPlay /></el-icon>
                手动扫描
              </el-button>
              <el-button size="small" type="warning" link @click="openLogDrawer(row)">
                <el-icon><View /></el-icon>
                日志
              </el-button>
              <el-button size="small" type="danger" link @click="handleDelete(row)">
                <el-icon><Delete /></el-icon>
                删除
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- ==================== 新建/编辑弹窗 ==================== -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="560px"
      destroy-on-close
      :close-on-click-modal="false"
    >
      <el-form :model="form" label-position="top" class="task-form">
        <el-form-item label="CD2 扫描目录路径" required>
          <el-input
            v-model="form.directory_path"
            placeholder="例如：/80003588/emby库/电视剧/国产剧/"
            clearable
          >
            <template #prepend>
              <el-icon><Document /></el-icon>
            </template>
          </el-input>
          <div class="form-hint">
            按 BFS（最大深度 4 层）遍历该目录树。命中条件：文件夹名含「tmdb」或直接父目录为 4 位年份。命中后严格串行调用洗版（间隔 3s）。
          </div>
        </el-form-item>

        <el-form-item label="Cron 表达式" required>
          <el-input
            v-model="form.cron_expression"
            placeholder="0 2 * * *"
            clearable
          >
            <template #prepend>
              <el-icon><Timer /></el-icon>
            </template>
          </el-input>
          <div class="cron-hint">
            <div class="cron-hint-title">常用示例：</div>
            <div class="cron-hint-grid">
              <code>0 2 * * *</code><span>每天凌晨 2:00</span>
              <code>0 */6 * * *</code><span>每 6 小时</span>
              <code>0 8 * * 1</code><span>每周一 8:00</span>
              <code>0 0 1 * *</code><span>每月 1 号 0:00</span>
              <code>*/30 * * * *</code><span>每 30 分钟</span>
              <code>0 2 * * 0</code><span>每周日 2:00</span>
            </div>
            <div class="cron-hint-format">
              格式：<strong>分 时 日 月 周</strong>（5 位标准 Cron）
            </div>
          </div>
        </el-form-item>

        <el-form-item label="启用状态">
          <el-switch
            v-model="form.is_active"
            active-text="启用"
            inactive-text="停用"
          />
          <span class="switch-desc">停用后任务将保留但不会被调度执行</span>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="formLoading" @click="handleFormSubmit">
          {{ isEditing ? '更新' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ==================== 日志抽屉 ==================== -->
    <el-drawer
      v-model="logDrawerVisible"
      :title="logDrawerTitle"
      direction="rtl"
      size="520px"
      destroy-on-close
    >
      <div class="log-drawer-body" v-loading="logLoading">
        <template v-if="logs.length === 0 && !logLoading">
          <div class="log-empty">
            <el-icon :size="40"><Document /></el-icon>
            <p>暂无扫描记录</p>
          </div>
        </template>

        <template v-else>
          <div class="log-list">
            <div
              v-for="log in logs"
              :key="log.id"
              class="log-card"
              :class="'log-card--' + log.status.toLowerCase()"
            >
              <!-- 日志头部 -->
              <div class="log-card-header">
                <div class="log-card-left">
                  <el-icon :size="18" :color="log.status === 'SUCCESS' ? '#10b981' : log.status === 'FAILED' ? '#ef4444' : '#f59e0b'">
                    <component :is="getLogStatusIcon(log.status)" />
                  </el-icon>
                  <span class="log-status-text">{{ log.status }}</span>
                  <el-tag
                    :color="getTriggerTypeColor(log.trigger_type)"
                    size="small"
                    effect="dark"
                    round
                    class="log-trigger-tag"
                  >
                    {{ getTriggerTypeLabel(log.trigger_type) }}
                  </el-tag>
                </div>
                <span class="log-time">{{ formatTime(log.created_at) }}</span>
              </div>

              <!-- 统计摘要 -->
              <div class="log-card-stats">
                <div class="stat-item">
                  <span class="stat-label">扫描目录数</span>
                  <span class="stat-value">{{ log.scanned_count }}</span>
                </div>
                <div class="stat-item">
                  <span class="stat-label">成功处理</span>
                  <span class="stat-value stat-value--success">{{ log.processed_count }}</span>
                </div>
                <div class="stat-item">
                  <span class="stat-label">失败/跳过</span>
                  <span class="stat-value stat-value--error">
                    {{ (log.details?.errors || 0) + (log.scanned_count - log.processed_count - (log.details?.errors || 0)) }}
                  </span>
                </div>
              </div>

              <!-- 折叠详情 -->
              <div v-if="log.details && Object.keys(log.details).length > 0" class="log-card-detail">
                <el-button
                  size="small"
                  text
                  type="primary"
                  @click="toggleLogDetail(log.id)"
                >
                  <el-icon><InfoFilled /></el-icon>
                  {{ expandedLogIds[log.id] ? '收起详情' : '查看详情' }}
                </el-button>
                <div v-if="expandedLogIds[log.id]" class="log-detail-content">
                  <!-- items 列表：每项一条简明记录 -->
                  <template v-if="log.details.items && log.details.items.length > 0">
                    <div class="log-items-title">处理清单 ({{ log.details.items.length }} 项)</div>
                    <div
                      v-for="(item, idx) in log.details.items"
                      :key="idx"
                      class="log-item-row"
                      :class="{ 'log-item-row--fail': !item.success }"
                    >
                      <span class="log-item-idx">{{ idx + 1 }}.</span>
                      <span class="log-item-dir">{{ item.dir_name }}</span>
                      <el-tag
                        :type="item.success ? 'success' : 'danger'"
                        size="small"
                        effect="plain"
                        round
                      >
                        {{ item.success ? 'OK' : item.stage || 'FAIL' }}
                      </el-tag>
                      <span v-if="!item.success && item.message" class="log-item-msg">{{ item.message }}</span>
                    </div>
                  </template>
                  <!-- 完整 JSON -->
                  <pre class="log-detail-json">{{ formatDetailJson(log.details) }}</pre>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
/* ==================== 页面根容器 ==================== */
.scheduler-root {
  padding: 20px 24px;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 20px;
  background-color: var(--bg-primary);
}

/* ==================== 页面标题栏 ==================== */
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.page-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
}

.subtitle {
  font-size: 13px;
  color: var(--text-tertiary);
}

.header-right {
  display: flex;
  gap: 10px;
}

/* ==================== 表格卡片 ==================== */
.table-card {
  flex: 1;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

/* Path code */
.cell-path {
  max-width: 280px;
}

.path-code {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 12px;
  background: var(--bg-overlay);
  padding: 3px 8px;
  border-radius: 4px;
  color: var(--accent-blue);
  word-break: break-all;
  display: inline-block;
  line-height: 1.5;
}

.cron-code {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 13px;
  background: var(--bg-overlay);
  padding: 3px 8px;
  border-radius: 4px;
  color: var(--accent-purple);
}

.time-text {
  font-size: 12px;
  color: var(--text-secondary);
  font-family: 'SF Mono', 'Fira Code', monospace;
}

/* Action buttons */
.action-btns {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

/* ==================== 表单弹窗 ==================== */
.task-form {
  padding: 8px 0;
}

.form-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 4px;
  line-height: 1.5;
}

.cron-hint {
  background: var(--bg-overlay);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  margin-top: 6px;
  font-size: 12px;
}

.cron-hint-title {
  color: var(--text-secondary);
  font-weight: 600;
  margin-bottom: 6px;
}

.cron-hint-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin-bottom: 8px;
}

.cron-hint-grid code {
  font-family: 'SF Mono', 'Fira Code', monospace;
  color: var(--accent-purple);
  font-size: 12px;
}

.cron-hint-grid span {
  color: var(--text-tertiary);
}

.cron-hint-format {
  color: var(--text-tertiary);
  font-size: 11px;
  border-top: 1px solid var(--border-color);
  padding-top: 6px;
}

.switch-desc {
  margin-left: 12px;
  font-size: 12px;
  color: var(--text-tertiary);
}

/* ==================== 日志抽屉 ==================== */
.log-drawer-body {
  padding: 8px 0;
}

.log-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  color: var(--text-tertiary);
  gap: 12px;
}

.log-empty p {
  font-size: 14px;
}

.log-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 日志卡片 */
.log-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  transition: border-color 0.2s;
}

.log-card--success {
  border-left: 3px solid var(--accent-green);
}

.log-card--failed {
  border-left: 3px solid var(--accent-red);
}

.log-card--running {
  border-left: 3px solid var(--accent-yellow);
}

.log-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.log-card-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.log-status-text {
  font-weight: 700;
  font-size: 14px;
  color: var(--text-primary);
}

.log-trigger-tag {
  font-size: 11px !important;
}

.log-time {
  font-size: 12px;
  color: var(--text-tertiary);
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.log-card-stats {
  display: flex;
  gap: 20px;
  margin-bottom: 8px;
}

.stat-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-label {
  font-size: 11px;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.stat-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
}

.stat-value--success {
  color: var(--accent-green);
}

.stat-value--error {
  color: var(--accent-red);
}

/* 日志详情 */
.log-card-detail {
  margin-top: 8px;
}

.log-detail-content {
  margin-top: 8px;
}

.log-items-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 6px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--border-color);
}

.log-item-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  font-size: 12px;
  border-bottom: 1px solid rgba(51, 65, 85, 0.3);
}

.log-item-row:last-child {
  border-bottom: none;
}

.log-item-row--fail {
  background: rgba(239, 68, 68, 0.05);
  border-radius: 4px;
  padding: 5px 6px;
}

.log-item-idx {
  color: var(--text-tertiary);
  min-width: 24px;
  font-family: 'SF Mono', monospace;
}

.log-item-dir {
  flex: 1;
  color: var(--text-primary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.log-item-msg {
  color: var(--accent-red);
  max-width: 180px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.log-detail-json {
  margin-top: 8px;
  background: #0b1120;
  color: #94a3b8;
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 11px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  overflow-x: auto;
  max-height: 300px;
  white-space: pre;
  border: 1px solid var(--border-color);
}

/* ==================== Element Plus overrides ==================== */
:deep(.el-table) {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: var(--bg-overlay);
  --el-table-row-hover-bg-color: var(--bg-card-hover);
  --el-table-border-color: var(--border-color);
  --el-table-text-color: var(--text-primary);
  --el-table-header-text-color: var(--text-secondary);
}

:deep(.el-drawer) {
  --el-drawer-bg-color: var(--bg-primary);
}

:deep(.el-drawer__header) {
  margin-bottom: 0;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
  color: var(--text-primary);
}

:deep(.el-drawer__body) {
  padding: 16px 20px;
}

/* ==================== 移动端响应式 ==================== */
@media (max-width: 768px) {
  .scheduler-root {
    padding: 12px;
    gap: 12px;
  }

  .page-header {
    flex-direction: column;
  }

  .header-right {
    width: 100%;
  }

  .header-right .el-button {
    flex: 1;
  }

  .page-title {
    font-size: 17px;
  }

  .action-btns {
    gap: 2px;
  }

  .action-btns .el-button {
    font-size: 11px;
    padding: 4px 6px;
  }

  .log-card-stats {
    gap: 12px;
  }

  .stat-value {
    font-size: 16px;
  }
}
</style>
