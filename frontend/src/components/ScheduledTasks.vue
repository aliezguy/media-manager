<script setup>
import { ref, reactive, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, Refresh, Delete, View, Edit, Timer, VideoPlay,
  Clock, CircleCheck, CircleClose, InfoFilled, WarningFilled,
  SuccessFilled, RemoveFilled, Document, Setting, Brush, DataAnalysis,
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

// ==================== 汉化与审计定时任务 ====================
const activeTab = ref('scan')
const libraries = ref([])

const localizationForm = reactive({
  library_ids: [], cron_expression: '0 3 * * *', is_active: false,
  last_run_at: null, next_run_at: null,
})
const auditForm = reactive({
  library_ids: [], cron_expression: '0 4 * * *', is_active: false,
  last_run_at: null, next_run_at: null,
})
const localizationPreset = ref('daily')
const auditPreset = ref('daily')
const saving = reactive({ localization_job: false, audit_job: false })
const running = reactive({ localization_job: false, audit_job: false })

// 友好预设 → Cron 表达式映射
const PRESETS = { daily: '0 3 * * *', weekly: '0 3 * * 1' }

const getJobForm = (key) => (key === 'localization_job' ? localizationForm : auditForm)

const fetchJobConfigs = async () => {
  try {
    const res = await axios.get(`${API_URL}/api/jobs/config`)
    const data = res.data || {}
    Object.assign(localizationForm, data.localization_job || {})
    Object.assign(auditForm, data.audit_job || {})
    // 反推预设：cron 命中预设值则显示 daily/weekly，否则显示自定义
    localizationPreset.value =
      PRESETS.daily === localizationForm.cron_expression ? 'daily'
      : PRESETS.weekly === localizationForm.cron_expression ? 'weekly' : 'custom'
    auditPreset.value =
      PRESETS.daily === auditForm.cron_expression ? 'daily'
      : PRESETS.weekly === auditForm.cron_expression ? 'weekly' : 'custom'
  } catch (e) {
    ElMessage.error('获取任务配置失败')
  }
}

const saveJob = async (key) => {
  const form = getJobForm(key)
  if (!form.library_ids || form.library_ids.length === 0) {
    ElMessage.warning('请先选择至少一个媒体库')
    return
  }
  saving[key] = true
  try {
    const res = await axios.put(`${API_URL}/api/jobs/config`, {
      [key]: {
        library_ids: form.library_ids,
        cron_expression: form.cron_expression,
        is_active: form.is_active,
      },
    })
    Object.assign(form, res.data[key] || {})
    ElMessage.success('配置已保存，将按所选媒体库逐个串行执行')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving[key] = false
  }
}

const runJob = async (key) => {
  running[key] = true
  try {
    await axios.post(`${API_URL}/api/jobs/${key}/run`)
    ElMessage.success('任务已触发，可在大盘查看进度')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '触发失败')
  } finally {
    running[key] = false
  }
}

const toggleActive = (key) => {
  saveJob(key)
}

const applyPreset = (key, preset) => {
  const form = getJobForm(key)
  if (PRESETS[preset]) form.cron_expression = PRESETS[preset]
}

const loadLibraries = async () => {
  try {
    const cfgRes = await axios.get(`${API_URL}/api/config`)
    const cfg = cfgRes.data || {}
    const libRes = await axios.post(`${API_URL}/api/libraries`, {
      emby_host: cfg.emby_host,
      emby_api_key: cfg.emby_api_key,
    })
    libraries.value = Array.isArray(libRes.data) ? libRes.data : []
  } catch (e) {
    libraries.value = []
  }
}

const refreshCurrent = () => {
  if (activeTab.value === 'scan') {
    loadTasks()
  } else {
    fetchJobConfigs()
    loadLibraries()
  }
}

// ==================== 生命周期 ====================
onMounted(() => {
  loadTasks()
  fetchJobConfigs()
  loadLibraries()
})
</script>

