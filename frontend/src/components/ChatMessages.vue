<template>
  <div class="messages-area" ref="scrollContainer">
    <!-- Loading -->
    <div v-if="loading" class="messages-loading">
      <div class="spinner"></div>
      <span class="text-sm text-muted">加载消息中…</span>
    </div>

    <!-- Empty state -->
    <div v-else-if="messages.length === 0 && !streamingText && toolEvents.length === 0" class="welcome-state">
      <div class="welcome-icon">
        <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="url(#welcome-grad)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <defs><linearGradient id="welcome-grad" x1="0" y1="0" x2="24" y2="24"><stop offset="0%" stop-color="#3b82f6"/><stop offset="100%" stop-color="#8b5cf6"/></linearGradient></defs>
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
      </div>
      <h2 class="welcome-title">WeChat 聊天记录助手</h2>
      <p class="welcome-desc">输入你的问题，我会帮你检索微信聊天记录并回答。</p>
      <div class="welcome-suggestions">
        <button class="suggestion-chip" @click="$emit('quick-send', '最近一周我和谁聊得最多？')">最近一周我和谁聊得最多？</button>
        <button class="suggestion-chip" @click="$emit('quick-send', '搜索一下关于旅游的聊天')">搜索一下关于旅游的聊天</button>
        <button class="suggestion-chip" @click="$emit('quick-send', '统计一下各个群聊的消息数')">统计一下各个群聊的消息数</button>
      </div>
    </div>

    <!-- Messages -->
    <div v-else class="messages-list">
      <div
        v-for="(msg, idx) in messages"
        :key="idx"
        :class="['message', `message-${msg.role}`]"
      >
        <div class="message-avatar">
          <template v-if="msg.role === 'user'">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
          </template>
          <template v-else>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a4 4 0 0 1 4 4v1a3 3 0 0 1 3 3v1a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3v-1a3 3 0 0 1 3-3V6a4 4 0 0 1 4-4z"/><line x1="8" y1="18" x2="8" y2="22"/><line x1="16" y1="18" x2="16" y2="22"/><line x1="12" y1="18" x2="12" y2="22"/></svg>
          </template>
        </div>
        <div class="message-content">
          <div class="message-role">{{ msg.role === 'user' ? '你' : 'AI 助手' }}</div>
          <div v-if="msg.role === 'user'" class="message-text">{{ msg.content }}</div>
          <div v-else class="message-text markdown-body" v-html="renderMarkdown(msg.content)"></div>
        </div>
      </div>

      <!-- Tool events -->
      <div v-if="toolEvents.length > 0" class="tool-events">
        <div v-for="(evt, idx) in toolEvents" :key="idx" class="tool-event">
          <template v-if="evt.type === 'call'">
            <div class="tool-badge tool-badge-call">
              <div class="spinner" style="width:12px;height:12px;border-width:1.5px;"></div>
              调用工具: <strong>{{ evt.name }}</strong>
            </div>
            <div v-if="evt.args" class="tool-args font-mono text-xs">{{ evt.args }}</div>
          </template>
          <template v-else>
            <div class="tool-badge tool-badge-result">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              {{ evt.name }}: <span class="text-secondary">{{ evt.summary }}</span>
            </div>
          </template>
        </div>
      </div>

      <!-- Streaming text -->
      <div v-if="streamingText" class="message message-assistant streaming">
        <div class="message-avatar">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a4 4 0 0 1 4 4v1a3 3 0 0 1 3 3v1a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3v-1a3 3 0 0 1 3-3V6a4 4 0 0 1 4-4z"/><line x1="8" y1="18" x2="8" y2="22"/><line x1="16" y1="18" x2="16" y2="22"/><line x1="12" y1="18" x2="12" y2="22"/></svg>
        </div>
        <div class="message-content">
          <div class="message-role">AI 助手</div>
          <div class="message-text markdown-body" v-html="renderMarkdown(streamingText)"></div>
          <span class="typing-cursor"></span>
        </div>
      </div>

      <!-- Generating indicator -->
      <div v-if="isGenerating && !streamingText && toolEvents.length === 0" class="generating-indicator">
        <div class="typing-dots">
          <span></span><span></span><span></span>
        </div>
        <span class="text-sm text-muted">思考中…</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { watch, ref, nextTick } from 'vue'
