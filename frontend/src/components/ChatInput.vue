<template>
  <div class="chat-input-area">
    <!-- Suggestions dropdown -->
    <div
      v-if="suggestionsVisible"
      id="chat-suggestion-list"
      class="suggestions-dropdown"
      role="listbox"
      aria-label="联系人和群聊建议"
    >
      <div
        v-for="(s, idx) in suggestions"
        :id="suggestionId(idx)"
        :key="suggestionKey(s, idx)"
        :class="['suggestion-item', { active: selectedSuggestionIdx === idx }]"
        role="option"
        :aria-selected="selectedSuggestionIdx === idx ? 'true' : 'false'"
        @mousedown.prevent="applySuggestion(s)"
      >
        <span :class="['suggestion-type', `type-${s.type}`]">{{ s.type === 'sender' ? '联系人' : '群聊' }}</span>
        <span class="suggestion-value">{{ s.value }}</span>
        <span class="suggestion-count text-muted text-xs">{{ s.count }} 条</span>
      </div>
    </div>

    <div class="input-wrapper glass-card">
      <textarea
        ref="inputEl"
        v-model="text"
        class="chat-textarea"
        :placeholder="isGenerating ? '等待回复中…' : '输入你的问题…'"
        :disabled="disabled"
        role="combobox"
        aria-autocomplete="list"
        aria-haspopup="listbox"
        aria-controls="chat-suggestion-list"
        :aria-expanded="suggestionsVisible ? 'true' : 'false'"
        :aria-activedescendant="activeSuggestionId"
        :maxlength="MAX_QUESTION_CHARS"
        @keydown="handleKeydown"
        @input="handleInput"
        @focus="onFocus"
        @blur="onBlur"
        rows="1"
        id="chat-input"
      ></textarea>

      <div class="input-actions">
        <button
          v-if="!isGenerating"
          class="btn btn-primary btn-icon send-btn"
          :disabled="!text.trim() || disabled"
          @click="send"
          id="btn-send"
          title="发送"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        </button>
        <button
          v-else
          class="btn btn-danger btn-icon stop-btn"
          @click="$emit('stop')"
          id="btn-stop"
          title="停止生成"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { getSuggestions } from '../api/api.js'

