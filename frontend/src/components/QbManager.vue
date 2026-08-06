<template>
  <div class="qb-manager">
    <!-- ================================================================== -->
    <!--                         种子管理                                    -->
    <!-- ================================================================== -->
    <div class="torrent-tab">
      <!-- Filter Bar — 胶囊形过滤器 -->
      <div class="filter-bar">
        <el-select
          v-model="selectedQb"
          placeholder="选择实例"
          class="filter-select"
          @change="fetchQbData"
        >
          <el-option
            v-for="item in qbConfigs"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>

        <div class="search-box">
          <el-icon :size="16" class="search-icon"><Search /></el-icon>
          <input
            v-model="filterName"
            type="text"
            placeholder="搜索种子名称..."
            class="search-input"
            @keyup.enter="fetchTorrents"
          />
          <el-icon
            v-if="filterName"
            :size="14"
            class="search-clear"
            @click="filterName = ''; fetchTorrents()"
          ><Close /></el-icon>
        </div>

        <el-select
          v-model="filterTag"
          placeholder="标签"
          class="filter-select filter-select-sm"
          clearable
          @change="fetchTorrents"
        >
          <el-option v-for="tag in currentTags" :key="tag" :label="tag" :value="tag" />
        </el-select>

        <el-select
          v-model="filterCategory"
          placeholder="分类"
          class="filter-select filter-select-sm"
          clearable
          @change="fetchTorrents"
        >
          <el-option
            v-for="cat in currentCategories"
            :key="cat"
            :label="cat"
            :value="cat"
          />
        </el-select>

        <button class="btn-icon" @click="fetchTorrents" :disabled="loading" title="刷新">
          <el-icon :size="18"><Refresh /></el-icon>
        </button>

        <!-- Selection Actions -->
        <template v-if="selectedHashes.length">
          <span class="select-count">已选 {{ selectedHashes.length }} 项</span>
          <button class="btn-pill btn-pill-all" @click="selectAll">全选</button>
          <button class="btn-pill btn-pill-clear" @click="clearSelect">取消</button>
          <button class="btn-pill btn-pill-danger" @click="batchDelete(false)">批量删除</button>
          <button class="btn-pill btn-pill-danger" @click="batchDelete(true)">删除+文件</button>
        </template>
      </div>

      <!-- ==================== Card Grid ==================== -->
      <div v-loading="loading" class="card-list" :class="{ 'is-empty': !loading && !torrents.length }">
        <div
          v-for="row in torrents"
          :key="row.hash"
          class="torrent-card"
          :class="{ selected: selectedHashes.includes(row.hash) }"
        >
          <!-- Left: state icon circle -->
          <div class="card-icon-col">
            <div
              class="card-icon-circle"
              :style="{ background: getStateBg(row.state) }"
            >
              <el-icon :size="20" :color="getStateColor(row.state)">
                <component :is="getStateIcon(row.state)" />
              </el-icon>
            </div>
          </div>

          <!-- Center: info -->
          <div class="card-body">
            <div class="card-name" :title="row.name">{{ row.name }}</div>

            <div class="card-meta-row">
              <span class="meta-size">{{ formatBytes(row.size) }}</span>
              <span class="meta-sep">·</span>
              <span class="meta-progress-text">{{ Math.round(row.progress * 100) }}%</span>
              <span class="meta-sep">·</span>
              <span class="meta-ratio">↑{{ row.ratio.toFixed(2) }}</span>
            </div>

            <div class="card-progress">
              <div class="progress-track h-1.5">
                <div
                  class="progress-fill bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.8)]"
                  :style="{ width: (row.progress * 100) + '%' }"
                ></div>
              </div>
            </div>

            <div class="card-tag-row">
              <span
                class="tag-chip tag-state"
                :style="{
                  background: getStateBg(row.state),
                  color: getStateColor(row.state)
                }"
              >
                {{ formatState(row.state) }}
              </span>
              <span v-if="row.category" class="tag-chip tag-category">
                <el-icon :size="12"><Folder /></el-icon>
                {{ row.category }}
              </span>
              <span
                v-for="tag in parseTags(row.tags)"
                :key="tag"
                class="tag-chip tag-hash"
              >#{{ tag }}</span>
            </div>
          </div>

          <!-- Right: checkbox + actions -->
          <div class="card-actions-col">
            <div
              class="card-checkbox"
              :class="{ checked: selectedHashes.includes(row.hash) }"
              @click.stop="toggleSelect(row.hash)"
            >
              <el-icon v-if="selectedHashes.includes(row.hash)" :size="14"><Check /></el-icon>
            </div>
            <div class="card-action-btns">
              <button class="ac-btn" @click.stop="viewFiles(row)" title="查看文件">
                <el-icon :size="16"><View /></el-icon>
              </button>
              <button class="ac-btn" @click.stop="deleteOne(row, false)" title="删除种子">
                <el-icon :size="16"><Delete /></el-icon>
              </button>
              <button class="ac-btn ac-btn-warn" @click.stop="deleteOne(row, true)" title="删除种子及文件">
                <el-icon :size="16"><DeleteFilled /></el-icon>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Empty state: no instance selected -->
      <div v-if="!selectedQb" class="empty-state">
        <div class="empty-icon-circle">
          <el-icon :size="36"><Monitor /></el-icon>
        </div>
        <p class="empty-title">未选择实例</p>
        <p class="empty-desc">请在上方选择一个 qBittorrent 实例</p>
      </div>

      <!-- Empty state: no torrents -->
      <div v-if="selectedQb && !loading && !torrents.length" class="empty-state">
        <div class="empty-icon-circle">
          <el-icon :size="36"><FolderOpened /></el-icon>
        </div>
        <p class="empty-title">暂无种子</p>
        <p class="empty-desc">当前实例没有符合条件的种子任务</p>
      </div>

      <!-- Pagination -->
      <div class="pagination-bar" v-if="torrentTotal > 0">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[20, 50, 100, 200]"
          :total="torrentTotal"
          layout="total, sizes, prev, pager, next"
          small
          background
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </div>

    <!-- ================================================================== -->
    <!--                         文件列表弹窗                                -->
    <!-- ================================================================== -->
    <el-dialog v-model="fileDialogVisible" title="文件列表" width="800px" class="file-dialog">
      <el-table :data="fileList" v-loading="filesLoading" height="500px" class="file-table">
        <el-table-column prop="name" label="文件名" min-width="360">
          <template #default="{ row }">
            <el-tooltip
              :content="row.name"
              placement="bottom"
              effect="dark"
              trigger="click"
              popper-class="file-name-popper"
            >
              <span class="cell-name-text">{{ row.name }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="size" label="大小" width="120">
          <template #default="{ row }">
            <span class="cell-size">{{ formatBytes(row.size) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="progress" label="进度" width="180">
          <template #default="{ row }">
            <div class="cell-progress">
              <el-progress
                :percentage="Math.round(row.progress * 100)"
                :stroke-width="6"
                :show-text="false"
              />
              <span class="cell-pct">{{ Math.round(row.progress * 100) }}%</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="100">
          <template #default="{ row }">
            <span class="cell-priority">{{ row.priority === 0 ? '忽略' : (row.priority === 6 ? '高' : '正常') }}</span>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="fileDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Search, Close, Refresh, Check,
  Folder, View, Delete, DeleteFilled, Monitor,
  FolderOpened, Upload, Download, VideoPause,
  Clock, WarningFilled, Loading, QuestionFilled,
  Remove
} from '@element-plus/icons-vue'
import axios from 'axios'

// ==================== Type Definitions ====================

/** qBittorrent 实例配置 */
interface QbConfig {
  id: string
  name: string
  host: string
  username: string
  password?: string
  active: boolean
}

/** qBittorrent 实例基础数据（标签/分类） */
interface QbData {
  id: string
  name: string
  tags: string[]
  categories: string[]
}

/** 种子信息 */
interface Torrent {
  hash: string
  name: string
  size: number
  progress: number
  state: string
  category: string
  tags: string | string[]
  added_on: number
  completion_on: number
  ratio: number
  upspeed: number
  dlspeed: number
  save_path: string
}

/** 种子文件信息 */
interface TorrentFile {
  name: string
  size: number
  progress: number
  priority: number
  is_seed: boolean
}

// ==================== Reactive State ====================
const qbConfigs = ref<QbConfig[]>([])
const loading = ref(false)
const selectedQb = ref('')
const torrents = ref<Torrent[]>([])
const torrentTotal = ref(0)
const currentPage = ref(1)
const pageSize = ref(50)
const selectedHashes = ref<string[]>([])
const currentTags = ref<string[]>([])
const currentCategories = ref<string[]>([])
const filterTag = ref('')
const filterCategory = ref('')
const filterName = ref('')

const fileDialogVisible = ref(false)
const fileList = ref<TorrentFile[]>([])
const filesLoading = ref(false)

// ==================== State Mapping ====================
const STATE_MAP: Record<string, string> = {
  'stalledUP': '做种中',
  'uploading': '上传中',
  'downloading': '下载中',
  'stalledDL': '等待下载',
  'pausedDL': '暂停下载',
  'pausedUP': '暂停上传',
  'queuedDL': '排队下载',
  'queuedUP': '排队上传',
  'checkingUP': '校验中',
  'checkingDL': '校验中',
  'error': '错误',
  'missingFiles': '文件丢失',
  'metaDL': '获取元数据',
  'moving': '移动中',
  'unknown': '未知'
}

const formatState = (state: string) => STATE_MAP[state] || state

// State → icon
const getStateIcon = (state: string) => {
  if (['stalledUP', 'uploading'].includes(state)) return Upload
  if (['downloading', 'metaDL'].includes(state)) return Download
  if (['error', 'missingFiles'].includes(state)) return WarningFilled
  if (['pausedDL', 'pausedUP'].includes(state)) return VideoPause
  if (['queuedDL', 'queuedUP'].includes(state)) return Clock
  if (['checkingUP', 'checkingDL'].includes(state)) return Loading
  if (state === 'moving') return Remove
  return QuestionFilled
}

// State → accent color
const getStateColor = (state: string) => {
  if (['stalledUP', 'uploading'].includes(state)) return '#10b981'
  if (['downloading', 'metaDL'].includes(state)) return '#3b82f6'
  if (['error', 'missingFiles'].includes(state)) return '#ef4444'
  if (['pausedDL', 'pausedUP'].includes(state)) return '#f59e0b'
  if (['queuedDL', 'queuedUP'].includes(state)) return '#f97316'
  if (['checkingUP', 'checkingDL'].includes(state)) return '#8b5cf6'
  if (state === 'moving') return '#06b6d4'
  return '#64748b'
}

// State → icon circle background (with alpha)
const getStateBg = (state: string) => {
  const color = getStateColor(state)
  // Convert hex to rgba with 0.15 alpha
  const r = parseInt(color.slice(1, 3), 16)
  const g = parseInt(color.slice(3, 5), 16)
  const b = parseInt(color.slice(5, 7), 16)
  return `rgba(${r}, ${g}, ${b}, 0.18)`
}

// Parse tags string to array
const parseTags = (tags: string | string[]): string[] => {
  if (!tags) return []
  if (Array.isArray(tags)) return tags
  return tags.split(',').map(t => t.trim()).filter(Boolean)
}

// ==================== Selection ====================
const toggleSelect = (hash: string) => {
  const idx = selectedHashes.value.indexOf(hash)
  if (idx === -1) {
    selectedHashes.value.push(hash)
  } else {
    selectedHashes.value.splice(idx, 1)
  }
}

const selectAll = () => {
  selectedHashes.value = torrents.value.map(t => t.hash)
}

const clearSelect = () => {
  selectedHashes.value = []
}

// ==================== File Viewer ====================
const viewFiles = async (row: Torrent) => {
  fileList.value = []
  fileDialogVisible.value = true
  filesLoading.value = true
  try {
    const res = await axios.get<TorrentFile[]>(`/api/qb/${selectedQb.value}/torrents/${row.hash}/files`)
    fileList.value = res.data
  } catch (err) {
    ElMessage.error('获取文件列表失败')
  } finally {
    filesLoading.value = false
  }
}

// ==================== Mobile Detection ====================
const isMobile = () => window.innerWidth < 768

// ==================== Data Fetching ====================
const fetchConfigs = async (autoLoad = false) => {
  try {
    const res = await axios.get<QbConfig[]>('/api/qb/configs')
    qbConfigs.value = res.data
    if (autoLoad && qbConfigs.value.length && !selectedQb.value) {
      selectedQb.value = qbConfigs.value[0].id
      fetchQbData()
    }
  } catch (err) {
    ElMessage.error('获取配置失败')
  }
}

const fetchQbData = async () => {
  if (!selectedQb.value) return
  try {
    const res = await axios.get<QbData[]>('/api/qb/data')
    const data = res.data.find(d => d.id === selectedQb.value)
    if (data) {
      currentTags.value = data.tags
      currentCategories.value = data.categories
    }
    fetchTorrents()
  } catch (err) {
    console.error(err)
  }
}

const fetchTorrents = async () => {
  if (!selectedQb.value) return
  loading.value = true
  try {
    const params: Record<string, number | string> = {
      page: currentPage.value,
      page_size: pageSize.value
    }
    if (filterTag.value) params.tag = filterTag.value
    if (filterName.value) params.keyword = filterName.value
    if (filterCategory.value) params.category = filterCategory.value

    const res = await axios.get<{ torrents: Torrent[]; total: number }>(`/api/qb/${selectedQb.value}/torrents`, { params })
    torrents.value = res.data.torrents || res.data
    torrentTotal.value = res.data.total || 0
    // Clear selection on refresh
    selectedHashes.value = []
  } catch (err) {
    ElMessage.error('获取种子列表失败')
  } finally {
    loading.value = false
  }
}

const handlePageChange = (page: number) => {
  currentPage.value = page
  fetchTorrents()
}

const handleSizeChange = (size: number) => {
  pageSize.value = size
  currentPage.value = 1
  fetchTorrents()
}

// ==================== Delete ====================
const batchDelete = async (deleteFiles: boolean) => {
  if (!selectedHashes.value.length) return
  try {
    await axios.post(`/api/qb/${selectedQb.value}/torrents/delete`, {
      hashes: selectedHashes.value,
      delete_files: deleteFiles
    })
    ElMessage.success('删除成功')
    selectedHashes.value = []
    fetchTorrents()
  } catch (err) {
    ElMessage.error('删除失败')
  }
}

const deleteOne = (row: Torrent, deleteFiles: boolean) => {
  ElMessageBox.confirm(
    `确定删除种子「${row.name}」${deleteFiles ? '及其文件' : ''} 吗？`,
    '提示',
    { type: 'warning' }
  ).then(async () => {
    try {
      await axios.post(`/api/qb/${selectedQb.value}/torrents/delete`, {
        hashes: [row.hash],
        delete_files: deleteFiles
      })
      ElMessage.success('删除成功')
      fetchTorrents()
    } catch (err) {
      ElMessage.error('删除失败')
    }
  })
}

const formatBytes = (bytes: number, decimals = 2) => {
  if (!+bytes) return '0 Bytes'
  const k = 1024
  const dm = decimals < 0 ? 0 : decimals
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

onMounted(() => {
  fetchConfigs(!isMobile())
})
</script>

<style scoped lang="postcss">
/* ==================== 页面根容器 ==================== */
.qb-manager {
  padding: 20px 24px;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
  background: radial-gradient(ellipse 70% 45% at 90% -5%, rgba(59, 130, 246, 0.08), transparent 65%),
              radial-gradient(ellipse 55% 40% at 0% 100%, rgba(16, 185, 129, 0.06), transparent 60%),
              var(--bg-primary);
}

/* ==================== 种子管理 ==================== */
.torrent-tab {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 过滤器 */
.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.filter-select {
  width: 190px;
}
.filter-select-sm { width: 140px; }

.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 180px;
  padding: 8px 14px;
  background: rgba(2, 6, 23, 0.45);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.search-box:focus-within {
  border-color: rgba(59, 130, 246, 0.4);
  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.25), 0 0 14px rgba(59, 130, 246, 0.2);
}
.search-icon { color: #64748b; }
.search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: #f1f5f9;
  font-size: 13px;
  font-family: inherit;
}
.search-input::placeholder { color: #475569; }
.search-clear {
  color: #64748b;
  cursor: pointer;
}
.search-clear:hover { color: #f87171; }

.btn-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.05);
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.2s;
}
.btn-icon:hover:not(:disabled) {
  color: #93c5fd;
  background: rgba(59, 130, 246, 0.12);
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.3);
}
.btn-icon:disabled { opacity: 0.4; cursor: not-allowed; }

/* 批量操作药丸按钮 */
.select-count {
  font-size: 12px;
  color: #93c5fd;
  font-weight: 600;
}
.btn-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 15px;
  border-radius: 999px;
  border: 1px solid;
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
}
.btn-pill-all {
  color: #93c5fd;
  border-color: rgba(59, 130, 246, 0.35);
  background: rgba(59, 130, 246, 0.12);
}
.btn-pill-all:hover {
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.35);
}
.btn-pill-clear {
  color: #94a3b8;
  border-color: rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.05);
}
.btn-pill-clear:hover { color: #e2e8f0; background: rgba(255, 255, 255, 0.1); }
.btn-pill-danger {
  color: #f87171;
  border-color: rgba(239, 68, 68, 0.35);
  background: rgba(239, 68, 68, 0.1);
}
.btn-pill-danger:hover {
  box-shadow: 0 0 12px rgba(239, 68, 68, 0.35);
}
.btn-pill-primary {
  color: #fff;
  border: none;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  box-shadow: 0 4px 16px rgba(59, 130, 246, 0.35);
}
.btn-pill-primary:hover {
  box-shadow: 0 8px 24px rgba(99, 102, 241, 0.55);
}

/* ==================== 种子卡片 ==================== */
.card-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 2px;
}

