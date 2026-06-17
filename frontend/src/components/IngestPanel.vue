<template>
  <div class="panel animate-fade-in">
    <!-- Upload section -->
    <div class="upload-section glass-card">
      <div class="upload-area" @click="triggerUpload" @dragover.prevent @drop.prevent="handleDrop" id="upload-area">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="color: var(--text-muted);">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
        </svg>
        <span class="text-sm text-secondary">点击或拖拽 JSON 文件到此上传</span>
        <span class="text-xs text-muted">仅支持微信聊天记录 .json 文件</span>
      </div>
      <input ref="fileInput" type="file" accept=".json" style="display:none" @change="handleFileSelect" />
      <div v-if="uploading" class="upload-progress">
        <div class="spinner"></div>
        <span class="text-sm">上传中…</span>
      </div>
    </div>

    <!-- Available files -->
    <div class="section">
      <div class="section-header">
        <h3 class="section-title">可导入文件</h3>
        <button class="btn btn-ghost btn-sm" @click="loadFiles" id="btn-refresh-files">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
          刷新
        </button>
      </div>

      <div v-if="loadingFiles" class="panel-loading"><div class="spinner"></div></div>

      <div v-else-if="files.length === 0" class="empty-state" style="padding: 1.5rem;">
        <span class="text-sm text-muted">暂无可导入文件，请先上传</span>
      </div>

      <div v-else class="file-list">
        <div v-for="f in files" :key="f.file_id" class="file-item glass-card">
          <div class="file-info">
            <span class="file-name">{{ f.filename }}</span>
            <span class="file-meta text-xs text-muted">
              {{ formatSize(f.size) }} · {{ formatDate(f.modified_at) }}
              <span v-if="f.source" class="badge badge-ok" style="margin-left: 4px;">{{ f.source }}</span>
            </span>
          </div>
          <button class="btn btn-primary btn-sm" @click="startImport(f)" :disabled="hasRunningTask">
            导入
          </button>
        </div>
      </div>
    </div>

    <!-- Tasks -->
    <div class="section">
      <div class="section-header">
        <h3 class="section-title">导入任务</h3>
        <button class="btn btn-ghost btn-sm" @click="loadTasks" id="btn-refresh-tasks">刷新</button>
      </div>

      <div v-if="tasks.length === 0" class="empty-state" style="padding: 1.5rem;">
        <span class="text-sm text-muted">暂无导入任务</span>
      </div>

      <div v-else class="task-list">
        <div v-for="task in tasks" :key="task.task_id" class="task-item glass-card">
          <div class="task-header">
            <span :class="['badge', `badge-${task.status}`]">{{ statusLabel(task.status) }}</span>
            <span class="text-xs text-muted">{{ task.task_id.slice(0, 8) }}…</span>
            <span class="text-xs text-muted">{{ formatDate(task.created_at) }}</span>
          </div>

          <!-- WS progress -->
          <div v-if="wsProgress[task.task_id]" class="task-progress">
            <div class="progress-info">
              <span class="text-sm">{{ stageLabel(wsProgress[task.task_id].stage) }}</span>
              <span class="text-sm font-mono">{{ wsProgress[task.task_id].progress || 0 }}%</span>
            </div>
            <div class="progress-bar">
              <div class="progress-bar-fill" :style="{ width: (wsProgress[task.task_id].progress || 0) + '%' }"></div>
            </div>
            <div v-if="wsProgress[task.task_id].eta" class="text-xs text-muted" style="margin-top: 4px;">
              预计剩余 {{ wsProgress[task.task_id].eta }} 秒
            </div>
            <div v-if="wsProgress[task.task_id].log_tail" class="task-log font-mono text-xs">
              {{ wsProgress[task.task_id].log_tail }}
            </div>
          </div>

          <div v-if="task.error" class="task-error text-sm">
            ❌ {{ task.error }}
          </div>

          <div class="task-actions" v-if="task.can_cancel && (task.status === 'running' || task.status === 'cancel_requested')">
            <button class="btn btn-danger btn-sm" @click="cancelTask(task.task_id)" id="btn-cancel-task">
              取消导入
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, inject, computed } from 'vue'
import {
  getIngestFiles, uploadFile, startIngest,
  getIngestTasks, cancelIngest, connectIngestWS,
} from '../api/api.js'

const toast = inject('toast')

const files = ref([])
const tasks = ref([])
const loadingFiles = ref(true)
const uploading = ref(false)
const fileInput = ref(null)
const wsProgress = reactive({})
const wsConnections = {}

