<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Delete, CircleCheck, Loading, Monitor, Close, Filter, Setting, Right } from '@element-plus/icons-vue'

// ==================== 实例数据（从 API 获取） ====================
const instances = ref([])
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

// 分类数据 — 从 qB 实例动态获取
const leftCategories = ref([])
const rightCategories = ref([])

// 分类选项（合并左右实例的分类 + 全部分类）
const categoryOptions = computed(() => {
  const all = new Set([...leftCategories.value, ...rightCategories.value])
  return [
    { label: '全部分类', value: '' },
    ...[...all].sort().map(cat => ({ label: cat, value: cat }))
  ]
})

// 从 qB 实例获取分类列表
const fetchCategories = async (side) => {
  const instanceId = side === 'left' ? leftInstanceId.value : rightInstanceId.value
  if (!instanceId) return
  try {
    const res = await axios.get('/api/qb/data')
    const data = res.data?.find?.(d => d.id === instanceId)
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
const leftTorrents = ref([])   // 原始 API 数据
const rightTorrents = ref([])  // 原始 API 数据

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
    ElMessage.error(`获取左列种子失败: ${e.response?.data?.detail || e.message}`)
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
    ElMessage.error(`获取右列种子失败: ${e.response?.data?.detail || e.message}`)
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
const selectedLeftTorrents = ref([])

// 右列批量选择
const selectedRightTorrents = ref([])
const batchOrganizing = ref(false)
const autoProcessing = ref(false)
const autoProcessResult = ref(null)

// 当前页是否全选
const isAllSelected = computed(() => {
  if (!paginatedLeftList.value.length) return false
  return paginatedLeftList.value.every(t => selectedLeftTorrents.value.includes(t.hash))
})

// 全选 / 取消全选当前页
const handleSelectAll = (val) => {
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
const handleCheckOne = (hash) => {
  const idx = selectedLeftTorrents.value.indexOf(hash)
  if (idx === -1) {
    selectedLeftTorrents.value.push(hash)
  } else {
    selectedLeftTorrents.value.splice(idx, 1)
  }
}

// 右列全选 / 取消全选当前页
const handleSelectAllRight = (val) => {
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
const handleCheckOneRight = (hash) => {
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
    ElMessage.error(`批量整理出错: ${e.message}`)
  } finally {
    batchOrganizing.value = false
    selectedRightTorrents.value = []
  }
}

// 全自动洗版 — 完结校验 + 智能对比 + 删除
const handleAutoProcess = async () => {
  if (!selectedRightTorrents.value.length) {
    ElMessage.warning('请先选择要自动化处理的种子')
    return
  }
  const torrents = rightTorrents.value.filter(t => selectedRightTorrents.value.includes(t.hash))
  if (!torrents.length) {
    ElMessage.warning('未找到选中的种子')
    return
  }

  try {
    await ElMessageBox.confirm(
      `将对选中的 ${torrents.length} 个种子执行全自动洗版流程：\n\n` +
      '1. 校验「已完结」目录中所有 Season 是否真正完结\n' +
      '2. 对比「媒体库」版本，智能决策保留/删除\n' +
      '3. 严格四重校验（名称/数量/总大小/单文件）后清理重复\n\n' +
      '确认开始？',
      '全自动洗版确认',
      { type: 'info', confirmButtonText: '开始执行', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  autoProcessing.value = true
  autoProcessResult.value = null

  try {
    const seeds = torrents.map(t => ({
      torrent_name: t.name,
      qb_config_id: rightInstanceId.value,
      category: t.category || selectedCategory.value || '',
    }))

    const res = await axios.post('/api/organize/auto_process_batch', { seeds })
    autoProcessResult.value = res.data

    const okCount = res.data?.ok || 0
    const errCount = res.data?.errors || 0

    if (errCount === 0) {
      ElMessage.success(`全自动洗版完成: ${okCount} 个成功`)
    } else {
      ElMessage.warning(`洗版结果: ${okCount} 成功, ${errCount} 失败 — 查看详情`)
    }

    // Show detailed results
    const results = res.data?.results || []
    for (const r of results) {
      if (r.success && r.stage === 'waiting_for_delete_webhook') {
        ElMessage.info(`「${r.details?.title || '?'}」已进入 Emby 确认阶段 — 等待自动移动 (task #${r.task_id})`)
      } else if (r.success && r.stage === 'completed') {
        ElMessage.success(`「${r.details?.title || '?'}」${r.message}`)
      } else if (r.success && r.stage === 'no_action_needed') {
        ElMessage.info(`「${r.details?.title || '?'}」${r.message}`)
      } else if (!r.success) {
        ElMessage.error(`「${r.details?.title || '?'}」${r.message}`)
      }
    }
  } catch (e) {
    ElMessage.error(`全自动洗版失败: ${e.response?.data?.detail || e.message}`)
  } finally {
    autoProcessing.value = false
    selectedRightTorrents.value = []
  }
}

// ==================== 右侧删除操作 ====================
const rightDeletingHash = ref(null)
const rightBatchDeleting = ref(false)

// 右侧单条删除
const handleDeleteRightTorrent = async (torrent) => {
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
    ElMessage.error(`删除失败: ${e.response?.data?.detail || e.message}`)
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
    ElMessage.error(`批量删除失败: ${e.response?.data?.detail || e.message}`)
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
    ElMessage.error(`批量删除失败: ${e.response?.data?.detail || e.message}`)
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
const deletingHash = ref(null)

const deleteTorrent = async (torrent) => {
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
    ElMessage.error(`删除失败: ${e.response?.data?.detail || e.message}`)
  } finally {
    deletingHash.value = null
  }
}

// ==================== 工具函数 ====================
const formatBytes = (bytes, decimals = 2) => {
  if (!+bytes) return '0 Bytes'
  const k = 1024
  const dm = decimals < 0 ? 0 : decimals
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

const getProgressColor = (state) => {
  if (['stalledUP', 'uploading'].includes(state)) return 'linear-gradient(90deg, #10b981, #34d399)'
  if (['downloading', 'metaDL'].includes(state)) return 'linear-gradient(90deg, #3b82f6, #60a5fa)'
  if (['pausedDL', 'pausedUP'].includes(state)) return 'linear-gradient(90deg, #f59e0b, #fbbf24)'
  if (['error', 'missingFiles'].includes(state)) return 'linear-gradient(90deg, #ef4444, #f87171)'
  return 'linear-gradient(90deg, #64748b, #94a3b8)'
}

const STATE_MAP = {
  'stalledUP': '做种中', 'uploading': '上传中', 'downloading': '下载中',
  'stalledDL': '等待下载', 'pausedDL': '暂停下载', 'pausedUP': '暂停上传',
  'queuedDL': '排队下载', 'queuedUP': '排队上传',
  'checkingUP': '校验中', 'checkingDL': '校验中',
  'error': '错误', 'missingFiles': '文件丢失',
  'metaDL': '获取元数据', 'moving': '移动中', 'unknown': '未知'
}

const formatState = (state) => STATE_MAP[state] || state

const getStateColor = (state) => {
  if (['stalledUP', 'uploading'].includes(state)) return '#10b981'
  if (['downloading', 'metaDL'].includes(state)) return '#3b82f6'
  if (['pausedDL', 'pausedUP'].includes(state)) return '#f59e0b'
  if (['error', 'missingFiles'].includes(state)) return '#ef4444'
  if (['queuedDL', 'queuedUP'].includes(state)) return '#f97316'
  if (['checkingUP', 'checkingDL'].includes(state)) return '#8b5cf6'
  return '#64748b'
}

// ==================== CD2 网盘目录浏览 (导航版) ====================
// 基础路径（固定常量，末尾带斜杠）
const CD2_MEDIA_BASE = '/80003588/emby库/电视剧/'
const CD2_ORGANIZED_BASE = '/80003588/网盘整理/完结整理/电视剧/'

// --- 每列独立导航状态 ---
const cd2Loading = ref(false)
const cd2Error = ref('')

// 媒体库（左列）
const cd2MediaPath = ref('')
const cd2MediaFiles = ref([])
const cd2MediaYearInput = ref('')   // 年份快捷跳转输入

// 已完结整理（右列）
const cd2OrganizedPath = ref('')
const cd2OrganizedFiles = ref([])

// 计算各列的分类根路径（基础路径 + qb分类 + /）
// 当全局分类筛选为"全部分类"时，强制默认展示 '国产剧' 下的年份文件夹
const cd2MediaRoot = computed(() => {
  const cat = selectedCategory.value || '国产剧'
  return CD2_MEDIA_BASE + cat + '/'
})

const cd2OrganizedRoot = computed(() => {
  const cat = selectedCategory.value || '国产剧'
  return CD2_ORGANIZED_BASE + cat + '/'
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
const cd2MediaWithStats = computed(() => cd2MediaDepth.value >= 2)
const cd2OrganizedWithStats = computed(() => cd2OrganizedDepth.value >= 2)
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
const isYearFolder = (name) => /^\d{4}$/.test(name)

// --- 工具：异步延迟（用于 CD2 缓存一致性容错）---
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms))

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
      cd2Error.value = e.response?.data?.detail || e.message || '获取CD2目录失败'
      ElMessage.error(cd2Error.value)
    }
  } finally {
    if (!silent) {
      cd2Loading.value = false
    }
  }
}

// --- 导航函数 ---
const enterFolder = (side, folderName) => {
  if (side === 'media') {
    cd2MediaPath.value = cd2MediaPath.value + folderName + '/'
  } else {
    cd2OrganizedPath.value = cd2OrganizedPath.value + folderName + '/'
  }
  loadCD2Data()
}

const goBack = (side) => {
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
  const year = cd2MediaYearInput.value.trim()
  if (!year) return
  cd2MediaPath.value = cd2MediaRoot.value + year + '/'
  cd2MediaYearInput.value = ''
  loadCD2Data()
}

// --- 分类变更时重置导航到新根路径 ---
watch(selectedCategory, () => {
  cd2MediaPath.value = cd2MediaRoot.value
  cd2OrganizedPath.value = cd2OrganizedRoot.value
  loadCD2Data()
})

// ==================== CD2 左侧（媒体库）删除功能 ====================
const selectedCd2MediaItems = ref([])
const cd2Deleting = ref(false)

// CD2 媒体库当前页文件标识（用于 checkbox 绑定）
const cd2MediaFileKeys = computed(() =>
  displayedMediaFiles.value.map(f => f.fullPathName || f.name)
)

const isAllCd2MediaSelected = computed(() => {
  if (!displayedMediaFiles.value.length) return false
  return displayedMediaFiles.value.every(f =>
    selectedCd2MediaItems.value.includes(f.fullPathName || f.name)
  )
})

const handleSelectAllCd2Media = (val) => {
  if (val) {
    const keys = displayedMediaFiles.value.map(f => f.fullPathName || f.name)
    const set = new Set([...selectedCd2MediaItems.value, ...keys])
    selectedCd2MediaItems.value = [...set]
  } else {
    const currentKeys = new Set(displayedMediaFiles.value.map(f => f.fullPathName || f.name))
    selectedCd2MediaItems.value = selectedCd2MediaItems.value.filter(k => !currentKeys.has(k))
  }
}

const handleCheckOneCd2Media = (key) => {
  const idx = selectedCd2MediaItems.value.indexOf(key)
  if (idx === -1) {
    selectedCd2MediaItems.value.push(key)
  } else {
    selectedCd2MediaItems.value.splice(idx, 1)
  }
}

// 删除确认弹窗
const confirmCd2Delete = async (count) => {
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
const handleDeleteSingleCd2Item = async (file) => {
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
    ElMessage.error(`删除失败: ${e.response?.data?.detail || e.message}`)
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
    ElMessage.error(`批量删除失败: ${e.response?.data?.detail || e.message}`)
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
const parseOrganizedPath = () => {
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
const buildMediaDestPath = (year, showName) => {
  const root = cd2MediaRoot.value  // e.g. /80003588/emby库/电视剧/国产剧/
  if (!year) return root
  if (!showName) return root + year + '/'
  return root + year + '/' + showName + '/'
}

// 核心移动函数
const doMoveToLeft = async (sourcePaths, destPath) => {
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
      error: e.response?.data?.detail || e.message || '移动失败',
      statusCode: e.response?.status,
    }
  } finally {
    cd2Moving.value = false
  }
}

// 移动单个 Season 文件夹 → 左侧对应剧集目录
const handleMoveSingleToLeft = async (file) => {
  const { year, showName, category } = parseOrganizedPath()
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
    const notFound = result.statusCode === 502 || /not found|不存在|no such/i.test(result.error)
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
  const { year, showName, category, depth } = parseOrganizedPath()

  if (depth < 2) {
    ElMessage.warning('请先进入具体的剧集目录')
    return
  }

  const sourcePath = cd2OrganizedPath.value
  const destPath = buildMediaDestPath(year, null)

  try {
    await ElMessageBox.confirm(
      `确定将整个剧集【${showName}】\n从：${sourcePath}\n移至左侧年份目录：${destPath}\n吗？\n\n（仅移动 Season 子文件夹，保留源根目录）`,
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
    ElMessage.success(`已移动整剧: ${showName}（${data.moved_seasons || 0} 个 Season）`)
    goBack('organized')
    // 左侧导航到刚移入的剧集目录，展示 Season 及 stats
    cd2MediaPath.value = data.target_show_path || buildMediaDestPath(year, showName)
    loadCD2Data()                                    // 即时加载 — 展现目录结构
    setTimeout(() => loadCD2Data(true), 2000)         // 2s 后静默重拉 — 获取 stats
  } catch (e) {
    const errMsg = e.response?.data?.detail || e.message || '移动失败'
    ElMessage.error(`移动整剧失败: ${errMsg}`)
  } finally {
    cd2Moving.value = false
  }
}

// 从 CD2 已完结面板直接触发自动化洗版
const handleAutoProcessFromCD2 = async () => {
  const { year, showName, depth } = parseOrganizedPath()

  if (depth < 2) {
    ElMessage.warning('请先进入具体的剧集目录')
    return
  }

  // 从文件夹名提取 tmdb_id（例如 "主角(2026) {tmdb=284110}"）
  const tmdbMatch = showName.match(/\{tmdb=(\d+)\}/)
  const tmdbId = tmdbMatch ? parseInt(tmdbMatch[1]) : null

  // 尝试从右列种子中找到匹配项（用于获取 qb_config_id）
  const matchedTorrent = rightTorrents.value.find(t => {
    if (tmdbId) return t.name.includes(`{tmdb=${tmdbId}}`)
    return t.name.includes(showName)
  })

  const torrentName = matchedTorrent?.name || `${showName} {tmdb=${tmdbId || ''}}`
  const qbConfigId = rightInstanceId.value || ''

  try {
    await ElMessageBox.confirm(
      `将对【${showName}】执行全自动洗版流程：\n\n` +
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
      const title = res.data.details?.title || showName
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
    ElMessage.error(`自动化洗版失败: ${e.response?.data?.detail || e.message}`)
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
    ElMessage.error(`创建目录失败: ${e.response?.data?.detail || e.message}`)
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
    const errMsg = e.response?.data?.detail || e.message || '移动失败'
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
    ElMessage.error(`删除失败: ${e.response?.data?.detail || e.message}`)
  } finally {
    cd2Deleting.value = false
  }
}

// ==================== 整理工作台 ====================
const orgLoading = ref(false)
const orgResult = ref(null)       // { title, year, season, total_episodes, tmdb_id, resolved_category, ... }
const orgError = ref('')
const orgCD2Status = ref('')      // CD2 双端跳转结果

// Helper: search for matching show folder in a directory listing
const findShowFolder = (files, tmdbId, title) => {
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
const jumpCD2Side = async (side, yearPath) => {
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
    return { ok: false, error: e.response?.data?.detail || e.message }
  }
}

const startOrganize = async (torrent) => {
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
    const tmdbId = res.data.tmdb_id
    const title = res.data.title
    const cat = selectedCategory.value || category || ''

    if (!year || !cat) {
      orgCD2Status.value = (year ? '未识别分类' : '未识别年份') + '，无法自动跳转 CD2'
      return
    }

    // Construct year-level paths for both sides
    const mediaYearPath = CD2_MEDIA_BASE + cat + '/' + year + '/'
    const organizedYearPath = CD2_ORGANIZED_BASE + cat + '/' + year + '/'

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
    orgError.value = e.response?.data?.detail || e.message || '解析失败'
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
onMounted(() => {
  fetchInstances()
  cd2MediaPath.value = cd2MediaRoot.value
  cd2OrganizedPath.value = cd2OrganizedRoot.value
  loadCD2Data()
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
            <span class="col-dot dot-danger"></span>
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
                        class="cc-progress-fill"
                        :style="{ width: (torrent.progress * 100) + '%', background: getProgressColor(torrent.state) }"
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
            <span class="col-dot dot-success"></span>
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
                        class="cc-progress-fill"
                        :style="{ width: (torrent.progress * 100) + '%', background: getProgressColor(torrent.state) }"
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

    <!-- ==================== CD2 网盘目录浏览 (导航版) ==================== -->
    <div class="cd2-section">
      <!-- Section Header -->
      <div class="cd2-section-header">
        <div class="cd2-section-title">
          <span class="col-dot dot-cd2"></span>
          <span>CD2 网盘文件概览</span>
          <span class="cd2-cat-sep">›</span>
            <span class="cd2-cat-tag">{{ selectedCategory || '国产剧' }}</span>
            <span v-if="!selectedCategory" class="cd2-default-hint">（默认）</span>
          <span v-if="!cd2Loading && !cd2Error" class="col-count">
            {{ cd2MediaFiles.length + cd2OrganizedFiles.length }} 项
          </span>
        </div>
        <div class="cd2-header-actions">
          <button class="cd2-refresh-btn" :disabled="cd2Loading" @click="loadCD2Data">
            <el-icon :size="14" :class="{ 'is-loading': cd2Loading }"><Loading /></el-icon>
            {{ cd2Loading ? '加载中...' : '刷新' }}
          </button>
        </div>
      </div>

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
                type="number"
                placeholder="年份..."
                class="cd2-year-input"
                @keyup.enter="jumpToYear"
                @keydown.escape="cd2MediaYearInput = ''"
              />
              <button class="cd2-year-btn" :disabled="!cd2MediaYearInput.trim()" @click="jumpToYear">跳转</button>
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
                    'is-checked': selectedCd2MediaItems.includes(file.fullPathName || file.name)
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
                  <!-- 目录：显示文件数和大小（年份文件夹强制屏蔽） -->
                  <span v-if="file.isDirectory && file.fileCount != null && !isYearFolder(file.name)" class="cd2-dir-stats">
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
                :class="{ 'is-dir': file.isDirectory, 'is-clickable': file.isDirectory }"
                @click="file.isDirectory && enterFolder('organized', file.name)"
              >
                <span class="cd2-file-icon">{{ file.isDirectory ? '📁' : '📄' }}</span>
                <span class="cd2-file-name">{{ file.name }}</span>
                <!-- 目录：显示文件数和大小（年份文件夹强制屏蔽） -->
                <span v-if="file.isDirectory && file.fileCount != null && !isYearFolder(file.name)" class="cd2-dir-stats">
                  {{ file.fileCount }} 文件
                  <template v-if="file.totalSize"> · {{ formatBytes(file.totalSize) }}</template>
                </span>
                <span v-else-if="!file.isDirectory" class="cd2-file-size">{{ formatBytes(file.size) }}</span>
                <span v-if="file.isDirectory" class="cd2-file-arrow">›</span>
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
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  transition: all 0.2s ease;
}
.compact-card:hover {
  border-color: #475569;
  box-shadow: var(--shadow-sm);
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
  border-color: var(--accent-blue);
  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.3);
}
.compact-card.card-right {
  border-left: 3px solid transparent;
}
.compact-card.card-right:hover {
  border-left-color: var(--accent-green);
}
.compact-card.card-right.is-checked {
  border-left-color: var(--accent-purple, #8b5cf6) !important;
  border-color: var(--accent-purple, #8b5cf6);
  box-shadow: 0 0 0 1px rgba(139, 92, 246, 0.3);
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
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
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
  height: 3px;
  background: var(--border-color);
  border-radius: 2px;
  overflow: hidden;
}

.cc-progress-fill {
  height: 100%;
  border-radius: 2px;
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
.cd2-section {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 4px;
  border-top: 1px solid var(--border-color);
  padding-top: 14px;
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

.cd2-cat-tag {
  font-size: 12px;
  font-weight: 600;
  color: var(--accent-purple, #8b5cf6);
  background: rgba(139, 92, 246, 0.12);
  padding: 2px 10px;
  border-radius: var(--radius-full);
}

.cd2-header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}


.cd2-default-hint {
  font-size: 10px;
  color: var(--text-tertiary);
  font-weight: 400;
  font-style: italic;
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
  max-height: 280px;
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
  background: var(--bg-card-hover);
}
.cd2-file-item + .cd2-file-item {
  border-top: 1px solid var(--border-color-subtle, rgba(255,255,255,0.03));
}

/* 文件夹可点击 */
.cd2-file-item.is-clickable {
  cursor: pointer;
}
.cd2-file-item.is-clickable:hover {
  background: var(--accent-blue-soft);
}

.cd2-file-icon {
  font-size: 14px;
  flex-shrink: 0;
  opacity: 0.85;
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
  }

  .cd2-section-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
  }

  .cd2-header-actions {
    width: 100%;
    justify-content: space-between;
  }

  .cd2-split {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .cd2-col-body {
    max-height: 200px;
  }

  .cd2-col-header {
    padding: 6px 10px;
  }

  .cd2-nav-bar {
    gap: 6px;
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
}
</style>
