<script setup>
import { reactive, onMounted, ref, computed } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  SlidersHorizontal, BellRing, Download, Plus, Pencil, Trash2,
  ArrowUp, ArrowDown, RefreshCw, Globe, UserRound, Lock, KeyRound, Save, ClipboardList
} from 'lucide-vue-next'
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
    <section class="glass-card">
      <header class="card-head">
        <div class="icon-badge">
          <SlidersHorizontal :size="19" />
        </div>
        <div class="min-w-0 flex-1">
          <h2 class="text-[15px] font-bold tracking-wide text-white">基础设置 & 连接</h2>
          <p class="mt-1 text-xs leading-relaxed text-slate-500">MoviePilot / TMDB 连接参数</p>
        </div>
      </header>

      <div class="card-body">
        <div class="grid grid-cols-1 gap-x-6 gap-y-5 md:grid-cols-3">
          <div>
            <label class="field-label"><Globe :size="13" />MoviePilot 地址</label>
            <el-input v-model="config.mp_host" placeholder="http://ip:3000" />
          </div>
          <div>
            <label class="field-label"><UserRound :size="13" />MP 用户名</label>
            <el-input v-model="config.mp_username" />
          </div>
          <div>
            <label class="field-label"><Lock :size="13" />MP 密码</label>
            <el-input v-model="config.mp_password" type="password" show-password />
          </div>
        </div>

        <div class="mt-5">
          <label class="field-label"><KeyRound :size="13" />TMDB API Key（用于自动分类）</label>
          <el-input v-model="config.tmdb_api_key" type="password" show-password placeholder="请输入 TMDB Key" />
          <p class="field-tip">配置后，新增订阅将根据 TMDB 信息自动归类（如：日韩剧、综艺）。</p>
        </div>

        <!-- 左下角保存按钮 -->
        <div class="mt-6 flex justify-start">
          <button type="button" class="btn btn-primary" @click="saveConfig">
            <Save :size="16" />保存全部配置
          </button>
        </div>
      </div>
    </section>

    <!-- ==================== 策略管理卡片 ==================== -->
    <section class="glass-card">
      <header class="card-head">
        <div class="icon-badge purple">
          <BellRing :size="19" />
        </div>
        <div class="min-w-0 flex-1">
          <h2 class="text-[15px] font-bold tracking-wide text-white">策略管理</h2>
          <p class="mt-1 text-xs leading-relaxed text-slate-500">追更 / 洗版 自动化策略规则</p>
        </div>
      </header>

      <div class="card-body">
        <!-- 胶囊 Tab + 新建按钮 -->
        <div class="flex flex-wrap items-center justify-between gap-3.5">
          <!-- 胶囊式滑动 Tab：滑块 translate-x-full 平滑位移 -->
          <div class="relative grid min-w-[300px] flex-1 grid-cols-2 gap-1 rounded-2xl border border-white/10 bg-white/5 p-1 backdrop-blur-md max-w-[480px]">
            <div
              class="absolute inset-y-1 left-1 w-[calc(50%-4px)] rounded-xl bg-gradient-to-br from-electric to-violet-500 shadow-glow-blue transition-all duration-300 ease-out"
              :class="activeTab === 'wash' ? 'translate-x-full' : 'translate-x-0'"
            ></div>
            <button
              type="button"
              class="relative z-[1] flex items-center justify-center gap-1.5 whitespace-nowrap rounded-[10px] px-4 py-2.5 text-[13px] font-semibold transition-colors duration-300"
              :class="activeTab === 'subscribe' ? 'text-white' : 'text-slate-400 hover:text-slate-200'"
              @click="activeTab = 'subscribe'"
            >
              <BellRing :size="15" />
              <span>追更 / 订阅策略</span>
            </button>
            <button
              type="button"
              class="relative z-[1] flex items-center justify-center gap-1.5 whitespace-nowrap rounded-[10px] px-4 py-2.5 text-[13px] font-semibold transition-colors duration-300"
              :class="activeTab === 'wash' ? 'text-white' : 'text-slate-400 hover:text-slate-200'"
              @click="activeTab = 'wash'"
            >
              <Download :size="15" />
              <span>洗版 / 订阅策略</span>
            </button>
          </div>

          <button type="button" class="btn btn-primary btn-add" @click="openAddDialog">
            <Plus :size="16" />
            {{ activeTab === 'subscribe' ? '新建追更策略' : '新建洗版策略' }}
          </button>
        </div>

        <p class="mt-3 mb-4.5 text-[13px] leading-relaxed text-slate-500">
          {{ activeTab === 'subscribe'
            ? '新增订阅时，根据剧名匹配规则，自动设置下载器、过滤组等参数。'
            : '订阅状态变为"已完成"时，根据规则触发洗版（下载更高质量版本）。' }}
        </p>

        <!-- ==================== 策略卡片网格（拖拽排序） ==================== -->
        <draggable
          v-if="currentSchemes.length"
          v-model="currentSchemes"
          :animation="300"
          ghost-class="ghost-card"
          drag-class="drag-card"
          handle=".drag-handle"
          :item-key="getItemKey"
          class="grid grid-cols-1 gap-3.5 md:grid-cols-2 lg:grid-cols-3"
          @change="onDragEnd"
        >
          <template #item="{ element: row, index }">
            <div class="strategy-card" :class="{ 'is-inactive': !row.active }">
              <!-- Header: 编号 + 名称 + Switch（拖拽手柄） -->
              <div class="drag-handle flex items-center justify-between gap-2">
                <div class="flex min-w-0 items-center gap-2">
                  <span class="sc-priority">#{{ index + 1 }}</span>
                  <span class="truncate text-[14px] font-semibold text-white">{{ row.name }}</span>
                </div>
                <el-switch v-model="row.active" size="small" @change="saveConfig" />
              </div>

              <!-- 匹配关键词 -->
              <div class="flex flex-wrap gap-1">
                <template v-if="row.keywords && row.keywords.length">
                  <span v-for="k in row.keywords" :key="k" class="sc-chip sc-chip-keyword">{{ k }}</span>
                </template>
                <span v-else class="sc-chip sc-chip-default">兜底默认</span>
              </div>

              <!-- Divider -->
              <div class="h-px bg-white/5"></div>

              <!-- 规则键值对 -->
              <div class="flex flex-col gap-1.5">
                <div class="flex items-center gap-1.5 text-xs">
                  <span class="sc-label">过滤规则</span>
                  <span class="truncate text-slate-300">{{
                    row.filter_groups && row.filter_groups.length
                      ? row.filter_groups.join(', ')
                      : '未设置'
                  }}</span>
                </div>
                <div class="flex items-center gap-1.5 text-xs">
                  <span class="sc-label">下载器</span>
                  <span v-if="row.downloader" class="sc-chip sc-chip-downloader">{{ row.downloader }}</span>
                  <span v-else class="truncate text-slate-300">默认</span>
                </div>
                <div v-if="row.quality" class="flex items-center gap-1.5 text-xs">
                  <span class="sc-label">质量</span>
                  <span class="sc-chip sc-chip-quality">{{ row.quality }}</span>
                </div>
              </div>

              <!-- 限定站点 -->
              <div v-if="row.sites && row.sites.length" class="flex flex-col gap-1.5">
                <span class="sc-label">限定站点</span>
                <div class="flex flex-wrap gap-1">
                  <span v-for="sid in row.sites" :key="sid" class="sc-chip sc-chip-site">
                    {{ getSiteName(sid) }}
                  </span>
                </div>
              </div>

              <!-- Footer: 操作 -->
              <div class="mt-auto flex items-center justify-between border-t border-white/5 pt-2.5">
                <div class="flex gap-1">
                  <button
                    type="button"
                    class="ac-btn"
                    :disabled="index === 0"
                    :class="{ 'is-disabled': index === 0 }"
                    @click="moveScheme(index, 'up')"
                    title="上移"
                  >
                    <ArrowUp :size="14" />
                  </button>
                  <button
                    type="button"
                    class="ac-btn"
                    :disabled="index === currentSchemes.length - 1"
                    :class="{ 'is-disabled': index === currentSchemes.length - 1 }"
                    @click="moveScheme(index, 'down')"
                    title="下移"
                  >
                    <ArrowDown :size="14" />
                  </button>
                </div>
                <div class="flex gap-1">
                  <button type="button" class="ac-btn" @click="openEditDialog(index, row)" title="编辑">
                    <Pencil :size="14" />
                  </button>
                  <button type="button" class="ac-btn ac-btn-warn" @click="deleteScheme(index)" title="删除">
                    <Trash2 :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </template>
        </draggable>

        <!-- 空状态 -->
        <div v-else class="flex flex-col items-center justify-center px-5 py-12 text-center">
          <div class="mb-3.5 flex h-16 w-16 items-center justify-center rounded-[18px] border border-white/10 bg-white/5 text-slate-500">
            <ClipboardList :size="30" />
          </div>
          <p class="mb-1 text-[15px] font-semibold text-slate-200">暂无策略</p>
          <p class="text-[13px] text-slate-500">点击上方按钮创建第一条策略规则</p>
        </div>
      </div>
    </section>

    <!-- ==================== 编辑弹窗 ==================== -->
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
            <el-button :icon="RefreshCw" circle @click="fetchResources(false)"></el-button>
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
/* ==================== 容器 ==================== */
.mp-container {
  @apply mx-auto flex max-w-[1200px] flex-col gap-5 p-4 pb-10 md:p-5;
}

