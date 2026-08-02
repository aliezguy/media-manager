<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Delete, CircleCheck, Loading, Monitor, Close, Filter, Setting, Right } from '@element-plus/icons-vue'

// ==================== 类型定义 ====================
// qB 实例配置（/api/qb/configs）
interface QbInstance {
  id: string
  name: string
}

// qB 实例数据（/api/qb/data）— 含分类 / 标签
interface QbData {
  id: string
  name: string
  tags?: string[]
  categories?: string[]
}

// qB 种子（/api/qb/:id/torrents）
interface QbTorrent {
  name: string
  hash: string
  size: number
  progress: number
  state: string
  category?: string
}

// CD2 文件 / 目录条目（/api/cd2/directories）
interface CD2File {
  id: string
  name: string
  fullPathName: string
  size: number
  fileType?: string
  isDirectory: boolean
  createTime?: string | null
  writeTime?: string | null
  accessTime?: string | null
  isForbidden?: boolean
  isLocal?: boolean
  readOnly?: boolean
  thumbnailUrl?: string | null
  originalPath?: string | null
  cloudName?: string | null
  cloudUserName?: string | null
  fileCount?: number
  folderCount?: number
  totalSize?: number
}

// 残缺季核查：单个 Season 文件夹标注
interface SeasonCheckSeason {
  actual: number
  expected: number
  season_num?: number
  folder_name?: string
}

// 残缺季核查：单个剧集条目标注
interface SeasonCheckEntry {
  status: 'complete' | 'incomplete'
  incompleteCount: number
  totalSeasons: number
  seasons: Record<string, SeasonCheckSeason>
}

type SeasonCheckMap = Record<string, SeasonCheckEntry>

// 残缺季核查 API 返回的单个 Season 条目
interface SeasonCheckItem {
  show_name: string
  tmdb_id: number
  season_num: number
  folder_name: string
  folder_path: string
  actual_count: number
  expected_count: number
}

// 残缺季核查 API 响应（/api/directories/check-incomplete）
interface SeasonCheckResult {
  error?: string
  total_shows_scanned?: number
  total_seasons_checked?: number
  incomplete_seasons?: SeasonCheckItem[]
  empty_folders?: SeasonCheckItem[]
  complete_seasons?: SeasonCheckItem[]
}

// 整理分析结果（/api/organize/analyze）
interface OrgResult {
  success?: boolean
  title?: string
  year?: string | number
  season?: string | number
  total_episodes?: number
  tmdb_id?: number
  tmdb_name?: string
  resolved_category?: string
  source?: string
}

// 全自动洗版结果（/api/organize/auto_process、auto_process_batch）
interface AutoProcessItem {
  success: boolean
  stage: string
  message?: string
  task_id?: number
  tmdb_id?: number
  details?: { title?: string }
}

interface AutoProcessResult {
  ok?: number
  errors?: number
  success?: boolean
  stage?: string
  message?: string
  task_id?: number
  total?: number
  details?: { title?: string }
  results?: AutoProcessItem[]
}

// CD2 双端跳转结果
interface JumpResult {
  ok: boolean
  name?: string | null
  error?: string
}

// CD2 移动操作结果
interface MoveResult {
  success: boolean
  error?: string
  statusCode?: number
  [key: string]: unknown
}

// 提取错误信息：优先保留 Axios 响应 detail，其次 Error.message，最后兜底
const getErrMessage = (e: unknown, fallback = ''): string => {
  if (axios.isAxiosError(e)) {
    const detail = e.response?.data?.detail
    if (typeof detail === 'string' && detail) return detail
    return e.message || fallback
  }
  if (e instanceof Error) return e.message || fallback
  return typeof e === 'string' ? e : fallback
}

// ==================== 实例数据（从 API 获取） ====================
const instances = ref<QbInstance[]>([])
const instanceLoading = ref(false)

const fetchInstances = async () => {
  instanceLoading.value = true
  try {
    const res = await axios.get('/api/qb/configs')
    instances.value = res.data || []
  } catch (e) {
    ElMessage.error('获取 qB 实例列表失败')
    instances.value = []
  } finally {
    instanceLoading.value = false
  }
}

// ==================== 全局筛选状态 ====================
const searchQuery = ref('')
const selectedCategory = ref('')

// 预设分类（从后端 category.yaml 获取）
const presetCategories = ref<string[]>([])

// 分类数据 — 从 qB 实例动态获取
const leftCategories = ref<string[]>([])
const rightCategories = ref<string[]>([])

// CD2 独立分类（与顶部 torrent 筛选解耦）
const cd2Category = ref('国产剧')

// 分类选项（预设 + qB 实例合并）
const categoryOptions = computed(() => {
  const all = new Set([...presetCategories.value, ...leftCategories.value, ...rightCategories.value])
  return [
    { label: '全部分类', value: '' },
    ...[...all].sort().map(cat => ({ label: cat, value: cat }))
  ]
})

// 从 qB 实例获取分类列表
const fetchCategories = async (side: 'left' | 'right') => {
  const instanceId = side === 'left' ? leftInstanceId.value : rightInstanceId.value
  if (!instanceId) return
  try {
    const res = await axios.get('/api/qb/data')
    const data = res.data?.find?.((d: QbData) => d.id === instanceId)
    if (data?.categories) {
      if (side === 'left') leftCategories.value = data.categories
      else rightCategories.value = data.categories
    }
  } catch {
    // 静默失败，分类下拉保持已有数据
  }
}

// ==================== 实例 & 数据状态 ====================
const leftInstanceId = ref('')
const rightInstanceId = ref('')
const leftLoading = ref(false)
const rightLoading = ref(false)
const leftTorrents = ref<QbTorrent[]>([])   // 原始 API 数据
const rightTorrents = ref<QbTorrent[]>([])  // 原始 API 数据

// ==================== 双列独立分页状态 ====================
const currentPageLeft = ref(1)
const pageSizeLeft = ref(20)
const currentPageRight = ref(1)
const pageSizeRight = ref(20)

// ==================== 实例选择互斥逻辑 ====================
const leftInstanceOptions = computed(() =>
  instances.value.map(inst => ({
    ...inst,
    disabled: inst.id === rightInstanceId.value
  }))
)

const rightInstanceOptions = computed(() =>
  instances.value.map(inst => ({
    ...inst,
    disabled: inst.id === leftInstanceId.value
  }))
)

const leftInstanceName = computed(() =>
  instances.value.find(i => i.id === leftInstanceId.value)?.name || ''
)
const rightInstanceName = computed(() =>
  instances.value.find(i => i.id === rightInstanceId.value)?.name || ''
)

// ==================== 数据获取（从 API 拉取全部种子） ====================
const fetchLeftTorrents = async () => {
  if (!leftInstanceId.value) { leftTorrents.value = []; return }
  leftLoading.value = true
  try {
    const res = await axios.get(`/api/qb/${leftInstanceId.value}/torrents`, {
      params: { page: 1, page_size: 2000 }
    })
    const data = res.data
    leftTorrents.value = data.torrents || data
  } catch (e) {
    ElMessage.error(`获取左列种子失败: ${getErrMessage(e)}`)
    leftTorrents.value = []
  } finally {
    leftLoading.value = false
  }
}

const fetchRightTorrents = async () => {
  if (!rightInstanceId.value) { rightTorrents.value = []; return }
  rightLoading.value = true
  try {
    const res = await axios.get(`/api/qb/${rightInstanceId.value}/torrents`, {
      params: { page: 1, page_size: 2000 }
    })
    const data = res.data
    rightTorrents.value = data.torrents || data
  } catch (e) {
    ElMessage.error(`获取右列种子失败: ${getErrMessage(e)}`)
    rightTorrents.value = []
  } finally {
    rightLoading.value = false
  }
}

// 实例切换 → 拉取数据 + 分类 + 重置分页
watch(leftInstanceId, (newId) => {
  currentPageLeft.value = 1
  selectedCategory.value = ''
  if (newId) {
    fetchCategories('left')
    fetchLeftTorrents()
  } else {
    leftTorrents.value = []
    leftCategories.value = []
  }
})

watch(rightInstanceId, (newId) => {
  currentPageRight.value = 1
  if (newId) {
    fetchCategories('right')
    fetchRightTorrents()
  } else {
    rightTorrents.value = []
    rightCategories.value = []
  }
})

// ==================== 核心 Computed：过滤 ====================
const filteredLeftList = computed(() => {
  let list = leftTorrents.value
  if (!list.length) return []

  const q = searchQuery.value.trim().toLowerCase()
  if (q) {
    list = list.filter(t => t.name?.toLowerCase().includes(q))
  }
  if (selectedCategory.value) {
    list = list.filter(t => t.category === selectedCategory.value)
  }
  return list
})

const filteredRightList = computed(() => {
  let list = rightTorrents.value
  if (!list.length) return []

  const q = searchQuery.value.trim().toLowerCase()
  if (q) {
    list = list.filter(t => t.name?.toLowerCase().includes(q))
  }
  if (selectedCategory.value) {
    list = list.filter(t => t.category === selectedCategory.value)
  }
  return list
})

// 总条数（过滤后）
const totalLeft = computed(() => filteredLeftList.value.length)
const totalRight = computed(() => filteredRightList.value.length)

// ==================== 核心 Computed：分页截取 ====================
const paginatedLeftList = computed(() => {
  const start = (currentPageLeft.value - 1) * pageSizeLeft.value
  return filteredLeftList.value.slice(start, start + pageSizeLeft.value)
})

const paginatedRightList = computed(() => {
  const start = (currentPageRight.value - 1) * pageSizeRight.value
  return filteredRightList.value.slice(start, start + pageSizeRight.value)
})

// 筛选条件变更 → 重置到第一页
watch([searchQuery, selectedCategory], () => {
  currentPageLeft.value = 1
  currentPageRight.value = 1
})

// 每页条数变更 → 重置到第一页
watch(pageSizeLeft, () => { currentPageLeft.value = 1 })
watch(pageSizeRight, () => { currentPageRight.value = 1 })

// ==================== 批量选择 ====================
const selectedLeftTorrents = ref<string[]>([])

// 右列批量选择
const selectedRightTorrents = ref<string[]>([])
const batchOrganizing = ref(false)
const autoProcessing = ref(false)
const autoProcessResult = ref<AutoProcessResult | null>(null)

// 当前页是否全选
const isAllSelected = computed(() => {
  if (!paginatedLeftList.value.length) return false
  return paginatedLeftList.value.every(t => selectedLeftTorrents.value.includes(t.hash))
})

// 全选 / 取消全选当前页
const handleSelectAll = (val: string | number | boolean) => {
  if (val) {
    const currentHashes = paginatedLeftList.value.map(t => t.hash)
    const set = new Set([...selectedLeftTorrents.value, ...currentHashes])
    selectedLeftTorrents.value = [...set]
  } else {
    const currentHashes = new Set(paginatedLeftList.value.map(t => t.hash))
    selectedLeftTorrents.value = selectedLeftTorrents.value.filter(h => !currentHashes.has(h))
  }
}

// 右列当前页是否全选
const isAllSelectedRight = computed(() => {
  if (!paginatedRightList.value.length) return false
  return paginatedRightList.value.every(t => selectedRightTorrents.value.includes(t.hash))
})

// 单个勾选
const handleCheckOne = (hash: string) => {
  const idx = selectedLeftTorrents.value.indexOf(hash)
  if (idx === -1) {
    selectedLeftTorrents.value.push(hash)
  } else {
    selectedLeftTorrents.value.splice(idx, 1)
  }
}

// 右列全选 / 取消全选当前页
const handleSelectAllRight = (val: string | number | boolean) => {
  if (val) {
    const currentHashes = paginatedRightList.value.map(t => t.hash)
    const set = new Set([...selectedRightTorrents.value, ...currentHashes])
    selectedRightTorrents.value = [...set]
  } else {
    const currentHashes = new Set(paginatedRightList.value.map(t => t.hash))
    selectedRightTorrents.value = selectedRightTorrents.value.filter(h => !currentHashes.has(h))
  }
}

// 右列单个勾选
const handleCheckOneRight = (hash: string) => {
  const idx = selectedRightTorrents.value.indexOf(hash)
  if (idx === -1) {
    selectedRightTorrents.value.push(hash)
  } else {
    selectedRightTorrents.value.splice(idx, 1)
  }
}

// 右列批量整理
const handleBatchOrganize = async () => {
  if (!selectedRightTorrents.value.length) return
  const torrents = rightTorrents.value.filter(t => selectedRightTorrents.value.includes(t.hash))
  if (!torrents.length) {
    ElMessage.warning('未找到选中的种子')
    return
  }

  batchOrganizing.value = true
  try {
    for (const torrent of torrents) {
      await startOrganize(torrent)
    }
    ElMessage.success(`已整理 ${torrents.length} 个种子`)
  } catch (e) {
    ElMessage.error(`批量整理出错: ${getErrMessage(e)}`)
  } finally {
    batchOrganizing.value = false
    selectedRightTorrents.value = []
  }
}

// ==================== 右侧删除操作 ====================
const rightDeletingHash = ref<string | null>(null)
const rightBatchDeleting = ref(false)

