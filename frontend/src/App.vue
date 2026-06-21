<template>
  <div class="app-layout">
    <nav class="navbar">
      <div class="navbar-brand">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="url(#nav-grad)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <defs><linearGradient id="nav-grad" x1="0" y1="0" x2="24" y2="24"><stop offset="0%" stop-color="#3b82f6"/><stop offset="100%" stop-color="#8b5cf6"/></linearGradient></defs>
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
        <span class="navbar-title">WeChat RAG</span>
      </div>

      <div class="navbar-actions">
        <div class="navbar-links">
          <router-link to="/" class="nav-link" active-class="active" exact>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          对话
          </router-link>
          <router-link to="/settings" class="nav-link" active-class="active">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
          </svg>
          设置
          </router-link>
        </div>

        <button
          class="theme-toggle"
          type="button"
          :aria-pressed="isDarkTheme ? 'true' : 'false'"
          :title="isDarkTheme ? '切换到浅色主题' : '切换到深色主题'"
          @click="toggleTheme"
        >
          <span class="sr-only">{{ isDarkTheme ? '切换到浅色主题' : '切换到深色主题' }}</span>
          <svg v-if="isDarkTheme" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="4"/>
            <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>
          </svg>
          <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
          </svg>
        </button>
      </div>
    </nav>

    <main class="main-content">
      <router-view />
    </main>

    <!-- Toast notifications -->
    <div class="toast-container" v-if="toasts.length">
      <div v-for="t in toasts" :key="t.id" :class="['toast', `toast-${t.type}`]">
        {{ t.message }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, provide } from 'vue'

const toasts = ref([])
const theme = ref('light')
let toastId = 0
const MAX_TOASTS = 4
const MAX_TOAST_CHARS = 260
const THEME_STORAGE_KEY = 'wechat-rag-theme'

const isDarkTheme = computed(() => theme.value === 'dark')

onMounted(() => {
  theme.value = resolveInitialTheme()
  applyTheme(theme.value)
})

function resolveInitialTheme() {
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function toggleTheme() {
  theme.value = isDarkTheme.value ? 'light' : 'dark'
  window.localStorage.setItem(THEME_STORAGE_KEY, theme.value)
  applyTheme(theme.value)
}

function applyTheme(nextTheme) {
  document.documentElement.classList.toggle('dark', nextTheme === 'dark')
}

function showToast(message, type = 'info', duration = 3000) {
  const normalized = normalizeToastMessage(message)
  if (toasts.value.some(t => t.type === type && t.message === normalized)) return
  const id = ++toastId
  toasts.value = [...toasts.value, { id, message: normalized, type }].slice(-MAX_TOASTS)
  setTimeout(() => {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }, duration)
}

function normalizeToastMessage(message) {
  const text = toastTextFromValue(message)
  const singleLine = text.replace(/\s+/g, ' ').trim() || '操作失败'
  return singleLine.length > MAX_TOAST_CHARS
    ? `${singleLine.slice(0, MAX_TOAST_CHARS)}...`
    : singleLine
}

function toastTextFromValue(message) {
  if (typeof message === 'string') return message
  const detailText = firstDetailText(
    message?.message,
    message?.error?.message,
    message?.body?.error?.message,
    message?.body?.detail,
    message?.body?.message,
    message?.detail,
    message?.error?.details,
    message?.body?.error?.details,
  )
  if (detailText) return detailText
  const serialized = safeJsonStringify(message)
  if (serialized && serialized !== 'null' && serialized !== '{}') return serialized
  return '操作失败'
}

function detailTextFromValue(detail) {
  return detailTextFromValueInner(detail, new WeakSet())
}

function detailTextFromValueInner(detail, seen) {
  const scalar = scalarToastText(detail)
  if (scalar) return scalar
  if (detail && typeof detail === 'object') {
    if (seen.has(detail)) return '[Circular]'
    seen.add(detail)
  }
  if (Array.isArray(detail)) {
    return detail.map((item) => detailTextFromValueInner(item, seen)).filter(Boolean).join('；')
  }
  if (detail && typeof detail === 'object') {
    const message = firstDetailTextWithSeen(seen, detail.msg, detail.message, detail.detail, detail.type)
    if (message) {
      const fieldPath = fieldPathFromDetail(detail.loc || detail.field || detail.path)
      return fieldPath ? `${fieldPath}: ${message}` : message
    }
    const serialized = safeJsonStringify(detail)
    return serialized && serialized !== '{}' ? serialized : ''
  }
  return ''
}

function firstDetailText(...values) {
  return firstDetailTextWithSeen(new WeakSet(), ...values)
}

function firstDetailTextWithSeen(seen, ...values) {
  for (const value of values) {
    const text = detailTextFromValueInner(value, seen)
    if (text) return text
  }
  return ''
}

function scalarToastText(value) {
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

function fieldPathFromDetail(loc) {
  if (Array.isArray(loc)) return loc.filter((item) => item !== 'body').map(String).join('.')
  return scalarToastText(loc)
}

function safeJsonStringify(value) {
  const seen = new WeakSet()
  try {
    return JSON.stringify(value, (_key, item) => {
      if (typeof item === 'object' && item !== null) {
        if (seen.has(item)) return '[Circular]'
        seen.add(item)
      }
      return item
    })
  } catch {
    return ''
  }
}

provide('toast', showToast)
</script>

<style scoped>
.app-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

.navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--navbar-height);
  padding: 0 var(--space-5);
  background: color-mix(in srgb, var(--bg-secondary) 88%, transparent);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
  z-index: 100;
}

.navbar-brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.navbar-brand svg {
  width: 34px;
  height: 34px;
  padding: 7px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--bg-elevated) 76%, transparent);
}

.navbar-title {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0;
}

.navbar-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.navbar-links {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: 4px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--bg-elevated) 60%, transparent);
}

.nav-link {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 34px;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--text-muted);
  text-decoration: none;
  transition: all var(--transition-fast);
}

.nav-link:hover {
  color: var(--text-secondary);
  background: var(--surface-glass-hover);
}

.nav-link.active {
  color: var(--text-primary);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-sm);
}

.theme-toggle {
  width: 36px;
  height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--bg-elevated) 72%, transparent);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.theme-toggle:hover {
  background: var(--surface-glass-hover);
  color: var(--text-primary);
  border-color: var(--border-default);
}

.main-content {
  flex: 1;
  overflow: hidden;
}

@media (max-width: 768px) {
  .navbar {
    height: auto;
    min-height: var(--navbar-height);
    padding: var(--space-2) var(--space-3);
    gap: var(--space-2);
  }

  .navbar-title {
    font-size: var(--text-base);
  }

  .nav-link {
    padding: var(--space-2);
  }

  .navbar-actions {
    gap: var(--space-1);
  }
}
</style>
