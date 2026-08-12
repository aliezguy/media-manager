<script setup lang="ts">
import { ref, shallowRef } from 'vue'
import type { Component } from 'vue'
import {
  Server, Cloud, Settings, Download, FolderTree, CloudUpload, Database, Languages, MessageSquare
} from 'lucide-vue-next'

import EmbyConfig from './settings/EmbyConfig.vue'
import CD2Config from './settings/CD2Config.vue'
import MPConfig from './settings/MPConfig.vue'
import QbConfig from './settings/QbConfig.vue'
import CategoryConfig from './settings/CategoryConfig.vue'
import WebDAVConfig from './settings/WebDAVConfig.vue'
import DatabaseConfig from './settings/DatabaseConfig.vue'
import LocalizationConfig from './settings/LocalizationConfig.vue'
import DanmuConfig from './settings/DanmuConfig.vue'

// ==================== Tab 定义（水平页签，单一数据源） ====================
interface ConfigTab {
  key: string
  label: string
  icon: Component
  component: Component
}

const tabs: ConfigTab[] = [
  { key: 'emby', label: 'Emby & AI', icon: Server, component: EmbyConfig },
  { key: 'cd2', label: 'CD2 挂载', icon: Cloud, component: CD2Config },
  { key: 'mp', label: 'MoviePilot & 策略', icon: Settings, component: MPConfig },
  { key: 'qb', label: '下载器 qB', icon: Download, component: QbConfig },
  { key: 'category', label: '分类规则', icon: FolderTree, component: CategoryConfig },
  { key: 'webdav', label: 'WebDAV 图片缓存', icon: CloudUpload, component: WebDAVConfig },
  { key: 'database', label: '数据库', icon: Database, component: DatabaseConfig },
  { key: 'localization', label: 'AI·汉化', icon: Languages, component: LocalizationConfig },
  { key: 'danmu', label: '弹幕服务', icon: MessageSquare, component: DanmuConfig }
]

const activeKey = ref(tabs[0].key)
// shallowRef 避免对整个组件做深响应式代理（配合 KeepAlive 缓存各页签实例）
const activeComponent = shallowRef<Component>(tabs[0].component)

const selectTab = (key: string) => {
  activeKey.value = key
  const tab = tabs.find(t => t.key === key)
  if (tab) activeComponent.value = tab.component
}

// 键盘方向键切换页签（无障碍）
const onTabKeydown = (e: KeyboardEvent) => {
  const idx = tabs.findIndex(t => t.key === activeKey.value)
  if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
    e.preventDefault()
    const dir = e.key === 'ArrowRight' ? 1 : -1
    const next = tabs[(idx + dir + tabs.length) % tabs.length]
    selectTab(next.key)
    // 焦点跟随激活页签
    const btns = (e.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('[role="tab"]')
    btns[(idx + dir + tabs.length) % tabs.length]?.focus()
  }
}
</script>

<template>
  <div class="basic-config">
    <!-- ==================== 页头 + 水平 Tab 导航 ==================== -->
    <header class="config-header">
      <div class="header-left">
        <h1 class="config-title">基础配置</h1>
        <p class="config-subtitle">Emby · CD2 · MoviePilot · qB · 分类 · WebDAV · 数据库 · AI汉化</p>
      </div>

      <div
        class="tab-bar"
        role="tablist"
        aria-label="基础配置页签"
        @keydown="onTabKeydown"
      >
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          role="tab"
          :aria-selected="activeKey === tab.key"
          :class="['tab-btn', { active: activeKey === tab.key }]"
          @click="selectTab(tab.key)"
        >
          <component :is="tab.icon" :size="15" />
          <span>{{ tab.label }}</span>
        </button>
      </div>
    </header>

    <!-- ==================== 页签内容（KeepAlive 保活，切换不丢表单数据） ==================== -->
    <div class="config-content">
      <KeepAlive>
        <component :is="activeComponent" />
      </KeepAlive>
    </div>
  </div>
</template>

<style scoped lang="postcss">
.basic-config {
  @apply flex h-full w-full flex-col;
  background-color: var(--bg-primary, #0b1120);
}

/* ==================== 页头 ==================== */
.config-header {
  @apply sticky top-0 z-40 flex flex-wrap items-center justify-between gap-x-6 gap-y-3 px-6 py-4 border-b border-slate-800;
  background: rgba(11, 17, 32, 0.85);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
}
.header-left {
  @apply flex min-w-0 flex-col;
}
.config-title {
  @apply text-lg font-extrabold tracking-wide text-white;
  letter-spacing: 0.3px;
}
.config-subtitle {
  @apply mt-0.5 text-xs tracking-widest text-slate-500;
}

/* ==================== 水平 Tab 栏（暗色 slate 胶囊容器） ==================== */
.tab-bar {
  @apply inline-flex max-w-full items-center gap-1 rounded-xl border border-slate-800 bg-slate-900/70 p-1 shadow-[inset_0_2px_8px_rgba(0,0,0,0.35)];
  overflow-x: auto;
  scrollbar-width: none;
}
.tab-bar::-webkit-scrollbar { display: none; }

.tab-btn {
  @apply relative flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 py-2 text-[13px] font-semibold text-slate-400 transition-colors duration-200 cursor-pointer;
  border: none;
  background: transparent;
  font-family: inherit;
}
.tab-btn:hover {
  @apply text-slate-200 bg-slate-800/60;
}
.tab-btn:focus-visible {
  @apply outline-none ring-2 ring-emerald-400/70;
}
.tab-btn.active {
  @apply text-white bg-slate-700/70;
}
/* 激活态绿色下划线指示条（与全局 accent 一致） */
.tab-btn.active::after {
  content: '';
  @apply absolute left-3 right-3 -bottom-px h-0.5 rounded-full bg-emerald-400;
  box-shadow: 0 0 8px rgba(52, 211, 153, 0.7);
}
.tab-btn.active svg {
  @apply text-emerald-300;
}

/* ==================== 页签内容区 ==================== */
.config-content {
  @apply relative z-[1] min-h-0 flex-1 overflow-y-auto;
}

@media (max-width: 768px) {
  .config-header {
    @apply px-3 py-3;
  }
  .config-title { @apply text-base; }
  .tab-btn { @apply px-3 py-1.5 text-xs; }
}
</style>
