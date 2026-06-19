/**
 * API service layer – wraps all backend endpoints.
 * Base URL defaults to http://localhost:8000.
 */

const rawApiBase = import.meta.env.VITE_API_BASE

function defaultApiBase() {
  if (typeof window === 'undefined') return 'http://localhost:8000'
  const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:'
  const hostname = window.location.hostname || 'localhost'
  return `${protocol}//${hostname}:8000`
}

const BASE = (rawApiBase === undefined ? defaultApiBase() : rawApiBase).replace(/\/+$/, '')
const BASE_LABEL = BASE || '当前同源'
const API = `${BASE}/api`
const REQUEST_TIMEOUT_MS = 60000
const UPLOAD_TIMEOUT_MS = 10 * 60 * 1000

// ─── helpers ────────────────────────────────────────────────────────────────────

function errorMessageFromBody(body, fallback = '请求失败') {
  const action = body?.error?.action
  const withAction = (message) => {
    if (typeof action === 'string' && action.trim() && action.trim() !== message.trim()) {
      return `${message} ${action.trim()}`
    }
    return message
  }
  const message = firstErrorText(
    body?.error?.message,
    body?.detail,
    body?.message,
    body?.error?.details,
  )
  if (message) return withAction(message)
  return withAction(fallback)
}

function firstErrorText(...values) {
  for (const value of values) {
    const text = errorTextFromValue(value)
    if (text) return text
  }
  return ''
}

function scalarText(value) {
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

function fieldPathFromLoc(loc) {
  if (Array.isArray(loc)) return loc.filter((item) => item !== 'body').map(String).join('.')
  return scalarText(loc)
}

function errorTextFromValue(value) {
  const scalar = scalarText(value)
  if (scalar) return scalar
  if (Array.isArray(value)) {
    const messages = value.map(errorTextFromValue).filter(Boolean)
    return messages.join('；')
  }
  if (value && typeof value === 'object') {
    const message = firstErrorText(value.msg, value.message, value.detail, value.type)
    if (!message) return ''
    const fieldPath = fieldPathFromLoc(value.loc || value.field || value.path)
    return fieldPath ? `${fieldPath}: ${message}` : message
  }
  return ''
}

function friendlyNetworkError(err) {
  if (err?.name === 'AbortError') return err
  const message = String(err?.message || err || '')
  if (
    err instanceof TypeError
    || /failed to fetch/i.test(message)
    || /networkerror/i.test(message)
    || /load failed/i.test(message)
  ) {
    return new Error(`无法连接后端服务（${BASE_LABEL}）。请确认后端已启动，或检查 VITE_API_BASE / CORS_ORIGINS 配置。`)
  }
  return err instanceof Error ? err : new Error(message || '网络请求失败')
}

function wsUrl(path) {
  const browserBase = typeof window !== 'undefined' ? window.location.href : 'http://localhost/'
  const url = new URL(`${API}${path}`, browserBase)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

function pathSegment(value) {
  return encodeURIComponent(String(value))
}

function queryString(params = {}) {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    query.set(key, String(value))
  })
  return query.toString()
}

function createAbortController(timeoutMs, externalSignal) {
  let timedOut = false
  const controller = new AbortController()
  const timeoutId = timeoutMs > 0
    ? setTimeout(() => {
        timedOut = true
        controller.abort()
      }, timeoutMs)
    : null
  const abortFromExternal = () => controller.abort()
  if (externalSignal?.aborted) {
    controller.abort()
  } else if (externalSignal) {
    externalSignal.addEventListener('abort', abortFromExternal, { once: true })
  }
  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    clearTimeout: () => {
      if (timeoutId) clearTimeout(timeoutId)
    },
    cleanup: () => {
      if (timeoutId) clearTimeout(timeoutId)
      externalSignal?.removeEventListener?.('abort', abortFromExternal)
    },
  }
}

async function request(path, options = {}) {
  const url = `${API}${path}`
  let res
  const timeoutMs = Number.isFinite(options.timeoutMs) ? options.timeoutMs : REQUEST_TIMEOUT_MS
  const { timeoutMs: _timeoutMs, signal: externalSignal, ...fetchOptions } = options
  const abortState = createAbortController(timeoutMs, externalSignal)
  try {
    res = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...fetchOptions.headers },
      ...fetchOptions,
      signal: abortState.signal,
    })
  } catch (err) {
    if (abortState.didTimeout()) {
      throw new Error(`请求超时（${Math.round(timeoutMs / 1000)} 秒）。请确认后端服务可用后重试。`)
    }
    throw friendlyNetworkError(err)
  } finally {
    abortState.cleanup()
  }
  if (!res.ok) {
    let errBody
    try { errBody = await res.json() } catch { errBody = null }
    const msg = errorMessageFromBody(errBody, res.statusText || `HTTP ${res.status}`)
    const err = new Error(msg)
    err.status = res.status
    err.body = errBody
    throw err
  }
  return res.json()
}