const hasRunningTask = computed(() => tasks.value.some(t => t.status === 'running' || t.status === 'cancel_requested'))

onMounted(async () => {
  await Promise.all([loadFiles(), loadTasks()])
})

onUnmounted(() => {
  Object.values(wsConnections).forEach(c => c.close())
})

async function loadFiles() {
  loadingFiles.value = true
  try {
    const data = await getIngestFiles()
    files.value = data.items || []
  } catch (e) { toast(e.message, 'error') }
  loadingFiles.value = false
}

async function loadTasks() {
  try {
    const data = await getIngestTasks()
    tasks.value = data.items || []
    // Connect WS for running tasks
    tasks.value
      .filter(t => t.status === 'running' || t.status === 'cancel_requested')
      .forEach(t => connectWS(t.task_id))
  } catch (e) { toast(e.message, 'error') }
}

function triggerUpload() { fileInput.value?.click() }

function handleDrop(e) {
  const file = e.dataTransfer?.files?.[0]
  if (file) doUpload(file)
}

function handleFileSelect(e) {
  const file = e.target.files?.[0]
  if (file) doUpload(file)
  e.target.value = ''
}

async function doUpload(file) {
  if (!file.name.endsWith('.json')) {
    toast('请上传 .json 文件', 'error')
    return
  }
  uploading.value = true
  try {
    await uploadFile(file)
    toast('文件上传成功', 'success')
    await loadFiles()
  } catch (e) { toast(e.message, 'error') }
  uploading.value = false
}

async function startImport(f) {
  try {
    const params = f.upload_id ? { upload_id: f.upload_id } : { file_id: f.file_id }
    const result = await startIngest(params)
    toast('导入任务已启动', 'success')
    await loadTasks()
    connectWS(result.task_id)
  } catch (e) { toast(e.message, 'error') }
}

async function cancelTask(taskId) {
  try {
    await cancelIngest(taskId)
    toast('已请求取消', 'info')
    await loadTasks()
  } catch (e) { toast(e.message, 'error') }
}

function connectWS(taskId) {
  if (wsConnections[taskId]) return
  wsConnections[taskId] = connectIngestWS(taskId, {
    onMessage(data) {
      wsProgress[taskId] = data
      // Update task status
      const t = tasks.value.find(t => t.task_id === taskId)
      if (t) t.status = data.status
      // Terminal states
      if (['completed', 'error', 'cancelled'].includes(data.status)) {
        if (data.status === 'completed') toast('导入完成！', 'success')
        else if (data.status === 'error') toast('导入失败: ' + (data.error || ''), 'error')
        delete wsConnections[taskId]
        loadTasks()
      }
    },
    onError() { delete wsConnections[taskId] },
    onClose() { delete wsConnections[taskId] },
  })
}

function formatSize(bytes) {
  if (!bytes) return '—'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}

function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function statusLabel(s) {
  const map = { running: '运行中', cancel_requested: '取消中', cancelled: '已取消', completed: '已完成', error: '失败' }
  return map[s] || s
}

function stageLabel(s) {
  const map = { starting: '启动中', parsing: '解析中', indexing: '索引中', chunking: '分块中', summary: '生成摘要', embedding: '生成向量', completed: '已完成' }
  return map[s] || s || '处理中'
}
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.panel-loading {
  display: flex;
  justify-content: center;
  padding: var(--space-8);
}

.upload-section {
  padding: var(--space-4);
}

.upload-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-8);
  border: 2px dashed var(--border-default);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.upload-area:hover {
  border-color: var(--accent-blue);
  background: rgba(59, 130, 246, 0.04);
}

.upload-progress {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
  padding: var(--space-3);
  justify-content: center;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.section-title {
  font-size: var(--text-lg);
  font-weight: 600;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.file-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) var(--space-4);
}

.file-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.file-name {
  font-size: var(--text-sm);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.task-item {
  padding: var(--space-4);
}

.task-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.task-progress {
  margin-bottom: var(--space-3);
}

.progress-info {
  display: flex;
  justify-content: space-between;
  margin-bottom: var(--space-2);
}

.task-log {
  margin-top: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: rgba(0,0,0,0.2);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  max-height: 80px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.task-error {
  padding: var(--space-2) var(--space-3);
  background: rgba(239, 68, 68, 0.08);
  border-radius: var(--radius-sm);
  color: var(--accent-red);
  margin-bottom: var(--space-3);
}

.task-actions {
  display: flex;
  gap: var(--space-2);
}
</style>