.torrent-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 13px 16px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  backdrop-filter: blur(14px) saturate(140%);
  -webkit-backdrop-filter: blur(14px) saturate(140%);
  transition: border-color 0.22s ease, box-shadow 0.22s ease, background 0.22s ease;
}
.torrent-card:hover {
  background: rgba(255, 255, 255, 0.07);
  border-color: rgba(255, 255, 255, 0.16);
}
.torrent-card.selected {
  border-color: rgba(59, 130, 246, 0.5);
  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.2), 0 8px 26px -14px rgba(59, 130, 246, 0.4);
  background: rgba(59, 130, 246, 0.05);
}

.card-icon-col { flex-shrink: 0; }

.card-icon-circle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  border-radius: 14px;
}

.card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* 资源名称 — 主标题纯白 */
.card-name {
  font-size: 14.5px;
  font-weight: 700;
  color: #ffffff;
  line-height: 1.35;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-meta-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #64748b;
}
.meta-size { color: #94a3b8; font-weight: 500; }
.meta-progress-text { color: #34d399; font-weight: 600; font-family: 'JetBrains Mono', monospace; }
.meta-ratio { color: #64748b; }
.meta-sep { color: #334155; }

/* 细长霓虹绿进度条 */
.card-progress { width: 100%; }

.progress-track {
  width: 100%;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.07);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #34d399, #10b981);
  transition: width 0.4s ease;
}

.card-tag-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

/* 状态标签 — 带边框低透明度发光药丸 */
.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
  background: rgba(255, 255, 255, 0.05);
}
.tag-state {
  border-color: transparent;
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.18);
}
.tag-category {
  color: #93c5fd;
  border-color: rgba(59, 130, 246, 0.28);
  background: rgba(59, 130, 246, 0.09);
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.15);
}
.tag-hash {
  color: #a78bfa;
  border-color: rgba(139, 92, 246, 0.28);
  background: rgba(139, 92, 246, 0.09);
  box-shadow: 0 0 10px rgba(139, 92, 246, 0.15);
}