<template>
  <div class="scheduler-root">
    <!-- ==================== 页面标题栏 ==================== -->
    <div class="page-header">
      <div class="header-left">
        <h2 class="page-title">
          <span class="title-badge"><Timer :size="18" /></span>
          定时任务配置
        </h2>
        <span class="subtitle">管理 CD2 定时扫描、全量汉化与全量审计任务</span>
      </div>
      <div class="header-right">
        <button class="hd-btn hd-ghost h-9" @click="refreshCurrent" :disabled="tableLoading">
          <Refresh :size="15" :class="{ spin: tableLoading }" />
          <span>刷新</span>
        </button>
        <button
          v-if="activeTab === 'scan'"
          class="inline-flex items-center gap-2 h-9 px-4 rounded-lg bg-blue-600/20 text-blue-400 border border-blue-500/40 hover:bg-blue-600/40 hover:text-white hover:border-blue-400 shadow-[0_0_10px_rgba(59,130,246,0.2)] transition-all text-[13px] font-semibold cursor-pointer whitespace-nowrap"
          @click="openCreateDialog"
        >
          <Plus :size="15" />
          <span>新建任务</span>
        </button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="scheduler-tabs">
      <!-- ==================== Tab 1: CD2 定时扫描 ==================== -->
      <el-tab-pane label="CD2 定时扫描" name="scan">
        <!-- ==================== 任务列表 — 毛玻璃卡片 ==================== -->
        <div class="cron-panel" v-loading="tableLoading">
          <div class="cron-list">
            <div
              v-for="task in tasks"
              :key="task.id"
              class="cron-row bg-white/5 even:bg-white/[0.02] hover:bg-white/10"
            >
              <!-- ID -->
              <span class="cron-id">#{{ task.id }}</span>

              <!-- 扫描目录 -->
              <div class="cron-dir">
                <code class="path-code">{{ task.directory_path }}</code>
              </div>

              <!-- Cron 药丸 + 执行时间 -->
              <div class="cron-mid">
                <code class="cron-pill">{{ task.cron_expression }}</code>
                <span class="cron-time">
                  <span class="cron-time-label">上次执行</span>
                  <span class="cron-time-val">{{ formatTime(task.last_run_at) }}</span>
                </span>
                <span class="cron-time">
                  <span class="cron-time-label">创建时间</span>
                  <span class="cron-time-val">{{ formatTime(task.created_at) }}</span>
                </span>
              </div>

              <!-- 启用开关 -->
              <div class="cron-toggle">
                <el-switch
                  :model-value="task.is_active"
                  size="small"
                  @change="(val) => handleSwitchChange(task, val)"
                />
              </div>

              <!-- 操作 — 圆形幽灵按钮 -->
              <div class="cron-actions">
                <button type="button" class="act-btn act-edit" title="编辑任务" @click="openEditDialog(task)">
                  <Edit :size="15" />
                </button>
                <button type="button" class="act-btn act-run" title="手动扫描" @click="handleRun(task)">
                  <VideoPlay :size="15" />
                </button>
                <button type="button" class="act-btn act-log" title="查看日志" @click="openLogDrawer(task)">
                  <View :size="15" />
                </button>
                <button type="button" class="act-btn act-del" title="删除任务" @click="handleDelete(task)">
                  <Delete :size="15" />
                </button>
              </div>
            </div>

            <!-- 空状态 -->
            <div v-if="!tableLoading && tasks.length === 0" class="cron-empty">
              <div class="cron-empty-icon"><Document :size="34" /></div>
              <p>暂无定时任务，点击右上角「新建任务」创建</p>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- ==================== Tab 2: 汉化与审计 ==================== -->
      <el-tab-pane label="汉化与审计" name="maintenance">
        <div class="maintenance-grid">
          <!-- ① 全量汉化定时任务 -->
          <div class="section-card">
            <div class="section-header">
              <span class="section-icon"><Brush :size="18" /></span>
              <div class="section-heading">
                <span class="section-title">全量汉化定时任务</span>
                <span class="section-en">Full Localization</span>
              </div>
              <div class="section-switch">
                <el-switch
                  v-model="localizationForm.is_active"
                  @change="toggleActive('localization_job')"
                />
              </div>
            </div>

            <div class="section-body">
              <el-form label-position="top">
                <el-form-item label="媒体库">
                  <el-select
                    v-model="localizationForm.library_ids"
                    placeholder="选择媒体库（可多选）"
                    clearable
                    filterable
                    multiple
                    collapse-tags
                    collapse-tags-tooltip
                    style="width: 100%"
                  >
                    <el-option
                      v-for="lib in libraries"
                      :key="lib.ItemId"
                      :label="lib.Name"
                      :value="lib.ItemId"
                    />
                  </el-select>
                  <div class="tip">支持多选媒体库，任务将按所选媒体库<b>逐个串行执行</b>（一个完成后再执行下一个）。对选中库中所有「未汉化」媒体项执行演员中文化。需先在「Emby」设置中配置连接。</div>
                </el-form-item>

                <el-form-item label="执行周期">
                  <el-radio-group
                    v-model="localizationPreset"
                    @change="(v) => applyPreset('localization_job', v)"
                  >
                    <el-radio-button value="daily">每天</el-radio-button>
                    <el-radio-button value="weekly">每周一</el-radio-button>
                    <el-radio-button value="custom">自定义</el-radio-button>
                  </el-radio-group>
                  <el-input
                    v-if="localizationPreset === 'custom'"
                    v-model="localizationForm.cron_expression"
                    placeholder="0 3 * * *"
                    class="cron-input"
                    clearable
                  />
                  <div class="cron-hint">
                    格式：<strong>分 时 日 月 周</strong> · 例如 <code>0 3 * * *</code> = 每天凌晨 3:00
                  </div>
                </el-form-item>

                <div class="run-times">
                  <div class="run-time-item">
                    <span class="run-label">上次执行</span>
                    <code class="run-value">{{ formatTime(localizationForm.last_run_at) }}</code>
                  </div>
                  <div class="run-time-item">
                    <span class="run-label">下次预计</span>
                    <code class="run-value">{{ formatTime(localizationForm.next_run_at) }}</code>
                  </div>
                </div>
              </el-form>

              <div class="actions">
                <el-button class="act-ghost" @click="runJob('localization_job')" :loading="running.localization_job">
                  <el-icon><VideoPlay /></el-icon>
                  立即执行一次
                </el-button>
                <el-button type="primary" class="act-primary" :loading="saving.localization_job" @click="saveJob('localization_job')">
                  <el-icon><Setting /></el-icon>
                  保存配置
                </el-button>
              </div>
            </div>
          </div>

          <!-- ② 全量审计定时任务 -->
          <div class="section-card">
            <div class="section-header">
              <span class="section-icon purple"><DataAnalysis :size="18" /></span>
              <div class="section-heading">
                <span class="section-title">全量审计定时任务</span>
                <span class="section-en">Full Audit</span>
              </div>
              <div class="section-switch">
                <el-switch
                  v-model="auditForm.is_active"
                  @change="toggleActive('audit_job')"
                />
              </div>
            </div>

            <div class="section-body">
              <el-form label-position="top">
                <el-form-item label="媒体库">
                  <el-select
                    v-model="auditForm.library_ids"
                    placeholder="选择媒体库（可多选）"
                    clearable
                    filterable
                    multiple
                    collapse-tags
                    collapse-tags-tooltip
                    style="width: 100%"
                  >
                    <el-option
                      v-for="lib in libraries"
                      :key="lib.ItemId"
                      :label="lib.Name"
                      :value="lib.ItemId"
                    />
                  </el-select>
                  <div class="tip">支持多选媒体库，任务将按所选媒体库<b>逐个串行执行</b>（一个完成后再执行下一个）。比对所选媒体库与本地数据库，自动补齐缺失媒体项（审计入库）。</div>
                </el-form-item>

                <el-form-item label="执行周期">
                  <el-radio-group
                    v-model="auditPreset"
                    @change="(v) => applyPreset('audit_job', v)"
                  >
                    <el-radio-button value="daily">每天</el-radio-button>
                    <el-radio-button value="weekly">每周一</el-radio-button>
                    <el-radio-button value="custom">自定义</el-radio-button>
                  </el-radio-group>
                  <el-input
                    v-if="auditPreset === 'custom'"
                    v-model="auditForm.cron_expression"
                    placeholder="0 4 * * *"
                    class="cron-input"
                    clearable
                  />
                  <div class="cron-hint">
                    格式：<strong>分 时 日 月 周</strong> · 例如 <code>0 4 * * *</code> = 每天凌晨 4:00
                  </div>
                </el-form-item>

                <div class="run-times">
                  <div class="run-time-item">
                    <span class="run-label">上次执行</span>
                    <code class="run-value">{{ formatTime(auditForm.last_run_at) }}</code>
                  </div>
                  <div class="run-time-item">
                    <span class="run-label">下次预计</span>
                    <code class="run-value">{{ formatTime(auditForm.next_run_at) }}</code>
                  </div>
                </div>
              </el-form>

              <div class="actions">
                <el-button class="act-ghost" @click="runJob('audit_job')" :loading="running.audit_job">
                  <el-icon><VideoPlay /></el-icon>
                  立即执行一次
                </el-button>
                <el-button type="primary" class="act-primary" :loading="saving.audit_job" @click="saveJob('audit_job')">
                  <el-icon><Setting /></el-icon>
                  保存配置
                </el-button>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

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
        <el-button class="act-ghost" @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" class="act-primary" :loading="formLoading" @click="handleFormSubmit">
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
            <div class="log-empty-icon"><Document :size="40" /></div>
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
                  <span
                    class="log-status-dot"
                    :style="{ color: log.status === 'SUCCESS' ? '#10b981' : log.status === 'FAILED' ? '#ef4444' : '#f59e0b' }"
                  >
                    <component :is="getLogStatusIcon(log.status)" :size="18" />
                  </span>
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
                <button type="button" class="log-detail-toggle" @click="toggleLogDetail(log.id)">
                  <InfoFilled :size="13" />
                  {{ expandedLogIds[log.id] ? '收起详情' : '查看详情' }}
                </button>
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
  background: radial-gradient(ellipse 70% 45% at 85% -5%, rgba(59, 130, 246, 0.08), transparent 65%),
              radial-gradient(ellipse 60% 40% at 0% 100%, rgba(139, 92, 246, 0.07), transparent 60%),
              var(--bg-primary);
}

