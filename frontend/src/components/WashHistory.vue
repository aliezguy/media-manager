<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { History, RefreshCw, Trash2, Clock, ChevronLeft, ChevronRight } from 'lucide-vue-next'

// 站点选项（/api/resources 的 sites 返回项）
interface SiteOption {
  id: string | number
  name: string
}

// 洗版执行参数（wash_params，字段均可选）
interface WashParams {
  scheme?: string
  filter_groups?: string[]
  downloader?: string
  quality?: string
  sites?: Array<string | number>
}

// 订阅任务历史记录（/api/history 返回项）
interface WashHistoryItem {
  id: number
  name: string
  season?: number
  tmdb_id?: number
  status: string
  wash_type: string
  created_at: string
  wash_params?: WashParams
  message?: string
}

const API_URL = ''
const historyData = ref<WashHistoryItem[]>([])
const loading = ref(false)
const siteOptions = ref<SiteOption[]>([])

// ==================== 客户端分页（纯 UI，不改变后端数据结构） ====================
const pageSize = 20
const page = ref(1)
const total = computed(() => historyData.value.length)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const pagedData = computed(() => {
  const start = (page.value - 1) * pageSize
  return historyData.value.slice(start, start + pageSize)
})
// 页码集合（含省略号，最多 7 个）
const pageNumbers = computed(() => {
  const n = totalPages.value
  if (n <= 7) return Array.from({ length: n }, (_, i) => i + 1)
  const set = new Set([1, n, page.value])
  for (let i = page.value - 1; i <= page.value + 1; i++) {
    if (i > 1 && i < n) set.add(i)
  }
  return [...set].sort((a, b) => a - b)
})
// 数据减少时钳制页码
watch(totalPages, (n) => {
  if (page.value > n) page.value = n
})

const fetchHistory = async () => {
  loading.value = true
  try {
    const res = await axios.get(`${API_URL}/api/history`)
    historyData.value = res.data
    page.value = 1
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
    page.value = 1
    ElMessage.success('已清空')
  } catch {}
}

