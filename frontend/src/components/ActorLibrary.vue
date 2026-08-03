<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { Search, Refresh, PictureFilled, MagicStick, User, Brush } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

// 演员信息（后端 /api/actors 返回项）
interface Actor {
  name: string
  local_image_path?: string
  image_url?: string
  tmdb_id?: number | string
  douban_celebrity_id?: number | string
  overview?: string
  birth_date?: string
  birth_place?: string
  update_time?: string
  source?: string
}

// ==================== 状态 ====================
const actors = ref<Actor[]>([])
const total = ref(0)
const loading = ref(false)
const searchQuery = ref('')
const filterStatus = ref('')
const currentPage = ref(1)
const pageSize = ref(24)
const refreshingActor = ref<string | null>(null) // 正在刷新的演员名

// ==================== 批量修复状态 ====================
const repairTaskId = ref('')
const repairing = ref(false)
const repairDialogVisible = ref(false)
const repairTaskPercent = ref(0)
const repairTaskMessage = ref('')
const repairTaskDone = ref(false)
let _repairTimer: ReturnType<typeof setInterval> | null = null

// ==================== 无效头像清洗状态 ====================
const cleanupLoading = ref(false) // 校验/清理无效头像 进行中

// ==================== 图片加载失败集合 ====================
// 加载失败的演员名 → 从「头像」降级为「科技缺省图」
const brokenImages = reactive(new Set<string>())

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
    params.set('page', String(currentPage.value))
    params.set('page_size', String(pageSize.value))
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
    brokenImages.clear() // 新数据到达 → 允许之前裂图重试加载
  } catch (e) {
    ElMessage.error('获取演员列表失败: ' + (e instanceof Error ? e.message : String(e)))
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
const refreshActor = async (actorName: string) => {
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
    ElMessage.error(`刷新失败: ${e instanceof Error ? e.message : String(e)}`)
  } finally {
    refreshingActor.value = null
  }
}

// ==================== 一键批量修复 ====================
const stopRepairPolling = () => {
  if (_repairTimer) { clearInterval(_repairTimer); _repairTimer = null }
}

const startRepairPolling = (taskId: string) => {
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
    ElMessage.error(`启动批量修复失败: ${e instanceof Error ? e.message : String(e)}`)
  } finally {
    repairing.value = false
  }
}

// ==================== 一键清洗无效头像 ====================
const handleCleanupImages = async () => {
  if (cleanupLoading.value) return
  // 深色二次确认弹窗
  try {
    await ElMessageBox.confirm(
      '将扫描全部演员的本地头像文件，<br/>对「数据库有记录但物理文件已丢失 / 空文件」的脏数据进行清除。<br/><span style="color:#fbbf24">此操作不可撤销，清除后相关演员将进入「待更新」状态。</span>',
      '⚠️ 校验 / 清理无效头像',
      {
        confirmButtonText: '确认清洗',
        cancelButtonText: '取消',
        type: 'warning',
        dangerouslyUseHTMLString: true,
        customClass: 'cleanup-confirm',
        confirmButtonClass: 'el-button--danger',
      }
    )
  } catch {
    return // 用户取消
  }

  cleanupLoading.value = true
  try {
    const res = await fetch('/api/actors/cleanup_images', { method: 'POST' })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    const data = await res.json()
    if (data.cleaned_count > 0) {
      ElMessage.warning(
        `已清洗 ${data.cleaned_count} 条无效头像` +
        (data.empty_file_count ? `（含 ${data.empty_file_count} 个空文件）` : '') +
        `，共检查 ${data.total_checked} 位演员`
      )
    } else {
      ElMessage.success(`共检查 ${data.total_checked} 位演员，头像全部有效 ✅`)
    }
    await fetchActors() // 刷新列表 → brokenImages 清空 → 裂图重试 / 标签复位
  } catch (e) {
    ElMessage.error(`清洗失败: ${e instanceof Error ? e.message : String(e)}`)
  } finally {
    cleanupLoading.value = false
  }
}