/* ==================== 页面标题栏 ==================== */
.page-header {
  display: flex;
  align-items: center; /* 标题与右侧操作按钮绝对垂直对齐 */
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.page-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 20px;
  font-weight: 800;
  letter-spacing: 0.3px;
  color: #fff;
  margin: 0;
}

.title-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px; height: 34px;
  border-radius: 10px;
  color: #60a5fa;
  background: rgba(59, 130, 246, 0.14);
  border: 1px solid rgba(59, 130, 246, 0.35);
  box-shadow: 0 0 14px rgba(59, 130, 246, 0.3), inset 0 0 8px rgba(59, 130, 246, 0.08);
}

.subtitle {
  font-size: 13px;
  color: #64748b;
}

.header-right {
  display: flex;
  gap: 10px;
}

/* 头部按钮 — 玻璃幽灵 / 电光蓝主按钮 */
.hd-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 9px 18px;
  border-radius: 12px;
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.22s cubic-bezier(0.4, 0, 0.2, 1);
}
.hd-ghost {
  color: #94a3b8;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
}
.hd-ghost:hover:not(:disabled) {
  color: #e2e8f0;
  background: rgba(255, 255, 255, 0.09);
  border-color: rgba(255, 255, 255, 0.18);
}
.hd-ghost:disabled { opacity: 0.5; cursor: not-allowed; }