/* ==================== 毛玻璃卡片 ==================== */
.glass-card {
  @apply relative z-[1] overflow-hidden rounded-[22px] border border-white/10;
  background: linear-gradient(160deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.02));
  backdrop-filter: blur(18px) saturate(140%);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  box-shadow: 0 24px 60px -24px rgba(0, 0, 0, 0.65), inset 0 1px 0 rgba(255, 255, 255, 0.06);
}
.glass-card::before {
  content: '';
  position: absolute;
  top: 0; left: 12%; right: 12%;
  height: 1px;
  z-index: 0;
  background: linear-gradient(90deg, transparent, rgba(59, 130, 246, 0.55), rgba(139, 92, 246, 0.55), transparent);
}

.card-head {
  @apply relative z-[1] flex flex-wrap items-center gap-3.5 border-b border-white/5 px-6 py-[18px];
}
.card-body {
  @apply relative z-[1] p-6;
}

.icon-badge {
  width: 40px; height: 40px;
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  border-radius: 12px;
  color: #60a5fa;
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.18), rgba(59, 130, 246, 0.05));
  border: 1px solid rgba(59, 130, 246, 0.32);
  box-shadow: 0 0 18px rgba(59, 130, 246, 0.22), inset 0 0 10px rgba(59, 130, 246, 0.08);
}
.icon-badge.purple {
  color: #a78bfa;
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.18), rgba(139, 92, 246, 0.05));
  border-color: rgba(139, 92, 246, 0.32);
  box-shadow: 0 0 18px rgba(139, 92, 246, 0.22), inset 0 0 10px rgba(139, 92, 246, 0.08);
}

