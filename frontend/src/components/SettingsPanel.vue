<template>
  <div class="panel animate-fade-in">
    <div v-if="loading" class="panel-loading"><div class="spinner"></div></div>

    <div v-else-if="loadError" class="error-state glass-card">
      <span class="text-sm">{{ loadError }}</span>
      <button class="btn btn-ghost btn-sm" @click="loadSettings" id="btn-retry-settings">
        重试
      </button>
    </div>

    <template v-else>
      <fieldset class="form-grid" :disabled="saving">
        <!-- System Prompt -->
        <div class="form-group full-width">
          <label class="form-label">系统提示词</label>
          <textarea class="input" v-model="form.system_prompt" rows="4" id="input-system-prompt"></textarea>
          <span class="form-help">定义 Agent 的身份和行为规则</span>
        </div>

        <!-- Chat Model -->
        <div class="form-group model-field">
          <label class="form-label">对话模型</label>
          <div class="model-picker">
            <input class="input" v-model="form.chat_model" list="chat-model-options" placeholder="选择或输入模型名称" id="input-chat-model" />
            <button type="button" class="icon-btn model-refresh" :class="{ loading: modelLoading.chat }" @click="loadModelOptions('chat')" :disabled="modelLoading.chat || saving" title="刷新模型列表">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M3 12a9 9 0 0 1 15.1-6.6"/>
                <path d="M18 2v6h-6"/>
                <path d="M21 12a9 9 0 0 1-15.1 6.6"/>
                <path d="M6 22v-6h6"/>
              </svg>
            </button>
          </div>
          <datalist id="chat-model-options">
            <option v-for="model in modelOptions.chat" :key="model" :value="model" />
          </datalist>
          <div class="effort-control" aria-label="对话思考强度">
            <span class="effort-label">思考强度</span>
            <label v-for="option in chatEffortOptions" :key="option.value" class="effort-option">
              <input type="radio" v-model="form.chat_reasoning_effort" :value="option.value" />
              <span>{{ option.label }}</span>
            </label>
          </div>
          <span class="form-help">{{ modelErrors.chat || '用于实时问答，可从接口列表选择，也可以直接输入自定义模型名' }}</span>
        </div>

        <!-- Summary Model -->
        <div class="form-group model-field">
          <label class="form-label">摘要模型</label>
          <div class="model-picker">
            <input class="input" v-model="form.summary_model" list="summary-model-options" placeholder="选择或输入模型名称" id="input-summary-model" />
            <button type="button" class="icon-btn model-refresh" :class="{ loading: modelLoading.summary }" @click="loadModelOptions('summary')" :disabled="modelLoading.summary || saving" title="刷新模型列表">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M3 12a9 9 0 0 1 15.1-6.6"/>
                <path d="M18 2v6h-6"/>
                <path d="M21 12a9 9 0 0 1-15.1 6.6"/>
                <path d="M6 22v-6h6"/>
              </svg>
            </button>
          </div>
          <datalist id="summary-model-options">
            <option v-for="model in modelOptions.summary" :key="model" :value="model" />
          </datalist>
          <div class="effort-control" aria-label="摘要思考强度">
            <span class="effort-label">思考强度</span>
            <label v-for="option in summaryEffortOptions" :key="option.value" class="effort-option">
              <input type="radio" v-model="summaryEffortChoice" :value="option.value" />
              <span>{{ option.label }}</span>
            </label>
          </div>
          <span class="form-help">{{ modelErrors.summary || '用于整理长聊天记录，留空的摘要连接信息会继承对话模型配置' }}</span>
        </div>

        <!-- Temperature -->
        <div class="form-group">
          <label class="form-label">温度 (Temperature): {{ form.chat_temperature }}</label>
          <div class="slider-wrap">
            <input type="range" min="0" max="2" step="0.1" v-model.number="form.chat_temperature" class="slider" id="input-temperature" />
            <div class="slider-labels">
              <span>精确 0</span><span>2 创意</span>
            </div>
          </div>
        </div>

        <!-- Max Rounds -->
        <div class="form-group">
          <label class="form-label">最大轮数</label>
          <input type="number" class="input" v-model.number="form.max_rounds" min="1" max="200" id="input-max-rounds" />
          <span class="form-help">Agent 每次对话最多执行的工具调用轮数</span>
        </div>

        <!-- Max History -->
        <div class="form-group">
          <label class="form-label">历史消息上限</label>
          <input type="number" class="input" v-model.number="form.max_history_messages" min="0" max="200" id="input-max-history" />
          <span class="form-help">传递给模型的历史消息条数</span>
        </div>

        <!-- Chat Timeout -->
        <div class="form-group">
          <label class="form-label">超时时间 (秒)</label>
          <input type="number" class="input" v-model.number="form.chat_timeout" min="1" max="1800" id="input-timeout" />
        </div>

        <section class="advanced-settings full-width" aria-labelledby="advanced-settings-title">
          <div class="section-header">
            <h3 id="advanced-settings-title">高级设置</h3>
            <span class="section-note">Base URL、线程、批次和索引模型</span>
          </div>

          <div class="key-status-row">
            <span class="key-status" :class="{ configured: form.chat_api_key_configured }">
              Chat API Key {{ form.chat_api_key_configured ? '已配置' : '未配置' }}
            </span>
            <span class="key-status" :class="{ configured: form.summary_api_key_configured }">
              Summary API Key {{ summaryKeyLabel }}
            </span>
            <span class="key-status" :class="{ configured: form.embed_api_key_configured }">
              Embedding API Key {{ form.embed_api_key_configured ? '已配置' : '未配置' }}
            </span>
          </div>

          <div class="advanced-grid">
            <div class="form-group">
              <label class="form-label">对话 Base URL</label>
              <input class="input" v-model="form.chat_base_url" placeholder="https://api.openai.com/v1" id="input-chat-base-url" />
            </div>
            <div class="form-group">
              <label class="form-label">对话 API Key</label>
              <input type="password" class="input" v-model="form.chat_api_key" autocomplete="new-password" placeholder="输入新 Key 后保存" id="input-chat-api-key" />
              <label class="inline-toggle">
                <input type="checkbox" v-model="form.clear_chat_api_key" />
                <span>清除运行时 Key</span>
              </label>
            </div>
            <div class="form-group">
              <label class="form-label">摘要 Base URL</label>
              <input class="input" v-model="form.summary_base_url" :disabled="form.summary_use_chat_base_url" placeholder="留空继承对话 Base URL" id="input-summary-base-url" />
              <label class="inline-toggle">
                <input type="checkbox" v-model="form.summary_use_chat_base_url" />
                <span>继承对话 Base URL</span>
              </label>
            </div>
            <div class="form-group">
              <label class="form-label">摘要 API Key</label>
              <input type="password" class="input" v-model="form.summary_api_key" :disabled="form.summary_use_chat_api_key" autocomplete="new-password" placeholder="输入摘要专用 Key" id="input-summary-api-key" />
              <label class="inline-toggle">
                <input type="checkbox" v-model="form.summary_use_chat_api_key" />
                <span>继承对话 API Key</span>
              </label>
            </div>
            <div class="form-group">
              <label class="form-label">对话重试次数</label>
              <input type="number" class="input" v-model.number="form.chat_max_retries" min="0" max="10" id="input-chat-max-retries" />
            </div>
            <div class="form-group">
              <label class="form-label">摘要线程数</label>
              <input type="number" class="input" v-model.number="form.summary_workers" min="1" max="32" id="input-summary-workers" />
            </div>
            <div class="form-group">
              <label class="form-label">摘要批次大小</label>
              <input type="number" class="input" v-model.number="form.summary_batch_size" min="1" max="128" id="input-summary-batch-size" />
            </div>
            <div class="form-group">
              <label class="form-label">摘要最大字符</label>
              <input type="number" class="input" v-model.number="form.summary_max_chars" min="100" max="20000" id="input-summary-max-chars" />
            </div>
            <div class="form-group">
              <label class="form-label">摘要回退字符</label>
              <input type="number" class="input" v-model.number="form.summary_fallback_chars" min="0" max="20000" id="input-summary-fallback-chars" />
            </div>
            <div class="form-group">
              <label class="form-label">Embedding Base URL</label>
              <input class="input" v-model="form.embed_base_url" placeholder="https://api.openai.com/v1" id="input-embed-base-url" />
            </div>
            <div class="form-group">
              <label class="form-label">Embedding 模型</label>
              <input class="input" v-model="form.embed_model" placeholder="text-embedding-3-small" id="input-embed-model" />
            </div>
            <div class="form-group">
              <label class="form-label">Embedding 超时 (秒)</label>
              <input type="number" class="input" v-model.number="form.embed_timeout" min="1" max="1800" id="input-embed-timeout" />
            </div>
            <div class="form-group">
              <label class="form-label">Embedding 重试次数</label>
              <input type="number" class="input" v-model.number="form.embed_max_retries" min="0" max="10" id="input-embed-max-retries" />
            </div>
            <div class="form-group">
              <label class="form-label">Embedding 线程数</label>
              <input type="number" class="input" v-model.number="form.embed_workers" min="1" max="64" id="input-embed-workers" />
            </div>
            <div class="form-group">
              <label class="form-label">Embedding 批次大小</label>
              <input type="number" class="input" v-model.number="form.embed_batch_size" min="1" max="512" id="input-embed-batch-size" />
            </div>
          </div>
        </section>

        <!-- Enabled Tools -->
        <div class="form-group full-width">
          <label class="form-label">启用的工具</label>
          <div class="tools-grid">
            <label v-for="tool in availableTools" :key="tool" class="tool-checkbox">
              <input type="checkbox" :value="tool" v-model="form.enabled_tools" />
              <span class="tool-name">{{ tool }}</span>
            </label>
          </div>
        </div>
      </fieldset>

      <div class="form-actions">
        <button class="btn btn-primary" @click="save" :disabled="saving" id="btn-save-settings">
          <div v-if="saving" class="spinner spinner-save"></div>
          <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
            <path d="M17 21v-8H7v8"/>
            <path d="M7 3v5h8"/>
          </svg>
          {{ saving ? '保存中…' : '保存设置' }}
        </button>
        <button class="btn btn-secondary" @click="openResetConfirm" :disabled="saving" id="btn-reset-settings">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 12a9 9 0 1 0 3-6.7"/>
            <path d="M3 3v6h6"/>
          </svg>
          恢复默认
        </button>
      </div>
      <p class="persist-note text-xs text-muted">设置保存后会在后端重启后继续生效；在页面输入的 API Key 不会回显。</p>
    </template>
  </div>

  <ConfirmDialog
    v-if="resetConfirmOpen"
    title="恢复默认设置？"
    message="这会把 Agent 配置恢复为默认值，但不会修改本地环境变量中的 API Key。"
    confirm-text="恢复默认"
    busy-text="恢复中…"
    :busy="saving"
    @confirm="reset"
    @cancel="closeResetConfirm"
  />
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, inject } from 'vue'
import ConfirmDialog from './ConfirmDialog.vue'
import { getSettings, updateSettings, resetSettings, getModelOptions } from '../api/api.js'

