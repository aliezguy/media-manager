<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  MessageSquare, Search, RefreshCw, Trash2, ChevronUp, ChevronDown,
  ListOrdered, Download, CircleAlert, X,
} from 'lucide-vue-next'

const API_URL = ''

const activeTab = ref<'library' | 'import'>('library')

// ============================================================================
// 类型
// ============================================================================
interface DanmuSource {
  source_id: number
  provider_name?: string
  is_favorited?: boolean
  is_finished?: boolean
  episode_count?: number
}

interface DanmuEpisode {
  episode_id: number | string
  provider?: string
  title?: string
  episode_index?: number
  comment_count?: number
  url?: string
}

interface DanmuAnime {
  anime_id: number
  danmu_title: string
  type?: string
  season?: number
  year?: number | null
  episode_count?: number
  source_count?: number
  sources: DanmuSource[]
  group_name?: string | null
  tmdb_id?: number | string | null
  // 备选名字/原名（仅详情接口返回）
  alias_cn1?: string | null
  alias_cn2?: string | null
  alias_cn3?: string | null
  name_en?: string | null
  name_jp?: string | null
  name_romaji?: string | null
}

interface SearchResultItem {
  result_index: number
  provider?: string
  mediaId?: string
  title: string
  type?: string
  season?: number
  year?: number
  episodeCount?: number
  url?: string
}

interface EditableEpisode {
  provider: string
  episodeId: string
  title: string
  episodeIndex: number
  url?: string
  checked: boolean
}

interface DanmuTask {
  task_id: string
  title: string
  status: string           // 上游中文枚举：等待/运行中/已完成/失败
  progress?: number | null
  description?: string
  created_at?: string
  is_system_task?: boolean
}

// ============================================================================
// 服务状态（未配置横幅 + 配置入口）
// ============================================================================
const configured = ref(false)
const baseUrl = ref('')
const statusLoading = ref(true)

// ============================================================================
// Tab1 弹幕库
// ============================================================================
const libraryLoading = ref(false)
const libraryItems = ref<DanmuAnime[]>([])
const filterKeyword = ref('')

const filteredItems = computed(() => {
  let list = libraryItems.value
  const kw = filterKeyword.value.trim().toLowerCase()
  if (kw) {
    list = list.filter(i => (i.danmu_title || '').toLowerCase().includes(kw))
  }
  return list
})

const loadLibrary = async (silent = false) => {
  if (!silent) libraryLoading.value = true
  try {
    const res = await axios.get(`${API_URL}/api/danmu/library`)
    libraryItems.value = res.data.items || []
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载弹幕库失败')
  } finally {
    libraryLoading.value = false
  }
}

// ---- 详情抽屉（anime → source → episode 三级） ----
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<DanmuAnime | null>(null)
const detailSources = ref<DanmuSource[]>([])
const sourcesLoading = ref(false)
// 分集懒加载 memoize（按 source_id 缓存）
const episodesBySource = reactive<Record<number, DanmuEpisode[]>>({})
const episodesLoading = reactive<Record<number, boolean>>({})

const openDetail = async (row: DanmuAnime) => {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = row
  detailSources.value = []
  try {
    const res = await axios.get(`${API_URL}/api/danmu/library/anime/${row.anime_id}`)
    detail.value = { ...row, ...res.data }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
  loadSources(row.anime_id)
}

const loadSources = async (animeId: number) => {
  sourcesLoading.value = true
  try {
    const res = await axios.get(`${API_URL}/api/danmu/library/anime/${animeId}/sources`)
    detailSources.value = res.data || []
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载数据源失败')
  } finally {
    sourcesLoading.value = false
  }
}

const toggleSourceEpisodes = async (src: DanmuSource) => {
  if (episodesBySource[src.source_id]) return  // 已加载过 → 折叠
  episodesLoading[src.source_id] = true
  try {
    const res = await axios.get(`${API_URL}/api/danmu/library/source/${src.source_id}/episodes`)
    episodesBySource[src.source_id] = res.data || []
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载分集失败')
  } finally {
    episodesLoading[src.source_id] = false
  }
}

const confirmDelete = async (msg: string, action: () => Promise<void>) => {
  try {
    await ElMessageBox.confirm(msg, '确认删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
      confirmButtonClass: 'el-button--danger',
    })
    await action()
  } catch (e) { /* 取消 */ }
}

