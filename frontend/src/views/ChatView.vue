<template>
  <div class="chat-layout">
    <SessionSidebar
      :sessions="sessions"
      :active-id="activeSessionId"
      :loading="loadingSessions"
      @select="selectSession"
      @new-chat="newChat"
      @rename="handleRename"
      @delete="handleDelete"
      @batch-delete="handleBatchDelete"
    />
    <div class="chat-main">
      <ChatMessages
        :messages="messages"
        :streaming-text="streamingText"
        :tool-events="toolEvents"
        :is-generating="isGenerating"
        :loading="loadingMessages"
      />
      <ChatInput
        :is-generating="isGenerating"
        :disabled="false"
        @send="handleSend"
        @stop="handleStop"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, inject, onMounted } from 'vue'
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
const loadingMessages = ref(false)

let currentStream = null

onMounted(() => { loadSessions() })

async function loadSessions() {
  loadingSessions.value = true
  try {
    sessions.value = await getSessions()
  } catch (e) {
    toast(e.message, 'error')
  }
  loadingSessions.value = false
}

async function selectSession(sid) {
  activeSessionId.value = sid
  loadingMessages.value = true
  try {
    messages.value = await getMessages(sid)
  } catch (e) {
    toast(e.message, 'error')
    messages.value = []
  }
  loadingMessages.value = false
}

function newChat() {
  activeSessionId.value = null
  messages.value = []
  streamingText.value = ''
  toolEvents.value = []
}

async function handleSend(question) {
  if (isGenerating.value || !question.trim()) return

  // Add user message immediately
  messages.value.push({ role: 'user', content: question, created_at: new Date().toISOString() })
  streamingText.value = ''
  toolEvents.value = []
  isGenerating.value = true

  currentStream = chatSSE(question, activeSessionId.value, {
    onSession(data) {
      activeSessionId.value = data.session_id
    },
    onToolCall(data) {
      toolEvents.value.push({ type: 'call', name: data.name, args: data.args })
    },
    onToolResult(data) {
      toolEvents.value.push({ type: 'result', name: data.name, summary: data.summary })
    },
    onText(data) {
      streamingText.value += data.chunk
    },
    onDone(data) {
      messages.value.push({
        role: 'assistant',
        content: data.answer,
        created_at: new Date().toISOString(),
      })
      streamingText.value = ''
      toolEvents.value = []
      isGenerating.value = false
      currentStream = null
      loadSessions() // refresh sidebar
    },
    onError(data) {
      toast(data.message || '生成失败', 'error')
      isGenerating.value = false
      currentStream = null
    },
  })
}

async function handleStop() {
  if (activeSessionId.value) {
    try {
      await abortChat(activeSessionId.value)
    } catch { /* ignore */ }
  }
  currentStream?.abort()
  isGenerating.value = false
  if (streamingText.value) {
    messages.value.push({
      role: 'assistant',
      content: streamingText.value + '\n\n*（已停止生成）*',
      created_at: new Date().toISOString(),
    })
    streamingText.value = ''
  }
}

async function handleRename({ sessionId, title }) {
  try {
    await renameSession(sessionId, title)
    await loadSessions()
    toast('已重命名', 'success')
  } catch (e) { toast(e.message, 'error') }
}

async function handleDelete(sessionId) {
  try {
    await deleteSession(sessionId)
    if (activeSessionId.value === sessionId) newChat()
    await loadSessions()
    toast('已删除', 'success')
  } catch (e) { toast(e.message, 'error') }
}

async function handleBatchDelete(ids) {
  try {
    await batchDeleteSessions(ids)
    if (ids.includes(activeSessionId.value)) newChat()
    await loadSessions()
    toast(`已删除 ${ids.length} 个会话`, 'success')
  } catch (e) { toast(e.message, 'error') }
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
}
</style>