// ==================== 图片 URL 拼接 + 高科技缺省图 ====================
const getAvatarUrl = (actor: Actor) => {
  // L1: 本地图片 — 代理转发到 FastAPI /people 静态目录
  if (actor.local_image_path) {
    return `/people/${actor.local_image_path}`
  }
  // L2: 外部直链 (豆瓣/TMDB/Emby)
  if (actor.image_url) {
    return actor.image_url
  }
  // L3: 无任何图片源 → 渲染科技缺省图
  return ''
}

// 是否有可用头像：有图片来源 且 未加载失败
const hasAvatar = (actor: Actor) => {
  if (brokenImages.has(actor.name)) return false
  return !!(actor.local_image_path || actor.image_url)
}

// 图片加载失败（404/跨域/超时）→ 降级为科技缺省图
const handleImageError = (name: string) => {
  brokenImages.add(name)
}

// 本地是否有已下载的头像文件
const hasLocalImage = (actor: Actor) => {
  return !!(actor.local_image_path && actor.local_image_path.trim() !== '')
}

// 是否有可用的图片来源（本地路径 或 外部直链）
const hasImageSource = (actor: Actor) => {
  return !!(actor.local_image_path || actor.image_url)
}

// 状态药丸三态: has(本地已存·绿) / broken(缺头像·黄) / no(需更新·红)
// 只有当 图片来源有效 && 图片加载成功 && 本地文件真实存在 时才允许显示「本地已存」
const pillState = (actor: Actor): 'has' | 'broken' | 'no' => {
  // 无任何图片来源 → 需更新
  if (!hasImageSource(actor)) return 'no'
  // 图片加载失败 (404/跨域/超时) → 立刻剥夺「本地已存」，降级为「缺头像」
  if (brokenImages.has(actor.name)) return 'broken'
  // 唯一允许显示「本地已存」的场景
  return hasLocalImage(actor) ? 'has' : 'no'
}

const pillLabel = (actor: Actor) => {
  const s = pillState(actor)
  if (s === 'has') return '本地已存'
  if (s === 'broken') return '缺头像'
  return '需更新'
}

