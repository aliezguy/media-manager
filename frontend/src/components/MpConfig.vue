<script setup>
import { reactive, onMounted, ref, computed } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Edit, Delete, ArrowUp, ArrowDown, Refresh, Key, VideoPlay, Download } from '@element-plus/icons-vue'
import draggable from 'vuedraggable'

// 本地开发用空字符串，自动适配
const API_URL = ''

// 拖拽排序 — 为每个策略生成唯一 key
let _uidCounter = 0
const getItemKey = (item) => {
  if (!item._dragId) item._dragId = ++_uidCounter
  return item._dragId
}

// 拖拽结束后自动保存
const onDragEnd = () => {
  saveConfig()
}

// 全局配置结构
const config = reactive({
  mp_host: '',
  mp_username: '',
  mp_password: '',
  tmdb_api_key: '',
  wash_schemes: [],
  subscribe_schemes: []
})

// UI 状态
const activeTab = ref('subscribe')
const options = reactive({ sites: [], filter_groups: [], downloaders: [] })
const loadingRes = ref(false)
const qualityOptions = ['全部','蓝光原盘', 'WEB-DL', 'BluRay', 'UHD', 'Remux', 'HDTV', 'H265', 'H264']

// 弹窗状态
const dialogVisible = ref(false)
const isEditMode = ref(false)
const editIndex = ref(-1)
const inputKeyword = ref('')

// 编辑中的策略对象
const editingScheme = reactive({
  name: '', keywords: [], sites: [], filter_groups: [],
  downloader: '', quality: '', active: true
})

// 计算当前正在操作哪个列表（支持拖拽排序的双向绑定）
const currentSchemes = computed({
  get() {
    return activeTab.value === 'wash' ? config.wash_schemes : config.subscribe_schemes
  },
  set(val) {
    if (activeTab.value === 'wash') {
      config.wash_schemes = val
    } else {
      config.subscribe_schemes = val
    }
  }
})

onMounted(async () => {
  await loadConfig()
  fetchResources(true)
})

// 加载配置
const loadConfig = async () => {
  try {
    const res = await axios.get(`${API_URL}/api/config`)
    if (res.data) {
      Object.assign(config, res.data)
      if (!config.wash_schemes) config.wash_schemes = []
      if (!config.subscribe_schemes) config.subscribe_schemes = []
    }
  } catch(e) { ElMessage.error('加载配置失败') }
}

// 保存配置
const saveConfig = async () => {
  try {
    await axios.post(`${API_URL}/api/config`, config)
    ElMessage.success('配置已保存')
  } catch(e) { ElMessage.error('保存失败') }
}

// 获取 MP 资源
const fetchResources = async (silent=false) => {
  if(!config.mp_host) return
  loadingRes.value = true
  try {
    const res = await axios.get(`${API_URL}/api/resources`)
    if (res.data) {
      options.sites = res.data.sites || []
      options.filter_groups = res.data.filter_groups || []
      options.downloaders = res.data.downloaders || []
      if(!silent) ElMessage.success('MP 资源同步完成')
    }
  } catch(e) { if(!silent) ElMessage.error('同步失败，请检查 MP 连接') }
  finally { loadingRes.value = false }
}

const getSiteName = (id) => {
  const s = options.sites.find(item => item.id === id)
  return s ? s.name : id
}

// === 策略操作 ===
const openAddDialog = () => {
  isEditMode.value = false
  Object.assign(editingScheme, {
    name: '新策略', keywords: [], sites: [], filter_groups: [],
    downloader: '', quality: '', active: true
  })
  dialogVisible.value = true
  if (options.filter_groups.length === 0) fetchResources(true)
}

const openEditDialog = (index, row) => {
  isEditMode.value = true
  editIndex.value = index
  Object.assign(editingScheme, JSON.parse(JSON.stringify(row)))
  dialogVisible.value = true
}

const deleteScheme = async (index) => {
  await ElMessageBox.confirm('确定删除该策略吗？', '提示', { type: 'warning' })
  currentSchemes.value.splice(index, 1)
  saveConfig()
}