const toast = inject('toast')
const loading = ref(true)
const saving = ref(false)
const loadError = ref('')
const resetConfirmOpen = ref(false)
let componentDisposed = false
let activeSettingsLoadController = null
let activeSettingsMutationController = null
const activeModelControllers = {}
let settingsLoadSeq = 0

const DEFAULT_AVAILABLE_TOOLS = ['search_messages', 'semantic_search', 'get_context', 'browse_by_time', 'get_stats']
const availableTools = ref([...DEFAULT_AVAILABLE_TOOLS])
const modelOptions = reactive({ chat: [], summary: [] })
const modelLoading = reactive({ chat: false, summary: false })
const modelErrors = reactive({ chat: '', summary: '' })
const chatEffortOptions = [
  { value: '', label: '自动' },
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' },
]
const summaryEffortOptions = [
  { value: 'inherit', label: '继承' },
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' },
]
const summaryKeyLabel = computed(() => {
  if (!form.summary_api_key_configured) return '未配置'
  return form.summary_api_key_inherited ? '继承对话 Key' : '已单独配置'
})
const summaryEffortChoice = computed({
  get() {
    return form.summary_use_chat_reasoning_effort ? 'inherit' : form.summary_reasoning_effort
  },
  set(value) {
    form.summary_use_chat_reasoning_effort = value === 'inherit'
    form.summary_reasoning_effort = value === 'inherit' ? '' : value
  },
})
const NUMBER_FIELDS = {
  max_rounds: { label: '最大轮数', min: 1, max: 200, integer: true },
  max_history_messages: { label: '历史消息上限', min: 0, max: 200, integer: true },
  chat_timeout: { label: '超时时间', min: 1, max: 1800, integer: false },
  chat_temperature: { label: '温度', min: 0, max: 2, integer: false },
  chat_max_retries: { label: '对话重试次数', min: 0, max: 10, integer: true },
  summary_workers: { label: '摘要线程数', min: 1, max: 32, integer: true },
  summary_batch_size: { label: '摘要批次大小', min: 1, max: 128, integer: true },
  summary_max_chars: { label: '摘要最大字符', min: 100, max: 20000, integer: true },
  summary_fallback_chars: { label: '摘要回退字符', min: 0, max: 20000, integer: true },
  embed_timeout: { label: 'Embedding 超时', min: 1, max: 1800, integer: false },
  embed_max_retries: { label: 'Embedding 重试次数', min: 0, max: 10, integer: true },
  embed_workers: { label: 'Embedding 线程数', min: 1, max: 64, integer: true },
  embed_batch_size: { label: 'Embedding 批次大小', min: 1, max: 512, integer: true },
}

