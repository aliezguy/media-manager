<script setup>
import { ref, shallowRef, onMounted, onUnmounted, computed } from 'vue'
import { Files, Connection, VideoCameraFilled, Expand, Fold, Timer, Box, EditPen, Menu } from '@element-plus/icons-vue'

import TagManager from './components/TagManager.vue'
import EmbySettings from './components/EmbySettings.vue'
import MpConfig from './components/MpConfig.vue'
import WashHistory from './components/WashHistory.vue'
import QbManager from './components/QbManager.vue'
import FileEditor from './components/FileEditor.vue'

const isCollapse = ref(false)
const activeMenu = ref('manager')
const windowWidth = ref(window.innerWidth)
const showMobileDrawer = ref(false)

const isMobile = computed(() => windowWidth.value < 768)

const currentComponent = shallowRef(TagManager)

const menuItems = [
  { index: 'manager', label: '标签管理', icon: Files, component: TagManager },
  { index: 'qb', label: '下载管理', icon: Box, component: QbManager },
  { index: 'emby', label: 'Emby', icon: Connection, component: EmbySettings },
  { index: 'mp', label: '配置', icon: VideoCameraFilled, component: MpConfig },
  { index: 'history', label: '记录', icon: Timer, component: WashHistory },
  { index: 'editor', label: '分类', icon: EditPen, component: FileEditor }
]

const handleSelect = (index) => {
  activeMenu.value = index
  const item = menuItems.find(i => i.index === index)
  if (item) currentComponent.value = item.component
  showMobileDrawer.value = false
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
    <!-- ==================== 桌面端侧边栏 ==================== -->
    <el-container class="app-layout" v-if="!isMobile">
      <el-aside :width="isCollapse ? '64px' : '200px'" class="app-sidebar">
        <div class="logo-area">
          <span v-if="!isCollapse" class="title">Media Manager</span>
          <el-icon v-else :size="20"><Menu /></el-icon>
        </div>

        <el-menu
          :default-active="activeMenu"
          class="sidebar-menu"
          :collapse="isCollapse"
          @select="handleSelect"
        >
          <el-menu-item v-for="item in menuItems" :key="item.index" :index="item.index">
            <el-icon><component :is="item.icon" /></el-icon>
            <template #title>{{ item.label }}</template>
          </el-menu-item>
        </el-menu>

        <div class="collapse-btn" @click="isCollapse = !isCollapse">
          <el-icon><Expand v-if="isCollapse" /><Fold v-else /></el-icon>
        </div>
      </el-aside>

      <el-main class="app-main">
        <component :is="currentComponent" :key="activeMenu" />
      </el-main>
    </el-container>

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
.app-layout { height: 100%; background-color: #f5f7fa; }

.app-sidebar {
  background-color: #fff;
  border-right: 1px solid #e6e6e6;
  display: flex;
  flex-direction: column;
  transition: width 0.3s;
  overflow: hidden;
}

.logo-area {
  height: 50px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid #f0f0f0;
}
.title {
  font-weight: bold; font-size: 16px; color: #303133; white-space: nowrap;
}

.sidebar-menu {
  border-right: none; margin-top: 5px; flex: 1;
}
.sidebar-menu :deep(.el-menu-item) {
  height: 44px; margin: 3px 8px; border-radius: 8px; color: #606266;
}
.sidebar-menu :deep(.el-menu-item.is-active) {
  background-color: #ecf5ff; color: #409EFF; font-weight: 600;
}

.collapse-btn {
  height: 44px; display: flex; align-items: center; justify-content: center;
  cursor: pointer; color: #909399; border-top: 1px solid #f0f0f0;
}

.app-main {
  padding: 0; overflow-y: auto; overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
}

/* ==================== 移动端样式 ==================== */
.mobile-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  background-color: #f5f7fa;
}

.mobile-header {
  height: 44px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  z-index: 10;
  flex-shrink: 0;
}
.mobile-title {
  font-size: 16px; font-weight: 600; color: #303133;
}

.mobile-content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
  padding: 8px;
}

.mobile-tabbar {
  display: flex;
  justify-content: space-around;
  align-items: center;
  background: #fff;
  border-top: 1px solid #e6e6e6;
  height: 50px;
  padding-bottom: calc(env(safe-area-inset-bottom, 0px));
  flex-shrink: 0;
}

.tabbar-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  color: #909399;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  padding: 4px 8px;
  border-radius: 8px;
  transition: color 0.2s;
}
.tabbar-item.active {
  color: #409EFF;
}
.tabbar-label {
  font-size: 10px; line-height: 1;
}
</style>