const confirmScheme = () => {
  const finalScheme = JSON.parse(JSON.stringify(editingScheme))
  if(isEditMode.value) {
    currentSchemes.value[editIndex.value] = finalScheme
  } else {
    currentSchemes.value.push(finalScheme)
  }
  dialogVisible.value = false
  saveConfig()
}

const moveScheme = (index, direction) => {
  const arr = currentSchemes.value
  if (direction === 'up' && index > 0) {
    [arr[index], arr[index - 1]] = [arr[index - 1], arr[index]]
  } else if (direction === 'down' && index < arr.length - 1) {
    [arr[index], arr[index + 1]] = [arr[index + 1], arr[index]]
  }
  saveConfig()
}

const addKeyword = () => {
  if (inputKeyword.value && !editingScheme.keywords.includes(inputKeyword.value)) {
    editingScheme.keywords.push(inputKeyword.value)
    inputKeyword.value = ''
  }
}
const removeKeyword = (tag) => {
  editingScheme.keywords = editingScheme.keywords.filter(k => k !== tag)
}
</script>

<template>
  <div class="mp-container">
    <!-- ==================== 基础设置卡片 ==================== -->
    <div class="base-section">
      <div class="section-header">
        <span class="section-icon">🌐</span>
        <span class="section-title">基础设置 & 连接</span>
      </div>
      <div class="section-body">
        <el-form label-position="top">
          <el-row :gutter="20">
            <el-col :xs="24" :sm="8">
              <el-form-item label="MoviePilot 地址">
                <el-input v-model="config.mp_host" placeholder="http://ip:3000" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="8">
              <el-form-item label="MP 用户名">
                <el-input v-model="config.mp_username" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="8">
              <el-form-item label="MP 密码">
                <el-input v-model="config.mp_password" type="password" show-password />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="TMDB API Key (用于自动分类)">
            <el-input v-model="config.tmdb_api_key" type="password" show-password placeholder="请输入 TMDB Key">
              <template #prefix><el-icon><Key /></el-icon></template>
            </el-input>
            <div class="tip">配置后，新增订阅将根据 TMDB 信息自动归类（如：日韩剧、综艺）。</div>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveConfig">保存全部配置</el-button>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <!-- ==================== 策略配置卡片 ==================== -->
    <div class="scheme-section">
      <!-- Custom Tab Bar -->
      <div class="scheme-tab-bar">
        <button
          :class="['scheme-tab', { active: activeTab === 'subscribe' }]"
          @click="activeTab = 'subscribe'"
        >
          <el-icon :size="16"><VideoPlay /></el-icon>
          <span>追更 / 订阅策略</span>
        </button>
        <button
          :class="['scheme-tab', { active: activeTab === 'wash' }]"
          @click="activeTab = 'wash'"
        >
          <el-icon :size="16"><Download /></el-icon>
          <span>洗版 / 订阅策略</span>
        </button>
      </div>

      <!-- Tab Description + Add Button -->
      <div class="scheme-toolbar">
        <p class="scheme-desc">
          {{ activeTab === 'subscribe'
            ? '新增订阅时，根据剧名匹配规则，自动设置下载器、过滤组等参数。'
            : '订阅状态变为"已完成"时，根据规则触发洗版（下载更高质量版本）。' }}
        </p>
        <button class="btn-pill btn-pill-primary" @click="openAddDialog">
          <el-icon :size="15"><Plus /></el-icon>
          {{ activeTab === 'subscribe' ? '新建追更策略' : '新建洗版策略' }}
        </button>
      </div>

      <!-- ==================== CSS Grid 策略卡片列表（拖拽排序） ==================== -->
      <draggable
        v-if="currentSchemes.length"
        v-model="currentSchemes"
        :animation="300"
        ghost-class="ghost-card"
        drag-class="drag-card"
        handle=".drag-handle"
        :item-key="getItemKey"
        class="strategy-grid"
        @change="onDragEnd"
      >
        <template #item="{ element: row, index }">
          <div
            class="strategy-card"
            :class="{ 'is-inactive': !row.active }"
          >
            <!-- Card Header: Priority + Name + Switch (拖拽手柄) -->
            <div class="sc-header drag-handle">
              <div class="sc-header-left">
                <span class="sc-priority">#{{ index + 1 }}</span>
                <span class="sc-name">{{ row.name }}</span>
              </div>
              <el-switch v-model="row.active" size="small" @change="saveConfig" />
            </div>

            <!-- Keywords -->
            <div class="sc-keywords">
              <template v-if="row.keywords && row.keywords.length">
                <span v-for="k in row.keywords" :key="k" class="sc-chip sc-chip-keyword">{{ k }}</span>
              </template>
              <span v-else class="sc-chip sc-chip-default">兜底默认</span>
            </div>

            <!-- Divider -->
            <div class="sc-divider"></div>

            <!-- Rule Details -->
            <div class="sc-details">
              <div class="sc-detail-item">
                <span class="sc-label">过滤规则</span>
                <span class="sc-value">{{
                  row.filter_groups && row.filter_groups.length
                    ? row.filter_groups.join(', ')
                    : '未设置'
                }}</span>
              </div>
              <div class="sc-detail-item">
                <span class="sc-label">下载器</span>
                <span class="sc-value sc-value-dl">{{ row.downloader || '默认' }}</span>
              </div>
              <div v-if="row.quality" class="sc-detail-item">
                <span class="sc-label">质量</span>
                <span class="sc-value sc-value-quality">{{ row.quality }}</span>
              </div>
            </div>

            <!-- Sites -->
            <div v-if="row.sites && row.sites.length" class="sc-sites">
              <span class="sc-label">限定站点</span>
              <div class="sc-site-chips">
                <span v-for="sid in row.sites" :key="sid" class="sc-chip sc-chip-site">
                  {{ getSiteName(sid) }}
                </span>
              </div>
            </div>

            <!-- Card Footer: Actions -->
            <div class="sc-footer">
              <div class="sc-move-btns">
                <button
                  class="ac-btn"
                  :disabled="index === 0"
                  :class="{ 'is-disabled': index === 0 }"
                  @click="moveScheme(index, 'up')"
                  title="上移"
                >
                  <el-icon :size="14"><ArrowUp /></el-icon>
                </button>
                <button
                  class="ac-btn"
                  :disabled="index === currentSchemes.length - 1"
                  :class="{ 'is-disabled': index === currentSchemes.length - 1 }"
                  @click="moveScheme(index, 'down')"
                  title="下移"
                >
                  <el-icon :size="14"><ArrowDown /></el-icon>
                </button>
              </div>
              <div class="sc-action-btns">
                <button class="ac-btn" @click="openEditDialog(index, row)" title="编辑">
                  <el-icon :size="14"><Edit /></el-icon>
                </button>
                <button class="ac-btn ac-btn-warn" @click="deleteScheme(index)" title="删除">
                  <el-icon :size="14"><Delete /></el-icon>
                </button>
              </div>
            </div>
          </div>
        </template>
      </draggable>

      <!-- Empty State -->
      <div v-else class="empty-state">
        <div class="empty-icon-circle">
          <el-icon :size="32"><VideoPlay /></el-icon>
        </div>
        <p class="empty-title">暂无策略</p>
        <p class="empty-desc">点击上方按钮创建第一条策略规则</p>
      </div>
    </div>

    <!-- ==================== 编辑弹窗（保留 el-dialog，全局已暗黑） ==================== -->
    <el-dialog v-model="dialogVisible" :title="isEditMode ? '编辑策略' : '新建策略'" width="600px">
      <el-form label-position="top">
        <el-form-item label="策略名称">
          <el-input v-model="editingScheme.name" placeholder="例如：动漫策略 / 4K原盘" />
        </el-form-item>
        <el-form-item label="匹配关键词 (留空则作为默认兜底策略)">
          <div class="keyword-input">
            <el-tag
              v-for="tag in editingScheme.keywords"
              :key="tag"
              closable
              @close="removeKeyword(tag)"
              class="keyword-tag"
            >{{ tag }}</el-tag>
            <el-input
              v-model="inputKeyword"
              class="input-new-tag"
              size="small"
              placeholder="+ 输入关键词回车"
              @keyup.enter="addKeyword"
              @blur="addKeyword"
              style="width: 150px;"
            />
          </div>
          <div class="tip">匹配范围：订阅名称 (Name)。按列表顺序自上而下匹配，命中即停止。</div>
        </el-form-item>
        <el-divider>执行参数</el-divider>
        <el-form-item label="指定站点 (可选)">
          <div style="display: flex; gap: 10px; width: 100%;">
            <el-select
              v-model="editingScheme.sites"
              multiple clearable filterable
              placeholder="不限 (留空)"
              style="flex: 1"
              :loading="loadingRes"
            >
              <el-option v-for="s in options.sites" :key="s.id" :label="s.name" :value="s.id" />
            </el-select>
            <el-button :icon="Refresh" circle @click="fetchResources(false)"></el-button>
          </div>
        </el-form-item>
        <el-form-item label="过滤规则组 (Filters)">
          <el-select
            v-model="editingScheme.filter_groups"
            multiple clearable filterable
            placeholder="请选择规则组"
            style="width: 100%"
          >
            <el-option v-for="f in options.filter_groups" :key="f.name" :label="f.name" :value="f.name" />
          </el-select>
        </el-form-item>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="下载器 (Downloader)">
              <el-select
                v-model="editingScheme.downloader"
                clearable filterable
                placeholder="请选择下载器"
                style="width: 100%"
              >
                <el-option v-for="d in options.downloaders" :key="d.name" :label="d.name" :value="d.name" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="质量 (Quality)">
              <el-select
                v-model="editingScheme.quality"
                clearable allow-create filterable
                placeholder="WEB-DL"
                style="width: 100%"
              >
                <el-option v-for="q in qualityOptions" :key="q" :label="q" :value="q" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" @click="confirmScheme">确定</el-button>
        </span>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ==================== Layout ==================== */
