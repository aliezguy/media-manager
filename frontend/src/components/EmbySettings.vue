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
    <!-- ==================== Emby 连接设置 ==================== -->
    <div class="section-card">
      <div class="section-header">
        <span class="section-icon">
          <el-icon :size="18"><Connection /></el-icon>
        </span>
        <span class="section-title">Emby 连接设置</span>
      </div>

      <div class="section-body">
        <el-form label-position="top" size="large">
          <el-row :gutter="40">
            <el-col :xs="24" :sm="12">
              <el-form-item label="Emby 服务器地址 (URL)">
                <el-input
                  v-model="config.emby_host"
                  placeholder="http://192.168.1.5:8096"
                />
                <div class="tip">内网 IP 或域名，包含端口号</div>
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12">
              <el-form-item label="外网访问 URL (可选)">
                <el-input disabled placeholder="暂未启用" />
                <div class="tip">用于远程封面图加载等功能</div>
              </el-form-item>
            </el-col>
          </el-row>

          <el-row :gutter="40">
            <el-col :xs="24" :sm="12">
              <el-form-item label="Emby API Key">
                <el-input
                  v-model="config.emby_api_key"
                  type="password"
                  show-password
                />
                <div class="tip">在 Emby 后台 → 高级 → API 密钥 中生成</div>
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12">
              <el-form-item label="Emby 用户 ID">
                <el-input v-model="config.emby_user_id" />
                <div class="tip">打开用户详情页，浏览器地址栏最后的 ID</div>
              </el-form-item>
            </el-col>
          </el-row>

          <!-- Divider -->
          <div class="section-divider">
            <span class="divider-label">智能服务</span>
          </div>

          <el-row :gutter="40">
            <el-col :span="24">
              <el-form-item label="SiliconFlow (AI) API Key">
                <el-input
                  v-model="config.sf_api_key"
                  type="password"
                  show-password
                  placeholder="sk-..."
                />
                <div class="tip">用于调用 DeepSeek V3 进行标签分析</div>
              </el-form-item>
            </el-col>
          </el-row>

          <!-- Actions -->
          <div class="actions">
            <button
              class="btn-pill btn-pill-blue"
              :disabled="loading"
              @click="testConnection"
            >
              <el-icon v-if="loading" :size="15" class="is-loading"><Connection /></el-icon>
              <el-icon v-else :size="15"><Connection /></el-icon>
              测试连接
            </button>
            <button class="btn-pill btn-pill-green" @click="saveConfig">
              <el-icon :size="15"><Setting /></el-icon>
              保存所有设置
            </button>
          </div>
        </el-form>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ==================== Layout ==================== */
.settings-container {
  max-width: 1000px;
  margin: 0 auto;
  padding: 16px 20px;
}

/* ==================== Section Card ==================== */
.section-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xl);
  overflow: hidden;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 24px;
  border-bottom: 1px solid var(--border-color);
}

.section-icon {
  color: var(--accent-blue);
  display: flex;
}

.section-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--text-primary);
}

.section-body {
  padding: 24px;
}

/* ==================== Tip ==================== */
.tip {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 5px;
  line-height: 1.5;
}

/* ==================== Divider ==================== */
.section-divider {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 28px 0 20px;
}
.section-divider::before,
.section-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--border-color);
}
.divider-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* ==================== Actions ==================== */
.actions {
  margin-top: 32px;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

/* Pill buttons (component-scoped) */
.btn-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 22px;
  border: none;
  border-radius: var(--radius-full);
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.btn-pill:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-pill-blue {
  background: var(--accent-blue);
  color: #fff;
}
.btn-pill-blue:hover:not(:disabled) {
  background: #2563eb;
  box-shadow: var(--shadow-glow-blue);
}

.btn-pill-green {
  background: var(--accent-green);
  color: #fff;
}
.btn-pill-green:hover:not(:disabled) {
  background: #059669;
  box-shadow: var(--shadow-glow-green);
}

/* ==================== Mobile ==================== */
@media screen and (max-width: 768px) {
  .settings-container {
    padding: 8px;
  }

  .section-header {
    padding: 12px 16px;
  }

  .section-body {
    padding: 16px;
  }

  .section-title {
    font-size: 15px;
  }

  .actions {
    flex-direction: column;
  }

  .btn-pill {
    justify-content: center;
    width: 100%;
    padding: 12px;
  }
}
</style>
