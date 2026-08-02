<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import draggable from 'vuedraggable'
import {
  Server, Cloud, Sparkles, Save, PlugZap, Loader2,
  ChevronDown, KeyRound, UserRound, Globe, Link2,
  FolderTree, Folder, HardDrive, Cpu, CheckCircle2, XCircle,
  GripVertical, Plus, Trash2
} from 'lucide-vue-next'

// AI Provider 配置项（与后端 config.json 的 ai_providers 结构保持一致）
interface AIProvider {
  name: string
  base_url: string
  alt_base_url: string
  api_key: string
  model_name: string
  _dragId?: number  // UI 临时字段：vuedraggable 稳定 key + 展开状态锚点；保存前剥离，不落盘
}

const MAX_PROVIDERS = 6
const ORDINAL_LABELS = ['首选', '次选', '三选', '四选', '五选', '六选']

// UI 临时唯一 id 生成（镜像 MpConfig.vue 的 _dragId 模式）
let _uidCounter = 0
const genDragId = () => ++_uidCounter

const API_URL = ''
const loading = ref(false)
// null | 'testing' | 'ok' | 'fail' —— 连接状态指示（纯前端 UI 状态，不影响数据结构）
const connState = ref<'testing' | 'ok' | 'fail' | null>(null)
const config = reactive({
  emby_host: '',
  emby_api_key: '',
  emby_user_id: '',
  max_actors_per_media: 50,
  enable_emby_avatar_first: false,
  cd2_media_dir: '',
  cd2_organized_dir: '',
  emby_prefix: '',
  cd2_media_prefix: '',
  // ★ AI 模型统一配置（首选用于所有 AI 功能；翻译时按 首选→次选→三选 瀑布降级）
  // alt_base_url: 备选接口地址，主地址网络连接不通时（本地调试 vs Docker 部署）自动切换
  ai_providers: [
    { name: '首选', base_url: '', alt_base_url: '', api_key: '', model_name: '' },
    { name: '次选', base_url: '', alt_base_url: '', api_key: '', model_name: '' },
    { name: '三选', base_url: '', alt_base_url: '', api_key: '', model_name: '' }
  ] as AIProvider[]
})

const priorityLabel = (idx: number) => ORDINAL_LABELS[idx] || ('Provider ' + (idx + 1))

const providerStatus = (p: AIProvider | null | undefined) => {
  if (!p) return { text: '未配置', tone: 'off' }
  if (p.model_name && p.api_key) return { text: '已就绪', tone: 'ok' }
  if (p.model_name || p.api_key) return { text: '配置不完整', tone: 'warn' }
  return { text: '未配置', tone: 'off' }
}

// 展开状态按 _dragId 锚定（重排 / 删除后不错位）
const openIds = ref<Set<number>>(new Set())
const isOpen = (p: AIProvider) => p._dragId != null && openIds.value.has(p._dragId)
const toggleProvider = (p: AIProvider) => {
  if (p._dragId == null) return
  const next = new Set(openIds.value)
  if (next.has(p._dragId)) next.delete(p._dragId)
  else next.add(p._dragId)
  openIds.value = next
}
const openAll = () => {
  openIds.value = new Set(
    (config.ai_providers as AIProvider[])
      .map((p) => p._dragId)
      .filter((id): id is number => id != null)
  )
}

onMounted(async () => {
  try {
    const res = await axios.get(`${API_URL}/api/config`)
    Object.assign(config, res.data)
    // 归一化 Provider 列表：兼容旧配置数据，backfill alt_base_url 与 UI 临时 _dragId
    if (!Array.isArray(config.ai_providers)) config.ai_providers = []
    config.ai_providers = (config.ai_providers as AIProvider[]).map((p) => ({
      ...p,
      alt_base_url: p.alt_base_url || '',
      _dragId: p._dragId ?? genDragId()
    }))
    openAll()
  } catch (e) {}
})

const atCap = () => (config.ai_providers || []).length >= MAX_PROVIDERS

