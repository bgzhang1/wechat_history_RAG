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
        <div class="form-group">
          <label class="form-label">对话模型</label>
          <input class="input" v-model="form.chat_model" placeholder="gpt-4o" id="input-chat-model" />
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
          {{ saving ? '保存中…' : '保存设置' }}
        </button>
        <button class="btn btn-secondary" @click="reset" :disabled="saving" id="btn-reset-settings">
          恢复默认
        </button>
      </div>
      <p class="persist-note text-xs text-muted">设置保存后会在后端重启后继续生效；API Key 仍只从本地环境变量读取。</p>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, inject } from 'vue'
import { getSettings, updateSettings, resetSettings } from '../api/api.js'

const toast = inject('toast')
const loading = ref(true)
const saving = ref(false)
const loadError = ref('')
let componentDisposed = false
let activeSettingsLoadController = null
let activeSettingsMutationController = null
let settingsLoadSeq = 0

const DEFAULT_AVAILABLE_TOOLS = ['search_messages', 'semantic_search', 'get_context', 'browse_by_time', 'get_stats']
const availableTools = ref([...DEFAULT_AVAILABLE_TOOLS])
const NUMBER_FIELDS = {
  max_rounds: { label: '最大轮数', min: 1, max: 200, integer: true },
  max_history_messages: { label: '历史消息上限', min: 0, max: 200, integer: true },
  chat_timeout: { label: '超时时间', min: 1, max: 1800, integer: false },
  chat_temperature: { label: '温度', min: 0, max: 2, integer: false },
}

const form = reactive({
  system_prompt: '',
  max_rounds: 12,
  max_history_messages: 40,
  chat_model: '',
  chat_timeout: 300,
  chat_temperature: 0,
  enabled_tools: [],
})

onMounted(() => {
  componentDisposed = false
  loadSettings()
})

onUnmounted(() => {
  componentDisposed = true
  activeSettingsLoadController?.abort()
  activeSettingsLoadController = null
  activeSettingsMutationController?.abort()
  activeSettingsMutationController = null
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
    Object.assign(form, data)
    form.enabled_tools = enabledToolsWithinAvailable(form.enabled_tools, availableTools.value)
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
    Object.assign(form, data)
    form.enabled_tools = enabledToolsWithinAvailable(form.enabled_tools, availableTools.value)
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
  if (!confirm('确定要恢复默认设置？')) return
  activeSettingsMutationController?.abort()
  const controller = new AbortController()
  activeSettingsMutationController = controller
  saving.value = true
  try {
    const data = await resetSettings({ signal: controller.signal })
    if (componentDisposed) return
    availableTools.value = toolsFromSettingsResponse(data)
    Object.assign(form, data)
    form.enabled_tools = enabledToolsWithinAvailable(form.enabled_tools, availableTools.value)
    toast('已恢复默认设置', 'success')
  } catch (e) {
    if (componentDisposed) return
    toast(e.message, 'error')
  } finally {
    if (activeSettingsMutationController === controller) activeSettingsMutationController = null
    if (!componentDisposed) saving.value = false
  }
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
    chat_model: String(form.chat_model || '').trim(),
    enabled_tools: enabledTools,
  }
  for (const [field, rule] of Object.entries(NUMBER_FIELDS)) {
    const value = normalizeNumberField(field, rule)
    if (value == null) return null
    payload[field] = value
  }
  return payload
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
  padding: var(--space-6);
  background: var(--surface-glass);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
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
  gap: var(--space-5);
  min-inline-size: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

.full-width {
  grid-column: 1 / -1;
}

/* Slider */
.slider-wrap {
  padding: var(--space-1) 0;
}

.slider {
  width: 100%;
  height: 6px;
  appearance: none;
  -webkit-appearance: none;
  background: rgba(255, 255, 255, 0.08);
  border-radius: var(--radius-full);
  outline: none;
  cursor: pointer;
}

.slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--gradient-brand);
  cursor: pointer;
  border: 2px solid var(--bg-primary);
  box-shadow: var(--shadow-glow-blue);
}

.slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--gradient-brand);
  cursor: pointer;
  border: 2px solid var(--bg-primary);
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
  background: var(--surface-glass);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.tool-checkbox:hover {
  background: var(--surface-glass-hover);
  border-color: var(--border-default);
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
  padding-top: var(--space-6);
  border-top: 1px solid var(--border-subtle);
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

  .form-actions {
    flex-direction: column;
  }
}
</style>
