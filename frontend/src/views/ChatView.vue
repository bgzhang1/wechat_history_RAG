<template>
  <div class="chat-layout">
    <SessionSidebar
      :sessions="sessions"
      :active-id="activeSessionId"
      :loading="loadingSessions"
      :loading-more="loadingMoreSessions"
      :total-count="sessionsTotal"
      :locked="isGenerating"
      :selection-clear-token="batchSelectionClearToken"
      @select="selectSession"
      @new-chat="newChat"
      @rename="handleRename"
      @delete="handleDelete"
      @batch-delete="handleBatchDelete"
      @load-more="loadMoreSessions"
    />
    <div class="chat-main">
      <ChatMessages
        :messages="messages"
        :streaming-text="streamingText"
        :tool-events="toolEvents"
        :is-generating="isGenerating"
        :loading="loadingMessages"
        :loading-older="loadingOlderMessages"
        :has-older="loadedMessagesCount < messagesTotal"
        @quick-send="handleSend"
        @load-older="loadOlderMessages"
      />
      <ChatInput
        :is-generating="isGenerating"
        :disabled="loadingMessages"
        @send="handleSend"
        @stop="handleStop"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, inject, onMounted, onUnmounted } from 'vue'
import SessionSidebar from '../components/SessionSidebar.vue'
import ChatMessages from '../components/ChatMessages.vue'
import ChatInput from '../components/ChatInput.vue'
import {
  chatSSE, abortChat,
  getSessions, getMessages,
  renameSession, deleteSession, batchDeleteSessions,
} from '../api/api.js'

const toast = inject('toast')

const sessions = ref([])
const activeSessionId = ref(null)
const messages = ref([])
const streamingText = ref('')
const toolEvents = ref([])
const isGenerating = ref(false)
const loadingSessions = ref(false)
const loadingMoreSessions = ref(false)
const loadingMessages = ref(false)
const loadingOlderMessages = ref(false)
const sessionsTotal = ref(0)
const messagesTotal = ref(0)
const loadedMessagesCount = ref(0)
const batchSelectionClearToken = ref(0)

let currentStream = null
let currentQuestion = ''
let currentStreamSessionId = null
let componentDisposed = false
let activeStreamRun = 0
let messageLoadSeq = 0
let sessionLoadSeq = 0
const activeSessionLoadControllers = new Set()
const activeSessionMutationControllers = new Set()
let activeMessagesController = null
let activeOlderMessagesController = null
const SESSIONS_PAGE_SIZE = 100
const MESSAGES_PAGE_SIZE = 500
const STOP_ABORT_FALLBACK_MS = 1500
const STOP_REQUEST_TIMEOUT_MS = 3000
const MAX_STOP_PARTIAL_ANSWER_CHARS = 20000
const DELETE_CONFIRM_PREFIX = '确定删除这个会话吗？'
const BATCH_DELETE_CONFIRM_PREFIX = '确定删除选中的会话吗？'

onMounted(() => { loadSessions() })

onUnmounted(() => {
  componentDisposed = true
  abortListLoads()
  abortMessageLoads()
  abortSessionMutations()
  cleanupActiveStream()
})

function mergeSessions(existing, incoming) {
  const byId = new Map(existing.map(session => [session.session_id, session]))
  incoming.forEach(session => byId.set(session.session_id, session))
  return Array.from(byId.values()).sort(compareSessions)
}

function compareSessions(a, b) {
  return (
    String(b.updated_at || '').localeCompare(String(a.updated_at || ''))
    || String(b.created_at || '').localeCompare(String(a.created_at || ''))
    || String(b.session_id || '').localeCompare(String(a.session_id || ''))
  )
}