const form = reactive({
  system_prompt: '',
  max_rounds: 12,
  max_history_messages: 40,
  chat_model: '',
  chat_reasoning_effort: '',
  chat_base_url: '',
  chat_api_key: '',
  clear_chat_api_key: false,
  chat_max_retries: 3,
  summary_base_url: '',
  summary_api_key: '',
  summary_use_chat_base_url: true,
  summary_use_chat_api_key: true,
  summary_model: '',
  summary_reasoning_effort: '',
  summary_use_chat_reasoning_effort: true,
  summary_workers: 2,
  summary_batch_size: 4,
  summary_max_chars: 3000,
  summary_fallback_chars: 1200,
  embed_base_url: '',
  embed_model: '',
  embed_timeout: 90,
  embed_max_retries: 0,
  embed_workers: 4,
  embed_batch_size: 32,
  chat_timeout: 300,
  chat_temperature: 0,
  enabled_tools: [],
  chat_api_key_configured: false,
  summary_api_key_configured: false,
  summary_api_key_inherited: true,
  summary_base_url_inherited: true,
  summary_reasoning_effort_inherited: true,
  embed_api_key_configured: false,
})

onMounted(() => {
  componentDisposed = false
  loadSettings()
})

onUnmounted(() => {
  componentDisposed = true
  resetConfirmOpen.value = false
  activeSettingsLoadController?.abort()
  activeSettingsLoadController = null
  activeSettingsMutationController?.abort()
  activeSettingsMutationController = null
  Object.values(activeModelControllers).forEach(controller => controller?.abort?.())
})

