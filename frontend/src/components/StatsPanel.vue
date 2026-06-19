<template>
  <div class="panel animate-fade-in">
    <div v-if="loading && !hasStatsData" class="panel-loading"><div class="spinner"></div></div>

    <div v-else-if="statsError && !hasStatsData" class="error-state glass-card">
      <span class="text-sm">{{ statsError }}</span>
      <button class="btn btn-ghost btn-sm" :disabled="refreshing" @click="refreshAll" id="btn-retry-stats">
        重试
      </button>
    </div>

    <template v-else>
      <div class="panel-toolbar">
        <button class="btn btn-ghost btn-sm" :disabled="refreshing" @click="refreshAll">
          <div v-if="refreshing" class="spinner spinner-sm"></div>
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
          刷新
        </button>
      </div>

      <div v-if="statsError" class="error-state glass-card">
        <span class="text-sm">{{ statsError }}</span>
      </div>

      <!-- Summary cards -->
      <div v-if="summaryLoaded" class="stats-cards">
        <div class="stat-card glass-card">
          <div class="stat-value">{{ formatCount(summary.total_messages) }}</div>
          <div class="stat-label">消息总数</div>
        </div>
        <div class="stat-card glass-card">
          <div class="stat-value">{{ formatCount(summary.thread_count) }}</div>
          <div class="stat-label">会话/群聊</div>
        </div>
        <div class="stat-card glass-card">
          <div class="stat-value">{{ formatCount(summary.sender_count) }}</div>
          <div class="stat-label">发送人</div>
        </div>
        <div class="stat-card glass-card">
          <div class="stat-value">{{ formatCount(summary.indexed_session_chunks) }}</div>
          <div class="stat-label">索引分块</div>
        </div>
      </div>

      <!-- Time span -->
      <div v-if="summaryLoaded && summary.time_span" class="time-span glass-card">
        <span class="text-muted text-sm">时间跨度：</span>
        <span class="text-sm">{{ formatDate(summary.time_span.earliest) }} — {{ formatDate(summary.time_span.latest) }}</span>
      </div>

      <!-- Message types -->
      <div v-if="summaryLoaded && summary.message_types?.length" class="section">
        <h3 class="section-title">消息类型分布</h3>
        <div class="type-chips">
          <div v-for="mt in summary.message_types" :key="mt.msg_type" class="type-chip">
            <span class="type-name">{{ mt.msg_type }}</span>
            <span class="type-count">{{ formatCount(mt.count) }}</span>
          </div>
        </div>
      </div>

      <!-- Threads table -->
      <div class="section">
        <div class="section-header">
          <h3 class="section-title">会话统计</h3>
          <div class="table-controls">
            <div v-if="threadsLoading" class="spinner spinner-sm"></div>
            <select class="input table-limit" v-model.number="threadsLimit" @change="loadThreads(0)" aria-label="会话统计每页条数">
              <option :value="20">20</option>
              <option :value="50">50</option>
              <option :value="100">100</option>
            </select>
          </div>
        </div>
        <div class="table-wrap">
          <table class="stats-table threads-table">
            <colgroup>
              <col class="col-name" />
              <col class="col-count" />
              <col class="col-date" />
              <col class="col-date" />
            </colgroup>
            <thead>
              <tr><th>会话名</th><th>消息数</th><th>最早</th><th>最晚</th></tr>
            </thead>
            <tbody>
              <tr v-for="t in threads" :key="t.thread">
                <td class="name-cell" :title="t.thread">{{ t.thread }}</td>
                <td class="count-cell">{{ formatCount(t.count) }}</td>
                <td class="date-cell text-muted text-xs">{{ formatDate(t.earliest) }}</td>
                <td class="date-cell text-muted text-xs">{{ formatDate(t.latest) }}</td>
              </tr>
              <tr v-if="threads.length === 0"><td colspan="4" class="text-muted empty-table-cell">暂无数据</td></tr>
            </tbody>
          </table>
        </div>
        <div class="pagination" v-if="threadsPage.total_count > threadsLimit">
          <button class="btn btn-ghost btn-sm" :disabled="threadsLoading || threadsOffset === 0" @click="loadThreads(threadsOffset - threadsLimit)">上一页</button>
          <span class="pagination-info">{{ pageStart(threadsOffset, threads.length, threadsPage.total_count) }}–{{ pageEnd(threadsOffset, threads.length, threadsPage.total_count) }} / {{ formatCount(threadsPage.total_count) }}</span>
          <button class="btn btn-ghost btn-sm" :disabled="threadsLoading || threadsOffset + threadsLimit >= threadsPage.total_count" @click="loadThreads(threadsOffset + threadsLimit)">下一页</button>
        </div>
      </div>

      <!-- Senders table -->
      <div class="section">
        <div class="section-header">
          <h3 class="section-title">发送人统计</h3>
          <div class="table-controls">
            <div v-if="sendersLoading" class="spinner spinner-sm"></div>
            <select class="input table-limit" v-model.number="sendersLimit" @change="loadSendersList(0)" aria-label="发送人统计每页条数">
              <option :value="20">20</option>
              <option :value="50">50</option>
              <option :value="100">100</option>
            </select>
          </div>
        </div>
        <div class="table-wrap">
          <table class="stats-table senders-table">
            <colgroup>
              <col class="col-name" />
              <col class="col-count" />
              <col class="col-self" />
            </colgroup>
            <thead>
              <tr><th>发送人</th><th>消息数</th><th>自己</th></tr>
            </thead>
            <tbody>
              <tr v-for="s in senders" :key="s.sender">
                <td class="name-cell" :title="s.sender">{{ s.sender }}</td>
                <td class="count-cell">{{ formatCount(s.count) }}</td>
                <td>
                  <span v-if="s.is_self" class="badge badge-ok">是</span>
                  <span v-else class="text-muted">—</span>
                </td>
              </tr>
              <tr v-if="senders.length === 0"><td colspan="3" class="text-muted empty-table-cell">暂无数据</td></tr>
            </tbody>
          </table>
        </div>
        <div class="pagination" v-if="sendersPage.total_count > sendersLimit">
          <button class="btn btn-ghost btn-sm" :disabled="sendersLoading || sendersOffset === 0" @click="loadSendersList(sendersOffset - sendersLimit)">上一页</button>
          <span class="pagination-info">{{ pageStart(sendersOffset, senders.length, sendersPage.total_count) }}–{{ pageEnd(sendersOffset, senders.length, sendersPage.total_count) }} / {{ formatCount(sendersPage.total_count) }}</span>
          <button class="btn btn-ghost btn-sm" :disabled="sendersLoading || sendersOffset + sendersLimit >= sendersPage.total_count" @click="loadSendersList(sendersOffset + sendersLimit)">下一页</button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, inject } from 'vue'
