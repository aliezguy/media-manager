<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import {
  Sparkles, Languages, Timer, Film, Save, CalendarClock, Cpu
} from 'lucide-vue-next'

const API_URL = ''

// AI 补全 / 汉化调参（与后端 config.yaml 键一致；保存只提交本 Tab 的键，后端浅合并）
const config = reactive({
  // 演员元数据 AI 补全
  actor_ai_enabled: true,
  actor_ai_local_first: true,
  llm_cooldown_days: 7,
  // 全库 Overview 汉化
  overview_translation_enabled: true,
  overview_local_first: true,
  overview_chinese_ratio: 0.5,
  overview_max_tokens: 1500,
  overview_job: { library_ids: [] as string[], cron_expression: '0 5 * * *', is_active: false, last_run_at: null as string | null },
  // 请求预算（豆瓣 / TMDB / Emby 回写）
  request_budget: { douban_per_series: 30, tmdb_per_min: 60, emby_writeback_per_series: 50 },
  // 演员简介 / 分集
  actor_bio_inline_enabled: false,
  sinicize_translate_episode_overviews: true
})

// library_ids 数组 ↔ 逗号分隔文本（UI 友好输入）
const libraryIdsText = ref('')

onMounted(async () => {
  try {
    const res = await axios.get(`${API_URL}/api/config`)
    const d = res.data || {}
    config.actor_ai_enabled = d.actor_ai_enabled !== false
    config.actor_ai_local_first = d.actor_ai_local_first !== false
    config.llm_cooldown_days = typeof d.llm_cooldown_days === 'number' ? d.llm_cooldown_days : 7
    config.overview_translation_enabled = d.overview_translation_enabled !== false
    config.overview_local_first = d.overview_local_first !== false
    config.overview_chinese_ratio = typeof d.overview_chinese_ratio === 'number' ? d.overview_chinese_ratio : 0.5
    config.overview_max_tokens = typeof d.overview_max_tokens === 'number' ? d.overview_max_tokens : 1500
    const job = d.overview_job || {}
    config.overview_job = {
      library_ids: Array.isArray(job.library_ids) ? job.library_ids.map(String) : [],
      cron_expression: job.cron_expression || '0 5 * * *',
      is_active: !!job.is_active,
      last_run_at: job.last_run_at ?? null
    }
    libraryIdsText.value = config.overview_job.library_ids.join(',')
    const rb = d.request_budget || {}
    config.request_budget = {
      douban_per_series: typeof rb.douban_per_series === 'number' ? rb.douban_per_series : 30,
      tmdb_per_min: typeof rb.tmdb_per_min === 'number' ? rb.tmdb_per_min : 60,
      emby_writeback_per_series: typeof rb.emby_writeback_per_series === 'number' ? rb.emby_writeback_per_series : 50
    }
    config.actor_bio_inline_enabled = !!d.actor_bio_inline_enabled
    config.sinicize_translate_episode_overviews = d.sinicize_translate_episode_overviews !== false
  } catch (e) {}
})

const cleanPayload = () => {
  const libIds = libraryIdsText.value.split(',').map(s => s.trim()).filter(Boolean)
  return {
    actor_ai_enabled: config.actor_ai_enabled,
    actor_ai_local_first: config.actor_ai_local_first,
    llm_cooldown_days: config.llm_cooldown_days,
    overview_translation_enabled: config.overview_translation_enabled,
    overview_local_first: config.overview_local_first,
    overview_chinese_ratio: config.overview_chinese_ratio,
    overview_max_tokens: config.overview_max_tokens,
    overview_job: { ...config.overview_job, library_ids: libIds },
    request_budget: { ...config.request_budget },
    actor_bio_inline_enabled: config.actor_bio_inline_enabled,
    sinicize_translate_episode_overviews: config.sinicize_translate_episode_overviews
  }
}

const saveConfig = async () => {
  try {
    await axios.post(`${API_URL}/api/config`, cleanPayload())
    ElMessage.success('配置已保存')
  } catch (e) { ElMessage.error('保存失败') }
}
</script>

