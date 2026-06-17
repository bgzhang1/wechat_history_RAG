<template>
  <div class="panel animate-fade-in">
    <div v-if="loading" class="panel-loading"><div class="spinner"></div></div>

    <template v-else>
      <div class="form-grid">
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
          <input type="number" class="input" v-model.number="form.max_rounds" min="1" max="1000" id="input-max-rounds" />
          <span class="form-help">Agent 每次对话最多执行的工具调用轮数</span>
        </div>

        <!-- Max History -->
        <div class="form-group">
          <label class="form-label">历史消息上限</label>
          <input type="number" class="input" v-model.number="form.max_history_messages" min="1" max="200" id="input-max-history" />
          <span class="form-help">传递给模型的历史消息条数</span>
        </div>

        <!-- Chat Timeout -->
        <div class="form-group">
          <label class="form-label">超时时间 (秒)</label>
          <input type="number" class="input" v-model.number="form.chat_timeout" min="10" max="600" id="input-timeout" />
        </div>

        <!-- Enabled Tools -->
        <div class="form-group full-width">
          <label class="form-label">启用的工具</label>
          <div class="tools-grid">
            <label v-for="tool in allTools" :key="tool" class="tool-checkbox">
              <input type="checkbox" :value="tool" v-model="form.enabled_tools" />
              <span class="tool-name">{{ tool }}</span>
            </label>
          </div>
        </div>
      </div>

      <div class="form-actions">
        <button class="btn btn-primary" @click="save" :disabled="saving" id="btn-save-settings">
          <div v-if="saving" class="spinner" style="width:14px;height:14px;border-width:1.5px;"></div>
          {{ saving ? '保存中…' : '保存设置' }}
        </button>
        <button class="btn btn-secondary" @click="reset" :disabled="saving" id="btn-reset-settings">
          恢复默认
        </button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, inject } from 'vue'
import { getSettings, updateSettings, resetSettings } from '../api/api.js'

const toast = inject('toast')
const loading = ref(true)
const saving = ref(false)

const allTools = ['search_messages', 'semantic_search', 'get_context', 'browse_by_time', 'get_stats']

const form = reactive({
  system_prompt: '',
  max_rounds: 100,
  max_history_messages: 40,
  chat_model: '',
  chat_timeout: 300,
  chat_temperature: 0,
  enabled_tools: [],
})

onMounted(async () => {
  try {
    const data = await getSettings()
    Object.assign(form, data)
  } catch (e) {
    toast(e.message, 'error')
  }
  loading.value = false
})

async function save() {
  saving.value = true
  try {
    const data = await updateSettings({ ...form })
    Object.assign(form, data)
    toast('设置已保存', 'success')
  } catch (e) {
    toast(e.message, 'error')
  }
  saving.value = false
}

async function reset() {
  if (!confirm('确定要恢复默认设置？')) return
  saving.value = true
  try {
    const data = await resetSettings()
    Object.assign(form, data)
    toast('已恢复默认设置', 'success')
  } catch (e) {
    toast(e.message, 'error')
  }
  saving.value = false
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

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-5);
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
</style>