const addProvider = () => {
  if (atCap()) {
    ElMessage.warning(`最多支持 ${MAX_PROVIDERS} 个模型，请先删除再添加`)
    return
  }
  const dragId = genDragId()
  config.ai_providers.push({
    name: priorityLabel(config.ai_providers.length),
    base_url: '',
    alt_base_url: '',
    api_key: '',
    model_name: '',
    _dragId: dragId
  })
  openIds.value = new Set(openIds.value).add(dragId)
}

const removeProvider = (p: AIProvider) => {
  if (p._dragId != null) {
    const next = new Set(openIds.value)
    next.delete(p._dragId)
    openIds.value = next
  }
  const i = config.ai_providers.findIndex((x) => x._dragId === p._dragId)
  if (i >= 0) config.ai_providers.splice(i, 1)
}

// 重排后：默认序数名称跟随新位置；用户自定义名称（如"硅基流动"）不动
const onDragChange = () => {
  config.ai_providers.forEach((p, idx) => {
    if (typeof p.name === 'string' && ORDINAL_LABELS.includes(p.name)) {
      p.name = priorityLabel(idx)
    }
  })
}

// vuedraggable 稳定 key（onMounted / addProvider 保证每项都有 _dragId）
const getProviderKey = (p: AIProvider) => p._dragId ?? -1

// 保存前剥离 UI 临时字段 _dragId，避免污染 config.json
const cleanPayload = () => {
  const { ai_providers, ...rest } = config
  return {
    ...rest,
    ai_providers: (ai_providers || []).map(({ _dragId, ...p }) => p)
  }
}

const saveConfig = async () => {
  try {
    await axios.post(`${API_URL}/api/config`, cleanPayload())
    ElMessage.success('配置已保存')
  } catch (e) { ElMessage.error('保存失败') }
}

