<script setup>
import { reactive, onMounted, ref } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Edit, Delete, ArrowUp, ArrowDown, Refresh } from '@element-plus/icons-vue'

const API_URL = 'http://127.0.0.1:8000'

// 全局配置数据
const config = reactive({
  mp_host: '',
  mp_username: '',
  mp_password: '',
  wash_schemes: [] 
})

// MP 资源选项 (用于下拉框)
const options = reactive({
  sites: [],
  filters: [],
  downloaders: []
})
const loadingRes = ref(false)

// 常用画质预设
const qualityOptions = ['WEB-DL', 'Bluray', 'UHD', 'Remux', '1080p', '2160p', '4k']

// 弹窗与编辑状态
const dialogVisible = ref(false)
const isEditMode = ref(false)
const editIndex = ref(-1)

// 当前正在编辑的策略对象
const editingScheme = reactive({
  name: '',
  keywords: [], 
  sites: [],        // 数组
  filter_groups: [], // 数组
  downloader: '',
  quality: '',
  active: true
})

// 关键词输入的临时变量
const inputKeyword = ref('')

// === 初始化 ===
onMounted(async () => {
  await loadConfig()
  fetchResources(true)
})

const loadConfig = async () => {
  try {
    const res = await axios.get(`${API_URL}/api/config`)
    // 深度合并，防止覆盖掉响应式对象
    if (res.data) {
      config.mp_host = res.data.mp_host || ''
      config.mp_username = res.data.mp_username || ''
      config.mp_password = res.data.mp_password || ''
      config.wash_schemes = res.data.wash_schemes || []
    }
  } catch(e) { ElMessage.error('加载配置失败') }
}

const saveConfig = async () => {
  try {
    await axios.post(`${API_URL}/api/config`, config)
    ElMessage.success('配置已保存')
  } catch(e) { ElMessage.error('保存失败') }
}

// === 获取 MP 资源 (站点/规则/下载器) ===
const fetchResources = async (silent=false) => {
  if(!config.mp_host) return
  loadingRes.value = true
  try {
    const res = await axios.get(`${API_URL}/api/resources`)
    if (res.data) {
      options.sites = res.data.sites || []
      options.filters = res.data.filters || []
      options.downloaders = res.data.downloaders || []
      if(!silent) ElMessage.success('MP 资源同步完成')
    }
  } catch(e) {
    if(!silent) ElMessage.error('同步失败，请检查 MP 连接')
  } finally {
    loadingRes.value = false
  }
}

// 辅助显示站点名称
const getSiteName = (id) => {
  const s = options.sites.find(item => item.id === id)
  return s ? s.name : id
}

// === 策略操作 ===
const openAddDialog = () => {
  isEditMode.value = false
  // 重置表单，所有数组字段初始化为空数组
  Object.assign(editingScheme, { 
    name: '新策略', 
    keywords: [], 
    sites: [], 
    filter_groups: [], 
    downloader: '', 
    quality: '', 
    active: true 
  })
  dialogVisible.value = true
  // 如果下拉框没数据，尝试加载一次
  if (options.filters.length === 0) fetchResources(true)
}

const openEditDialog = (index, row) => {
  isEditMode.value = true
  editIndex.value = index
  // 深拷贝数据到编辑对象
  Object.assign(editingScheme, JSON.parse(JSON.stringify(row)))
  dialogVisible.value = true
}

const deleteScheme = async (index) => {
  await ElMessageBox.confirm('确定删除该策略吗？', '提示', { type: 'warning' })
  config.wash_schemes.splice(index, 1)
  saveConfig()
}

const confirmScheme = () => {
  // 深拷贝一份，断开引用
  const finalScheme = JSON.parse(JSON.stringify(editingScheme))
  
  if(isEditMode.value) {
    config.wash_schemes[editIndex.value] = finalScheme
  } else {
    config.wash_schemes.push(finalScheme)
  }
  
  dialogVisible.value = false
  saveConfig()
}

const moveScheme = (index, direction) => {
  const arr = config.wash_schemes
  if (direction === 'up' && index > 0) {
    [arr[index], arr[index - 1]] = [arr[index - 1], arr[index]]
  } else if (direction === 'down' && index < arr.length - 1) {
    [arr[index], arr[index + 1]] = [arr[index + 1], arr[index]]
  }
  saveConfig()
}