// ==================== 底部信息排版 ====================
// 左侧 ID 主标识：TMDB → 豆瓣；无 ID 时留空（由 v-if 隐藏）
const idLabel = (actor: Actor) => {
  if (actor.tmdb_id) return `TMDB ${actor.tmdb_id}`
  if (actor.douban_celebrity_id) return `豆瓣 ${actor.douban_celebrity_id}`
  return ''
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
        <!-- 校验/清理无效头像 — 危险警示色幽灵按钮 -->
        <button
          class="cleanup-btn"
          :disabled="cleanupLoading"
          @click="handleCleanupImages"
        >
          <el-icon :class="{ 'is-spinning': cleanupLoading }">
            <Brush />
          </el-icon>
          {{ cleanupLoading ? '清洗中...' : '校验/清理无效头像' }}
        </button>

        <button
          class="repair-btn"
          :disabled="repairing"
          @click="handleRepairMissing"
        >
          <el-icon :class="{ 'is-spinning': repairing }">
            <MagicStick />
          </el-icon>
          {{ repairing ? '修复中...' : '一键修复空数据' }}
        </button>
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
            <!-- 头像区：完美 2:3 肖像比例 -->
            <div class="avatar-area">
              <!-- 刷新按钮（右上角悬浮） -->
              <el-tooltip content="强制刷新演员信息" placement="top">
                <button
                  class="refresh-btn"
                  :disabled="refreshingActor === actor.name"
                  @click.stop="refreshActor(actor.name)"
                >
                  <el-icon :class="{ 'is-spinning': refreshingActor === actor.name }">
                    <Refresh />
                  </el-icon>
                </button>
              </el-tooltip>

              <!-- 状态药丸（左上角发光）：三态联动
                   has    → 图片来源有效 + 加载成功 + 本地文件真实存在 → 「本地已存」(绿)
                   broken → 图片加载失败 (404/跨域/超时) → 「缺头像」(黄·警示)
                   no     → 无任何图片来源 → 「需更新」(红) -->
              <span
                class="status-pill"
                :class="`status-${pillState(actor)}`"
              >
                <span class="status-dot"></span>
                {{ pillLabel(actor) }}
              </span>

              <!-- 有图：真实头像 -->
              <img
                v-if="hasAvatar(actor)"
                :src="getAvatarUrl(actor)"
                :alt="actor.name"
                class="avatar-img"
                loading="lazy"
                referrerpolicy="no-referrer"
                @error="handleImageError(actor.name)"
              />

              <!-- 无图 / 加载失败：科技缺省图 -->
              <div v-else class="avatar-fallback">
                <el-icon :size="48" class="fallback-icon">
                  <User />
                </el-icon>
                <span class="fallback-text">NO IMAGE DATA</span>
              </div>

              <!-- 底部融合遮罩：图片平滑过渡到文字区 -->
              <div class="avatar-mask"></div>
            </div>

            <!-- 信息区：微排版四层结构，所有卡片高度绝对一致 -->
            <div class="info-section">
              <!-- 第一层：演员名 -->
              <div class="actor-name" :title="actor.name">{{ actor.name }}</div>

              <!-- 第二层：元数据 Meta（生日 & 出生地，v-if 条件拼接） -->
              <p
                v-if="actor.birth_date || actor.birth_place"
                class="actor-meta"
                :title="[actor.birth_date, actor.birth_place].filter(Boolean).join(' · ')"
              >
                <template v-if="actor.birth_date">{{ actor.birth_date }}</template>
                <template v-if="actor.birth_date && actor.birth_place"> · </template>
                <template v-if="actor.birth_place">{{ actor.birth_place }}</template>
              </p>

              <!-- 第三层：人物简介（无数据直接留空，绝不显示刺眼占位文案） -->
              <p
                v-if="actor.overview"
                class="actor-bio"
                :title="actor.overview"
              >
                {{ actor.overview }}
              </p>

              <!-- 尾部沉底：ID + 来源（mt-auto 永远贴底） -->
              <div class="meta-row">
                <span v-if="idLabel(actor)" class="meta-id" :title="idLabel(actor)">{{ idLabel(actor) }}</span>
                <span v-if="actor.source" class="source-tag">{{ actor.source }}</span>
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

<style scoped lang="postcss">
/* ==================== 容器 ==================== */
.actor-library {
  padding: 20px;
  min-height: 100%;
  background-color: var(--bg-primary);
}

/* ==================== 顶部操作栏 — 毛玻璃 ==================== */
.top-bar {
  @apply flex items-center justify-between flex-wrap gap-3 mb-6 p-4
    rounded-2xl bg-[#0F172A]/40 border border-white/5 backdrop-blur-xl;
}

.bar-left {
  @apply flex items-center gap-3 flex-wrap flex-1;
}

.bar-right {
  @apply flex items-center gap-3 flex-wrap;
}

/* 搜索框 — 电光蓝发光 Input */
.search-input {
  width: 260px;
}
.search-input :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--border-color) !important;
  transition: box-shadow 0.2s ease;
}
.search-input :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px var(--text-tertiary) !important;
}
.search-input :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px var(--accent-blue), 0 0 12px rgba(59, 130, 246, 0.35) !important;
}

.filter-select {
  width: 132px;
}

/* 「校验/清理无效头像」— 危险警示感高科技幽灵按钮 */
.cleanup-btn {
  @apply inline-flex items-center gap-2 px-4 py-2 rounded-lg
    bg-yellow-500/10 text-yellow-500 border border-yellow-500/30
    hover:bg-yellow-500/20 hover:shadow-[0_0_15px_rgba(234,179,8,0.2)]
    transition-all font-medium text-sm cursor-pointer;
}
.cleanup-btn:disabled {
  @apply opacity-60 cursor-not-allowed;
}

/* 「一键修复空数据」— 高级警示色幽灵按钮 */
.repair-btn {
  @apply inline-flex items-center gap-2 px-4 py-2 rounded-lg
    bg-yellow-500/10 text-yellow-500 border border-yellow-500/30
    hover:bg-yellow-500/20 hover:shadow-[0_0_10px_rgba(234,179,8,0.2)]
    transition-all cursor-pointer;
}
.repair-btn:disabled {
  @apply opacity-60 cursor-not-allowed;
}