const props = defineProps({
  isGenerating: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['send', 'stop'])

const text = ref('')
const inputEl = ref(null)
const suggestions = ref([])
const showSuggestions = ref(false)
const selectedSuggestionIdx = ref(-1)
const suggestionsVisible = computed(() => showSuggestions.value && suggestions.value.length > 0)
const activeSuggestionId = computed(() => (
  suggestionsVisible.value && selectedSuggestionIdx.value >= 0
    ? suggestionId(selectedSuggestionIdx.value)
    : undefined
))

let suggestTimer = null
let blurTimer = null
let suggestRequestSeq = 0
let activeSuggestController = null
const MAX_QUESTION_CHARS = 8000
const MAX_SUGGESTION_QUERY_CHARS = 120

function send() {
  const question = text.value.trim().slice(0, MAX_QUESTION_CHARS)
  if (!question || props.disabled || props.isGenerating) return
  emit('send', question)
  text.value = ''
  closeSuggestions()
  autoResize()
}

function handleKeydown(e) {
  if (suggestionsVisible.value) {
    if (e.key === 'Escape') {
      e.preventDefault()
      closeSuggestions()
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      selectedSuggestionIdx.value = (selectedSuggestionIdx.value + 1) % suggestions.value.length
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      selectedSuggestionIdx.value = (selectedSuggestionIdx.value - 1 + suggestions.value.length) % suggestions.value.length
      return
    }
    if (e.key === 'Tab') {
      e.preventDefault()
      applySuggestion(suggestions.value[Math.max(selectedSuggestionIdx.value, 0)])
      return
    }
    if (e.key === 'Enter' && selectedSuggestionIdx.value >= 0) {
      e.preventDefault()
      applySuggestion(suggestions.value[selectedSuggestionIdx.value])
      return
    }
  }

  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function handleInput() {
  autoResize()
  debounceSuggest()
}

function autoResize() {
  nextTick(() => {
    const el = inputEl.value
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  })
}

function suggestionQuery() {
  const cursor = inputEl.value?.selectionStart ?? text.value.length
  const beforeCursor = text.value.slice(0, cursor)
  const match = beforeCursor.match(/[^\s,，。！？；;:：、]*$/)
  return (match?.[0] || '').trim().slice(0, MAX_SUGGESTION_QUERY_CHARS)
}

function debounceSuggest() {
  clearTimeout(suggestTimer)
  if (props.disabled || props.isGenerating) {
    closeSuggestions()
    return
  }
  const val = suggestionQuery()
  const requestSeq = ++suggestRequestSeq
  activeSuggestController?.abort()
  activeSuggestController = null
  if (!val || val.length < 1) {
    closeSuggestions()
    return
  }
  suggestTimer = setTimeout(async () => {
    const controller = new AbortController()
    activeSuggestController = controller
    try {
      const res = await getSuggestions(val, 8, { signal: controller.signal })
      if (requestSeq !== suggestRequestSeq) return
      suggestions.value = res.items || []
      showSuggestions.value = suggestions.value.length > 0
      selectedSuggestionIdx.value = -1
    } catch {
      if (requestSeq !== suggestRequestSeq) return
      suggestions.value = []
      showSuggestions.value = false
      selectedSuggestionIdx.value = -1
    } finally {
      if (activeSuggestController === controller) activeSuggestController = null
    }
  }, 250)
}

function applySuggestion(s) {
  if (!s?.value) return
  const el = inputEl.value
  const cursor = el?.selectionStart ?? text.value.length
  const beforeCursor = text.value.slice(0, cursor)
  const afterCursor = text.value.slice(cursor)
  const prefix = beforeCursor.replace(/[^\s,，。！？；;:：、]*$/, '')
  const separator = afterCursor && !/^[\s,，。！？；;:：、]/.test(afterCursor) ? ' ' : ''
  const nextText = `${prefix}${s.value}${separator}${afterCursor}`
  const nextCursor = prefix.length + s.value.length + (separator ? 1 : 0)
  const boundedText = nextText.slice(0, MAX_QUESTION_CHARS)
  const boundedCursor = Math.min(nextCursor, boundedText.length)
  text.value = boundedText
  closeSuggestions()
  nextTick(() => {
    inputEl.value?.focus()
    inputEl.value?.setSelectionRange(boundedCursor, boundedCursor)
    autoResize()
  })
}

function closeSuggestions() {
  clearTimeout(suggestTimer)
  suggestTimer = null
  activeSuggestController?.abort()
  activeSuggestController = null
  suggestRequestSeq += 1
  suggestions.value = []
  showSuggestions.value = false
  selectedSuggestionIdx.value = -1
}

function suggestionId(idx) {
  return `chat-suggestion-${idx}`
}

function suggestionKey(s, idx) {
  return `${s?.type || 'unknown'}:${s?.value || idx}`
}

function onFocus() {
  clearTimeout(blurTimer)
  blurTimer = null
  if (!props.disabled && !props.isGenerating && suggestions.value.length > 0) {
    showSuggestions.value = true
  }
}

function onBlur() {
  clearTimeout(blurTimer)
  blurTimer = setTimeout(() => {
    blurTimer = null
    showSuggestions.value = false
  }, 150)
}

watch(
  () => [props.disabled, props.isGenerating],
  ([disabled, isGenerating]) => {
    if (disabled || isGenerating) closeSuggestions()
  },
)

onMounted(() => {
  inputEl.value?.focus()
})

onUnmounted(() => {
  clearTimeout(blurTimer)
  blurTimer = null
  clearTimeout(suggestTimer)
  closeSuggestions()
})
</script>

<style scoped>
.chat-input-area {
  padding: var(--space-4) var(--space-6) var(--space-6);
  position: relative;
  max-width: 800px;
  margin: 0 auto;
  width: 100%;
}

.input-wrapper {
  display: flex;
  align-items: flex-end;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-default);
  transition: border-color var(--transition-fast);
}

.input-wrapper:focus-within {
  border-color: var(--border-focus);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.08);
}

.chat-textarea {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--text-base);
  line-height: 1.5;
  resize: none;
  max-height: 160px;
  padding: var(--space-2) 0;
}

.chat-textarea::placeholder {
  color: var(--text-muted);
}

.chat-textarea:disabled {
  opacity: 0.5;
}

.input-actions {
  flex-shrink: 0;
  display: flex;
  padding-bottom: 2px;
}

.send-btn, .stop-btn {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
}

.stop-btn {
  animation: fadeIn var(--transition-fast) ease-out;
}

/* Suggestions */
.suggestions-dropdown {
  position: absolute;
  bottom: 100%;
  left: var(--space-6);
  right: var(--space-6);
  max-width: calc(800px - var(--space-6) * 2);
  margin: 0 auto var(--space-2) auto;
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  animation: slideUp var(--transition-fast) ease-out;
  z-index: 50;
}

.suggestion-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.suggestion-item:hover, .suggestion-item.active {
  background: var(--surface-glass-hover);
}

.suggestion-type {
  font-size: var(--text-xs);
  font-weight: 600;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
}

.type-sender {
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent-blue);
}

.type-thread {
  background: rgba(139, 92, 246, 0.15);
  color: var(--accent-purple);
}

.suggestion-value {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

.suggestion-count {
  flex-shrink: 0;
}

@media (max-width: 768px) {
  .chat-input-area {
    padding: var(--space-3) var(--space-4) var(--space-4);
  }

  .suggestions-dropdown {
    left: var(--space-4);
    right: var(--space-4);
    max-width: none;
  }
}
</style>