/* ==================== 任务列表 — 毛玻璃卡片 ==================== */
.cron-panel {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  border-radius: 20px;
}

.cron-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px;
}

.cron-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 13px 16px;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(14px) saturate(140%);
  -webkit-backdrop-filter: blur(14px) saturate(140%);
  transition: border-color 0.22s ease, box-shadow 0.22s ease, background 0.22s ease;
}
.cron-row:hover {
  border-color: rgba(59, 130, 246, 0.4);
  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.15), 0 10px 30px -16px rgba(59, 130, 246, 0.3);
}

.cron-id {
  flex-shrink: 0;
  min-width: 44px;
  font-size: 12px;
  font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
  color: #475569;
}

.cron-dir {
  flex: 1;
  min-width: 0;
}

.path-code {
  display: inline-block;
  max-width: 100%;
  font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12.5px;
  color: #60a5fa;
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.2);
  padding: 4px 10px;
  border-radius: 8px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: middle;
}

/* Cron 表达式 — 等宽极客字体 + 深紫发光药丸 */
.cron-pill {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #c4b5fd;
  background: rgba(139, 92, 246, 0.12);
  border: 1px solid rgba(139, 92, 246, 0.32);
  border-radius: 999px;
  padding: 5px 14px;
  box-shadow: 0 0 14px rgba(139, 92, 246, 0.28), inset 0 0 8px rgba(139, 92, 246, 0.08);
  white-space: nowrap;
}