.total-hint {
  @apply text-slate-500 text-[13px] whitespace-nowrap;
}
.total-hint strong {
  @apply text-blue-400 font-bold;
}

/* ==================== 卡片网格 — 呼吸感响应式 ==================== */
.card-grid-wrapper {
  min-height: 400px;
}

.card-grid {
  @apply grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 2xl:grid-cols-6 gap-6;
}

/* ==================== 单张卡片 — 毛玻璃全息 ==================== */
.actor-card {
  @apply flex flex-col bg-[#0F172A]/60 border border-white/5 backdrop-blur-xl rounded-2xl
    overflow-hidden shadow-lg hover:shadow-blue-900/20 hover:-translate-y-1
    hover:border-blue-500/30 transition-all duration-300 cursor-pointer;
}

/* ---- 刷新按钮（右上角悬浮毛玻璃） ---- */
.refresh-btn {
  @apply absolute top-2 right-2 z-10 inline-flex items-center justify-center
    w-8 h-8 rounded-full bg-slate-900/60 border border-white/10 text-slate-300
    backdrop-blur-md opacity-0 -translate-y-1 transition-all duration-200 cursor-pointer;
}
.actor-card:hover .refresh-btn {
  @apply opacity-100 translate-y-0;
}
.refresh-btn:hover {
  @apply bg-blue-500/20 border-blue-500/40 text-blue-300;
}
.refresh-btn:disabled {
  @apply opacity-100 translate-y-0 cursor-wait;
}

/* ---- 状态药丸（左上角发光） ---- */
.status-pill {
  @apply absolute top-2 left-2 z-10 inline-flex items-center gap-1.5
    px-2 py-0.5 rounded-full text-[10px] font-medium backdrop-blur-md;
}
.status-has {
  @apply bg-emerald-500/10 text-emerald-400 border border-emerald-500/20;
  box-shadow: 0 0 12px rgba(16, 185, 129, 0.12);
}
.status-no {
  @apply bg-red-500/10 text-red-400 border border-red-500/20;
  box-shadow: 0 0 12px rgba(239, 68, 68, 0.14);
}
/* 缺头像 — 图片加载失败 (警告黄) */
.status-broken {
  @apply bg-amber-500/10 text-amber-400 border border-amber-500/25;
  box-shadow: 0 0 12px rgba(245, 158, 11, 0.16);
}
.status-dot {
  @apply w-1 h-1 rounded-full;
}
.status-has .status-dot {
  @apply bg-emerald-400;
  box-shadow: 0 0 6px rgba(52, 211, 153, 0.8);
}
.status-no .status-dot {
  @apply bg-red-400;
  box-shadow: 0 0 6px rgba(248, 113, 113, 0.8);
}
.status-broken .status-dot {
  @apply bg-amber-400;
  box-shadow: 0 0 6px rgba(251, 191, 36, 0.8);
}

/* ---- 头像区：完美 2:3 肖像比例 ---- */
.avatar-area {
  @apply relative w-full overflow-hidden
    bg-gradient-to-b from-[#1E293B] to-[#0B1120];
}

.avatar-img {
  @apply w-full aspect-[2/3] object-cover object-center;
  display: block;
  transition: transform 0.45s cubic-bezier(0.4, 0, 0.2, 1);
}
.actor-card:hover .avatar-img {
  transform: scale(1.05);
}

/* 高科技缺省图 — 微弱呼吸 User 图标 + 极客字体 */
.avatar-fallback {
  @apply w-full aspect-[2/3] flex flex-col items-center justify-center
    bg-gradient-to-b from-[#1E293B] to-[#0B1120];
}
.fallback-icon {
  @apply text-slate-600 animate-pulse;
}
.fallback-text {
  @apply text-slate-600 font-hud text-[9px] tracking-[0.3em] mt-3 select-none;
}

/* 底部融合遮罩：图片平滑过渡到下方文字区 */
.avatar-mask {
  @apply absolute bottom-0 inset-x-0 h-1/3
    bg-gradient-to-t from-[#0F172A]/60 to-transparent pointer-events-none;
}

/* ---- 信息区 — Flex 弹性重组，绝对等高 ---- */
.info-section {
  @apply flex flex-col p-3 flex-1 overflow-hidden;
}

/* 第一层：演员名 */
.actor-name {
  @apply text-white text-[15px] font-bold tracking-wide truncate;
}

/* 第二层：元数据 Meta（生日 & 出生地）— 极致收敛 */
.actor-meta {
  @apply text-slate-500 text-[10px] font-mono truncate mt-0.5;
}

/* 第三层：人物简介 — 6 行扩容 + 呼吸感（行高拉距/字号压低/对比度减弱，消解文本压迫） */
.actor-bio {
  @apply text-slate-400/80 text-[11px] leading-relaxed mt-2.5 mb-1 line-clamp-[6];
}

/* 尾部沉底：mt-auto 自动推开上方空间 → 即使无简介也死死钉在卡片最底部 */
.meta-row {
  @apply mt-auto pt-2.5 pb-0.5 flex items-center justify-between gap-2 border-t border-white/5;
}

/* 底部左侧：ID */
.meta-id {
  @apply text-slate-600 text-[9px] font-mono truncate;
}

/* 底部右侧：来源 — 微型发光药丸 */
.source-tag {
  @apply text-blue-400 text-[8px] font-mono uppercase px-1.5 py-0.5 rounded
    border border-blue-500/20 bg-blue-500/10 shrink-0;
}

/* ==================== 空状态 ==================== */
.empty-state {
  @apply flex flex-col items-center justify-center gap-3 py-20 text-slate-500;
}
.empty-state p {
  @apply text-[15px] text-center leading-6;
}
.empty-state strong {
  @apply text-slate-200;
}

/* ==================== 批量修复进度对话框 ==================== */
.repair-dialog-body {
  @apply py-2;
}
.repair-message {
  @apply text-slate-300 text-sm mb-4 leading-relaxed min-h-[42px];
}

/* ==================== 清洗确认弹窗 — 深色科技风 ====================
   ElMessageBox teleport 到 body，scoped 样式需 :global 穿透 */
:global(.cleanup-confirm) {
  @apply rounded-xl border border-white/10;
  background: rgba(15, 23, 42, 0.97) !important;
  box-shadow: 0 0 30px rgba(234, 179, 8, 0.08);
}
:global(.cleanup-confirm .el-message-box__title) {
  @apply text-amber-400 font-bold;
}
:global(.cleanup-confirm .el-message-box__content) {
  @apply text-slate-300 text-sm leading-relaxed;
}
:global(.cleanup-confirm .el-message-box__btns .el-button--cancel) {
  @apply text-slate-400 bg-white/5 border border-white/10 hover:bg-white/10;
}

/* ==================== 分页栏 ==================== */
.pagination-bar {
  @apply flex justify-center mt-7 pt-4;
}

/* ---- 分页器黑化 — 融入暗黑科技风 ---- */
.pagination-bar :deep(.el-pagination button),
.pagination-bar :deep(.el-pagination .el-pager li) {
  @apply bg-transparent text-slate-400 rounded-md transition-colors;
}
.pagination-bar :deep(.el-pagination .el-pager li.is-active) {
  @apply bg-blue-600 text-white shadow-[0_0_8px_rgba(59,130,246,0.5)];
}
.pagination-bar :deep(.el-pagination .el-pager li:hover:not(.is-active)),
.pagination-bar :deep(.el-pagination button:hover:not(:disabled)) {
  @apply bg-blue-500/10 text-blue-400;
}
.pagination-bar :deep(.el-pagination button:disabled) {
  @apply bg-transparent text-slate-600;
}

/* ==================== 图标旋转 ==================== */
.is-spinning {
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ==================== 响应式 ==================== */
@media screen and (max-width: 768px) {
  .actor-library {
    padding: 12px;
  }

  .top-bar {
    @apply p-3 flex-col items-stretch;
  }

  .bar-left {
    @apply flex-col w-full;
  }

  .search-input {
    width: 100%;
  }

  .filter-select {
    width: 100%;
  }

  .bar-right {
    @apply justify-center;
  }

  .actor-name {
    @apply text-sm;
  }
}
</style>