.mp-container {
  padding: 16px 20px;
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ==================== Section Card (replaces el-card) ==================== */
.base-section,
.scheme-section {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xl);
  overflow: hidden;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border-color);
}
.section-icon { font-size: 16px; }
.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.section-body {
  padding: 20px;
}

/* ==================== Tip text ==================== */
.tip {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 4px;
  line-height: 1.5;
}

/* ==================== Scheme Tab Bar ==================== */
.scheme-tab-bar {
  display: flex;
  border-bottom: 1px solid var(--border-color);
}

.scheme-tab {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 12px 16px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 14px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
}
.scheme-tab:hover {
  color: var(--text-secondary);
  background: rgba(59, 130, 246, 0.04);
}
.scheme-tab.active {
  color: var(--accent-blue);
  font-weight: 600;
}
.scheme-tab.active::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 20%;
  right: 20%;
  height: 2px;
  background: var(--accent-blue);
  border-radius: 1px 1px 0 0;
}

/* ==================== Scheme Toolbar ==================== */
.scheme-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 20px;
}

.scheme-desc {
  font-size: 13px;
  color: var(--text-tertiary);
  line-height: 1.5;
  margin: 0;
  flex: 1;
}

/* Pill button (shared style — duplicated for component isolation) */
.btn-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 18px;
  border: none;
  border-radius: var(--radius-full);
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.btn-pill-primary {
  background: var(--accent-blue);
  color: #fff;
}
.btn-pill-primary:hover {
  background: #2563eb;
  box-shadow: var(--shadow-glow-blue);
}

