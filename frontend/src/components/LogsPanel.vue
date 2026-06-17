<template>
  <div class="panel animate-fade-in">
    <!-- Controls -->
    <div class="logs-controls">
      <div class="control-group">
        <label class="form-label">日志级别</label>
        <select class="input" v-model="level" @change="fetchLogs" id="select-log-level">
          <option value="error">Error</option>
          <option value="info">Info + Error</option>
          <option value="debug">Debug + Info + Error</option>
        </select>
      </div>
      <div class="control-group">
        <label class="form-label">条数</label>
        <select class="input" v-model.number="limit" @change="fetchLogs" id="select-log-limit">
          <option :value="20">20</option>
          <option :value="50">50</option>
          <option :value="100">100</option>
          <option :value="500">500</option>
        </select>
      </div>
      <button class="btn btn-ghost btn-sm" @click="fetchLogs" id="btn-refresh-logs" style="align-self: flex-end;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
        刷新
      </button>
    </div>

    <div v-if="loading" class="panel-loading"><div class="spinner"></div></div>

    <div v-else-if="logs.length === 0" class="empty-state" style="padding: 2rem;">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
      <span class="text-sm text-muted">暂无日志</span>
    </div>

    <div v-else class="logs-list">
      <div v-for="(log, idx) in logs" :key="idx" class="log-item" @click="toggleExpand(idx)">
        <div class="log-header">
          <span :class="['log-level', `level-${log.level}`]">{{ log.level }}</span>
          <span class="log-time text-xs text-muted font-mono">{{ formatTime(log.timestamp) }}</span>
          <span class="log-module text-xs text-muted">{{ log.module }}.{{ log.function }}</span>
        </div>
        <div class="log-message text-sm">{{ log.message }}</div>

        <!-- Details -->
        <div v-if="log.details && Object.keys(log.details).length > 0" class="log-details text-xs font-mono">
          <span v-for="(v, k) in log.details" :key="k" class="detail-chip">
            {{ k }}: {{ v }}
          </span>
        </div>

        <!-- Traceback (expandable) -->
        <div v-if="log.traceback && expandedIdx === idx" class="log-traceback font-mono text-xs">
          {{ log.traceback }}
        </div>
        <div v-if="log.traceback" class="log-expand text-xs text-muted">
          {{ expandedIdx === idx ? '▲ 收起' : '▼ 展开 Traceback' }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, inject } from 'vue'
import { getLogs } from '../api/api.js'

const toast = inject('toast')
const loading = ref(true)
const logs = ref([])
const level = ref('error')
const limit = ref(100)
const expandedIdx = ref(-1)

onMounted(() => fetchLogs())

async function fetchLogs() {
  loading.value = true
  try {
    logs.value = await getLogs(level.value, limit.value)
  } catch (e) {
    toast(e.message, 'error')
    logs.value = []
  }
  loading.value = false
}

function toggleExpand(idx) {
  expandedIdx.value = expandedIdx.value === idx ? -1 : idx
}

function formatTime(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
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
  padding: var(--space-8);
}

.logs-controls {
  display: flex;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
  align-items: flex-end;
}

.control-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.control-group .input {
  width: 120px;
}

.logs-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-height: 600px;
  overflow-y: auto;
}

.log-item {
  padding: var(--space-3) var(--space-4);
  background: var(--surface-glass);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.log-item:hover {
  background: var(--surface-glass-hover);
}

.log-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-1);
}

.log-level {
  font-size: var(--text-xs);
  font-weight: 700;
  text-transform: uppercase;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
}

.level-error {
  background: rgba(239, 68, 68, 0.15);
  color: var(--accent-red);
}

.level-warning {
  background: rgba(245, 158, 11, 0.15);
  color: var(--accent-yellow);
}

.level-info {
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent-blue);
}

.level-debug {
  background: rgba(148, 163, 184, 0.15);
  color: var(--text-muted);
}

.log-message {
  color: var(--text-primary);
  margin-bottom: var(--space-1);
}

.log-details {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-1);
}

.detail-chip {
  padding: 1px 6px;
  background: rgba(255, 255, 255, 0.04);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
}

.log-traceback {
  margin-top: var(--space-2);
  padding: var(--space-3);
  background: rgba(0, 0, 0, 0.25);
  border-radius: var(--radius-sm);
  color: var(--accent-red);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
  animation: fadeIn var(--transition-fast) ease-out;
}

.log-expand {
  margin-top: var(--space-1);
  text-align: center;
}
</style>