import { getStatsSummary, getThreads, getSenders } from '../api/api.js'

const toast = inject('toast')
const loading = ref(true)
const refreshing = ref(false)
const summary = ref({})
const summaryError = ref('')
const threadsError = ref('')
const sendersError = ref('')
const summaryLoaded = ref(false)
const threadsLoaded = ref(false)
const sendersLoaded = ref(false)

const threads = ref([])
const threadsPage = ref({ total_count: 0 })
const threadsOffset = ref(0)
const threadsLoading = ref(false)
const threadsLimit = ref(20)
const threadsAppliedLimit = ref(20)
let threadsRequestSeq = 0
let summaryRequestSeq = 0
let activeSummaryController = null
let activeThreadsController = null

const senders = ref([])
const sendersPage = ref({ total_count: 0 })
const sendersOffset = ref(0)
const sendersLoading = ref(false)
const sendersLimit = ref(20)
const sendersAppliedLimit = ref(20)
let sendersRequestSeq = 0
let activeSendersController = null
let componentDisposed = false

const statsError = computed(() => {
  const errors = [summaryError.value, threadsError.value, sendersError.value].filter(Boolean)
  return errors.length ? errors.join('；') : ''
})
const hasStatsData = computed(() => summaryLoaded.value || threadsLoaded.value || sendersLoaded.value)

onMounted(async () => {
  componentDisposed = false
  await loadAllStats()
  if (componentDisposed) return
  loading.value = false
})

onUnmounted(() => {
  componentDisposed = true
  activeSummaryController?.abort()
  activeSummaryController = null
  activeThreadsController?.abort()
  activeThreadsController = null
  activeSendersController?.abort()
  activeSendersController = null
})

async function loadAllStats({ preservePagination = false } = {}) {
  await Promise.allSettled([
    loadSummary(),
    loadThreads(preservePagination ? threadsOffset.value : 0),
    loadSendersList(preservePagination ? sendersOffset.value : 0),
  ])
}

async function refreshAll() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await loadAllStats({ preservePagination: true })
  } finally {
    if (!componentDisposed) refreshing.value = false
  }
}

async function loadSummary() {
  const seq = ++summaryRequestSeq
  activeSummaryController?.abort()
  const controller = new AbortController()
  activeSummaryController = controller
  try {
    const data = await getStatsSummary({ signal: controller.signal })
    if (componentDisposed || seq !== summaryRequestSeq) return
    summary.value = data
    summaryLoaded.value = true
    summaryError.value = ''
  } catch (e) {
    if (componentDisposed || seq !== summaryRequestSeq) return
    summaryError.value = `概览加载失败：${e.message}`
    toast(e.message, 'error')
  } finally {
    if (activeSummaryController === controller) activeSummaryController = null
  }
}

async function loadThreads(offset) {
  const safeOffset = Math.max(0, offset)
  const requestedLimit = safePageLimit(threadsLimit.value, threadsAppliedLimit.value)
  const seq = ++threadsRequestSeq
  activeThreadsController?.abort()
  const controller = new AbortController()
  activeThreadsController = controller
  threadsLoading.value = true
  try {
    const data = await getThreads(requestedLimit, safeOffset, { signal: controller.signal })
    if (componentDisposed || seq !== threadsRequestSeq) return
    const pageOffset = safePageOffset(data.offset, safeOffset)
    threads.value = data.items || []
    threadsPage.value = { total_count: data.total_count, returned: data.returned, offset: pageOffset }
    threadsOffset.value = pageOffset
    threadsLimit.value = requestedLimit
    threadsAppliedLimit.value = requestedLimit
    threadsLoaded.value = true
    threadsError.value = ''
  } catch (e) {
    if (!componentDisposed && seq === threadsRequestSeq) {
      if (threadsLoaded.value) threadsLimit.value = threadsAppliedLimit.value
      threadsError.value = `会话统计加载失败：${e.message}`
      toast(e.message, 'error')
    }
  } finally {
    if (activeThreadsController === controller) activeThreadsController = null
    if (!componentDisposed && seq === threadsRequestSeq) threadsLoading.value = false
  }
}

