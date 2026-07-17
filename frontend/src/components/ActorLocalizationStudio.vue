<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import {
  Search, SwitchButton, CircleCheck, CircleClose, RefreshRight,
  Upload, VideoCamera, Picture, Lock, Loading, VideoPlay
} from '@element-plus/icons-vue'

const API_URL = ''
const config = reactive({})
const libraries = ref([])
const items = ref([])
const loading = ref(false)
const batchLoading = ref(false)
const autoUpdateEnabled = ref(false)
const autoUpdating = ref(false)
const searchQuery = ref('')
const selectedLibrary = ref('')
const selectedStatus = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const totalItems = ref(0)

const libraryOptions = computed(() => [
  { label: '全部媒体库', value: '' },
  ...libraries.value.map(l => ({ label: l.Name, value: l.ItemId || l.Id }))
])

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '未汉化', value: 'pending' },
  { label: '已同步', value: 'synced' },
  { label: '已锁定', value: 'locked' },
]

const filteredItems = computed(() => {
  let result = items.value
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    result = result.filter(i => i.name.toLowerCase().includes(q))
  }
  if (selectedStatus.value) result = result.filter(i => i.status === selectedStatus.value)
  return result
})

const stats = computed(() => {
  const total = items.value.length
  const synced = items.value.filter(i => i.status === 'synced' || i.status === 'locked').length
  return { total, synced }
})

const isAllChecked = computed({
  get: () => filteredItems.value.length > 0 && filteredItems.value.every(i => i.checked),
  set: (val) => { filteredItems.value.forEach(i => { i.checked = val }) }
})

const checkedIds = computed(() => items.value.filter(i => i.checked).map(i => i.id))
const pendingCheckedIds = computed(() => items.value.filter(i => i.checked && i.status === 'pending').map(i => i.id))

const connectEmby = async () => {
  try {
    const res = await axios.post(API_URL + '/api/libraries', config)
    libraries.value = res.data || []
  } catch (e) {
    ElMessage.error('连接 Emby 失败: ' + (e.response?.data?.detail || e.message))
  }
}