// 右侧单条删除
const handleDeleteRightTorrent = async (torrent: QbTorrent) => {
  try {
    await ElMessageBox.confirm(
      `确定删除「${torrent.name?.substring(0, 50)}」及其所有下载的源文件吗？\n实例：${rightInstanceName.value}\n此操作不可恢复！`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  rightDeletingHash.value = torrent.hash
  try {
    await axios.post(`/api/qb/${rightInstanceId.value}/torrents/delete`, {
      hashes: [torrent.hash],
      delete_files: true
    })
    const idx = rightTorrents.value.findIndex(t => t.hash === torrent.hash)
    if (idx !== -1) rightTorrents.value.splice(idx, 1)
    ElMessage.success('已删除种子及文件')
  } catch (e) {
    ElMessage.error(`删除失败: ${getErrMessage(e)}`)
  } finally {
    rightDeletingHash.value = null
  }
}

// 右侧批量删除
const handleBatchDeleteRight = async () => {
  if (!selectedRightTorrents.value.length) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${selectedRightTorrents.value.length} 个种子及其所有下载的源文件吗？此操作不可恢复！`,
      '批量删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  rightBatchDeleting.value = true
  try {
    await axios.post(`/api/qb/${rightInstanceId.value}/torrents/delete`, {
      hashes: [...selectedRightTorrents.value],
      delete_files: true
    })
    const hashSet = new Set(selectedRightTorrents.value)
    rightTorrents.value = rightTorrents.value.filter(t => !hashSet.has(t.hash))
    ElMessage.success(`已删除 ${selectedRightTorrents.value.length} 个种子及文件`)
    selectedRightTorrents.value = []
  } catch (e) {
    ElMessage.error(`批量删除失败: ${getErrMessage(e)}`)
  } finally {
    rightBatchDeleting.value = false
  }
}

// 批量删除
const batchDeleting = ref(false)
const handleBatchDelete = async () => {
  if (!selectedLeftTorrents.value.length) return
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${selectedLeftTorrents.value.length} 个种子及其物理文件吗？此操作不可逆！`,
      '批量删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  batchDeleting.value = true
  try {
    await axios.post(`/api/qb/${leftInstanceId.value}/torrents/delete`, {
      hashes: [...selectedLeftTorrents.value],
      delete_files: true
    })
    const hashSet = new Set(selectedLeftTorrents.value)
    leftTorrents.value = leftTorrents.value.filter(t => !hashSet.has(t.hash))
    ElMessage.success(`已删除 ${selectedLeftTorrents.value.length} 个种子及文件`)
    selectedLeftTorrents.value = []
  } catch (e) {
    ElMessage.error(`批量删除失败: ${getErrMessage(e)}`)
  } finally {
    batchDeleting.value = false
  }
}

// 筛选/翻页 → 清空选中
watch([searchQuery, selectedCategory, currentPageLeft, pageSizeLeft], () => {
  selectedLeftTorrents.value = []
})
watch([searchQuery, selectedCategory, currentPageRight, pageSizeRight], () => {
  selectedRightTorrents.value = []
})

// ==================== 单条删除操作 ====================
const deletingHash = ref<string | null>(null)

const deleteTorrent = async (torrent: QbTorrent) => {
  try {
    await ElMessageBox.confirm(
      `确定删除「${torrent.name?.substring(0, 50)}」及其文件吗？\n实例：${leftInstanceName.value}`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  deletingHash.value = torrent.hash
  try {
    await axios.post(`/api/qb/${leftInstanceId.value}/torrents/delete`, {
      hashes: [torrent.hash],
      delete_files: true
    })
    const idx = leftTorrents.value.findIndex(t => t.hash === torrent.hash)
    if (idx !== -1) leftTorrents.value.splice(idx, 1)
    ElMessage.success('已删除种子及文件')
  } catch (e) {
    ElMessage.error(`删除失败: ${getErrMessage(e)}`)
  } finally {
    deletingHash.value = null
  }
}

// ==================== 工具函数 ====================
const formatBytes = (bytes: number, decimals = 2) => {
  if (!+bytes) return '0 Bytes'
  const k = 1024
  const dm = decimals < 0 ? 0 : decimals
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

const STATE_MAP: Record<string, string> = {
  'stalledUP': '做种中', 'uploading': '上传中', 'downloading': '下载中',
  'stalledDL': '等待下载', 'pausedDL': '暂停下载', 'pausedUP': '暂停上传',
  'queuedDL': '排队下载', 'queuedUP': '排队上传',
  'checkingUP': '校验中', 'checkingDL': '校验中',
  'error': '错误', 'missingFiles': '文件丢失',
  'metaDL': '获取元数据', 'moving': '移动中', 'unknown': '未知'
}

const formatState = (state: string) => STATE_MAP[state] || state

const getStateColor = (state: string) => {
  if (['stalledUP', 'uploading'].includes(state)) return '#10b981'
  if (['downloading', 'metaDL'].includes(state)) return '#3b82f6'
  if (['pausedDL', 'pausedUP'].includes(state)) return '#f59e0b'
  if (['error', 'missingFiles'].includes(state)) return '#ef4444'
  if (['queuedDL', 'queuedUP'].includes(state)) return '#f97316'
  if (['checkingUP', 'checkingDL'].includes(state)) return '#8b5cf6'
  return '#64748b'
}

// ==================== CD2 网盘目录浏览 (导航版) ====================
// 基础路径（从配置中读取，带 fallback 默认值）
const CD2_MEDIA_BASE = ref('/80003588/emby库/电视剧/')
const CD2_ORGANIZED_BASE = ref('/80003588/网盘整理/完结整理/电视剧/')

// ==================== 移动端响应式 ====================
const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
const showCD2 = ref(false)  // 移动端 CD2 默认收起

const onWindowResize = () => {
  windowWidth.value = window.innerWidth
}

// --- CD2 区域拖拽调整大小 ---
const cd2SectionHeight = ref<number | null>(null)  // null = 自动高度, number = 固定 px
const isDraggingCD2 = ref(false)
let dragStartY = 0
let dragStartHeight = 0

const onDragStart = (e: MouseEvent) => {
  isDraggingCD2.value = true
  dragStartY = e.clientY
  const el = document.querySelector<HTMLElement>('.cd2-section')
  dragStartHeight = el ? el.offsetHeight : 300
  document.addEventListener('mousemove', onDragMove)
  document.addEventListener('mouseup', onDragEnd)
  document.body.style.cursor = 'row-resize'
  document.body.style.userSelect = 'none'
}

const onDragMove = (e: MouseEvent) => {
  if (!isDraggingCD2.value) return
  const dy = dragStartY - e.clientY  // 向上拖 = 增大 CD2 区域
  const newHeight = Math.max(120, Math.min(800, dragStartHeight + dy))
  cd2SectionHeight.value = newHeight
}

const onDragEnd = () => {
  isDraggingCD2.value = false
  document.removeEventListener('mousemove', onDragMove)
  document.removeEventListener('mouseup', onDragEnd)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}

// --- 每列独立导航状态 ---
const cd2Loading = ref(false)
const cd2Error = ref('')

// 媒体库（左列）
const cd2MediaPath = ref('')
const cd2MediaFiles = ref<CD2File[]>([])
const cd2MediaYearInput = ref('')   // 年份快捷跳转输入

// 已完结整理（右列）
const cd2OrganizedPath = ref('')
const cd2OrganizedFiles = ref<CD2File[]>([])

// --- 残缺季雷达（内联标注版）---
// seasonCheckData: { [showPath]: { status: 'complete'|'incomplete', incompleteCount, totalSeasons, seasons: { [seasonPath]: { actual, expected } } } }
const seasonCheckData = ref<SeasonCheckMap>({})
const seasonCheckLoading = ref(false)
const seasonCheckError = ref('')

// 从扫描结果构建内联标注数据
const buildSeasonCheckMap = (result: SeasonCheckResult, currentPath: string): SeasonCheckMap => {
  const map: SeasonCheckMap = {}
  // result.incomplete_seasons: [{ show_name, tmdb_id, season_num, folder_name, folder_path, actual_count, expected_count }]
  for (const item of (result.incomplete_seasons || [])) {
    const showPath = (currentPath + item.show_name).replace(/\/+$/, '')
    if (!map[showPath]) {
      map[showPath] = { status: 'incomplete', incompleteCount: 0, totalSeasons: 0, seasons: {} }
    }
    map[showPath].incompleteCount++
    map[showPath].seasons[item.folder_path] = {
      actual: item.actual_count,
      expected: item.expected_count,
      season_num: item.season_num,
      folder_name: item.folder_name,
    }
  }
  // Mark empty folders too (from empty_folders)
  for (const item of (result.empty_folders || [])) {
    const showPath = (currentPath + item.show_name).replace(/\/+$/, '')
    if (!map[showPath]) {
      map[showPath] = { status: 'incomplete', incompleteCount: 0, totalSeasons: 0, seasons: {} }
    }
    map[showPath].seasons[item.folder_path] = {
      actual: 0,
      expected: item.expected_count,
      season_num: item.season_num,
      folder_name: item.folder_name,
    }
  }
  // Mark complete shows: any show dir listed that's not in the map is complete
  // We'll check this at render time — if a show dir has no entry in map, it might be complete or not scanned
  // For now, we also store a "scanned" flag
  return map
}

const runSeasonCheck = async (side: 'media' | 'organized' = 'media') => {
  const scanPath = side === 'media' ? cd2MediaPath.value : cd2OrganizedPath.value
  if (!scanPath) {
    ElMessage.warning('请先导航到年份目录（如 2026/）')
    return
  }
  seasonCheckLoading.value = true
  seasonCheckError.value = ''
  seasonCheckData.value = {}
  try {
    const res = await axios.post('/api/directories/check-incomplete', { path: scanPath })
    const data = res.data
    if (data.error) {
      seasonCheckError.value = data.error
      return
    }
    // 构建两份标注数据：左侧（media）和右侧（organized）
    // 用当前扫描的路径构建 map
    const map = buildSeasonCheckMap(data, scanPath)
    seasonCheckData.value = map
    const incompleteCount = data.incomplete_seasons?.length || 0
    const emptyCount = data.empty_folders?.length || 0
    if (incompleteCount === 0 && emptyCount === 0) {
      ElMessage.success(`扫描完成：${data.total_shows_scanned} 部剧集，所有 Season 文件完整 🎉`)
    } else {
      ElMessage.warning(
        `发现 ${incompleteCount} 个残缺季 + ${emptyCount} 个空目录（共扫描 ${data.total_shows_scanned} 部剧集）`
      )
    }
  } catch (e) {
    seasonCheckError.value = getErrMessage(e)
    ElMessage.error('残缺季核查失败: ' + seasonCheckError.value)
  } finally {
    seasonCheckLoading.value = false
  }
}

// 单剧集核查状态（记录正在核查中的 show path）
const checkingShows = ref<Set<string>>(new Set())

// 单剧集残缺季核查
const runSingleShowCheck = async (side: 'media' | 'organized', file: CD2File) => {
  if (!file.isDirectory) return
  const basePath = side === 'media' ? cd2MediaPath.value : cd2OrganizedPath.value
  const showPath = (basePath + file.name).replace(/\/+$/, '') + '/'

  // 标记为核查中
  const newSet = new Set(checkingShows.value)
  newSet.add(showPath)
  checkingShows.value = newSet

  try {
    const res = await axios.post('/api/directories/check-show-incomplete', { path: showPath })
    const data = res.data

    if (data.error) {
      ElMessage.error(`核查失败: ${data.error}`)
      return
    }

    // 将单剧核查结果合并到 seasonCheckData
    const map = { ...seasonCheckData.value }
    const incompleteCount = (data.incomplete_seasons?.length || 0) + (data.empty_folders?.length || 0)
    const totalSeasons = data.total_seasons_checked || 0

    const entry: SeasonCheckEntry = {
      status: incompleteCount > 0 ? 'incomplete' : 'complete',
      incompleteCount,
      totalSeasons,
      seasons: {},
    }

    // 合并所有 season 信息
    for (const item of (data.incomplete_seasons || [])) {
      entry.seasons[item.folder_path] = {
        actual: item.actual_count,
        expected: item.expected_count,
        season_num: item.season_num,
        folder_name: item.folder_name,
      }
    }
    for (const item of (data.empty_folders || [])) {
      entry.seasons[item.folder_path] = {
        actual: 0,
        expected: item.expected_count,
        season_num: item.season_num,
        folder_name: item.folder_name,
      }
    }
    for (const item of (data.complete_seasons || [])) {
      entry.seasons[item.folder_path] = {
        actual: item.actual_count,
        expected: item.expected_count,
        season_num: item.season_num,
        folder_name: item.folder_name,
      }
    }

    map[showPath.replace(/\/+$/, '')] = entry
    seasonCheckData.value = map

    if (incompleteCount === 0) {
      ElMessage.success(`「${file.name}」所有 ${totalSeasons} 个 Season 文件完整 ✅`)
    } else {
      ElMessage.warning(`「${file.name}」: ${incompleteCount} 个残缺季 / 空目录（共 ${totalSeasons} 个 Season）`)
    }
  } catch (e) {
    ElMessage.error(`核查失败: ${getErrMessage(e)}`)
  } finally {
    const newSet2 = new Set(checkingShows.value)
    newSet2.delete(showPath)
    checkingShows.value = newSet2
  }
}

// 清除当前标注
const clearSeasonCheck = () => {
  seasonCheckData.value = {}
  seasonCheckError.value = ''
}

// 获取单个文件/目录的内联标注信息（用于列表渲染）
const getShowCheckStatus = (file: CD2File): SeasonCheckEntry | null => {
  if (!file.isDirectory) return null
  const key = (cd2MediaPath.value + file.name).replace(/\/+$/, '')
  const entry = seasonCheckData.value[key]
  if (!entry) return null
  return entry
}

const getOrganizedShowCheckStatus = (file: CD2File): SeasonCheckEntry | null => {
  if (!file.isDirectory) return null
  const key = (cd2OrganizedPath.value + file.name).replace(/\/+$/, '')
  const entry = seasonCheckData.value[key]
  if (!entry) return null
  return entry
}

// 检查某个 show 是否正在被单剧核查
const isShowChecking = (side: 'media' | 'organized', file: CD2File): boolean => {
  if (!file.isDirectory) return false
  const basePath = side === 'media' ? cd2MediaPath.value : cd2OrganizedPath.value
  const showPath = (basePath + file.name).replace(/\/+$/, '') + '/'
  return checkingShows.value.has(showPath)
}

const getSeasonCheckDetail = (file: CD2File, side: 'media' | 'organized'): SeasonCheckSeason | null => {
  if (!file.isDirectory) return null
  const basePath = side === 'media' ? cd2MediaPath.value : cd2OrganizedPath.value
  // 构建 season 文件夹的完整路径
  const seasonPath = (basePath + file.name).replace(/\/+$/, '')
  // 遍历所有 show 的 seasons 查找匹配
  for (const showEntry of Object.values(seasonCheckData.value)) {
    if (showEntry.seasons && showEntry.seasons[seasonPath]) {
      return showEntry.seasons[seasonPath]
    }
  }
  return null
}

// 计算当前目录下有多少个 show 被标记
const checkedShowCount = computed(() => Object.keys(seasonCheckData.value).length)

// 计算各列的分类根路径（基础路径 + CD2分类 + /）
// CD2 使用独立的 cd2Category，与顶部 torrent 筛选解耦
const cd2MediaRoot = computed(() => {
  const cat = cd2Category.value || '国产剧'
  return CD2_MEDIA_BASE.value + cat + '/'
})

const cd2OrganizedRoot = computed(() => {
  const cat = cd2Category.value || '国产剧'
  return CD2_ORGANIZED_BASE.value + cat + '/'
})

// 从完整路径中剥离根路径，得到可读的相对路径
const cd2MediaRelative = computed(() => {
  const root = cd2MediaRoot.value
  if (cd2MediaPath.value === root) return ''
  if (cd2MediaPath.value.startsWith(root)) return cd2MediaPath.value.slice(root.length)
  return cd2MediaPath.value
})

const cd2OrganizedRelative = computed(() => {
  const root = cd2OrganizedRoot.value
  if (cd2OrganizedPath.value === root) return ''
  if (cd2OrganizedPath.value.startsWith(root)) return cd2OrganizedPath.value.slice(root.length)
  return cd2OrganizedPath.value
})

// 是否可返回上一级（当前路径深度 > 根路径深度）
const cd2MediaCanGoBack = computed(() => cd2MediaPath.value !== cd2MediaRoot.value)
const cd2OrganizedCanGoBack = computed(() => cd2OrganizedPath.value !== cd2OrganizedRoot.value)

// --- 路径深度（相对根路径的 / 分段数）---
const cd2MediaDepth = computed(() => {
  const rel = cd2MediaRelative.value
  if (!rel) return 0
  return rel.split('/').filter(s => s.length > 0).length
})

const cd2OrganizedDepth = computed(() => {
  const rel = cd2OrganizedRelative.value
  if (!rel) return 0
  return rel.split('/').filter(s => s.length > 0).length
})

// --- 智能统计开关：严格只在 Depth >= 2（进入具体剧集内部）时才请求 stats ---
// Depth 0: /80003588/.../国产剧/             → 年份列表   → NO stats
// Depth 1: /80003588/.../国产剧/2026/        → 剧名列表   → NO stats
// Depth 2: /80003588/.../国产剧/2026/主角/   → Season列表 → YES stats
// 注意：不再使用单一 OR 合并值。改为在 loadCD2Data 中按侧独立计算并传递，
// 防止一侧进入深层目录时另一侧年份层级被错误注入 stats。

// --- 根目录年份过滤：在分类根目录时，只显示最近 5 个年份 ---
const isAtMediaRoot = computed(() => cd2MediaPath.value === cd2MediaRoot.value)
const isAtOrganizedRoot = computed(() => cd2OrganizedPath.value === cd2OrganizedRoot.value)

const displayedMediaFiles = computed(() => {
  let list = cd2MediaFiles.value
  if (!isAtMediaRoot.value) return list
  // 年份降序排列 + 截取前 5
  return [...list]
    .sort((a, b) => {
      const na = parseInt(a.name), nb = parseInt(b.name)
      if (!isNaN(na) && !isNaN(nb)) return nb - na
      return b.name.localeCompare(a.name)
    })
    .slice(0, 5)
})

const displayedOrganizedFiles = computed(() => {
  let list = cd2OrganizedFiles.value
  if (!isAtOrganizedRoot.value) return list
  return [...list]
    .sort((a, b) => {
      const na = parseInt(a.name), nb = parseInt(b.name)
      if (!isNaN(na) && !isNaN(nb)) return nb - na
      return b.name.localeCompare(a.name)
    })
    .slice(0, 5)
})

// --- 工具：判断文件夹名是否为年份（纯 4 位数字）---
const isYearFolder = (name: string) => /^\d{4}$/.test(name)

// --- 通用数据加载 ---
// silent=true: 静默刷新，不触发 loading 遮罩、不清除错误、不弹 toast。
// 用于 CD2 缓存延迟后的二次拉取，获取刚计算完成的 stats。
const loadCD2Data = async (silent = false) => {
  if (!silent) {
    cd2Loading.value = true
    cd2Error.value = ''
  }

  // 内联计算深度，确保每个侧的 stats 独立判断。
  const mediaRel = cd2MediaRelative.value
  const organizedRel = cd2OrganizedRelative.value
  const mediaDepth = mediaRel ? mediaRel.split('/').filter(s => s.length > 0).length : 0
  const organizedDepth = organizedRel ? organizedRel.split('/').filter(s => s.length > 0).length : 0

  // 二次校验：如果路径以 4 位年份结尾，强制关闭 stats（防止 OR 泄漏到年份层级）
  const YEAR_DIR_RE = /\/\d{4}\/?$/
  const mediaPathIsYear = YEAR_DIR_RE.test(cd2MediaPath.value)
  const organizedPathIsYear = YEAR_DIR_RE.test(cd2OrganizedPath.value)

  const needMediaStats = (mediaDepth >= 2) && !mediaPathIsYear
  const needOrganizedStats = (organizedDepth >= 2) && !organizedPathIsYear

  if (!silent) {
    console.log(
      '[CD2 Fetch]',
      `mediaDepth=${mediaDepth} (isYear=${mediaPathIsYear}) mediaStats=${needMediaStats}`,
      `| organizedDepth=${organizedDepth} (isYear=${organizedPathIsYear}) organizedStats=${needOrganizedStats}`,
      '| media:', cd2MediaPath.value,
      '| organized:', cd2OrganizedPath.value,
    )
  }

  try {
    const res = await axios.get('/api/cd2/directories', {
      params: {
        media_dir: cd2MediaPath.value,
        organized_dir: cd2OrganizedPath.value,
        media_with_stats: needMediaStats,
        organized_with_stats: needOrganizedStats,
      },
    })
    cd2MediaFiles.value = res.data.media || []
    cd2OrganizedFiles.value = res.data.organized || []
    if (!silent) {
      cd2Error.value = ''
    }
  } catch (e) {
    if (!silent) {
      cd2Error.value = getErrMessage(e, '获取CD2目录失败')
      ElMessage.error(cd2Error.value)
    }
  } finally {
    if (!silent) {
      cd2Loading.value = false
    }
  }
}

// --- 导航函数 ---
const enterFolder = (side: 'media' | 'organized', folderName: string) => {
  if (side === 'media') {
    cd2MediaPath.value = cd2MediaPath.value + folderName + '/'
  } else {
    cd2OrganizedPath.value = cd2OrganizedPath.value + folderName + '/'
  }
  loadCD2Data()
}

const goBack = (side: 'media' | 'organized') => {
  if (side === 'media') {
    if (!cd2MediaCanGoBack.value) return
    const trimmed = cd2MediaPath.value.replace(/\/+$/, '')
    const idx = trimmed.lastIndexOf('/')
    cd2MediaPath.value = trimmed.slice(0, idx + 1)
  } else {
    if (!cd2OrganizedCanGoBack.value) return
    const trimmed = cd2OrganizedPath.value.replace(/\/+$/, '')
    const idx = trimmed.lastIndexOf('/')
    cd2OrganizedPath.value = trimmed.slice(0, idx + 1)
  }
  loadCD2Data()
}

// --- 年份快捷跳转（媒体库） ---
const jumpToYear = () => {
  const year = String(cd2MediaYearInput.value || '').trim()
  if (!year) return
  cd2MediaPath.value = cd2MediaRoot.value + year + '/'
  cd2MediaYearInput.value = ''
  loadCD2Data()
}

// --- 分类变更时重置导航到新根路径 ---
// 顶部 torrent 筛选分类变更 → 同步到 CD2 分类
watch(selectedCategory, (newCat) => {
  if (newCat) {
    cd2Category.value = newCat
  }
})

// CD2 分类变更 → 重置导航并重新加载
watch(cd2Category, () => {
  cd2MediaPath.value = cd2MediaRoot.value
  cd2OrganizedPath.value = cd2OrganizedRoot.value
  loadCD2Data()
})

// ==================== CD2 目录识别功能 ====================
const cd2Identifying = ref(false)

const identifyCD2Directory = async (side: 'media' | 'organized') => {
  const path = side === 'media' ? cd2MediaPath.value : cd2OrganizedPath.value
  const segments = path.replace(/\/+$/, '').split('/').filter(s => s.length > 0)
  const dirName = segments[segments.length - 1] || ''

  if (!dirName) {
    ElMessage.warning('无法获取当前目录名')
    return
  }

  cd2Identifying.value = true
  orgResult.value = null
  orgError.value = ''
  orgCD2Status.value = ''

  try {
    const res = await axios.post('/api/organize/analyze', {
      torrent_name: dirName,
    })
    orgResult.value = res.data

    // 同步搜索框
    searchQuery.value = res.data.title || ''

    // 自动切换分类（保存当前 CD2 路径，防止 watcher 重置导致跳转）
    const category = res.data.resolved_category
    if (category && selectedCategory.value !== category) {
      const savedMediaPath = cd2MediaPath.value
      const savedOrganizedPath = cd2OrganizedPath.value
      selectedCategory.value = category
      await nextTick()
      cd2MediaPath.value = savedMediaPath
      cd2OrganizedPath.value = savedOrganizedPath
      loadCD2Data()
    }

    // 构建状态信息
    const parts = []
    if (res.data.title) parts.push(`《${res.data.title}》`)
    if (res.data.year) parts.push(res.data.year)
    if (res.data.season) parts.push(`第${res.data.season}季`)
    if (res.data.total_episodes) parts.push(`${res.data.total_episodes}集`)
    if (res.data.tmdb_id) parts.push(`TMDB:${res.data.tmdb_id}`)
    if (res.data.resolved_category) parts.push(`分类:${res.data.resolved_category}`)
    orgCD2Status.value = parts.join(' · ')
  } catch (e) {
    orgError.value = getErrMessage(e, '识别失败')
    ElMessage.error(orgError.value)
  } finally {
    cd2Identifying.value = false
  }
}

// ==================== CD2 左侧（媒体库）删除功能 ====================
const selectedCd2MediaItems = ref<string[]>([])
const cd2Deleting = ref(false)

const isAllCd2MediaSelected = computed(() => {
  if (!displayedMediaFiles.value.length) return false
  return displayedMediaFiles.value.every(f =>
    selectedCd2MediaItems.value.includes(f.fullPathName || f.name)
  )
})

const handleSelectAllCd2Media = (val: string | number | boolean) => {
  if (val) {
    const keys = displayedMediaFiles.value.map(f => f.fullPathName || f.name)
    const set = new Set([...selectedCd2MediaItems.value, ...keys])
    selectedCd2MediaItems.value = [...set]
  } else {
    const currentKeys = new Set(displayedMediaFiles.value.map(f => f.fullPathName || f.name))
    selectedCd2MediaItems.value = selectedCd2MediaItems.value.filter(k => !currentKeys.has(k))
  }
}

const handleCheckOneCd2Media = (key: string) => {
  const idx = selectedCd2MediaItems.value.indexOf(key)
  if (idx === -1) {
    selectedCd2MediaItems.value.push(key)
  } else {
    selectedCd2MediaItems.value.splice(idx, 1)
  }
}

// 删除确认弹窗
const confirmCd2Delete = async (count: number): Promise<boolean> => {
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${count} 个项目吗？文件将被移动到 CD2 回收站。`,
      'CD2 删除确认',
      {
        type: 'warning',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
      }
    )
    return true
  } catch {
    return false
  }
}

// 单个删除
const handleDeleteSingleCd2Item = async (file: CD2File) => {
  const key = file.fullPathName || file.name
  const paths = [key]

  const confirmed = await confirmCd2Delete(1)
  if (!confirmed) return

  cd2Deleting.value = true
  selectedCd2MediaItems.value = selectedCd2MediaItems.value.filter(k => k !== key)
  try {
    await axios.delete('/api/cd2/delete', {
      data: { paths },
    })
    ElMessage.success(`已删除: ${file.name}`)
    loadCD2Data()
    setTimeout(() => loadCD2Data(true), 500)          // 0.5s 后静默刷新 — 应对缓存延迟
  } catch (e) {
    ElMessage.error(`删除失败: ${getErrMessage(e)}`)
  } finally {
    cd2Deleting.value = false
  }
}

// 批量删除
const handleBatchDeleteCd2Items = async () => {
  if (!selectedCd2MediaItems.value.length) return

  const paths = [...selectedCd2MediaItems.value]
  const confirmed = await confirmCd2Delete(paths.length)
  if (!confirmed) return

  cd2Deleting.value = true
  selectedCd2MediaItems.value = []
  try {
    await axios.delete('/api/cd2/delete', {
      data: { paths },
    })
    ElMessage.success(`已删除 ${paths.length} 个项目`)
    loadCD2Data()
    setTimeout(() => loadCD2Data(true), 500)          // 0.5s 后静默刷新 — 应对缓存延迟
  } catch (e) {
    ElMessage.error(`批量删除失败: ${getErrMessage(e)}`)
  } finally {
    cd2Deleting.value = false
  }
}

// CD2 导航/刷新 → 清空选中
watch([cd2MediaPath, cd2MediaFiles], () => {
  selectedCd2MediaItems.value = []
})

// ==================== CD2 右侧（已完结）移动至左侧（媒体库） ====================
const cd2Moving = ref(false)

// 从已完结侧相对路径中提取 年份、剧名
const parseOrganizedPath = (): { year: string | null; showName: string | null; category: string; depth: number } => {
  const rel = cd2OrganizedRelative.value
  const segments = rel.split('/').filter(s => s.length > 0)
  return {
    year: segments[0] || null,
    showName: segments[1] || null,
    category: selectedCategory.value || '国产剧',
    depth: segments.length,
  }
}

// 构造左侧目标父目录
const buildMediaDestPath = (year: string, showName: string | null) => {
  const root = cd2MediaRoot.value  // e.g. /80003588/emby库/电视剧/国产剧/
  if (!year) return root
  if (!showName) return root + year + '/'
  return root + year + '/' + showName + '/'
}

// 核心移动函数
const doMoveToLeft = async (sourcePaths: string[], destPath: string): Promise<MoveResult> => {
  cd2Moving.value = true
  try {
    const res = await axios.post('/api/cd2/move', {
      source_paths: sourcePaths,
      dest_path: destPath,
      conflict_policy: 1,  // Rename on conflict
    })
    return { success: true, ...res.data }
  } catch (e) {
    return {
      success: false,
      error: getErrMessage(e, '移动失败'),
      statusCode: axios.isAxiosError(e) ? e.response?.status : undefined,
    }
  } finally {
    cd2Moving.value = false
  }
}

// 移动单个 Season 文件夹 → 左侧对应剧集目录
const handleMoveSingleToLeft = async (file: CD2File) => {
  const { year, showName } = parseOrganizedPath()
  const sourcePath = file.fullPathName || file.name
  const sourceName = file.name

  if (!year || !showName) {
    ElMessage.warning('无法确定所属年份或剧名，请进入具体剧集目录')
    return
  }

  const destPath = buildMediaDestPath(year, showName)

  // 确认弹窗
  try {
    await ElMessageBox.confirm(
      `确定将【${sourceName}】\n从：${sourcePath}\n移至：${destPath}\n吗？`,
      '移动至媒体库',
      { type: 'warning', confirmButtonText: '确认移动', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  // 执行移动
  const result = await doMoveToLeft([sourcePath], destPath)
  if (result.success) {
    ElMessage.success(`已移动: ${sourceName}`)
    loadCD2Data()
    setTimeout(() => loadCD2Data(true), 1000)         // 1s 后静默刷新 — 应对缓存延迟
  } else {
    // 容错：目标目录不存在 → 弹出三段式选择对话框
    const notFound = result.statusCode === 502 || /not found|不存在|no such/i.test(result.error || '')
    if (notFound) {
      moveFallbackShowName.value = showName
      moveFallbackSourcePath.value = sourcePath
      moveFallbackYear.value = year
      moveFallbackVisible.value = true
    } else {
      ElMessage.error(`移动失败: ${result.error}`)
    }
  }
}

// 移动当前整个剧集目录 → 左侧年份目录（逐季移动，不碰根目录）
const handleMoveEntireShowToLeft = async () => {
  const { year, showName, depth } = parseOrganizedPath()

  if (depth < 2) {
    ElMessage.warning('请先进入具体的剧集目录')
    return
  }
  // depth >= 2 时 segments[0]/segments[1] 必存在，year / showName 不可能为 null
  const dirYear = year!
  const dirShow = showName!

  const sourcePath = cd2OrganizedPath.value
  const destPath = buildMediaDestPath(dirYear, null)

  try {
    await ElMessageBox.confirm(
      `确定将整个剧集【${dirShow}】\n从：${sourcePath}\n移至左侧年份目录：${destPath}\n吗？\n\n（仅移动 Season 子文件夹，保留源根目录）`,
      '移动整剧至媒体库',
      { type: 'warning', confirmButtonText: '确认移动整剧', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  cd2Moving.value = true
  try {
    const res = await axios.post('/api/cd2/move_show_seasons', {
      source_show_path: sourcePath,
      target_parent_path: destPath,
      conflict_policy: 1,
    })
    const data = res.data
    ElMessage.success(`已移动整剧: ${dirShow}（${data.moved_seasons || 0} 个 Season）`)
    goBack('organized')
    // 左侧导航到刚移入的剧集目录，展示 Season 及 stats
    cd2MediaPath.value = data.target_show_path || buildMediaDestPath(dirYear, dirShow)
    loadCD2Data()                                    // 即时加载 — 展现目录结构
    setTimeout(() => loadCD2Data(true), 2000)         // 2s 后静默重拉 — 获取 stats
  } catch (e) {
    const errMsg = getErrMessage(e, '移动失败')
    ElMessage.error(`移动整剧失败: ${errMsg}`)
  } finally {
    cd2Moving.value = false
  }
}

// 从 CD2 已完结面板直接触发自动化洗版
const handleAutoProcessFromCD2 = async () => {
  const { showName, depth } = parseOrganizedPath()

  if (depth < 2) {
    ElMessage.warning('请先进入具体的剧集目录')
    return
  }
  // depth >= 2 时 segments[1] 必存在，showName 不可能为 null
  const dirShow = showName!

  // 从文件夹名提取 tmdb_id（例如 "主角(2026) {tmdb=284110}"）
  const tmdbMatch = dirShow.match(/\{tmdb=(\d+)\}/)
  const tmdbId = tmdbMatch ? parseInt(tmdbMatch[1]) : null

  // 尝试从右列种子中找到匹配项（用于获取 qb_config_id）
  const matchedTorrent = rightTorrents.value.find(t => {
    if (tmdbId) return t.name.includes(`{tmdb=${tmdbId}}`)
    return t.name.includes(dirShow)
  })

  const torrentName = matchedTorrent?.name || `${dirShow} {tmdb=${tmdbId || ''}}`
  const qbConfigId = rightInstanceId.value || ''

  try {
    await ElMessageBox.confirm(
      `将对【${dirShow}】执行全自动洗版流程：\n\n` +
      '1. 校验「已完结」目录中所有 Season 是否真正完结\n' +
      '2. 对比「媒体库」版本，智能决策保留/删除\n' +
      '3. 严格四重校验（名称/数量/总大小/单文件）后清理重复\n\n' +
      (matchedTorrent ? `匹配种子: ${matchedTorrent.name.substring(0, 60)}` : '未在右列找到匹配种子') +
      '\n\n确认开始？',
      '全自动洗版确认',
      { type: 'info', confirmButtonText: '开始执行', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  autoProcessing.value = true
  autoProcessResult.value = null

  try {
    const res = await axios.post('/api/organize/auto_process', {
      torrent_name: torrentName,
      tmdb_id: tmdbId,
      qb_config_id: qbConfigId,
      category: selectedCategory.value || '',
    })
    autoProcessResult.value = res.data

    if (res.data?.success) {
      const stage = res.data.stage
      const title = res.data.details?.title || dirShow
      if (stage === 'waiting_for_delete_webhook') {
        ElMessage.info(`「${title}」已进入 Emby 确认阶段 — 等待自动移动 (task #${res.data.task_id})`)
      } else if (stage === 'completed') {
        ElMessage.success(`「${title}」${res.data.message}`)
      } else if (stage === 'no_action_needed') {
        ElMessage.info(`「${title}」${res.data.message}`)
      }
    } else {
      ElMessage.error(res.data?.message || '自动化洗版失败')
    }
  } catch (e) {
    ElMessage.error(`自动化洗版失败: ${getErrMessage(e)}`)
  } finally {
    autoProcessing.value = false
  }
}

// --- 移动失败时的三段式选择对话框 ---
const moveFallbackVisible = ref(false)
const moveFallbackShowName = ref('')
const moveFallbackSourcePath = ref('')
const moveFallbackYear = ref('')

// 选项 A：新建目录并移动本季
const handleFallbackCreateAndMove = async () => {
  moveFallbackVisible.value = false
  const showName = moveFallbackShowName.value
  const year = moveFallbackYear.value
  const sourcePath = moveFallbackSourcePath.value
  const destPath = buildMediaDestPath(year, showName)

  // 1) 先创建目标目录
  cd2Moving.value = true
  try {
    const parentPath = cd2MediaRoot.value + year + '/'
    const mkRes = await axios.post('/api/cd2/mkdir', {
      parent_path: parentPath,
      folder_name: showName,
    })
    if (!mkRes.data?.success) {
      ElMessage.error(`创建目录失败: ${mkRes.data?.detail || '未知错误'}`)
      return
    }
    ElMessage.success(`已创建目录: ${showName}`)
  } catch (e) {
    ElMessage.error(`创建目录失败: ${getErrMessage(e)}`)
    return
  } finally {
    cd2Moving.value = false
  }

  // 2) 再移动单季
  const r = await doMoveToLeft([sourcePath], destPath)
  if (r.success) {
    ElMessage.success(`已移动至新建目录: ${showName}`)
    // 将左侧导航到新建的剧集目录，让用户直观看到刚移入的 Season 及 stats
    cd2MediaPath.value = destPath
    loadCD2Data()                                    // 即时加载 — 展现目录结构
    setTimeout(() => loadCD2Data(true), 2000)         // 2s 后静默重拉 — 获取 stats
  } else {
    ElMessage.error(`移动失败: ${r.error}`)
  }
}

// 选项 B：移动整个电视剧目录（逐季移动，不碰根目录）
const handleFallbackMoveEntire = async () => {
  moveFallbackVisible.value = false
  const showName = moveFallbackShowName.value
  const year = moveFallbackYear.value

  const showPath = cd2OrganizedRoot.value + year + '/' + showName + '/'
  const yearDestPath = buildMediaDestPath(year, null)

  cd2Moving.value = true
  try {
    const res = await axios.post('/api/cd2/move_show_seasons', {
      source_show_path: showPath,
      target_parent_path: yearDestPath,
      conflict_policy: 1,
    })
    const data = res.data
    ElMessage.success(`已移动整剧: ${showName}（${data.moved_seasons || 0} 个 Season）`)
    goBack('organized')
    // 左侧导航到刚移入的剧集目录，展示 Season 及 stats
    cd2MediaPath.value = data.target_show_path || buildMediaDestPath(year, showName)
    loadCD2Data()                                    // 即时加载 — 展现目录结构
    setTimeout(() => loadCD2Data(true), 2000)         // 2s 后静默重拉 — 获取 stats
  } catch (e) {
    const errMsg = getErrMessage(e, '移动失败')
    ElMessage.error(`移动整剧失败: ${errMsg}`)
  } finally {
    cd2Moving.value = false
  }
}

// 选项 C：取消
const handleFallbackCancel = () => {
  moveFallbackVisible.value = false
}

// 删除当前 CD2 媒体库目录
// ==================== CD2 右侧（已完结）目录删除 ====================

// 删除已完结侧单个 Season 文件夹
const handleDeleteOrganizedItem = async (file: CD2File) => {
  const key = file.fullPathName || file.name
  const confirmed = await confirmCd2Delete(1)
  if (!confirmed) return

  cd2Deleting.value = true
  try {
    await axios.delete('/api/cd2/delete', {
      data: { paths: [key] },
    })
    ElMessage.success(`已删除: ${file.name}`)
    loadCD2Data()
    setTimeout(() => loadCD2Data(true), 500)          // 0.5s 后静默刷新 — 应对缓存延迟
  } catch (e) {
    ElMessage.error(`删除失败: ${getErrMessage(e)}`)
  } finally {
    cd2Deleting.value = false
  }
}

// 删除已完结侧当前整个剧集目录
const handleDeleteCurrentOrganizedDirectory = async () => {
  const segments = cd2OrganizedPath.value.replace(/\/+$/, '').split('/').filter(s => s.length > 0)
  const dirName = segments[segments.length - 1] || '当前目录'

  try {
    await ElMessageBox.confirm(
      `确定要删除整个【${dirName}】及其内部所有文件吗？此操作不可恢复！`,
      '删除整个目录',
      {
        type: 'error',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
      }
    )
  } catch {
    return
  }

  cd2Deleting.value = true
  try {
    await axios.delete('/api/cd2/delete', {
      data: { paths: [cd2OrganizedPath.value] },
    })
    ElMessage.success(`已删除: ${dirName}`)
    goBack('organized')
    setTimeout(() => loadCD2Data(true), 500)          // 0.5s 后静默刷新 — 应对缓存延迟
  } catch (e) {
    ElMessage.error(`删除失败: ${getErrMessage(e)}`)
  } finally {
    cd2Deleting.value = false
  }
}

const handleDeleteCurrentCd2Directory = async () => {
  const segments = cd2MediaPath.value.replace(/\/+$/, '').split('/').filter(s => s.length > 0)
  const dirName = segments[segments.length - 1] || '当前目录'

  try {
    await ElMessageBox.confirm(
      `确定要删除整个【${dirName}】及其内部所有文件吗？此操作不可恢复！`,
      '删除整个目录',
      {
        type: 'error',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
      }
    )
  } catch {
    return
  }

  cd2Deleting.value = true
  selectedCd2MediaItems.value = []
  try {
    await axios.delete('/api/cd2/delete', {
      data: { paths: [cd2MediaPath.value] },
    })
    ElMessage.success(`已删除: ${dirName}`)
    goBack('media')
    setTimeout(() => loadCD2Data(true), 500)          // 0.5s 后静默刷新 — 应对缓存延迟
  } catch (e) {
    ElMessage.error(`删除失败: ${getErrMessage(e)}`)
  } finally {
    cd2Deleting.value = false
  }
}

// ==================== 整理工作台 ====================
const orgLoading = ref(false)
const orgResult = ref<OrgResult | null>(null)       // { title, year, season, total_episodes, tmdb_id, resolved_category, ... }
const orgError = ref('')
const orgCD2Status = ref('')      // CD2 双端跳转结果

// Helper: search for matching show folder in a directory listing
const findShowFolder = (files: CD2File[] | null, tmdbId?: number, title?: string): CD2File | null => {
  if (!files || !files.length) return null
  const tmdbTag = tmdbId ? `{tmdb=${tmdbId}}` : ''
  if (tmdbTag) {
    const found = files.find(f => f.isDirectory && f.name.includes(tmdbTag))
    if (found) return found
  }
  if (title) {
    const found = files.find(f => f.isDirectory && f.name.includes(title))
    if (found) return found
  }
  return null
}

// Helper: try to jump one CD2 side to the show folder
const jumpCD2Side = async (side: 'media' | 'organized', yearPath: string): Promise<JumpResult> => {
  try {
    const res = await axios.get('/api/cd2/directories', {
      params: {
        media_dir: side === 'media' ? yearPath : cd2MediaPath.value,
        organized_dir: side === 'organized' ? yearPath : cd2OrganizedPath.value,
      },
    })
    const files = side === 'media' ? (res.data.media || []) : (res.data.organized || [])
    const matched = findShowFolder(files, orgResult.value?.tmdb_id, orgResult.value?.title)
    if (matched) {
      if (side === 'media') {
        cd2MediaPath.value = yearPath + matched.name + '/'
      } else {
        cd2OrganizedPath.value = yearPath + matched.name + '/'
      }
      return { ok: true, name: matched.name }
    }
    return { ok: false, name: null }
  } catch (e) {
    return { ok: false, error: getErrMessage(e) }
  }
}

const startOrganize = async (torrent: QbTorrent) => {
  orgLoading.value = true
  orgResult.value = null
  orgError.value = ''
  orgCD2Status.value = ''

  try {
    // 1 — Call analyze API
    const res = await axios.post('/api/organize/analyze', {
      torrent_name: torrent.name,
    })
    orgResult.value = res.data

    // 2 — Set search query to filter left column
    searchQuery.value = res.data.title || ''

    // 3 — Auto-switch category if resolved
    const category = res.data.resolved_category
    if (category && selectedCategory.value !== category) {
      selectedCategory.value = category
      await nextTick()  // Wait for Vue watchers to update CD2 roots
    }

    // 4 — Dual CD2 jump (both sides)
    const year = res.data.year
    const cat = selectedCategory.value || category || ''

    if (!year || !cat) {
      orgCD2Status.value = (year ? '未识别分类' : '未识别年份') + '，无法自动跳转 CD2'
      return
    }

    // Construct year-level paths for both sides
    const mediaYearPath = CD2_MEDIA_BASE.value + cat + '/' + year + '/'
    const organizedYearPath = CD2_ORGANIZED_BASE.value + cat + '/' + year + '/'

    // Jump both sides in parallel
    const [mediaResult, organizedResult] = await Promise.all([
      jumpCD2Side('media', mediaYearPath),
      jumpCD2Side('organized', organizedYearPath),
    ])

    // Reload CD2 data with updated paths
    loadCD2Data()

    // Build status message
    const parts = []
    if (cat) parts.push(`分类: ${cat}`)
    parts.push(
      `媒体库 ${mediaResult.ok ? '✓' : '✗'}` +
      (mediaResult.name ? ` (${mediaResult.name})` : '')
    )
    parts.push(
      `已完结 ${organizedResult.ok ? '✓' : '✗'}` +
      (organizedResult.name ? ` (${organizedResult.name})` : '')
    )
    orgCD2Status.value = parts.join(' | ')
  } catch (e) {
    orgError.value = getErrMessage(e, '解析失败')
    ElMessage.error(orgError.value)
  } finally {
    orgLoading.value = false
  }
}

const clearOrganize = () => {
  orgResult.value = null
  orgError.value = ''
  orgCD2Status.value = ''
  searchQuery.value = ''
}

// --- 初始化 ---
onMounted(async () => {
  // 并行加载：配置、分类、实例
  await Promise.all([
    // 从配置 API 加载 CD2 根路径
    (async () => {
      try {
        const configRes = await axios.get('/api/config')
        if (configRes.data?.cd2_media_dir) {
          CD2_MEDIA_BASE.value = configRes.data.cd2_media_dir
        }
        if (configRes.data?.cd2_organized_dir) {
          CD2_ORGANIZED_BASE.value = configRes.data.cd2_organized_dir
        }
      } catch (e) {
        console.warn('[CD2] 加载配置失败，使用默认路径', e)
      }
    })(),
    // 从后端 category.yaml 加载预设分类
    (async () => {
      try {
        const catRes = await axios.get('/api/categories')
        if (catRes.data?.all?.length) {
          presetCategories.value = catRes.data.all
          // 如果当前 cd2Category 不在预设列表中，使用第一个
          if (!presetCategories.value.includes(cd2Category.value)) {
            cd2Category.value = presetCategories.value[0]
          }
        }
      } catch (e) {
        console.warn('[CD2] 加载分类列表失败', e)
      }
    })(),
  ])
  fetchInstances()
  cd2MediaPath.value = cd2MediaRoot.value
  cd2OrganizedPath.value = cd2OrganizedRoot.value
  loadCD2Data()
  window.addEventListener('resize', onWindowResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', onWindowResize)
})
</script>

<template>
  <div class="cleanup-container">
    <!-- ==================== 整理工作台 ==================== -->
    <div v-if="orgLoading || orgResult || orgError" class="organize-workbench">
      <!-- Loading -->
      <div v-if="orgLoading" class="ow-loading">
        <el-icon :size="18" class="is-loading" color="var(--accent-blue)"><Loading /></el-icon>
        <span>正在解析种子名称...</span>
      </div>

      <!-- Result -->
      <template v-else-if="orgResult">
        <div class="ow-result">
          <div class="ow-info">
            <span class="ow-label">整理目标</span>
            <span class="ow-title">《{{ orgResult.title }}》</span>
            <span v-if="orgResult.tmdb_name && orgResult.tmdb_name !== orgResult.title" class="ow-tmdb-name">{{ orgResult.tmdb_name }}</span>
            <template v-if="orgResult.year">
              <span class="ow-sep">·</span>
              <span class="ow-year">{{ orgResult.year }}</span>
            </template>
            <span class="ow-sep">·</span>
            <span class="ow-season">第 {{ orgResult.season }} 季</span>
            <template v-if="orgResult.total_episodes">
              <span class="ow-sep">·</span>
              <span class="ow-eps">共 {{ orgResult.total_episodes }} 集</span>
            </template>
            <span v-if="orgResult.tmdb_id" class="ow-tmdb">TMDB: {{ orgResult.tmdb_id }}</span>
            <span v-if="orgResult.resolved_category" class="ow-cat-tag">{{ orgResult.resolved_category }}</span>
          </div>
          <div class="ow-actions">
            <span v-if="orgCD2Status" class="ow-cd2-status" :class="{ 'ow-cd2-ok': orgCD2Status.includes('✓'), 'ow-cd2-warn': orgCD2Status.includes('✗') }">
              {{ orgCD2Status }}
            </span>
            <button class="ow-clear-btn" @click="clearOrganize">✕ 清除</button>
          </div>
        </div>
      </template>

      <!-- Error -->
      <div v-else-if="orgError" class="ow-error">
        <span>⚠️ {{ orgError }}</span>
        <button class="ow-clear-btn" @click="clearOrganize">✕</button>
      </div>
    </div>

    <!-- ==================== Header — 全局联动筛选 ==================== -->
    <div class="cleanup-header">
      <!-- 全局搜索框 -->
      <div class="header-search">
        <el-icon :size="18" class="header-search-icon"><Search /></el-icon>
        <input
          v-model="searchQuery"
          type="text"
          placeholder="输入种子名称关键词，同时过滤两列..."
          class="header-search-input"
        />
        <el-icon
          v-if="searchQuery"
          :size="16"
          class="header-search-clear"
          @click="searchQuery = ''"
        ><Close /></el-icon>
      </div>

      <!-- 分类筛选 -->
      <div class="header-category">
        <el-icon :size="15" class="category-icon"><Filter /></el-icon>
        <el-select
          v-model="selectedCategory"
          placeholder="全部分类"
          class="category-select"
          clearable
          teleported="false"
        >
          <el-option
            v-for="opt in categoryOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </div>

      <!-- 预留按钮 -->
      <button class="btn-auto-scan" disabled title="自动扫描功能开发中">
        🤖 自动扫描可清理项 (开发中)
      </button>
    </div>

    <!-- ==================== 双列比对区 ==================== -->
    <div class="split-view">
      <!-- ===== 左列 — 待清理实例 ===== -->
      <div class="split-col">
        <div class="col-header">
          <div class="col-header-left">
            <span class="col-dot dot-danger animate-pulse"></span>
            <span class="col-label">待清理实例</span>
            <span v-if="leftInstanceId && totalLeft > 0" class="col-count">
              {{ totalLeft }} 个匹配
            </span>
          </div>
          <el-select
            v-model="leftInstanceId"
            placeholder="选择单集实例 (通常为 8089 端口)"
            class="col-select"
            :loading="instanceLoading"
          >
            <el-option
              v-for="inst in leftInstanceOptions"
              :key="inst.id"
              :label="inst.name"
              :value="inst.id"
              :disabled="inst.disabled"
            />
          </el-select>
        </div>

        <div class="col-body">
          <!-- 未选择实例 -->
          <div v-if="!leftInstanceId" class="col-empty">
            <div class="empty-icon-circle">
              <el-icon :size="32"><Monitor /></el-icon>
            </div>
            <p class="empty-title">未选择实例</p>
            <p class="empty-desc">请在上方选择一个待清理的 qBittorrent 实例</p>
          </div>

          <!-- 加载中 -->
          <div v-else-if="leftLoading" class="col-empty">
            <el-icon :size="32" class="is-loading" color="var(--accent-blue)"><Loading /></el-icon>
            <p class="empty-title" style="margin-top:12px">加载中...</p>
          </div>

          <!-- 无匹配结果 -->
          <div v-else-if="!paginatedLeftList.length" class="col-empty">
            <div class="empty-icon-circle">
              <el-icon :size="32"><Search /></el-icon>
            </div>
            <p class="empty-title">无匹配种子</p>
            <p class="empty-desc">
              {{ searchQuery || selectedCategory ? '当前筛选条件无匹配结果' : '该实例暂无种子数据' }}
            </p>
          </div>

          <!-- 卡片列表 -->
          <template v-else>
            <!-- 批量操作栏 -->
            <div class="batch-bar">
              <el-checkbox
                :model-value="isAllSelected"
                :indeterminate="selectedLeftTorrents.length > 0 && !isAllSelected"
                @change="handleSelectAll"
              >
                全选当前页 ({{ paginatedLeftList.length }})
              </el-checkbox>
              <button
                class="batch-delete-btn"
                :disabled="!selectedLeftTorrents.length || batchDeleting"
                @click="handleBatchDelete"
              >
                <el-icon v-if="batchDeleting" :size="15" class="is-loading"><Loading /></el-icon>
                <el-icon v-else :size="15"><Delete /></el-icon>
                批量删种及文件
                <template v-if="selectedLeftTorrents.length">
                  ({{ selectedLeftTorrents.length }})
                </template>
              </button>
            </div>

            <div class="card-list">
              <div
                v-for="torrent in paginatedLeftList"
                :key="torrent.hash"
                class="compact-card card-left"
                :class="{ 'is-deleting': deletingHash === torrent.hash, 'is-checked': selectedLeftTorrents.includes(torrent.hash) }"
              >
                <el-checkbox
                  :model-value="selectedLeftTorrents.includes(torrent.hash)"
                  class="cc-checkbox"
                  @change="handleCheckOne(torrent.hash)"
                />
                <div class="cc-body">
                  <div class="cc-name">{{ torrent.name }}</div>
                  <div class="cc-meta-row">
                    <span class="cc-size">{{ formatBytes(torrent.size) }}</span>
                    <span class="cc-sep">·</span>
                    <span class="cc-state" :style="{ color: getStateColor(torrent.state) }">
                      {{ formatState(torrent.state) }}
                    </span>
                    <template v-if="torrent.category">
                      <span class="cc-sep">·</span>
                      <span class="cc-category">{{ torrent.category }}</span>
                    </template>
                  </div>
                  <div class="cc-progress">
                    <div class="cc-progress-track">
                      <div
                        class="cc-progress-fill bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.8)]"
                        :style="{ width: (torrent.progress * 100) + '%' }"
                      ></div>
                    </div>
                    <span class="cc-progress-num">{{ Math.round(torrent.progress * 100) }}%</span>
                  </div>
                </div>
                <button
                  class="cc-delete-btn"
                  :disabled="deletingHash === torrent.hash"
                  @click.stop="deleteTorrent(torrent)"
                  title="删除种子及文件"
                >
                  <el-icon v-if="deletingHash === torrent.hash" :size="16" class="is-loading"><Loading /></el-icon>
                  <el-icon v-else :size="17"><Delete /></el-icon>
                </button>
              </div>
            </div>

            <!-- 左列独立分页 -->
            <div v-if="totalLeft > pageSizeLeft" class="col-pagination">
              <el-pagination
                v-model:current-page="currentPageLeft"
                v-model:page-size="pageSizeLeft"
                :page-sizes="[10, 20, 50, 100]"
                :total="totalLeft"
                layout="total, sizes, prev, pager, next"
                small
                background
              />
            </div>
          </template>
        </div>
      </div>

      <!-- ===== 右列 — 全集归档实例 ===== -->
      <div class="split-col">
        <div class="col-header">
          <div class="col-header-left">
            <span class="col-dot dot-success animate-pulse"></span>
            <span class="col-label">全集归档实例</span>
            <span v-if="rightInstanceId && totalRight > 0" class="col-count">
              {{ totalRight }} 个匹配
            </span>
          </div>
          <el-select
            v-model="rightInstanceId"
            placeholder="选择全集归档实例"
            class="col-select"
            :loading="instanceLoading"
          >
            <el-option
              v-for="inst in rightInstanceOptions"
              :key="inst.id"
              :label="inst.name"
              :value="inst.id"
              :disabled="inst.disabled"
            />
          </el-select>
        </div>

        <div class="col-body">
          <!-- 未选择实例 -->
          <div v-if="!rightInstanceId" class="col-empty">
            <div class="empty-icon-circle">
              <el-icon :size="32"><Monitor /></el-icon>
            </div>
            <p class="empty-title">未选择实例</p>
            <p class="empty-desc">请在上方选择一个全集归档的 qBittorrent 实例</p>
          </div>

          <!-- 加载中 -->
          <div v-else-if="rightLoading" class="col-empty">
            <el-icon :size="32" class="is-loading" color="var(--accent-blue)"><Loading /></el-icon>
            <p class="empty-title" style="margin-top:12px">加载中...</p>
          </div>

          <!-- 无匹配结果 -->
          <div v-else-if="!paginatedRightList.length" class="col-empty">
            <div class="empty-icon-circle">
              <el-icon :size="32"><Search /></el-icon>
            </div>
            <p class="empty-title">无匹配种子</p>
            <p class="empty-desc">
              {{ searchQuery || selectedCategory ? '当前筛选条件无匹配结果' : '该实例暂无种子数据' }}
            </p>
          </div>

          <!-- 卡片列表 -->
          <template v-else>
            <!-- 右列批量操作栏 -->
            <div class="batch-bar">
              <el-checkbox
                :model-value="isAllSelectedRight"
                :indeterminate="selectedRightTorrents.length > 0 && !isAllSelectedRight"
                @change="handleSelectAllRight"
              >
                全选当前页 ({{ paginatedRightList.length }})
              </el-checkbox>
              <div class="cd2-batch-actions">
                <button
                  class="batch-organize-btn"
                  :disabled="!selectedRightTorrents.length || batchOrganizing || orgLoading"
                  @click="handleBatchOrganize"
                >
                  <el-icon v-if="batchOrganizing" :size="15" class="is-loading"><Loading /></el-icon>
                  <el-icon v-else :size="15"><Setting /></el-icon>
                  批量整理
                  <template v-if="selectedRightTorrents.length">
                    ({{ selectedRightTorrents.length }})
                  </template>
                </button>
                <button
                  class="batch-delete-btn"
                  :disabled="!selectedRightTorrents.length || rightBatchDeleting"
                  @click="handleBatchDeleteRight"
                >
                  <el-icon v-if="rightBatchDeleting" :size="15" class="is-loading"><Loading /></el-icon>
                  <el-icon v-else :size="15"><Delete /></el-icon>
                  批量删种及文件
                  <template v-if="selectedRightTorrents.length">
                    ({{ selectedRightTorrents.length }})
                  </template>
                </button>
              </div>
            </div>

            <div class="card-list">
              <div
                v-for="torrent in paginatedRightList"
                :key="torrent.hash"
                class="compact-card card-right"
                :class="{ 'is-checked': selectedRightTorrents.includes(torrent.hash) }"
              >
                <el-checkbox
                  :model-value="selectedRightTorrents.includes(torrent.hash)"
                  class="cc-checkbox"
                  @change="handleCheckOneRight(torrent.hash)"
                />
                <div class="cc-body">
                  <div class="cc-name">{{ torrent.name }}</div>
                  <div class="cc-meta-row">
                    <span class="cc-size">{{ formatBytes(torrent.size) }}</span>
                    <span class="cc-sep">·</span>
                    <span class="cc-state" :style="{ color: getStateColor(torrent.state) }">
                      {{ formatState(torrent.state) }}
                    </span>
                    <template v-if="torrent.category">
                      <span class="cc-sep">·</span>
                      <span class="cc-category">{{ torrent.category }}</span>
                    </template>
                  </div>
                  <div class="cc-progress">
                    <div class="cc-progress-track">
                      <div
                        class="cc-progress-fill bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.8)]"
                        :style="{ width: (torrent.progress * 100) + '%' }"
                      ></div>
                    </div>
                    <span class="cc-progress-num">{{ Math.round(torrent.progress * 100) }}%</span>
                  </div>
                </div>
                <button
                  class="cc-organize-btn"
                  :disabled="orgLoading"
                  @click.stop="startOrganize(torrent)"
                  title="整理此剧集"
                >
                  <el-icon :size="14"><Setting /></el-icon>
                  <span class="cc-org-text">整理</span>
                </button>
                <button
                  class="cc-delete-btn"
                  :disabled="rightDeletingHash === torrent.hash"
                  @click.stop="handleDeleteRightTorrent(torrent)"
                  title="删除种子及文件"
                >
                  <el-icon v-if="rightDeletingHash === torrent.hash" :size="16" class="is-loading"><Loading /></el-icon>
                  <el-icon v-else :size="17"><Delete /></el-icon>
                </button>
                <span v-if="torrent.progress >= 1" class="cc-archived-badge">
                  <el-icon :size="14"><CircleCheck /></el-icon> 已归档
                </span>
              </div>
            </div>

            <!-- 右列独立分页 -->
            <div v-if="totalRight > pageSizeRight" class="col-pagination">
              <el-pagination
                v-model:current-page="currentPageRight"
                v-model:page-size="pageSizeRight"
                :page-sizes="[10, 20, 50, 100]"
                :total="totalRight"
                layout="total, sizes, prev, pager, next"
                small
                background
              />
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- ==================== CD2 区域拖拽手柄 ==================== -->
    <div
      class="cd2-resize-handle"
      @mousedown="onDragStart"
    >
      <div class="cd2-resize-handle-bar"></div>
    </div>

    <!-- ==================== CD2 网盘目录浏览 (导航版) ==================== -->
    <div class="cd2-section" :class="{ 'cd2-collapsed': isMobile && !showCD2 }" :style="cd2SectionHeight && !isMobile ? { height: cd2SectionHeight + 'px', flexShrink: '0', flex: 'none' } : {}">
      <!-- Section Header -->
      <div class="cd2-section-header">
        <div class="cd2-section-title">
          <span class="col-dot dot-cd2 animate-pulse"></span>
          <span>CD2 网盘文件概览</span>
          <span class="cd2-cat-sep">›</span>
          <el-select
            v-model="cd2Category"
            class="cd2-cat-select"
            size="small"
            :teleported="false"
          >
            <el-option
              v-for="cat in presetCategories"
              :key="cat"
              :label="cat"
              :value="cat"
            />
          </el-select>
          <span v-if="!cd2Loading && !cd2Error" class="col-count">
            {{ cd2MediaFiles.length + cd2OrganizedFiles.length }} 项
          </span>
        </div>
        <div class="cd2-header-actions">
          <button
            v-if="isMobile"
            class="cd2-toggle-btn"
            @click="showCD2 = !showCD2"
          >
            {{ showCD2 ? '收起 ▲' : '展开 ▼' }}
          </button>
          <button class="cd2-refresh-btn" :disabled="cd2Loading" @click="loadCD2Data()">
            <el-icon :size="14" :class="{ 'is-loading': cd2Loading }"><Loading /></el-icon>
            {{ cd2Loading ? '加载中...' : '刷新' }}
          </button>
        </div>
      </div>

      <!-- 移动端：内容可折叠 -->
      <template v-if="!isMobile || showCD2">
        <!-- 加载中 -->
        <div v-if="cd2Loading" class="cd2-loading">
          <el-icon :size="24" class="is-loading" color="var(--accent-blue)"><Loading /></el-icon>
          <span>正在连接 CloudDrive2 ...</span>
        </div>

        <!-- 错误 -->
        <div v-else-if="cd2Error" class="cd2-error">
          <span class="cd2-error-icon">⚠️</span>
          <span>{{ cd2Error }}</span>
        </div>

        <!-- 数据内容：双列 -->
        <div v-else class="cd2-split">
        <!-- ===== 左列：媒体库（待整理） ===== -->
        <div class="cd2-col">
          <div class="cd2-col-header">
            <span class="cd2-col-icon">📂</span>
            <span class="cd2-col-label">媒体库（待整理）</span>
            <!-- 年份快捷跳转 -->
            <div class="cd2-year-jump" v-if="isAtMediaRoot">
              <input
                v-model="cd2MediaYearInput"
                type="text"
                inputmode="numeric"
                pattern="[0-9]*"
                placeholder="年份..."
                class="cd2-year-input"
                @keyup.enter="jumpToYear"
                @keydown.escape="cd2MediaYearInput = ''"
              />
              <button class="cd2-year-btn" :disabled="!String(cd2MediaYearInput).trim()" @click="jumpToYear">跳转</button>
            </div>
            <span class="cd2-col-badge media-badge">{{ cd2MediaFiles.length }} 项</span>
          </div>
          <!-- 面包屑 + 返回按钮 -->
          <div class="cd2-nav-bar">
            <button
              class="cd2-back-btn"
              :class="{ invisible: !cd2MediaCanGoBack }"
              :disabled="!cd2MediaCanGoBack"
              @click="goBack('media')"
              title="返回上一级"
            >
              <span class="cd2-back-arrow">←</span> 上一级
            </button>
            <div class="cd2-crumb" :title="cd2MediaPath">
              <span class="cd2-crumb-root">📂</span>
              <span v-if="cd2MediaRelative" class="cd2-crumb-rel">/{{ cd2MediaRelative }}</span>
              <span v-else class="cd2-crumb-rel is-root">/ (根目录)</span>
            </div>
            <!-- 残缺季核查（仅年份层级 depth=1） -->
            <button
              v-if="cd2MediaDepth === 1"
              class="cd2-season-check-btn"
              :disabled="seasonCheckLoading || cd2Loading"
              @click="runSeasonCheck('media')"
              title="扫描当前年份目录下所有剧集，找出集数残缺的 Season 文件夹"
            >
              <el-icon v-if="seasonCheckLoading" :size="13" class="is-loading"><Loading /></el-icon>
              <span v-else class="cd2-identify-icon">🔍</span>
              {{ seasonCheckLoading ? '核查中...' : '核查残缺季' }}
            </button>
            <button
              v-if="checkedShowCount > 0 && cd2MediaDepth >= 1"
              class="cd2-clear-check-btn"
              @click="clearSeasonCheck"
              title="清除核查标注"
            >
              ✕ 清除标注
            </button>
            <button
              v-if="cd2MediaDepth >= 2"
              class="cd2-identify-btn"
              :disabled="cd2Identifying || orgLoading"
              @click="identifyCD2Directory('media')"
              title="识别当前剧集（TMDB、集数等）"
            >
              <el-icon v-if="cd2Identifying" :size="13" class="is-loading"><Loading /></el-icon>
              <span v-else class="cd2-identify-icon">🔍</span>
              识别
            </button>
            <button
              v-if="cd2MediaDepth >= 2"
              class="cd2-delete-dir-btn"
              :disabled="cd2Deleting"
              @click="handleDeleteCurrentCd2Directory"
              title="删除当前目录"
            >
              <el-icon v-if="cd2Deleting" :size="13" class="is-loading"><Loading /></el-icon>
              <el-icon v-else :size="13"><Delete /></el-icon>
              删除目录
            </button>
          </div>
          <div class="cd2-col-body">
            <!-- 根目录截断提示 -->
            <div v-if="isAtMediaRoot && cd2MediaFiles.length > 5" class="cd2-truncate-hint">
              显示最近 5 个年份（共 {{ cd2MediaFiles.length }} 个），输入年份可跳转
            </div>
            <div v-if="!cd2MediaFiles.length" class="col-empty">
              <p class="empty-title">暂无文件</p>
              <p class="empty-desc">该目录下没有内容</p>
            </div>
            <template v-else>
              <!-- 批量删除操作栏 -->
              <div class="cd2-batch-bar">
                <el-checkbox
                  :model-value="isAllCd2MediaSelected"
                  :indeterminate="selectedCd2MediaItems.length > 0 && !isAllCd2MediaSelected"
                  @change="handleSelectAllCd2Media"
                >
                  全选 ({{ displayedMediaFiles.length }})
                </el-checkbox>
                <div class="cd2-batch-actions">
                  <button
                    class="cd2-batch-delete-btn"
                    :disabled="!selectedCd2MediaItems.length || cd2Deleting"
                    @click="handleBatchDeleteCd2Items"
                  >
                    <el-icon v-if="cd2Deleting" :size="14" class="is-loading"><Loading /></el-icon>
                    <el-icon v-else :size="14"><Delete /></el-icon>
                    批量删除
                    <template v-if="selectedCd2MediaItems.length">
                      ({{ selectedCd2MediaItems.length }})
                    </template>
                  </button>
                </div>
              </div>

              <div class="cd2-file-list">
                <div
                  v-for="file in displayedMediaFiles"
                  :key="file.fullPathName || file.name"
                  class="cd2-file-item"
                  :class="{
                    'is-dir': file.isDirectory,
                    'is-clickable': file.isDirectory,
                    'is-checked': selectedCd2MediaItems.includes(file.fullPathName || file.name),
                    'sc-show-incomplete': file.isDirectory && getShowCheckStatus(file)?.status === 'incomplete',
                    'sc-show-complete': file.isDirectory && getShowCheckStatus(file)?.status === 'complete',
                  }"
                >
                  <el-checkbox
                    :model-value="selectedCd2MediaItems.includes(file.fullPathName || file.name)"
                    class="cd2-item-checkbox"
                    @change="handleCheckOneCd2Media(file.fullPathName || file.name)"
                    @click.stop
                  />
                  <span
                    class="cd2-file-icon cd2-file-clickable"
                    @click="file.isDirectory && enterFolder('media', file.name)"
                  >{{ file.isDirectory ? '📁' : '📄' }}</span>
                  <span
                    class="cd2-file-name cd2-file-clickable"
                    :class="{ 'is-folder': file.isDirectory }"
                    @click="file.isDirectory && enterFolder('media', file.name)"
                  >{{ file.name }}</span>
                  <!-- 残缺季标注：剧集级别（depth=1 年份层级） -->
                  <span
                    v-if="file.isDirectory && cd2MediaDepth === 1 && getShowCheckStatus(file)"
                    class="sc-inline-badge"
                    :class="getShowCheckStatus(file)!.status === 'incomplete' ? 'sc-badge-warn' : 'sc-badge-ok'"
                  >
                    {{ getShowCheckStatus(file)!.status === 'incomplete' ? `⚠️ 缺 ${getShowCheckStatus(file)!.incompleteCount} 季` : '✅ 完整' }}
                  </span>
                  <!-- 单剧核查按钮（depth=1 剧集层级） -->
                  <button
                    v-if="file.isDirectory && cd2MediaDepth === 1 && !isYearFolder(file.name)"
                    class="cd2-check-one-btn"
                    :disabled="isShowChecking('media', file) || cd2Loading"
                    @click.stop="runSingleShowCheck('media', file)"
                    :title="`核查「${file.name}」的 Season 完整性`"
                  >
                    <el-icon v-if="isShowChecking('media', file)" :size="11" class="is-loading"><Loading /></el-icon>
                    <span v-else>🔍</span>
                  </button>
                  <!-- 残缺季标注：Season 级别（depth=2 剧集内部） -->
                  <span
                    v-if="file.isDirectory && cd2MediaDepth >= 2 && getSeasonCheckDetail(file, 'media')"
                    class="sc-inline-badge sc-badge-season"
                  >
                    <span class="sc-season-nums">
                      <span class="sc-season-actual">{{ getSeasonCheckDetail(file, 'media')!.actual }}</span>
                      <span class="sc-season-sep">/</span>
                      <span class="sc-season-expected">{{ getSeasonCheckDetail(file, 'media')!.expected }}</span>
                    </span>
                    <span class="sc-season-missing">缺 {{ getSeasonCheckDetail(file, 'media')!.expected - getSeasonCheckDetail(file, 'media')!.actual }} 集</span>
                  </span>
                  <!-- 目录：显示文件数和大小（年份文件夹强制屏蔽，有标注时隐藏原始 stats） -->
                  <span v-if="file.isDirectory && file.fileCount != null && !isYearFolder(file.name) && !getSeasonCheckDetail(file, 'media')" class="cd2-dir-stats">
                    {{ file.fileCount }} 文件
                    <template v-if="file.totalSize"> · {{ formatBytes(file.totalSize) }}</template>
                  </span>
                  <span v-else-if="!file.isDirectory" class="cd2-file-size">{{ formatBytes(file.size) }}</span>
                  <span v-if="file.isDirectory" class="cd2-file-arrow cd2-file-clickable" @click="enterFolder('media', file.name)">›</span>
                  <!-- 删除按钮 -->
                  <button
                    class="cd2-item-delete-btn"
                    :disabled="cd2Deleting"
                    @click.stop="handleDeleteSingleCd2Item(file)"
                    title="删除此项目"
                  >
                    <el-icon :size="14"><Delete /></el-icon>
                  </button>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- ===== 右列：已完结（已整理） ===== -->
        <div class="cd2-col">
          <div class="cd2-col-header">
            <span class="cd2-col-icon">✅</span>
            <span class="cd2-col-label">已完结（已整理）</span>
            <span class="cd2-col-badge organized-badge">{{ cd2OrganizedFiles.length }} 项</span>
          </div>
          <!-- 面包屑 + 返回按钮 -->
          <div class="cd2-nav-bar">
            <button
              class="cd2-back-btn"
              :class="{ invisible: !cd2OrganizedCanGoBack }"
              :disabled="!cd2OrganizedCanGoBack"
              @click="goBack('organized')"
              title="返回上一级"
            >
              <span class="cd2-back-arrow">←</span> 上一级
            </button>
            <div class="cd2-crumb" :title="cd2OrganizedPath">
              <span class="cd2-crumb-root">✅</span>
              <span v-if="cd2OrganizedRelative" class="cd2-crumb-rel">/{{ cd2OrganizedRelative }}</span>
              <span v-else class="cd2-crumb-rel is-root">/ (根目录)</span>
            </div>
            <!-- 残缺季核查（仅年份层级 depth=1） -->
            <button
              v-if="cd2OrganizedDepth === 1"
              class="cd2-season-check-btn"
              :disabled="seasonCheckLoading || cd2Loading"
              @click="runSeasonCheck('organized')"
              title="扫描当前年份目录下所有剧集，找出集数残缺的 Season 文件夹"
            >
              <el-icon v-if="seasonCheckLoading" :size="13" class="is-loading"><Loading /></el-icon>
              <span v-else class="cd2-identify-icon">🔍</span>
              {{ seasonCheckLoading ? '核查中...' : '核查残缺季' }}
            </button>
            <button
              v-if="checkedShowCount > 0 && cd2OrganizedDepth >= 1"
              class="cd2-clear-check-btn"
              @click="clearSeasonCheck"
              title="清除核查标注"
            >
              ✕ 清除标注
            </button>
            <button
              v-if="cd2OrganizedDepth >= 2"
              class="cd2-identify-btn"
              :disabled="cd2Identifying || orgLoading"
              @click="identifyCD2Directory('organized')"
              title="识别当前剧集（TMDB、集数等）"
            >
              <el-icon v-if="cd2Identifying" :size="13" class="is-loading"><Loading /></el-icon>
              <span v-else class="cd2-identify-icon">🔍</span>
              识别
            </button>
            <button
              v-if="cd2OrganizedDepth >= 2"
              class="cd2-auto-process-btn"
              :disabled="autoProcessing || cd2Moving"
              @click="handleAutoProcessFromCD2"
              title="对该剧集执行全自动洗版（完结校验 + 智能对比 + 去重）"
            >
              <el-icon v-if="autoProcessing" :size="13" class="is-loading"><Loading /></el-icon>
              <el-icon v-else :size="13"><CircleCheck /></el-icon>
              执行自动化洗版
            </button>
            <button
              v-if="cd2OrganizedDepth >= 2"
              class="cd2-delete-dir-btn"
              :disabled="cd2Deleting"
              @click="handleDeleteCurrentOrganizedDirectory"
              title="删除当前剧集目录"
            >
              <el-icon v-if="cd2Deleting" :size="13" class="is-loading"><Loading /></el-icon>
              <el-icon v-else :size="13"><Delete /></el-icon>
              删除目录
            </button>
            <button
              v-if="cd2OrganizedDepth >= 2"
              class="cd2-move-dir-btn"
              :disabled="cd2Moving"
              @click="handleMoveEntireShowToLeft"
              title="移动整剧至左侧媒体库"
            >
              <el-icon v-if="cd2Moving" :size="13" class="is-loading"><Loading /></el-icon>
              <el-icon v-else :size="13"><Right /></el-icon>
              移动整剧
            </button>
          </div>
          <div class="cd2-col-body">
            <!-- 根目录截断提示 -->
            <div v-if="isAtOrganizedRoot && cd2OrganizedFiles.length > 5" class="cd2-truncate-hint">
              显示最近 5 项（共 {{ cd2OrganizedFiles.length }} 项）
            </div>
            <div v-if="!cd2OrganizedFiles.length" class="col-empty">
              <p class="empty-title">暂无文件</p>
              <p class="empty-desc">该目录下没有内容</p>
            </div>
            <div v-else class="cd2-file-list">
              <div
                v-for="file in displayedOrganizedFiles"
                :key="file.fullPathName || file.name"
                class="cd2-file-item"
                :class="{
                  'is-dir': file.isDirectory,
                  'is-clickable': file.isDirectory,
                  'sc-show-incomplete': file.isDirectory && getOrganizedShowCheckStatus(file)?.status === 'incomplete',
                  'sc-show-complete': file.isDirectory && getOrganizedShowCheckStatus(file)?.status === 'complete',
                }"
                @click="file.isDirectory && enterFolder('organized', file.name)"
              >
                <span class="cd2-file-icon">{{ file.isDirectory ? '📁' : '📄' }}</span>
                <span class="cd2-file-name">{{ file.name }}</span>
                <!-- 残缺季标注：剧集级别 -->
                <span
                  v-if="file.isDirectory && cd2OrganizedDepth === 1 && getOrganizedShowCheckStatus(file)"
                  class="sc-inline-badge"
                  :class="getOrganizedShowCheckStatus(file)!.status === 'incomplete' ? 'sc-badge-warn' : 'sc-badge-ok'"
                >
                  {{ getOrganizedShowCheckStatus(file)!.status === 'incomplete' ? `⚠️ 缺 ${getOrganizedShowCheckStatus(file)!.incompleteCount} 季` : '✅ 完整' }}
                </span>
                <!-- 单剧核查按钮（depth=1 剧集层级） -->
                <button
                  v-if="file.isDirectory && cd2OrganizedDepth === 1 && !isYearFolder(file.name)"
                  class="cd2-check-one-btn"
                  :disabled="isShowChecking('organized', file) || cd2Loading"
                  @click.stop="runSingleShowCheck('organized', file)"
                  :title="`核查「${file.name}」的 Season 完整性`"
                >
                  <el-icon v-if="isShowChecking('organized', file)" :size="11" class="is-loading"><Loading /></el-icon>
                  <span v-else>🔍</span>
                </button>
                <!-- 残缺季标注：Season 级别 -->
                <span
                  v-if="file.isDirectory && cd2OrganizedDepth >= 2 && getSeasonCheckDetail(file, 'organized')"
                  class="sc-inline-badge sc-badge-season"
                >
                  <span class="sc-season-nums">
                    <span class="sc-season-actual">{{ getSeasonCheckDetail(file, 'organized')!.actual }}</span>
                    <span class="sc-season-sep">/</span>
                    <span class="sc-season-expected">{{ getSeasonCheckDetail(file, 'organized')!.expected }}</span>
                  </span>
                  <span class="sc-season-missing">缺 {{ getSeasonCheckDetail(file, 'organized')!.expected - getSeasonCheckDetail(file, 'organized')!.actual }} 集</span>
                </span>
                <!-- 目录：显示文件数和大小（有标注时隐藏原始 stats） -->
                <span v-if="file.isDirectory && file.fileCount != null && !isYearFolder(file.name) && !getSeasonCheckDetail(file, 'organized')" class="cd2-dir-stats">
                  {{ file.fileCount }} 文件
                  <template v-if="file.totalSize"> · {{ formatBytes(file.totalSize) }}</template>
                </span>
                <span v-else-if="!file.isDirectory" class="cd2-file-size">{{ formatBytes(file.size) }}</span>
                <span v-if="file.isDirectory" class="cd2-file-arrow">›</span>
                <!-- 删除 Season 文件夹按钮（仅目录） -->
                <button
                  v-if="file.isDirectory"
                  class="cd2-item-delete-btn"
                  :disabled="cd2Deleting"
                  @click.stop="handleDeleteOrganizedItem(file)"
                  title="删除此目录"
                >
                  <el-icon :size="14"><Delete /></el-icon>
                </button>
                <!-- 移至左侧按钮（仅目录） -->
                <button
                  v-if="file.isDirectory"
                  class="cd2-item-move-btn"
                  :disabled="cd2Moving"
                  @click.stop="handleMoveSingleToLeft(file)"
                  title="移至左侧媒体库"
                >
                  <el-icon :size="14"><Right /></el-icon>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
      </template>
    </div>
  </div>

  <!-- ==================== 移动失败：三段式选择对话框 ==================== -->
  <el-dialog
    v-model="moveFallbackVisible"
    title="目标目录不存在"
    width="420px"
    :close-on-click-modal="false"
    center
  >
    <div class="fallback-dialog-body">
      <p class="fallback-dialog-msg">
        左侧不存在该电视剧父目录<br />
        <strong>「{{ moveFallbackShowName }}」</strong><br />
        请选择接下来的操作：
      </p>
      <div class="fallback-dialog-options">
        <el-button
          type="primary"
          :loading="cd2Moving"
          :disabled="cd2Moving"
          @click="handleFallbackCreateAndMove"
        >
          📁 新建目录并移动本季
        </el-button>
        <el-button
          type="warning"
          :disabled="cd2Moving"
          @click="handleFallbackMoveEntire"
        >
          📦 移动整个电视剧目录
        </el-button>
        <el-button
          :disabled="cd2Moving"
          @click="handleFallbackCancel"
        >
          取消
        </el-button>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
/* ==================== Organize Workbench ==================== */
.organize-workbench {
  flex-shrink: 0;
  padding: 12px 16px;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.ow-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--text-secondary);
}

.ow-result {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.ow-info {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.ow-label {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--accent-purple, #8b5cf6);
  background: rgba(139, 92, 246, 0.12);
  padding: 2px 8px;
  border-radius: var(--radius-full);
  margin-right: 4px;
}

.ow-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
}

.ow-tmdb-name {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-tertiary);
  background: var(--bg-card-hover);
  padding: 1px 8px;
  border-radius: var(--radius-full);
}

.ow-sep { color: var(--text-tertiary); font-weight: 300; }

.ow-year {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.ow-season {
  font-size: 13px;
  font-weight: 600;
  color: var(--accent-blue);
}

.ow-eps {
  font-size: 13px;
  font-weight: 600;
  color: var(--accent-green);
}

.ow-tmdb {
  font-size: 10px;
  color: var(--text-tertiary);
  background: var(--bg-card-hover);
  padding: 2px 7px;
  border-radius: var(--radius-full);
  margin-left: 2px;
}

.ow-cat-tag {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-purple, #8b5cf6);
  background: rgba(139, 92, 246, 0.12);
  padding: 2px 9px;
  border-radius: var(--radius-full);
  margin-left: 2px;
}

.ow-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.ow-cd2-status {
  font-size: 11px;
  font-weight: 500;
  padding: 3px 10px;
  border-radius: var(--radius-full);
}
.ow-cd2-ok {
  color: var(--accent-green);
  background: var(--accent-green-soft);
}
.ow-cd2-warn {
  color: var(--accent-orange, #f59e0b);
  background: rgba(245, 158, 11, 0.12);
}

.ow-clear-btn {
  padding: 4px 10px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  background: transparent;
  color: var(--text-tertiary);
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
}
.ow-clear-btn:hover { border-color: var(--accent-red); color: var(--accent-red); }

.ow-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
  color: var(--accent-red);
  font-weight: 500;
}

/* ==================== Layout ==================== */
.cleanup-container {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 12px 20px;
  gap: 16px;
  overflow: hidden;
}

/* ==================== Header ==================== */
.cleanup-header {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

/* --- 全局搜索框 --- */
.header-search {
  flex: 1;
  max-width: 480px;
  position: relative;
  display: flex;
  align-items: center;
}

.header-search-icon {
  position: absolute;
  left: 16px;
  color: var(--text-tertiary);
  pointer-events: none;
}

.header-search-input {
  width: 100%;
  padding: 11px 44px 11px 44px;
  background: var(--bg-card);
  border: none;
  border-radius: var(--radius-full);
  color: var(--text-primary);
  font-size: 14px;
  font-family: inherit;
  outline: none;
  box-shadow: 0 0 0 1px var(--border-color);
  transition: all 0.25s;
}
.header-search-input::placeholder { color: var(--text-tertiary); }
.header-search-input:focus {
  box-shadow: 0 0 0 2px var(--accent-blue), 0 0 16px rgba(59, 130, 246, 0.12);
  background: var(--bg-input-focus);
}

.header-search-clear {
  position: absolute;
  right: 14px;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: color 0.15s;
}
.header-search-clear:hover { color: var(--text-primary); }

/* --- 分类筛选 --- */
.header-category {
  position: relative;
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.category-icon {
  position: absolute;
  left: 14px;
  color: var(--text-tertiary);
  z-index: 1;
  pointer-events: none;
}

.category-select {
  width: 140px;
}
.category-select :deep(.el-input__wrapper) {
  padding-left: 36px;
}

/* ==================== 下拉框毛玻璃（teleported="false" 时局部覆盖） ==================== */
:deep(.el-select-dropdown) {
  background: rgba(15, 23, 42, 0.94);
  backdrop-filter: blur(20px) saturate(150%);
  -webkit-backdrop-filter: blur(20px) saturate(150%);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 14px;
  box-shadow: 0 18px 50px -12px rgba(0, 0, 0, 0.55), 0 0 24px rgba(59, 130, 246, 0.06);
  overflow: hidden;
  z-index: 2100;
}
:deep(.el-select-dropdown__item) { color: #94a3b8; border-radius: 9px; }
:deep(.el-select-dropdown__item.hover),
:deep(.el-select-dropdown__item.is-hovering),
:deep(.el-select-dropdown__item:hover) {
  background: rgba(255, 255, 255, 0.1);
  color: #f1f5f9;
}
:deep(.el-select-dropdown__item.is-selected) {
  color: #60a5fa;
  font-weight: 600;
  text-shadow: 0 0 8px rgba(96, 165, 250, 0.55);
}

/* ==================== 弹窗 — 深度毛玻璃化 ==================== */
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
}
:deep(.el-dialog__title) {
  color: #ffffff;
  font-weight: 700;
}
:deep(.el-dialog__body) { color: #cbd5e1; }
:deep(.el-dialog__footer) {
  background: transparent;
  border-top: none;
}

/* --- 预留按钮 --- */
.btn-auto-scan {
  padding: 9px 18px;
  border: none;
  border-radius: var(--radius-full);
  background: var(--bg-card);
  color: var(--text-tertiary);
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  cursor: not-allowed;
  white-space: nowrap;
  opacity: 0.55;
  flex-shrink: 0;
  margin-left: auto;
}

/* ==================== Split View ==================== */
.split-view {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  overflow: hidden;
  min-height: 0;
}

.split-col {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
  /* 毛玻璃面板：带边框圆角 */
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.03);
  backdrop-filter: blur(14px) saturate(140%);
  -webkit-backdrop-filter: blur(14px) saturate(140%);
}

/* ==================== Column Header ==================== */
.col-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color);
  flex-shrink: 0;
  margin-bottom: 12px;
}

.col-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.col-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.col-dot.dot-danger {
  background: var(--accent-red);
  box-shadow: 0 0 6px rgba(239, 68, 68, 0.5);
}
.col-dot.dot-success {
  background: var(--accent-green);
  box-shadow: 0 0 6px rgba(16, 185, 129, 0.5);
}

.col-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.col-count {
  font-size: 11px;
  color: var(--accent-blue);
  background: var(--accent-blue-soft);
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-weight: 600;
}

.col-select {
  width: 240px;
  flex-shrink: 0;
}

/* ==================== Column Body ==================== */
.col-body {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* ==================== Empty State ==================== */
.col-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 20px;
  text-align: center;
  height: 100%;
}

.empty-icon-circle {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-tertiary);
  margin-bottom: 14px;
}

.empty-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 4px;
}