<template>
  <div class="settings-root">
    <!-- ==================== 沉浸感背景光效 ==================== -->
    <div class="ambient ambient-a"></div>
    <div class="ambient ambient-b"></div>
    <div class="grid-overlay"></div>

    <!-- ==================== 吸顶操作栏 ==================== -->
    <div class="sticky-bar">
      <div class="min-w-0">
        <h1 class="page-title">AI · 汉化调参</h1>
        <p class="mt-0.5 text-xs tracking-widest text-slate-500">演员元数据 AI 补全 · 全库 Overview 汉化 · 请求预算 · 分集简介</p>
      </div>
      <div class="flex gap-3">
        <button
          type="button"
          class="btn btn-primary"
          @click="saveConfig"
        >
          <Save :size="16" />
          <span>保存配置</span>
        </button>
      </div>
    </div>

    <div class="content">
      <!-- ==================== 演员元数据 AI 补全 ==================== -->
      <section class="glass-card fade-up" style="--d: 0ms">
        <header class="card-head">
          <div class="icon-badge cyan"><Sparkles :size="20" /></div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[15px] font-bold tracking-wide text-white">演员元数据 AI 补全</h2>
            <p class="mt-1 text-xs leading-relaxed text-slate-500">出生地汉化 + 空值补全，严格防伪 NULL</p>
          </div>
        </header>
        <div class="card-body">
          <div class="grid grid-cols-1 gap-x-7 gap-y-5 md:grid-cols-2">
            <div>
              <label class="field-label"><Sparkles :size="13" />AI 补全总开关</label>
              <div class="switch-row">
                <el-switch v-model="config.actor_ai_enabled" />
                <span class="switch-hint">{{ config.actor_ai_enabled ? '已开启' : '已关闭' }}</span>
              </div>
              <p class="field-tip">关闭后跳过所有演员元数据 LLM 调用（仅用 TMDB/豆瓣免费元数据）</p>
            </div>
            <div>
              <label class="field-label"><Cpu :size="13" />本地大模型优先</label>
              <div class="switch-row">
                <el-switch v-model="config.actor_ai_local_first" />
                <span class="switch-hint">{{ config.actor_ai_local_first ? '本地优先' : '云端优先' }}</span>
              </div>
              <p class="field-tip">本地 qwen2.5 优先（ollama），翻译不到再走其他 Provider</p>
            </div>
            <div>
              <label class="field-label"><Timer :size="13" />LLM 冷静期（天）</label>
              <el-input-number
                v-model="config.llm_cooldown_days"
                :min="-1"
                :max="365"
                controls-position="right"
              />
              <p class="field-tip">-1 无限期（未知永不再查）· 0 无冷静期 · N 天内不重查</p>
            </div>
          </div>
        </div>
      </section>

      <!-- ==================== 全库 Overview 汉化 ==================== -->
      <section class="glass-card fade-up" style="--d: 80ms">
        <header class="card-head">
          <div class="icon-badge violet"><Languages :size="20" /></div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[15px] font-bold tracking-wide text-white">全库 Overview 汉化</h2>
            <p class="mt-1 text-xs leading-relaxed text-slate-500">本地 qwen 优先，失败/超时云端兜底；定时任务扫描翻译</p>
          </div>
        </header>
        <div class="card-body">
          <div class="grid grid-cols-1 gap-x-7 gap-y-5 md:grid-cols-2">
            <div>
              <label class="field-label"><Languages :size="13" />汉化总开关</label>
              <div class="switch-row">
                <el-switch v-model="config.overview_translation_enabled" />
                <span class="switch-hint">{{ config.overview_translation_enabled ? '已开启' : '已关闭' }}</span>
              </div>
              <p class="field-tip">False 时全库汉化任务不执行</p>
            </div>
            <div>
              <label class="field-label"><Cpu :size="13" />本地优先</label>
              <div class="switch-row">
                <el-switch v-model="config.overview_local_first" />
                <span class="switch-hint">{{ config.overview_local_first ? '本地优先' : '云端优先' }}</span>
              </div>
              <p class="field-tip">本地 qwen 优先，超时/失败/NULL/未过中文校验 → 云端兜底</p>
            </div>
            <div>
              <label class="field-label">中文判定阈值</label>
              <el-input-number
                v-model="config.overview_chinese_ratio"
                :min="0"
                :max="1"
                :step="0.05"
                controls-position="right"
              />
              <p class="field-tip">「已中文」判定阈值（is_already_chinese），默认 0.5</p>
            </div>
            <div>
              <label class="field-label">翻译输出上限（tokens）</label>
              <el-input-number
                v-model="config.overview_max_tokens"
                :min="200"
                :max="8000"
                :step="100"
                controls-position="right"
              />
              <p class="field-tip">单条简介翻译输出 token 上限</p>
            </div>
          </div>

          <!-- 定时任务 -->
          <div class="mt-5 grid grid-cols-1 gap-x-7 gap-y-5 border-t border-white/5 pt-5 md:grid-cols-2">
            <div>
              <label class="field-label"><CalendarClock :size="13" />定时扫描任务</label>
              <div class="switch-row">
                <el-switch v-model="config.overview_job.is_active" />
                <span class="switch-hint">{{ config.overview_job.is_active ? '已启用' : '已停用' }}</span>
              </div>
              <p class="field-tip">启用的定时任务按下方 cron 周期扫描全库翻译 Overview</p>
            </div>
            <div>
              <label class="field-label"><CalendarClock :size="13" />Cron 表达式</label>
              <el-input v-model="config.overview_job.cron_expression" placeholder="0 5 * * *" />
              <p class="field-tip">标准 5 段 cron，默认每天 05:00</p>
            </div>
            <div>
              <label class="field-label">目标媒体库 ID（逗号分隔）</label>
              <el-input v-model="libraryIdsText" placeholder="1875208, 2134785" />
              <p class="field-tip">多个库用逗号分隔；留空表示默认范围</p>
            </div>
          </div>
        </div>
      </section>

      <!-- ==================== 请求预算 ==================== -->
      <section class="glass-card fade-up" style="--d: 160ms">
        <header class="card-head">
          <div class="icon-badge amber"><Timer :size="20" /></div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[15px] font-bold tracking-wide text-white">请求预算</h2>
            <p class="mt-1 text-xs leading-relaxed text-slate-500">进程级每 Provider 令牌桶，防止接口被限流</p>
          </div>
        </header>
        <div class="card-body">
          <div class="grid grid-cols-1 gap-x-7 gap-y-5 md:grid-cols-3">
            <div>
              <label class="field-label">豆瓣 / 系列</label>
              <el-input-number
                v-model="config.request_budget.douban_per_series"
                :min="0"
                :max="500"
                :step="5"
                controls-position="right"
              />
              <p class="field-tip">单个系列最多请求豆瓣次数</p>
            </div>
            <div>
              <label class="field-label">TMDB / 分钟</label>
              <el-input-number
                v-model="config.request_budget.tmdb_per_min"
                :min="0"
                :max="1000"
                :step="10"
                controls-position="right"
              />
              <p class="field-tip">每分钟 TMDB 请求上限</p>
            </div>
            <div>
              <label class="field-label">Emby 回写 / 系列</label>
              <el-input-number
                v-model="config.request_budget.emby_writeback_per_series"
                :min="0"
                :max="500"
                :step="5"
                controls-position="right"
              />
              <p class="field-tip">单个系列回写 Emby 请求上限</p>
            </div>
          </div>
        </div>
      </section>

      <!-- ==================== 演员简介 / 分集 ==================== -->
      <section class="glass-card fade-up" style="--d: 240ms">
        <header class="card-head">
          <div class="icon-badge pink"><Film :size="20" /></div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[15px] font-bold tracking-wide text-white">演员简介 / 分集</h2>
            <p class="mt-1 text-xs leading-relaxed text-slate-500">汉化时是否内联补简介、是否顺带翻译分集简介</p>
          </div>
        </header>
        <div class="card-body">
          <div class="grid grid-cols-1 gap-x-7 gap-y-5 md:grid-cols-2">
            <div>
              <label class="field-label"><Film :size="13" />汉化/审计内联补演员简介</label>
              <div class="switch-row">
                <el-switch v-model="config.actor_bio_inline_enabled" />
                <span class="switch-hint">{{ config.actor_bio_inline_enabled ? '已开启（慢）' : '已关闭（快，走独立修复）' }}</span>
              </div>
              <p class="field-tip">False（默认）= 汉化/审计只建身份，不逐演员触发 LLM 简介补全；演员库刷新/修复路径不受影响</p>
            </div>
            <div>
              <label class="field-label"><Languages :size="13" />汉化 Series 顺带翻译分集简介</label>
              <div class="switch-row">
                <el-switch v-model="config.sinicize_translate_episode_overviews" />
                <span class="switch-hint">{{ config.sinicize_translate_episode_overviews ? '已开启' : '已关闭' }}</span>
              </div>
              <p class="field-tip">汉化时对非中文分集简介调 LLM 翻译并写回 Emby + 落库，整部剧全中文</p>
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
.ambient { position: fixed; border-radius: 50%; pointer-events: none; z-index: 0; }
.ambient-a {
  width: 560px; height: 560px; top: -180px; right: -140px;
  background: radial-gradient(circle, rgba(6, 182, 212, 0.11), transparent 70%);
}
.ambient-b {
  width: 480px; height: 480px; bottom: -160px; left: -120px;
  background: radial-gradient(circle, rgba(236, 72, 153, 0.08), transparent 70%);
}
.grid-overlay {
  position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background-image:
    linear-gradient(rgba(148, 163, 184, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148, 163, 184, 0.045) 1px, transparent 1px);
  background-size: 46px 46px;
  -webkit-mask-image: radial-gradient(ellipse 85% 65% at 50% 0%, #000 30%, transparent 78%);
          mask-image: radial-gradient(ellipse 85% 65% at 50% 0%, #000 30%, transparent 78%);
}

/* ==================== 吸顶操作栏 ==================== */
.sticky-bar {
  position: sticky; top: 0; z-index: 50;
  display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
  padding: 16px 4px; margin-bottom: 10px;
  background: rgba(11, 17, 32, 0.62);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}
.page-title {
  font-size: 20px; font-weight: 800; letter-spacing: 0.3px; color: #fff;
  background: linear-gradient(90deg, #fff 20%, #f472b6 100%);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent;
}

/* ==================== 内容区 ==================== */
.content { @apply relative z-[1] grid grid-cols-1 gap-6; }

/* ==================== 玻璃卡片 ==================== */
.glass-card {
  @apply relative z-[1] flex h-full flex-col overflow-hidden rounded-[22px] border border-white/10;
  background: linear-gradient(160deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.02));
  backdrop-filter: blur(18px) saturate(140%);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  box-shadow: 0 24px 60px -24px rgba(0, 0, 0, 0.65), inset 0 1px 0 rgba(255, 255, 255, 0.06);
}
.glass-card::before {
  content: ''; position: absolute; top: 0; left: 12%; right: 12%; height: 1px; z-index: 0;
  background: linear-gradient(90deg, transparent, rgba(6, 182, 212, 0.5), rgba(236, 72, 153, 0.45), transparent);
}
.glass-card::after {
  content: ''; position: absolute; top: -90px; right: -70px; width: 260px; height: 260px; z-index: 0;
  pointer-events: none;
  background: radial-gradient(circle, rgba(236, 72, 153, 0.06), transparent 70%);
}

.card-head { @apply relative z-[1] flex flex-wrap items-center gap-3.5 border-b border-white/5 px-6 py-[18px]; }
.card-body { @apply relative z-[1] p-6; }

/* 图标徽章 */
.icon-badge {
  width: 40px; height: 40px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  border-radius: 12px;
  box-shadow: 0 0 18px rgba(6, 182, 212, 0.22), inset 0 0 10px rgba(6, 182, 212, 0.08);
}
.icon-badge.cyan {
  color: #22d3ee;
  background: linear-gradient(135deg, rgba(6, 182, 212, 0.18), rgba(6, 182, 212, 0.05));
  border: 1px solid rgba(6, 182, 212, 0.32);
}
.icon-badge.violet {
  color: #a78bfa;
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.18), rgba(139, 92, 246, 0.05));
  border: 1px solid rgba(139, 92, 246, 0.32);
  box-shadow: 0 0 18px rgba(139, 92, 246, 0.22), inset 0 0 10px rgba(139, 92, 246, 0.08);
}
.icon-badge.amber {
  color: #fbbf24;
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.18), rgba(245, 158, 11, 0.05));
  border: 1px solid rgba(245, 158, 11, 0.32);
  box-shadow: 0 0 18px rgba(245, 158, 11, 0.22), inset 0 0 10px rgba(245, 158, 11, 0.08);
}
.icon-badge.pink {
  color: #f472b6;
  background: linear-gradient(135deg, rgba(236, 72, 153, 0.18), rgba(236, 72, 153, 0.05));
  border: 1px solid rgba(236, 72, 153, 0.32);
  box-shadow: 0 0 18px rgba(236, 72, 153, 0.22), inset 0 0 10px rgba(236, 72, 153, 0.08);
}