const deleteAnime = async (row: DanmuAnime) => {
  await confirmDelete(`确认删除「${row.danmu_title}」及其全部弹幕？此操作不可恢复。`, async () => {
    try {
      await axios.delete(`${API_URL}/api/danmu/library/anime/${row.anime_id}`)
      ElMessage.success('删除任务已提交，稍后刷新生效')
      setTimeout(() => loadLibrary(true), 3000)
      if (detailVisible.value && detail.value?.anime_id === row.anime_id) {
        detailVisible.value = false
      }
    } catch (e: any) {
      ElMessage.error(e?.response?.data?.detail || '删除失败')
    }
  })
}

const deleteSource = async (src: DanmuSource) => {
  await confirmDelete(`确认删除数据源「${src.provider_name}」及其全部弹幕？`, async () => {
    try {
      await axios.delete(`${API_URL}/api/danmu/library/source/${src.source_id}`)
      ElMessage.success('删除任务已提交')
      delete episodesBySource[src.source_id]
      await loadSources(detail.value!.anime_id)
      setTimeout(() => loadLibrary(true), 3000)
    } catch (e: any) {
      ElMessage.error(e?.response?.data?.detail || '删除失败')
    }
  })
}

const deleteEpisode = async (ep: DanmuEpisode, src: DanmuSource) => {
  await confirmDelete(`确认删除第 ${ep.episode_index} 集弹幕「${ep.title || ''}」？`, async () => {
    try {
      await axios.delete(`${API_URL}/api/danmu/library/episode/${ep.episode_id}`)
      ElMessage.success('删除任务已提交')
      delete episodesBySource[src.source_id]
      setTimeout(() => loadLibrary(true), 3000)
    } catch (e: any) {
      ElMessage.error(e?.response?.data?.detail || '删除失败')
    }
  })
}

// ============================================================================
// Tab2 搜索导入
// ============================================================================
const searchKeyword = ref('')
const searchSeason = ref<number | undefined>(undefined)
const searching = ref(false)
const searchResults = ref<SearchResultItem[]>([])
const searchId = ref('')
const searchExpireAt = ref(0)
const SEARCH_TTL_MS = 8 * 60 * 1000  // 上游缓存 10min，留 2min 余量
// 轻量 1s ticker：驱动倒计时实时刷新（computed 需响应式依赖才重算）
const nowTick = ref(Date.now())
let _tickTimer: ReturnType<typeof setInterval> | null = null

const searchExpireText = computed(() => {
  if (!searchExpireAt.value) return ''
  const remain = Math.max(0, searchExpireAt.value - nowTick.value)
  const m = Math.floor(remain / 60000)
  const s = Math.floor((remain % 60000) / 1000)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
})
const searchExpired = computed(() => searchExpireAt.value > 0 && nowTick.value > searchExpireAt.value)

const doSearch = async () => {
  if (!searchKeyword.value.trim()) {
    ElMessage.warning('请输入关键词')
    return
  }
  searching.value = true
  searchResults.value = []
  try {
    const res = await axios.post(`${API_URL}/api/danmu/search`, {
      keyword: searchKeyword.value.trim(),
      season: searchSeason.value,
    })
    searchId.value = res.data.searchId || ''
    searchResults.value = res.data.results || []
    searchExpireAt.value = Date.now() + SEARCH_TTL_MS
    if (!searchResults.value.length) ElMessage.info('没有搜索到结果')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '搜索失败')
  } finally {
    searching.value = false
  }
}

// ---- 编辑分集对话框 ----
const editVisible = ref(false)
const editLoading = ref(false)
const editResult = ref<SearchResultItem | null>(null)
const editEpisodes = ref<EditableEpisode[]>([])
const editCheckedCount = computed(() => editEpisodes.value.filter(e => e.checked).length)

const openEdit = async (result: SearchResultItem) => {
  if (searchExpired.value || !searchId.value) {
    ElMessage.warning('搜索结果已过期（10 分钟），请重新搜索')
    return
  }
  editResult.value = result
  editEpisodes.value = []
  editVisible.value = true
  editLoading.value = true
  try {
    const res = await axios.get(`${API_URL}/api/danmu/search/episodes`, {
      params: { searchId: searchId.value, result_index: result.result_index },
    })
    editEpisodes.value = (res.data || []).map((e: DanmuEpisode) => ({
      provider: e.provider || '',
      episodeId: String(e.episode_id),
      title: e.title || '',
      episodeIndex: e.episode_index ?? 0,
      url: e.url,
      checked: true,
    }))
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载分集列表失败')
  } finally {
    editLoading.value = false
  }
}

const toggleAll = () => {
  const all = editCheckedCount.value === editEpisodes.value.length
  editEpisodes.value.forEach(e => { e.checked = !all })
}

