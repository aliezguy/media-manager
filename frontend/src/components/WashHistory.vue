<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { Timer, RefreshLeft, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const API_URL = ''
const historyData = ref([])
const loading = ref(false)
const siteOptions = ref([])

const fetchHistory = async () => {
  loading.value = true
  try {
    const res = await axios.get(`${API_URL}/api/history`)
    historyData.value = res.data
  } catch (e) {
    ElMessage.error('获取历史记录失败')
  } finally {
    loading.value = false
  }
}

const clearHistory = async () => {
  try {
    await ElMessageBox.confirm('确定要清空所有记录吗？', '提示', { type: 'warning' })
    await axios.delete(`${API_URL}/api/history`)
    historyData.value = []
    ElMessage.success('已清空')
  } catch {}
}

const formatDate = (dateStr) => {
  const d = new Date(dateStr)
  return d.toLocaleString()
}

const fetchResources = async () => {
  try {
    const res = await axios.get(`${API_URL}/api/resources`)
    if (res.data && res.data.sites) {
      siteOptions.value = res.data.sites
    }
  } catch (e) {
    console.error('获取站点列表失败', e)
  }
}

const formatSiteNames = (siteIds) => {
  if (!siteIds || !Array.isArray(siteIds) || siteIds.length === 0) return ''
  const names = siteIds.map(id => {
    const found = siteOptions.value.find(s => String(s.id) === String(id))
    return found ? found.name : id
  })
  return names.join(' / ')
}

onMounted(() => {
  fetchHistory()
  fetchResources()
})
</script>

<template>
  <div class="history-container">
    <!-- ==================== Toolbar ==================== -->
    <div class="history-toolbar">
      <span class="history-title">
        <span class="title-icon"><el-icon :size="18"><Timer /></el-icon></span>
        订阅任务历史
      </span>
      <div class="btn-group">
        <button class="btn-icon" @click="fetchHistory" title="刷新">
          <el-icon :size="16"><RefreshLeft /></el-icon>
        </button>
        <button class="btn-pill btn-pill-danger" @click="clearHistory">
          <el-icon :size="14"><Delete /></el-icon> 清空
        </button>
      </div>
    </div>

    <!-- ==================== Card List (统一桌面端 + 移动端) ==================== -->
    <div v-loading="loading" class="history-list">
      <div
        v-for="row in historyData"
        :key="row.id"
        class="history-card"
        :class="{ 'card-fail': row.status !== 'success' }"
      >
        <!-- Status indicator bar -->
        <div class="card-status-bar" :class="row.status === 'success' ? 'bar-success' : 'bar-fail'"></div>

        <!-- Card content -->
        <div class="card-inner">
          <!-- Top: name + status + type -->
          <div class="card-header">
            <div class="card-name-group">
              <span class="card-name">{{ row.name }}</span>
              <span class="card-season">S{{ row.season }}</span>
              <span class="card-tmdb">TMDB: {{ row.tmdb_id }}</span>
            </div>
            <div class="card-badges">
              <span class="badge" :class="row.status === 'success' ? 'badge-success' : 'badge-fail'">
                {{ row.status === 'success' ? '成功' : '失败' }}
              </span>
              <span class="badge" :class="row.wash_type === 'complete' ? 'badge-wash' : row.wash_type === 'new_sub' ? 'badge-sub' : 'badge-unknown'">
                {{ row.wash_type === 'complete' ? '完结洗版' : row.wash_type === 'new_sub' ? '新增配置' : '未知' }}
              </span>
            </div>
          </div>

          <!-- Meta: time -->
          <div class="card-meta">
            <el-icon :size="13"><Timer /></el-icon>
            <span>{{ formatDate(row.created_at) }}</span>
          </div>

          <!-- Wash params -->
          <div v-if="row.wash_params" class="card-params">
            <span v-if="row.wash_params.scheme" class="param-chip param-scheme">策略: {{ row.wash_params.scheme }}</span>
            <span v-if="row.wash_params.filter_groups" class="param-chip param-filter">规则: {{ row.wash_params.filter_groups?.join(',') }}</span>
            <span v-if="row.wash_params.downloader" class="param-chip param-downloader">下载器: {{ row.wash_params.downloader }}</span>
            <span v-if="row.wash_params.quality" class="param-chip param-quality">画质: {{ row.wash_params.quality }}</span>
            <span v-if="row.wash_params.sites?.length" class="param-chip param-sites">站点: {{ formatSiteNames(row.wash_params.sites) }}</span>
          </div>

          <!-- Message -->
          <div v-if="row.message" class="card-msg" :class="{ 'msg-error': row.status !== 'success' }">
            {{ row.message }}
          </div>
        </div>
      </div>

      <!-- Empty state -->
      <div v-if="!loading && !historyData.length" class="empty-state">
        <div class="empty-icon-circle">
          <el-icon :size="36"><Timer /></el-icon>
        </div>
        <p class="empty-title">暂无记录</p>
        <p class="empty-desc">还没有任何订阅任务历史</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ==================== Layout ==================== */
.history-container {
  padding: 8px 16px;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  height: 100%;
}

/* ==================== Toolbar ==================== */
.history-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding: 0 4px;
  flex-wrap: wrap;
  gap: 8px;
  flex-shrink: 0;
}

