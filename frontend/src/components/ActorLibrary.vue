<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { Search, Refresh, UserFilled, PictureFilled, MagicStick } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

// ==================== 状态 ====================
const actors = ref([])
const total = ref(0)
const loading = ref(false)
const searchQuery = ref('')
const filterStatus = ref('')
const currentPage = ref(1)
const pageSize = ref(24)
const refreshingActor = ref(null) // 正在刷新的演员名

// ==================== 批量修复状态 ====================
const repairTaskId = ref('')
const repairing = ref(false)
const repairDialogVisible = ref(false)
const repairTaskPercent = ref(0)
const repairTaskMessage = ref('')
const repairTaskDone = ref(false)
let _repairTimer = null

// ==================== 筛选选项 ====================
const filterOptions = [
  { label: '全部', value: '' },
  { label: '本地有图', value: 'true' },
  { label: '本地无图', value: 'false' },
]

// ==================== 计算属性 ====================
const hasActiveSearch = computed(() => searchQuery.value.trim() !== '')

// ==================== 数据获取 ====================
const fetchActors = async () => {
  loading.value = true
  try {
    const params = new URLSearchParams()
    params.set('page', currentPage.value)
    params.set('page_size', pageSize.value)
    if (searchQuery.value.trim()) {
      params.set('search', searchQuery.value.trim())
    }
    if (filterStatus.value) {
      params.set('has_local_image', filterStatus.value)
    }

    const res = await fetch(`/api/actors?${params.toString()}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    actors.value = data.items || []
    total.value = data.total || 0
  } catch (e) {
    ElMessage.error('获取演员列表失败: ' + e.message)
    actors.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

// ==================== 搜索/筛选变化时重置到第一页 ====================
watch([searchQuery, filterStatus], () => {
  currentPage.value = 1
  fetchActors()
})

watch(currentPage, () => {
  fetchActors()
})

// ==================== 刷新单个演员 ====================
const refreshActor = async (actorName) => {
  if (refreshingActor.value) return // 防止重复点击
  refreshingActor.value = actorName
  try {
    const res = await fetch(`/api/actors/${encodeURIComponent(actorName)}/refresh`, {
      method: 'POST',
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    const data = await res.json()
    ElMessage.success(data.message || `'${actorName}' 刷新成功`)
    // 刷新当前列表
    await fetchActors()
  } catch (e) {
    ElMessage.error(`刷新失败: ${e.message}`)
  } finally {
    refreshingActor.value = null
  }
}

// ==================== 一键批量修复 ====================
const stopRepairPolling = () => {
  if (_repairTimer) { clearInterval(_repairTimer); _repairTimer = null }
}

const startRepairPolling = (taskId) => {
  stopRepairPolling()
  repairTaskDone.value = false
  repairTaskPercent.value = 0
  _repairTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/tasks/${taskId}`)
      if (!res.ok) return
      const s = await res.json()
      repairTaskMessage.value = s.message || ''

      if (s.status === 'completed') {
        stopRepairPolling()
        repairTaskDone.value = true
        repairTaskPercent.value = 100
        ElMessage.success(s.message || '批量修复完成')
        await fetchActors()
        setTimeout(() => {
          if (repairDialogVisible.value) repairDialogVisible.value = false
        }, 2000)
      } else if (s.status === 'error' || s.status === 'failed') {
        stopRepairPolling()
        repairTaskDone.value = true
        repairDialogVisible.value = false
        ElMessage.error(`批量修复异常终止: ${s.message || '未知错误'}`)
        await fetchActors()
      } else {
        repairTaskPercent.value = s.total > 0
          ? Math.min(99, Math.floor((s.current / s.total) * 100))
          : 0
      }
    } catch (e) {
      stopRepairPolling()
      repairTaskDone.value = true
      repairTaskMessage.value = '无法获取任务进度（网络异常），请手动关闭窗口'
    }
  }, 1000)
}

const closeRepairDialog = () => {
  stopRepairPolling()
  repairDialogVisible.value = false
}