async function loadSessions({ append = false, force = false } = {}) {
  if (append && loadingSessions.value) return
  if (append ? loadingMoreSessions.value : (loadingSessions.value && !force)) return
  const seq = append ? sessionLoadSeq : ++sessionLoadSeq
  if (!append) abortListLoads()
  const controller = new AbortController()
  activeSessionLoadControllers.add(controller)
  if (append) loadingMoreSessions.value = true
  else loadingSessions.value = true
  try {
    const offset = append ? sessions.value.length : 0
    const data = await getSessions(SESSIONS_PAGE_SIZE, offset, { signal: controller.signal })
    if (componentDisposed || seq !== sessionLoadSeq) return
    const items = Array.isArray(data) ? data : (data.items || [])
    sessions.value = append ? mergeSessions(sessions.value, items) : items
    sessionsTotal.value = Array.isArray(data) ? sessions.value.length : (data.total_count || 0)
  } catch (e) {
    if (!componentDisposed && seq === sessionLoadSeq) toast(e.message, 'error')
  } finally {
    if (!componentDisposed) {
      if (append) loadingMoreSessions.value = false
      else if (seq === sessionLoadSeq) loadingSessions.value = false
    }
    activeSessionLoadControllers.delete(controller)
  }
}

function loadMoreSessions() {
  if (loadingSessions.value || loadingMoreSessions.value) return
  if (sessions.value.length >= sessionsTotal.value) return
  loadSessions({ append: true })
}

async function selectSession(sid) {
  if (isGenerating.value) {
    toast('请先停止当前生成', 'info')
    return
  }
  const seq = ++messageLoadSeq
  abortMessageLoads()
  const controller = new AbortController()
  activeMessagesController = controller
  activeSessionId.value = sid
  loadingMessages.value = true
  try {
    const data = await getMessages(sid, MESSAGES_PAGE_SIZE, 0, { signal: controller.signal })
    if (componentDisposed || seq !== messageLoadSeq || activeSessionId.value !== sid) return
    const items = Array.isArray(data) ? data : (data.items || [])
    messages.value = items
    messagesTotal.value = Array.isArray(data) ? items.length : (data.total_count || 0)
    loadedMessagesCount.value = items.length
  } catch (e) {
    if (componentDisposed || seq !== messageLoadSeq || activeSessionId.value !== sid) return
    toast(e.message, 'error')
    messages.value = []
    messagesTotal.value = 0
    loadedMessagesCount.value = 0
  } finally {
    if (!componentDisposed && seq === messageLoadSeq && activeSessionId.value === sid) loadingMessages.value = false
    if (activeMessagesController === controller) activeMessagesController = null
  }
}

async function loadOlderMessages() {
  if (!activeSessionId.value || loadingOlderMessages.value || loadingMessages.value) return
  if (loadedMessagesCount.value >= messagesTotal.value) return
  const sid = activeSessionId.value
  const seq = messageLoadSeq
  const controller = new AbortController()
  activeOlderMessagesController = controller
  loadingOlderMessages.value = true
  try {
    const data = await getMessages(sid, MESSAGES_PAGE_SIZE, loadedMessagesCount.value, { signal: controller.signal })
    if (componentDisposed || seq !== messageLoadSeq || activeSessionId.value !== sid) return
    const items = Array.isArray(data) ? data : (data.items || [])
    const existingIds = new Set(messages.value.map(message => message.id).filter(Boolean))
    const older = items.filter(message => !message.id || !existingIds.has(message.id))
    messages.value = [...older, ...messages.value]
    loadedMessagesCount.value += items.length
    messagesTotal.value = Array.isArray(data) ? messages.value.length : (data.total_count || messagesTotal.value)
  } catch (e) {
    if (!componentDisposed && seq === messageLoadSeq && activeSessionId.value === sid) toast(e.message, 'error')
  } finally {
    if (!componentDisposed && seq === messageLoadSeq && activeSessionId.value === sid) loadingOlderMessages.value = false
    if (activeOlderMessagesController === controller) activeOlderMessagesController = null
  }
}

function newChat() {
  if (isGenerating.value) {
    toast('请先停止当前生成', 'info')
    return
  }
  messageLoadSeq += 1
  abortMessageLoads()
  activeSessionId.value = null
  messages.value = []
  messagesTotal.value = 0
  loadedMessagesCount.value = 0
  streamingText.value = ''
  toolEvents.value = []
}