.history-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
}

.title-icon {
  color: var(--accent-blue);
}

.btn-group {
  display: flex;
  gap: 6px;
  align-items: center;
}

/* Icon button */
.btn-icon {
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-full);
  background: var(--bg-card);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s;
}
.btn-icon:hover {
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
}

/* Pill button */
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

.btn-pill-danger {
  background: var(--accent-red-soft);
  color: var(--accent-red);
}
.btn-pill-danger:hover {
  background: rgba(239, 68, 68, 0.3);
}

/* ==================== Card List ==================== */
.history-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* ==================== History Card ==================== */
.history-card {
  display: flex;
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  transition: all 0.2s;
}
.history-card:hover {
  border-color: #475569;
  box-shadow: var(--shadow-sm);
}

/* Left status bar */
.card-status-bar {
  width: 4px;
  flex-shrink: 0;
}
.card-status-bar.bar-success {
  background: var(--accent-green);
}
.card-status-bar.bar-fail {
  background: var(--accent-red);
}

/* Inner content */
.card-inner {
  flex: 1;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

/* Header: name + badges */
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}

.card-name-group {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
}

.card-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.card-season {
  font-size: 12px;
  color: var(--text-tertiary);
  font-weight: 500;
}

.card-tmdb {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: 'SF Mono', 'Fira Code', monospace;
}

/* Badges */
.card-badges {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.badge-success {
  background: var(--accent-green-soft);
  color: var(--accent-green);
}

.badge-fail {
  background: var(--accent-red-soft);
  color: var(--accent-red);
}

.badge-wash {
  background: rgba(245, 158, 11, 0.15);
  color: var(--accent-yellow);
}

.badge-sub {
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
}

.badge-unknown {
  background: rgba(100, 116, 139, 0.15);
  color: var(--text-tertiary);
}

/* Meta */
.card-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-tertiary);
}

/* Params chips */
.card-params {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.param-chip {
  display: inline-block;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}

.param-scheme {
  background: rgba(245, 158, 11, 0.12);
  color: var(--accent-yellow);
}

.param-filter {
  background: rgba(100, 116, 139, 0.12);
  color: var(--text-secondary);
}

.param-downloader {
  background: rgba(16, 185, 129, 0.12);
  color: var(--accent-green);
}

.param-quality {
  background: rgba(239, 68, 68, 0.12);
  color: var(--accent-red);
}

.param-sites {
  background: rgba(139, 92, 246, 0.12);
  color: var(--accent-purple);
}

/* Message */
.card-msg {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
  word-break: break-all;
}
.card-msg.msg-error {
  color: var(--accent-red);
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

/* ==================== Mobile ==================== */
@media screen and (max-width: 768px) {
  .history-container {
    padding: 4px;
  }

  .history-toolbar {
    padding: 4px;
    margin-bottom: 8px;
  }

  .card-inner {
    padding: 12px;
    gap: 6px;
  }

  .card-name {
    font-size: 13px;
  }

  .card-badges {
    gap: 4px;
  }

  .badge {
    font-size: 10px;
    padding: 2px 8px;
  }
}
</style>