.cron-mid {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-shrink: 0;
}

.cron-time {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.cron-time-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  color: #475569;
}
.cron-time-val {
  font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
  font-size: 11.5px;
  color: #94a3b8;
  white-space: nowrap;
}

.cron-toggle {
  flex-shrink: 0;
}

/* 操作按钮 — 圆形幽灵按钮 + Hover 状态光 + 放大动效 */
.cron-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.act-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
  color: #64748b;
  cursor: pointer;
  transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1), background 0.2s ease, color 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
}
.act-btn:hover {
  background: rgba(255, 255, 255, 0.08); /* hover:bg-white/5 微提亮 */
  transform: scale(1.12);                /* hover:scale-110 放大 */
}
.act-edit:hover {
  color: #60a5fa;
  border-color: rgba(59, 130, 246, 0.4);
  background: rgba(59, 130, 246, 0.14);
  box-shadow: 0 0 14px rgba(59, 130, 246, 0.35);
}
.act-run:hover {
  color: #34d399;
  border-color: rgba(52, 211, 153, 0.4);
  background: rgba(16, 185, 129, 0.14);
  box-shadow: 0 0 14px rgba(52, 211, 153, 0.4);
}
.act-log:hover {
  color: #fbbf24;
  border-color: rgba(245, 158, 11, 0.4);
  background: rgba(245, 158, 11, 0.13);
  box-shadow: 0 0 14px rgba(245, 158, 11, 0.32);
}
.act-del:hover {
  color: #f87171;
  border-color: rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.13);
  box-shadow: 0 0 14px rgba(239, 68, 68, 0.32);
}
.act-btn:active { transform: scale(0.96); }

/* 空状态 */
.cron-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 64px 0;
  color: #475569;
  border: 1px dashed rgba(255, 255, 255, 0.08);
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.02);
}
.cron-empty-icon {
  display: flex;
  color: #334155;
}
.cron-empty p {
  font-size: 13px;
  letter-spacing: 0.3px;
}

/* ==================== Tabs — 胶囊风格 ==================== */
.scheduler-tabs {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.scheduler-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.scheduler-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 0;
}

.scheduler-tabs :deep(.el-tabs__item) {
  height: 40px;
  line-height: 40px;
  padding: 0 20px;
  margin-right: 6px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  color: #64748b;
  transition: all 0.22s ease;
}
.scheduler-tabs :deep(.el-tabs__item:hover) { color: #cbd5e1; }
.scheduler-tabs :deep(.el-tabs__item.is-active) {
  color: #93c5fd;
  background: rgba(59, 130, 246, 0.14);
  box-shadow: 0 0 14px rgba(59, 130, 246, 0.25), inset 0 0 8px rgba(59, 130, 246, 0.06);
}
.scheduler-tabs :deep(.el-tabs__active-bar) { display: none; }

.scheduler-tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.scheduler-tabs :deep(.el-tab-pane) {
  min-height: 100%;
  display: flex;
  flex-direction: column;
}

/* ==================== 汉化与审计 — 毛玻璃卡片网格 ==================== */
/* items-stretch：双栏等高；移动端 1 列，lg 起 2 列 */
.maintenance-grid {
  @apply grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch;
}

/* 卡片根节点：h-full 填满网格行 + flex 纵向布局，让底部操作栏能对齐 */
.section-card {
  @apply h-full flex flex-col;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 20px;
  backdrop-filter: blur(18px) saturate(140%);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  box-shadow: 0 20px 50px -22px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.06);
  overflow: hidden;
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
}
.section-card:hover {
  border-color: rgba(59, 130, 246, 0.3);
  box-shadow: 0 24px 60px -22px rgba(59, 130, 246, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.06);
}