.empty-desc {
  font-size: 13px;
  color: var(--text-tertiary);
  margin: 0;
  line-height: 1.5;
}

/* ==================== Batch Action Bar ==================== */
.batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  margin-bottom: 8px;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

/* el-checkbox 暗黑覆盖 */
.batch-bar :deep(.el-checkbox__label) {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 500;
}

.batch-bar :deep(.el-checkbox__inner) {
  background: transparent;
  border-color: var(--border-color);
}

.batch-bar :deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  background: var(--accent-blue);
  border-color: var(--accent-blue);
}

.batch-bar :deep(.el-checkbox__input.is-indeterminate .el-checkbox__inner) {
  background: var(--accent-blue);
  border-color: var(--accent-blue);
}

.batch-delete-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 18px;
  border: none;
  border-radius: var(--radius-full);
  background: var(--accent-red-soft);
  color: var(--accent-red);
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.batch-delete-btn:hover:not(:disabled) {
  background: var(--accent-red);
  color: #fff;
  box-shadow: 0 0 14px rgba(239, 68, 68, 0.4);
}
.batch-delete-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.batch-organize-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 18px;
  border: none;
  border-radius: var(--radius-full);
  background: rgba(139, 92, 246, 0.12);
  color: var(--accent-purple, #8b5cf6);
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.batch-organize-btn:hover:not(:disabled) {
  background: var(--accent-purple, #8b5cf6);
  color: #fff;
  box-shadow: 0 0 14px rgba(139, 92, 246, 0.4);
}
.batch-organize-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.batch-auto-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 18px;
  border: none;
  border-radius: var(--radius-full);
  background: rgba(16, 185, 129, 0.12);
  color: var(--accent-green, #10b981);
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.batch-auto-btn:hover:not(:disabled) {
  background: var(--accent-green, #10b981);
  color: #fff;
  box-shadow: 0 0 14px rgba(16, 185, 129, 0.4);
}
.batch-auto-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

/* ==================== Card List ==================== */
.card-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 2px;
}

/* ==================== Compact Card ==================== */
.compact-card {
  display: flex;
  align-items: stretch;
  gap: 12px;
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 14px;
  backdrop-filter: blur(12px) saturate(140%);
  -webkit-backdrop-filter: blur(12px) saturate(140%);
  transition: all 0.2s ease;
}
.compact-card:hover {
  border-color: rgba(255, 255, 255, 0.18);
  background: rgba(255, 255, 255, 0.07);
  box-shadow: 0 8px 22px -14px rgba(0, 0, 0, 0.5);
}
.compact-card.card-left {
  border-left: 3px solid transparent;
}
.compact-card.card-left:hover {
  border-left-color: var(--accent-red);
}
.compact-card.card-left.is-deleting {
  opacity: 0.5;
  pointer-events: none;
}
.compact-card.card-left.is-checked {
  border-left-color: var(--accent-blue) !important;
  border-color: rgba(59, 130, 246, 0.55);
  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.25), 0 0 16px rgba(59, 130, 246, 0.3);
  background: rgba(59, 130, 246, 0.06);
}
.compact-card.card-right {
  border-left: 3px solid transparent;
}
.compact-card.card-right:hover {
  border-left-color: var(--accent-green);
}
.compact-card.card-right.is-checked {
  border-left-color: var(--accent-purple, #8b5cf6) !important;
  border-color: rgba(139, 92, 246, 0.55);
  box-shadow: 0 0 0 1px rgba(139, 92, 246, 0.25), 0 0 16px rgba(139, 92, 246, 0.3);
  background: rgba(139, 92, 246, 0.06);
}

/* Card checkbox (left column only) */
.cc-checkbox {
  flex-shrink: 0;
  align-self: center;
  margin-right: 2px;
}
.cc-checkbox :deep(.el-checkbox__inner) {
  background: transparent;
  border-color: var(--border-color);
  width: 18px;
  height: 18px;
  border-radius: 5px;
}
.cc-checkbox :deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  background: var(--accent-blue);
  border-color: var(--accent-blue);
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.5);
}
.cc-checkbox :deep(.el-checkbox__input.is-checked .el-checkbox__inner::after) {
  border-color: #fff;
}

.cc-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.cc-name {
  font-size: 13.5px;
  font-weight: 700;
  color: #ffffff;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
}

.cc-meta-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  flex-wrap: wrap;
}