const moveUp = (i: number) => {
  if (i <= 0) return
  const arr = editEpisodes.value
  ;[arr[i - 1], arr[i]] = [arr[i], arr[i - 1]]
}
const moveDown = (i: number) => {
  const arr = editEpisodes.value
  if (i >= arr.length - 1) return
  ;[arr[i], arr[i + 1]] = [arr[i + 1], arr[i]]
}

// 勾选的行按当前数组顺序重排 1..N（仅对勾选行重编，未勾选不动）
const autoRenumber = () => {
  let n = 1
  editEpisodes.value.forEach(e => { if (e.checked) e.episodeIndex = n++ })
  ElMessage.success(`已重排 ${n - 1} 集序号`)
}

// ---- 导入 + 任务列表面板（显示所有任务，活动任务置顶轮询刷新） ----
const importing = ref(false)
const myTaskId = ref('')               // 本次导入的任务，用于完成/失败检测
const taskList = ref<DanmuTask[]>([])
const tasksLoading = ref(false)
const TASK_POLL_MS = 2000
let _taskPollTimer: ReturnType<typeof setInterval> | null = null

const stopTaskPoll = () => {
  if (_taskPollTimer) { clearInterval(_taskPollTimer); _taskPollTimer = null }
}

// 上游 status 为中文枚举，按关键词映射（覆盖 等待/排队/运行/完成/失败 等变体）
const statusMeta = (status?: string): { label: string; type: 'primary' | 'success' | 'warning' | 'danger' | 'info' } => {
  const s = (status || '').toLowerCase()
  if (s.includes('运行') || s.includes('进行') || s === 'in_progress' || s === 'running' || s === 'processing')
    return { label: '进行中', type: 'primary' }
  if (s.includes('完成') || s === 'completed' || s === 'done' || s === 'success')
    return { label: '已完成', type: 'success' }
  if (s.includes('失败') || s.includes('错误') || s === 'error' || s === 'failed')
    return { label: '失败', type: 'danger' }
  if (s.includes('等待') || s.includes('排队') || s.includes('暂停') || s === 'pending' || s === 'queued' || s === 'waiting' || s === 'paused')
    return { label: '等待', type: 'warning' }
  return { label: status || '—', type: 'info' }
}

const isRunning = (t: DanmuTask) => statusMeta(t.status).label === '进行中'
const isWaiting = (t: DanmuTask) => statusMeta(t.status).label === '等待'
const isActive = (t: DanmuTask) => isRunning(t) || isWaiting(t)
const activeTaskCount = computed(() => taskList.value.filter(isActive).length)

// 活动任务置顶（进行中 > 等待），同层按创建时间倒序
const sortTasks = (list: DanmuTask[]) => {
  const rank = (t: DanmuTask) => (isRunning(t) ? 0 : isWaiting(t) ? 1 : 2)
  return [...list].sort((a, b) => {
    const r = rank(a) - rank(b)
    if (r !== 0) return r
    return String(b.created_at || '').localeCompare(String(a.created_at || ''))
  })
}

const loadTasks = async (silent = false) => {
  if (!silent) tasksLoading.value = true
  try {
    const res = await axios.get(`${API_URL}/api/danmu/tasks`, { params: { status: 'all' } })
    taskList.value = sortTasks(res.data || [])
  } catch {
    // ★ 静默失败：面板保留旧数据，下轮继续
  } finally {
    tasksLoading.value = false
  }
}

// 轮询任务列表：有活动任务时每 2s 刷新；本次导入任务到终态则提示 + 刷新弹幕库
const startTaskPolling = () => {
  stopTaskPoll()
  loadTasks(true)
  _taskPollTimer = setInterval(async () => {
    await loadTasks(true)
    if (myTaskId.value) {
      const mine = taskList.value.find(t => t.task_id === myTaskId.value)
      if (mine) {
        const m = statusMeta(mine.status)
        if (m.label === '已完成') {
          myTaskId.value = ''
          stopTaskPoll()
          ElMessage.success('导入完成，弹幕库已更新')
          loadLibrary(true)
          return
        }
        if (m.label === '失败') {
          myTaskId.value = ''
          stopTaskPoll()
          ElMessage.error(`导入失败: ${mine.description || '未知错误'}`)
          return
        }
      }
    }
    if (!taskList.value.some(isActive)) stopTaskPoll()
  }, TASK_POLL_MS)
}

