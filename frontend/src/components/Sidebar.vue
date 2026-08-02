<script setup>
import {
  LayoutDashboard, CalendarClock, Download, Brush, Tags, Users,
  Library, FolderTree, Server, Settings, History,
  PanelLeftClose, PanelLeftOpen, Boxes
} from 'lucide-vue-next'

defineProps({
  collapsed: { type: Boolean, default: false },
  activeMenu: { type: String, default: '' }
})

defineEmits(['select', 'toggle-collapse'])

const VERSION = 'v1.0.0'

const menuGroups = [
  {
    title: '仪表盘',
    items: [
      { index: 'dashboard', label: '大盘', icon: LayoutDashboard }
    ]
  },
  {
    title: '任务工作流',
    items: [
      { index: 'scheduler', label: '定时扫描', icon: CalendarClock },
      { index: 'qb', label: '下载管理', icon: Download },
      { index: 'cleanup', label: '种子清理', icon: Brush }
    ]
  },
  {
    title: '媒体与演员',
    items: [
      { index: 'manager', label: '标签管理', icon: Tags },
      { index: 'actor', label: '演员', icon: Users },
      { index: 'actorLib', label: '演员库', icon: Library },
      { index: 'editor', label: '分类', icon: FolderTree }
    ]
  },
  {
    title: '系统设置',
    items: [
      { index: 'emby', label: 'Emby', icon: Server },
      { index: 'mp', label: '配置', icon: Settings },
      { index: 'history', label: '记录', icon: History }
    ]
  }
]
</script>

<template>
  <aside
    class="flex h-full flex-shrink-0 flex-col overflow-hidden border-r border-white/5 bg-space-950/80 backdrop-blur-xl transition-all duration-300"
    :class="collapsed ? 'w-16' : 'w-[216px]'"
  >
    <!-- ==================== Logo ==================== -->
    <div
      class="flex h-[54px] flex-shrink-0 items-center gap-2.5 border-b border-white/5 px-3.5"
      :class="collapsed ? 'justify-center px-0' : ''"
    >
      <div
        class="flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded-lg border border-electric/40 bg-gradient-to-br from-blue-500/40 to-violet-500/40 text-blue-300 shadow-[0_0_16px_rgba(59,130,246,0.35),inset_0_0_10px_rgba(59,130,246,0.15)]"
      >
        <Boxes :size="16" />
      </div>
      <span
        v-if="!collapsed"
        class="bg-gradient-to-r from-slate-50 via-blue-300 to-violet-300 bg-clip-text text-[16px] font-extrabold tracking-[0.4px] text-transparent drop-shadow-[0_0_12px_rgba(59,130,246,0.25)]"
      >
        Media Manager
      </span>
    </div>

    <!-- ==================== 分组导航 ==================== -->
    <nav class="sidebar-nav flex-1 overflow-y-auto overflow-x-hidden px-2.5 py-3.5" aria-label="主导航">
      <div v-for="group in menuGroups" :key="group.title" class="mb-5 last:mb-1">
        <!-- 分组标题：小号 / 大写 / 极浅 -->
        <div
          v-if="!collapsed"
          class="px-2.5 pb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500"
        >
          {{ group.title }}
        </div>
        <div v-else class="mx-3 mb-2.5 mt-1 h-px bg-white/5"></div>

        <button
          v-for="item in group.items"
          :key="item.index"
          type="button"
          class="menu-item relative my-0.5 flex w-full items-center gap-2.5 rounded-[10px] border border-transparent px-2.5 py-2.5 text-left text-[13px] font-medium text-slate-400 transition-all duration-300 hover:bg-white/5 hover:text-slate-200"
          :class="[
            collapsed ? 'justify-center px-0' : '',
            { active: item.index === activeMenu }
          ]"
          :title="collapsed ? item.label : undefined"
          :aria-current="item.index === activeMenu ? 'page' : undefined"
          @click="$emit('select', item.index)"
        >
          <!-- 选中态：左侧 3px 电光蓝发光指示线 -->
          <span
            v-if="item.index === activeMenu"
            class="absolute left-0 top-1/2 h-[58%] w-[3px] -translate-y-1/2 rounded-r bg-gradient-to-b from-blue-400 to-violet-400 shadow-[0_0_10px_rgba(59,130,246,0.9)]"
          ></span>
          <component
            :is="item.icon"
            class="item-icon flex-shrink-0 transition-colors duration-300"
            :class="item.index === activeMenu ? 'text-blue-400 drop-shadow-[0_0_6px_rgba(59,130,246,0.8)]' : 'text-slate-500'"
            :size="18"
          />
          <span v-if="!collapsed" class="truncate">{{ item.label }}</span>
        </button>
      </div>
    </nav>

    <!-- ==================== 底部悬浮区 ==================== -->
    <div class="flex flex-shrink-0 flex-col gap-1.5 border-t border-white/5 p-2.5">
      <button
        type="button"
        class="flex w-full items-center gap-2.5 rounded-[10px] border border-transparent px-2.5 py-2.5 text-left text-[12.5px] font-medium text-slate-500 transition-all duration-300 hover:bg-white/5 hover:text-slate-300"
        :class="collapsed ? 'justify-center px-0' : ''"
        :title="collapsed ? '展开侧边栏' : '折叠侧边栏'"
        @click="$emit('toggle-collapse')"
      >
        <component :is="collapsed ? PanelLeftOpen : PanelLeftClose" :size="16" />
        <span v-if="!collapsed">折叠侧边栏</span>
      </button>
      <div
        class="flex items-center gap-2 px-2.5"
        :class="collapsed ? 'justify-center px-0' : ''"
      >
        <span class="version-dot h-1.5 w-1.5 flex-shrink-0 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.9)]"></span>
        <span v-if="!collapsed" class="font-hud text-[10.5px] tracking-[0.06em] text-slate-600">{{ VERSION }} · SYSTEM ONLINE</span>
      </div>
    </div>
  </aside>
</template>

<style scoped>
/* ==================== 选中态：低透明度电光蓝 + 发光 ==================== */
.menu-item.active {
  background: rgba(59, 130, 246, 0.1);
  border-color: rgba(59, 130, 246, 0.25);
  color: #ffffff;
  box-shadow:
    inset 0 0 20px rgba(59, 130, 246, 0.06),
    0 0 18px rgba(59, 130, 246, 0.1);
}

/* ==================== 导航区自定义滚动条（4px） ==================== */
.sidebar-nav::-webkit-scrollbar {
  width: 4px;
}
.sidebar-nav::-webkit-scrollbar-track {
  background: transparent;
}
.sidebar-nav::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 2px;
}

/* ==================== 版本指示灯呼吸动画 ==================== */
.version-dot {
  animation: pulse 2.4s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

/* ==================== 尊重系统减弱动效偏好 ==================== */
@media (prefers-reduced-motion: reduce) {
  aside,
  .menu-item,
  .item-icon,
  button,
  .version-dot {
    transition: none !important;
    animation: none !important;
  }
}
</style>
