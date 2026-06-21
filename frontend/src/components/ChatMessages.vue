<template>
  <div class="messages-area" ref="scrollContainer" @scroll="handleScroll">
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
      <button
        v-if="hasOlder"
        class="btn btn-ghost btn-sm load-older"
        :disabled="loadingOlder || isGenerating"
        @click="requestOlderMessages"
      >
        {{ loadingOlder ? '加载中…' : '加载更早消息' }}
      </button>

      <div
        v-for="(msg, idx) in messages"
        :key="msg.id || `${msg.created_at || ''}-${idx}`"
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
          <template v-else>
            <details v-if="hasThinking(msg.reasoning_content)" class="thinking-panel">
              <summary>
                <span class="thinking-dot"></span>
                <span>思考过程</span>
              </summary>
              <div class="thinking-content markdown-body" v-html="renderMarkdown(msg.reasoning_content)"></div>
            </details>
            <div class="message-text markdown-body" v-html="renderMarkdown(msg.content)"></div>
          </template>
        </div>
      </div>

      <!-- Tool events -->
      <div v-if="toolEvents.length > 0" class="tool-events">
        <div v-for="(evt, idx) in toolEvents" :key="idx" class="tool-event">
          <template v-if="evt.type === 'call'">
            <div class="tool-badge tool-badge-call">
              <div class="spinner spinner-tool"></div>
              <span>调用工具:</span>
              <strong class="tool-name">{{ evt.name }}</strong>
            </div>
            <details v-if="hasLongToolText(evt.args)" class="tool-detail">
              <summary>
                <span class="tool-detail-label">参数</span>
                <code>{{ previewToolText(evt.args) }}</code>
              </summary>
              <pre class="tool-args font-mono text-xs">{{ formatToolText(evt.args) }}</pre>
            </details>
            <div v-else-if="evt.args" class="tool-args font-mono text-xs">{{ formatToolText(evt.args) }}</div>
          </template>
          <template v-else>
            <div :class="['tool-badge', isToolError(evt) ? 'tool-badge-error' : 'tool-badge-result']">
              <svg v-if="isToolError(evt)" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="7" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
              <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              <strong class="tool-name">{{ evt.name }}</strong>
              <span>:</span>
              <span class="tool-summary text-secondary">{{ previewToolText(evt.summary, 120) }}</span>
            </div>
            <details v-if="hasLongToolText(evt.summary, 120)" class="tool-detail">
              <summary>
                <span class="tool-detail-label">结果</span>
                <code>{{ previewToolText(evt.summary, 120) }}</code>
              </summary>
              <pre class="tool-args font-mono text-xs">{{ formatToolText(evt.summary) }}</pre>
            </details>
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
          <details v-if="hasThinking(streamingThinking)" class="thinking-panel">
            <summary>
              <span class="thinking-dot thinking-active"></span>
              <span>思考过程</span>
            </summary>
            <div class="thinking-content markdown-body" v-html="renderMarkdown(streamingThinking)"></div>
          </details>
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
import DOMPurify from 'dompurify'

marked.setOptions({ breaks: true, gfm: true })

const MARKDOWN_SANITIZE_CONFIG = {
  ALLOWED_TAGS: [
    'p', 'br', 'strong', 'b', 'em', 'i', 's', 'del',
    'code', 'pre', 'blockquote', 'ul', 'ol', 'li',
    'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr',
  ],
  ALLOWED_ATTR: ['href', 'title', 'target', 'rel'],
  ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|#|\/(?!\/)|\.{1,2}\/)/i,
  FORBID_TAGS: [
    'img', 'svg', 'math', 'iframe', 'object', 'embed',
    'video', 'audio', 'source', 'picture', 'style',
  ],
}

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName !== 'A') return
  const href = node.getAttribute('href') || ''
  if (!/^https?:\/\//i.test(href)) return
  node.setAttribute('target', '_blank')
  node.setAttribute('rel', 'noopener noreferrer')
})

const props = defineProps({
  messages: { type: Array, default: () => [] },
  streamingText: { type: String, default: '' },
  streamingThinking: { type: String, default: '' },
  toolEvents: { type: Array, default: () => [] },
  isGenerating: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  loadingOlder: { type: Boolean, default: false },
  hasOlder: { type: Boolean, default: false },
})

const emit = defineEmits(['quick-send', 'load-older'])