const formatDate = (dateStr: string) => {
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

const formatSiteNames = (siteIds: Array<string | number>) => {
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
  <!-- ==================== 固定高度容器：内部列表滚动，分页器固定在底部 ==================== -->
  <div class="records-root mx-auto flex h-[calc(100vh-120px)] w-full max-w-[1100px] flex-col overflow-hidden px-5 pt-3.5">
    <!-- ==================== 头部 ==================== -->
    <header class="flex flex-shrink-0 flex-wrap items-center justify-between gap-3.5 pb-3.5">
      <div class="flex min-w-0 items-center gap-3.5">
        <div
          class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl border border-electric/30 bg-gradient-to-br from-electric/20 to-electric/5 text-blue-400 shadow-[0_0_18px_rgba(59,130,246,0.22),inset_0_0_10px_rgba(59,130,246,0.08)]"
        >
          <History :size="20" />
        </div>
        <div class="min-w-0">
          <h2 class="text-base font-bold tracking-wide text-white">订阅任务历史</h2>
          <p class="mt-0.5 text-xs text-slate-500">共 {{ total }} 条执行记录</p>
        </div>
      </div>
      <div class="flex items-center gap-2.5">
        <button
          type="button"
          class="flex h-9 w-9 items-center justify-center rounded-[10px] border border-white/10 bg-white/5 text-slate-400 transition-all duration-200 hover:border-electric/40 hover:bg-electric/10 hover:text-blue-400 hover:shadow-[0_0_14px_rgba(59,130,246,0.25)]"
          @click="fetchHistory"
          title="刷新"
        >
          <RefreshCw :size="16" :class="{ spin: loading }" />
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-[10px] border border-danger/40 bg-danger/10 px-4 py-2 text-[13px] font-semibold text-red-400 transition-all duration-200 hover:bg-danger/20 hover:text-red-300 hover:border-danger/60 hover:shadow-[0_0_18px_rgba(239,68,68,0.35)]"
          @click="clearHistory"
        >
          <Trash2 :size="15" />清空
        </button>
      </div>
    </header>

    <!-- ==================== 主视窗（独立 overflow-y-auto，内部滚动） ==================== -->
    <div class="records-body min-h-0 flex-1 overflow-y-auto overflow-x-hidden pr-1" v-loading="loading">
      <!-- 空状态：居中呼吸灯 -->
      <div v-if="!loading && !total" class="flex h-full min-h-[320px] flex-col items-center justify-center gap-1.5 text-center">
        <div class="empty-breathe mb-2.5 flex text-slate-500 drop-shadow-[0_0_24px_rgba(59,130,246,0.3)]">
          <History :size="64" />
        </div>
        <p class="text-[15px] font-semibold text-slate-400">暂无记录</p>
        <p class="text-[13px] text-slate-600">还没有任何订阅任务历史</p>
      </div>

      <!-- 日志列表（极浅斑马纹） -->
      <div v-else class="flex flex-col gap-0.5 pb-1">
        <div
          v-for="row in pagedData"
          :key="row.id"
          class="flex items-stretch rounded-xl border border-transparent transition-all duration-200 even:bg-white/[0.02] hover:border-electric/20 hover:bg-white/5"
          :class="{ 'row-fail': row.status !== 'success' }"
        >
          <!-- 左侧状态发光条 -->
          <div
            class="my-2 w-[3px] flex-shrink-0 self-stretch rounded-r"
            :class="row.status === 'success'
              ? 'bg-neon shadow-[0_0_8px_rgba(52,211,153,0.6)]'
              : 'bg-danger shadow-[0_0_8px_rgba(248,113,113,0.6)]'"
          ></div>

          <div class="flex min-w-0 flex-1 flex-col gap-1.5 px-3.5 py-3">
            <!-- 标题行 -->
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="flex min-w-0 flex-wrap items-baseline gap-2">
                <span class="text-[14px] font-semibold text-slate-100">{{ row.name }}</span>
                <span class="text-xs font-semibold text-slate-400">S{{ row.season }}</span>
                <span class="font-hud text-[11px] text-slate-600">TMDB {{ row.tmdb_id }}</span>
              </div>
              <div class="flex flex-shrink-0 gap-1.5">
                <span
                  class="inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[11px] font-semibold"
                  :class="row.status === 'success'
                    ? 'border-neon/25 bg-neon/10 text-neon'
                    : 'border-danger/25 bg-danger/10 text-danger'"
                >
                  <span class="h-1.5 w-1.5 rounded-full bg-current shadow-[0_0_6px_currentColor]"></span>
                  {{ row.status === 'success' ? '成功' : '失败' }}
                </span>
                <span
                  class="whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[11px] font-semibold"
                  :class="row.wash_type === 'complete'
                    ? 'border-warn/25 bg-warn/10 text-warn'
                    : row.wash_type === 'new_sub'
                      ? 'border-electric/25 bg-electric/10 text-electric'
                      : 'border-slate-500/20 bg-slate-500/10 text-slate-400'"
                >
                  {{ row.wash_type === 'complete' ? '完结洗版' : row.wash_type === 'new_sub' ? '新增配置' : '未知' }}
                </span>
              </div>
            </div>

            <!-- 时间 -->
            <div class="flex items-center gap-1.5 text-xs text-slate-600">
              <Clock :size="12" />
              <span>{{ formatDate(row.created_at) }}</span>
            </div>

            <!-- 执行参数 -->
            <div v-if="row.wash_params" class="flex flex-wrap gap-1">
              <span v-if="row.wash_params.scheme" class="p-chip p-scheme">策略: {{ row.wash_params.scheme }}</span>
              <span v-if="row.wash_params.filter_groups" class="p-chip p-filter">规则: {{ row.wash_params.filter_groups?.join(',') }}</span>
              <span v-if="row.wash_params.downloader" class="p-chip p-downloader">下载器: {{ row.wash_params.downloader }}</span>
              <span v-if="row.wash_params.quality" class="p-chip p-quality">画质: {{ row.wash_params.quality }}</span>
              <span v-if="row.wash_params.sites?.length" class="p-chip p-sites">站点: {{ formatSiteNames(row.wash_params.sites) }}</span>
            </div>

            <!-- 消息 -->
            <div
              v-if="row.message"
              class="break-all text-xs leading-relaxed"
              :class="row.status !== 'success' ? 'text-red-400' : 'text-slate-400'"
            >
              {{ row.message }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ==================== 底部固定分页器（不随列表滚动） ==================== -->
    <footer class="flex flex-shrink-0 flex-wrap items-center justify-between gap-3 border-t border-white/5 px-1 pb-3.5 pt-3">
      <span class="font-hud text-xs text-slate-600">共 {{ total }} 条</span>
      <div class="flex items-center gap-1.5">
        <button
          type="button"
          class="pager-btn flex h-[30px] min-w-[30px] items-center justify-center rounded-lg border border-white/10 bg-white/5 px-2 text-[13px] font-semibold text-slate-400 transition-all duration-200 hover:border-electric/40 hover:bg-electric/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-35"
          :disabled="page <= 1"
          @click="page--"
          title="上一页"
        >
          <ChevronLeft :size="16" />
        </button>
        <template v-for="(p, i) in pageNumbers" :key="p">
          <span v-if="i > 0 && p - pageNumbers[i - 1] > 1" class="px-1 text-slate-600">…</span>
          <button
            type="button"
            class="pager-btn flex h-[30px] min-w-[30px] items-center justify-center rounded-lg border px-2 text-[13px] font-semibold transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-35"
            :class="p === page
              ? 'border-transparent bg-electric text-white shadow-[0_0_14px_rgba(59,130,246,0.45)]'
              : 'border-white/10 bg-white/5 text-slate-400 hover:border-electric/40 hover:bg-electric/10 hover:text-white'"
            @click="page = p"
          >{{ p }}</button>
        </template>
        <button
          type="button"
          class="pager-btn flex h-[30px] min-w-[30px] items-center justify-center rounded-lg border border-white/10 bg-white/5 px-2 text-[13px] font-semibold text-slate-400 transition-all duration-200 hover:border-electric/40 hover:bg-electric/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-35"
          :disabled="page >= totalPages"
          @click="page++"
          title="下一页"
        >
          <ChevronRight :size="16" />
        </button>
      </div>
    </footer>
  </div>
</template>

<style scoped>
/* ==================== 主视窗极细自定义滚动条（4px 半透明白） ==================== */
.records-body::-webkit-scrollbar { width: 4px; }
.records-body::-webkit-scrollbar-track { background: transparent; }
.records-body::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.2);
  border-radius: 2px;
}
.records-body::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.4); }