async function handleSend(question) {
  const questionText = question.trim()
  if (isGenerating.value || loadingMessages.value || !questionText) return

  // Add user message immediately
  currentQuestion = questionText
  currentStreamSessionId = activeSessionId.value
  messages.value.push({ id: `local-user-${Date.now()}`, role: 'user', content: questionText, created_at: new Date().toISOString() })
  streamingText.value = ''
  toolEvents.value = []
  isGenerating.value = true
  const runId = ++activeStreamRun

  currentStream = chatSSE(questionText, activeSessionId.value, {
    onSession(data) {
      if (runId !== activeStreamRun) return
      activeSessionId.value = data.session_id
      currentStreamSessionId = data.session_id
    },
    onToolCall(data) {
      if (runId !== activeStreamRun) return
      toolEvents.value.push({ type: 'call', name: data.name, args: data.args })
    },
    onToolResult(data) {
      if (runId !== activeStreamRun) return
      toolEvents.value.push({ type: 'result', name: data.name, summary: data.summary })
    },
    onText(data) {
      if (runId !== activeStreamRun) return
      streamingText.value += data.chunk
    },
    onDone(data) {
      if (runId !== activeStreamRun) return
      const doneSessionId = data.session_id || currentStreamSessionId
      if (!doneSessionId || doneSessionId === activeSessionId.value) {
        messages.value.push({
          id: `local-assistant-${Date.now()}`,
          role: 'assistant',
          content: data.answer,
          created_at: new Date().toISOString(),
        })
        recordPersistedLocalExchange()
      }
      streamingText.value = ''
      toolEvents.value = []
      isGenerating.value = false
      currentStream = null
      currentQuestion = ''
      currentStreamSessionId = null
      loadSessions({ force: true }) // refresh sidebar
    },
    onError(data) {
      if (runId !== activeStreamRun) return
      const message = data.message || data.detail || '生成失败'
      toast(message, 'error')
      messages.value.push({
        id: `local-error-${Date.now()}`,
        role: 'assistant',
        content: streamingText.value
          ? `${streamingText.value}\n\n*（生成失败：${message}）*`
          : `*（生成失败：${message}）*`,
        created_at: new Date().toISOString(),
      })
      clearStreamState()
      loadSessions({ force: true })
    },
  })
}

async function handleStop() {
  if (!isGenerating.value && !currentStream) return
  const stoppedQuestion = currentQuestion
  const stoppedAnswer = stopPartialAnswerPayload(streamingText.value)
  const abortSessionId = activeSessionId.value || currentStreamSessionId
  const streamToAbort = currentStream
  const abortRequest = abortSessionId
    ? abortChat(abortSessionId, stoppedQuestion ? {
        question: stoppedQuestion,
        partial_answer: stoppedAnswer,
      } : null, { timeoutMs: STOP_REQUEST_TIMEOUT_MS }).catch(() => null)
    : Promise.resolve(null)
  const abortFallback = setTimeout(() => streamToAbort?.abort(), STOP_ABORT_FALLBACK_MS)
  activeStreamRun += 1
  isGenerating.value = false
  currentStream = null
  currentQuestion = ''
  currentStreamSessionId = null
  if (streamingText.value) {
    messages.value.push({
      id: `local-stop-${Date.now()}`,
      role: 'assistant',
      content: streamingText.value + '\n\n*（已停止生成）*',
      created_at: new Date().toISOString(),
    })
    streamingText.value = ''
  } else if (stoppedQuestion) {
    messages.value.push({
      id: `local-stop-${Date.now()}`,
      role: 'assistant',
      content: '*（已停止生成）*',
      created_at: new Date().toISOString(),
    })
  }
  toolEvents.value = []
  const abortResult = await Promise.race([abortRequest, delay(STOP_REQUEST_TIMEOUT_MS)])
  clearTimeout(abortFallback)
  streamToAbort?.abort()
  if (componentDisposed) return
  if (abortResult && abortSessionId && stoppedQuestion) recordPersistedLocalExchange()
  loadSessions({ force: true })
}

function delay(ms) {
  return new Promise(resolve => window.setTimeout(resolve, ms))
}

function stopPartialAnswerPayload(value) {
  return String(value || '').trim().slice(0, MAX_STOP_PARTIAL_ANSWER_CHARS)
}