const loadItems = async () => {
  if (!selectedLibrary.value) return
  loading.value = true
  items.value = []
  try {
    const startIndex = (currentPage.value - 1) * pageSize.value
    const res = await axios.post(API_URL + '/api/actor_items', {
      ...config, library_id: selectedLibrary.value, limit: pageSize.value, start_index: startIndex
    })
    items.value = (res.data.items || []).map(item => ({
      id: item.id, name: item.name, year: item.year, type: item.type,
      actors: item.actors || [], poster_url: item.poster_url || null, provider_ids: item.provider_ids || {},
      status: 'pending', checked: false, syncing: false, syncResult: null
    }))
    totalItems.value = res.data.total || 0
    ElMessage.success('已加载 ' + items.value.length + ' 个媒体项（共 ' + totalItems.value + '）')
  } catch (e) {
    ElMessage.error('加载失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

const handleSyncItem = async (item) => {
  if (item.status === 'locked') { ElMessage.warning('该项目已锁定，无法同步'); return }
  item.syncing = true; item.syncResult = null
  try {
    const res = await axios.post(API_URL + '/api/douban/sinicize', { item_id: item.id })
    if (res.data.success) {
      item.status = 'synced'; item.syncResult = res.data
      try {
        const dr = await axios.post(API_URL + '/api/actor_items', { ...config, library_id: selectedLibrary.value, limit: 20 })
        const ref = (dr.data.items || []).find(i => i.id === item.id)
        if (ref) { item.actors = ref.actors || []; item.provider_ids = ref.provider_ids || {} }
      } catch (e) {}
      ElMessage.success('《' + item.name + '》同步成功：匹配 ' + res.data.matched + '/' + res.data.total_actors + ' 位演员')
    } else {
      ElMessage.error('《' + item.name + '》同步失败')
    }
  } catch (e) {
    item.syncResult = { success: false, error: e.response?.data?.detail || e.message }
    ElMessage.error('《' + item.name + '》同步异常')
  } finally { item.syncing = false }
}

const handleBatchSync = async () => {
  const ids = pendingCheckedIds.value
  if (ids.length === 0) { ElMessage.warning('请至少勾选一个未汉化状态的媒体项'); return }
  batchLoading.value = true; let done = 0, failed = 0
  for (const id of ids) {
    const item = items.value.find(i => i.id === id)
    if (!item) continue
    item.syncing = true
    try {
      const res = await axios.post(API_URL + '/api/douban/sinicize', { item_id: id })
      if (res.data.success) { item.status = 'synced'; item.syncResult = res.data; done++ } else { failed++ }
    } catch (e) { failed++ }
    finally { item.syncing = false }
  }
  batchLoading.value = false
  ElMessage.success('批量同步完成：成功 ' + done + '，失败 ' + failed)
}

const handleToggleAuto = async (val) => {
  autoUpdating.value = true
  try {
    autoUpdateEnabled.value = val
    ElMessage.success(val ? '自动化更新已开启' : '自动化更新已关闭')
  } catch (e) { ElMessage.error('切换失败'); autoUpdateEnabled.value = !val }
  finally { autoUpdating.value = false }
}

const statusLabel = (s) => ({ pending: '未汉化', synced: '已同步', locked: '已锁定' }[s] || s)
const statusColor = (s) => ({ pending: '#f59e0b', synced: '#10b981', locked: '#64748b' }[s] || '#94a3b8')

const getPosterGradient = (name) => {
  const g = ['linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)',
    'linear-gradient(135deg, #1a1a2e 0%, #1a1a2e 50%, #533483 100%)',
    'linear-gradient(135deg, #16213e 0%, #0f3460 50%, #16213e 100%)',
    'linear-gradient(135deg, #1a1a2e 0%, #2d3436 50%, #1a1a2e 100%)',
    'linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%)']
  let h = 0
  for (let i = 0; i < name.length; i++) h = name.charCodeAt(i) + ((h << 5) - h)
  return g[Math.abs(h) % g.length]
}

const getPosterUrl = (item) => {
  if (!config.emby_host || !config.emby_api_key || !item.id) return null
  return config.emby_host + '/emby/Items/' + item.id + '/Images/Primary?api_key=' + config.emby_api_key
}

const getSyncTag = (item) => {
  if (!item.syncResult) return null
  if (item.syncResult.success) return '✓ 匹配 ' + item.syncResult.matched + '/' + item.syncResult.total_actors
  return '✘ 失败'
}

onMounted(async () => {
  try {
    const res = await axios.get(API_URL + '/api/config')
    Object.assign(config, res.data)
    if (config.emby_api_key) await connectEmby()
  } catch (e) {}
})
</script>
<template>
  <div class="studio-root">
    <div class="header-bar">
      <div class="header-left">
        <h1 class="page-title">演职员中文化治理</h1>
        <div class="stats-badge">
          <span class="stats-dot" />
          <span>已汉化 <strong>{{ stats.synced }}</strong> / {{ stats.total }}</span>
        </div>
      </div>
      <div class="header-center">
        <el-select v-model="selectedLibrary" placeholder="选择媒体库" class="header-select" size="default" @change="currentPage=1;loadItems()">
          <el-option v-for="opt in libraryOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>
        <el-select v-model="selectedStatus" placeholder="全部状态" class="header-select" size="default">
          <el-option v-for="opt in statusOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>
      </div>
      <div class="header-right">
        <el-input v-model="searchQuery" placeholder="搜索剧名..." :prefix-icon="Search" class="search-input" clearable size="default" />
        <div class="auto-switch-group">
          <span class="switch-label">自动化更新</span>
          <el-switch v-model="autoUpdateEnabled" :loading="autoUpdating" @change="handleToggleAuto" />
        </div>
      </div>
    </div>
    <div class="select-all-row" v-if="filteredItems.length > 0">
      <el-checkbox v-model="isAllChecked" :indeterminate="checkedIds.length > 0 && !isAllChecked">
        全选 ({{ checkedIds.length }}/{{ filteredItems.length }})
      </el-checkbox>
      <span class="select-info">已选 {{ pendingCheckedIds.length }} 个待处理项</span>
      <el-button type="primary" size="small" class="btn-batch-inline" :loading="batchLoading" :disabled="pendingCheckedIds.length === 0" @click="handleBatchSync">
        {{ pendingCheckedIds.length > 0 ? '同步选中项 (' + pendingCheckedIds.length + ')' : '批量执行中文化' }}
      </el-button>
    </div>
    <div class="cards-grid">
      <div v-if="loading" class="loading-overlay">
        <el-icon class="is-loading" :size="32"><Loading /></el-icon>
        <span>正在加载媒体数据...</span>
      </div>
      <div v-for="item in filteredItems" :key="item.id" class="media-card" :class="{ 'is-locked': item.status === 'locked', 'is-synced': item.status === 'synced', 'is-checked': item.checked }" @click="item.checked = !item.checked">
        <div class="card-check" @click.stop>
          <el-checkbox v-model="item.checked" @click.stop />
        </div>
        <div class="card-poster" :style="{ background: getPosterGradient(item.name) }">
          <el-image :src="getPosterUrl(item)" class="poster-img" lazy fit="cover">
            <template #placeholder><div class="poster-skeleton"></div></template>
            <template #error><div class="poster-placeholder"><el-icon :size="20"><VideoCamera /></el-icon></div></template>
          </el-image>
          <span class="poster-type">{{ item.type === 'Movie' ? '电影' : '剧集' }}</span>
        </div>
        <div class="card-body">
          <div class="card-header">
            <span class="card-title">{{ item.name }}</span>
            <span class="card-year">{{ item.year }}</span>
            <span class="card-status" :style="{ color: statusColor(item.status) }">{{ statusLabel(item.status) }}</span>
          </div>
          <div class="actor-compare">
            <div class="compare-col compare-emby">
              <div class="compare-label">Emby 当前</div>
              <div class="actor-list">
                <div v-for="(a, ai) in item.actors.slice(0, 5)" :key="'emby-' + ai" class="actor-row">
                  <span class="actor-name">{{ a.Name || a.name }}</span>
                  <span class="actor-role">{{ a.Role || a.role }}</span>
                </div>
                <div v-if="item.actors.length > 5" class="actor-more">+{{ item.actors.length - 5 }} 更多</div>
                <div v-if="item.actors.length === 0" class="actor-empty">暂无演员</div>
              </div>
            </div>
            <div class="compare-divider"></div>
            <div class="compare-col compare-douban">
              <div class="compare-label">豆瓣同步</div>
              <div class="actor-list" v-if="item.syncing">
                <div class="actor-syncing"><el-icon class="is-loading" :size="12"><Loading /></el-icon> 抓取中...</div>
              </div>
              <div class="actor-list" v-else-if="item.status === 'synced' && item.syncResult">
                <div v-for="(d, di) in (item.syncResult.details || []).slice(0, 5)" :key="'detail-' + di" class="actor-row">
                  <span class="actor-name">{{ d.new_name || d.emby_name }}</span>
                  <span class="actor-role">{{ d.new_role || '' }}</span>
                </div>
                <div class="sync-tag">{{ getSyncTag(item) }}</div>
              </div>
              <div class="actor-list" v-else>
                <div class="actor-empty hint">未同步</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="!loading && filteredItems.length === 0 && selectedLibrary" class="empty-state">
        <el-icon :size="48"><Picture /></el-icon>
        <p>暂无匹配的媒体项</p>
        <span>尝试调整筛选条件或搜索关键词</span>
      </div>
      <div v-if="!loading && !selectedLibrary" class="empty-state">
        <el-icon :size="48"><VideoPlay /></el-icon>
        <p>请先选择媒体库</p>
        <span>在上方下拉框中选择 Emby 媒体库后自动加载数据</span>
      </div>
    </div>

    <div class="pagination-bar" v-if="totalItems > 0">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="totalItems"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next, jumper"
        background
        small
        @current-change="loadItems"
        @size-change="currentPage=1;loadItems()"
      />
    </div>
  </div>
</template>

<style scoped>
.studio-root{--s-bg:#0a0a0a;--s-card:#1a1a1a;--s-card-hv:#222;--s-accent:#00A3FF;--s-accent-soft:rgba(0,163,255,.12);--s-border:#2a2a2a;--s-text:#e0e0e0;--s-text2:#888;--s-text3:#555;background:var(--s-bg);min-height:100vh;padding:20px 24px;color:var(--s-text)}
.header-bar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 20px;background:var(--s-card);border:1px solid var(--s-border);border-radius:10px;margin-bottom:14px;flex-wrap:wrap}
.header-left{display:flex;align-items:center;gap:14px}
.page-title{font-size:18px;font-weight:700;color:#fff;white-space:nowrap}
.stats-badge{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--s-text2);padding:4px 10px;background:rgba(255,255,255,.04);border-radius:20px}
.stats-badge strong{color:var(--s-accent);font-weight:600}
.stats-dot{width:7px;height:7px;border-radius:50%;background:var(--s-accent);box-shadow:0 0 6px rgba(0,163,255,.5)}
.header-center{display:flex;align-items:center;gap:10px}
.header-select{width:150px}
.header-right{display:flex;align-items:center;gap:10px}
.search-input{width:180px}
.auto-switch-group{display:flex;align-items:center;gap:6px;padding:4px 10px;background:rgba(255,255,255,.03);border-radius:8px;border:1px solid var(--s-border)}
.switch-label{font-size:12px;color:var(--s-text2);white-space:nowrap}
.select-all-row{display:flex;align-items:center;gap:14px;padding:6px 16px;margin-bottom:12px;font-size:13px;color:var(--s-text2)}
.select-info{font-size:12px;color:var(--s-accent)}
.btn-batch-inline{font-weight:500;border-radius:6px;font-size:12px;margin-left:auto;background:var(--s-accent)!important;border-color:var(--s-accent)!important}
.btn-batch-inline:hover{background:#0090e0!important;border-color:#0090e0!important}

.cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:16px}
@media(min-width:1800px){.cards-grid{grid-template-columns:repeat(4,1fr)}}
@media(max-width:1600px){.cards-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:1200px){.cards-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:768px){.cards-grid{grid-template-columns:1fr}}

.loading-overlay{grid-column:1/-1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:80px 20px;gap:14px;color:var(--s-text2);font-size:14px}
.loading-overlay .el-icon{color:var(--s-accent)}
.empty-state{grid-column:1/-1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:80px 20px;color:var(--s-text2)}
.empty-state p{font-size:16px;margin:14px 0 6px;color:var(--s-text)}
.empty-state span{font-size:13px;color:var(--s-text3)}

.media-card{display:flex;gap:0;padding:0;background:var(--s-card);border:1px solid var(--s-border);border-radius:10px;transition:all .2s;cursor:pointer;overflow:hidden;min-width:0;position:relative}
.media-card:hover{background:var(--s-card-hv);border-color:#444}
.media-card.is-checked{border-color:var(--s-accent)!important;box-shadow:0 0 0 1px var(--s-accent)}
.media-card.is-locked{opacity:.65}
.media-card.is-synced{border-color:rgba(16,185,129,.2)}
.card-check{position:absolute;top:10px;left:10px;z-index:3}
.card-check :deep(.el-checkbox__inner){background:rgba(0,0,0,.5);border-color:rgba(255,255,255,.3)}

.card-poster{width:130px;aspect-ratio:2/3;border-radius:10px 0 0 10px;display:flex;flex-direction:column;align-items:center;justify-content:center;flex-shrink:0;position:relative;overflow:hidden}
.poster-img{position:absolute;inset:0;width:100%;height:100%}
.poster-img :deep(img){object-fit:cover;width:100%;height:100%;transition:opacity .5s ease}
.poster-img :deep(.el-image__placeholder),.poster-img :deep(.el-image__error){position:absolute;inset:0}
.poster-skeleton{position:absolute;inset:0;background:linear-gradient(90deg,#1a1a2e 25%,#222 50%,#1a1a2e 75%);background-size:200% 100%;animation:shimmer 1.8s infinite}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.poster-placeholder{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:rgba(255,255,255,.15)}
.poster-type{font-size:9px;color:rgba(255,255,255,.5);position:absolute;bottom:4px;right:4px;z-index:2;background:rgba(0,0,0,.6);padding:1px 5px;border-radius:3px}

.card-body{flex:1;min-width:0;display:flex;flex-direction:column;padding:14px 16px;gap:10px}
.card-header{display:flex;align-items:baseline;gap:6px;flex-wrap:wrap;min-width:0}
.card-title{font-size:14px;font-weight:600;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}
.card-year{font-size:11px;color:var(--s-text3);flex-shrink:0}
.card-status{font-size:10px;font-weight:500;white-space:nowrap;flex-shrink:0;margin-left:auto;padding:1px 6px;border-radius:10px;background:rgba(255,255,255,.05)}

.actor-compare{display:grid;grid-template-columns:minmax(0,1fr) 1px minmax(0,1fr);gap:16px;flex:1;min-width:0;max-width:340px}
.compare-col{min-width:0;overflow:hidden}
.compare-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.3px;color:var(--s-text3);margin-bottom:4px;padding-bottom:3px;border-bottom:1px solid var(--s-border);white-space:nowrap}
.compare-douban .compare-label{color:var(--s-accent)}
.compare-divider{width:1px;background:var(--s-border);align-self:stretch}
.actor-list{display:flex;flex-direction:column;gap:1px}
.actor-row{display:flex;align-items:center;justify-content:space-between;padding:2px 0;min-width:0}
.actor-name{font-size:12px;color:var(--s-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0;margin-right:6px}
.actor-role{font-size:11px;color:var(--s-text3);text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:50%;flex-shrink:0}
.actor-more{font-size:10px;color:var(--s-text3);padding-top:2px}
.actor-empty{font-size:11px;color:var(--s-text3);padding:4px 0}
.actor-empty.hint{color:#444;font-style:italic;font-size:11px;padding:6px 0}
.actor-syncing{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--s-text3);padding:6px 0}
.sync-tag{font-size:10px;padding:2px 6px;border-radius:4px;margin-top:4px;color:#10b981;background:rgba(16,185,129,.1)}

.header-select :deep(.el-input__wrapper),.search-input :deep(.el-input__wrapper){background:var(--s-card)!important;border-radius:8px!important;box-shadow:0 0 0 1px var(--s-border)!important}
.header-select :deep(.el-input__wrapper:hover),.search-input :deep(.el-input__wrapper:hover){box-shadow:0 0 0 1px #444!important}
.header-select :deep(.el-input__wrapper.is-focus),.search-input :deep(.el-input__wrapper.is-focus){box-shadow:0 0 0 1px var(--s-accent)!important}
.header-select :deep(.el-input__inner),.search-input :deep(.el-input__inner){color:var(--s-text)}
.auto-switch-group :deep(.el-switch.is-checked .el-switch__core){background-color:var(--s-accent);border-color:var(--s-accent)}
.select-all-row :deep(.el-checkbox__label),.card-check :deep(.el-checkbox__label){color:var(--s-text2)!important}

.pagination-bar{display:flex;justify-content:center;margin-top:24px;padding-bottom:20px}
@media(max-width:1024px){.header-bar{flex-direction:column;align-items:stretch}.header-center,.header-right{flex-wrap:wrap;justify-content:flex-start}}
</style>
