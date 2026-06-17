<template>
  <div class="panel animate-fade-in">
    <div v-if="loading" class="panel-loading"><div class="spinner"></div></div>

    <template v-else>
      <!-- Summary cards -->
      <div class="stats-cards">
        <div class="stat-card glass-card">
          <div class="stat-value">{{ summary.total_messages?.toLocaleString() || 0 }}</div>
          <div class="stat-label">消息总数</div>
        </div>
        <div class="stat-card glass-card">
          <div class="stat-value">{{ summary.thread_count || 0 }}</div>
          <div class="stat-label">会话/群聊</div>
        </div>
        <div class="stat-card glass-card">
          <div class="stat-value">{{ summary.sender_count || 0 }}</div>
          <div class="stat-label">发送人</div>
        </div>
        <div class="stat-card glass-card">
          <div class="stat-value">{{ summary.indexed_session_chunks || 0 }}</div>
          <div class="stat-label">索引分块</div>
        </div>
      </div>

      <!-- Time span -->
      <div v-if="summary.time_span" class="time-span glass-card">
        <span class="text-muted text-sm">时间跨度：</span>
        <span class="text-sm">{{ formatDate(summary.time_span.earliest) }} — {{ formatDate(summary.time_span.latest) }}</span>
      </div>

      <!-- Message types -->
      <div v-if="summary.message_types?.length" class="section">
        <h3 class="section-title">消息类型分布</h3>
        <div class="type-chips">
          <div v-for="mt in summary.message_types" :key="mt.msg_type" class="type-chip">
            <span class="type-name">{{ mt.msg_type }}</span>
            <span class="type-count">{{ mt.count?.toLocaleString() }}</span>
          </div>
        </div>
      </div>

      <!-- Threads table -->
      <div class="section">
        <h3 class="section-title">会话统计</h3>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>会话名</th><th>消息数</th><th>最早</th><th>最晚</th></tr>
            </thead>
            <tbody>
              <tr v-for="t in threads" :key="t.thread">
                <td>{{ t.thread }}</td>
                <td>{{ t.count?.toLocaleString() }}</td>
                <td class="text-muted text-xs">{{ formatDate(t.earliest) }}</td>
                <td class="text-muted text-xs">{{ formatDate(t.latest) }}</td>
              </tr>
              <tr v-if="threads.length === 0"><td colspan="4" class="text-muted" style="text-align:center;">暂无数据</td></tr>
            </tbody>
          </table>
        </div>
        <div class="pagination" v-if="threadsPage.total_count > threadsLimit">
          <button class="btn btn-ghost btn-sm" :disabled="threadsOffset === 0" @click="loadThreads(threadsOffset - threadsLimit)">上一页</button>
          <span class="pagination-info">{{ threadsOffset + 1 }}–{{ Math.min(threadsOffset + threadsLimit, threadsPage.total_count) }} / {{ threadsPage.total_count }}</span>
          <button class="btn btn-ghost btn-sm" :disabled="threadsOffset + threadsLimit >= threadsPage.total_count" @click="loadThreads(threadsOffset + threadsLimit)">下一页</button>
        </div>
      </div>

      <!-- Senders table -->
      <div class="section">
        <h3 class="section-title">发送人统计</h3>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>发送人</th><th>消息数</th><th>自己</th></tr>
            </thead>
            <tbody>
              <tr v-for="s in senders" :key="s.sender">
                <td>{{ s.sender }}</td>
                <td>{{ s.count?.toLocaleString() }}</td>
                <td>
                  <span v-if="s.is_self" class="badge badge-ok">是</span>
                  <span v-else class="text-muted">—</span>
                </td>
              </tr>
              <tr v-if="senders.length === 0"><td colspan="3" class="text-muted" style="text-align:center;">暂无数据</td></tr>
            </tbody>
          </table>
        </div>
        <div class="pagination" v-if="sendersPage.total_count > sendersLimit">
          <button class="btn btn-ghost btn-sm" :disabled="sendersOffset === 0" @click="loadSendersList(sendersOffset - sendersLimit)">上一页</button>
          <span class="pagination-info">{{ sendersOffset + 1 }}–{{ Math.min(sendersOffset + sendersLimit, sendersPage.total_count) }} / {{ sendersPage.total_count }}</span>
          <button class="btn btn-ghost btn-sm" :disabled="sendersOffset + sendersLimit >= sendersPage.total_count" @click="loadSendersList(sendersOffset + sendersLimit)">下一页</button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted, inject } from 'vue'
import { getStatsSummary, getThreads, getSenders } from '../api/api.js'

const toast = inject('toast')
const loading = ref(true)
const summary = ref({})

const threads = ref([])
const threadsPage = ref({ total_count: 0 })
const threadsOffset = ref(0)
const threadsLimit = 20

const senders = ref([])
const sendersPage = ref({ total_count: 0 })
const sendersOffset = ref(0)
const sendersLimit = 20

onMounted(async () => {
  try {
    const [s] = await Promise.all([
      getStatsSummary(),
      loadThreads(0),
      loadSendersList(0),
    ])
    summary.value = s
  } catch (e) {
    toast(e.message, 'error')
  }
  loading.value = false
})

async function loadThreads(offset) {
  try {
    const data = await getThreads(threadsLimit, offset)
    threads.value = data.items || []
    threadsPage.value = { total_count: data.total_count, returned: data.returned }
    threadsOffset.value = offset
  } catch (e) { toast(e.message, 'error') }
}

async function loadSendersList(offset) {
  try {
    const data = await getSenders(sendersLimit, offset)
    senders.value = data.items || []
    sendersPage.value = { total_count: data.total_count, returned: data.returned }
    sendersOffset.value = offset
  } catch (e) { toast(e.message, 'error') }
}

function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
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
  letter-spacing: 0.05em;
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
  margin-bottom: var(--space-3);
  color: var(--text-primary);
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
  .stats-cards {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
