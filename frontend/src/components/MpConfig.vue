<script setup>
import { reactive, onMounted, ref, computed } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Edit, Delete, ArrowUp, ArrowDown, Refresh, Key, VideoPlay, Download } from '@element-plus/icons-vue'

// 本地开发用空字符串，自动适配
const API_URL = ''

// 全局配置结构
const config = reactive({
  mp_host: '',
  mp_username: '',
  mp_password: '',
  tmdb_api_key: '',
  wash_schemes: [],      // 洗版策略
  subscribe_schemes: []  // 追更策略
})

// UI 状态
const activeTab = ref('subscribe') // 默认显示追更
// 🔥 【修改点1】将 filters 改为 filter_groups 以匹配后端和模版
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

// 计算当前正在操作哪个列表
const currentSchemes = computed(() => {
  return activeTab.value === 'wash' ? config.wash_schemes : config.subscribe_schemes
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
      // 兜底初始化，防止 undefined 报错
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
      // 🔥 【修改点2】后端返回的是 filter_groups，这里必须对应接收
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
  // 重置表单
  Object.assign(editingScheme, { 
    name: '新策略', keywords: [], sites: [], filter_groups: [], 
    downloader: '', quality: '', active: true 
  })
  dialogVisible.value = true
  // 🔥 【修改点3】检查长度时也要用 filter_groups
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
    <el-card shadow="never" class="base-card">
      <template #header><div class="card-header"><span>🌐 基础设置 & 连接</span></div></template>
      <el-form label-position="top">
        <el-row :gutter="20">
          <el-col :span="8">
            <el-form-item label="MoviePilot 地址"><el-input v-model="config.mp_host" placeholder="http://ip:3000" /></el-form-item>
          </el-col>
          <el-col :span="8">
             <el-form-item label="MP 用户名"><el-input v-model="config.mp_username" /></el-form-item>
          </el-col>
          <el-col :span="8">
             <el-form-item label="MP 密码"><el-input v-model="config.mp_password" type="password" show-password /></el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="TMDB API Key (用于自动分类)">
          <el-input v-model="config.tmdb_api_key" type="password" show-password placeholder="请输入 TMDB Key">
            <template #prefix><el-icon><Key /></el-icon></template>
          </el-input>
          <div class="tip">配置后，新增订阅将根据 TMDB 信息自动归类（如：日韩剧、综艺）。</div>
        </el-form-item>
        <el-form-item><el-button type="primary" @click="saveConfig">保存全部配置</el-button></el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="hover" class="scheme-card">
      <el-tabs v-model="activeTab" class="custom-tabs">
        
        <el-tab-pane name="subscribe">
          <template #label>
            <span class="custom-tabs-label"><el-icon><VideoPlay /></el-icon> <span> 追更/订阅配置策略</span></span>
          </template>
          <div class="tab-content-wrapper">
             <div class="tab-desc">新增订阅时，根据剧名匹配规则，自动设置下载器、过滤组等参数。</div>
             <div class="table-toolbar">
                <el-button type="success" size="small" :icon="Plus" @click="openAddDialog">
                  新建追更策略
                </el-button>
             </div>
          </div>
        </el-tab-pane>

        <el-tab-pane name="wash">
          <template #label>
             <span class="custom-tabs-label"><el-icon><Download /></el-icon> <span> 洗版/订阅配置策略</span></span>
          </template>
          <div class="tab-content-wrapper">
            <div class="tab-desc">订阅状态变为“已完成”时，根据规则触发洗版（下载更高质量版本）。</div>
            <div class="table-toolbar">
               <el-button type="success" size="small" :icon="Plus" @click="openAddDialog">
                 新建洗版策略
               </el-button>
            </div>
          </div>
        </el-tab-pane>

      </el-tabs>

      <el-table :data="currentSchemes" stripe style="width: 100%" row-key="name" border>
        <el-table-column label="优先级" width="80" align="center">
          <template #default="scope"><span class="index-badge">{{ scope.$index + 1 }}</span></template>
        </el-table-column>
        
        <el-table-column label="策略名称" width="150" prop="name">
          <template #default="{row}">
            <el-tag v-if="!row.keywords || row.keywords.length===0" type="info">兜底默认</el-tag>
            <span v-else style="font-weight:bold">{{ row.name }}</span>
          </template>
        </el-table-column>
        
        <el-table-column label="匹配关键词" min-width="150">
          <template #default="{row}">
            <div v-if="row.keywords && row.keywords.length">
              <el-tag v-for="k in row.keywords" :key="k" size="small" style="margin-right:4px; margin-bottom: 2px;">{{ k }}</el-tag>
            </div>
            <span v-else class="text-gray">匹配所有未命中项</span>
          </template>
        </el-table-column>
        
        <el-table-column label="执行动作" min-width="320">
          <template #default="{row}">
            <div class="action-tags">
              <div class="row-item">
                <el-tag type="warning" size="small" effect="plain">规则: {{ row.filter_groups && row.filter_groups.length ? row.filter_groups.join(',') : '未设置' }}</el-tag>
                <el-tag type="success" size="small" effect="plain">下载器: {{ row.downloader || '默认' }}</el-tag>
                <el-tag v-if="row.quality" size="small" effect="plain">{{ row.quality }}</el-tag>
              </div>
              <div v-if="row.sites && row.sites.length > 0" class="row-item" style="margin-top:4px">
                <el-tag type="danger" size="small" effect="dark">站点:</el-tag>
                <div style="display:inline-flex; flex-wrap:wrap; gap:4px; margin-left:4px">
                   <el-tag v-for="sid in row.sites" :key="sid" size="small" type="info">{{ getSiteName(sid) }}</el-tag>
                </div>
              </div>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column label="启用" width="80" align="center">
          <template #default="{row}">
            <el-switch v-model="row.active" size="small" @change="saveConfig"/>
          </template>
        </el-table-column>
        
        <el-table-column label="操作" width="160" align="center">
          <template #default="scope">
            <el-button-group class="move-btns">
              <el-button type="info" plain size="small" :icon="ArrowUp" :disabled="scope.$index === 0" @click="moveScheme(scope.$index, 'up')"/>
              <el-button type="info" plain size="small" :icon="ArrowDown" :disabled="scope.$index === currentSchemes.length - 1" @click="moveScheme(scope.$index, 'down')"/>
            </el-button-group>
            <el-button type="primary" link :icon="Edit" @click="openEditDialog(scope.$index, scope.row)"></el-button>
            <el-button type="danger" link :icon="Delete" @click="deleteScheme(scope.$index)"></el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="isEditMode ? '编辑策略' : '新建策略'" width="600px">
      <el-form label-position="top">
        <el-form-item label="策略名称">
          <el-input v-model="editingScheme.name" placeholder="例如：动漫策略 / 4K原盘" />
        </el-form-item>
        <el-form-item label="匹配关键词 (留空则作为默认兜底策略)">
          <div class="keyword-input">
            <el-tag v-for="tag in editingScheme.keywords" :key="tag" closable @close="removeKeyword(tag)" style="margin-right:5px; margin-bottom:5px">{{ tag }}</el-tag>
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
              <el-select v-model="editingScheme.sites" multiple clearable filterable placeholder="不限 (留空)" style="flex: 1" :loading="loadingRes">
                <el-option v-for="s in options.sites" :key="s.id" :label="s.name" :value="s.id" />
              </el-select>
              <el-button :icon="Refresh" circle @click="fetchResources(false)"></el-button>
            </div>
        </el-form-item>
        <el-form-item label="过滤规则组 (Filters)">
          <el-select v-model="editingScheme.filter_groups" multiple clearable filterable placeholder="请选择规则组" style="width: 100%">
             <el-option v-for="f in options.filter_groups" :key="f.name" :label="f.name" :value="f.name" />
          </el-select>
        </el-form-item>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="下载器 (Downloader)">
               <el-select v-model="editingScheme.downloader" clearable filterable placeholder="请选择下载器" style="width: 100%">
                 <el-option v-for="d in options.downloaders" :key="d.name" :label="d.name" :value="d.name" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="质量 (Quality)">
               <el-select v-model="editingScheme.quality" clearable allow-create filterable placeholder="WEB-DL" style="width: 100%">
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
.mp-container { padding: 20px; max-width: 1000px; margin: 0 auto; }
.base-card { margin-bottom: 20px; }
.card-header { display: flex; justify-content: space-between; align-items: center; font-weight: bold; }
.action-tags { display: flex; flex-direction: column; gap: 4px; padding: 4px 0; }
.row-item { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
.text-gray { color: #999; font-size: 12px; font-style: italic; }
.keyword-input { border: 1px solid #dcdfe6; padding: 5px; border-radius: 4px; min-height: 40px; }
.tip { font-size: 12px; color: #999; margin-top: 4px; }
.index-badge { background: #f0f2f5; color: #909399; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
.move-btns { margin-right: 8px; }
.custom-tabs-label { display: flex; align-items: center; gap: 5px; font-weight: 500; }

/* 修复遮挡问题的关键样式 */
.tab-content-wrapper {
  margin: 15px 0; 
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap; 
  gap: 10px;
}

.tab-desc {
  color: #909399;
  font-size: 13px;
  line-height: 1.5;
  max-width: 70%;
}

@media screen and (max-width: 768px) {
  .mp-container { padding: 10px; }
  .el-col { margin-bottom: 15px; }
  
  .tab-content-wrapper {
    flex-direction: column;
    align-items: flex-start;
  }
  
  .tab-desc {
    max-width: 100%;
    margin-bottom: 10px;
  }
  
  .table-toolbar {
    width: 100%;
    display: flex;
    justify-content: flex-end;
  }
}
</style>