const scrollContainer = ref(null)
const shouldStickToBottom = ref(true)
const pendingPrependAnchor = ref(null)
const STICKY_SCROLL_THRESHOLD = 96
const TOOL_PREVIEW_LIMIT = 140
const TOOL_DETAIL_LIMIT = 1600
const TOOL_ERROR_PREFIXES = ['错误：', '工具执行错误：', '工具参数不是合法 JSON']

function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text), MARKDOWN_SANITIZE_CONFIG)
}

function hasThinking(value) {
  return String(value || '').trim().length > 0
}

function normalizeToolText(value) {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function prettyToolText(value) {
  const text = normalizeToolText(value)
  if (!text) return ''
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

function truncateText(text, limit) {
  if (text.length <= limit) return text
  return `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…`
}

function previewToolText(value, limit = TOOL_PREVIEW_LIMIT) {
  return truncateText(prettyToolText(value).replace(/\s+/g, ' ').trim(), limit)
}

function formatToolText(value) {
  return truncateText(prettyToolText(value), TOOL_DETAIL_LIMIT)
}

function hasLongToolText(value, limit = TOOL_PREVIEW_LIMIT) {
  const text = prettyToolText(value)
  return text.length > limit || text.includes('\n')
}

function isToolError(evt) {
  if (evt?.error === true) return true
  const summary = normalizeToolText(evt?.summary).trim()
  return TOOL_ERROR_PREFIXES.some(prefix => summary.startsWith(prefix))
}

function isNearBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= STICKY_SCROLL_THRESHOLD
}

function handleScroll() {
  const el = scrollContainer.value
  if (el) shouldStickToBottom.value = isNearBottom(el)
}

function scrollToBottom() {
  nextTick(() => {
    const el = scrollContainer.value
    if (!el || !shouldStickToBottom.value) return
    el.scrollTop = el.scrollHeight
  })
}

function requestOlderMessages() {
  const el = scrollContainer.value
  pendingPrependAnchor.value = el
    ? { scrollHeight: el.scrollHeight, scrollTop: el.scrollTop }
    : null
  emit('load-older')
}

function restorePrependScroll() {
  nextTick(() => {
    const el = scrollContainer.value
    const anchor = pendingPrependAnchor.value
    pendingPrependAnchor.value = null
    if (!el || !anchor) return
    el.scrollTop = el.scrollHeight - anchor.scrollHeight + anchor.scrollTop
    shouldStickToBottom.value = false
  })
}

watch(
  () => props.messages.length,
  (length, previousLength) => {
    if (pendingPrependAnchor.value && length > previousLength) {
      restorePrependScroll()
      return
    }
    const lastMessage = props.messages[length - 1]
    if (length < previousLength || lastMessage?.role === 'user') {
      shouldStickToBottom.value = true
    }
    scrollToBottom()
  },
)
watch(() => props.streamingText, scrollToBottom)
watch(() => props.streamingThinking, scrollToBottom)
watch(() => props.toolEvents.length, scrollToBottom)
watch(
  () => props.loadingOlder,
  (loading, wasLoading) => {
    if (!loading && wasLoading && pendingPrependAnchor.value) {
      pendingPrependAnchor.value = null
    }
  },
)
</script>

<style scoped>
.messages-area {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-6) var(--space-8);
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
  max-width: 860px;
  margin: 0 auto;
  padding-bottom: 8vh;
  animation: fadeIn 0.6s ease-out;
}

.welcome-icon {
  width: 72px;
  height: 72px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--bg-elevated) 82%, transparent);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-subtle);
  box-shadow: var(--shadow-md);
}

.welcome-title {
  font-size: 2.25rem;
  font-weight: 760;
  background: var(--gradient-brand);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  line-height: 1.12;
  text-align: center;
}

.welcome-desc {
  color: var(--text-secondary);
  font-size: 0.95rem;
  max-width: 520px;
  text-align: center;
}

.welcome-suggestions {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  justify-content: stretch;
  margin-top: var(--space-5);
  width: min(760px, 100%);
}

.suggestion-chip {
  min-height: 74px;
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--bg-elevated) 78%, transparent);
  border: 1px solid var(--border-default);
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: 560;
  line-height: 1.5;
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: var(--font-sans);
  text-align: left;
  box-shadow: var(--shadow-sm);
  position: relative;
  overflow: hidden;
}

.suggestion-chip::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--gradient-brand);
  opacity: 0.78;
}

