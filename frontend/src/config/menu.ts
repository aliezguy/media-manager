/**
 * 全局菜单配置 — 单一数据源 (Single Source of Truth)
 *
 * 桌面侧边栏 (Sidebar.vue)、移动端底部 Tab 栏、页面组件切换 (App.vue)
 * 全部从这一份配置派生，禁止任何组件再写死菜单 label / icon / index。
 */
import type { Component } from 'vue'
import {
  LayoutDashboard, CalendarClock, Download, Brush, Tags, Users,
  Library, FolderTree, Server, Settings, History,
} from 'lucide-vue-next'

// 页面组件
import TagManager from '../components/TagManager.vue'
import EmbySettings from '../components/EmbySettings.vue'
import MpConfig from '../components/MpConfig.vue'
import WashHistory from '../components/WashHistory.vue'
import QbManager from '../components/QbManager.vue'
import FileEditor from '../components/FileEditor.vue'
import TorrentCleanup from '../components/TorrentCleanup.vue'
import TaskDashboard from '../components/TaskDashboard.vue'
import ScheduledTasks from '../components/ScheduledTasks.vue'
import ActorLocalizationStudio from '../components/ActorLocalizationStudio.vue'
import ActorLibrary from '../components/ActorLibrary.vue'

export interface MenuItem {
  /** 唯一索引，App.vue 组件切换 / 侧边栏选中态 / Tab 激活态共用 */
  index: string
  label: string
  /** 导航图标（统一使用 Lucide） */
  icon: Component
  /** 对应渲染的页面组件 */
  component: Component
}

export interface MenuGroup {
  title: string
  items: MenuItem[]
}

export const menuGroups: MenuGroup[] = [
  {
    title: '仪表盘',
    items: [
      { index: 'dashboard', label: '大盘', icon: LayoutDashboard, component: TaskDashboard },
    ],
  },
  {
    title: '任务工作流',
    items: [
      { index: 'scheduler', label: '定时扫描', icon: CalendarClock, component: ScheduledTasks },
      { index: 'qb', label: '下载管理', icon: Download, component: QbManager },
      { index: 'cleanup', label: '种子清理', icon: Brush, component: TorrentCleanup },
    ],
  },
  {
    title: '媒体与演员',
    items: [
      { index: 'manager', label: '标签管理', icon: Tags, component: TagManager },
      { index: 'actor', label: '演员中文化', icon: Users, component: ActorLocalizationStudio },
      { index: 'actorLib', label: '演员库', icon: Library, component: ActorLibrary },
      { index: 'editor', label: '分类', icon: FolderTree, component: FileEditor },
    ],
  },
  {
    title: '系统设置',
    items: [
      { index: 'emby', label: 'Emby', icon: Server, component: EmbySettings },
      { index: 'mp', label: '配置', icon: Settings, component: MpConfig },
      { index: 'history', label: '记录', icon: History, component: WashHistory },
    ],
  },
]
