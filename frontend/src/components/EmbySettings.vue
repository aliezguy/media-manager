<script setup>
import { reactive, onMounted, ref } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { Connection, Setting } from '@element-plus/icons-vue'

const API_URL = ''
const loading = ref(false)
const config = reactive({
  emby_host: '',
  emby_api_key: '',
  emby_user_id: '',
  sf_api_key: ''
})

onMounted(async () => {
  try {
    const res = await axios.get(`${API_URL}/api/config`)
    Object.assign(config, res.data)
  } catch(e) {}
})

const saveConfig = async () => {
  try {
    await axios.post(`${API_URL}/api/config`, config)
    ElMessage.success('配置已保存')
  } catch(e) { ElMessage.error('保存失败') }
}

const testConnection = async () => {
  loading.value = true
  try {
    await axios.post(`${API_URL}/api/libraries`, config)
    ElMessage.success('连接成功，API Key 有效')
    await saveConfig()
  } catch (e) { ElMessage.error('连接失败: ' + e.message) }
  finally { loading.value = false }
}
</script>

<template>
  <div class="settings-container">
    <el-card shadow="hover" class="box-card">
      <template #header>
        <div class="card-header">
          <span><el-icon><Connection /></el-icon> Emby 连接设置</span>
        </div>
      </template>
      
      <el-form label-position="top" size="large">
        <el-row :gutter="40">
          <el-col :span="12">
            <el-form-item label="Emby 服务器地址 (URL)">
              <el-input v-model="config.emby_host" placeholder="http://192.168.1.5:8096" />
              <div class="tip">内网 IP 或域名，包含端口号</div>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="外网访问 URL (可选)">
              <el-input disabled placeholder="暂未启用" />
              <div class="tip">用于远程封面图加载等功能</div>
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="40">
          <el-col :span="12">
            <el-form-item label="Emby API Key">
              <el-input v-model="config.emby_api_key" type="password" show-password />
              <div class="tip">在 Emby 后台 -> 高级 -> API 密钥 中生成</div>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="Emby 用户 ID">
              <el-input v-model="config.emby_user_id" />
              <div class="tip">打开用户详情页，浏览器地址栏最后的 ID</div>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">智能服务</el-divider>

        <el-row :gutter="40">
          <el-col :span="24">
            <el-form-item label="SiliconFlow (AI) API Key">
              <el-input v-model="config.sf_api_key" type="password" show-password placeholder="sk-..." />
              <div class="tip">用于调用 DeepSeek V3 进行标签分析</div>
            </el-form-item>
          </el-col>
        </el-row>

        <div class="actions">
          <el-button type="primary" :loading="loading" @click="testConnection">测试连接</el-button>
          <el-button type="success" @click="saveConfig">保存所有设置</el-button>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.settings-container { max-width: 1000px; margin: 0 auto; padding: 20px; }
.card-header { font-size: 18px; font-weight: bold; display: flex; align-items: center; gap: 10px; color: #409EFF; }
.tip { font-size: 12px; color: #909399; margin-top: 5px; }
.actions { margin-top: 30px; display: flex; justify-content: flex-end; gap: 15px; }
</style>