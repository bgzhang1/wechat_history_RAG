<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <button class="btn btn-primary btn-new-chat" @click="$emit('new-chat')" :disabled="locked" id="btn-new-chat">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        新建对话
      </button>
      <button
        v-if="selected.size > 0"
        class="btn btn-danger btn-sm"
        @click="emitBatchDelete"
        :disabled="locked"
        id="btn-batch-delete"
      >
        删除 ({{ selected.size }})
      </button>
    </div>

    <div class="session-list" v-if="!loading">
      <div
        v-for="s in sessions"
        :key="s.session_id"
        :class="['session-item', { active: s.session_id === activeId, locked }]"
        @click="selectSession(s.session_id)"
      >
        <label class="session-checkbox" @click.stop>
          <input
            type="checkbox"
            :checked="selected.has(s.session_id)"
            :disabled="locked || isBusy(s)"
            @change="toggleSelect(s.session_id)"
          />
        </label>
        <div class="session-info">
          <div class="session-title" v-if="editingId !== s.session_id">
            {{ s.title || s.last_question || '新对话' }}
          </div>
          <input
            v-else
            class="session-rename-input input"
            v-model="editTitle"
            @keydown.enter="confirmRename(s.session_id)"
            @keydown.escape="cancelRename"
            @blur="confirmRename(s.session_id)"
            :ref="setRenameInput"
          />
          <div class="session-meta">
            <span class="session-count">{{ s.message_count || 0 }} 条消息</span>
            <span class="session-time">{{ formatTime(s.updated_at) }}</span>
            <span
              v-if="sessionStatusLabel(s)"
              :class="['session-status', sessionStatusClass(s)]"
              :title="sessionStatusTitle(s)"
            >
              {{ sessionStatusLabel(s) }}
            </span>
          </div>
        </div>
        <div class="session-actions" @click.stop>
          <button class="btn-ghost btn-icon" @click="startRename(s)" :disabled="locked || isBusy(s)" title="重命名">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
          </button>
          <button class="btn-ghost btn-icon" @click="$emit('delete', s.session_id)" :disabled="locked || isBusy(s)" title="删除">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
        </div>
      </div>

      <div v-if="sessions.length === 0" class="empty-state session-empty">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        <span class="text-sm">暂无对话记录</span>
      </div>

      <button
        v-if="hasMore"
        class="btn btn-ghost btn-sm load-more"
        :disabled="locked || loadingMore"
        @click="$emit('load-more')"
      >
        {{ loadingMore ? '加载中…' : `加载更多（${remainingCount}）` }}
      </button>
    </div>

    <div v-else class="sidebar-loading">
      <div class="spinner"></div>
    </div>
  </aside>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, default: null },
  loading: { type: Boolean, default: false },
  loadingMore: { type: Boolean, default: false },
  totalCount: { type: Number, default: 0 },
  locked: { type: Boolean, default: false },
  selectionClearToken: { type: Number, default: 0 },
})

const emit = defineEmits(['select', 'new-chat', 'rename', 'delete', 'batch-delete', 'load-more'])

const selected = ref(new Set())
const editingId = ref(null)
const editTitle = ref('')
const renameInput = ref(null)

const remainingCount = computed(() => Math.max(0, props.totalCount - props.sessions.length))
const hasMore = computed(() => remainingCount.value > 0)

watch(
  () => props.sessions,
  (sessions) => {
    const liveIds = new Set(sessions.map(session => session.session_id))
    const busyIds = new Set(sessions.filter(isBusy).map(session => session.session_id))
    const nextSelected = new Set([...selected.value].filter(id => liveIds.has(id) && !busyIds.has(id)))
    if (nextSelected.size !== selected.value.size) selected.value = nextSelected
    if (editingId.value && !liveIds.has(editingId.value)) cancelRename()
  },
  { deep: true },
)

watch(() => props.selectionClearToken, () => {
  if (selected.value.size) selected.value = new Set()
})

watch(() => props.locked, (locked) => {
  if (locked) cancelRename()
})

function toggleSelect(id) {
  if (props.locked) return
  const session = props.sessions.find(item => item.session_id === id)
  if (!session || isBusy(session)) return
  const s = new Set(selected.value)
  if (s.has(id)) s.delete(id); else s.add(id)
  selected.value = s
}

function emitBatchDelete() {
  if (props.locked) return
  emit('batch-delete', Array.from(selected.value))
}

function startRename(session) {
  if (props.locked || isBusy(session)) return
  editingId.value = session.session_id
  editTitle.value = session.title || session.last_question || ''
  nextTick(() => {
    renameInput.value?.focus()
    renameInput.value?.select()
  })
}

function setRenameInput(el) {
  renameInput.value = el
}

function confirmRename(sid) {
  if (editingId.value !== sid) return
  if (props.locked) {
    cancelRename()
    return
  }
  if (editTitle.value.trim()) {
    emit('rename', { sessionId: sid, title: editTitle.value.trim() })
  }
  editingId.value = null
  editTitle.value = ''
}

