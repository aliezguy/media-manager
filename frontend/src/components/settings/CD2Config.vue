<script setup lang="ts">
import { reactive, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import {
  Cloud, Save, FolderTree, Folder, HardDrive, Link2,
  Server, KeyRound, UserRound
} from 'lucide-vue-next'

const API_URL = ''

// CD2 挂载相关配置（与后端 config.yaml 对应键一致；保存只提交本 Tab 的键，后端浅合并）
const config = reactive({
  cd2_host: '',
  cd2_port: '',
  cd2_username: '',
  cd2_password: '',
  cd2_media_dir: '',
  cd2_organized_dir: '',
  emby_prefix: '',
  cd2_media_prefix: ''
})

onMounted(async () => {
  try {
    const res = await axios.get(`${API_URL}/api/config`)
    const d = res.data || {}
    config.cd2_host = d.cd2_host || ''
    config.cd2_port = d.cd2_port || ''
    config.cd2_username = d.cd2_username || ''
    config.cd2_password = d.cd2_password || ''
    config.cd2_media_dir = d.cd2_media_dir || ''
    config.cd2_organized_dir = d.cd2_organized_dir || ''
    config.emby_prefix = d.emby_prefix || ''
    config.cd2_media_prefix = d.cd2_media_prefix || ''
  } catch (e) {}
})

const saveConfig = async () => {
  try {
    await axios.post(`${API_URL}/api/config`, { ...config })
    ElMessage.success('配置已保存')
  } catch (e) { ElMessage.error('保存失败') }
}
</script>

<template>
  <div class="cd2-root">
    <!-- ==================== 沉浸感背景光效 ==================== -->
    <div class="ambient ambient-a"></div>
    <div class="ambient ambient-b"></div>
    <div class="grid-overlay"></div>

    <!-- ==================== 吸顶操作栏 ==================== -->
    <div class="sticky-bar">
      <div class="min-w-0">
        <h1 class="page-title">CD2 挂载</h1>
        <p class="mt-0.5 text-xs tracking-widest text-slate-500">云盘目录映射 · 路径前缀用于 CD2 ↔ Emby 双向转换</p>
      </div>
      <div class="flex gap-3">
        <button
          type="button"
          class="btn btn-primary"
          @click="saveConfig"
        >
          <Save :size="16" />
          <span>保存配置</span>
        </button>
      </div>
    </div>

    <!-- ==================== CD2 连接卡片 ==================== -->
    <div class="content">
      <section class="glass-card fade-up" style="--d: 0ms">
        <header class="card-head">
          <div class="icon-badge cyan">
            <Server :size="20" />
          </div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[15px] font-bold tracking-wide text-white">CD2 连接</h2>
            <p class="mt-1 text-xs leading-relaxed text-slate-500">CloudDrive2 gRPC 服务地址与鉴权</p>
          </div>
        </header>

        <div class="card-body">
          <div class="grid grid-cols-1 gap-x-7 gap-y-5 md:grid-cols-2">
            <div>
              <label class="field-label"><Server :size="13" />CD2 地址 (Host)</label>
              <el-input v-model="config.cd2_host" placeholder="192.168.31.173" />
              <p class="field-tip">CloudDrive2 gRPC 服务地址</p>
            </div>
            <div>
              <label class="field-label"><Server :size="13" />端口 (Port)</label>
              <el-input v-model="config.cd2_port" placeholder="19797" />
              <p class="field-tip">gRPC 端口</p>
            </div>
            <div>
              <label class="field-label"><UserRound :size="13" />账号</label>
              <el-input v-model="config.cd2_username" placeholder="CD2 登录账号" />
              <p class="field-tip">CD2 登录账号（用于 GetToken 鉴权）</p>
            </div>
            <div>
              <label class="field-label"><KeyRound :size="13" />密码</label>
              <el-input v-model="config.cd2_password" type="password" show-password placeholder="CD2 密码" />
              <p class="field-tip">CD2 登录密码</p>
            </div>
          </div>
        </div>
      </section>

      <!-- ==================== CD2 网盘设置卡片 ==================== -->
      <section class="glass-card fade-up" style="--d: 80ms">
        <header class="card-head">
          <div class="icon-badge cyan">
            <Cloud :size="20" />
          </div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[15px] font-bold tracking-wide text-white">CD2 网盘设置</h2>
            <p class="mt-1 text-xs leading-relaxed text-slate-500">云盘目录映射 · 路径前缀用于 CD2 ↔ Emby 双向转换</p>
          </div>
        </header>

        <div class="card-body">
          <div class="grid grid-cols-1 gap-x-7 gap-y-5 md:grid-cols-2">
            <div>
              <label class="field-label"><Folder :size="13" />媒体库根路径（左侧）</label>
              <el-input v-model="config.cd2_media_dir" placeholder="/80003588/emby库/电视剧/" />
              <p class="field-tip">CD2 文件概览左侧「媒体库（待整理）」的根路径</p>
            </div>
            <div>
              <label class="field-label"><FolderTree :size="13" />已完结根路径（右侧）</label>
              <el-input v-model="config.cd2_organized_dir" placeholder="/80003588/网盘整理/完结整理/电视剧/" />
              <p class="field-tip">CD2 文件概览右侧「已完结（已整理）」的根路径</p>
            </div>
            <div>
              <label class="field-label"><HardDrive :size="13" />Emby 路径前缀</label>
              <el-input v-model="config.emby_prefix" placeholder="/volume3/emby影院/115网盘_3588/" />
              <p class="field-tip">Emby 服务器上挂载的云端存储根路径（用于 CD2 ↔ Emby 路径互转）</p>
            </div>
            <div>
              <label class="field-label"><Link2 :size="13" />CD2 路径前缀</label>
              <el-input v-model="config.cd2_media_prefix" placeholder="/80003588/emby库/" />
              <p class="field-tip">CD2 中挂载的媒体库根路径（与 Emby 前缀对应）</p>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped lang="postcss">
/* ==================== 根容器 ==================== */
.cd2-root {
  position: relative;
  z-index: 1;
  max-width: 1080px;
  margin: 0 auto;
  padding: 16px 24px 40px;
}

/* ==================== 沉浸感背景光效 ==================== */
.ambient {
  position: fixed;
  border-radius: 50%;
  pointer-events: none;
  z-index: 0;
}
.ambient-a {
  width: 560px; height: 560px;
  top: -180px; right: -140px;
  background: radial-gradient(circle, rgba(6, 182, 212, 0.12), transparent 70%);
}
.ambient-b {
  width: 480px; height: 480px;
  bottom: -160px; left: -120px;
  background: radial-gradient(circle, rgba(59, 130, 246, 0.1), transparent 70%);
}
.grid-overlay {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background-image:
    linear-gradient(rgba(148, 163, 184, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148, 163, 184, 0.045) 1px, transparent 1px);
  background-size: 46px 46px;
  -webkit-mask-image: radial-gradient(ellipse 85% 65% at 50% 0%, #000 30%, transparent 78%);
          mask-image: radial-gradient(ellipse 85% 65% at 50% 0%, #000 30%, transparent 78%);
}

/* ==================== 吸顶操作栏 ==================== */
.sticky-bar {
  position: sticky;
  top: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 16px 4px;
  margin-bottom: 10px;
  background: rgba(11, 17, 32, 0.62);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}
.page-title {
  font-size: 20px;
  font-weight: 800;
  letter-spacing: 0.3px;
  color: #fff;
  background: linear-gradient(90deg, #fff 20%, #67e8f9 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

/* ==================== 内容区 ==================== */
.content {
  @apply relative z-[1] grid grid-cols-1 gap-6;
}

/* ==================== 玻璃卡片 ==================== */
.glass-card {
  @apply relative z-[1] flex h-full flex-col overflow-hidden rounded-[22px] border border-white/10;
  background: linear-gradient(160deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.02));
  backdrop-filter: blur(18px) saturate(140%);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  box-shadow: 0 24px 60px -24px rgba(0, 0, 0, 0.65), inset 0 1px 0 rgba(255, 255, 255, 0.06);
}
/* 顶部渐变发光线 */
.glass-card::before {
  content: '';
  position: absolute;
  top: 0; left: 12%; right: 12%;
  height: 1px;
  z-index: 0;
  background: linear-gradient(90deg, transparent, rgba(6, 182, 212, 0.55), rgba(59, 130, 246, 0.55), transparent);
}
/* 右上角氛围光斑 */
.glass-card::after {
  content: '';
  position: absolute;
  top: -90px; right: -70px;
  width: 260px; height: 260px;
  z-index: 0;
  pointer-events: none;
  background: radial-gradient(circle, rgba(6, 182, 212, 0.08), transparent 70%);
}

.card-head {
  @apply relative z-[1] flex flex-wrap items-center gap-3.5 border-b border-white/5 px-6 py-[18px];
}
.card-body {
  @apply relative z-[1] p-6;
}

/* 图标徽章 */
.icon-badge {
  width: 40px; height: 40px;
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  border-radius: 12px;
  color: #22d3ee;
  background: linear-gradient(135deg, rgba(6, 182, 212, 0.18), rgba(6, 182, 212, 0.05));
  border: 1px solid rgba(6, 182, 212, 0.32);
  box-shadow: 0 0 18px rgba(6, 182, 212, 0.22), inset 0 0 10px rgba(6, 182, 212, 0.08);
}

/* ==================== 字段 ==================== */
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

/* ==================== Element Plus Input 深度定制 ==================== */
:deep(.el-input__wrapper) {
  @apply rounded-xl bg-black/20 transition-shadow duration-200;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.10);
}
:deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.28);
}
:deep(.el-input__wrapper.is-focus) {
  background: rgba(6, 182, 212, 0.06);
  border-color: #22d3ee;
  box-shadow: 0 0 0 1px #22d3ee, 0 0 8px rgba(6, 182, 212, 0.5);
}
:deep(.el-input__inner) { color: #f1f5f9; }
:deep(.el-input__inner::placeholder) { color: #475569; }

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
.btn:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-primary {
  background: linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%);
  box-shadow: 0 4px 20px rgba(6, 182, 212, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.2);
}
.btn-primary:hover:not(:disabled) {
  transform: translateY(-1px) scale(1.03);
  box-shadow: 0 8px 30px rgba(6, 182, 212, 0.45), 0 0 26px rgba(59, 130, 246, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.2);
}
.btn-primary:active:not(:disabled) { transform: scale(0.98); }

/* ==================== 动画 ==================== */
.fade-up {
  opacity: 0;
  animation: fadeUp 0.6s cubic-bezier(0.22, 0.61, 0.36, 1) forwards;
  animation-delay: var(--d, 0ms);
}
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ==================== 响应式 & 无障碍 ==================== */
@media (max-width: 768px) {
  .cd2-root { padding: 10px 12px 32px; }
  .sticky-bar { padding: 12px 2px; }
  .page-title { font-size: 17px; }
  .btn { flex: 1; }
  .card-head { @apply px-4 py-3.5; }
  .card-body { @apply p-4; }
}

@media (prefers-reduced-motion: reduce) {
  .fade-up, .btn {
    animation: none !important;
    transition: none !important;
  }
  .fade-up { opacity: 1; }
}
</style>