const testConnection = async () => {
  loading.value = true
  connState.value = 'testing'
  try {
    await axios.post(`${API_URL}/api/libraries`, config)
    connState.value = 'ok'
    ElMessage.success('连接成功，API Key 有效')
    await saveConfig()
  } catch (e) {
    connState.value = 'fail'
    ElMessage.error('连接失败: ' + (e instanceof Error ? e.message : String(e)))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="settings-root">
    <!-- ==================== 环境光效（沉浸感背景） ==================== -->
    <div class="ambient ambient-a"></div>
    <div class="ambient ambient-b"></div>
    <div class="grid-overlay"></div>

    <!-- ==================== 吸顶操作栏 ==================== -->
    <div class="sticky-bar">
      <div class="min-w-0">
        <h1 class="page-title">系统设置</h1>
        <p class="mt-0.5 text-xs tracking-widest text-slate-500">Emby 连接 · CD2 网盘 · 智能服务</p>
      </div>
      <div class="flex gap-3">
        <button
          type="button"
          class="btn btn-test"
          :disabled="loading"
          @click="testConnection"
        >
          <Loader2 v-if="loading" :size="16" class="spin" />
          <PlugZap v-else :size="16" />
          <span>测试连接</span>
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="loading"
          @click="saveConfig"
        >
          <Save :size="16" />
          <span>保存配置</span>
        </button>
      </div>
    </div>

    <!-- ==================== 卡片网格：基础连接 / CD2 / 智能服务 ==================== -->
    <div class="content">
      <!-- ==================== Card 1 · Emby 连接设置 ==================== -->
      <section class="glass-card fade-up" style="--d: 0ms">
        <header class="card-head">
          <div class="icon-badge">
            <Server :size="20" />
          </div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[15px] font-bold tracking-wide text-white">Emby 连接设置</h2>
            <p class="mt-1 text-xs leading-relaxed text-slate-500">配置媒体服务器连接信息与访问凭证</p>
          </div>
          <div class="flex-shrink-0">
            <span v-if="connState === 'testing'" class="status-chip st-testing">
              <Loader2 :size="12" class="spin" />测试中
            </span>
            <span v-else-if="connState === 'ok'" class="status-chip st-ok">
              <CheckCircle2 :size="12" />连接正常
            </span>
            <span v-else-if="connState === 'fail'" class="status-chip st-fail">
              <XCircle :size="12" />连接失败
            </span>
          </div>
        </header>

        <div class="card-body">
          <div class="grid grid-cols-1 gap-x-7 gap-y-5 md:grid-cols-2">
            <div>
              <label class="field-label"><Globe :size="13" />Emby 服务器地址 (URL)</label>
              <el-input v-model="config.emby_host" placeholder="http://192.168.1.5:8096" />
              <p class="field-tip">内网 IP 或域名，包含端口号</p>
            </div>
            <div>
              <label class="field-label"><Link2 :size="13" />外网访问 URL (可选)</label>
              <el-input disabled placeholder="暂未启用" />
              <p class="field-tip">用于远程封面图加载等功能</p>
            </div>
            <div>
              <label class="field-label"><KeyRound :size="13" />Emby API Key</label>
              <el-input
                v-model="config.emby_api_key"
                type="password"
                show-password
                placeholder="粘贴你的 API Key"
              />
              <p class="field-tip">在 Emby 后台 → 高级 → API 密钥 中生成</p>
            </div>
            <div>
              <label class="field-label"><UserRound :size="13" />Emby 用户 ID</label>
              <el-input v-model="config.emby_user_id" placeholder="用户详情页地址栏末端的 ID" />
              <p class="field-tip">打开用户详情页，浏览器地址栏最后的 ID</p>
            </div>
          </div>
        </div>
      </section>

      <!-- ==================== Card 2 · CD2 网盘设置 ==================== -->
      <section class="glass-card fade-up" style="--d: 90ms">
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

      <!-- ==================== Card 3 · 智能服务 / AI 模型配置（横跨两列） ==================== -->
      <section class="glass-card fade-up md:col-span-2" style="--d: 180ms">
        <header class="card-head">
          <div class="icon-badge purple">
            <Sparkles :size="20" />
          </div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[15px] font-bold tracking-wide text-white">智能服务 · AI 模型配置</h2>
            <p class="mt-1 text-xs leading-relaxed text-slate-500">多级 Fallback · 所有 AI 功能共用「首选」Provider，翻译按 首选 → 次选 → 三选 瀑布降级</p>
          </div>
        </header>

        <div class="card-body">
          <!-- Provider 可折叠子卡片（拖拽排序 + 增删，上限 6 个） -->
          <div class="provider-list">
            <!-- 空态：未配置任何模型 -->
            <div v-if="!(config.ai_providers || []).length" class="provider-empty">
              <p class="provider-empty-title">尚未配置任何 AI 模型</p>
              <p class="provider-empty-sub">所有 AI 功能（翻译 / 推荐 / 推理 / 打标）将禁用。点击下方「添加模型」开始配置。</p>
            </div>

            <draggable
              v-if="(config.ai_providers || []).length"
              v-model="config.ai_providers"
              class="flex flex-col gap-3"
              :animation="250"
              ghost-class="drag-ghost"
              drag-class="drag-live"
              handle=".drag-handle"
              :item-key="getProviderKey"
              @change="onDragChange"
            >
              <template #item="{ element: p, index }">
                <div class="provider-card" :class="{ 'is-open': isOpen(p) }">
                  <div class="provider-head" @click="toggleProvider(p)">
                    <span class="drag-handle" title="拖拽调整优先级" @click.stop>
                      <GripVertical :size="15" />
                    </span>
                    <span class="priority-badge" :class="'badge-' + (index % 3)">
                      <i></i>{{ priorityLabel(index) }}
                    </span>
                    <span class="provider-model">{{ p.model_name || '未配置模型' }}</span>
                    <span class="provider-state" :class="'st-' + providerStatus(p).tone">
                      <i></i>{{ providerStatus(p).text }}
                    </span>
                    <span class="provider-remove" title="删除该模型" @click.stop="removeProvider(p)">
                      <Trash2 :size="14" />
                    </span>
                    <span class="chevron" :class="{ rotated: isOpen(p) }">
                      <ChevronDown :size="16" />
                    </span>
                  </div>

                  <div class="provider-body" :class="{ hidden: !isOpen(p) }">
                    <div class="provider-body-inner">
                      <div class="grid grid-cols-1 gap-x-6 gap-y-4.5 md:grid-cols-2">
                        <div>
                          <label class="field-label">名称（用于日志标识）</label>
                          <el-input v-model="p.name" placeholder="如：硅基流动 / OpenAI官方" />
                        </div>
                        <div>
                          <label class="field-label"><Cpu :size="13" />模型名称 (Model)</label>
                          <el-input v-model="p.model_name" placeholder="deepseek-ai/DeepSeek-V3" />
                        </div>
                      </div>
                      <div class="mt-5">
                        <label class="field-label"><Link2 :size="13" />接口地址 (Base URL)</label>
                        <el-input v-model="p.base_url" placeholder="https://api.siliconflow.cn/v1（留空使用 OpenAI 默认地址）" />
                      </div>
                      <div class="mt-5">
                        <label class="field-label"><Globe :size="13" />备选接口地址 (Alt Base URL)</label>
                        <el-input v-model="p.alt_base_url" placeholder="例如：http://host.docker.internal:11434/v1 (用于 Docker 兜底)" />
                        <p class="field-tip">非必填。主地址网络连接不通时（本地调试 vs Docker 部署）自动切换到此地址重试</p>
                      </div>
                      <div class="mt-5">
                        <label class="field-label"><KeyRound :size="13" />API Key</label>
                        <el-input v-model="p.api_key" type="password" show-password placeholder="sk-..." />
                      </div>
                    </div>
                  </div>
                </div>
              </template>
            </draggable>

            <!-- 添加模型按钮（达上限禁用） -->
            <button
              type="button"
              class="provider-add"
              :disabled="atCap()"
              @click="addProvider"
            >
              <Plus :size="15" />
              {{ atCap() ? `已达上限（${MAX_PROVIDERS} 个）` : '添加模型' }}
            </button>
          </div>

          <!-- 多模型降级说明 -->
          <div class="ai-note">
            <div class="ai-note-icon"><Sparkles :size="14" /></div>
            <p>
              所有 AI 功能（翻译 / 推荐 / 推理 / 打标）统一使用<b>首选</b> Provider；翻译人名、角色名时额外按
              <b>首选 → 次选 → 三选</b> 瀑布降级，当前 API 限流或报错自动切换下一个。每个 Provider 的
              <b>备选接口地址</b>用于主地址网络连接不通时（如本地 VS Code 调试 ↔ Docker 容器部署）自动切换重试。
              缺少 API Key 或模型名的 Provider 会自动跳过；三项均未配置时 AI 功能将禁用。
            </p>
          </div>

          <!-- 附加智能服务参数 -->
          <div class="mt-5 grid grid-cols-1 gap-x-7 gap-y-5 border-t border-white/5 pt-5 md:grid-cols-2">
            <div>
              <label class="field-label">最大入库演员数</label>
              <el-input-number
                v-model="config.max_actors_per_media"
                :min="1"
                :max="200"
                :step="5"
                controls-position="right"
              />
              <p class="field-tip">抓取全量构建匹配字典，回写 Emby 和入库时截断到此数量</p>
            </div>
            <div>
              <label class="field-label">Emby 原生头像优先 (L0.5)</label>
              <div class="switch-row">
                <el-switch v-model="config.enable_emby_avatar_first" />
                <span class="switch-hint">{{ config.enable_emby_avatar_first ? '已开启 - 优先使用 Emby 头像' : '已关闭 - 走豆瓣/TMDB 获取' }}</span>
              </div>
              <p class="field-tip">开启后在演员画像解析时，L0.5 优先通过 Emby API 获取头像，命中则跳过豆瓣和 TMDB 外部请求。适用于 TMDB 代理不稳定 (503) 的网络环境。</p>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped lang="postcss">
/* ==================== 根容器 ==================== */
.settings-root {
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
  background: radial-gradient(circle, rgba(59, 130, 246, 0.14), transparent 70%);
}
.ambient-b {
  width: 480px; height: 480px;
  bottom: -160px; left: -120px;
  background: radial-gradient(circle, rgba(139, 92, 246, 0.12), transparent 70%);
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
  background: linear-gradient(90deg, #fff 20%, #93c5fd 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

/* ==================== 内容区 — 卡片网格 grid-cols-1 md:grid-cols-2 ==================== */
.content {
  @apply relative z-[1] grid grid-cols-1 gap-6 md:grid-cols-2;
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
  background: linear-gradient(90deg, transparent, rgba(59, 130, 246, 0.55), rgba(139, 92, 246, 0.55), transparent);
}
/* 右上角氛围光斑 */
.glass-card::after {
  content: '';
  position: absolute;
  top: -90px; right: -70px;
  width: 260px; height: 260px;
  z-index: 0;
  pointer-events: none;
  background: radial-gradient(circle, rgba(59, 130, 246, 0.09), transparent 70%);
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
  color: #60a5fa;
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.18), rgba(59, 130, 246, 0.05));
  border: 1px solid rgba(59, 130, 246, 0.32);
  box-shadow: 0 0 18px rgba(59, 130, 246, 0.22), inset 0 0 10px rgba(59, 130, 246, 0.08);
}
.icon-badge.cyan {
  color: #22d3ee;
  background: linear-gradient(135deg, rgba(6, 182, 212, 0.18), rgba(6, 182, 212, 0.05));
  border-color: rgba(6, 182, 212, 0.32);
  box-shadow: 0 0 18px rgba(6, 182, 212, 0.22), inset 0 0 10px rgba(6, 182, 212, 0.08);
}
.icon-badge.purple {
  color: #a78bfa;
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.18), rgba(139, 92, 246, 0.05));
  border-color: rgba(139, 92, 246, 0.32);
  box-shadow: 0 0 18px rgba(139, 92, 246, 0.22), inset 0 0 10px rgba(139, 92, 246, 0.08);
}