.cc-size {
  color: var(--text-secondary);
  font-weight: 500;
}

.cc-sep {
  color: var(--border-color);
}

.cc-state {
  font-weight: 600;
}

.cc-category {
  color: var(--text-tertiary);
  background: var(--bg-card-hover);
  padding: 1px 8px;
  border-radius: var(--radius-full);
}

.cc-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}

.cc-progress-track {
  flex: 1;
  height: 6px; /* 极细 h-1.5 */
  background: rgba(255, 255, 255, 0.07);
  border-radius: 999px;
  overflow: hidden;
}

.cc-progress-fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #34d399, #10b981);
  box-shadow: 0 0 10px rgba(52, 211, 153, 0.8), 0 0 3px rgba(52, 211, 153, 0.5);
  transition: width 0.5s ease;
}

.cc-progress-num {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  flex-shrink: 0;
  min-width: 34px;
  text-align: right;
}

/* ==================== Card Actions ==================== */
.cc-delete-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--accent-red-soft);
  color: var(--accent-red);
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
  align-self: center;
}
.cc-delete-btn:hover:not(:disabled) {
  background: var(--accent-red);
  color: #fff;
  box-shadow: 0 0 12px rgba(239, 68, 68, 0.4);
  transform: scale(1.08);
}
.cc-delete-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* 整理按钮 (右列卡片) */
.cc-organize-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 5px 10px;
  border: 1px solid rgba(139, 92, 246, 0.3);
  border-radius: var(--radius-full);
  background: rgba(139, 92, 246, 0.1);
  color: var(--accent-purple, #8b5cf6);
  font-size: 11px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  align-self: center;
  transition: all 0.2s;
}
.cc-organize-btn:hover:not(:disabled) {
  background: var(--accent-purple, #8b5cf6);
  color: #fff;
  box-shadow: 0 0 12px rgba(139, 92, 246, 0.4);
}
.cc-organize-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.cc-org-text {
  font-size: 10px;
}

.cc-archived-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 12px;
  border-radius: var(--radius-full);
  background: var(--accent-green-soft);
  color: var(--accent-green);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
  align-self: center;
  border: 1px solid rgba(16, 185, 129, 0.2);
}