/* ==================== 字段 ==================== */
.field-label {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 8px;
  font-size: 12.5px; font-weight: 600; color: #cbd5e1; letter-spacing: 0.2px;
}
.field-label svg { color: #64748b; }
.field-tip { margin-top: 6px; font-size: 12px; line-height: 1.5; color: #64748b; }
.switch-row { display: flex; align-items: center; gap: 10px; min-height: 32px; }
.switch-hint { font-size: 12.5px; color: #94a3b8; }

/* ==================== Element Plus Input / Number / Switch 深度定制 ==================== */
:deep(.el-input__wrapper), :deep(.el-input-number .el-input__wrapper), :deep(.el-select__wrapper) {
  @apply rounded-xl bg-black/20 transition-shadow duration-200;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.10);
}
:deep(.el-input__wrapper:hover), :deep(.el-input-number .el-input__wrapper:hover) { box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.28); }
:deep(.el-input__wrapper.is-focus), :deep(.el-input-number .el-input__wrapper.is-focus) {
  background: rgba(6, 182, 212, 0.06);
  box-shadow: 0 0 0 1px #22d3ee, 0 0 8px rgba(6, 182, 212, 0.5);
}
:deep(.el-input__inner) { color: #f1f5f9; }
:deep(.el-input__inner::placeholder) { color: #475569; }
:deep(.el-switch) {
  --el-switch-on-color: #3b82f6;
  --el-switch-off-color: rgba(148, 163, 184, 0.28);
  --el-switch-border-color: transparent;
}

/* ==================== 按钮 ==================== */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  padding: 10px 22px; border-radius: 12px; border: 1px solid transparent;
  font-size: 14px; font-weight: 600; font-family: inherit; color: #fff;
  cursor: pointer; white-space: nowrap;
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

/* ==================== 动画 ==================== */
.fade-up { opacity: 0; animation: fadeUp 0.6s cubic-bezier(0.22, 0.61, 0.36, 1) forwards; animation-delay: var(--d, 0ms); }
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ==================== 响应式 & 无障碍 ==================== */
@media (max-width: 768px) {
  .settings-root { padding: 10px 12px 32px; }
  .sticky-bar { padding: 12px 2px; }
  .page-title { font-size: 17px; }
  .btn { flex: 1; }
  .card-head { @apply px-4 py-3.5; }
  .card-body { @apply p-4; }
}
@media (prefers-reduced-motion: reduce) {
  .fade-up, .btn { animation: none !important; transition: none !important; }
  .fade-up { opacity: 1; }
}
</style>
