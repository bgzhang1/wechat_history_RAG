<template>
  <div class="panel animate-fade-in">
    <div v-if="loading && !hasDiagnosticData" class="panel-loading"><div class="spinner"></div></div>

    <div v-else-if="loadError && !hasDiagnosticData" class="error-state glass-card">
      <span class="text-sm">{{ loadError }}</span>
      <button class="btn btn-ghost btn-sm" @click="refresh" id="btn-retry-health">
        重试
      </button>
    </div>

    <template v-else>
      <div v-if="loadError" class="error-state stale-error glass-card">
        <span class="text-sm">{{ loadError }}</span>
        <button class="btn btn-ghost btn-sm" @click="refresh" :disabled="loading">
          重试
        </button>
      </div>

      <!-- Overall status -->
      <div class="health-header">
        <div :class="['overall-badge', `status-${health.status || diagnostics.overall}`]">
          <div class="status-dot"></div>
          <span class="status-text">{{ statusLabel(health.status || diagnostics.overall) }}</span>
        </div>
        <button class="btn btn-ghost btn-sm" @click="refresh" :disabled="loading" id="btn-refresh-health">
          <div v-if="loading" class="spinner spinner-sm"></div>
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
          刷新
        </button>
      </div>

      <!-- Quick info -->
      <div class="health-info-grid">
        <div class="info-item">
          <span class="info-label">对话模型</span>
          <span :class="['info-value', { 'text-muted': !health.chat_model_configured }]">
            {{ health.chat_model || '未配置' }}
            <span v-if="health.chat_model_configured" class="badge badge-ok config-badge">已配置</span>
            <span v-else class="badge badge-error config-badge">缺失</span>
          </span>
        </div>
        <div class="info-item">
          <span class="info-label">嵌入模型</span>
          <span :class="['info-value', { 'text-muted': !health.embedding_configured }]">
            {{ health.embedding_model || '未配置' }}
            <span v-if="health.embedding_configured" class="badge badge-ok config-badge">已配置</span>
            <span v-else class="badge badge-warning config-badge">缺失</span>
          </span>
        </div>
        <div class="info-item">
          <span class="info-label">向量搜索</span>
          <span class="info-value">
            <span v-if="health.vector_search_available" class="badge badge-ok">可用</span>
            <span v-else-if="health.has_data === false" class="badge badge-warning">暂无数据</span>
            <span v-else class="badge badge-warning">不可用</span>
          </span>
        </div>
        <div class="info-item">
          <span class="info-label">数据状态</span>
          <span class="info-value">
            {{ health.total_messages?.toLocaleString() || 0 }} 条消息 · {{ health.thread_count || 0 }} 会话
          </span>
        </div>
      </div>

      <!-- Diagnostics checks -->
      <div class="section">
        <h3 class="section-title">组件诊断</h3>
        <div class="checks-list">
          <div v-for="check in diagnostics.checks || []" :key="check.component" class="check-item glass-card">
            <div class="check-header">
              <span :class="['badge', `badge-${check.status}`]">{{ checkStatusLabel(check.status) }}</span>
              <span class="check-component">{{ componentLabel(check.component) }}</span>
            </div>
            <div class="check-detail text-sm text-secondary">{{ check.detail }}</div>
            <div v-if="check.action" class="check-action text-xs">
              <div class="check-action-text">
                <span class="action-label">建议：</span>{{ check.action }}
              </div>
              <button
                v-if="actionTarget(check)"
                class="btn btn-ghost btn-xs"
                type="button"
                @click="goToAction(check)"
              >
                {{ actionLabel(check) }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, inject } from 'vue'
import { useRouter } from 'vue-router'
import { healthDiagnostics } from '../api/api.js'

const toast = inject('toast')
const router = useRouter()
const loading = ref(true)
const health = ref({})
const diagnostics = ref({ checks: [] })
const loadError = ref('')
const hasDiagnosticData = computed(() => Boolean(health.value.status || diagnostics.value.overall))
let refreshSeq = 0
let componentDisposed = false
let activeRefreshController = null

onMounted(() => {
  componentDisposed = false
  refresh()
})

onUnmounted(() => {
  componentDisposed = true
  activeRefreshController?.abort()
  activeRefreshController = null
})

async function refresh() {
  const seq = ++refreshSeq
  activeRefreshController?.abort()
  const controller = new AbortController()
  activeRefreshController = controller
  loading.value = true
  try {
    const d = await healthDiagnostics({ signal: controller.signal })
    if (componentDisposed || seq !== refreshSeq) return
    diagnostics.value = d
    health.value = healthSummaryFromDiagnostics(d)
    loadError.value = ''
  } catch (e) {
    if (componentDisposed || seq !== refreshSeq) return
    loadError.value = `健康诊断加载失败：${e.message}`
    toast(e.message, 'error')
  } finally {
    if (activeRefreshController === controller) activeRefreshController = null
    if (!componentDisposed && seq === refreshSeq) loading.value = false
  }
}