const handleRepairMissing = async () => {
  if (repairing.value) return
  repairing.value = true
  try {
    const res = await fetch('/api/actors/repair_missing', { method: 'POST' })
    const data = await res.json()
    if (!data.task_id) {
      // 没有需要修复的
      ElMessage.info(data.message || '无需修复')
      return
    }
    repairTaskId.value = data.task_id
    repairDialogVisible.value = true
    startRepairPolling(data.task_id)
  } catch (e) {
    ElMessage.error(`启动批量修复失败: ${e.message}`)
  } finally {
    repairing.value = false
  }
}

// ==================== 图片 URL 拼接 + 防裂兜底 ====================
// 兜底占位图（Element Plus 内置灰底占位）
const FALLBACK_AVATAR = 'data:image/svg+xml,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="280" height="280" fill="%231e293b">' +
  '<rect width="280" height="280" fill="%23111827"/>' +
  '<circle cx="140" cy="110" r="40" fill="%23334155"/>' +
  '<ellipse cx="140" cy="190" rx="65" ry="40" fill="%23334155"/>' +
  '<text x="140" y="260" text-anchor="middle" fill="%2364748b" font-size="13" font-family="sans-serif">暂无头像</text>' +
  '</svg>'
)

const getAvatarUrl = (actor) => {
  // L1: 本地图片 — 代理转发到 FastAPI /people 静态目录
  if (actor.local_image_path) {
    return `/people/${actor.local_image_path}`
  }
  // L2: 外部直链 (豆瓣/TMDB/Emby)
  if (actor.image_url) {
    return actor.image_url
  }
  // L3: 内联 SVG 兜底（无网络依赖，永不裂图）
  return FALLBACK_AVATAR
}

const handleImageError = (e) => {
  // 图片加载失败（404/跨域/超时）→ 静默降级为内联占位图
  if (e.target.src !== FALLBACK_AVATAR) {
    e.target.src = FALLBACK_AVATAR
  }
}

const hasLocalImage = (actor) => {
  return !!(actor.local_image_path && actor.local_image_path.trim() !== '')
}