async function loadSettings() {
  const seq = ++settingsLoadSeq
  activeSettingsLoadController?.abort()
  const controller = new AbortController()
  activeSettingsLoadController = controller
  loading.value = true
  try {
    const data = await getSettings({ signal: controller.signal })
    if (componentDisposed || seq !== settingsLoadSeq) return
    availableTools.value = toolsFromSettingsResponse(data)
    hydrateForm(data)
    loadModelOptions('chat', { silent: true })
    loadModelOptions('summary', { silent: true })
    loadError.value = ''
  } catch (e) {
    if (componentDisposed || seq !== settingsLoadSeq) return
    loadError.value = `设置加载失败：${e.message}`
    toast(e.message, 'error')
  } finally {
    if (activeSettingsLoadController === controller) activeSettingsLoadController = null
    if (!componentDisposed && seq === settingsLoadSeq) loading.value = false
  }
}

async function save() {
  if (saving.value) return
  const payload = buildSettingsPayload()
  if (!payload) return
  activeSettingsMutationController?.abort()
  const controller = new AbortController()
  activeSettingsMutationController = controller
  saving.value = true
  try {
    const data = await updateSettings(payload, { signal: controller.signal })
    if (componentDisposed) return
    availableTools.value = toolsFromSettingsResponse(data)
    hydrateForm(data)
    loadModelOptions('chat', { silent: true })
    loadModelOptions('summary', { silent: true })
    toast('设置已保存', 'success')
  } catch (e) {
    if (componentDisposed) return
    toast(e.message, 'error')
  } finally {
    if (activeSettingsMutationController === controller) activeSettingsMutationController = null
    if (!componentDisposed) saving.value = false
  }
}