// === 关键词 Tag 处理 ===
const addKeyword = () => {
  if (inputKeyword.value) {
    if (!editingScheme.keywords.includes(inputKeyword.value)) {
      editingScheme.keywords.push(inputKeyword.value)
    }
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
      <template #header>
        <div class="card-header"><span>🌐 MoviePilot 连接</span></div>
      </template>
      <el-form :inline="true">
        <el-form-item label="地址"><el-input v-model="config.mp_host" placeholder="http://ip:3000" /></el-form-item>
        <el-form-item label="用户"><el-input v-model="config.mp_username" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="config.mp_password" type="password" show-password /></el-form-item>
        <el-form-item><el-button type="primary" @click="saveConfig">保存连接</el-button></el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="hover" class="scheme-card">
      <template #header>
        <div class="card-header">
          <span>📋 洗版策略管理 (按从上到下顺序匹配)</span>
          <el-button type="success" size="small" :icon="Plus" @click="openAddDialog">新建策略</el-button>
        </div>
      </template>

      <el-table :data="config.wash_schemes" stripe style="width: 100%" row-key="name">
        <el-table-column label="顺序" width="60" align="center">
          <template #default="scope"><span class="index-badge">{{ scope.$index + 1 }}</span></template>
        </el-table-column>

        <el-table-column label="策略名称" width="120" prop="name">
          <template #default="{row}">
            <el-tag v-if="!row.keywords || row.keywords.length===0" type="info">兜底默认</el-tag>
            <span v-else style="font-weight:bold">{{ row.name }}</span>
          </template>
        </el-table-column>
        
        <el-table-column label="匹配关键词" min-width="150">
          <template #default="{row}">
            <div v-if="row.keywords && row.keywords.length">
              <el-tag v-for="k in row.keywords" :key="k" size="small" style="margin-right:4px">{{ k }}</el-tag>
            </div>
            <span v-else class="text-gray">无关键词 (匹配剩余所有)</span>
          </template>
        </el-table-column>

        <el-table-column label="执行动作" min-width="320">
          <template #default="{row}">
            <div class="action-tags">
              <div class="row-item">
                <el-tag type="warning" size="small" effect="plain">
                  规则: {{ row.filter_groups && row.filter_groups.length ? row.filter_groups.join(',') : '未设置' }}
                </el-tag>
                <el-tag type="success" size="small" effect="plain">
                  下载器: {{ row.downloader || '默认' }}
                </el-tag>
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

        <el-table-column label="状态" width="70">
          <template #default="{row}">
            <el-switch v-model="row.active" size="small" @change="saveConfig"/>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="180" align="right">
          <template #default="scope">
            <el-button-group class="move-btns">
              <el-button type="info" plain size="small" :icon="ArrowUp" :disabled="scope.$index === 0" @click="moveScheme(scope.$index, 'up')"/>
              <el-button type="info" plain size="small" :icon="ArrowDown" :disabled="scope.$index === config.wash_schemes.length - 1" @click="moveScheme(scope.$index, 'down')"/>
            </el-button-group>
            <el-divider direction="vertical" />
            <el-button type="primary" link :icon="Edit" @click="openEditDialog(scope.$index, scope.row)"></el-button>
            <el-button type="danger" link :icon="Delete" @click="deleteScheme(scope.$index)"></el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="isEditMode ? '编辑策略' : '新建策略'" width="600px">
      <el-form label-position="top">
        <el-form-item label="策略名称">
          <el-input v-model="editingScheme.name" placeholder="例如：国产剧方案" />
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
          <div class="tip">匹配范围：剧集名称、MP分类、Emby媒体库名称</div>
        </el-form-item>

        <el-divider>执行参数 (自动同步 MP 数据)</el-divider>

        <el-row :gutter="20">
          <el-col :span="24">
             <el-form-item label="指定站点 (可选)">
                <div style="display: flex; gap: 10px; width: 100%;">
                  <el-select 
                    v-model="editingScheme.sites" 
                    multiple 
                    clearable 
                    filterable
                    placeholder="不限 (留空)" 
                    style="flex: 1"
                    :loading="loadingRes"
                  >
                    <el-option v-for="s in options.sites" :key="s.id" :label="s.name" :value="s.id" />
                  </el-select>
                  <el-button :icon="Refresh" circle @click="fetchResources(false)" title="刷新资源"></el-button>
                </div>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="洗版规则组 (Filters)">
          <el-select 
            v-model="editingScheme.filter_groups" 
            multiple 
            clearable 
            filterable
            placeholder="请选择规则组" 
            style="width: 100%"
          >
             <el-option v-for="f in options.filters" :key="f.name" :label="f.name" :value="f.name" />
          </el-select>
        </el-form-item>
        
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="下载器 (Downloader)">
               <el-select 
                v-model="editingScheme.downloader" 
                clearable 
                filterable
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
                clearable 
                allow-create 
                filterable
                placeholder="WEB-DL / 4k" 
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
.mp-container { padding: 20px; max-width: 1000px; margin: 0 auto; }
.base-card { margin-bottom: 20px; }
.card-header { display: flex; justify-content: space-between; align-items: center; font-weight: bold; }
.action-tags { display: flex; flex-direction: column; gap: 4px; }
.row-item { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
.text-gray { color: #999; font-size: 12px; font-style: italic; }
.keyword-input { border: 1px solid #dcdfe6; padding: 5px; border-radius: 4px; min-height: 40px; }
.tip { font-size: 12px; color: #999; margin-top: 4px; }
.index-badge { background: #f0f2f5; color: #909399; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
.move-btns { margin-right: 8px; }
</style>