/* ==================== 表单字段 ==================== */
.field-label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  font-size: 12.5px;
  font-weight: 600;
  color: #cbd5e1;
  letter-spacing: 0.2px;
}
.field-label svg { color: #64748b; }
.field-tip {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: #64748b;
}

/* ==================== Element Plus 深度定制（复用步骤 3 电光蓝发光 Input） ==================== */
:deep(.el-input__wrapper) {
  @apply rounded-xl bg-black/20 transition-shadow duration-200;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.10);
}
:deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.28);
}
:deep(.el-input__wrapper.is-focus) {
  background: rgba(59, 130, 246, 0.06);
  border-color: #3b82f6;
  box-shadow: 0 0 0 1px #3b82f6, 0 0 8px rgba(59, 130, 246, 0.5);
}
:deep(.el-input__inner) { color: #f1f5f9; }
:deep(.el-input__inner::placeholder) { color: #475569; }
:deep(.el-input__password) { color: #64748b; }
:deep(.el-input__password:hover) { color: #60a5fa; }

/* el-select 触发器 —— 与 Input 同风格（弹窗内多级下拉） */
:deep(.el-select__wrapper) {
  @apply rounded-xl bg-black/20 transition-shadow duration-200;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.10);
}
:deep(.el-select__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.28);
}
:deep(.el-select__wrapper.is-focused) {
  background: rgba(59, 130, 246, 0.06);
  border-color: #3b82f6;
  box-shadow: 0 0 0 1px #3b82f6, 0 0 8px rgba(59, 130, 246, 0.5);
}

/* el-form-item 标签对齐深色主题 */
:deep(.el-form-item__label) { color: #cbd5e1; }

/* Switch —— 激活时强烈电光蓝光晕 */
:deep(.el-switch) {
  --el-switch-on-color: #3b82f6;
  --el-switch-off-color: rgba(148, 163, 184, 0.28);
  --el-switch-border-color: transparent;
}
:deep(.el-switch.is-checked .el-switch__core) {
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.6);
}

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
.btn-primary {
  background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
  box-shadow: 0 4px 20px rgba(59, 130, 246, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.2);
}
.btn-primary:hover {
  transform: translateY(-1px) scale(1.03);
  box-shadow: 0 8px 30px rgba(99, 102, 241, 0.55), 0 0 26px rgba(139, 92, 246, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.2);
}
.btn-primary:active { transform: scale(0.98); }
.btn-add { padding: 9px 18px; font-size: 13px; }

/* ==================== 策略卡片网格 ==================== */
.strategy-card {
  @apply relative flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/5 p-4 backdrop-blur-md transition-all duration-200;
}
.strategy-card:hover {
  @apply -translate-y-0.5 border-electric/35 bg-white/[0.055] shadow-[0_14px_32px_-16px_rgba(59,130,246,0.35)];
}
.strategy-card.is-inactive { opacity: 0.5; }
.strategy-card.is-inactive:hover { opacity: 0.8; }

/* 编号徽章 */
.sc-priority {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  height: 30px;
  padding: 0 8px;
  flex-shrink: 0;
  border-radius: 9px;
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.18), rgba(139, 92, 246, 0.18));
  border: 1px solid rgba(59, 130, 246, 0.35);
  color: #93c5fd;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  font-weight: 700;
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.2);
}