.section-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.section-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px; height: 38px;
  flex-shrink: 0;
  border-radius: 11px;
  color: #60a5fa;
  background: rgba(59, 130, 246, 0.14);
  border: 1px solid rgba(59, 130, 246, 0.3);
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.22);
}
.section-icon.purple {
  color: #a78bfa;
  background: rgba(139, 92, 246, 0.14);
  border-color: rgba(139, 92, 246, 0.32);
  box-shadow: 0 0 12px rgba(139, 92, 246, 0.22);
}

.section-heading {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.section-title {
  font-size: 15px;
  font-weight: 700;
  color: #f1f5f9;
  letter-spacing: 0.3px;
}

.section-en {
  font-size: 10px;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 1px;
}

.section-switch {
  margin-left: auto;
  flex-shrink: 0;
}

.section-body {
  @apply flex-1 flex flex-col;
  padding: 20px;
}

/* 表单内容区 flex-1 撑开剩余高度，把底部操作栏推到卡片底部并水平对齐 */
.section-body :deep(.el-form) {
  flex: 1;
  min-height: 0;
}

/* 分段控制器 — 胶囊式 (el-radio-button 重定义) */
.section-body :deep(.el-radio-group) {
  display: inline-flex;
  padding: 3px;
  gap: 2px;
  background: rgba(2, 6, 23, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.35);
}
.section-body :deep(.el-radio-button) {
  --el-radio-button-bg-color: transparent;
  --el-radio-button-checked-bg-color: rgba(59, 130, 246, 0.2);
  --el-radio-button-checked-text-color: #93c5fd;
  --el-radio-button-checked-border-color: rgba(59, 130, 246, 0.4);
  --el-radio-button-text-color: #64748b;
}
.section-body :deep(.el-radio-button__inner) {
  border: none;
  border-radius: 999px;
  padding: 7px 20px;
  background: transparent;
  color: #64748b;
  font-weight: 600;
  box-shadow: none !important;
  transition: all 0.2s ease;
}
.section-body :deep(.el-radio-button__inner:hover) { color: #cbd5e1; }
.section-body :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: rgba(59, 130, 246, 0.2);
  color: #93c5fd;
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.35), inset 0 0 6px rgba(59, 130, 246, 0.1) !important;
}

.cron-input {
  margin-top: 12px;
}

.cron-hint {
  font-size: 12px;
  color: #64748b;
  margin-top: 8px;
  line-height: 1.5;
}

.cron-hint code {
  font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
  color: #a78bfa;
  background: rgba(139, 92, 246, 0.1);
  padding: 1px 6px;
  border-radius: 6px;
}

/* 执行时间 */
.run-times {
  display: flex;
  gap: 36px;
  background: rgba(2, 6, 23, 0.45);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 12px;
  padding: 12px 16px;
  margin-top: 4px;
}

.run-time-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.run-label {
  font-size: 10px;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.8px;
}

.run-value {
  font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  color: #94a3b8;
}