.suggestion-chip:hover {
  background: var(--bg-elevated);
  color: var(--text-primary);
  border-color: color-mix(in srgb, var(--accent-blue) 32%, var(--border-default));
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

:global(.dark) .suggestion-chip {
  background: #171f2b;
  border-color: rgba(255, 255, 255, 0.12);
  color: var(--text-primary);
}

:global(.dark) .suggestion-chip:hover {
  background: #1d2633;
}

/* Messages */
.messages-list {
  max-width: 860px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  padding-bottom: var(--space-4);
}

.load-older {
  align-self: center;
  min-width: 132px;
}

.message {
  display: flex;
  gap: var(--space-3);
  animation: fadeIn var(--transition-normal) ease-out;
}

.message-user {
  justify-content: flex-end;
}

.message-user .message-avatar {
  order: 2;
}

.message-user .message-content {
  flex: 0 1 auto;
  max-width: min(78%, 620px);
}

.message-avatar {
  width: 34px;
  height: 34px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.message-user .message-avatar {
  background: color-mix(in srgb, var(--accent-blue) 13%, transparent);
  color: var(--accent-blue);
}

.message-assistant .message-avatar {
  background: color-mix(in srgb, var(--bg-elevated) 78%, transparent);
  border: 1px solid var(--border-subtle);
  color: var(--accent-blue);
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
  letter-spacing: 0;
}

.message-text {
  font-size: var(--text-base);
  line-height: 1.7;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

.message-user .message-text {
  background: color-mix(in srgb, var(--accent-blue) 10%, var(--bg-elevated));
  border: 1px solid color-mix(in srgb, var(--accent-blue) 20%, var(--border-subtle));
  border-radius: var(--radius-lg);
  padding: var(--space-3) var(--space-4);
  box-shadow: var(--shadow-sm);
}

.thinking-panel {
  margin-bottom: var(--space-2);
  max-width: min(100%, 680px);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--bg-tertiary) 56%, transparent);
  overflow: hidden;
}

.thinking-panel summary {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 36px;
  padding: 0 var(--space-3);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: var(--text-xs);
  font-weight: 680;
  list-style: none;
}

.thinking-panel summary::-webkit-details-marker {
  display: none;
}

.thinking-panel summary::after {
  content: '展开';
  margin-left: auto;
  color: var(--text-muted);
  font-size: var(--text-xs);
  font-weight: 560;
}

.thinking-panel[open] summary::after {
  content: '收起';
}

.thinking-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--accent-blue);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent-blue) 12%, transparent);
}

.thinking-active {
  animation: pulse-dot 1.4s ease-in-out infinite;
}

.thinking-content {
  padding: var(--space-3);
  border-top: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: 1.65;
  max-height: 280px;
  overflow: auto;
  overflow-wrap: anywhere;
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
  min-width: 0;
}

.tool-badge {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: var(--text-xs);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-full);
  max-width: 100%;
  line-height: 1.5;
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

.tool-badge-error {
  background: rgba(239, 68, 68, 0.1);
  color: var(--accent-red);
  border: 1px solid rgba(239, 68, 68, 0.18);
}

.tool-name,
.tool-summary {
  min-width: 0;
  overflow-wrap: anywhere;
}

.spinner-tool {
  width: 12px;
  height: 12px;
  border-width: 1.5px;
}

.tool-detail {
  max-width: min(100%, 560px);
  margin-top: var(--space-1);
}

.tool-detail summary {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  color: var(--text-muted);
  cursor: pointer;
  font-size: var(--text-xs);
  line-height: 1.5;
  list-style-position: inside;
}

.tool-detail summary code {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-detail-label {
  color: var(--text-secondary);
  flex-shrink: 0;
}

.tool-args {
  margin-top: var(--space-1);
  padding: var(--space-2) var(--space-3);
  background: color-mix(in srgb, var(--bg-tertiary) 74%, transparent);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  max-width: min(100%, 560px);
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
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

@media (max-width: 768px) {
  .messages-area {
    padding: var(--space-4);
  }

  .welcome-state {
    justify-content: flex-start;
    padding-top: var(--space-6);
  }

  .welcome-title {
    font-size: 1.65rem;
  }

  .welcome-suggestions {
    grid-template-columns: 1fr;
    width: 100%;
  }

  .suggestion-chip {
    width: 100%;
  }

  .tool-events,
  .generating-indicator {
    padding-left: 0;
  }

  .tool-badge,
  .tool-detail,
  .tool-args {
    max-width: 100%;
  }
}
</style>