async function reset() {
  if (saving.value) return
  activeSettingsMutationController?.abort()
  const controller = new AbortController()
  activeSettingsMutationController = controller
  saving.value = true
  try {
    const data = await resetSettings({ signal: controller.signal })
    if (componentDisposed) return
    availableTools.value = toolsFromSettingsResponse(data)
    hydrateForm(data)
    resetConfirmOpen.value = false
    toast('已恢复默认设置', 'success')
  } catch (e) {
    if (componentDisposed) return
    toast(e.message, 'error')
  } finally {
    if (activeSettingsMutationController === controller) activeSettingsMutationController = null
    if (!componentDisposed) saving.value = false
  }
}

function openResetConfirm() {
  if (saving.value) return
  resetConfirmOpen.value = true
}

function closeResetConfirm() {
  if (saving.value) return
  resetConfirmOpen.value = false
}

function buildSettingsPayload() {
  const systemPrompt = String(form.system_prompt || '').trim()
  if (!systemPrompt) {
    toast('系统提示词不能为空', 'error')
    return null
  }
  if (!Array.isArray(form.enabled_tools) || form.enabled_tools.length === 0) {
    toast('至少需要启用一个检索工具', 'error')
    return null
  }
  const enabledTools = enabledToolsWithinAvailable(form.enabled_tools, availableTools.value)
  if (enabledTools.length === 0) {
    toast('至少需要启用一个当前可用的检索工具', 'error')
    return null
  }

  const payload = {
    system_prompt: systemPrompt,
    chat_base_url: String(form.chat_base_url || '').trim(),
    chat_model: String(form.chat_model || '').trim(),
    chat_reasoning_effort: normalizeReasoningEffort(form.chat_reasoning_effort),
    summary_base_url: form.summary_use_chat_base_url ? '' : String(form.summary_base_url || '').trim(),
    summary_model: String(form.summary_model || '').trim(),
    summary_reasoning_effort: form.summary_use_chat_reasoning_effort ? '' : normalizeReasoningEffort(form.summary_reasoning_effort),
    embed_base_url: String(form.embed_base_url || '').trim(),
    embed_model: String(form.embed_model || '').trim(),
    enabled_tools: enabledTools,
  }
  const chatApiKey = String(form.chat_api_key || '').trim()
  if (chatApiKey || form.clear_chat_api_key) payload.chat_api_key = chatApiKey

  const summaryApiKey = String(form.summary_api_key || '').trim()
  if (form.summary_use_chat_api_key) {
    payload.summary_api_key = ''
  } else if (summaryApiKey) {
    payload.summary_api_key = summaryApiKey
  }

  for (const [field, rule] of Object.entries(NUMBER_FIELDS)) {
    const value = normalizeNumberField(field, rule)
    if (value == null) return null
    payload[field] = value
  }
  return payload
}

function hydrateForm(data) {
  Object.assign(form, data)
  form.enabled_tools = enabledToolsWithinAvailable(form.enabled_tools, availableTools.value)
  form.chat_api_key = ''
  form.summary_api_key = ''
  form.clear_chat_api_key = false
  form.summary_use_chat_base_url = Boolean(data?.summary_base_url_inherited)
  form.summary_use_chat_api_key = Boolean(data?.summary_api_key_inherited)
  form.chat_reasoning_effort = normalizeReasoningEffort(data?.chat_reasoning_effort)
  form.summary_reasoning_effort = normalizeReasoningEffort(data?.summary_reasoning_effort)
  form.summary_use_chat_reasoning_effort = Boolean(data?.summary_reasoning_effort_inherited)
}