/* ==================== Column Pagination (Dark) ==================== */
.col-pagination {
  display: flex;
  justify-content: center;
  padding: 12px 0 4px;
  margin-top: auto;
  flex-shrink: 0;
}

/* 分页器暗黑覆盖 — 彻底杜绝白底 */
.col-pagination :deep(.el-pagination) {
  --el-pagination-bg-color: transparent;
  --el-pagination-text-color: var(--text-secondary);
  --el-pagination-button-bg-color: var(--bg-card);
  --el-pagination-button-disabled-bg-color: var(--bg-card);
  --el-pagination-hover-color: var(--accent-blue);
}

.col-pagination :deep(.el-pager li) {
  background: var(--bg-card) !important;
  color: var(--text-secondary) !important;
  border-radius: var(--radius-sm) !important;
  min-width: 30px !important;
  height: 30px !important;
  line-height: 30px !important;
  font-weight: 500;
}

.col-pagination :deep(.el-pager li.is-active) {
  background: var(--accent-blue) !important;
  color: #fff !important;
  font-weight: 600;
}

.col-pagination :deep(.el-pager li:hover:not(.is-active)) {
  background: var(--accent-blue-soft) !important;
  color: var(--accent-blue) !important;
}

.col-pagination :deep(.btn-prev),
.col-pagination :deep(.btn-next) {
  background: var(--bg-card) !important;
  color: var(--text-secondary) !important;
  border-radius: var(--radius-sm) !important;
}

