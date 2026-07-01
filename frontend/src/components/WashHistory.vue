<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { Timer, RefreshLeft, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const API_URL = ''
const historyData = ref([])
const loading = ref(false)
const siteOptions = ref([])

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

const fetchResources = async () => {
  try {
    const res = await axios.get(`${API_URL}/api/resources`)
    if (res.data && res.data.sites) {
      siteOptions.value = res.data.sites
    }
  } catch (e) {
    console.error('获取站点列表失败', e)
  }
}

const formatSiteNames = (siteIds) => {
  if (!siteIds || !Array.isArray(siteIds) || siteIds.length === 0) return ''
  const names = siteIds.map(id => {
    const found = siteOptions.value.find(s => String(s.id) === String(id))
    return found ? found.name : id
  })
  return names.join(' / ')
}

onMounted(() => {
  fetchHistory()
  fetchResources()
})
</script>

<template>
  <div class="history-container">
    <!-- 操作栏 -->
    <div class="history-toolbar">
      <span class="history-title"><el-icon><Timer /></el-icon> 订阅任务历史</span>
      <div class="btn-group">
        <el-button :icon="RefreshLeft" circle size="small" @click="fetchHistory" />
        <el-button type="danger" plain :icon="Delete" size="small" @click="clearHistory">清空</el-button>
      </div>
    </div>

    <!-- ==================== 桌面端：表格 ==================== -->
    <div class="desktop-only">
      <el-table :data="historyData" stripe style="width: 100%" v-loading="loading" size="small">
        <el-table-column label="时间" width="160">
          <template #default="{row}">
            <span class="time-text">{{ formatDate(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="剧集信息" min-width="160">
          <template #default="{row}">
            <div class="name">{{ row.name }}</div>
            <div class="season">第 {{ row.season }} 季 (TMDB: {{ row.tmdb_id }})</div>
          </template>
        </el-table-column>
        <el-table-column label="洗版条件" min-width="280">
          <template #default="{row}">
            <div v-if="row.wash_params" class="params-box">
              <el-tag size="small" type="warning" effect="dark" v-if="row.wash_params.scheme">策略: {{ row.wash_params.scheme }}</el-tag>
              <el-tag size="small" type="info" v-if="row.wash_params.filter_groups">规则: {{ row.wash_params.filter_groups?.join(',') }}</el-tag>
              <el-tag size="small" type="success" v-if="row.wash_params.downloader">下载器: {{ row.wash_params.downloader }}</el-tag>
              <el-tag size="small" type="danger" v-if="row.wash_params.quality">画质: {{ row.wash_params.quality }}</el-tag>
              <el-tag size="small" v-if="row.wash_params.sites?.length" style="color:#909399">站点: {{ formatSiteNames(row.wash_params.sites) }}</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80" align="center">
          <template #default="{row}">
            <el-tag v-if="row.status === 'success'" type="success" size="small" effect="dark">成功</el-tag>
            <el-tag v-else type="danger" size="small" effect="dark">失败</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="90" align="center">
          <template #default="{row}">
            <el-tag v-if="row.wash_type === 'complete'" type="warning" size="small">完结洗版</el-tag>
            <el-tag v-else-if="row.wash_type === 'new_sub'" type="primary" size="small">新增配置</el-tag>
            <el-tag v-else type="info" size="small">未知</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="反馈" min-width="180">
          <template #default="{row}">
            <span :class="{'err-msg': row.status !== 'success'}">{{ row.message }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- ==================== 移动端：卡片列表 ==================== -->
    <div class="mobile-only card-list">
      <div
        v-for="row in historyData"
        :key="row.id"
        class="history-card"
        :class="{ 'card-failed': row.status !== 'success' }"
      >
        <div class="card-top">
          <span class="card-name">{{ row.name }} <small>S{{ row.season }}</small></span>
          <el-tag v-if="row.status === 'success'" type="success" size="small">成功</el-tag>
          <el-tag v-else type="danger" size="small">失败</el-tag>
        </div>
        <div class="card-meta">
          <span>{{ formatDate(row.created_at) }}</span>
          <el-tag v-if="row.wash_type === 'complete'" type="warning" size="small">完结洗版</el-tag>
          <el-tag v-else-if="row.wash_type === 'new_sub'" type="primary" size="small">新增配置</el-tag>
        </div>
        <div v-if="row.wash_params" class="card-tags">
          <el-tag size="small" type="warning" v-if="row.wash_params.scheme">{{ row.wash_params.scheme }}</el-tag>
          <el-tag size="small" type="info" v-if="row.wash_params.filter_groups">{{ row.wash_params.filter_groups?.join(',') }}</el-tag>
          <el-tag size="small" type="success" v-if="row.wash_params.downloader">{{ row.wash_params.downloader }}</el-tag>
        </div>
        <div class="card-msg">{{ row.message }}</div>
      </div>
      <el-empty v-if="!loading && !historyData.length" description="暂无记录" :image-size="80" />
    </div>
  </div>
</template>

<style scoped>
.history-container {
  padding: 12px;
  max-width: 100%;
}

.history-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding: 0 4px;
  flex-wrap: wrap;
  gap: 8px;
}
.history-title {
  font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 6px;
}
.btn-group {
  display: flex; gap: 6px;
}

/* 桌面表格 */
.time-text { font-size: 12px; color: #606266; }
.name { font-weight: 600; font-size: 13px; }
.season { font-size: 12px; color: #909399; }
.params-box { display: flex; flex-wrap: wrap; gap: 3px; }
.err-msg { color: #F56C6C; font-size: 12px; }

/* 显示控制 */
.desktop-only { display: block; }
.mobile-only { display: none; }

/* ==================== 移动端卡片 ==================== */
@media screen and (max-width: 768px) {
  .history-container {
    padding: 0;
  }
  .history-toolbar {
    padding: 8px 4px;
    margin-bottom: 8px;
  }
  .desktop-only { display: none; }
  .mobile-only { display: block; }

  .card-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .history-card {
    background: #fff;
    border-radius: 10px;
    padding: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    border-left: 3px solid #67C23A;
  }
  .history-card.card-failed {
    border-left-color: #F56C6C;
  }

  .card-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
  }
  .card-name {
    font-weight: 600; font-size: 14px; color: #303133;
  }
  .card-name small {
    font-weight: 400; color: #909399; font-size: 12px;
  }

  .card-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    color: #909399;
    margin-bottom: 6px;
  }

  .card-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-bottom: 4px;
  }

  .card-msg {
    font-size: 12px;
    color: #606266;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}
</style>