async function loadModelOptions(target, options = {}) {
  if (!['chat', 'summary'].includes(target)) return
  activeModelControllers[target]?.abort()
  const controller = new AbortController()
  activeModelControllers[target] = controller
  modelLoading[target] = true
  modelErrors[target] = ''
  try {
    const data = await getModelOptions(target, { signal: controller.signal, timeoutMs: 20000 })
    if (componentDisposed || activeModelControllers[target] !== controller) return
    modelOptions[target] = normalizeModelOptions(data?.items)
    if (!options.silent) toast('模型列表已刷新', 'success')
  } catch (e) {
    if (componentDisposed || activeModelControllers[target] !== controller || e?.name === 'AbortError') return
    modelErrors[target] = e.message || '模型列表加载失败'
    if (!options.silent) toast(modelErrors[target], 'error')
  } finally {
    if (activeModelControllers[target] === controller) activeModelControllers[target] = null
    if (!componentDisposed) modelLoading[target] = false
  }
}

function normalizeNumberField(field, rule) {
  if (form[field] === '' || form[field] == null) {
    toast(`${rule.label}必须是数字`, 'error')
    return null
  }
  const value = Number(form[field])
  if (!Number.isFinite(value)) {
    toast(`${rule.label}必须是数字`, 'error')
    return null
  }
  if (value < rule.min || value > rule.max) {
    toast(`${rule.label}必须在 ${rule.min} 到 ${rule.max} 之间`, 'error')
    return null
  }
  if (rule.integer && !Number.isInteger(value)) {
    toast(`${rule.label}必须是整数`, 'error')
    return null
  }
  return value
}

function normalizeToolNames(values) {
  if (!Array.isArray(values)) return []
  return [...new Set(values.map(tool => String(tool || '').trim()).filter(Boolean))]
}

function normalizeModelOptions(values) {
  if (!Array.isArray(values)) return []
  return [...new Set(values.map(model => String(model || '').trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b))
}

function normalizeReasoningEffort(value) {
  const normalized = String(value || '').trim().toLowerCase()
  return ['low', 'medium', 'high'].includes(normalized) ? normalized : ''
}

function toolsFromSettingsResponse(data) {
  const backendTools = normalizeToolNames(data?.available_tools)
  if (backendTools.length > 0) return backendTools
  return normalizeToolNames([...DEFAULT_AVAILABLE_TOOLS, ...normalizeToolNames(data?.enabled_tools)])
}

function enabledToolsWithinAvailable(enabledTools, tools) {
  const available = new Set(normalizeToolNames(tools))
  return normalizeToolNames(enabledTools).filter(tool => available.has(tool))
}
</script>

<style scoped>
.panel {
  padding: var(--space-5);
  background: color-mix(in srgb, var(--bg-elevated) 82%, transparent);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
}

.panel-loading {
  display: flex;
  justify-content: center;
  padding: var(--space-12);
}

.error-state {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  color: var(--accent-red);
  border-color: rgba(239, 68, 68, 0.35);
}

.error-state .btn {
  flex-shrink: 0;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
  min-inline-size: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

.full-width {
  grid-column: 1 / -1;
}

.form-group {
  padding: var(--space-4);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--field-bg);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.form-group:focus-within {
  border-color: color-mix(in srgb, var(--accent-blue) 28%, var(--border-default));
  background: var(--field-bg-focus);
  box-shadow: var(--shadow-sm);
}

.form-group .form-label {
  color: var(--text-primary);
  font-size: 0.82rem;
  font-weight: 680;
}

.form-group .form-help {
  max-width: 60ch;
  line-height: 1.55;
}

.model-field {
  position: relative;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--accent-blue) 7%, transparent), transparent 46%),
    var(--field-bg-accent);
}

.model-field::before {
  content: '';
  position: absolute;
  left: -1px;
  top: var(--space-4);
  bottom: var(--space-4);
  width: 3px;
  border-radius: var(--radius-full);
  background: var(--gradient-brand);
}

.model-picker {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 42px;
  gap: var(--space-2);
  align-items: center;
}

.model-picker .input {
  min-width: 0;
}

.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  background: color-mix(in srgb, var(--bg-elevated) 84%, transparent);
  cursor: pointer;
  transition: color var(--transition-fast), border-color var(--transition-fast), background var(--transition-fast), transform var(--transition-fast);
}

.icon-btn:hover:not(:disabled) {
  color: var(--accent-blue);
  border-color: color-mix(in srgb, var(--accent-blue) 34%, var(--border-default));
  background: color-mix(in srgb, var(--accent-blue) 8%, var(--bg-elevated));
}