.col-pagination :deep(.btn-prev:hover:not(:disabled)),
.col-pagination :deep(.btn-next:hover:not(:disabled)) {
  background: var(--accent-blue-soft) !important;
  color: var(--accent-blue) !important;
}

.col-pagination :deep(.btn-prev:disabled),
.col-pagination :deep(.btn-next:disabled) {
  background: var(--bg-card) !important;
  color: var(--text-tertiary) !important;
  opacity: 0.4;
}

.col-pagination :deep(.el-pagination__sizes .el-select .el-input__wrapper) {
  background: var(--bg-card) !important;
  box-shadow: 0 0 0 1px var(--border-color) !important;
}

.col-pagination :deep(.el-pagination__total) {
  color: var(--text-tertiary) !important;
}

.col-pagination :deep(.el-pagination__jump) {
  color: var(--text-secondary) !important;
}

/* ==================== CD2 网盘目录浏览 (导航版) ==================== */

/* --- 拖拽手柄 --- */
.cd2-resize-handle {
  flex-shrink: 0;
  height: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: row-resize;
  margin-top: 4px;
}
.cd2-resize-handle:hover .cd2-resize-handle-bar,
.cd2-resize-handle:active .cd2-resize-handle-bar {
  background: var(--accent-blue);
  opacity: 0.8;
}
.cd2-resize-handle-bar {
  width: 50px;
  height: 4px;
  border-radius: 2px;
  background: var(--border-color);
  transition: background 0.2s, opacity 0.2s;
}

