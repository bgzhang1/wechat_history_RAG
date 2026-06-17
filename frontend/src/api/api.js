/**
 * API service layer – wraps all backend endpoints.
 * Base URL defaults to http://localhost:8000.
 */

const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'
const API = `${BASE}/api`
const WS_BASE = BASE.replace(/^http/, 'ws')

// ─── helpers ────────────────────────────────────────────────────────────────────

async function request(path, options = {}) {
  const url = `${API}${path}`
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    let errBody
    try { errBody = await res.json() } catch { errBody = null }
    const msg = errBody?.error?.message || errBody?.detail || res.statusText
    const err = new Error(msg)
    err.status = res.status
    err.body = errBody
    throw err
  }
  return res.json()
}

function get(path) { return request(path) }
function post(path, body) {
  return request(path, { method: 'POST', body: body != null ? JSON.stringify(body) : undefined })
}
function patch(path, body) {
  return request(path, { method: 'PATCH', body: JSON.stringify(body) })
}
function del(path, body) {
  return request(path, {
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

  ;(async () => {
    try {
      const body = { question }
      if (sessionId) body.session_id = sessionId
      const res = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (!res.ok) {
        const errBody = await res.json().catch(() => null)
        callbacks.onError?.({ message: errBody?.error?.message || res.statusText })
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() // keep incomplete line

        let currentEvent = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            const raw = line.slice(6)
            let data
            try { data = JSON.parse(raw) } catch { data = raw }
            switch (currentEvent) {
              case 'session': callbacks.onSession?.(data); break
              case 'tool_call': callbacks.onToolCall?.(data); break
              case 'tool_result': callbacks.onToolResult?.(data); break
              case 'text': callbacks.onText?.(data); break
              case 'done': callbacks.onDone?.(data); break
              case 'error': callbacks.onError?.(data); break
            }
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') callbacks.onError?.({ message: err.message })
    }
  })()

  return { abort: () => controller.abort() }
}

/** Stop a running generation. */
export function abortChat(sessionId) {
  return post(`/chat/${sessionId}/abort`)
}

// ─── 2. Session Management ─────────────────────────────────────────────────────

export function getSessions(limit = 100, offset = 0) {
  return get(`/chat/sessions?limit=${limit}&offset=${offset}`)
}

export function getMessages(sessionId) {
  return get(`/chat/${sessionId}/messages`)
}

export function getSessionStatus(sessionId) {
  return get(`/chat/${sessionId}/status`)
}

export function renameSession(sessionId, title) {
  return patch(`/chat/${sessionId}`, { title })
}

export function deleteSession(sessionId) {
  return del(`/chat/${sessionId}`)
}

export function batchDeleteSessions(sessionIds) {
  return post('/chat/sessions/delete', { session_ids: sessionIds })
}

// ─── 3. Settings ────────────────────────────────────────────────────────────────

export function getSettings() { return get('/settings') }
export function updateSettings(data) { return post('/settings', data) }
export function resetSettings() { return post('/settings/reset') }

// ─── 4. Stats ───────────────────────────────────────────────────────────────────

export function getStatsSummary() { return get('/stats/summary') }
export function getStatsDetailed(params = {}) {
  const q = new URLSearchParams({ include_details: 'true', ...params })
  return get(`/stats?${q}`)
}
export function getThreads(limit = 50, offset = 0) {
  return get(`/stats/threads?limit=${limit}&offset=${offset}`)
}
export function getSenders(limit = 50, offset = 0) {
  return get(`/stats/senders?limit=${limit}&offset=${offset}`)
}

// ─── 5. Health ──────────────────────────────────────────────────────────────────

export function healthCheck() { return get('/health') }
export function healthDiagnostics() { return get('/health/diagnostics') }

// ─── 6. Ingest ──────────────────────────────────────────────────────────────────

export function getIngestFiles(limit = 100, offset = 0) {
  return get(`/ingest/files?limit=${limit}&offset=${offset}`)
}

export function uploadFile(file) {
  const form = new FormData()
  form.append('file', file)
  return fetch(`${API}/ingest/upload`, { method: 'POST', body: form })
    .then(async (res) => {
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.error?.message || res.statusText)
      }
      return res.json()
    })
}

export function startIngest(params) {
  return post('/ingest/start', params)
}

export function getIngestStatus(taskId) {
  return get(`/ingest/status/${taskId}`)
}

export function getIngestTasks(limit = 50, offset = 0) {
  return get(`/ingest/tasks?limit=${limit}&offset=${offset}`)
}

export function cancelIngest(taskId) {
  return post(`/ingest/tasks/${taskId}/cancel`)
}

/**
 * Connect to WebSocket for real-time ingest progress.
 * @returns {{ close: Function }}
 */
export function connectIngestWS(taskId, callbacks = {}) {
  const ws = new WebSocket(`${WS_BASE}/api/ws/ingest/${taskId}`)
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

export function getSuggestions(query, limit = 10) {
  return get(`/suggestions?query=${encodeURIComponent(query)}&limit=${limit}`)
}

/**
 * Open a WebSocket for live suggestions.
 * @returns {{ send: Function, close: Function }}
 */
export function connectSuggestionsWS(callbacks = {}) {
  const ws = new WebSocket(`${WS_BASE}/api/ws/suggestions`)
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

export function getLogs(level = 'error', limit = 100) {
  return get(`/logs?level=${level}&limit=${limit}`)
}