.icon-btn:disabled {
  cursor: not-allowed;
  opacity: 0.62;
}

.model-refresh.loading svg {
  animation: spin 0.8s linear infinite;
}

.effort-control {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.effort-label {
  color: var(--text-muted);
  font-size: var(--text-xs);
  font-weight: 650;
}

.effort-option {
  position: relative;
  display: inline-flex;
  align-items: center;
  min-height: 30px;
  cursor: pointer;
}

.effort-option input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.effort-option span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 42px;
  height: 30px;
  padding: 0 var(--space-3);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--bg-elevated) 70%, transparent);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-weight: 650;
  transition: color var(--transition-fast), border-color var(--transition-fast), background var(--transition-fast), box-shadow var(--transition-fast);
}

.effort-option:hover span {
  border-color: color-mix(in srgb, var(--accent-blue) 30%, var(--border-default));
  color: var(--text-primary);
}

.effort-option input:checked + span {
  border-color: color-mix(in srgb, var(--accent-blue) 48%, var(--border-default));
  background: color-mix(in srgb, var(--accent-blue) 12%, var(--bg-elevated));
  color: var(--accent-blue);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent-blue) 20%, transparent);
}

.advanced-settings {
  padding: var(--space-5);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--bg-tertiary) 44%, transparent);
}

.section-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.section-header h3 {
  margin: 0;
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--text-primary);
}

.section-note {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.key-status-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}

.key-status {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 var(--space-3);
  border: 1px solid rgba(239, 68, 68, 0.35);
  border-radius: var(--radius-full);
  color: var(--accent-red);
  font-size: var(--text-xs);
}

.key-status.configured {
  border-color: rgba(34, 197, 94, 0.35);
  color: var(--accent-green);
}

.inline-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
  color: var(--text-muted);
  font-size: var(--text-xs);
  cursor: pointer;
}

.inline-toggle input {
  width: 16px;
  height: 16px;
  accent-color: var(--accent-blue);
}

.advanced-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  min-inline-size: 0;
}

/* Slider */
.slider-wrap {
  padding: var(--space-2) 0 0;
}

.slider {
  width: 100%;
  height: 8px;
  appearance: none;
  -webkit-appearance: none;
  background:
    linear-gradient(90deg, var(--accent-blue), var(--accent-green)),
    color-mix(in srgb, var(--text-muted) 16%, transparent);
  border-radius: var(--radius-full);
  outline: none;
  cursor: pointer;
}

.slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--bg-elevated);
  cursor: pointer;
  border: 5px solid var(--accent-blue);
  box-shadow: 0 8px 22px rgba(37, 99, 235, 0.22);
}

.slider::-moz-range-thumb {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--bg-elevated);
  cursor: pointer;
  border: 5px solid var(--accent-blue);
}

.slider-labels {
  display: flex;
  justify-content: space-between;
  font-size: var(--text-xs);
  color: var(--text-muted);
  margin-top: var(--space-1);
}

/* Tools */
.tools-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.tool-checkbox {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: color-mix(in srgb, var(--bg-elevated) 72%, transparent);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.tool-checkbox:hover {
  background: color-mix(in srgb, var(--accent-blue) 7%, var(--bg-elevated));
  border-color: color-mix(in srgb, var(--accent-blue) 24%, var(--border-default));
}

.tool-checkbox input {
  accent-color: var(--accent-blue);
}

.tool-name {
  font-size: var(--text-sm);
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

/* Actions */
.form-actions {
  display: flex;
  gap: var(--space-3);
  margin-top: var(--space-6);
  padding-top: var(--space-5);
  border-top: 1px solid var(--border-subtle);
  justify-content: flex-end;
  align-items: center;
}

.form-actions .btn {
  min-width: 132px;
}

.persist-note {
  margin-top: var(--space-3);
}

.spinner-save {
  width: 14px;
  height: 14px;
  border-width: 1.5px;
}

@media (max-width: 768px) {
  .panel {
    padding: var(--space-4);
  }

  .form-grid {
    grid-template-columns: 1fr;
  }

  .advanced-grid {
    grid-template-columns: 1fr;
  }

  .section-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .form-actions {
    flex-direction: column;
  }
}
</style>