const submitImport = async () => {
  if (!editResult.value) return
  if (!editCheckedCount.value) {
    ElMessage.warning('至少勾选一集')
    return
  }
  if (searchExpired.value || !searchId.value) {
    ElMessage.warning('搜索结果已过期，请重新搜索后再导入')
    return
  }
  const episodes = editEpisodes.value
    .filter(e => e.checked)
    .map(e => ({
      provider: e.provider,
      episodeId: e.episodeId,
      title: e.title,
      episodeIndex: e.episodeIndex,
      ...(e.url ? { url: e.url } : {}),
    }))
  importing.value = true
  try {
    const res = await axios.post(`${API_URL}/api/danmu/import/edited`, {
      searchId: searchId.value,
      result_index: editResult.value.result_index,
      title: editResult.value.title,
      episodes,
    })
    editVisible.value = false
    myTaskId.value = res.data.taskId
    startTaskPolling()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '导入失败')
  } finally {
    importing.value = false
  }
}

// ============================================================================
// 工具
// ============================================================================
const typeLabel = (t?: string) => {
  if (t === 'tv_series') return '剧集'
  if (t === 'movie') return '电影'
  return t || '—'
}

// 备选名字（名字1/2/3）与原名（英文/日文/罗马音），仅详情有值
const cnAliases = computed(() => [detail.value?.alias_cn1, detail.value?.alias_cn2, detail.value?.alias_cn3].filter((v): v is string => !!v))
const origNames = computed(() => [detail.value?.name_en, detail.value?.name_jp, detail.value?.name_romaji].filter((v): v is string => !!v))

// ============================================================================
// 生命周期
// ============================================================================
onMounted(async () => {
  try {
    const res = await axios.get(`${API_URL}/api/danmu/status`)
    configured.value = !!res.data?.configured
    baseUrl.value = res.data?.base_url || ''
  } catch (e) {
    configured.value = false
  } finally {
    statusLoading.value = false
  }
  if (configured.value) loadLibrary()
  if (configured.value) loadTasks(true)
  // 倒计时 ticker
  _tickTimer = setInterval(() => { nowTick.value = Date.now() }, 1000)
})

onUnmounted(() => {
  stopTaskPoll()
  if (_tickTimer) { clearInterval(_tickTimer); _tickTimer = null }
})
</script>