/* ==================== 空状态呼吸灯（缓慢 Pulse） ==================== */
.empty-breathe {
  animation: breathe 2.6s ease-in-out infinite;
}
@keyframes breathe {
  0%, 100% { opacity: 0.35; transform: scale(1); }
  50% { opacity: 0.9; transform: scale(1.05); }
}

/* ==================== 参数标签药丸 ==================== */
.p-chip {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
  border: 1px solid;
}
.p-scheme { background: rgba(250, 204, 21, 0.07); color: #facc15; border-color: rgba(250, 204, 21, 0.25); text-shadow: 0 0 8px rgba(250, 204, 21, 0.35); }
.p-filter { background: rgba(148, 163, 184, 0.08); color: #cbd5e1; border-color: rgba(148, 163, 184, 0.2); }
.p-downloader { background: rgba(16, 185, 129, 0.08); color: #34d399; border-color: rgba(16, 185, 129, 0.25); }
.p-quality { background: rgba(248, 113, 113, 0.07); color: #fb7185; border-color: rgba(248, 113, 113, 0.25); }
.p-sites { background: rgba(139, 92, 246, 0.08); color: #a78bfa; border-color: rgba(139, 92, 246, 0.25); }

/* ==================== 动画 & 响应式 ==================== */
@keyframes spin { to { transform: rotate(360deg); } }
.spin { animation: spin 1s linear infinite; }

@media (max-width: 768px) {
  .records-root {
    height: calc(100dvh - 112px);
    padding: 0 12px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .empty-breathe, .spin, .record-row, .btn-icon, .btn-danger, .pager-btn {
    animation: none !important;
    transition: none !important;
  }
  .empty-breathe { opacity: 0.9; }
}
</style>
