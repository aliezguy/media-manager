<script setup lang="ts">
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { Settings2, Save } from 'lucide-vue-next'

// 修正了之前的 # 注释错误
const API_URL = import.meta.env.VITE_API_URL || ''

// 默认选中 'category_yaml'
const activeTab = ref('category_yaml')
const fileContent = ref('')
const loading = ref(false)

const loadFile = async () => {
  loading.value = true
  try {
    const res = await axios.get(`${API_URL}/api/editor/${activeTab.value}`)
    fileContent.value = res.data.content
  } catch (e) {
    ElMessage.error('加载文件失败')
  } finally {
    loading.value = false
  }
}

const saveFile = async () => {
  try {
    await axios.post(`${API_URL}/api/editor/${activeTab.value}`, {
      content: fileContent.value
    })
    ElMessage.success('保存成功！配置已更新')
  } catch (e) {
    ElMessage.error('保存失败')
  }
}

onMounted(loadFile)
</script>

<template>
  <div class="console" v-loading="loading">
    <!-- ========== 科技感头部导航 ========== -->
    <div class="console-header">
      <div class="header-left">
        <span class="header-title">
          <Settings2 class="title-icon" />
          策略配置编辑器
        </span>
        <span class="header-file">category.yaml</span>
      </div>

      <button class="save-btn" :disabled="loading" @click="saveFile">
        <Save class="save-icon" />
        保存配置
      </button>
    </div>

    <!-- ========== 骇客终端编辑器 ========== -->
    <div class="editor-area">
      <el-input
        v-model="fileContent"
        type="textarea"
        :rows="25"
        placeholder="// 策略配置 · 加载中..."
        class="editor-input"
      />
    </div>

    <!-- ========== 底部状态栏 ========== -->
    <div class="status-bar">
      <div class="status-labels">
        <span>YAML</span>
        <span>UTF-8</span>
        <span>System Ready</span>
      </div>
    </div>
  </div>
</template>

<style scoped lang="postcss">
/* ==================== 全息策略控制台容器 ==================== */
.console {
  @apply flex flex-col w-full h-full bg-[#0B1120]/80 backdrop-blur-2xl
    border border-white/10 rounded-2xl shadow-2xl overflow-hidden;
}

/* ==================== 科技感头部导航 ==================== */
.console-header {
  @apply flex items-center justify-between px-6 py-4 bg-white/[0.02] border-b border-white/10;
}

.header-left {
  @apply flex items-center gap-2;
}

.header-title {
  @apply text-white text-base font-bold tracking-wide flex items-center gap-2;
}

.title-icon {
  @apply w-5 h-5 text-blue-400;
}

/* 文件路径标签 */
.header-file {
  @apply text-slate-500 text-xs font-mono ml-2 px-2 py-0.5 rounded bg-black/20 border border-white/5;
}

/* 高科技发光幽灵保存按钮 */
.save-btn {
  @apply flex items-center gap-2 px-5 py-2 rounded-lg bg-blue-600/20 text-blue-400
    border border-blue-500/30 hover:bg-blue-600/40 hover:text-white hover:border-blue-400
    hover:shadow-[0_0_15px_rgba(59,130,246,0.3)] transition-all duration-300
    font-medium text-sm cursor-pointer;
}
.save-btn:disabled {
  @apply opacity-60 cursor-not-allowed;
}
.save-icon {
  @apply w-4 h-4;
}

/* ==================== 骇客终端编辑器 ==================== */
.editor-area {
  @apply flex-1 w-full bg-[#050B14] p-4 overflow-hidden;
  transition: box-shadow 0.3s ease;
}
/* Focus 时外层容器整体微微发光（替代原生蓝框） */
.editor-area:focus-within {
  box-shadow: inset 0 0 40px rgba(59, 130, 246, 0.06);
}

/* el-textarea 深度穿透：占满区域 + 等宽荧光字体 + 移除原生边框 */
.editor-area :deep(.el-textarea) {
  @apply block w-full h-full;
}
.editor-area :deep(.el-textarea__inner) {
  @apply w-full h-full font-mono text-[13px] leading-relaxed text-emerald-400 bg-transparent;
  border: none !important;
  box-shadow: none !important;
  outline: none !important;
  padding: 0;
  resize: none;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.15) transparent;
}
.editor-area :deep(.el-textarea__inner:focus),
.editor-area :deep(.el-textarea__inner:focus-visible) {
  border: none !important;
  box-shadow: none !important;
  outline: none !important;
}
.editor-area :deep(.el-textarea__inner::placeholder) {
  @apply text-slate-600;
}

/* 纤细半透明自定义滚动条 */
.editor-area :deep(.el-textarea__inner::-webkit-scrollbar) {
  width: 4px;
  height: 4px;
}
.editor-area :deep(.el-textarea__inner::-webkit-scrollbar-track) {
  background: transparent;
}
.editor-area :deep(.el-textarea__inner::-webkit-scrollbar-thumb) {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 2px;
}
.editor-area :deep(.el-textarea__inner::-webkit-scrollbar-thumb:hover) {
  background: rgba(255, 255, 255, 0.3);
}

/* loading 遮罩 — 暗黑化，融入控制台 */
.console :deep(.el-loading-mask) {
  background-color: rgba(5, 11, 20, 0.6);
  backdrop-filter: blur(2px);
}

/* ==================== 底部状态栏 ==================== */
.status-bar {
  @apply flex items-center justify-end px-4 py-1.5 bg-[#0F172A] border-t border-white/5;
}
.status-labels {
  @apply flex items-center gap-4 text-slate-500 text-[10px] font-mono uppercase tracking-widest;
}
</style>
