<template>
  <div class="qb-manager">
    <!-- ==================== Custom Tab Bar ==================== -->
    <div class="tab-bar">
      <button
        :class="['tab-btn', { active: activeTab === 'torrents' }]"
        @click="activeTab = 'torrents'"
      >
        <el-icon :size="16"><VideoPlay /></el-icon>
        <span>种子管理</span>
      </button>
      <button
        :class="['tab-btn', { active: activeTab === 'configs' }]"
        @click="activeTab = 'configs'"
      >
        <el-icon :size="16"><Setting /></el-icon>
        <span>实例配置</span>
      </button>
    </div>

    <!-- ================================================================== -->
    <!--                         种子管理 Tab                                -->
    <!-- ================================================================== -->
    <div v-show="activeTab === 'torrents'" class="torrent-tab">
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
              <div class="progress-track">
                <div
                  class="progress-fill"
                  :style="{
                    width: (row.progress * 100) + '%',
                    background: getProgressColor(row.state)
                  }"
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
    <!--                         实例配置 Tab                                -->
    <!-- ================================================================== -->
    <div v-show="activeTab === 'configs'" class="configs-tab">
      <div class="configs-header">
        <h3 class="configs-title">qBittorrent 实例列表</h3>
        <button class="btn-pill btn-pill-primary" @click="showAddDialog">
          <el-icon :size="16"><Plus /></el-icon>
          新增实例
        </button>
      </div>

      <div class="config-cards">
        <div v-for="cfg in qbConfigs" :key="cfg.id" class="config-card">
          <div class="cfg-left">
            <div class="cfg-icon-circle" :class="{ active: cfg.active }">
              <el-icon :size="20"><Monitor /></el-icon>
            </div>
          </div>
          <div class="cfg-body">
            <div class="cfg-name">{{ cfg.name }}</div>
            <div class="cfg-url">{{ cfg.host }}</div>
            <div class="cfg-user">{{ cfg.username || '未设置用户名' }}</div>
          </div>
          <div class="cfg-right">
            <el-switch
              v-model="cfg.active"
              class="cfg-switch"
              @change="updateConfig(cfg)"
            />
            <div class="cfg-actions">
              <button class="ac-btn" @click.stop="editConfig(cfg)" title="编辑">
                <el-icon :size="16"><Edit /></el-icon>
              </button>
              <el-popconfirm
                title="确定删除该配置吗？"
                @confirm="deleteConfig(cfg.id)"
              >
                <template #reference>
                  <button class="ac-btn ac-btn-warn" title="删除">
                    <el-icon :size="16"><Delete /></el-icon>
                  </button>
                </template>
              </el-popconfirm>
            </div>
          </div>
        </div>

        <div v-if="!qbConfigs.length" class="empty-state">
          <div class="empty-icon-circle">
            <el-icon :size="36"><Setting /></el-icon>
          </div>
          <p class="empty-title">暂无实例</p>
          <p class="empty-desc">点击上方按钮新增 qBittorrent 实例配置</p>
        </div>
      </div>
    </div>

    <!-- ================================================================== -->
    <!--                         文件列表弹窗                                -->
    <!-- ================================================================== -->
    <el-dialog v-model="fileDialogVisible" title="文件列表" width="800px" class="file-dialog">
      <el-table :data="fileList" v-loading="filesLoading" height="500px">
        <el-table-column prop="name" label="文件名" min-width="400" show-overflow-tooltip />
        <el-table-column prop="size" label="大小" width="120">
          <template #default="{ row }">
            {{ formatBytes(row.size) }}
          </template>
        </el-table-column>
        <el-table-column prop="progress" label="进度" width="150">
          <template #default="{ row }">
            <el-progress :percentage="Math.round(row.progress * 100)" />
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="100">
          <template #default="{ row }">
            {{ row.priority === 0 ? '忽略' : (row.priority === 6 ? '高' : '正常') }}
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="fileDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- ================================================================== -->
    <!--                         配置弹窗                                    -->
    <!-- ================================================================== -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEdit ? '编辑实例' : '新增实例'"
      width="500px"
    >
      <el-form :model="currentConfig" label-width="100px">
        <el-form-item label="名称" required>
          <el-input v-model="currentConfig.name" placeholder="例如: 家中主下载机" />
        </el-form-item>
        <el-form-item label="地址" required>
          <el-input v-model="currentConfig.host" placeholder="http://192.168.1.10:8080" />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="currentConfig.username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="currentConfig.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="激活">
          <el-switch v-model="currentConfig.active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveQbConfig">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  VideoPlay, Setting, Search, Close, Refresh, Check,
  Folder, View, Delete, DeleteFilled, Monitor,
  FolderOpened, Plus, Edit, Upload, Download, VideoPause,
  Clock, WarningFilled, Loading, QuestionFilled, CircleCheck,
  Remove
} from '@element-plus/icons-vue'
import axios from 'axios'

