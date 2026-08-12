<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { Save, MessageSquare, Link2, KeyRound, CircleCheck, CircleAlert } from 'lucide-vue-next'

const API_URL = ''

// 弹幕服务配置（与后端 config.yaml 键一致；保存只提交本 Tab 的键，后端浅合并）
const config = reactive({
  danmu_base_url: '',
  danmu_api_key: ''
})

// 连接状态：idle=未测 / ok=通 / fail=不通
const connState = ref<'idle' | 'ok' | 'fail'>('idle')
const checking = ref(false)

onMounted(async () => {
  try {
    const res = await axios.get(`${API_URL}/api/config`)
    const d = res.data || {}
    config.danmu_base_url = d.danmu_base_url || ''
    config.danmu_api_key = d.danmu_api_key || ''
  } catch (e) {}
})

const saveConfig = async () => {
  try {
    await axios.post(`${API_URL}/api/config`, { ...config })
    ElMessage.success('配置已保存')
  } catch (e) { ElMessage.error('保存失败') }
}

const testConnection = async () => {
  checking.value = true
  try {
    const res = await axios.get(`${API_URL}/api/danmu/status`)
    if (res.data?.configured) {
      connState.value = 'ok'
      ElMessage.success('连接成功')
    } else {
      connState.value = 'fail'
      ElMessage.warning('尚未配置完整（需同时填地址与密钥）')
    }
  } catch (e) {
    connState.value = 'fail'
    ElMessage.error('连接失败，请检查地址与网络')
  } finally {
    checking.value = false
  }
}
</script>

<template>
  <div class="danmu-root">
    <!-- ==================== 沉浸感背景光效 ==================== -->
    <div class="ambient ambient-a"></div>
    <div class="ambient ambient-b"></div>
    <div class="grid-overlay"></div>

    <!-- ==================== 吸顶操作栏 ==================== -->
    <div class="sticky-bar">
      <div class="min-w-0">
        <h1 class="page-title">弹幕服务</h1>
        <p class="mt-0.5 text-xs tracking-widest text-slate-500">MisakaDanmaku 外部控制 API · 媒体弹幕管理页面数据源</p>
      </div>
      <div class="flex gap-3">
        <button type="button" class="btn btn-ghost" :disabled="checking" @click="testConnection">
          <CircleCheck v-if="connState === 'ok'" :size="16" />
          <CircleAlert v-else-if="connState === 'fail'" :size="16" />
          <MessageSquare v-else :size="16" />
          <span>{{ checking ? '测试中…' : connState === 'ok' ? '连接正常' : connState === 'fail' ? '连接失败' : '测试连接' }}</span>
        </button>
        <button type="button" class="btn btn-primary" @click="saveConfig">
          <Save :size="16" />
          <span>保存配置</span>
        </button>
      </div>
    </div>

    <!-- ==================== 弹幕服务连接卡片 ==================== -->
    <div class="content">
      <section class="glass-card fade-up" style="--d: 0ms">
        <header class="card-head">
          <div class="icon-badge cyan">
            <MessageSquare :size="20" />
          </div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[15px] font-bold tracking-wide text-white">弹幕服务连接</h2>
            <p class="mt-1 text-xs leading-relaxed text-slate-500">「媒体弹幕管理」页面代理的外部弹幕服务，完整 API 文档见项目 docs/danmu-api.md</p>
          </div>
        </header>

        <div class="card-body">
          <div class="grid grid-cols-1 gap-x-7 gap-y-5 md:grid-cols-2">
            <div class="md:col-span-2">
              <label class="field-label"><Link2 :size="13" />服务地址 (Base URL)</label>
              <el-input v-model="config.danmu_base_url" placeholder="https://danmu.2503.seeyo.top:13360" />
              <p class="field-tip">弹幕服务外部控制 API 根地址（含协议与端口，末尾不带 /）</p>
            </div>
            <div class="md:col-span-2">
              <label class="field-label"><KeyRound :size="13" />API 密钥 (X-API-KEY)</label>
              <el-input v-model="config.danmu_api_key" type="password" show-password placeholder="控制台生成的 API Key" />
              <p class="field-tip">调用弹幕服务全部接口所需密钥；仅存于 config.yaml（已 gitignore）</p>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped lang="postcss">
/* ==================== 根容器 ==================== */
.danmu-root {
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
  background: radial-gradient(circle, rgba(34, 211, 238, 0.12), transparent 70%);
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
.glass-card::before {
  content: '';
  position: absolute;
  top: 0; left: 12%; right: 12%;
  height: 1px;
  z-index: 0;
  background: linear-gradient(90deg, transparent, rgba(6, 182, 212, 0.55), rgba(59, 130, 246, 0.55), transparent);
}
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

.btn-ghost {
  border-color: rgba(148, 163, 184, 0.25);
  background: rgba(255, 255, 255, 0.04);
  color: #cbd5e1;
}
.btn-ghost:hover:not(:disabled) {
  border-color: rgba(34, 211, 238, 0.5);
  color: #e2e8f0;
  transform: translateY(-1px);
}

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

@media (max-width: 768px) {
  .danmu-root { padding: 10px 12px 32px; }
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