function get(path, options = {}) { return request(path, options) }
function post(path, body, options = {}) {
  return request(path, { ...options, method: 'POST', body: body != null ? JSON.stringify(body) : undefined })
}
function patch(path, body, options = {}) {
  return request(path, { ...options, method: 'PATCH', body: JSON.stringify(body) })
}
function del(path, body, options = {}) {
  return request(path, {
    ...options,
    method: 'DELETE',
    body: body != null ? JSON.stringify(body) : undefined,
  })
}

// ─── 1. Chat (SSE) ─────────────────────────────────────────────────────────────

/**
 * Start an SSE chat stream.
 * @param {string} question
 * @param {string|null} sessionId
 * @param {object} callbacks – { onSession, onToolCall, onToolResult, onText, onDone, onError }
 * @returns {{ abort: Function }} controller to stop the stream client-side
 */
export function chatSSE(question, sessionId, callbacks = {}) {
  const controller = new AbortController()
  let aborted = false
  let terminalSeen = false

  ;(async () => {
    const abortState = createAbortController(REQUEST_TIMEOUT_MS, controller.signal)
    try {
      const body = { question }
      if (sessionId) body.session_id = sessionId
      const res = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: abortState.signal,
      })
      abortState.clearTimeout()
      if (!res.ok) {
        const errBody = await res.json().catch(() => null)
        callbacks.onError?.({ message: errorMessageFromBody(errBody, res.statusText || `HTTP ${res.status}`) })
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let currentEvent = ''
      let dataLines = []

      function dispatchEvent() {
        if (!currentEvent && dataLines.length === 0) return
        const raw = dataLines.join('\n')
        let data
        try { data = JSON.parse(raw) } catch { data = raw }
        switch (currentEvent) {
          case 'session': callbacks.onSession?.(data); break
          case 'tool_call': callbacks.onToolCall?.(data); break
          case 'tool_result': callbacks.onToolResult?.(data); break
          case 'text': callbacks.onText?.(data); break
          case 'done':
            terminalSeen = true
            callbacks.onDone?.(data)
            break
          case 'error':
            terminalSeen = true
            callbacks.onError?.(data)
            break
        }
        currentEvent = ''
        dataLines = []
      }

      function processLine(rawLine) {
        const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
        if (line === '') {
          dispatchEvent()
        } else if (line.startsWith('event:')) {
          currentEvent = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart())
        }
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          buffer += decoder.decode()
          break
        }
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() // keep incomplete line

        for (const line of lines) {
          processLine(line)
        }
      }
      if (buffer) processLine(buffer)
      dispatchEvent()
      if (!terminalSeen && !aborted) {
        callbacks.onError?.({ message: '连接已中断，请重试' })
      }
    } catch (err) {
      if (abortState.didTimeout()) {
        callbacks.onError?.({ message: `请求超时（${Math.round(REQUEST_TIMEOUT_MS / 1000)} 秒）。请确认后端服务可用后重试。` })
        return
      }
      const friendly = friendlyNetworkError(err)
      if (friendly.name !== 'AbortError' && !aborted) callbacks.onError?.({ message: friendly.message })
    } finally {
      abortState.cleanup()
    }
  })()

  return {
    abort: () => {
      aborted = true
      controller.abort()
    },
  }
}

/** Stop a running generation. */
export function abortChat(sessionId, payload = null, options = {}) {
  return post(`/chat/${pathSegment(sessionId)}/abort`, payload, options)
}

// ─── 2. Session Management ─────────────────────────────────────────────────────

export function getSessions(limit = 100, offset = 0, options = {}) {
  return get(`/chat/sessions?${queryString({ limit, offset })}`, options)
}

export function getMessages(sessionId, limit = 500, offset = 0, options = {}) {
  const query = queryString({ limit, offset })
  return get(`/chat/${pathSegment(sessionId)}/messages${query ? `?${query}` : ''}`, options)
}

export function getSessionStatus(sessionId, options = {}) {
  return get(`/chat/${pathSegment(sessionId)}/status`, options)
}

export function renameSession(sessionId, title, options = {}) {
  return patch(`/chat/${pathSegment(sessionId)}`, { title }, options)
}

export function deleteSession(sessionId, options = {}) {
  return del(`/chat/${pathSegment(sessionId)}`, null, options)
}

export function batchDeleteSessions(sessionIds, options = {}) {
  return post('/chat/sessions/delete', { session_ids: sessionIds }, options)
}

// ─── 3. Settings ────────────────────────────────────────────────────────────────