.cd2-section {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 4px;
  overflow: auto;
  min-height: 0;
}

.cd2-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.cd2-section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.dot-cd2 {
  background: var(--accent-purple, #8b5cf6);
  box-shadow: 0 0 6px rgba(139, 92, 246, 0.5);
}

.cd2-cat-sep {
  color: var(--text-tertiary);
  font-weight: 400;
}

.cd2-cat-select {
  width: 100px;
}

.cd2-header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}


.cd2-refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 14px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
}
.cd2-refresh-btn:hover:not(:disabled) {
  border-color: var(--accent-blue);
  color: var(--accent-blue);
  background: var(--accent-blue-soft);
}
.cd2-refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* loading / error */
.cd2-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 0;
  color: var(--text-tertiary);
  font-size: 13px;
}

.cd2-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  background: var(--accent-red-soft);
  color: var(--accent-red);
  font-size: 13px;
  font-weight: 500;
}
.cd2-error-icon { font-size: 16px; flex-shrink: 0; }

/* split grid */
.cd2-split {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.cd2-col {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
}

.cd2-col-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color);
  flex-shrink: 0;
}

.cd2-col-icon { font-size: 15px; flex-shrink: 0; }

.cd2-col-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
}

.cd2-col-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  white-space: nowrap;
}
.cd2-col-badge.media-badge     { background: var(--accent-blue-soft);   color: var(--accent-blue); }
.cd2-col-badge.organized-badge { background: var(--accent-green-soft);  color: var(--accent-green); }