.actions {
  margin-top: auto; /* 推到卡片底部，双栏操作栏水平对齐 */
  padding-top: 18px;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

/* el-button 定制 — 玻璃 / 电光蓝 */
.act-ghost.el-button {
  --el-button-bg-color: rgba(255, 255, 255, 0.05);
  --el-button-border-color: rgba(255, 255, 255, 0.12);
  --el-button-text-color: #94a3b8;
  --el-button-hover-bg-color: rgba(255, 255, 255, 0.09);
  --el-button-hover-border-color: rgba(255, 255, 255, 0.2);
  --el-button-hover-text-color: #e2e8f0;
  border-radius: 10px;
  font-weight: 600;
}
.act-primary.el-button {
  --el-button-bg-color: linear-gradient(135deg, #3b82f6, #8b5cf6);
  border: none;
  border-radius: 10px;
  font-weight: 600;
  box-shadow: 0 4px 16px rgba(59, 130, 246, 0.35);
}
.act-primary.el-button:hover {
  box-shadow: 0 8px 26px rgba(99, 102, 241, 0.55);
}

/* ==================== 表单弹窗 ==================== */
.task-form {
  padding: 8px 0;
}

.form-hint {
  font-size: 12px;
  color: #64748b;
  margin-top: 4px;
  line-height: 1.6;
}

.cron-hint {
  background: rgba(2, 6, 23, 0.4);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 12px 14px;
  margin-top: 8px;
  font-size: 12px;
}

.cron-hint-title {
  color: #94a3b8;
  font-weight: 600;
  margin-bottom: 8px;
  font-size: 12px;
}

.cron-hint-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 5px 14px;
  margin-bottom: 10px;
}

.cron-hint-grid code {
  font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
  color: #a78bfa;
  font-size: 12px;
}

.cron-hint-grid span {
  color: #64748b;
}

.cron-hint-format {
  color: #64748b;
  font-size: 11px;
  border-top: 1px solid rgba(255, 255, 255, 0.07);
  padding-top: 8px;
}

.switch-desc {
  margin-left: 12px;
  font-size: 12px;
  color: #64748b;
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
  color: #475569;
  gap: 14px;
}
.log-empty-icon { display: flex; color: #334155; }
.log-empty p { font-size: 14px; }

.log-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 日志卡片 — 毛玻璃 */
.log-card {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  padding: 15px 16px;
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  transition: border-color 0.2s;
}
.log-card--success { border-left: 3px solid rgba(16, 185, 129, 0.85); }
.log-card--failed { border-left: 3px solid rgba(239, 68, 68, 0.85); }
.log-card--running { border-left: 3px solid rgba(245, 158, 11, 0.85); }

.log-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.log-card-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.log-status-dot {
  display: inline-flex;
  align-items: center;
  filter: drop-shadow(0 0 6px currentColor);
}

.log-status-text {
  font-weight: 700;
  font-size: 14px;
  color: #f1f5f9;
  letter-spacing: 0.4px;
}

.log-trigger-tag {
  font-size: 11px !important;
  border: none;
}

.log-time {
  font-size: 11.5px;
  color: #64748b;
  font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
}

.log-card-stats {
  display: flex;
  gap: 26px;
  margin-bottom: 8px;
}

.stat-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-label {
  font-size: 10px;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.7px;
}

.stat-value {
  font-size: 18px;
  font-weight: 700;
  color: #f1f5f9;
}

.stat-value--success {
  color: #34d399;
  text-shadow: 0 0 12px rgba(52, 211, 153, 0.4);
}

.stat-value--error {
  color: #f87171;
  text-shadow: 0 0 12px rgba(239, 68, 68, 0.4);
}

.log-card-detail {
  margin-top: 8px;
}

.log-detail-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: #60a5fa;
  cursor: pointer;
  transition: all 0.2s;
}
.log-detail-toggle:hover {
  background: rgba(59, 130, 246, 0.12);
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.25);
}

.log-detail-content {
  margin-top: 10px;
}

.log-items-title {
  font-size: 12px;
  font-weight: 600;
  color: #94a3b8;
  margin-bottom: 6px;
  padding-bottom: 4px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.log-item-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  font-size: 12px;
  border-bottom: 1px solid rgba(51, 65, 85, 0.25);
}

.log-item-row:last-child { border-bottom: none; }

.log-item-row--fail {
  background: rgba(239, 68, 68, 0.06);
  border-radius: 6px;
  padding: 5px 6px;
}

.log-item-idx {
  color: #475569;
  min-width: 24px;
  font-family: 'JetBrains Mono', monospace;
}

