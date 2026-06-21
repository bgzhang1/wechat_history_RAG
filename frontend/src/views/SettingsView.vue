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
          @click="setActiveTab(tab.key)"
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
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SettingsPanel from '../components/SettingsPanel.vue'
import StatsPanel from '../components/StatsPanel.vue'
import HealthPanel from '../components/HealthPanel.vue'
import IngestPanel from '../components/IngestPanel.vue'
import LogsPanel from '../components/LogsPanel.vue'

const tabs = [
  { key: 'settings', label: 'Agent 配置' },
  { key: 'stats', label: '数据统计' },
  { key: 'health', label: '健康诊断' },
  { key: 'ingest', label: '数据导入' },
  { key: 'logs', label: '错误日志' },
]
const tabKeys = new Set(tabs.map(tab => tab.key))
const route = useRoute()
const router = useRouter()

const activeTab = computed(() => {
  const tab = typeof route.query.tab === 'string' ? route.query.tab : ''
  return tabKeys.has(tab) ? tab : 'settings'
})

function setActiveTab(tab) {
  if (!tabKeys.has(tab) || tab === activeTab.value) return
  router.replace({
    query: {
      ...route.query,
      tab: tab === 'settings' ? undefined : tab,
    },
  })
}
</script>

<style scoped>
.settings-layout {
  height: 100%;
  overflow-y: auto;
  padding: var(--space-8);
}

.settings-container {
  max-width: 1040px;
  margin: 0 auto;
}

.settings-header {
  margin-bottom: var(--space-5);
}

.settings-title {
  font-size: 1.85rem;
  font-weight: 760;
  background: var(--gradient-brand);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: var(--space-1);
}

.tabs {
  margin-bottom: var(--space-6);
  flex-wrap: wrap;
  width: fit-content;
  max-width: 100%;
  box-shadow: var(--shadow-sm);
}

.tab-content {
  animation: fadeIn var(--transition-normal) ease-out;
}

@media (max-width: 768px) {
  .settings-layout {
    padding: var(--space-4);
  }

  .settings-title {
    font-size: var(--text-xl);
  }

  .tabs {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    width: 100%;
  }

  .tab {
    min-width: 0;
    min-height: 36px;
  }
}
</style>