/* 年份快捷跳转 */
.cd2-year-jump {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.cd2-year-input {
  width: 64px;
  padding: 4px 8px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-card-hover);
  color: var(--text-primary);
  font-size: 11px;
  font-family: inherit;
  text-align: center;
  outline: none;
  transition: border-color 0.2s;
  /* hide number spinner */
  -moz-appearance: textfield;
}
.cd2-year-input::-webkit-inner-spin-button,
.cd2-year-input::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
.cd2-year-input:focus { border-color: var(--accent-blue); }
.cd2-year-input::placeholder { color: var(--text-tertiary); font-size: 10px; }

.cd2-year-btn {
  padding: 4px 8px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
}
.cd2-year-btn:hover:not(:disabled) {
  border-color: var(--accent-blue);
  color: var(--accent-blue);
}
.cd2-year-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* 根目录截断提示 */
.cd2-truncate-hint {
  padding: 6px 14px;
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--accent-blue-soft);
  border-bottom: 1px solid var(--border-color);
  text-align: center;
  font-style: italic;
}

/* --- 导航栏 (面包屑 + 返回按钮) --- */
.cd2-nav-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  min-height: 30px;
}

.cd2-back-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 4px 10px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  flex-shrink: 0;
}
.cd2-back-btn:hover:not(:disabled) {
  border-color: var(--accent-blue);
  color: var(--accent-blue);
  background: var(--accent-blue-soft);
}
.cd2-back-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.cd2-back-btn.invisible { visibility: hidden; }

.cd2-back-arrow { font-size: 12px; line-height: 1; }

.cd2-crumb {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--text-secondary);
  overflow: hidden;
}
.cd2-crumb-root { flex-shrink: 0; opacity: 0.7; }
.cd2-crumb-rel {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
  font-weight: 500;
}
.cd2-crumb-rel.is-root { color: var(--text-tertiary); font-weight: 400; font-style: italic; }

/* 识别按钮（CD2 导航栏） */
.cd2-identify-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid rgba(139, 92, 246, 0.3);
  border-radius: var(--radius-full);
  background: rgba(139, 92, 246, 0.1);
  color: var(--accent-purple, #8b5cf6);
  font-size: 11px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  flex-shrink: 0;
}
.cd2-identify-btn:hover:not(:disabled) {
  background: var(--accent-purple, #8b5cf6);
  color: #fff;
  box-shadow: 0 0 10px rgba(139, 92, 246, 0.4);
}
.cd2-identify-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.cd2-identify-icon {
  font-size: 12px;
  line-height: 1;
}

/* 删除当前目录按钮 */
.cd2-delete-dir-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: var(--radius-full);
  background: var(--accent-red-soft);
  color: var(--accent-red);
  font-size: 11px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  flex-shrink: 0;
  margin-left: auto;
}
.cd2-delete-dir-btn:hover:not(:disabled) {
  background: var(--accent-red);
  color: #fff;
  box-shadow: 0 0 10px rgba(239, 68, 68, 0.35);
}
.cd2-delete-dir-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

/* 自动化洗版按钮（已完结侧导航栏） */
.cd2-auto-process-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid rgba(59, 130, 246, 0.3);
  border-radius: var(--radius-full);
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
  font-size: 11px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  flex-shrink: 0;
  margin-left: auto;
}
.cd2-auto-process-btn:hover:not(:disabled) {
  background: var(--accent-blue);
  color: #fff;
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.35);
}
.cd2-auto-process-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

/* 移动整剧按钮（已完结侧导航栏） */
.cd2-move-dir-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid rgba(16, 185, 129, 0.3);
  border-radius: var(--radius-full);
  background: var(--accent-green-soft);
  color: var(--accent-green);
  font-size: 11px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  flex-shrink: 0;
}
.cd2-move-dir-btn:hover:not(:disabled) {
  background: var(--accent-green);
  color: #fff;
  box-shadow: 0 0 10px rgba(16, 185, 129, 0.35);
}
.cd2-move-dir-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

/* 单条目移至左侧按钮（已完结侧） */
.cd2-item-move-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
  opacity: 0;
}
.cd2-file-item:hover .cd2-item-move-btn {
  opacity: 1;
}
.cd2-item-move-btn:hover:not(:disabled) {
  background: var(--accent-green-soft);
  color: var(--accent-green);
}
.cd2-item-move-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

/* --- 文件列表 body --- */
.cd2-col-body {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  padding: 4px 0;
}

.cd2-file-list {
  display: flex;
  flex-direction: column;
}

.cd2-file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  transition: background 0.15s;
  cursor: default;
}
.cd2-file-item:hover {
  background: rgba(255, 255, 255, 0.05); /* hover:bg-white/5 整行微提亮 */
}
.cd2-file-item + .cd2-file-item {
  border-top: 1px solid var(--border-color-subtle, rgba(255,255,255,0.03));
}

/* 文件夹可点击 */
.cd2-file-item.is-clickable {
  cursor: pointer;
}
.cd2-file-item.is-clickable:hover {
  background: rgba(255, 255, 255, 0.07);
}

.cd2-file-icon {
  font-size: 14px;
  flex-shrink: 0;
  /* 文件树图标 — 浅灰色 */
  filter: grayscale(0.75) opacity(0.82);
  transition: filter 0.15s;
}
.cd2-file-item:hover .cd2-file-icon {
  filter: grayscale(0.4) opacity(0.95);
}

.cd2-file-name {
  flex: 1;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cd2-file-item.is-dir .cd2-file-name { font-weight: 600; }

.cd2-file-size {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary);
  flex-shrink: 0;
  white-space: nowrap;
}

/* 目录统计：文件数 + 总大小 */
.cd2-dir-stats {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary);
  flex-shrink: 0;
  white-space: nowrap;
  background: var(--bg-card-hover);
  padding: 2px 8px;
  border-radius: var(--radius-full);
  margin-right: 2px;
}

.cd2-file-arrow {
  font-size: 15px;
  color: var(--text-tertiary);
  flex-shrink: 0;
  font-weight: 400;
  transition: color 0.15s;
}
.cd2-file-item.is-clickable:hover .cd2-file-arrow { color: var(--accent-blue); }

/* --- CD2 媒体库删除功能样式 --- */

/* 批量操作栏 */
.cd2-batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-card-hover);
  flex-shrink: 0;
  gap: 10px;
  flex-wrap: wrap;
}

.cd2-batch-bar :deep(.el-checkbox__label) {
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
}

.cd2-batch-bar :deep(.el-checkbox__inner) {
  background: transparent;
  border-color: var(--border-color);
  width: 16px;
  height: 16px;
}

.cd2-batch-bar :deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  background: var(--accent-red);
  border-color: var(--accent-red);
}

.cd2-batch-bar :deep(.el-checkbox__input.is-indeterminate .el-checkbox__inner) {
  background: var(--accent-red);
  border-color: var(--accent-red);
}

.cd2-batch-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.cd2-batch-delete-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 14px;
  border: none;
  border-radius: var(--radius-full);
  background: var(--accent-red-soft);
  color: var(--accent-red);
  font-size: 11px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.cd2-batch-delete-btn:hover:not(:disabled) {
  background: var(--accent-red);
  color: #fff;
  box-shadow: 0 0 12px rgba(239, 68, 68, 0.4);
}
.cd2-batch-delete-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

/* 单个条目 checkbox */
.cd2-item-checkbox {
  flex-shrink: 0;
  margin-right: 2px;
}
.cd2-item-checkbox :deep(.el-checkbox__inner) {
  background: transparent;
  border-color: var(--border-color);
  width: 16px;
  height: 16px;
  border-radius: 4px;
}
.cd2-item-checkbox :deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  background: var(--accent-red);
  border-color: var(--accent-red);
}

/* 条目可点击区域 */
.cd2-file-clickable {
  cursor: pointer;
}

/* 文件夹名称加粗 */
.cd2-file-name.is-folder {
  font-weight: 600;
}

/* 条目选中高亮 */
.cd2-file-item.is-checked {
  background: var(--accent-red-soft) !important;
  border-left: 3px solid var(--accent-red);
}

/* 单个删除按钮 */
.cd2-item-delete-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
  opacity: 0;
}
.cd2-file-item:hover .cd2-item-delete-btn {
  opacity: 1;
}
.cd2-item-delete-btn:hover:not(:disabled) {
  background: var(--accent-red-soft);
  color: var(--accent-red);
}
.cd2-item-delete-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

/* 单剧核查按钮（剧集行内） */
.cd2-check-one-btn {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
  opacity: 0;
  font-size: 12px;
  padding: 0;
}
.cd2-file-item:hover .cd2-check-one-btn {
  opacity: 1;
}
.cd2-check-one-btn:hover:not(:disabled) {
  background: var(--accent-blue-soft, rgba(59, 130, 246, 0.12));
  color: var(--accent-blue);
  border-color: var(--accent-blue);
}
.cd2-check-one-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

/* ==================== Move Fallback Dialog ==================== */
.fallback-dialog-body {
  text-align: center;
}

.fallback-dialog-msg {
  font-size: 14px;
  line-height: 1.8;
  color: var(--text-primary);
  margin: 0 0 20px 0;
}

.fallback-dialog-msg strong {
  color: var(--accent-purple, #8b5cf6);
  font-size: 15px;
}

.fallback-dialog-options {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.fallback-dialog-options .el-button {
  width: 100%;
  justify-content: center;
  height: 40px;
  font-size: 13px;
  font-weight: 600;
}

/* ==================== Mobile ==================== */
@media screen and (max-width: 768px) {
  .cleanup-container {
    padding: 8px;
    gap: 10px;
    overflow-y: auto;
    overflow-x: hidden;
  }

  .cleanup-header {
    flex-wrap: wrap;
    gap: 8px;
  }

  .header-search {
    max-width: 100%;
    min-width: 100%;
  }

  .header-search-input {
    font-size: 13px;
    padding: 10px 38px 10px 38px;
  }

  .header-category {
    flex: 1;
  }

  .category-select {
    width: 100%;
  }

  .btn-auto-scan {
    width: 100%;
    text-align: center;
    font-size: 11px;
    padding: 8px;
    margin-left: 0;
  }

  .split-view {
    grid-template-columns: 1fr;
    gap: 12px;
    flex: none;
    min-height: 400px;
  }

  .col-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    padding: 10px 12px;
    margin-bottom: 8px;
  }

  .col-select { width: 100%; }

  .compact-card {
    padding: 10px 12px;
    gap: 10px;
  }

  .cc-name {
    font-size: 12px;
    -webkit-line-clamp: 3;
  }

  /* CD2 section mobile */
  .cd2-section {
    padding-top: 10px;
    gap: 8px;
    max-height: none;
    overflow: visible;
  }

  .cd2-section.cd2-collapsed {
    /* 收起状态：只显示 header，隐藏内部内容 */
    min-height: auto;
  }

  .cd2-section-header {
    flex-direction: row;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  .cd2-section-title {
    flex: 1;
    min-width: 0;
    flex-wrap: wrap;
  }

  .cd2-header-actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .cd2-toggle-btn {
    display: inline-flex;
    align-items: center;
    padding: 5px 12px;
    border: 1px solid var(--border-color);
    border-radius: var(--radius-full);
    background: var(--bg-card);
    color: var(--text-secondary);
    font-size: 11px;
    font-weight: 600;
    font-family: inherit;
    cursor: pointer;
    white-space: nowrap;
    transition: all 0.2s;
  }
  .cd2-toggle-btn:hover {
    border-color: var(--accent-blue);
    color: var(--accent-blue);
    background: var(--accent-blue-soft);
  }

  .cd2-split {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .cd2-col-header {
    padding: 6px 10px;
  }

  .cd2-nav-bar {
    gap: 6px;
    flex-wrap: wrap;
  }

  .cd2-file-item {
    padding: 6px 10px;
  }

  .cd2-file-name {
    font-size: 11px;
  }

  .cd2-crumb {
    font-size: 10px;
  }

  /* 移动端隐藏拖拽手柄 */
  .cd2-resize-handle {
    display: none;
  }

  /* 移动端导航栏按钮缩小/换行 */
  .cd2-nav-bar .cd2-identify-btn,
  .cd2-nav-bar .cd2-season-check-btn,
  .cd2-nav-bar .cd2-clear-check-btn,
  .cd2-nav-bar .cd2-auto-process-btn,
  .cd2-nav-bar .cd2-delete-dir-btn,
  .cd2-nav-bar .cd2-move-dir-btn {
    font-size: 10px;
    padding: 3px 8px;
    white-space: nowrap;
  }
}

/* ==================== 残缺季核查按钮（nav bar 内） ==================== */
.cd2-season-check-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid var(--accent-orange);
  border-radius: var(--radius-sm);
  background: rgba(245, 158, 11, 0.08);
  color: var(--accent-orange);
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  margin-left: auto;
}
.cd2-season-check-btn:hover:not(:disabled) {
  background: rgba(245, 158, 11, 0.16);
  border-color: var(--accent-orange);
}
.cd2-season-check-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cd2-clear-check-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 4px 8px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-tertiary);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.cd2-clear-check-btn:hover {
  color: var(--text-secondary);
  border-color: var(--text-tertiary);
}

/* ==================== 残缺季内联标注 ==================== */
.sc-inline-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  margin-left: 6px;
  flex-shrink: 0;
}
.sc-badge-warn {
  background: rgba(245, 158, 11, 0.12);
  color: #d97706;
  border: 1px solid rgba(245, 158, 11, 0.25);
}
.sc-badge-ok {
  background: rgba(34, 197, 94, 0.1);
  color: #16a34a;
  border: 1px solid rgba(34, 197, 94, 0.2);
}
.sc-badge-season {
  background: rgba(239, 68, 68, 0.08);
  color: #dc2626;
  border: 1px solid rgba(239, 68, 68, 0.2);
  gap: 4px;
}
.sc-season-nums {
  font-weight: 700;
  font-size: 11.5px;
}
.sc-season-actual {
  color: #ef4444;
}
.sc-season-sep {
  color: var(--text-tertiary);
}
.sc-season-expected {
  color: var(--text-secondary);
}
.sc-season-missing {
  font-weight: 500;
  font-size: 10.5px;
  color: #f97316;
}

/* 目录项高亮：残缺 */
.sc-show-incomplete {
  background: rgba(245, 158, 11, 0.04) !important;
  border-left: 3px solid var(--accent-orange) !important;
}
/* 目录项高亮：完整 */
.sc-show-complete {
  background: rgba(34, 197, 94, 0.03) !important;
  border-left: 3px solid rgba(34, 197, 94, 0.4) !important;
}
</style>
