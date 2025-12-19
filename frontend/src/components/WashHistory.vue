<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { Timer, RefreshLeft, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const API_URL = 'http://127.0.0.1:8000'
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

onMounted(fetchHistory)
</script>

<template>
  <div class="history-container">
    <el-card shadow="never">
      <template #header>
        <div class="header">
          <span class="title"><el-icon><Timer /></el-icon> 洗版任务历史</span>
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

        <el-table-column label="洗版条件快照" min-width="250">
          <template #default="{row}">
            <div v-if="row.wash_params" class="params-box">
              <el-tag size="small" type="info">规则: {{ row.wash_params.filter_groups?.join(',') }}</el-tag>
              <el-tag size="small" type="info">下载器: {{ row.wash_params.downloader }}</el-tag>
              <el-tag size="small" type="info" v-if="row.wash_params.quality">画质: {{ row.wash_params.quality }}</el-tag>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="120" align="center">
          <template #default="{row}">
            <el-tag v-if="row.status === 'success'" type="success" effect="dark">成功</el-tag>
            <el-tag v-else type="danger" effect="dark">失败</el-tag>
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