/* 连接状态徽章 */
.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid;
}
.st-testing {
  color: #60a5fa;
  border-color: rgba(59, 130, 246, 0.4);
  background: rgba(59, 130, 246, 0.1);
  box-shadow: 0 0 14px rgba(59, 130, 246, 0.25);
}
.st-ok {
  color: #34d399;
  border-color: rgba(52, 211, 153, 0.4);
  background: rgba(52, 211, 153, 0.1);
  box-shadow: 0 0 14px rgba(52, 211, 153, 0.25);
}
.st-fail {
  color: #f87171;
  border-color: rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.1);
  box-shadow: 0 0 14px rgba(239, 68, 68, 0.25);
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

/* ==================== Element Plus 组件深度定制（:deep 穿透） ==================== */
/* Input —— 极暗底 (bg-black/20)，无明显边框；聚焦时电光蓝外发光 */
:deep(.el-input__wrapper) {
  @apply rounded-xl bg-black/20 transition-shadow duration-200;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.10);
}
:deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.28);
}
:deep(.el-input__wrapper.is-focus) {
  background: rgba(59, 130, 246, 0.06);
  border-color: #3b82f6;
  box-shadow: 0 0 0 1px #3b82f6, 0 0 8px rgba(59, 130, 246, 0.5);
}
:deep(.el-input__inner) { color: #f1f5f9; }
:deep(.el-input__inner::placeholder) { color: #475569; }
:deep(.el-input.is-disabled .el-input__wrapper) {
  background: rgba(0, 0, 0, 0.12);
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.06);
}

/* 密码可见切换图标 */
:deep(.el-input__password) { color: #64748b; }
:deep(.el-input__password:hover) { color: #60a5fa; }

/* el-form-item 若被引用，标签与背景同样对齐深色主题 */
:deep(.el-form-item__label) { color: #cbd5e1; }
:deep(.el-form-item__content) { color: #f1f5f9; }

/* InputNumber */
:deep(.el-input-number) { width: 100%; }
:deep(.el-input-number__decrease),
:deep(.el-input-number__increase) {
  background: rgba(255, 255, 255, 0.04);
  color: #94a3b8;
  border-color: rgba(255, 255, 255, 0.08);
}
:deep(.el-input-number__decrease:hover),
:deep(.el-input-number__increase:hover) {
  color: #fff;
  background: rgba(59, 130, 246, 0.16);
}

/* Switch —— 电光蓝发光 */
:deep(.el-switch) {
  --el-switch-on-color: #3b82f6;
  --el-switch-off-color: rgba(148, 163, 184, 0.28);
  --el-switch-border-color: transparent;
}
:deep(.el-switch.is-checked .el-switch__core) {
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.5);
}

.switch-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 40px;
}
.switch-hint { font-size: 12.5px; color: #94a3b8; }

/* ==================== AI Provider 子卡片 ==================== */
.provider-list { display: flex; flex-direction: column; gap: 12px; }

.provider-card {
  position: relative;
  border-radius: 16px;
  background: rgba(2, 6, 23, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.07);
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
  overflow: hidden;
}
.provider-card:hover { border-color: rgba(255, 255, 255, 0.14); }
.provider-card.is-open {
  border-color: rgba(59, 130, 246, 0.28);
  box-shadow: 0 8px 30px -18px rgba(59, 130, 246, 0.35);
}

.provider-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 14px 18px;
  cursor: pointer;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
}
.provider-model {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: #cbd5e1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.provider-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #64748b;
}
.provider-state i {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: #64748b;
}
.provider-state.st-ok i { background: #34d399; box-shadow: 0 0 7px rgba(52, 211, 153, 0.9); }
.provider-state.st-ok { color: #6ee7b7; }
.provider-state.st-warn i { background: #fbbf24; box-shadow: 0 0 7px rgba(251, 191, 36, 0.9); }
.provider-state.st-warn { color: #fcd34d; }
.provider-state.st-off i { background: #64748b; }

.chevron {
  display: flex;
  color: #64748b;
  transition: transform 0.3s ease, color 0.3s ease;
}
.chevron.rotated {
  transform: rotate(180deg);
  color: #60a5fa;
}

/* 展开/收起动画（grid-rows 技巧） */
.provider-body {
  display: grid;
  grid-template-rows: 1fr;
  opacity: 1;
  transition: grid-template-rows 0.3s ease, opacity 0.25s ease;
}
.provider-body.hidden {
  grid-template-rows: 0fr;
  opacity: 0;
}
.provider-body-inner {
  overflow: hidden;
  min-height: 0;
  padding: 0 18px 18px;
}

/* 发光药丸徽章 —— 优先级 */
.priority-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.5px;
  border: 1px solid;
  flex-shrink: 0;
}
.priority-badge i {
  width: 5px; height: 5px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
}
.badge-0 {
  color: #60a5fa;
  border-color: rgba(59, 130, 246, 0.45);
  background: rgba(59, 130, 246, 0.12);
  box-shadow: 0 0 14px rgba(59, 130, 246, 0.32), inset 0 0 8px rgba(59, 130, 246, 0.12);
  text-shadow: 0 0 8px rgba(96, 165, 250, 0.6);
}
.badge-1 {
  color: #a78bfa;
  border-color: rgba(139, 92, 246, 0.45);
  background: rgba(139, 92, 246, 0.12);
  box-shadow: 0 0 14px rgba(139, 92, 246, 0.32), inset 0 0 8px rgba(139, 92, 246, 0.12);
  text-shadow: 0 0 8px rgba(167, 139, 250, 0.6);
}
.badge-2 {
  color: #22d3ee;
  border-color: rgba(6, 182, 212, 0.45);
  background: rgba(6, 182, 212, 0.12);
  box-shadow: 0 0 14px rgba(6, 182, 212, 0.32), inset 0 0 8px rgba(6, 182, 212, 0.12);
  text-shadow: 0 0 8px rgba(34, 211, 238, 0.6);
}

/* 多模型降级说明 */
.ai-note {
  display: flex;
  gap: 10px;
  margin-top: 18px;
  padding: 14px 16px;
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.06), rgba(139, 92, 246, 0.06));
  border: 1px solid rgba(59, 130, 246, 0.16);
}
.ai-note-icon { flex-shrink: 0; display: flex; margin-top: 1px; color: #a78bfa; }
.ai-note p { font-size: 12.5px; line-height: 1.7; color: #94a3b8; }
.ai-note b { color: #e2e8f0; font-weight: 600; }

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

.btn-test {
  background: rgba(16, 185, 129, 0.09);
  color: #6ee7b7;
  border-color: rgba(16, 185, 129, 0.32);
}
.btn-test:hover:not(:disabled) {
  background: rgba(16, 185, 129, 0.16);
  color: #a7f3d0;
  box-shadow: 0 0 18px rgba(16, 185, 129, 0.28);
}

.btn-primary {
  background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
  box-shadow: 0 4px 20px rgba(59, 130, 246, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.2);
}
.btn-primary:hover:not(:disabled) {
  transform: translateY(-1px) scale(1.03);
  box-shadow: 0 8px 30px rgba(99, 102, 241, 0.55), 0 0 26px rgba(139, 92, 246, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.2);
}
.btn-primary:active:not(:disabled) { transform: scale(0.98); }

/* ==================== 动画 ==================== */
@keyframes spin { to { transform: rotate(360deg); } }
.spin { animation: spin 1s linear infinite; }

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
  .settings-root { padding: 10px 12px 32px; }
  .sticky-bar { padding: 12px 2px; }
  .page-title { font-size: 17px; }
  .sticky-actions { width: 100%; }
  .btn { flex: 1; }
  .card-head { @apply px-4 py-3.5; }
  .card-body { @apply p-4; }
  .provider-model { flex-basis: 100%; order: 3; }
}

@media (prefers-reduced-motion: reduce) {
  .fade-up, .spin, .chevron, .provider-body, .btn {
    animation: none !important;
    transition: none !important;
  }
  /* fade-up 基础态为透明，禁用动画时必须强制可见，否则内容会消失 */
  .fade-up { opacity: 1; }
}

/* ==================== AI Provider 拖拽排序 + 增删 ==================== */
.drag-handle {
  display: inline-flex;
  align-items: center;
  color: rgba(255, 255, 255, 0.35);
  cursor: grab;
  user-select: none;
  transition: color 0.2s ease;
}
.drag-handle:hover { color: rgba(255, 255, 255, 0.7); }
.drag-handle:active { cursor: grabbing; }

.provider-remove {
  display: inline-flex;
  align-items: center;
  color: rgba(255, 255, 255, 0.35);
  cursor: pointer;
  transition: color 0.2s ease;
}
.provider-remove:hover { color: #f87171; }

:deep(.drag-ghost) {
  opacity: 0.3;
  background: rgba(59, 130, 246, 0.12) !important;
  border: 1px dashed #3b82f6 !important;
  border-radius: 16px;
}
:deep(.drag-live) {
  transform: scale(1.04) !important;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5), 0 0 20px rgba(59, 130, 246, 0.3) !important;
  z-index: 1000 !important;
  cursor: grabbing !important;
  background: rgba(255, 255, 255, 0.08) !important;
  border-color: #3b82f6 !important;
}

.provider-add {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  padding: 10px 0;
  border: 1px dashed rgba(255, 255, 255, 0.15);
  border-radius: 12px;
  background: transparent;
  color: rgba(255, 255, 255, 0.55);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
}
.provider-add:hover:not(:disabled) {
  border-color: rgba(59, 130, 246, 0.6);
  color: rgba(255, 255, 255, 0.85);
  background: rgba(59, 130, 246, 0.08);
}
.provider-add:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.provider-empty {
  padding: 24px 0;
  text-align: center;
  border: 1px dashed rgba(255, 255, 255, 0.12);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.02);
}
.provider-empty-title {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.8);
}
.provider-empty-sub {
  margin: 0;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.4);
}
</style>