.log-item-dir {
  flex: 1;
  color: #f1f5f9;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.log-item-msg {
  color: #f87171;
  max-width: 180px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.log-detail-json {
  margin-top: 10px;
  background: #0b1120;
  color: #94a3b8;
  font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
  font-size: 11px;
  padding: 12px 14px;
  border-radius: 12px;
  overflow-x: auto;
  max-height: 300px;
  white-space: pre;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

/* ==================== Element Plus overrides ==================== */
:deep(.el-switch) {
  --el-switch-on-color: #3b82f6;
  --el-switch-off-color: rgba(148, 163, 184, 0.25);
  --el-switch-border-color: transparent;
}
:deep(.el-switch.is-checked .el-switch__core) {
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.5);
}

:deep(.el-drawer) { --el-drawer-bg-color: #0f172a; }
:deep(.el-drawer__header) {
  margin-bottom: 0;
  padding-bottom: 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  color: #f1f5f9;
}
:deep(.el-drawer__body) { padding: 16px 20px; }

/* 弹窗 — 深度毛玻璃化 */
:deep(.el-dialog) {
  --el-dialog-bg-color: transparent;
  background: rgba(11, 17, 32, 0.82);
  backdrop-filter: blur(40px) saturate(150%);
  -webkit-backdrop-filter: blur(40px) saturate(150%);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 18px;
  box-shadow: 0 24px 60px -16px rgba(0, 0, 0, 0.6), 0 0 30px rgba(30, 58, 138, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.06);
}
:deep(.el-dialog__header) {
  background: transparent;
  border-bottom: none;
  padding-bottom: 14px;
}
:deep(.el-dialog__title) {
  color: #ffffff;
  font-weight: 700;
  letter-spacing: 0.3px;
}
:deep(.el-dialog__body) { color: #cbd5e1; }
:deep(.el-dialog__footer) {
  background: transparent;
  border-top: none;
  padding-top: 8px;
}
:deep(.el-dialog__headerbtn) { color: #64748b; }
:deep(.el-dialog__headerbtn:hover) { color: #f87171; }

:deep(.el-form-item__label) { color: #94a3b8; }
:deep(.el-input__wrapper) {
  background: rgba(0, 0, 0, 0.25);
  border-radius: 10px;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.12) !important;
}
:deep(.el-input__wrapper:hover) { box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.28) !important; }
:deep(.el-input__wrapper.is-focus) {
  background: rgba(59, 130, 246, 0.06);
  box-shadow: 0 0 0 1px #3b82f6, 0 0 10px rgba(59, 130, 246, 0.4) !important;
}
:deep(.el-input__inner) { color: #f1f5f9; }
:deep(.el-input__inner::placeholder) { color: #475569; }
:deep(.el-input-group__prepend) {
  background: rgba(255, 255, 255, 0.04);
  border-color: rgba(255, 255, 255, 0.09);
  color: #64748b;
}

/* ==================== 动画 ==================== */
@keyframes spin { to { transform: rotate(360deg); } }
.spin { animation: spin 1s linear infinite; }

@media (prefers-reduced-motion: reduce) {
  .spin, .cron-row, .act-btn, .hd-btn, .section-card, .log-card {
    transition: none !important;
    animation: none !important;
  }
}

/* ==================== 移动端响应式 ==================== */
@media (max-width: 768px) {
  .scheduler-root {
    padding: 10px 12px 32px;
    gap: 12px;
  }

  .page-header { flex-direction: column; }
  .header-right { width: 100%; }
  .header-right .hd-btn { flex: 1; justify-content: center; }
  .page-title { font-size: 17px; }

  .cron-row { flex-wrap: wrap; gap: 10px; }
  .cron-dir { flex-basis: 100%; }
  .cron-mid { flex-wrap: wrap; gap: 12px; }

  .section-header { flex-wrap: wrap; }
  .actions { flex-direction: column; }
  .actions .el-button { width: 100%; margin-left: 0 !important; }
  .run-times { gap: 20px; flex-wrap: wrap; }
}
</style>
