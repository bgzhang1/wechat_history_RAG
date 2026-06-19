<template>
  <div class="panel animate-fade-in">
    <!-- Controls -->
    <div class="logs-controls">
      <div class="control-group">
        <label class="form-label">日志级别</label>
        <select class="input" v-model="level" @change="fetchLogs" id="select-log-level">
          <option value="error">仅错误</option>
          <option value="warning">警告和错误</option>
          <option value="info">信息、警告和错误</option>
          <option value="debug">全部日志</option>
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
      <button class="btn btn-ghost btn-sm refresh-button" @click="fetchLogs" :disabled="loading" id="btn-refresh-logs">
        <div v-if="loading" class="spinner spinner-sm"></div>
        <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
        刷新
      </button>
    </div>

    <div v-if="loading && logs.length === 0" class="panel-loading"><div class="spinner"></div></div>

    <div v-if="logsError" class="error-state glass-card">
      <span class="text-sm">{{ logsError }}</span>
      <button class="btn btn-ghost btn-sm" @click="fetchLogs" :disabled="loading">
        重试
      </button>
    </div>

    <div v-else-if="!loading && logs.length === 0" class="empty-state logs-empty">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
      <span class="text-sm text-muted">当前级别下暂无日志</span>
    </div>

    <div v-if="logs.length > 0" class="logs-list">
      <div
        v-for="(log, idx) in logs"
        :key="idx"
        :class="['log-item', { expandable: log.traceback }]"
        @click="toggleExpand(idx, log)"
      >
        <div class="log-header">
          <span :class="['log-level', `level-${log.level}`]">{{ levelLabel(log.level) }}</span>
          <span class="log-time text-xs text-muted font-mono">{{ formatTime(log.timestamp) }}</span>
          <span class="log-module text-xs text-muted">{{ log.module }}.{{ log.function }}</span>
        </div>
        <div class="log-message text-sm">{{ log.message }}</div>

        <!-- Details -->
        <div v-if="log.details && Object.keys(log.details).length > 0" class="log-details text-xs font-mono">
          <span v-for="(v, k) in log.details" :key="k" class="detail-chip">
            {{ k }}: {{ formatDetailValue(v) }}
          </span>
        </div>

        <!-- Traceback (expandable) -->
        <div v-if="log.traceback && expandedIdx === idx" class="log-traceback font-mono text-xs">
          {{ log.traceback }}
        </div>
        <div v-if="log.traceback" class="log-expand text-xs text-muted">
          {{ expandedIdx === idx ? '▲ 收起' : '▼ 展开堆栈' }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, inject } from 'vue'
import { getLogs } from '../api/api.js'

const toast = inject('toast')
const loading = ref(true)
const logs = ref([])
const logsError = ref('')
const level = ref('error')
const limit = ref(100)
const expandedIdx = ref(-1)
let requestSeq = 0
let componentDisposed = false
let activeLogsController = null

onMounted(() => {
  componentDisposed = false
  fetchLogs()
})

onUnmounted(() => {
  componentDisposed = true
  activeLogsController?.abort()
  activeLogsController = null
})

async function fetchLogs() {
  const seq = ++requestSeq
  activeLogsController?.abort()
  const controller = new AbortController()
  activeLogsController = controller
  loading.value = true
  try {
    const data = await getLogs(level.value, limit.value, { signal: controller.signal })
    if (componentDisposed || seq !== requestSeq) return
    logs.value = data
    logsError.value = ''
    expandedIdx.value = -1
  } catch (e) {
    if (componentDisposed || seq !== requestSeq) return
    logsError.value = logs.value.length
      ? `日志加载失败，当前显示上次成功结果：${e.message}`
      : `日志加载失败：${e.message}`
    toast(e.message, 'error')
  } finally {
    if (activeLogsController === controller) activeLogsController = null
    if (!componentDisposed && seq === requestSeq) loading.value = false
  }
}

function toggleExpand(idx, log) {
  if (!log.traceback) return
  expandedIdx.value = expandedIdx.value === idx ? -1 : idx
}

function levelLabel(value) {
  const map = { error: '错误', warning: '警告', info: '信息', debug: '调试' }
  return map[value] || value
}

function formatTime(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function formatDetailValue(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  if (!text) return ''
  return text.length > 300 ? text.slice(0, 300) + '...' : text
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

.error-state {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
  padding: var(--space-4);
  color: var(--accent-red);
  border-color: rgba(239, 68, 68, 0.35);
}

.error-state .btn {
  flex-shrink: 0;
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

.refresh-button {
  align-self: flex-end;
}

.spinner-sm {
  width: 14px;
  height: 14px;
  border-width: 1.5px;
}

.logs-empty {
  padding: var(--space-8);
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
  transition: background var(--transition-fast);
}

.log-item.expandable {
  cursor: pointer;
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
  max-width: 100%;
  overflow-wrap: anywhere;
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

@media (max-width: 768px) {
  .panel {
    padding: var(--space-4);
  }

  .logs-controls {
    flex-direction: column;
    align-items: stretch;
  }

  .control-group .input {
    width: 100%;
  }

  .log-header {
    flex-wrap: wrap;
    gap: var(--space-2);
  }
}
</style>
