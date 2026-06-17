<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <button class="btn btn-primary btn-new-chat" @click="$emit('new-chat')" id="btn-new-chat">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        新建对话
      </button>
      <button
        v-if="selected.size > 0"
        class="btn btn-danger btn-sm"
        @click="emitBatchDelete"
        id="btn-batch-delete"
      >
        删除 ({{ selected.size }})
      </button>
    </div>

    <div class="session-list" v-if="!loading">
      <div
        v-for="s in sessions"
        :key="s.session_id"
        :class="['session-item', { active: s.session_id === activeId }]"
        @click="$emit('select', s.session_id)"
      >
        <label class="session-checkbox" @click.stop>
          <input type="checkbox" :checked="selected.has(s.session_id)" @change="toggleSelect(s.session_id)" />
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
            ref="renameInput"
            autofocus
          />
          <div class="session-meta">
            <span class="session-count">{{ s.message_count || 0 }} 条消息</span>
            <span class="session-time">{{ formatTime(s.updated_at) }}</span>
          </div>
        </div>
        <div class="session-actions" @click.stop>
          <button class="btn-ghost btn-icon" @click="startRename(s)" title="重命名">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
          </button>
          <button class="btn-ghost btn-icon" @click="$emit('delete', s.session_id)" title="删除">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
        </div>
      </div>

      <div v-if="sessions.length === 0" class="empty-state" style="padding: 2rem;">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        <span class="text-sm">暂无对话记录</span>
      </div>
    </div>

    <div v-else class="sidebar-loading">
      <div class="spinner"></div>
    </div>
  </aside>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, default: null },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'new-chat', 'rename', 'delete', 'batch-delete'])

const selected = ref(new Set())
const editingId = ref(null)
const editTitle = ref('')

function toggleSelect(id) {
  const s = new Set(selected.value)
  if (s.has(id)) s.delete(id); else s.add(id)
  selected.value = s
}

function emitBatchDelete() {
  emit('batch-delete', Array.from(selected.value))
  selected.value = new Set()
}

function startRename(session) {
  editingId.value = session.session_id
  editTitle.value = session.title || session.last_question || ''
}

function confirmRename(sid) {
  if (editTitle.value.trim()) {
    emit('rename', { sessionId: sid, title: editTitle.value.trim() })
  }
  editingId.value = null
}

function cancelRename() {
  editingId.value = null
}

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
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
  width: var(--sidebar-width);
  height: 100%;
  display: flex;
  flex-direction: column;
  background: rgba(17, 24, 39, 0.6);
  backdrop-filter: blur(16px);
  border-right: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.sidebar-header {
  padding: var(--space-4);
  display: flex;
  gap: var(--space-2);
  border-bottom: 1px solid var(--border-subtle);
}

.btn-new-chat {
  flex: 1;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-2);
}

.session-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
  animation: fadeIn var(--transition-normal) ease-out;
  position: relative;
}

.session-item:hover {
  background: var(--surface-glass-hover);
}

.session-item.active {
  background: var(--surface-glass-active);
  border: 1px solid var(--border-default);
}

.session-checkbox input {
  width: 14px;
  height: 14px;
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
  font-weight: 500;
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
  gap: var(--space-2);
  margin-top: 2px;
}

.session-count, .session-time {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.session-actions {
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity var(--transition-fast);
  flex-shrink: 0;
}

.session-item:hover .session-actions {
  opacity: 1;
}

.session-actions .btn-ghost.btn-icon {
  padding: 4px;
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

.sidebar-loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