<template>
  <div class="danmu-root">
    <!-- ==================== 沉浸感背景光效 ==================== -->
    <div class="ambient ambient-a"></div>
    <div class="ambient ambient-b"></div>
    <div class="grid-overlay"></div>

    <!-- ==================== 未配置横幅 ==================== -->
    <div v-if="!configured" class="warn-banner fade-up">
      <CircleAlert :size="18" />
      <span>尚未配置弹幕服务。请到「基础配置 → 弹幕服务」填写服务地址与 API 密钥。</span>
      <span v-if="baseUrl" class="text-white/40">{{ baseUrl }}</span>
    </div>

    <!-- ==================== 吸顶操作栏 ==================== -->
    <div class="sticky-bar">
      <div class="min-w-0">
        <h1 class="page-title">媒体弹幕管理</h1>
        <p class="mt-0.5 text-xs tracking-widest text-slate-500">搜索 → 过滤分集 → 重整序号 → 导入弹幕库 · 按本项目媒体信息汉化展示</p>
      </div>
      <div class="flex gap-3">
        <button type="button" class="btn btn-primary" :disabled="!configured" @click="loadLibrary()">
          <RefreshCw :size="16" :class="{ 'animate-spin': libraryLoading }" />
          <span>刷新</span>
        </button>
      </div>
    </div>

    <div class="content">
      <!-- ==================== 双 Tab ==================== -->
      <div class="tabs-wrap fade-up">
        <button
          type="button" class="tab-btn" :class="{ active: activeTab === 'library' }"
          @click="activeTab = 'library'"
        >
          <MessageSquare :size="15" />弹幕库
        </button>
        <button
          type="button" class="tab-btn" :class="{ active: activeTab === 'import' }"
          @click="activeTab = 'import'"
        >
          <Download :size="15" />搜索导入
        </button>
      </div>

      <!-- ================================================================
           Tab1 弹幕库
      ================================================================= -->
      <template v-if="activeTab === 'library'">
        <!-- 过滤条 -->
        <div class="glass-card fade-up" style="--d: 60ms">
          <div class="filter-row">
            <div class="filter-item grow">
              <span class="filter-label">关键词</span>
              <el-input v-model="filterKeyword" placeholder="按标题搜索弹幕库" clearable />
            </div>
            <span class="filter-count">{{ filteredItems.length }} / {{ libraryItems.length }}</span>
          </div>
        </div>

        <!-- 主表 -->
        <div class="glass-card fade-up" style="--d: 120ms">
          <el-table
            v-loading="libraryLoading"
            :data="filteredItems"
            row-key="anime_id"
            class="danmu-table"
            empty-text="弹幕库暂无数据"
          >
            <el-table-column prop="danmu_title" label="标题" min-width="260" show-overflow-tooltip />
            <el-table-column prop="year" label="年份" width="80" />
            <el-table-column label="类型" width="90">
              <template #default="{ row }">{{ typeLabel(row.type) }}</template>
            </el-table-column>
            <el-table-column prop="episode_count" label="集数" width="70" />
            <el-table-column prop="source_count" label="源" width="60" />
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
                <el-button link type="danger" size="small" @click="deleteAnime(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>

      <!-- ================================================================
           Tab2 搜索导入
      ================================================================= -->
      <template v-else>
        <!-- 导入任务列表面板（显示所有任务，活动任务置顶，轮询刷新） -->
        <div class="glass-card task-banner fade-up">
          <div class="flex items-center gap-3">
            <ListOrdered :size="18" class="text-cyan-400" />
            <span class="text-sm font-semibold text-white">导入任务</span>
            <span class="text-xs text-slate-500">{{ activeTaskCount }} 个进行中</span>
            <span class="grow"></span>
            <button type="button" class="btn btn-primary btn-sm" :disabled="tasksLoading" @click="loadTasks()">
              <RefreshCw :size="14" :class="{ 'animate-spin': tasksLoading }" />
              <span>刷新</span>
            </button>
          </div>
          <el-table
            v-loading="tasksLoading" :data="taskList" row-key="task_id"
            class="danmu-table mt-3" max-height="300" empty-text="暂无任务"
          >
            <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="statusMeta(row.status).type" size="small" effect="light" round>
                  {{ statusMeta(row.status).label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="进度" width="140">
              <template #default="{ row }">
                <el-progress v-if="isRunning(row)" :percentage="row.progress ?? 0" :stroke-width="6" color="#06b6d4" />
                <span v-else-if="isWaiting(row)" class="text-xs text-amber-400">等待中…</span>
                <span v-else-if="row.progress != null" class="text-xs text-slate-400">{{ row.progress }}%</span>
                <span v-else class="text-slate-600">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
            <el-table-column label="时间" width="150">
              <template #default="{ row }">{{ row.created_at || '—' }}</template>
            </el-table-column>
          </el-table>
        </div>

        <!-- 搜索条 -->
        <div class="glass-card fade-up" style="--d: 60ms">
          <div class="search-row">
            <el-input
              v-model="searchKeyword" placeholder="输入关键词，如：天才厨人" class="grow"
              @keyup.enter="doSearch"
            />
            <div class="w-28">
              <el-input-number v-model="searchSeason" :min="1" :max="30" placeholder="季" controls-position="right" class="w-full" />
            </div>
            <button type="button" class="btn btn-primary" :disabled="searching" @click="doSearch">
              <Search :size="16" :class="{ 'animate-spin': searching }" />
              <span>{{ searching ? '搜索中…' : '搜索' }}</span>
            </button>
          </div>
          <div v-if="searchId" class="search-hint">
            <span :class="searchExpired ? 'text-red-400' : 'text-slate-400'">
              searchId 有效期 {{ searchExpireText }}
            </span>
            <span v-if="searchExpired" class="ml-2 text-red-400">已过期，请重新搜索</span>
          </div>
        </div>

        <!-- 搜索结果 -->
        <div class="glass-card fade-up" style="--d: 120ms">
          <div class="card-head2">
            <h2 class="text-[15px] font-bold tracking-wide text-white">搜索结果</h2>
            <span v-if="searchResults.length" class="text-xs text-slate-500">{{ searchResults.length }} 个数据源</span>
          </div>
          <el-table
            v-loading="searching" :data="searchResults" row-key="result_index"
            class="danmu-table" empty-text="请先搜索"
          >
            <el-table-column label="数据源" width="120">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.provider }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="title" label="标题" min-width="180" />
            <el-table-column label="类型" width="90">
              <template #default="{ row }">{{ typeLabel(row.type) }}</template>
            </el-table-column>
            <el-table-column prop="season" label="季" width="60" />
            <el-table-column prop="year" label="年份" width="80" />
            <el-table-column prop="episodeCount" label="集数" width="80" />
            <el-table-column label="操作" width="160" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openEdit(row)">选择并编辑分集</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>
    </div>

    <!-- ==================== 详情抽屉（三级） ==================== -->
    <el-drawer
      v-model="detailVisible" :title="detail?.danmu_title || '详情'"
      size="560px" :with-header="true"
    >
      <div v-loading="detailLoading" class="drawer-body">
        <!-- 作品信息卡 -->
        <div v-if="detail" class="anime-card">
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0">
              <h3 class="text-base font-bold text-white">{{ detail.danmu_title }}</h3>

              <!-- 备选名字（名字1/2/3） -->
              <div v-if="cnAliases.length" class="mt-2 flex flex-wrap items-center gap-2">
                <span class="alias-label">备选名字</span>
                <el-tag v-for="a in cnAliases" :key="a" size="small" effect="plain" type="info">{{ a }}</el-tag>
              </div>
              <!-- 原名（英文/日文/罗马音） -->
              <div v-if="origNames.length" class="mt-1.5 flex flex-wrap items-center gap-2">
                <span class="alias-label">原名</span>
                <el-tag v-for="a in origNames" :key="a" size="small" effect="plain">{{ a }}</el-tag>
              </div>

              <div class="mt-2 flex flex-wrap gap-4 text-xs text-slate-400">
                <span>类型 {{ typeLabel(detail.type) }}</span>
                <span>年份 {{ detail.year ?? '—' }}</span>
                <span>集数 {{ detail.episode_count ?? '—' }}</span>
                <span>数据源 {{ detail.source_count ?? '—' }}</span>
                <span v-if="detail.tmdb_id">TMDB {{ detail.tmdb_id }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 数据源列表（点击展开分集，懒加载） -->
        <div class="mt-5">
          <div class="section-title">数据源 ({{ detailSources.length }})</div>
          <div v-loading="sourcesLoading" class="space-y-3">
            <div v-for="src in detailSources" :key="src.source_id" class="source-card">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <el-tag size="small" effect="plain">{{ src.provider_name }}</el-tag>
                  <span v-if="src.is_finished" class="text-[10px] text-emerald-400">已完结</span>
                  <span class="text-[11px] text-slate-500">{{ episodesBySource[src.source_id]?.length ?? '' }}</span>
                </div>
                <div class="flex items-center gap-1">
                  <el-button
                    link type="primary" size="small"
                    @click="toggleSourceEpisodes(src)"
                  >
                    {{ episodesBySource[src.source_id] ? '折叠' : '展开分集' }}
                  </el-button>
                  <el-button link type="danger" size="small" @click="deleteSource(src)">
                    <Trash2 :size="13" />删源
                  </el-button>
                </div>
              </div>

              <!-- 分集子表 -->
              <div v-if="episodesBySource[src.source_id] || episodesLoading[src.source_id]" class="episodes-panel">
                <el-table
                  v-loading="episodesLoading[src.source_id]"
                  :data="episodesBySource[src.source_id] || []" size="small" max-height="320"
                  class="danmu-table" empty-text="该源暂无分集"
                >
                  <el-table-column prop="episode_index" label="集" width="60" />
                  <el-table-column prop="title" label="标题" min-width="160" show-overflow-tooltip />
                  <el-table-column label="弹幕" width="80">
                    <template #default="{ row }">
                      <span class="text-slate-400">{{ row.comment_count ?? '—' }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="" width="56" align="right">
                    <template #default="{ row }">
                      <el-button link type="danger" size="small" @click="deleteEpisode(row, src)">
                        <Trash2 :size="13" />
                      </el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </div>
          </div>
        </div>
      </div>
    </el-drawer>

    <!-- ==================== 编辑分集对话框 ==================== -->
    <el-dialog
      v-model="editVisible" width="820px" :title="`编辑分集 — ${editResult?.title || ''}（${editResult?.provider || ''}）`"
      :close-on-click-modal="false"
    >
      <div class="mb-3 flex items-center justify-between">
        <span class="text-sm text-slate-400">
          已勾选 <b class="text-cyan-400">{{ editCheckedCount }}</b> / {{ editEpisodes.length }} 集
        </span>
        <div class="flex gap-2">
          <el-checkbox :model-value="editCheckedCount === editEpisodes.length" @change="toggleAll">
            全选
          </el-checkbox>
          <button type="button" class="btn btn-ghost btn-sm" @click="autoRenumber">
            <ListOrdered :size="13" />一键重排序号
          </button>
        </div>
      </div>

      <div v-loading="editLoading" class="edit-table-wrap">
        <el-table
          :data="editEpisodes" size="small" row-key="episodeId" max-height="420"
          class="danmu-table" empty-text="加载分集列表…"
        >
          <el-table-column width="44" align="center">
            <template #default="{ row }">
              <el-checkbox v-model="row.checked" />
            </template>
          </el-table-column>
          <el-table-column label="排序" width="92" align="center">
            <template #default="{ $index }">
              <div class="flex items-center justify-center gap-0.5">
                <button type="button" class="icon-btn" :disabled="$index === 0" @click="moveUp($index)"><ChevronUp :size="13" /></button>
                <button type="button" class="icon-btn" :disabled="$index === editEpisodes.length - 1" @click="moveDown($index)"><ChevronDown :size="13" /></button>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="集数" width="96" align="center">
            <template #default="{ row }">
              <el-input-number v-model="row.episodeIndex" :min="1" :max="999" size="small" controls-position="right" class="w-20" />
            </template>
          </el-table-column>
          <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
          <el-table-column label="数据源" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ row.provider }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <template #footer>
        <span v-if="editVisible" class="float-left text-xs text-slate-600">
          导入前 {{ searchExpireText }} 内有效，过期请重新搜索
        </span>
        <button type="button" class="btn btn-ghost btn-sm" @click="editVisible = false">
          <X :size="14" />取消
        </button>
        <button type="button" class="btn btn-primary btn-sm" :disabled="importing || editLoading || !editCheckedCount" @click="submitImport">
          <Download :size="14" />{{ importing ? '提交中…' : `导入 ${editCheckedCount} 集` }}
        </button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="postcss">
/* ==================== 根容器 ==================== */
.danmu-root {
  position: relative;
  z-index: 1;
  max-width: 1180px;
  margin: 0 auto;
  padding: 16px 24px 40px;
}

.ambient {
  position: fixed;
  border-radius: 50%;
  pointer-events: none;
  z-index: 0;
}
.ambient-a {
  width: 560px; height: 560px;
  top: -180px; right: -140px;
  background: radial-gradient(circle, rgba(6, 182, 212, 0.12), transparent 70%);
}
.ambient-b {
  width: 480px; height: 480px;
  bottom: -160px; left: -120px;
  background: radial-gradient(circle, rgba(59, 130, 246, 0.1), transparent 70%);
}
.grid-overlay {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background-image:
    linear-gradient(rgba(148, 163, 184, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148, 163, 184, 0.045) 1px, transparent 1px);
  background-size: 46px 46px;
  -webkit-mask-image: radial-gradient(ellipse 85% 65% at 50% 0%, #000 30%, transparent 78%);
          mask-image: radial-gradient(ellipse 85% 65% at 50% 0%, #000 30%, transparent 78%);
}

/* ==================== 未配置横幅 ==================== */
.warn-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  margin-bottom: 10px;
  border-radius: 12px;
  border: 1px solid rgba(245, 158, 11, 0.35);
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.12), rgba(245, 158, 11, 0.04));
  color: #fcd34d;
  font-size: 13px;
  backdrop-filter: blur(14px);
}

/* ==================== 吸顶操作栏 ==================== */
.sticky-bar {
  position: sticky;
  top: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 16px 4px;
  margin-bottom: 10px;
  background: rgba(11, 17, 32, 0.62);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}
.page-title {
  font-size: 20px;
  font-weight: 800;
  letter-spacing: 0.3px;
  color: #fff;
  background: linear-gradient(90deg, #fff 20%, #67e8f9 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

/* ==================== 双 Tab ==================== */
.tabs-wrap {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  margin-bottom: 14px;
  border-radius: 14px;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.07);
  backdrop-filter: blur(12px);
}
.tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 8px 18px;
  border-radius: 10px;
  border: none;
  background: transparent;
  color: #94a3b8;
  font-size: 13.5px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
}
.tab-btn:hover { color: #e2e8f0; }
.tab-btn.active {
  color: #fff;
  background: linear-gradient(135deg, rgba(6, 182, 212, 0.25), rgba(59, 130, 246, 0.25));
  box-shadow: inset 0 0 0 1px rgba(34, 211, 238, 0.35);
}

/* ==================== 内容区 ==================== */
.content {
  @apply relative z-[1] grid grid-cols-1 gap-5;
}

.glass-card {
  @apply relative z-[1] overflow-hidden rounded-[22px] border border-white/10;
  background: linear-gradient(160deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.02));
  backdrop-filter: blur(18px) saturate(140%);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  box-shadow: 0 24px 60px -24px rgba(0, 0, 0, 0.65), inset 0 1px 0 rgba(255, 255, 255, 0.06);
}

/* 过滤条 / 搜索条 */
.filter-row, .search-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 18px;
  flex-wrap: wrap;
}
.filter-item { display: flex; align-items: center; gap: 8px; }
.filter-item.grow { flex: 1; min-width: 220px; }
.filter-label { font-size: 12.5px; color: #64748b; font-weight: 600; white-space: nowrap; }
.filter-count { font-size: 12px; color: #64748b; white-space: nowrap; }
.search-hint { padding: 0 18px 12px; font-size: 12px; }

.task-banner { padding: 16px 18px; border: 1px solid rgba(6, 182, 212, 0.25); }

.card-head2 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 14px 18px 6px;
}

/* ==================== 表格 ==================== */
.danmu-table {
  --el-table-border-color: rgba(255, 255, 255, 0.06);
  --el-table-header-bg-color: rgba(255, 255, 255, 0.03);
  --el-table-tr-bg-color: transparent;
  --el-table-row-hover-bg-color: rgba(6, 182, 212, 0.06);
  --el-table-bg-color: transparent;
  --el-table-text-color: #cbd5e1;
  --el-table-header-text-color: #94a3b8;
  background: transparent;
}
.danmu-table :deep(.el-table__inner-wrapper::before) { display: none; }
.danmu-table :deep(.el-table__empty-text) { color: #475569; }

/* ==================== 按钮 ==================== */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 22px;
  border-radius: 12px;
  border: 1px solid transparent;
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  color: #fff;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-sm { padding: 6px 12px; font-size: 12.5px; border-radius: 9px; }
.btn-primary {
  background: linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%);
  box-shadow: 0 4px 20px rgba(6, 182, 212, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.2);
}
.btn-primary:hover:not(:disabled) {
  transform: translateY(-1px) scale(1.03);
  box-shadow: 0 8px 30px rgba(6, 182, 212, 0.45), 0 0 26px rgba(59, 130, 246, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.2);
}
.btn-primary:active:not(:disabled) { transform: scale(0.98); }
.btn-ghost {
  border-color: rgba(148, 163, 184, 0.25);
  background: rgba(255, 255, 255, 0.04);
  color: #cbd5e1;
}
.btn-ghost:hover:not(:disabled) {
  border-color: rgba(34, 211, 238, 0.5);
  color: #e2e8f0;
  transform: translateY(-1px);
}

.icon-btn {
  width: 22px; height: 22px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 6px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  background: rgba(255, 255, 255, 0.04);
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.15s;
}
.icon-btn:hover:not(:disabled) { color: #22d3ee; border-color: rgba(34, 211, 238, 0.5); }
.icon-btn:disabled { opacity: 0.3; cursor: not-allowed; }

/* ==================== 详情抽屉 ==================== */
.drawer-body { padding: 4px 2px; }
.anime-card {
  padding: 14px 16px;
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
}
.section-title {
  margin-bottom: 10px;
  font-size: 12.5px;
  font-weight: 700;
  letter-spacing: 1px;
  color: #64748b;
}
.alias-label {
  font-size: 12px;
  color: #64748b;
  white-space: nowrap;
}
.source-card {
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(255, 255, 255, 0.025);
}
.episodes-panel { margin-top: 10px; }

/* ==================== 编辑分集 ==================== */
.edit-table-wrap { border-radius: 12px; overflow: hidden; }

/* ==================== Element Plus 深度定制 ==================== */
:deep(.el-input__wrapper), :deep(.el-textarea__inner) {
  @apply rounded-xl bg-black/20 transition-shadow duration-200;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.10);
}
:deep(.el-input__wrapper:hover) { box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.28); }
:deep(.el-input__wrapper.is-focus) {
  background: rgba(6, 182, 212, 0.06);
  box-shadow: 0 0 0 1px #22d3ee, 0 0 8px rgba(6, 182, 212, 0.5);
}
:deep(.el-input__inner) { color: #f1f5f9; }
:deep(.el-input__inner::placeholder) { color: #475569; }

:deep(.el-drawer), :deep(.el-dialog) { background: #0f172a; }
:deep(.el-drawer__header), :deep(.el-dialog__header) { color: #fff; border-bottom: 1px solid rgba(255, 255, 255, 0.07); }
:deep(.el-dialog__title) { color: #fff; font-weight: 700; }
:deep(.el-drawer__body) { color: #cbd5e1; }

/* ==================== 动画 ==================== */
.fade-up {
  opacity: 0;
  animation: fadeUp 0.6s cubic-bezier(0.22, 0.61, 0.36, 1) forwards;
  animation-delay: var(--d, 0ms);
}
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (max-width: 768px) {
  .danmu-root { padding: 10px 12px 32px; }
  .sticky-bar { padding: 12px 2px; }
  .page-title { font-size: 17px; }
  .btn { flex: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .fade-up, .btn { animation: none !important; transition: none !important; }
  .fade-up { opacity: 1; }
}
</style>