import { marked } from 'marked'

marked.setOptions({ breaks: true, gfm: true })

const props = defineProps({
  messages: { type: Array, default: () => [] },
  streamingText: { type: String, default: '' },
  toolEvents: { type: Array, default: () => [] },
  isGenerating: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})

defineEmits(['quick-send'])

const scrollContainer = ref(null)

function renderMarkdown(text) {
  if (!text) return ''
  return marked.parse(text)
}

function scrollToBottom() {
  nextTick(() => {
    const el = scrollContainer.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

watch(() => props.messages.length, scrollToBottom)
watch(() => props.streamingText, scrollToBottom)
watch(() => props.toolEvents.length, scrollToBottom)
</script>

<style scoped>
.messages-area {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-6);
}

.messages-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  height: 100%;
}

/* Welcome */
.welcome-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: var(--space-4);
  animation: fadeIn 0.6s ease-out;
}

.welcome-icon {
  width: 80px;
  height: 80px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--surface-glass);
  border-radius: var(--radius-xl);
  border: 1px solid var(--border-subtle);
}

.welcome-title {
  font-size: var(--text-2xl);
  font-weight: 700;
  background: var(--gradient-brand);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.welcome-desc {
  color: var(--text-secondary);
  font-size: var(--text-base);
  max-width: 400px;
  text-align: center;
}

.welcome-suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  justify-content: center;
  margin-top: var(--space-4);
  max-width: 560px;
}

.suggestion-chip {
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-full);
  background: var(--surface-glass);
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: var(--font-sans);
}

.suggestion-chip:hover {
  background: var(--surface-glass-hover);
  color: var(--text-primary);
  border-color: rgba(59, 130, 246, 0.3);
  transform: translateY(-1px);
}

/* Messages */
.messages-list {
  max-width: 800px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.message {
  display: flex;
  gap: var(--space-3);
  animation: fadeIn var(--transition-normal) ease-out;
}

.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.message-user .message-avatar {
  background: var(--surface-glass-active);
  color: var(--accent-blue);
}

.message-assistant .message-avatar {
  background: linear-gradient(135deg, rgba(59,130,246,0.15), rgba(139,92,246,0.15));
  color: var(--accent-purple);
}

.message-content {
  flex: 1;
  min-width: 0;
}

.message-role {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-muted);
  margin-bottom: var(--space-1);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.message-text {
  font-size: var(--text-base);
  line-height: 1.7;
  color: var(--text-primary);
}

.message-user .message-text {
  background: var(--surface-glass);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  padding: var(--space-3) var(--space-4);
}

/* Tool events */
.tool-events {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-left: calc(36px + var(--space-3));
}

.tool-event {
  animation: fadeIn var(--transition-fast) ease-out;
}

.tool-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-full);
}

.tool-badge-call {
  background: rgba(59, 130, 246, 0.1);
  color: var(--accent-blue);
  border: 1px solid rgba(59, 130, 246, 0.15);
}

.tool-badge-result {
  background: rgba(16, 185, 129, 0.1);
  color: var(--accent-green);
  border: 1px solid rgba(16, 185, 129, 0.15);
}

.tool-args {
  margin-top: var(--space-1);
  padding: var(--space-2) var(--space-3);
  background: rgba(0,0,0,0.2);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  max-width: 500px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

/* Streaming */
.streaming .message-text {
  position: relative;
}

.typing-cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: var(--accent-blue);
  margin-left: 2px;
  animation: typing 1s ease-in-out infinite;
  vertical-align: text-bottom;
}

/* Generating indicator */
.generating-indicator {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding-left: calc(36px + var(--space-3));
  animation: fadeIn var(--transition-normal) ease-out;
}

.typing-dots {
  display: flex;
  gap: 4px;
}

.typing-dots span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent-blue);
  animation: typing 1.4s ease-in-out infinite;
}

.typing-dots span:nth-child(2) { animation-delay: 0.2s; }
.typing-dots span:nth-child(3) { animation-delay: 0.4s; }
</style>