export function getSettings(options = {}) { return get('/settings', options) }
export function updateSettings(data, options = {}) { return post('/settings', data, options) }
export function resetSettings(options = {}) { return post('/settings/reset', null, options) }

// ─── 4. Stats ───────────────────────────────────────────────────────────────────

export function getStatsSummary(options = {}) { return get('/stats/summary', options) }
export function getStatsDetailed(params = {}, options = {}) {
  const q = queryString({ include_details: 'true', ...params })
  return get(`/stats?${q}`, options)
}
export function getThreads(limit = 50, offset = 0, options = {}) {
  return get(`/stats/threads?${queryString({ limit, offset })}`, options)
}
export function getSenders(limit = 50, offset = 0, options = {}) {
  return get(`/stats/senders?${queryString({ limit, offset })}`, options)
}

// ─── 5. Health ──────────────────────────────────────────────────────────────────

export function healthCheck(options = {}) { return get('/health', options) }
export function healthDiagnostics(options = {}) { return get('/health/diagnostics', options) }

// ─── 6. Ingest ──────────────────────────────────────────────────────────────────

export function getIngestFiles(limit = 100, offset = 0, options = {}) {
  return get(`/ingest/files?${queryString({ limit, offset })}`, options)
}

export async function getAllIngestFiles(pageSize = 500, options = {}) {
  const safePageSize = Math.min(500, Math.max(1, Number.parseInt(pageSize, 10) || 500))
  const items = []
  let offset = 0
  let total = null

  while (total === null || offset < total) {
    const page = await getIngestFiles(safePageSize, offset, options)
    const pageItems = page.items || []
    items.push(...pageItems)
    total = Number.isFinite(page.total_count) ? page.total_count : items.length
    if (pageItems.length === 0) break
    offset += pageItems.length
  }

  return {
    total_count: total ?? items.length,
    returned: items.length,
    offset: 0,
    items,
  }
}

export function uploadFile(file, options = {}) {
  const form = new FormData()
  form.append('file', file)
  const abortState = createAbortController(UPLOAD_TIMEOUT_MS, options.signal)
  return fetch(`${API}/ingest/upload`, { method: 'POST', body: form, signal: abortState.signal })
    .then(async (res) => {
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        const error = new Error(errorMessageFromBody(body, res.statusText || `HTTP ${res.status}`))
        error.status = res.status
        error.body = body
        throw error
      }
      return res.json()
    })
    .catch((err) => {
      if (abortState.didTimeout()) {
        throw new Error(`上传超时（${Math.round(UPLOAD_TIMEOUT_MS / 60000)} 分钟）。请检查文件大小和后端服务状态后重试。`)
      }
      throw friendlyNetworkError(err)
    })
    .finally(() => abortState.cleanup())
}

export function startIngest(params, options = {}) {
  return post('/ingest/start', params, options)
}

export function getIngestStatus(taskId, options = {}) {
  return get(`/ingest/status/${pathSegment(taskId)}`, options)
}

export function getIngestTasks(limit = 50, offset = 0, options = {}) {
  return get(`/ingest/tasks?${queryString({ limit, offset })}`, options)
}

export function cancelIngest(taskId, options = {}) {
  return post(`/ingest/tasks/${pathSegment(taskId)}/cancel`, null, options)
}

/**
 * Connect to WebSocket for real-time ingest progress.
 * @returns {{ close: Function }}
 */
export function connectIngestWS(taskId, callbacks = {}) {
  const ws = new WebSocket(wsUrl(`/ws/ingest/${pathSegment(taskId)}`))
  ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      callbacks.onMessage?.(data)
    } catch { /* ignore */ }
  }
  ws.onerror = () => callbacks.onError?.()
  ws.onclose = () => callbacks.onClose?.()
  return { close: () => ws.close() }
}

// ─── 7. Suggestions ─────────────────────────────────────────────────────────────

export function getSuggestions(query, limit = 10, options = {}) {
  return get(`/suggestions?${queryString({ query, limit })}`, options)
}

/**
 * Open a WebSocket for live suggestions.
 * @returns {{ send: Function, close: Function }}
 */
export function connectSuggestionsWS(callbacks = {}) {
  const ws = new WebSocket(wsUrl('/ws/suggestions'))
  ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      callbacks.onMessage?.(data)
    } catch { /* ignore */ }
  }
  ws.onerror = () => callbacks.onError?.()
  ws.onclose = () => callbacks.onClose?.()
  ws.onopen = () => callbacks.onOpen?.()
  return {
    send: (query, limit = 10) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ query, limit }))
      }
    },
    close: () => ws.close(),
  }
}

// ─── 8. Logs ────────────────────────────────────────────────────────────────────

export function getLogs(level = 'error', limit = 100, options = {}) {
  return get(`/logs?${queryString({ level, limit })}`, options)
}