/* ==================== 数据药丸 ==================== */
.sc-chip {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  line-height: 1.4;
}
.sc-chip-keyword {
  background: rgba(59, 130, 246, 0.1);
  color: #93c5fd;
  border: 1px solid rgba(59, 130, 246, 0.25);
}
.sc-chip-default {
  background: rgba(100, 116, 139, 0.12);
  color: #64748b;
  font-style: italic;
}
/* 下载器 —— 霓虹绿 */
.sc-chip-downloader {
  background: rgba(16, 185, 129, 0.1);
  color: #34d399;
  border: 1px solid rgba(16, 185, 129, 0.3);
  box-shadow: 0 0 10px rgba(16, 185, 129, 0.15);
}
/* 质量 —— 警示黄发光 */
.sc-chip-quality {
  background: rgba(250, 204, 21, 0.08);
  color: #facc15;
  border: 1px solid rgba(250, 204, 21, 0.3);
  text-shadow: 0 0 8px rgba(250, 204, 21, 0.5);
  box-shadow: 0 0 10px rgba(250, 204, 21, 0.12);
}
/* 站点 —— 半透明药丸 */
.sc-chip-site {
  background: rgba(255, 255, 255, 0.05);
  color: #cbd5e1;
  border: 1px solid rgba(255, 255, 255, 0.12);
  font-size: 10px;
  padding: 2px 9px;
}

.sc-label {
  color: #64748b;
  flex-shrink: 0;
  font-size: 11px;
  min-width: 52px;
}

/* ==================== 底部操作按钮（线框 · 默认降透明 · Hover 亮主题色并放大） ==================== */
.ac-btn {
  @apply flex h-[30px] w-[30px] items-center justify-center rounded-lg border border-white/10 bg-transparent text-slate-500 opacity-50 transition-all duration-200;
}
.ac-btn:hover:not(:disabled):not(.is-disabled) {
  @apply scale-110 border-electric/40 bg-blue-500/10 text-blue-400 opacity-100 drop-shadow-[0_0_6px_rgba(59,130,246,0.5)];
}
.ac-btn.is-disabled { @apply pointer-events-none opacity-25; }
/* 删除 —— Hover 变红 + 红色光晕 */
.ac-btn-warn:hover:not(:disabled):not(.is-disabled) {
  @apply scale-110 border-danger/40 bg-red-500/10 text-red-400 opacity-100 drop-shadow-[0_0_6px_rgba(239,68,68,0.5)];
}

/* ==================== 弹窗内关键词输入 ==================== */
.keyword-input {
  border: 1px solid rgba(148, 163, 184, 0.2);
  background: rgba(2, 6, 23, 0.4);
  padding: 6px 8px;
  border-radius: 12px;
  min-height: 40px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  transition: border-color 0.2s;
}
.keyword-input:focus-within {
  border-color: #3b82f6;
  box-shadow: 0 0 0 1px #3b82f6, 0 0 12px rgba(59, 130, 246, 0.25);
}
.keyword-tag {
  --el-tag-bg-color: rgba(59, 130, 246, 0.12);
  --el-tag-border-color: transparent;
  --el-tag-text-color: #93c5fd;
}
.tip {
  font-size: 12px;
  color: #64748b;
  margin-top: 4px;
  line-height: 1.5;
}

/* ==================== 拖拽排序动画 ==================== */
.drag-handle { cursor: grab; user-select: none; }
.drag-handle:active { cursor: grabbing; }
:deep(.ghost-card) {
  opacity: 0.25;
  background: rgba(59, 130, 246, 0.12) !important;
  border: 1px dashed #3b82f6 !important;
  border-radius: 16px;
}
:deep(.drag-card) {
  transform: scale(1.04) !important;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5), 0 0 20px rgba(59, 130, 246, 0.3) !important;
  z-index: 1000 !important;
  cursor: grabbing !important;
  background: rgba(255, 255, 255, 0.08) !important;
  border-color: #3b82f6 !important;
}
.drag-handle .el-switch { pointer-events: none; }

/* ==================== 响应式 ==================== */
@media (max-width: 768px) {
  .card-head { @apply px-4 py-3.5; }
  .card-body { @apply p-4; }
  .btn-add { width: 100%; }
  .tab-btn { font-size: 12px; padding: 10px 8px; }
}

@media (prefers-reduced-motion: reduce) {
  .tab-pill, .strategy-card, .btn, .ac-btn, .el-input__wrapper {
    transition: none !important;
  }
}
</style>