/* ==================== Strategy Grid ==================== */
.strategy-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  padding: 0 20px 20px;
}

/* ==================== Strategy Card ==================== */
.strategy-card {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: all 0.2s ease;
}
.strategy-card:hover {
  border-color: #475569;
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}
.strategy-card.is-inactive {
  opacity: 0.55;
}
.strategy-card.is-inactive:hover {
  opacity: 0.75;
}

/* --- Card Header --- */
.sc-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sc-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.sc-priority {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--radius-sm);
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.sc-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* --- Keywords --- */
.sc-keywords {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* --- Chip --- */
.sc-chip {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}

.sc-chip-keyword {
  background: rgba(59, 130, 246, 0.12);
  color: var(--accent-blue);
  border: 1px solid rgba(59, 130, 246, 0.2);
}

.sc-chip-default {
  background: rgba(100, 116, 139, 0.12);
  color: var(--text-tertiary);
  font-style: italic;
}

.sc-chip-site {
  background: rgba(239, 68, 68, 0.12);
  color: var(--accent-red);
  border: 1px solid rgba(239, 68, 68, 0.2);
  font-size: 10px;
  padding: 2px 8px;
}

/* --- Divider --- */
.sc-divider {
  height: 1px;
  background: var(--border-color);
  margin: 0;
}

/* --- Details --- */
.sc-details {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.sc-detail-item {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
}

.sc-label {
  color: var(--text-tertiary);
  flex-shrink: 0;
  font-size: 11px;
  min-width: 48px;
}

.sc-value {
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sc-value-dl {
  color: var(--accent-green);
  font-weight: 500;
}

.sc-value-quality {
  color: var(--accent-yellow);
  font-weight: 500;
}

/* --- Sites --- */
.sc-sites {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sc-site-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* --- Footer --- */
.sc-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 8px;
  border-top: 1px solid var(--border-color);
}

.sc-move-btns,
.sc-action-btns {
  display: flex;
  gap: 4px;
}

/* Action icon button (shared) */
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
.ac-btn.is-disabled {
  opacity: 0.3;
  pointer-events: none;
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
  padding: 48px 20px;
  text-align: center;
}

.empty-icon-circle {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--bg-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-tertiary);
  margin-bottom: 12px;
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
}

/* ==================== Keyword Input (dialog) ==================== */
.keyword-input {
  border: 1px solid var(--border-color);
  background: var(--bg-input);
  padding: 6px 8px;
  border-radius: var(--radius-md);
  min-height: 40px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  transition: border-color 0.2s;
}
.keyword-input:focus-within {
  border-color: var(--accent-blue);
}

.keyword-tag {
  --el-tag-bg-color: rgba(59, 130, 246, 0.12);
  --el-tag-border-color: transparent;
  --el-tag-text-color: var(--accent-blue);
}

/* ==================== Responsive ==================== */
@media screen and (max-width: 1200px) {
  .strategy-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media screen and (max-width: 768px) {
  .mp-container {
    padding: 8px;
    gap: 12px;
  }

  .section-header,
  .section-body {
    padding: 12px;
  }

  .section-body {
    padding-top: 8px;
  }

  .scheme-toolbar {
    flex-direction: column;
    align-items: flex-start;
    padding: 10px 12px;
  }

  .scheme-desc {
    font-size: 12px;
  }

  .strategy-grid {
    grid-template-columns: 1fr;
    gap: 8px;
    padding: 0 12px 12px;
  }

  .strategy-card {
    padding: 12px;
    gap: 10px;
  }

  .scheme-tab {
    font-size: 12px;
    padding: 10px 8px;
  }

  .el-col {
    margin-bottom: 12px;
  }
}

/* ==================== 拖拽排序动画 ==================== */
/* Drag handle — 仅卡片 Header 可拖拽 */
.drag-handle {
  cursor: grab;
  user-select: none;
}
.drag-handle:active {
  cursor: grabbing;
}

/* Ghost — 拖拽时原位置的占位符（半透明） */
:deep(.ghost-card) {
  opacity: 0.25;
  background: var(--accent-blue-soft) !important;
  border: 1px dashed var(--accent-blue) !important;
  border-radius: var(--radius-lg);
}

/* Drag — 正在被拖拽的卡片（放大 + 浮起 + 光晕） */
:deep(.drag-card) {
  transform: scale(1.04) !important;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5), 0 0 20px rgba(59, 130, 246, 0.25) !important;
  z-index: 1000 !important;
  cursor: grabbing !important;
  background: var(--bg-card-hover) !important;
  border-color: var(--accent-blue) !important;
}

/* SortableJS 使用的内部 class — 确保拖拽区域外的卡片正常 */
:deep(.sortable-chosen) {
  /* 正在被拖拽的原始元素 — 样式由 drag-card 控制 */;
}

/* 阻止卡片内按钮在拖拽时触发事件 */
.drag-handle .el-switch {
  pointer-events: none;
}
</style>
