<template>
  <div class="qb-config">
    <!-- ==================== 实例配置页头 ==================== -->
    <div class="configs-header">
      <h3 class="configs-title">qBittorrent 实例列表</h3>
      <button class="btn-pill btn-pill-primary" @click="showAddDialog">
        <el-icon :size="16"><Plus /></el-icon>
        新增实例
      </button>
    </div>

    <!-- ==================== 实例卡片列表 ==================== -->
    <div class="config-cards">
      <div v-for="cfg in qbConfigs" :key="cfg.id" class="config-card">
        <div class="cfg-left">
          <div class="cfg-icon-circle bg-emerald-500/10 text-emerald-400" :class="{ active: cfg.active }">
            <el-icon :size="20"><Monitor /></el-icon>
          </div>
        </div>
        <div class="cfg-body">
          <div class="cfg-name">{{ cfg.name }}</div>
          <div class="cfg-url">{{ cfg.host }}</div>
          <div class="cfg-user">{{ cfg.username || '未设置用户名' }}</div>
        </div>
        <div class="cfg-right">
          <el-switch
            v-model="cfg.active"
            class="cfg-switch"
            @change="updateConfig(cfg)"
          />
          <div class="cfg-actions">
            <button class="ac-btn" @click.stop="editConfig(cfg)" title="编辑">
              <el-icon :size="16"><Edit /></el-icon>
            </button>
            <el-popconfirm
              title="确定删除该配置吗？"
              @confirm="deleteConfig(cfg.id)"
            >
              <template #reference>
                <button class="ac-btn ac-btn-warn" title="删除">
                  <el-icon :size="16"><Delete /></el-icon>
                </button>
              </template>
            </el-popconfirm>
          </div>
        </div>
      </div>

      <div v-if="!qbConfigs.length" class="empty-state">
        <div class="empty-icon-circle">
          <el-icon :size="36"><Setting /></el-icon>
        </div>
        <p class="empty-title">暂无实例</p>
        <p class="empty-desc">点击上方按钮新增 qBittorrent 实例配置</p>
      </div>
    </div>

    <!-- ==================== 新增/编辑实例弹窗 ==================== -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEdit ? '编辑实例' : '新增实例'"
      width="500px"
    >
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

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Monitor, Setting, Plus, Edit, Delete } from '@element-plus/icons-vue'
import axios from 'axios'

// ==================== Type Definitions ====================

/** qBittorrent 实例配置 */
interface QbConfig {
  id: string
  name: string
  host: string
  username: string
  password?: string
  active: boolean
}

/** 实例配置编辑表单（id 在编辑时才有） */
type ConfigForm = Omit<QbConfig, 'id'> & { id?: string }

const API_URL = ''

// ==================== Reactive State ====================
const qbConfigs = ref<QbConfig[]>([])
const loading = ref(false)

const dialogVisible = ref(false)
const isEdit = ref(false)
const currentConfig = ref<ConfigForm>({
  name: '',
  host: '',
  username: 'admin',
  password: '',
  active: true
})

// ==================== Data Fetching ====================
const fetchConfigs = async (autoLoad = false) => {
  loading.value = true
  try {
    const res = await axios.get<QbConfig[]>(`${API_URL}/api/qb/configs`)
    qbConfigs.value = res.data
    if (autoLoad && qbConfigs.value.length) {
      // 无需自动选中（种子管理页负责实例选择），仅保证列表加载完成
    }
  } catch (err) {
    ElMessage.error('获取配置失败')
  } finally {
    loading.value = false
  }
}

// ==================== Config CRUD ====================
const showAddDialog = () => {
  isEdit.value = false
  currentConfig.value = { name: '', host: '', username: 'admin', password: '', active: true }
  dialogVisible.value = true
}

const editConfig = (row: QbConfig) => {
  isEdit.value = true
  currentConfig.value = { ...row }
  dialogVisible.value = true
}

const saveQbConfig = async () => {
  try {
    if (isEdit.value) {
      await axios.put(`${API_URL}/api/qb/configs/${currentConfig.value.id}`, currentConfig.value)
    } else {
      await axios.post(`${API_URL}/api/qb/configs`, currentConfig.value)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    fetchConfigs()
  } catch (err) {
    ElMessage.error('保存失败')
  }
}

const updateConfig = async (row: QbConfig) => {
  try {
    await axios.put(`${API_URL}/api/qb/configs/${row.id}`, row)
    ElMessage.success('更新成功')
  } catch (err) {
    ElMessage.error('更新失败')
    fetchConfigs()
  }
}

const deleteConfig = async (id: string) => {
  try {
    await axios.delete(`${API_URL}/api/qb/configs/${id}`)
    ElMessage.success('删除成功')
    fetchConfigs()
  } catch (err) {
    ElMessage.error('删除失败')
  }
}

onMounted(() => {
  fetchConfigs()
})
</script>

<style scoped lang="postcss">
/* ==================== 页面根容器 ==================== */
.qb-config {
  padding: 16px 24px 24px;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
  background: radial-gradient(ellipse 70% 45% at 90% -5%, rgba(59, 130, 246, 0.08), transparent 65%),
              radial-gradient(ellipse 55% 40% at 0% 100%, rgba(16, 185, 129, 0.06), transparent 60%),
              var(--bg-primary);
}

/* ==================== 实例配置页头 ==================== */
.configs-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}
.configs-title {
  font-size: 16px;
  font-weight: 700;
  color: #f1f5f9;
  margin: 0;
  letter-spacing: 0.3px;
}

