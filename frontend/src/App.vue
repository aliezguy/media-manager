<script setup lang="ts">
import { ref, shallowRef, onMounted, onUnmounted, computed } from 'vue'
import { Files, Connection, VideoCameraFilled, Timer, Box, EditPen, Brush, DataAnalysis, Calendar, User } from '@element-plus/icons-vue'

import Sidebar from './components/Sidebar.vue'
import TagManager from './components/TagManager.vue'
import EmbySettings from './components/EmbySettings.vue'
import MpConfig from './components/MpConfig.vue'
import WashHistory from './components/WashHistory.vue'
import QbManager from './components/QbManager.vue'
import FileEditor from './components/FileEditor.vue'
import TorrentCleanup from './components/TorrentCleanup.vue'
import TaskDashboard from './components/TaskDashboard.vue'
import ScheduledTasks from './components/ScheduledTasks.vue'
import ActorLocalizationStudio from './components/ActorLocalizationStudio.vue'
import ActorLibrary from './components/ActorLibrary.vue'

const isCollapse = ref(false)
const activeMenu = ref('emby')
const windowWidth = ref(window.innerWidth)

const isMobile = computed(() => windowWidth.value < 768)

const currentComponent = shallowRef(EmbySettings)

// 用于组件解析 + 移动端底部 Tab 栏（index 必须与 Sidebar 中的菜单项保持一致）
const menuItems = [
  { index: 'manager', label: '标签管理', icon: Files, component: TagManager },
  { index: 'qb', label: '下载管理', icon: Box, component: QbManager },
  { index: 'cleanup', label: '种子清理', icon: Brush, component: TorrentCleanup },
  { index: 'dashboard', label: '大盘', icon: DataAnalysis, component: TaskDashboard },
  { index: 'actor', label: '演员中文化', icon: User, component: ActorLocalizationStudio },
  { index: 'actorLib', label: '演员库', icon: User, component: ActorLibrary },
  { index: 'scheduler', label: '定时扫描', icon: Calendar, component: ScheduledTasks },
  { index: 'emby', label: 'Emby', icon: Connection, component: EmbySettings },
  { index: 'mp', label: '配置', icon: VideoCameraFilled, component: MpConfig },
  { index: 'history', label: '记录', icon: Timer, component: WashHistory },
  { index: 'editor', label: '分类', icon: EditPen, component: FileEditor }
]

const handleSelect = (index: string) => {
  activeMenu.value = index
  const item = menuItems.find(i => i.index === index)
  if (item) currentComponent.value = item.component
}

const handleResize = () => {
  windowWidth.value = window.innerWidth
  if (window.innerWidth < 768) {
    isCollapse.value = true
  }
}

onMounted(() => {
  handleResize()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <div class="app-root">
    <!-- ==================== 桌面端布局 ==================== -->
    <div class="app-layout" v-if="!isMobile">
      <Sidebar
        :collapsed="isCollapse"
        :active-menu="activeMenu"
        @select="handleSelect"
        @toggle-collapse="isCollapse = !isCollapse"
      />

      <el-main class="app-main">
        <component :is="currentComponent" :key="activeMenu" />
      </el-main>
    </div>

    <!-- ==================== 移动端布局 ==================== -->
    <div class="mobile-layout" v-else>
      <!-- 顶部标题栏 -->
      <div class="mobile-header">
        <span class="mobile-title">{{ menuItems.find(i => i.index === activeMenu)?.label }}</span>
      </div>

      <!-- 主内容区 -->
      <div class="mobile-content">
        <component :is="currentComponent" :key="activeMenu" />
      </div>

      <!-- 底部标签栏 -->
      <div class="mobile-tabbar safe-bottom">
        <div
          v-for="item in menuItems"
          :key="item.index"
          class="tabbar-item"
          :class="{ active: activeMenu === item.index }"
          @click="handleSelect(item.index)"
        >
          <el-icon :size="20"><component :is="item.icon" /></el-icon>
          <span class="tabbar-label">{{ item.label }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ==================== 桌面端样式 ==================== */
.app-root { width: 100%; height: 100vh; overflow: hidden; }
.app-layout { display: flex; height: 100%; background-color: var(--bg-primary); }

.app-main {
  flex: 1;
  min-width: 0;
  padding: 0;
  overflow-y: auto;
  overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
  background-color: var(--bg-primary);
}

/* ==================== 移动端样式 ==================== */
.mobile-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  background-color: var(--bg-primary);
}

.mobile-header {
  height: 44px;
  background: var(--bg-overlay);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
  flex-shrink: 0;
  border-bottom: 1px solid var(--border-color);
}
.mobile-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.5px;
}

.mobile-content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
  padding: 8px;
  background-color: var(--bg-primary);
}

/* Bottom Tab Bar — frosted glass */
.mobile-tabbar {
  display: flex;
  justify-content: space-around;
  align-items: center;
  background: rgba(15, 23, 42, 0.82);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-top: 1px solid rgba(51, 65, 85, 0.5);
  height: 54px;
  padding-bottom: calc(env(safe-area-inset-bottom, 0px));
  flex-shrink: 0;
}

.tabbar-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  color: var(--text-tertiary);
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  padding: 4px 10px;
  border-radius: var(--radius-md);
  transition: all 0.2s ease;
  position: relative;
}
.tabbar-item:active {
  transform: scale(0.92);
}
.tabbar-item.active {
  color: var(--accent-blue);
  /* Glow effect on active icon */
  filter: drop-shadow(0 0 8px rgba(59, 130, 246, 0.6));
}
.tabbar-item.active::before {
  content: '';
  position: absolute;
  top: -1px;
  left: 50%;
  transform: translateX(-50%);
  width: 20px;
  height: 2px;
  background: var(--accent-blue);
  border-radius: 0 0 2px 2px;
  box-shadow: 0 0 6px rgba(59, 130, 246, 0.6);
}
.tabbar-label {
  font-size: 10px;
  line-height: 1;
  font-weight: 500;
}
.tabbar-item.active .tabbar-label {
  font-weight: 600;
}
</style>