function cancelRename() {
  editingId.value = null
  editTitle.value = ''
}

function selectSession(id) {
  if (props.locked) return
  emit('select', id)
}

function isBusy(session) {
  return session.status === 'running' || session.status === 'aborting'
}

function sessionStatusLabel(session) {
  if (session.status === 'running') return '生成中'
  if (session.status === 'aborting') return '停止中'
  if (session.status === 'error') return '失败'
  return ''
}

function sessionStatusClass(session) {
  if (session.status === 'error') return 'session-status-error'
  if (session.status === 'aborting') return 'session-status-warning'
  if (session.status === 'running') return 'session-status-running'
  return ''
}

function sessionStatusTitle(session) {
  if (session.status !== 'error') return sessionStatusLabel(session)
  const error = shortErrorText(session.last_error)
  return error ? `失败：${error}` : '失败'
}

function shortErrorText(value) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.length > 160 ? `${text.slice(0, 159).trimEnd()}…` : text
}

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const diff = now - d
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}
</script>

<style scoped>
.sidebar {
  width: 312px;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: color-mix(in srgb, var(--bg-secondary) 86%, transparent);
  backdrop-filter: blur(20px);
  border-right: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.sidebar-header {
  padding: var(--space-4);
  display: flex;
  gap: var(--space-2);
  border-bottom: 1px solid var(--border-subtle);
  background: color-mix(in srgb, var(--bg-secondary) 72%, transparent);
}

.btn-new-chat {
  flex: 1;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-3);
}

.session-empty {
  padding: var(--space-8);
}

.session-item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
  animation: fadeIn var(--transition-normal) ease-out;
  position: relative;
  border: 1px solid transparent;
}

.session-item::before {
  content: '';
  position: absolute;
  left: 0;
  top: var(--space-3);
  bottom: var(--space-3);
  width: 3px;
  border-radius: var(--radius-full);
  background: transparent;
}

.session-checkbox {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  padding-top: 2px;
}

.session-item:hover {
  background: color-mix(in srgb, var(--bg-elevated) 66%, transparent);
  border-color: var(--border-subtle);
}

.session-item.active {
  background: color-mix(in srgb, var(--accent-blue) 9%, var(--bg-elevated));
  border-color: color-mix(in srgb, var(--accent-blue) 20%, var(--border-default));
  box-shadow: var(--shadow-sm);
}

.session-item.active::before {
  background: var(--gradient-brand);
}

.session-checkbox input {
  width: 16px;
  height: 16px;
  cursor: pointer;
  accent-color: var(--accent-blue);
  flex-shrink: 0;
}

.session-info {
  flex: 1;
  min-width: 0;
}

.session-title {
  font-size: var(--text-sm);
  font-weight: 610;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-rename-input {
  font-size: var(--text-sm);
  padding: 2px var(--space-2);
  height: 24px;
}

.session-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: 4px;
  min-width: 0;
}

.session-count, .session-time, .session-status {
  font-size: var(--text-xs);
  color: var(--text-muted);
  white-space: nowrap;
}

.session-status {
  color: var(--accent-yellow);
}

.session-status-running {
  color: var(--accent-blue);
}

.session-status-warning {
  color: var(--accent-yellow);
}

.session-status-error {
  color: var(--accent-red);
}

.session-actions {
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity var(--transition-fast);
  flex-shrink: 0;
}

.session-item:hover .session-actions,
.session-item.active .session-actions {
  opacity: 1;
}

.session-actions .btn-ghost.btn-icon {
  width: 32px;
  height: 32px;
  padding: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--transition-fast);
}

.session-actions .btn-ghost.btn-icon:hover {
  color: var(--text-primary);
  background: var(--surface-glass-hover);
}

.session-item.locked {
  cursor: not-allowed;
}

.session-actions .btn-ghost.btn-icon:disabled,
.session-checkbox input:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.sidebar-loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.load-more {
  width: 100%;
  margin-top: var(--space-3);
  justify-content: center;
}

@media (max-width: 768px) {
  .sidebar {
    width: 100%;
    height: 188px;
    border-right: none;
    border-bottom: 1px solid var(--border-subtle);
  }

  .sidebar-header {
    padding: var(--space-3);
  }

  .session-list {
    display: flex;
    gap: var(--space-2);
    overflow-x: auto;
    overflow-y: hidden;
    padding: var(--space-2) var(--space-3);
  }

  .session-item {
    flex: 0 0 min(280px, calc(100vw - var(--space-6)));
    min-width: 0;
    align-items: flex-start;
  }

  .session-title {
    white-space: normal;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  .session-actions {
    opacity: 1;
  }

  .load-more {
    width: auto;
    min-width: 160px;
    margin-top: 0;
    flex: 0 0 auto;
  }
}
</style>
