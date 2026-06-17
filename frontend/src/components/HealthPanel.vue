<template>
  <div class="panel animate-fade-in">
    <div v-if="loading" class="panel-loading"><div class="spinner"></div></div>

    <template v-else>
      <!-- Overall status -->
      <div class="health-header">
        <div :class="['overall-badge', `status-${health.status || diagnostics.overall}`]">
          <div class="status-dot"></div>
          <span class="status-text">{{ statusLabel(health.status || diagnostics.overall) }}</span>
        </div>
        <button class="btn btn-ghost btn-sm" @click="refresh" id="btn-refresh-health">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
          刷新
        </button>
      </div>

      <!-- Quick info -->
      <div class="health-info-grid">
        <div class="info-item">
          <span class="info-label">对话模型</span>
          <span :class="['info-value', { 'text-muted': !health.chat_model_configured }]">
            {{ health.chat_model || '未配置' }}
            <span v-if="health.chat_model_configured" class="badge badge-ok" style="margin-left: 4px;">已配置</span>
            <span v-else class="badge badge-error" style="margin-left: 4px;">缺失</span>
          </span>
        </div>
        <div class="info-item">
          <span class="info-label">嵌入模型</span>
          <span :class="['info-value', { 'text-muted': !health.embedding_configured }]">
            {{ health.embedding_model || '未配置' }}
            <span v-if="health.embedding_configured" class="badge badge-ok" style="margin-left: 4px;">已配置</span>
            <span v-else class="badge badge-warning" style="margin-left: 4px;">缺失</span>
          </span>
        </div>
        <div class="info-item">
          <span class="info-label">向量搜索</span>
          <span class="info-value">
            <span v-if="health.vector_search_available" class="badge badge-ok">可用</span>
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
              <span :class="['badge', `badge-${check.status}`]">{{ check.status }}</span>
              <span class="check-component">{{ componentLabel(check.component) }}</span>
            </div>
            <div class="check-detail text-sm text-secondary">{{ check.detail }}</div>
            <div v-if="check.action" class="check-action text-xs">
              💡 {{ check.action }}
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted, inject } from 'vue'
import { healthCheck, healthDiagnostics } from '../api/api.js'

const toast = inject('toast')
const loading = ref(true)
const health = ref({})
const diagnostics = ref({ checks: [] })

onMounted(() => refresh())

async function refresh() {
  loading.value = true
  try {
    const [h, d] = await Promise.all([healthCheck(), healthDiagnostics()])
    health.value = h
    diagnostics.value = d
  } catch (e) {
    toast(e.message, 'error')
  }
  loading.value = false
}

function statusLabel(s) {
  const map = { ok: '系统正常', degraded: '部分降级', error: '存在错误' }
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
  letter-spacing: 0.05em;
}

.info-value {
  font-size: var(--text-sm);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
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
}
</style>