/* ==================== 实例卡片列表 ==================== */
.config-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-y: auto;
  min-height: 0;
  padding: 4px 2px;
}

/* 实例卡片 — 横向毛玻璃卡片 */
.config-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 18px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 16px;
  backdrop-filter: blur(14px) saturate(140%);
  -webkit-backdrop-filter: blur(14px) saturate(140%);
  transition: border-color 0.22s ease, box-shadow 0.22s ease;
}
.config-card:hover {
  border-color: rgba(16, 185, 129, 0.3);
  box-shadow: 0 8px 26px -14px rgba(16, 185, 129, 0.25);
}

.cfg-left { flex-shrink: 0; }

/* 服务器图标 — 发光绿 */
.cfg-icon-circle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  border-radius: 14px;
  box-shadow: 0 0 14px rgba(52, 211, 153, 0.25), inset 0 0 8px rgba(52, 211, 153, 0.08);
}
.cfg-icon-circle.active {
  color: #34d399;
  box-shadow: 0 0 18px rgba(52, 211, 153, 0.5), inset 0 0 10px rgba(52, 211, 153, 0.12);
}

.cfg-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.cfg-name {
  font-size: 14.5px;
  font-weight: 700;
  color: #ffffff;
}
.cfg-url {
  font-size: 12.5px;
  color: #94a3b8;
  font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cfg-user {
  font-size: 12px;
  color: #64748b;
}

.cfg-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

/* Toggle 开关 — 激活霓虹绿光晕 */
.cfg-switch :deep(.el-switch__core) {
  width: 44px;
}
.cfg-switch :deep(.el-switch.is-checked .el-switch__core) {
  background: #10b981;
  box-shadow: 0 0 14px rgba(16, 185, 129, 0.6);
  border-color: transparent;
}

/* ==================== 按钮 ==================== */
.btn-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 15px;
  border-radius: 999px;
  border: 1px solid;
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
}
.btn-pill-primary {
  color: #fff;
  border: none;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  box-shadow: 0 4px 16px rgba(59, 130, 246, 0.35);
}
.btn-pill-primary:hover {
  box-shadow: 0 8px 24px rgba(99, 102, 241, 0.55);
}

/* 通用小图标按钮 */
.ac-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  border: 1px solid transparent;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  transition: all 0.2s;
}
.ac-btn:hover {
  color: #93c5fd;
  background: rgba(59, 130, 246, 0.12);
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.25);
}
.ac-btn-warn:hover {
  color: #f87171;
  background: rgba(239, 68, 68, 0.12);
  box-shadow: 0 0 10px rgba(239, 68, 68, 0.25);
}

/* ==================== 空状态 ==================== */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  gap: 8px;
  border: 1px dashed rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.02);
}
.empty-icon-circle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border-radius: 20px;
  margin-bottom: 6px;
  color: #60a5fa;
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid rgba(59, 130, 246, 0.2);
  box-shadow: 0 0 20px rgba(59, 130, 246, 0.15);
}
.empty-title { font-size: 15px; font-weight: 600; color: #cbd5e1; margin: 0; }
.empty-desc { font-size: 12.5px; color: #64748b; margin: 0; }

/* ==================== Element Plus overrides ==================== */
:deep(.el-switch) {
  --el-switch-on-color: #3b82f6;
  --el-switch-off-color: rgba(148, 163, 184, 0.25);
  --el-switch-border-color: transparent;
}
:deep(.el-switch.is-checked .el-switch__core) {
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.5);
}

/* 弹窗 — 全息毛玻璃（新增/编辑实例） */
:deep(.el-overlay) {
  @apply bg-black/60 backdrop-blur-sm;
}
:deep(.el-dialog) {
  --el-dialog-bg-color: transparent;
  @apply bg-[#0B1120]/80 backdrop-blur-2xl border border-white/10 rounded-2xl;
  box-shadow: 0 0 30px rgba(59, 130, 246, 0.15), inset 0 1px 0 rgba(255, 255, 255, 0.06);
}
:deep(.el-dialog__header) {
  background: transparent;
  border-bottom: none;
  padding-bottom: 14px;
}
:deep(.el-dialog__title) {
  color: #ffffff;
  font-weight: 700;
  letter-spacing: 0.3px;
}
:deep(.el-dialog__body) {
  color: #cbd5e1;
}
:deep(.el-dialog__footer) {
  background: transparent;
  border-top: none;
  padding-top: 8px;
}
:deep(.el-dialog__headerbtn) {
  color: #64748b;
  transition: color 0.25s ease, transform 0.25s ease, text-shadow 0.25s ease;
}
:deep(.el-dialog__headerbtn:hover) {
  color: #3b82f6;
  transform: rotate(90deg);
  text-shadow: 0 0 8px rgba(59, 130, 246, 0.6);
}
:deep(.el-form-item__label) { color: #94a3b8; }
:deep(.el-input__wrapper) {
  background: rgba(0, 0, 0, 0.25);
  border-radius: 10px;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.12) !important;
}
:deep(.el-input__wrapper.is-focus) {
  background: rgba(59, 130, 246, 0.06);
  box-shadow: 0 0 0 1px #3b82f6, 0 0 10px rgba(59, 130, 246, 0.4) !important;
}
:deep(.el-input__inner) { color: #f1f5f9; }
:deep(.el-input__inner::placeholder) { color: #475569; }

/* ==================== 移动端响应式 ==================== */
@media (max-width: 768px) {
  .qb-config {
    padding: 10px 12px 32px;
  }
  .config-card { flex-wrap: wrap; }
  .cfg-right { width: 100%; justify-content: flex-end; }
}
</style>