function healthSummaryFromDiagnostics(data = {}) {
  const dbStats = data.db_stats || {}
  const chatStatus = data.chat_status || {}
  const summaryStatus = data.summary_status || {}
  const embedStatus = data.embed_status || {}
  const totalMessages = Number(dbStats.total_messages ?? 0)

  return {
    status: data.overall,
    chat_model_configured: Boolean(chatStatus.configured),
    chat_model: chatStatus.model || '',
    chat_model_missing: chatStatus.missing || [],
    summary_model_configured: Boolean(summaryStatus.configured),
    summary_model: summaryStatus.model || '',
    summary_model_missing: summaryStatus.missing || [],
    embedding_configured: Boolean(embedStatus.configured),
    embedding_model: embedStatus.model || '',
    embedding_missing: embedStatus.missing || [],
    vector_index_available: Boolean(data.vector_index_available),
    vector_search_available: Boolean(data.vector_search_available),
    total_messages: totalMessages,
    indexed_session_chunks: Number(dbStats.indexed_session_chunks ?? 0),
    thread_count: Number(dbStats.thread_count ?? 0),
    sender_count: Number(dbStats.sender_count ?? 0),
    has_data: totalMessages > 0,
    checks: data.checks || [],
  }
}

function statusLabel(s) {
  const map = { ok: '系统正常', degraded: '部分降级', error: '存在错误' }
  return map[s] || s
}

function checkStatusLabel(s) {
  const map = { ok: '正常', warning: '警告', error: '错误' }
  return map[s] || s
}

function componentLabel(c) {
  const map = {
    database: '数据库',
    chat_sessions: '会话存储',
    chat_model: '对话模型',
    embedding_model: '嵌入模型',
    vector_index: '向量索引',
  }
  return map[c] || c
}

function actionTarget(check) {
  if (Object.prototype.hasOwnProperty.call(check || {}, 'action_target')) {
    return check.action_target || null
  }
  const map = {
    database: 'ingest',
    vector_index: 'ingest',
    chat_model: 'settings',
    chat_sessions: 'logs',
  }
  return map[check?.component] || null
}

function actionLabel(check) {
  const target = actionTarget(check)
  if (target === 'ingest') return '打开导入'
  if (target === 'settings') return '打开配置'
  if (target === 'logs') return '查看日志'
  return '去处理'
}

function goToAction(check) {
  const tab = actionTarget(check)
  if (!tab) return
  router.replace({
    query: tab === 'settings' ? {} : { tab },
  })
}
</script>

<style scoped>
.panel {
  padding: var(--space-6);
  background: var(--surface-glass);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
}

.panel-loading {
  display: flex;
  justify-content: center;
  padding: var(--space-12);
}

.error-state {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  color: var(--accent-red);
  border-color: rgba(239, 68, 68, 0.35);
}

.stale-error {
  margin-bottom: var(--space-4);
}

.error-state .btn {
  flex-shrink: 0;
}

.spinner-sm {
  width: 14px;
  height: 14px;
  border-width: 1.5px;
}

.health-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-6);
}

.overall-badge {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-5);
  border-radius: var(--radius-lg);
  font-weight: 600;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.status-ok { background: rgba(16, 185, 129, 0.12); }
.status-ok .status-dot { background: var(--accent-green); box-shadow: 0 0 8px var(--accent-green); }
.status-ok .status-text { color: var(--accent-green); }

.status-degraded { background: rgba(245, 158, 11, 0.12); }
.status-degraded .status-dot { background: var(--accent-yellow); box-shadow: 0 0 8px var(--accent-yellow); }
.status-degraded .status-text { color: var(--accent-yellow); }

.status-error { background: rgba(239, 68, 68, 0.12); }
.status-error .status-dot { background: var(--accent-red); box-shadow: 0 0 8px var(--accent-red); animation: pulse-dot 1.5s ease-in-out infinite; }
.status-error .status-text { color: var(--accent-red); }

.health-info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
  margin-bottom: var(--space-6);
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-3) var(--space-4);
  background: var(--surface-glass);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
}

.info-label {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0;
}

.info-value {
  font-size: var(--text-sm);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.config-badge {
  margin-left: 4px;
}

.section {
  margin-bottom: var(--space-4);
}

.section-title {
  font-size: var(--text-lg);
  font-weight: 600;
  margin-bottom: var(--space-3);
}

.checks-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.check-item {
  padding: var(--space-4);
}

.check-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}

.check-component {
  font-weight: 600;
  font-size: var(--text-sm);
}

.check-detail {
  margin-bottom: var(--space-1);
}

.check-action {
  color: var(--accent-yellow);
  padding: var(--space-2) var(--space-3);
  background: rgba(245, 158, 11, 0.06);
  border-radius: var(--radius-sm);
  margin-top: var(--space-2);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.action-label {
  font-weight: 700;
}

.check-action-text {
  min-width: 0;
}

.btn-xs {
  padding: 2px var(--space-2);
  min-height: 24px;
  font-size: var(--text-xs);
  flex-shrink: 0;
}

@media (max-width: 768px) {
  .panel {
    padding: var(--space-4);
  }

  .health-header {
    align-items: flex-start;
    gap: var(--space-3);
  }

  .health-info-grid {
    grid-template-columns: 1fr;
  }

  .check-action {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