.card-actions-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.card-checkbox {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 7px;
  border: 1.5px solid rgba(148, 163, 184, 0.35);
  color: #fff;
  cursor: pointer;
  transition: all 0.2s;
}
.card-checkbox.checked {
  background: #3b82f6;
  border-color: #3b82f6;
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.5);
}

.card-action-btns {
  display: flex;
  gap: 6px;
}

/* 通用小图标按钮 */
.ac-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  border: 1px solid transparent;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  transition: all 0.2s;
}
.ac-btn:hover {
  color: #93c5fd;
  background: rgba(59, 130, 246, 0.12);
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.25);
}
.ac-btn-warn:hover {
  color: #f87171;
  background: rgba(239, 68, 68, 0.12);
  box-shadow: 0 0 10px rgba(239, 68, 68, 0.25);
}

/* ==================== 空状态 ==================== */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  gap: 8px;
  border: 1px dashed rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.02);
}
.empty-icon-circle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border-radius: 20px;
  margin-bottom: 6px;
  color: #60a5fa;
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid rgba(59, 130, 246, 0.2);
  box-shadow: 0 0 20px rgba(59, 130, 246, 0.15);
}
.empty-title { font-size: 15px; font-weight: 600; color: #cbd5e1; margin: 0; }
.empty-desc { font-size: 12.5px; color: #64748b; margin: 0; }

/* ==================== 分页 ==================== */
.pagination-bar {
  display: flex;
  justify-content: center;
  padding: 6px 0 2px;
  flex-shrink: 0;
}
.pagination-bar :deep(.el-pagination) {
  --el-pagination-bg-color: rgba(255, 255, 255, 0.05);
  --el-pagination-button-bg-color: rgba(255, 255, 255, 0.05);
  --el-pagination-hover-color: #60a5fa;
  --el-pagination-text-color: #94a3b8;
}
.pagination-bar :deep(.el-pager li) {
  color: #94a3b8;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  margin: 0 2px;
}
.pagination-bar :deep(.el-pager li.is-active) {
  background: #3b82f6;
  color: #fff;
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.5);
}

/* ==================== Element Plus overrides ==================== */
:deep(.el-switch) {
  --el-switch-on-color: #3b82f6;
  --el-switch-off-color: rgba(148, 163, 184, 0.25);
  --el-switch-border-color: transparent;
}
:deep(.el-switch.is-checked .el-switch__core) {
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.5);
}