// ==================== 格式化工具 ====================
const formatDate = (isoString) => {
  if (!isoString) return ''
  const d = new Date(isoString)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

const trimOverview = (text, maxLen = 120) => {
  if (!text) return ''
  return text.length > maxLen ? text.slice(0, maxLen) + '…' : text
}

// ==================== 生命周期 ====================
onMounted(() => {
  fetchActors()
})

onUnmounted(() => {
  stopRepairPolling()
})
</script>

<template>
  <div class="actor-library">
    <!-- ========== 顶部操作栏 ========== -->
    <div class="top-bar">
      <div class="bar-left">
        <el-input
          v-model="searchQuery"
          placeholder="搜索演员名..."
          clearable
          class="search-input"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-select
          v-model="filterStatus"
          placeholder="筛选状态"
          class="filter-select"
        >
          <el-option
            v-for="opt in filterOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </div>

      <div class="bar-right">
        <el-button
          type="warning"
          :icon="MagicStick"
          :loading="repairing"
          @click="handleRepairMissing"
        >
          一键修复空数据
        </el-button>
        <span class="total-hint">
          共 <strong>{{ total }}</strong> 位演员
        </span>
      </div>
    </div>

    <!-- ========== 卡片网格 ========== -->
    <div v-loading="loading" class="card-grid-wrapper">
      <template v-if="actors.length > 0">
        <div class="card-grid">
          <div
            v-for="actor in actors"
            :key="actor.name"
            class="actor-card"
          >
            <!-- 刷新按钮（右上角悬浮） -->
            <el-tooltip content="强制刷新演员信息" placement="top">
              <el-button
                class="refresh-btn"
                circle
                size="small"
                :icon="Refresh"
                :loading="refreshingActor === actor.name"
                @click.stop="refreshActor(actor.name)"
              />
            </el-tooltip>

            <!-- 头像容器 -->
            <div class="avatar-container">
              <img
                :src="getAvatarUrl(actor)"
                :alt="actor.name"
                class="avatar-img"
                loading="lazy"
                referrerpolicy="no-referrer"
                @error="handleImageError"
              />

              <!-- 状态徽章（左上角磨砂玻璃） -->
              <el-tag
                class="status-badge"
                :class="hasLocalImage(actor) ? 'status-has' : 'status-no'"
                size="small"
                effect="dark"
              >
                {{ hasLocalImage(actor) ? '本地已存' : '需更新' }}
              </el-tag>
            </div>

            <!-- 信息区域 -->
            <div class="info-section">
              <!-- 姓名 -->
              <div class="actor-name" :title="actor.name">{{ actor.name }}</div>

              <!-- ID 徽章行 -->
              <div class="id-badges">
                <el-tag
                  v-if="actor.tmdb_id"
                  size="small"
                  type="info"
                  class="id-tag"
                >
                  TMDB {{ actor.tmdb_id }}
                </el-tag>
                <el-tag
                  v-if="actor.douban_celebrity_id"
                  size="small"
                  class="id-tag douban-tag"
                >
                  豆瓣 {{ actor.douban_celebrity_id }}
                </el-tag>
                <span
                  v-if="!actor.tmdb_id && !actor.douban_celebrity_id"
                  class="no-id-hint"
                >
                  暂无 ID
                </span>
              </div>

              <!-- 简介 -->
              <div class="overview-text" v-if="actor.overview">
                {{ trimOverview(actor.overview) }}
              </div>

              <!-- 底部 Meta -->
              <div class="meta-row">
                <span v-if="actor.birth_date" class="meta-item">
                  🎂 {{ actor.birth_date }}
                </span>
                <span v-if="actor.birth_place" class="meta-item">
                  📍 {{ actor.birth_place }}
                </span>
              </div>
              <div class="meta-row meta-update" v-if="actor.update_time">
                <span class="update-time">
                  更新于 {{ formatDate(actor.update_time) }}
                </span>
                <span v-if="actor.source" class="source-badge">
                  {{ actor.source }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- 空状态 -->
      <div v-else-if="!loading" class="empty-state">
        <el-icon :size="56"><PictureFilled /></el-icon>
        <p v-if="hasActiveSearch">
          没有找到匹配 "<strong>{{ searchQuery }}</strong>" 的演员
        </p>
        <p v-else>暂无演员数据，请先在汉化管理中同步数据</p>
      </div>
    </div>

    <!-- ========== 底部分页 ========== -->
    <div class="pagination-bar" v-if="total > pageSize">
      <el-pagination
        v-model:current-page="currentPage"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next, total"
        background
      />
    </div>

    <!-- ========== 批量修复进度对话框 ========== -->
    <el-dialog
      v-model="repairDialogVisible"
      title="一键批量修复元数据"
      width="500px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="true"
      @close="closeRepairDialog"
    >
      <div class="repair-dialog-body">
        <div class="repair-message">{{ repairTaskMessage }}</div>
        <el-progress
          :percentage="repairTaskPercent"
          :text-inside="true"
          :stroke-width="20"
          :status="repairTaskDone ? (repairTaskPercent === 100 ? 'success' : 'exception') : ''"
          color="#e6a23c"
        />
      </div>
      <template #footer>
        <el-button
          type="primary"
          @click="closeRepairDialog"
        >
          {{ repairTaskDone ? '关闭' : '后台运行 / 关闭' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ==================== 容器 ==================== */
.actor-library {
  padding: 20px;
  min-height: 100%;
  background-color: var(--bg-primary);
}

/* ==================== 顶部操作栏 ==================== */
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 24px;
  padding: 16px 20px;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.bar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  flex: 1;
}

.bar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.search-input {
  width: 280px;
}

.filter-select {
  width: 140px;
}

.total-hint {
  color: var(--text-tertiary);
  font-size: 13px;
  white-space: nowrap;
}

.total-hint strong {
  color: var(--accent-blue);
  font-weight: 700;
}

/* ==================== 卡片网格 ==================== */
.card-grid-wrapper {
  min-height: 400px;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(248px, 1fr));
  gap: 20px;
}

/* ==================== 单张卡片 ==================== */
.actor-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
  position: relative;
}

.actor-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
  border-color: var(--text-tertiary);
}