async function loadSendersList(offset) {
  const safeOffset = Math.max(0, offset)
  const requestedLimit = safePageLimit(sendersLimit.value, sendersAppliedLimit.value)
  const seq = ++sendersRequestSeq
  activeSendersController?.abort()
  const controller = new AbortController()
  activeSendersController = controller
  sendersLoading.value = true
  try {
    const data = await getSenders(requestedLimit, safeOffset, { signal: controller.signal })
    if (componentDisposed || seq !== sendersRequestSeq) return
    const pageOffset = safePageOffset(data.offset, safeOffset)
    senders.value = data.items || []
    sendersPage.value = { total_count: data.total_count, returned: data.returned, offset: pageOffset }
    sendersOffset.value = pageOffset
    sendersLimit.value = requestedLimit
    sendersAppliedLimit.value = requestedLimit
    sendersLoaded.value = true
    sendersError.value = ''
  } catch (e) {
    if (!componentDisposed && seq === sendersRequestSeq) {
      if (sendersLoaded.value) sendersLimit.value = sendersAppliedLimit.value
      sendersError.value = `发送人统计加载失败：${e.message}`
      toast(e.message, 'error')
    }
  } finally {
    if (activeSendersController === controller) activeSendersController = null
    if (!componentDisposed && seq === sendersRequestSeq) sendersLoading.value = false
  }
}

function formatCount(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number.toLocaleString() : '0'
}

function pageStart(offset, count, total) {
  if (!count || !total) return 0
  return Math.min(offset + 1, total)
}

function pageEnd(offset, count, total) {
  if (!count || !total) return 0
  return Math.min(offset + count, total)
}

function safePageOffset(value, fallback) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.max(0, number) : fallback
}

function safePageLimit(value, fallback) {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? Math.floor(number) : fallback
}

function formatDate(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
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

.panel-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: var(--space-4);
}

.error-state {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
  padding: var(--space-4);
  color: var(--accent-red);
  border-color: rgba(239, 68, 68, 0.35);
}

.error-state .btn {
  flex-shrink: 0;
}

.spinner-sm {
  width: 14px;
  height: 14px;
  border-width: 1.5px;
}

.stats-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-4);
  margin-bottom: var(--space-6);
}

.stat-card {
  padding: var(--space-5);
  text-align: center;
  transition: transform var(--transition-fast);
}

.stat-card:hover {
  transform: translateY(-2px);
}

.stat-value {
  font-size: var(--text-2xl);
  font-weight: 700;
  background: var(--gradient-brand);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.stat-label {
  font-size: var(--text-xs);
  color: var(--text-muted);
  margin-top: var(--space-1);
  text-transform: uppercase;
  letter-spacing: 0;
}

.time-span {
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-6);
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.section {
  margin-bottom: var(--space-6);
}

.section:last-child {
  margin-bottom: 0;
}

.section-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-primary);
}

.section > .section-title {
  margin-bottom: var(--space-3);
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.table-controls {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.table-limit {
  width: 84px;
  height: 32px;
  padding-top: var(--space-1);
  padding-bottom: var(--space-1);
  font-size: var(--text-xs);
}

.empty-table-cell {
  text-align: center;
}

.stats-table {
  table-layout: fixed;
}

.threads-table .col-name {
  width: 38%;
}

.threads-table .col-count {
  width: 18%;
}

.threads-table .col-date {
  width: 22%;
}

.senders-table .col-name {
  width: 56%;
}

.senders-table .col-count {
  width: 26%;
}

.senders-table .col-self {
  width: 18%;
}

.name-cell {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.count-cell,
.date-cell {
  white-space: nowrap;
}

.type-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.type-chip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: var(--surface-glass);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
}

.type-name {
  font-size: var(--text-sm);
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

.type-count {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--accent-blue);
}

@media (max-width: 768px) {
  .panel {
    padding: var(--space-4);
  }

  .stats-cards {
    grid-template-columns: repeat(2, 1fr);
  }

  .time-span {
    align-items: flex-start;
    flex-direction: column;
    gap: var(--space-1);
  }

  .section-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .name-cell {
    max-width: none;
  }

  .stats-table th,
  .stats-table td {
    padding: var(--space-2);
  }

  .threads-table .col-name {
    width: 34%;
  }

  .threads-table .col-count {
    width: 20%;
  }

  .threads-table .col-date {
    width: 23%;
  }
}

@media (max-width: 480px) {
  .stats-cards {
    grid-template-columns: 1fr;
  }
}
</style>