:deep(.el-select__wrapper) {
  background: rgba(2, 6, 23, 0.5);
  border-radius: 999px;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.14) !important;
  transition: box-shadow 0.2s;
}
:deep(.el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px #3b82f6, 0 0 10px rgba(59, 130, 246, 0.4) !important;
}
:deep(.el-select__selected-item) { color: #f1f5f9; }
:deep(.el-select-dropdown) {
  --el-select-dropdown-bg-color: #0f172a;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 12px;
  overflow: hidden; /* 强制裁切，防直角元素溢出圆角 */
}
:deep(.el-select-dropdown__item) { color: #94a3b8; }
:deep(.el-select-dropdown__item.hover),
:deep(.el-select-dropdown__item:hover) { background: rgba(59, 130, 246, 0.12); color: #93c5fd; }
:deep(.el-select-dropdown__item.is-selected) { color: #60a5fa; font-weight: 600; }

/* 弹窗 — 全息毛玻璃（文件列表 / 新增编辑实例） */
:deep(.el-overlay) {
  @apply bg-black/60 backdrop-blur-sm;
}
:deep(.el-dialog) {
  --el-dialog-bg-color: transparent;
  @apply bg-[#0B1120]/80 backdrop-blur-2xl border border-white/10 rounded-2xl;
  box-shadow: 0 0 30px rgba(59, 130, 246, 0.15), inset 0 1px 0 rgba(255, 255, 255, 0.06);
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
:deep(.el-dialog__body) {
  color: #cbd5e1;
}
:deep(.el-dialog__footer) {
  background: transparent;
  border-top: none;
  padding-top: 8px;
}
:deep(.el-dialog__headerbtn) {
  color: #64748b;
  transition: color 0.25s ease, transform 0.25s ease, text-shadow 0.25s ease;
}
:deep(.el-dialog__headerbtn:hover) {
  color: #3b82f6;
  transform: rotate(90deg);
  text-shadow: 0 0 8px rgba(59, 130, 246, 0.6);
}
:deep(.el-form-item__label) { color: #94a3b8; }
:deep(.el-input__wrapper) {
  background: rgba(0, 0, 0, 0.25);
  border-radius: 10px;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.12) !important;
}
:deep(.el-input__wrapper.is-focus) {
  background: rgba(59, 130, 246, 0.06);
  box-shadow: 0 0 0 1px #3b82f6, 0 0 10px rgba(59, 130, 246, 0.4) !important;
}
:deep(.el-input__inner) { color: #f1f5f9; }
:deep(.el-input__inner::placeholder) { color: #475569; }

/* ==================== 文件列表 — 隐形排版表格 ==================== */
/* 注意：class="file-table" 直接加在 el-table 根节点上（同一个元素同时持有
   .el-table 与 .file-table）。因此穿透选择器里不能再写 .el-table 作为中间
   祖先（`.file-table .el-table` 要求 .el-table 是 .file-table 的后代，永不命中），
   一律以 .file-table 为根，只写其后代选择器。 */
.file-table {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: transparent;
  --el-table-row-hover-bg-color: transparent;
  --el-table-border-color: transparent;
}
/* 剥离所有默认背景 */
.file-table :deep(tr),
.file-table :deep(th.el-table__cell),
.file-table :deep(td.el-table__cell) {
  background-color: transparent !important;
}
/* 去除纵向边框，底边替换为极细半透明线 */
.file-table :deep(th.el-table__cell),
.file-table :deep(td.el-table__cell) {
  border-bottom: 1px solid rgba(255, 255, 255, 0.05) !important;
}
/* 隐藏表格底部默认灰线 */
.file-table :deep(.el-table__inner-wrapper::before) { display: none; }
/* Hover 提亮 */
.file-table :deep(tbody tr:hover > td.el-table__cell) {
  background-color: rgba(255, 255, 255, 0.03) !important;
}
/* 单元格固定高度 + 垂直居中（修复错位的关键） */
.file-table :deep(td.el-table__cell) {
  height: 48px !important;
  padding: 0 8px !important;
  vertical-align: middle;
}
/* 单元格内容统一：去 EP 默认 12px 内边距 + 单行 nowrap + 省略号。
   .cell 自身渲染省略号，保证列表始终单行紧凑，不会多行撑高行高；
   全名改为「点击触发」展示。 */
.file-table :deep(td.el-table__cell .cell) {
  padding: 0 !important;
  line-height: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 单元格内容 flex 容器：垂直居中（进度条 + 百分比） */
.cell-progress {
  display: flex;
  align-items: center;
  min-width: 0;
}
/* 文件名 — 行内文本，不自身裁剪；点击查看全名（cursor 提示可点） */
.cell-name-text {
  color: #e2e8f0;
  font-size: 13px;
  cursor: pointer;
}
/* 大小 / 进度百分比 — 等宽极客字体 */
.cell-size {
  @apply font-mono text-slate-300;
  font-size: 12px;
}
.cell-pct {
  @apply font-mono text-slate-300;
  font-size: 12px;
  min-width: 34px;
  text-align: right;
}
.cell-priority {
  font-size: 12px;
  color: #94a3b8;
}
/* 高科技进度条 — 电光蓝细条 + 发光 */
.file-dialog :deep(.el-progress) { flex: 1; min-width: 0; }
.file-dialog :deep(.el-progress-bar__outer) { background-color: rgba(255, 255, 255, 0.08); }
.file-dialog :deep(.el-progress-bar__inner) {
  background: linear-gradient(90deg, #2563eb, #3b82f6);
  box-shadow: 0 0 8px rgba(59, 130, 246, 0.6);
}

/* ==================== 移动端响应式 ==================== */
@media (max-width: 768px) {
  .qb-manager {
    padding: 10px 12px 32px;
  }
  .filter-bar { gap: 8px; }
  .filter-select { width: 100%; }
  .filter-select-sm { width: calc(50% - 4px); }
  .search-box { min-width: 100%; }
  .torrent-card { flex-wrap: wrap; }
  .card-actions-col { flex-direction: row; justify-content: flex-end; width: 100%; }
}
</style>
