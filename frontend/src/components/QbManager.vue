<template>
  <div class="qb-manager">
    <el-tabs v-model="activeTab" class="qb-tabs">
      <!-- 种子管理标签页 -->
      <el-tab-pane label="种子管理" name="torrents">
        <div class="toolbar">
          <el-select v-model="selectedQb" placeholder="选择 qBittorrent 实例" style="width: 200px" @change="fetchQbData">
            <el-option v-for="item in qbConfigs" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
          
          <el-input 
            v-model="filterName" 
            placeholder="搜索种子名称..." 
            style="width: 200px" 
            clearable 
            @keyup.enter="fetchTorrents"
            @clear="fetchTorrents"
          />

          <el-select v-model="filterTag" placeholder="标签过滤" clearable style="width: 150px" @change="fetchTorrents">
            <el-option v-for="tag in currentTags" :key="tag" :label="tag" :value="tag" />
          </el-select>

          <el-select v-model="filterCategory" placeholder="分类过滤" clearable style="width: 150px" @change="fetchTorrents">
            <el-option v-for="cat in currentCategories" :key="cat" :label="cat" :value="cat" />
          </el-select>

          <el-button type="primary" @click="fetchTorrents" :loading="loading">刷新</el-button>
          
          <el-popconfirm title="确定要删除选中的种子吗？" @confirm="batchDelete(false)">
            <template #reference>
              <el-button type="danger" :disabled="!selectedHashes.length">批量删除</el-button>
            </template>
          </el-popconfirm>

          <el-popconfirm title="确定要删除选中的种子及其文件吗？" @confirm="batchDelete(true)">
            <template #reference>
              <el-button type="danger" :disabled="!selectedHashes.length">删除种子及文件</el-button>
            </template>
          </el-popconfirm>
        </div>

        <el-table 
          v-loading="loading" 
          :data="torrents" 
          style="width: 100%; margin-top: 20px" 
          @selection-change="handleSelectionChange"
          height="calc(100vh - 280px)"
        >
          <el-table-column type="selection" width="55" />
          <el-table-column prop="name" label="名称" min-width="400" sortable show-overflow-tooltip />
          <el-table-column prop="size" label="大小" width="100">
            <template #default="{ row }">
              {{ formatBytes(row.size) }}
            </template>
          </el-table-column>
          <el-table-column prop="progress" label="进度" width="70">
            <template #default="{ row }">
              <el-progress :percentage="Math.round(row.progress * 100)" />
            </template>
          </el-table-column>
          <el-table-column prop="state" label="状态" width="120">
            <template #default="{ row }">
              <el-tag :type="getStateType(row.state)" size="small">
                {{ formatState(row.state) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="category" label="分类" width="120" />
          <el-table-column prop="tags" label="标签" min-width="150" show-overflow-tooltip />
          <el-table-column prop="ratio" label="分享率" width="100">
            <template #default="{ row }">
              {{ row.ratio.toFixed(2) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="viewFiles(row)">查看文件</el-button>
              <el-button link type="danger" @click="deleteOne(row, false)">删除</el-button>
              <el-button link type="danger" @click="deleteOne(row, true)">删除文件</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 实例配置标签页 -->
      <el-tab-pane label="实例配置" name="configs">
        <div class="toolbar">
          <el-button type="primary" @click="showAddDialog">新增实例</el-button>
        </div>

        <el-table :data="qbConfigs" style="width: 100%; margin-top: 20px">
          <el-table-column prop="name" label="名称" />
          <el-table-column prop="host" label="地址" />
          <el-table-column prop="username" label="用户名" />
          <el-table-column prop="active" label="激活">
            <template #default="{ row }">
              <el-switch v-model="row.active" @change="updateConfig(row)" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150">
            <template #default="{ row }">
              <el-button link type="primary" @click="editConfig(row)">编辑</el-button>
              <el-popconfirm title="确定删除该配置吗？" @confirm="deleteConfig(row.id)">
                <template #reference>
                  <el-button link type="danger">删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="fileDialogVisible" title="文件列表" width="800px">
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

    <!-- 配置弹窗 -->
    <el-dialog v-model="dialogVisible" :title="isEdit ? '编辑实例' : '新增实例'" width="500px">
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
import { ref, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'
// 新增变量
const fileDialogVisible = ref(false)
const fileList = ref([])
const filesLoading = ref(false)

// 新增函数：查看文件
const viewFiles = async (row) => {
  fileList.value = [] // 清空旧数据
  fileDialogVisible.value = true // 打开弹窗
  filesLoading.value = true // 显示加载圈
  
  try {
    // 调用刚才写的后端接口
    const res = await axios.get(`/api/qb/${selectedQb.value}/torrents/${row.hash}/files`)
    fileList.value = res.data
  } catch (err) {
    ElMessage.error('获取文件列表失败')
  } finally {
    filesLoading.value = false
  }
}

const activeTab = ref('torrents')
const qbConfigs = ref([])
const loading = ref(false)
const selectedQb = ref('')
const torrents = ref([])
const selectedHashes = ref([])
const currentTags = ref([])
const currentCategories = ref([])
const filterTag = ref('')
const filterCategory = ref('')
// 3. 新增：名称筛选变量
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

// 加载配置
const fetchConfigs = async () => {
  try {
    const res = await axios.get('/api/qb/configs')
    qbConfigs.value = res.data
    if (qbConfigs.value.length && !selectedQb.value) {
      selectedQb.value = qbConfigs.value[0].id
      fetchQbData()
    }
  } catch (err) {
    ElMessage.error('获取配置失败')
  }
}

// 4. 新增：状态映射字典
const STATE_MAP = {
  'stalledUP': '做种',       // 做种（无连接）
  'uploading': '上传中',     // 做种（有连接）
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

// 5. 新增：状态格式化函数
const formatState = (state) => {
  return STATE_MAP[state] || state
}

// 6. 新增：根据状态返回 Tag 颜色类型
const getStateType = (state) => {
  if (['stalledUP', 'uploading'].includes(state)) return 'success'
  if (['downloading', 'metaDL'].includes(state)) return 'primary'
  if (['error', 'missingFiles'].includes(state)) return 'danger'
  if (['pausedDL', 'pausedUP'].includes(state)) return 'info'
  return 'warning'
}

// 获取标签和分类
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

// 获取种子列表
const fetchTorrents = async () => {
  if (!selectedQb.value) return
  loading.value = true
  try {
    const params = {}
    if (filterTag.value) params.tag = filterTag.value
    if (filterName.value) params.keyword = filterName.value // 传递关键字
    if (filterCategory.value) params.category = filterCategory.value
    
    const res = await axios.get(`/api/qb/${selectedQb.value}/torrents`, { params })
    torrents.value = res.data
  } catch (err) {
    ElMessage.error('获取种子列表失败')
  } finally {
    loading.value = false
  }
}

const handleSelectionChange = (val) => {
  selectedHashes.value = val.map(i => i.hash)
}

const batchDelete = async (deleteFiles) => {
  if (!selectedHashes.value.length) return
  try {
    await axios.post(`/api/qb/${selectedQb.value}/torrents/delete`, {
      hashes: selectedHashes.value,
      delete_files: deleteFiles
    })
    ElMessage.success('删除成功')
    fetchTorrents()
  } catch (err) {
    ElMessage.error('删除失败')
  }
}

const deleteOne = (row, deleteFiles) => {
  ElMessageBox.confirm(
    `确定删除种子 ${row.name} ${deleteFiles ? '及其文件' : ''} 吗?`,
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

// 配置相关
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
  fetchConfigs()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.qb-tabs {
  background: #fff;
  padding: 20px;
  border-radius: 8px;
}
</style>