function recordPersistedLocalExchange() {
  loadedMessagesCount.value += 2
  messagesTotal.value += 2
}

function clearStreamState() {
  streamingText.value = ''
  toolEvents.value = []
  isGenerating.value = false
  currentStream = null
  currentQuestion = ''
  currentStreamSessionId = null
}

function cleanupActiveStream() {
  if (!currentStream) return
  activeStreamRun += 1
  const abortSessionId = activeSessionId.value || currentStreamSessionId
  const question = currentQuestion
  const partialAnswer = stopPartialAnswerPayload(streamingText.value)
  if (abortSessionId && question) {
    abortChat(abortSessionId, {
      question,
      partial_answer: partialAnswer,
    }, { timeoutMs: STOP_REQUEST_TIMEOUT_MS }).catch(() => null)
  }
  currentStream.abort()
  clearStreamState()
}

function abortListLoads() {
  activeSessionLoadControllers.forEach(controller => controller.abort())
  activeSessionLoadControllers.clear()
}

function abortMessageLoads() {
  activeMessagesController?.abort()
  activeMessagesController = null
  activeOlderMessagesController?.abort()
  activeOlderMessagesController = null
}

function sessionMutationController() {
  const controller = new AbortController()
  activeSessionMutationControllers.add(controller)
  return controller
}

function releaseSessionMutationController(controller) {
  activeSessionMutationControllers.delete(controller)
}

function abortSessionMutations() {
  activeSessionMutationControllers.forEach(controller => controller.abort())
  activeSessionMutationControllers.clear()
}

async function handleRename({ sessionId, title }) {
  if (isGenerating.value) {
    toast('请先停止当前生成', 'info')
    return
  }
  const controller = sessionMutationController()
  try {
    await renameSession(sessionId, title, { signal: controller.signal })
    if (componentDisposed) return
    await loadSessions({ force: true })
    if (componentDisposed) return
    toast('已重命名', 'success')
  } catch (e) {
    if (!componentDisposed) toast(e.message, 'error')
  } finally {
    releaseSessionMutationController(controller)
  }
}

async function handleDelete(sessionId) {
  if (isGenerating.value) {
    toast('请先停止当前生成', 'info')
    return
  }
  if (!window.confirm(DELETE_CONFIRM_PREFIX)) return
  const controller = sessionMutationController()
  try {
    await deleteSession(sessionId, { signal: controller.signal })
    if (componentDisposed) return
    if (activeSessionId.value === sessionId) newChat()
    await loadSessions({ force: true })
    if (componentDisposed) return
    toast('已删除', 'success')
  } catch (e) {
    if (!componentDisposed) toast(e.message, 'error')
  } finally {
    releaseSessionMutationController(controller)
  }
}

async function handleBatchDelete(ids) {
  if (isGenerating.value) {
    toast('请先停止当前生成', 'info')
    return
  }
  if (!ids.length) return
  if (!window.confirm(`${BATCH_DELETE_CONFIRM_PREFIX}（${ids.length} 个）`)) return
  const controller = sessionMutationController()
  try {
    const result = await batchDeleteSessions(ids, { signal: controller.signal })
    if (componentDisposed) return
    if (result.deleted?.includes(activeSessionId.value)) newChat()
    batchSelectionClearToken.value += 1
    await loadSessions({ force: true })
    if (componentDisposed) return
    const deleted = result.deleted?.length ?? ids.length
    const active = result.active?.length ?? 0
    toast(active ? `已删除 ${deleted} 个会话，${active} 个生成中会话已保留` : `已删除 ${deleted} 个会话`, 'success')
  } catch (e) {
    if (!componentDisposed) toast(e.message, 'error')
  } finally {
    releaseSessionMutationController(controller)
  }
}
</script>

<style scoped>
.chat-layout {
  display: flex;
  height: 100%;
  overflow: hidden;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
  min-width: 0;
}

@media (max-width: 768px) {
  .chat-layout {
    flex-direction: column;
  }

  .chat-main {
    min-height: 0;
  }
}
</style>
