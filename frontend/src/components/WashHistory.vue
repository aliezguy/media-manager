<script setup>
import { ref, reactive, onMounted } from 'vue'
import axios from 'axios'
import { Timer, RefreshLeft, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const API_URL = ''
const historyData = ref([])
const loading = ref(false)

const fetchHistory = async () => {
  loading.value = true
  try {
    const res = await axios.get(`${API_URL}/api/history`)
    historyData.value = res.data
  } catch (e) {
    ElMessage.error('获取历史记录失败')
  } finally {
    loading.value = false
  }
}

const clearHistory = async () => {
  try {
    await ElMessageBox.confirm('确定要清空所有记录吗？', '提示', { type: 'warning' })
    await axios.delete(`${API_URL}/api/history`)
    historyData.value = []
    ElMessage.success('已清空')
  } catch {}
}

const formatDate = (dateStr) => {
  const d = new Date(dateStr)
  return d.toLocaleString()
}
// 1. 定义一个变量存储站点字典
const siteOptions = ref([])

// 2. 新增：获取站点资源的函数 (复用后端的 /api/resources 接口)
const fetchResources = async () => {
  try {
    const res = await axios.get(`${API_URL}/api/resources`)
    if (res.data && res.data.sites) {
      siteOptions.value = res.data.sites
    }
  } catch (e) {
    // 资源获取失败通常不阻断主流程，可以仅仅console记录
    console.error('获取站点列表失败', e)
  }
}

// 3. 新增：核心辅助函数，将 ID 数组转为 名称字符串
const formatSiteNames = (siteIds) => {
  if (!siteIds || !Array.isArray(siteIds) || siteIds.length === 0) return '无'
  
  // 遍历 ID，去 siteOptions 里找对应的 name
  const names = siteIds.map(id => {
    // 注意：后端返回的id可能是数字也可能是字符串，这里用 == 比较宽松匹配，或者都转字符串
    const found = siteOptions.value.find(s => String(s.id) === String(id))
    return found ? found.name : id // 找到了返回名字，找不到返回原始ID
  })
  
  return names.join(' / ') // 用斜杠分隔，例如 "憨憨 / 彩虹岛"
}

// 4. 修改 onMounted，同时加载历史和资源
onMounted(() => {
  fetchHistory()
  fetchResources() // <--- 增加这一行
})


</script>

<template>
  <div class="history-container">
    <el-card shadow="never">
      <template #header>
        <div class="header">
          <span class="title"><el-icon><Timer /></el-icon> 订阅任务历史</span>
          <div class="btns">
            <el-button :icon="RefreshLeft" circle @click="fetchHistory" />
            <el-button type="danger" plain :icon="Delete" size="small" @click="clearHistory">清空</el-button>
          </div>
        </div>
      </template>

      <el-table :data="historyData" stripe style="width: 100%" v-loading="loading">
        <el-table-column label="时间" width="180">
          <template #default="{row}">
            <span class="time-text">{{ formatDate(row.created_at) }}</span>
          </template>
        </el-table-column>
        
        <el-table-column label="剧集信息" min-width="180">
          <template #default="{row}">
            <div class="name">{{ row.name }}</div>
            <div class="season">第 {{ row.season }} 季 (TMDB: {{ row.tmdb_id }})</div>
          </template>
        </el-table-column>

        <el-table-column label="洗版条件快照" min-width="350">
          <template #default="{row}">
            <div v-if="row.wash_params" class="params-box">
              <el-tag size="small" type="warning" effect="dark" v-if="row.wash_params.scheme">
                策略: {{ row.wash_params.scheme }}
              </el-tag>
              
              <el-tag size="small" type="info" effect="plain" v-if="row.wash_params.filter_groups">
                规则: {{ row.wash_params.filter_groups?.join(',') }}
              </el-tag>
              
              <el-tag size="small" type="success" effect="plain" v-if="row.wash_params.downloader">
                下载器: {{ row.wash_params.downloader }}
              </el-tag>
              
              <el-tag size="small" type="danger" effect="plain" v-if="row.wash_params.quality">
                画质: {{ row.wash_params.quality }}
              </el-tag>

              <el-tag size="small" color="#f4f4f5" style="color:#909399" v-if="row.wash_params.sites && row.wash_params.sites.length">
                站点: {{ formatSiteNames(row.wash_params.sites) }}
              </el-tag>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="120" align="center">
          <template #default="{row}">
            <el-tag v-if="row.status === 'success'" type="success" effect="dark">成功</el-tag>
            <el-tag v-else type="danger" effect="dark">失败</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="类型" width="100" align="center">
          <template #default="{row}">
            <el-tag v-if="row.wash_type === 'complete'" type="warning" effect="plain">完结洗版</el-tag>
            <el-tag v-else-if="row.wash_type === 'new_sub'" type="primary" effect="plain">新增配置</el-tag>
            <el-tag v-else type="info">未知</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="反馈信息" min-width="200">
          <template #default="{row}">
            <span :class="{'err-msg': row.status !== 'success'}">{{ row.message }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.history-container { padding: 20px; max-width: 1200px; margin: 0 auto; }
.header { display: flex; justify-content: space-between; align-items: center; }
.title { font-size: 16px; font-weight: bold; display: flex; align-items: center; gap: 8px; }
.time-text { font-size: 13px; color: #606266; }
.name { font-weight: bold; font-size: 14px; }
.season { font-size: 12px; color: #909399; }
.params-box { display: flex; flex-wrap: wrap; gap: 4px; }
.err-msg { color: #F56C6C; font-size: 12px; }
</style>