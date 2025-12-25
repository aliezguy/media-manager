<script setup>
import { ref, shallowRef, onMounted, onUnmounted } from 'vue'
import { Files, Connection, VideoCameraFilled, Expand, Fold, Timer, Box } from '@element-plus/icons-vue'

// 引入我们的三个子组件 (保持你原有的引用不变)
import TagManager from './components/TagManager.vue'
import EmbySettings from './components/EmbySettings.vue'
import MpConfig from './components/MpConfig.vue'
import WashHistory from './components/WashHistory.vue'
import QbManager from './components/QbManager.vue'

// 菜单配置
const isCollapse = ref(false)
const activeMenu = ref('manager')
const windowWidth = ref(window.innerWidth) // 🔥 新增：追踪屏幕宽度

// 动态组件映射
const currentComponent = shallowRef(TagManager)

const menuItems = [
  { index: 'manager', label: '标签管理', icon: Files, component: TagManager },
  { index: 'qb', label: '下载管理', icon: Box, component: QbManager },
  { index: 'emby', label: 'Emby 设置', icon: Connection, component: EmbySettings },
  { index: 'mp', label: '订阅配置', icon: VideoCameraFilled, component: MpConfig },
  { index: 'history', label: '订阅记录', icon: Timer, component: WashHistory }
]

const handleSelect = (index) => {
  activeMenu.value = index
  const item = menuItems.find(i => i.index === index)
  if (item) currentComponent.value = item.component
}

// 🔥 新增：处理窗口大小变化
const handleResize = () => {
  windowWidth.value = window.innerWidth
  // 如果屏幕小于 768px (手机/iPad竖屏)，自动折叠
  if (window.innerWidth < 768) {
    isCollapse.value = true
  } else {
    isCollapse.value = false
  }
}

// 🔥 新增：生命周期钩子
onMounted(() => {
  handleResize() // 初始化检查
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <el-container class="app-layout">
    <el-aside :width="isCollapse ? '64px' : '240px'" class="app-sidebar">
      <div class="logo-area">
        <img src="/vite.svg" alt="logo" class="logo" />
        <span v-if="!isCollapse && windowWidth > 768" class="title">Media Manager</span>
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
      <div class="main-header">
        <h2>{{ menuItems.find(i => i.index === activeMenu)?.label }}</h2>
      </div>

      <div class="content-wrapper">
        <transition name="fade-slide" mode="out-in">
          <component :is="currentComponent" :key="activeMenu" />
        </transition>
      </div>
    </el-main>
  </el-container>
</template>

<style scoped>
/* 全局布局 */
.app-layout { height: 100vh; background-color: #f5f7fa; }

/* 侧边栏样式 */
.app-sidebar {
  background-color: #fff;
  border-right: 1px solid #e6e6e6;
  display: flex;
  flex-direction: column;
  transition: width 0.3s;
  overflow: hidden;
  z-index: 100; /* 保证层级 */
}

.logo-area {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid #f0f0f0;
}
.logo { height: 32px; width: 32px; }
.title { margin-left: 10px; font-weight: bold; font-size: 18px; color: #303133; white-space: nowrap; }

.sidebar-menu { border-right: none; margin-top: 10px; flex: 1; }
.sidebar-menu :deep(.el-menu-item) {
  height: 50px;
  margin: 4px 10px;
  border-radius: 8px;
  color: #606266;
}
.sidebar-menu :deep(.el-menu-item.is-active) {
  background-color: #ecf5ff;
  color: #409EFF;
  font-weight: 600;
}
.sidebar-menu :deep(.el-menu-item:hover) { background-color: #f5f7fa; }

.collapse-btn {
  height: 50px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #909399;
  border-top: 1px solid #f0f0f0;
}
.collapse-btn:hover { color: #409EFF; background: #f9f9f9; }

/* 主内容区 */
.app-main { padding: 0; display: flex; flex-direction: column; overflow: hidden; }
.main-header {
  height: 60px;
  background: #fff;
  padding: 0 20px;
  display: flex;
  align-items: center;
  box-shadow: 0 1px 4px rgba(0,21,41,0.08);
  z-index: 10;
}
.main-header h2 { margin: 0; font-size: 18px; color: #303133; font-weight: 600; }

.content-wrapper {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
  overflow-x: hidden;
  -webkit-overflow-scrolling: touch; /* 优化 iOS 滚动体验 */
}

/* 🌟 动画效果 CSS 🌟 */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.3s ease;
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateX(20px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateX(-20px);
}

/* 🔥 新增：手机端适配样式 🔥 */
@media screen and (max-width: 768px) {
  .content-wrapper {
    padding: 10px; /* 减小手机端内边距 */
  }
  .main-header {
    padding: 0 15px; /* 减小头部边距 */
  }
  .app-sidebar {
    position: relative; /* 保持流式布局，不遮挡内容，直接挤压 */
  }
}
</style>
