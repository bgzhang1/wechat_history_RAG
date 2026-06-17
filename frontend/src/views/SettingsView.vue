<template>
  <div class="settings-layout">
    <div class="settings-container animate-fade-in">
      <div class="settings-header">
        <h1 class="settings-title">系统设置</h1>
        <p class="text-secondary text-sm">管理 Agent 配置、数据导入、系统健康和错误日志</p>
      </div>

      <div class="tabs" id="settings-tabs">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          :class="['tab', { active: activeTab === tab.key }]"
          @click="activeTab = tab.key"
          :id="`tab-${tab.key}`"
        >
          {{ tab.label }}
        </button>
      </div>

      <div class="tab-content">
        <SettingsPanel   v-if="activeTab === 'settings'" />
        <StatsPanel      v-if="activeTab === 'stats'" />
        <HealthPanel     v-if="activeTab === 'health'" />
        <IngestPanel     v-if="activeTab === 'ingest'" />
        <LogsPanel       v-if="activeTab === 'logs'" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import SettingsPanel from '../components/SettingsPanel.vue'
import StatsPanel from '../components/StatsPanel.vue'
import HealthPanel from '../components/HealthPanel.vue'
import IngestPanel from '../components/IngestPanel.vue'
import LogsPanel from '../components/LogsPanel.vue'

const activeTab = ref('settings')

const tabs = [
  { key: 'settings', label: '⚙️ Agent 配置' },
  { key: 'stats', label: '📊 数据统计' },
  { key: 'health', label: '🏥 健康诊断' },
  { key: 'ingest', label: '📥 数据导入' },
  { key: 'logs', label: '📋 错误日志' },
]
</script>

<style scoped>
.settings-layout {
  height: 100%;
  overflow-y: auto;
  padding: var(--space-6) var(--space-8);
}

.settings-container {
  max-width: 900px;
  margin: 0 auto;
}

.settings-header {
  margin-bottom: var(--space-6);
}

.settings-title {
  font-size: var(--text-2xl);
  font-weight: 700;
  background: var(--gradient-brand);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: var(--space-1);
}

.tabs {
  margin-bottom: var(--space-6);
  flex-wrap: wrap;
}

.tab-content {
  animation: fadeIn var(--transition-normal) ease-out;
}
</style>