/* ---- 刷新按钮（右上角悬浮） ---- */
.refresh-btn {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 10;
  /* 磨砂玻璃底 */
  background: rgba(15, 23, 42, 0.65) !important;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.12) !important;
  color: #cbd5e1 !important;
  width: 32px;
  height: 32px;
  opacity: 0;
  transform: translateY(-4px);
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.actor-card:hover .refresh-btn {
  opacity: 1;
  transform: translateY(0);
}

.refresh-btn:hover {
  background: var(--accent-blue) !important;
  border-color: var(--accent-blue) !important;
  color: #fff !important;
}

/* ---- 头像区 ---- */
.avatar-container {
  height: 280px;
  width: 100%;
  position: relative;
  background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
  overflow: hidden;
}

.avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.3s ease;
}

.actor-card:hover .avatar-img {
  transform: scale(1.04);
}

/* 缺省占位 */
.avatar-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-tertiary);
  font-size: 13px;
}

/* ---- 状态徽章（左上角磨砂玻璃） ---- */
.status-badge {
  position: absolute;
  top: 10px;
  left: 10px;
  font-size: 11px;
  font-weight: 600;
  padding: 4px 10px;
  height: auto;
  line-height: 1.4;
  letter-spacing: 0.3px;
}

.status-has {
  background: rgba(16, 185, 129, 0.25) !important;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid rgba(16, 185, 129, 0.4) !important;
  color: #6ee7b7 !important;
}

.status-no {
  background: rgba(239, 68, 68, 0.25) !important;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid rgba(239, 68, 68, 0.4) !important;
  color: #fca5a5 !important;
}

/* ---- 信息区 ---- */
.info-section {
  padding: 14px 16px 16px;
}

.actor-name {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 8px;
  line-height: 1.3;
  /* 单行溢出省略 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.id-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
  min-height: 22px;
  align-items: center;
}

.id-tag {
  font-size: 11px;
  padding: 0 8px;
  height: 20px;
  line-height: 20px;
}

.douban-tag {
  background: rgba(7, 193, 96, 0.12) !important;
  border-color: rgba(7, 193, 96, 0.3) !important;
  color: #5ecc82 !important;
}

.no-id-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  font-style: italic;
}

/* 简介 — 两行截断 */
.overview-text {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-tertiary);
  margin-bottom: 12px;
  min-height: 39px;
}

/* 底部 Meta */
.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  color: var(--text-tertiary);
}

.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.meta-update {
  margin-top: 6px;
  justify-content: space-between;
}

.update-time {
  color: var(--text-tertiary);
  font-size: 11px;
}

.source-badge {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
  font-weight: 600;
}

/* ==================== 空状态 ==================== */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 20px;
  color: var(--text-tertiary);
  gap: 12px;
}

.empty-state p {
  font-size: 15px;
  text-align: center;
  line-height: 1.6;
}

.empty-state strong {
  color: var(--text-primary);
}

/* ==================== 批量修复进度对话框 ==================== */
.repair-dialog-body {
  padding: 8px 0;
}

.repair-message {
  color: var(--text-secondary);
  font-size: 14px;
  margin-bottom: 16px;
  line-height: 1.5;
  min-height: 42px;
}

/* ==================== 分页栏 ==================== */
.pagination-bar {
  display: flex;
  justify-content: center;
  margin-top: 28px;
  padding: 16px 0;
}

/* ==================== 响应式 ==================== */
@media screen and (max-width: 768px) {
  .actor-library {
    padding: 12px;
  }

  .top-bar {
    padding: 12px;
    flex-direction: column;
    align-items: stretch;
  }

  .bar-left {
    flex-direction: column;
    width: 100%;
  }

  .search-input {
    width: 100%;
  }

  .filter-select {
    width: 100%;
  }

  .bar-right {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    flex-wrap: wrap;
  }

  .card-grid {
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 12px;
  }

  .avatar-container {
    height: 220px;
  }

  .actor-name {
    font-size: 14px;
  }
}
</style>