// ==================== Reactive State ====================
const activeTab = ref('torrents')
const qbConfigs = ref([])
const loading = ref(false)
const selectedQb = ref('')
const torrents = ref([])
const torrentTotal = ref(0)
const currentPage = ref(1)
const pageSize = ref(50)
const selectedHashes = ref([])
const currentTags = ref([])
const currentCategories = ref([])
const filterTag = ref('')
const filterCategory = ref('')
const filterName = ref('')

const dialogVisible = ref(false)
const isEdit = ref(false)
const currentConfig = ref({
  name: '',
  host: '',
  username: 'admin',
  password: '',
  active: true
})

const fileDialogVisible = ref(false)
const fileList = ref([])
const filesLoading = ref(false)

// ==================== State Mapping ====================
const STATE_MAP = {
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

const formatState = (state) => STATE_MAP[state] || state

// State → icon
const getStateIcon = (state) => {
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
const getStateColor = (state) => {
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
const getStateBg = (state) => {
  const color = getStateColor(state)
  // Convert hex to rgba with 0.15 alpha
  const r = parseInt(color.slice(1, 3), 16)
  const g = parseInt(color.slice(3, 5), 16)
  const b = parseInt(color.slice(5, 7), 16)
  return `rgba(${r}, ${g}, ${b}, 0.18)`
}

// Progress bar color
const getProgressColor = (state) => {
  if (['stalledUP', 'uploading'].includes(state)) return 'linear-gradient(90deg, #10b981, #34d399)'
  if (['downloading', 'metaDL'].includes(state)) return 'linear-gradient(90deg, #3b82f6, #60a5fa)'
  if (['error', 'missingFiles'].includes(state)) return 'linear-gradient(90deg, #ef4444, #f87171)'
  if (['pausedDL', 'pausedUP'].includes(state)) return 'linear-gradient(90deg, #f59e0b, #fbbf24)'
  if (['queuedDL', 'queuedUP'].includes(state)) return 'linear-gradient(90deg, #f97316, #fb923c)'
  if (['checkingUP', 'checkingDL'].includes(state)) return 'linear-gradient(90deg, #8b5cf6, #a78bfa)'
  return 'linear-gradient(90deg, #64748b, #94a3b8)'
}

// Parse tags string to array
const parseTags = (tags) => {
  if (!tags) return []
  if (Array.isArray(tags)) return tags
  return tags.split(',').map(t => t.trim()).filter(Boolean)
}

// ==================== Selection ====================
const toggleSelect = (hash) => {
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
const viewFiles = async (row) => {
  fileList.value = []
  fileDialogVisible.value = true
  filesLoading.value = true
  try {
    const res = await axios.get(`/api/qb/${selectedQb.value}/torrents/${row.hash}/files`)
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
    const res = await axios.get('/api/qb/configs')
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
    const res = await axios.get('/api/qb/data')
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
    const params = {
      page: currentPage.value,
      page_size: pageSize.value
    }
    if (filterTag.value) params.tag = filterTag.value
    if (filterName.value) params.keyword = filterName.value
    if (filterCategory.value) params.category = filterCategory.value

    const res = await axios.get(`/api/qb/${selectedQb.value}/torrents`, { params })
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

const handlePageChange = (page) => {
  currentPage.value = page
  fetchTorrents()
}

const handleSizeChange = (size) => {
  pageSize.value = size
  currentPage.value = 1
  fetchTorrents()
}

// ==================== Delete ====================
const batchDelete = async (deleteFiles) => {
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

const deleteOne = (row, deleteFiles) => {
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

// ==================== Config CRUD ====================
const showAddDialog = () => {
  isEdit.value = false
  currentConfig.value = { name: '', host: '', username: 'admin', password: '', active: true }
  dialogVisible.value = true
}

const editConfig = (row) => {
  isEdit.value = true
  currentConfig.value = { ...row }
  dialogVisible.value = true
}

const saveQbConfig = async () => {
  try {
    if (isEdit.value) {
      await axios.put(`/api/qb/configs/${currentConfig.value.id}`, currentConfig.value)
    } else {
      await axios.post('/api/qb/configs', currentConfig.value)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    fetchConfigs()
  } catch (err) {
    ElMessage.error('保存失败')
  }
}

const updateConfig = async (row) => {
  try {
    await axios.put(`/api/qb/configs/${row.id}`, row)
    ElMessage.success('更新成功')
  } catch (err) {
    ElMessage.error('更新失败')
    fetchConfigs()
  }
}

const deleteConfig = async (id) => {
  try {
    await axios.delete(`/api/qb/configs/${id}`)
    ElMessage.success('删除成功')
    fetchConfigs()
  } catch (err) {
    ElMessage.error('删除失败')
  }
}

const formatBytes = (bytes, decimals = 2) => {
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

<style scoped>
/* ==================== Layout ==================== */
.qb-manager {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 8px 16px;
}

/* ==================== Tab Bar ==================== */
.tab-bar {
  display: flex;
  gap: 4px;
  padding: 4px;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  margin-bottom: 12px;
  flex-shrink: 0;
}

.tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 16px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text-tertiary);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
  font-family: inherit;
}
.tab-btn:hover {
  color: var(--text-secondary);
  background: var(--bg-card-hover);
}
.tab-btn.active {
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(59, 130, 246, 0.15);
}

/* ==================== Torrent Tab ==================== */
.torrent-tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ==================== Filter Bar ==================== */
.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  flex-shrink: 0;
}

.filter-select {
  width: 140px;
}

.filter-select-sm {
  width: 110px;
}

/* Search box — custom pill input */
.search-box {
  position: relative;
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 180px;
  max-width: 280px;
}

.search-icon {
  position: absolute;
  left: 12px;
  color: var(--text-tertiary);
  pointer-events: none;
}

.search-input {
  width: 100%;
  padding: 8px 32px 8px 36px;
  background: var(--bg-input);
  border: none;
  border-radius: var(--radius-full);
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  outline: none;
  transition: box-shadow 0.2s, background 0.2s;
  box-shadow: 0 0 0 1px var(--border-color);
}
.search-input::placeholder {
  color: var(--text-tertiary);
}
.search-input:focus {
  box-shadow: 0 0 0 2px var(--accent-blue);
  background: var(--bg-input-focus);
}

.search-clear {
  position: absolute;
  right: 10px;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: color 0.15s;
}
.search-clear:hover {
  color: var(--text-primary);
}

/* Icon button */
.btn-icon {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-full);
  background: var(--bg-card);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
}
.btn-icon:hover {
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
}
.btn-icon:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* Selection count */
.select-count {
  color: var(--accent-blue);
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  margin-left: 4px;
}

/* Pill buttons */
.btn-pill {
  padding: 6px 14px;
  border: none;
  border-radius: var(--radius-full);
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.btn-pill-all {
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
}
.btn-pill-all:hover {
  background: rgba(59, 130, 246, 0.3);
}

.btn-pill-clear {
  background: var(--bg-card-hover);
  color: var(--text-secondary);
}
.btn-pill-clear:hover {
  background: var(--border-color);
  color: var(--text-primary);
}

.btn-pill-danger {
  background: var(--accent-red-soft);
  color: var(--accent-red);
}
.btn-pill-danger:hover {
  background: rgba(239, 68, 68, 0.3);
}

.btn-pill-primary {
  background: var(--accent-blue);
  color: #fff;
}
.btn-pill-primary:hover {
  background: #2563eb;
}

/* ==================== Card List ==================== */
.card-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 4px;
}
.card-list.is-empty {
  flex: none;
}

/* ==================== Torrent Card ==================== */
.torrent-card {
  display: flex;
  align-items: stretch;
  gap: 12px;
  padding: 14px 16px;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid transparent;
  transition: all 0.2s ease;
  cursor: default;
}
.torrent-card:hover {
  background: var(--bg-card-hover);
  border-color: var(--border-color);
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}
.torrent-card.selected {
  border-color: var(--accent-blue);
  box-shadow: 0 0 0 1px var(--accent-blue), var(--shadow-md);
}

/* Left: icon circle */
.card-icon-col {
  display: flex;
  align-items: flex-start;
  padding-top: 2px;
  flex-shrink: 0;
}

.card-icon-circle {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* Center: info */
.card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.card-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-meta-row {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.meta-size {
  font-weight: 500;
  color: var(--text-secondary);
}

.meta-sep {
  color: var(--border-color);
}

.meta-progress-text {
  font-weight: 600;
  color: var(--text-secondary);
}

.meta-ratio {
  color: var(--text-tertiary);
}

/* Progress bar */
.card-progress {
  width: 100%;
}

.progress-track {
  width: 100%;
  height: 4px;
  background: var(--border-color);
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.6s ease;
  min-width: 0;
}

/* Tag chips */
.card-tag-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}

.tag-state {
  font-weight: 600;
}

.tag-category {
  background: rgba(139, 92, 246, 0.15);
  color: var(--accent-purple);
}

.tag-hash {
  background: var(--bg-card-hover);
  color: var(--text-tertiary);
}

/* Right: checkbox + actions */
.card-actions-col {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: space-between;
  flex-shrink: 0;
  gap: 8px;
}

.card-checkbox {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  border: 2px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
  color: #fff;
  flex-shrink: 0;
}
.card-checkbox:hover {
  border-color: var(--accent-blue);
}
.card-checkbox.checked {
  background: var(--accent-blue);
  border-color: var(--accent-blue);
}

.card-action-btns {
  display: flex;
  gap: 4px;
}

.ac-btn {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all 0.2s;
}
.ac-btn:hover {
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
}
.ac-btn-warn:hover {
  background: var(--accent-red-soft);
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
  margin-bottom: 4px;
}

.empty-desc {
  font-size: 13px;
  color: var(--text-tertiary);
}

/* ==================== Pagination ==================== */
.pagination-bar {
  display: flex;
  justify-content: center;
  padding: 12px 0 4px;
  flex-shrink: 0;
}

/* ==================== Configs Tab ==================== */
.configs-tab {
  flex: 1;
  overflow-y: auto;
}

.configs-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.configs-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.config-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.config-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid transparent;
  transition: all 0.2s;
}
.config-card:hover {
  background: var(--bg-card-hover);
  border-color: var(--border-color);
}

.cfg-left {
  flex-shrink: 0;
}

.cfg-icon-circle {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  background: var(--bg-card-hover);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-tertiary);
  transition: all 0.3s;
}
.cfg-icon-circle.active {
  background: rgba(16, 185, 129, 0.18);
  color: var(--accent-green);
  box-shadow: 0 0 8px rgba(16, 185, 129, 0.3);
}

.cfg-body {
  flex: 1;
  min-width: 0;
}

.cfg-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 2px;
}

.cfg-url {
  font-size: 12px;
  color: var(--accent-blue);
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.cfg-user {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 1px;
}

.cfg-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.cfg-switch {
  --el-switch-on-color: var(--accent-green);
}

.cfg-actions {
  display: flex;
  gap: 4px;
}

/* ==================== Mobile ==================== */
@media screen and (max-width: 768px) {
  .qb-manager {
    padding: 4px;
  }

  .tab-bar {
    margin-bottom: 8px;
  }

  .tab-btn {
    font-size: 13px;
    padding: 6px 12px;
  }

  .filter-bar {
    gap: 4px;
  }

  .filter-select {
    width: calc(50% - 30px) !important;
  }

  .filter-select-sm {
    width: calc(25% - 4px) !important;
  }

  .search-box {
    min-width: 120px;
    max-width: none;
    flex: none;
    width: 100%;
    order: 99;
  }

  .torrent-card {
    padding: 10px 12px;
    gap: 8px;
  }

  .card-icon-circle {
    width: 36px;
    height: 36px;
  }

  .card-name {
    font-size: 13px;
  }

  .card-meta-row {
    font-size: 11px;
  }

  .card-tag-row {
    gap: 4px;
  }

  .tag-chip {
    font-size: 10px;
    padding: 2px 8px;
  }

  .card-actions-col {
    gap: 4px;
  }

  .ac-btn {
    width: 26px;
    height: 26px;
  }

  .btn-pill {
    font-size: 11px;
    padding: 4px 10px;
  }

  .config-card {
    flex-wrap: wrap;
    padding: 12px;
  }

  .cfg-right {
    width: 100%;
    justify-content: space-between;
    margin-top: 8px;
  }
}
</style>
