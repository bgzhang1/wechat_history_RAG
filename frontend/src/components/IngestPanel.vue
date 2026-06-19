<template>
  <div class="panel animate-fade-in">
    <div class="upload-section glass-card">
      <div
        id="upload-area"
        :class="['upload-area', { disabled: uploadDisabled }]"
        role="button"
        tabindex="0"
        aria-label="上传一个或多个微信聊天 JSON 文件"
        :aria-disabled="uploadDisabled ? 'true' : 'false'"
        @click="triggerUpload"
        @keydown.enter.prevent="triggerUpload"
        @keydown.space.prevent="triggerUpload"
        @dragover.prevent
        @drop.prevent="handleDrop"
      >
        <svg class="upload-icon" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
        </svg>
        <span class="text-sm text-secondary">点击或拖拽一个或多个 JSON 文件到此上传</span>
        <span class="text-xs text-muted">仅支持微信聊天记录 .json 文件</span>
      </div>
      <input ref="fileInput" class="file-input-hidden" type="file" accept=".json" multiple @change="handleFileSelect" />
      <div v-if="uploading" class="upload-progress">
        <div class="spinner"></div>
        <div class="upload-progress-text">
          <span class="text-sm">上传中… {{ uploadDone }}/{{ uploadTotal }}</span>
          <span class="text-xs text-muted">{{ uploadCurrentName }}</span>
        </div>
      </div>
    </div>

    <div v-if="indexDiagnostics.length || ingestHealthError" class="index-diagnostics glass-card">
      <div class="index-diagnostics-main">
        <div v-for="check in indexDiagnostics" :key="check.component" class="index-diagnostic-item">
          <span :class="['badge', `badge-${check.status}`]">{{ diagnosticStatusLabel(check.status) }}</span>
          <span class="diagnostic-component">{{ diagnosticComponentLabel(check.component) }}</span>
          <span class="text-sm text-secondary">{{ check.detail }}</span>
          <span v-if="check.action" class="text-xs text-muted">{{ check.action }}</span>
        </div>
        <div v-if="ingestHealthError" class="index-diagnostic-item">
          <span class="badge badge-warning">诊断失败</span>
          <span class="text-sm text-secondary">{{ ingestHealthError }}</span>
        </div>
      </div>
      <button class="btn btn-ghost btn-sm" :disabled="loadingIngestHealth" @click="loadIngestHealth">
        刷新诊断
      </button>
    </div>

    <div class="section">
      <div class="section-header">
        <h3 class="section-title">可导入文件</h3>
        <div class="section-actions">
          <button
            class="btn btn-primary btn-sm"
            :disabled="ingestStartLocked || hasBusyFile || pendingFiles.length === 0"
            @click="startBatchImport"
          >
            {{ batchRunning ? '批量导入中…' : `导入待处理 ${pendingFiles.length}` }}
          </button>
          <button id="btn-refresh-files" class="btn btn-ghost btn-sm" :disabled="loadingFiles" @click="loadFiles">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
            刷新
          </button>
        </div>
      </div>

      <div v-if="batchRunning" class="batch-progress glass-card">
        <div class="progress-info">
          <span class="text-xs text-muted">批量导入 {{ batchDone }}/{{ batchTotal }} · {{ batchCurrentName }}</span>
          <span class="text-xs font-mono">{{ batchPercent }}%</span>
        </div>
        <div
          class="progress-bar"
          role="progressbar"
          aria-label="批量导入进度"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuenow="batchPercent"
        >
          <div class="progress-bar-fill" :style="{ width: batchPercent + '%' }"></div>
        </div>
      </div>

      <div v-if="filesError && files.length > 0" class="error-state stale-error glass-card">
        <span class="text-sm">{{ filesError }}</span>
        <button class="btn btn-ghost btn-sm" @click="loadFiles">重试</button>
      </div>

      <div v-if="loadingFiles && files.length === 0" class="panel-loading"><div class="spinner"></div></div>

      <div v-else-if="filesError && files.length === 0" class="error-state glass-card">
        <span class="text-sm">{{ filesError }}</span>
        <button class="btn btn-ghost btn-sm" @click="loadFiles">重试</button>
      </div>

      <div v-else-if="files.length === 0" class="empty-state">
        <span class="text-sm text-muted">暂无可导入文件，请先上传</span>
      </div>

      <div v-else class="file-list">
        <div v-for="f in files" :key="f.file_id" class="file-item glass-card">
          <div class="file-main">
            <div class="file-title-row">
              <span class="file-name">{{ f.filename }}</span>
              <span :class="['badge', statusBadgeClass(f.ingest_status)]">{{ ingestStatusLabel(f.ingest_status) }}</span>
            </div>
            <div class="file-meta text-xs text-muted">
              <span>{{ formatSize(f.size) }}</span>
              <span>{{ formatDate(f.modified_at) }}</span>
              <span v-if="f.source" class="badge badge-source">{{ sourceLabel(f.source) }}</span>
              <span v-if="f.file_id" class="file-path" :title="f.file_id">{{ displayFilePath(f) }}</span>
              <span v-if="latestTaskLabel(f)" :class="['badge', statusBadgeClass(f.task_status)]">{{ latestTaskLabel(f) }}</span>
              <span v-if="statusReasonLabel(f.ingest_status_reason)" class="badge badge-warning">{{ statusReasonLabel(f.ingest_status_reason) }}</span>
              <span v-if="f.last_ingested_at">上次导入 {{ formatDate(f.last_ingested_at) }}</span>
            </div>
            <div v-if="f.ingest_total != null" class="file-meta text-xs text-muted">
              <span>总 {{ f.ingest_total }}</span>
              <span>入库 {{ f.ingest_included ?? 0 }}</span>
              <span>变更 {{ f.ingest_changed ?? f.ingest_inserted ?? 0 }}</span>
            </div>
            <div v-if="f.session_chunks != null" class="file-meta text-xs text-muted">
              <span>会话块 {{ f.session_chunks }}</span>
              <span v-if="f.missing_summary_chunks != null">缺摘要 {{ f.missing_summary_chunks }}</span>
              <span v-if="f.missing_vector_chunks != null">缺向量 {{ f.missing_vector_chunks }}</span>
              <span v-else>向量不可用</span>
            </div>
            <div v-else-if="hasUnknownIndexStatus(f)" class="file-meta text-xs text-muted">
              <span class="badge badge-warning" title="旧版本导入缺少文件来源映射，重新导入或重建后会补齐">索引状态未知</span>
            </div>
            <div v-if="fileProgress(f)" class="file-progress">
              <div class="progress-info">
                <span class="text-xs text-muted">{{ modeLabel(fileProgress(f).mode || f.task_mode) }} · {{ stageLabel(fileProgress(f).stage) }}</span>
                <span class="text-xs font-mono">{{ fileProgress(f).progress || 0 }}%</span>
              </div>
              <div
                class="progress-bar"
                role="progressbar"
                aria-label="文件导入进度"
                aria-valuemin="0"
                aria-valuemax="100"
                :aria-valuenow="fileProgress(f).progress || 0"
              >
                <div class="progress-bar-fill" :style="{ width: (fileProgress(f).progress || 0) + '%' }"></div>
              </div>
              <div v-if="progressMessage(fileProgress(f))" class="progress-message text-xs text-muted">
                {{ progressMessage(fileProgress(f)) }}
              </div>
            </div>
          </div>

          <div class="file-actions">
            <select
              v-model="selectedModes[fileKey(f)]"
              class="input mode-select"
              :disabled="ingestStartLocked || isFileBusy(f)"
              aria-label="导入模式"
              :title="modeHelp(selectedMode(f), f)"
            >
              <option v-for="mode in availableModes(f)" :key="mode.value" :value="mode.value">{{ mode.label }}</option>
            </select>
            <button
              class="btn btn-primary btn-sm import-btn"
              :disabled="ingestStartLocked || isFileBusy(f)"
              :title="modeHelp(selectedMode(f), f)"
              @click="startImport(f)"
            >
              {{ importButtonLabel(f) }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-header">
        <h3 class="section-title">导入任务</h3>
        <button id="btn-refresh-tasks" class="btn btn-ghost btn-sm" :disabled="loadingTasks" @click="loadTasks">刷新</button>
      </div>

      <div v-if="tasksError && tasks.length > 0" class="error-state stale-error glass-card">
        <span class="text-sm">{{ tasksError }}</span>
        <button class="btn btn-ghost btn-sm" @click="loadTasks">重试</button>
      </div>

      <div v-if="loadingTasks && tasks.length === 0" class="panel-loading"><div class="spinner"></div></div>

      <div v-else-if="tasksError && tasks.length === 0" class="error-state glass-card">
        <span class="text-sm">{{ tasksError }}</span>
        <button class="btn btn-ghost btn-sm" @click="loadTasks">重试</button>
      </div>

      <div v-else-if="tasks.length === 0" class="empty-state">
        <span class="text-sm text-muted">暂无导入任务</span>
      </div>

      <div v-else class="task-list">
        <div v-for="task in tasks" :key="task.task_id" class="task-item glass-card">
          <div class="task-header">
            <span :class="['badge', statusBadgeClass(task.status)]">{{ taskStatusLabel(task.status) }}</span>
            <span class="text-xs text-muted">{{ modeLabel(task.mode) }}</span>
            <span v-if="taskFileLabel(task)" class="task-file text-xs text-muted" :title="task.file_id">
              {{ taskFileLabel(task) }}
            </span>
            <span class="text-xs text-muted">{{ task.task_id.slice(0, 8) }}…</span>
            <span class="text-xs text-muted">{{ formatDate(task.created_at) }}</span>
          </div>

          <div v-if="taskProgress(task)" class="task-progress">
            <div class="progress-info">
              <span class="text-sm">{{ stageLabel(taskProgress(task).stage) }}</span>
              <span class="text-sm font-mono">{{ taskProgress(task).progress || 0 }}%</span>
            </div>
            <div
              class="progress-bar"
              role="progressbar"
              aria-label="导入任务进度"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-valuenow="taskProgress(task).progress || 0"
            >
              <div class="progress-bar-fill" :style="{ width: (taskProgress(task).progress || 0) + '%' }"></div>
            </div>
            <div v-if="progressMessage(taskProgress(task))" class="progress-message text-xs text-muted">
              {{ progressMessage(taskProgress(task)) }}
            </div>
            <div v-if="taskProgress(task).eta" class="text-xs text-muted eta">
              预计剩余 {{ taskProgress(task).eta }} 秒
            </div>
            <div v-if="taskProgress(task).log_tail" class="task-log font-mono text-xs">
              {{ taskProgress(task).log_tail }}
            </div>
          </div>

          <div v-if="task.error" class="task-error text-sm">
            导入失败：{{ task.error }}
          </div>

          <div v-if="task.can_cancel && isTaskLive(task)" class="task-actions">
            <button
              class="btn btn-danger btn-sm"
              :disabled="cancellingTaskIds.has(task.task_id) || task.status === 'cancel_requested'"
              @click="cancelTask(task.task_id)"
            >
              {{ cancellingTaskIds.has(task.task_id) || task.status === 'cancel_requested' ? '取消中…' : '取消导入' }}
            </button>
          </div>
        </div>
        <button
          v-if="tasks.length < tasksTotal"
          class="btn btn-ghost btn-sm load-more-tasks"
          :disabled="loadingMoreTasks"
          @click="loadMoreTasks"
        >
          {{ loadingMoreTasks ? '加载中…' : `加载更多任务（${tasks.length}/${tasksTotal}）` }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, inject, computed } from 'vue'
import {
  getAllIngestFiles, uploadFile, startIngest,
  getIngestTasks, getIngestStatus, cancelIngest, connectIngestWS,
  healthCheck,
} from '../api/api.js'

const toast = inject('toast')

const modeOptions = [
  { value: 'full', label: '全流程导入' },
  { value: 'rebuild', label: '强制重建' },
  { value: 'fts', label: '仅 FTS' },
  { value: 'embeddings', label: '仅向量' },
  { value: 'chunks', label: '仅分块' },
  { value: 'summary', label: '仅摘要' },
  { value: 'incremental', label: '增量导入' },
]
const TASKS_PAGE_SIZE = 50
const TASKS_BACKEND_PAGE_LIMIT = 200
const LIVE_REFRESH_MS = 5000
const TASK_POLL_MS = 1500
const TASK_POLL_MAX_ERRORS = 3
const TERMINAL_TASK_STATUSES = new Set(['completed', 'error', 'cancelled'])

const files = ref([])
const tasks = ref([])
const loadingFiles = ref(true)
const loadingTasks = ref(false)
const loadingMoreTasks = ref(false)
const filesError = ref('')
const tasksError = ref('')
const loadingIngestHealth = ref(false)
const ingestHealth = ref({})
const ingestHealthError = ref('')
const uploading = ref(false)
const uploadTotal = ref(0)
const uploadDone = ref(0)
const uploadCurrentName = ref('')
const batchRunning = ref(false)
const batchTotal = ref(0)
const batchDone = ref(0)
const batchCurrentName = ref('')
const batchCurrentTaskId = ref('')
const tasksTotal = ref(0)
const fileInput = ref(null)
const wsProgress = reactive({})
const wsConnections = {}
const selectedModes = reactive({})
const startingFileIds = ref(new Set())
const cancellingTaskIds = ref(new Set())
const refreshingLiveState = ref(false)
let liveRefreshTimer = null
let componentDisposed = false
let activeUploadController = null
let fileLoadSeq = 0
let fileVisibleLoadSeq = 0
let taskLoadSeq = 0
let taskVisibleLoadSeq = 0
let taskAppendVisibleLoadSeq = 0
let ingestHealthSeq = 0
let ingestHealthVisibleSeq = 0
const activeFileLoadControllers = new Set()
const activeTaskLoadControllers = new Set()
const activeTaskStatusControllers = new Set()
const activeTaskStartControllers = new Set()
const activeTaskCancelControllers = new Set()
const activeHealthControllers = new Set()

const hasRunningTask = computed(() => tasks.value.some(isTaskLive))
const hasBusyFile = computed(() => files.value.some(isFileBusy))
const hasStartingTask = computed(() => startingFileIds.value.size > 0)
const ingestStartLocked = computed(
  () => uploading.value || batchRunning.value || hasStartingTask.value || hasRunningTask.value || hasBusyFile.value,
)
const uploadDisabled = computed(() => uploading.value || batchRunning.value)
const pendingFiles = computed(() => files.value.filter(canBatchImport))
const indexDiagnostics = computed(() => {
  const checks = Array.isArray(ingestHealth.value?.checks) ? ingestHealth.value.checks : []
  return checks.filter(
    check => ['database', 'vector_index'].includes(check?.component) && check?.status && check.status !== 'ok',
  )
})
const batchPercent = computed(() => {
  if (!batchTotal.value) return 0
  const currentProgress = batchCurrentTaskProgress.value / 100
  return Math.min(100, Math.floor(((batchDone.value + currentProgress) / batchTotal.value) * 100))
})
const batchCurrentTaskProgress = computed(() => {
  if (!batchCurrentTaskId.value) return 0
  const task = tasks.value.find(item => item.task_id === batchCurrentTaskId.value)
  const progress = task ? taskProgress(task)?.progress : wsProgress[batchCurrentTaskId.value]?.progress
  return Math.max(0, Math.min(100, Number(progress) || 0))
})

onMounted(async () => {
  componentDisposed = false
  await Promise.all([loadFiles(), loadTasks(), loadIngestHealth()])
  if (componentDisposed) return
  liveRefreshTimer = window.setInterval(refreshLiveTaskState, LIVE_REFRESH_MS)
  document.addEventListener('visibilitychange', handleVisibilityChange)
})

onUnmounted(() => {
  componentDisposed = true
  activeUploadController?.abort()
  activeUploadController = null
  activeFileLoadControllers.forEach(controller => controller.abort())
  activeFileLoadControllers.clear()
  activeTaskLoadControllers.forEach(controller => controller.abort())
  activeTaskLoadControllers.clear()
  activeTaskStatusControllers.forEach(controller => controller.abort())
  activeTaskStatusControllers.clear()
  activeTaskStartControllers.forEach(controller => controller.abort())
  activeTaskStartControllers.clear()
  activeTaskCancelControllers.forEach(controller => controller.abort())
  activeTaskCancelControllers.clear()
  activeHealthControllers.forEach(controller => controller.abort())
  activeHealthControllers.clear()
  if (liveRefreshTimer) window.clearInterval(liveRefreshTimer)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  Object.values(wsConnections).forEach(c => c.close())
  Object.keys(wsConnections).forEach(id => delete wsConnections[id])
})

async function loadFiles(options = {}) {
  const silent = options?.silent === true
  const seq = ++fileLoadSeq
  const visibleSeq = silent ? 0 : ++fileVisibleLoadSeq
  const controller = new AbortController()
  activeFileLoadControllers.add(controller)
  if (!silent) loadingFiles.value = true
  try {
    const data = await getAllIngestFiles(500, { signal: controller.signal })
    if (componentDisposed) return
    if (seq !== fileLoadSeq) return
    filesError.value = ''
    files.value = data.items || []
    files.value.forEach((file) => {
      reconcileSelectedMode(file)
    })
  } catch (e) {
    if (componentDisposed) return
    if (seq !== fileLoadSeq) return
    filesError.value = `文件列表加载失败：${e.message}`
    if (!silent) toast(e.message, 'error')
  } finally {
    if (!componentDisposed && !silent && visibleSeq === fileVisibleLoadSeq) loadingFiles.value = false
    activeFileLoadControllers.delete(controller)
  }
}

function mergeTasks(existing, incoming) {
  const byId = new Map(existing.map(task => [task.task_id, task]))
  incoming.forEach(task => byId.set(task.task_id, { ...(byId.get(task.task_id) || {}), ...task }))
  return sortTasks(Array.from(byId.values()))
}

function mergeRefreshedTasks(existing, incoming) {
  const byId = new Map(existing.map(task => [task.task_id, task]))
  return sortTasks(incoming.map(task => ({ ...(byId.get(task.task_id) || {}), ...task })))
}

function taskSortKey(task) {
  const liveRank = isTaskLive(task) ? 1 : 0
  return [
    liveRank,
    String(task.updated_at || ''),
    String(task.created_at || ''),
  ]
}

function sortTasks(items) {
  return items.sort((a, b) => {
    const left = taskSortKey(a)
    const right = taskSortKey(b)
    for (let index = 0; index < left.length; index += 1) {
      const compared = String(right[index]).localeCompare(String(left[index]))
      if (compared !== 0) return compared
    }
    return String(b.task_id || '').localeCompare(String(a.task_id || ''))
  })
}

async function loadTasks(options = {}) {
  const silent = options?.silent === true
  const append = options?.append === true
  const preserveLoaded = options?.preserveLoaded === true
  if (append && loadingTasks.value) return
  if (append ? loadingMoreTasks.value : loadingTasks.value) return
  const seq = ++taskLoadSeq
  const visibleSeq = silent ? 0 : append ? ++taskAppendVisibleLoadSeq : ++taskVisibleLoadSeq
  const controller = new AbortController()
  activeTaskLoadControllers.add(controller)
  if (!silent) {
    if (append) loadingMoreTasks.value = true
    else loadingTasks.value = true
  }
  try {
    const offset = append ? tasks.value.length : 0
    const loadedTaskCount = tasks.value.length
    const limit = append
      ? TASKS_PAGE_SIZE
      : preserveLoaded
        ? Math.min(TASKS_BACKEND_PAGE_LIMIT, Math.max(TASKS_PAGE_SIZE, loadedTaskCount))
        : TASKS_PAGE_SIZE
    const data = await getIngestTasks(limit, offset, { signal: controller.signal })
    let items = data.items || []
    let totalCount = data.total_count ?? items.length
    let targetCount = preserveLoaded && !append
      ? Math.min(Math.max(TASKS_PAGE_SIZE, loadedTaskCount), totalCount)
      : items.length
    while (!componentDisposed && seq === taskLoadSeq && items.length < targetCount) {
      const nextLimit = Math.min(TASKS_BACKEND_PAGE_LIMIT, targetCount - items.length)
      const page = await getIngestTasks(nextLimit, items.length, { signal: controller.signal })
      const pageItems = page.items || []
      totalCount = page.total_count ?? totalCount
      targetCount = Math.min(targetCount, totalCount)
      if (pageItems.length === 0) break
      items = items.concat(pageItems)
    }
    if (componentDisposed) return
    if (seq !== taskLoadSeq) return
    tasksError.value = ''
    tasks.value = append
      ? mergeTasks(tasks.value, items)
      : preserveLoaded
        ? mergeRefreshedTasks(tasks.value, items).slice(0, targetCount)
        : items
    tasksTotal.value = totalCount
    syncLiveTaskConnections()
  } catch (e) {
    if (componentDisposed) return
    if (seq !== taskLoadSeq) return
    if (!append) tasksError.value = `任务列表加载失败：${e.message}`
    if (!silent) toast(e.message, 'error')
  } finally {
    if (!componentDisposed && !silent) {
      if (append && visibleSeq === taskAppendVisibleLoadSeq) loadingMoreTasks.value = false
      else if (!append && visibleSeq === taskVisibleLoadSeq) loadingTasks.value = false
    }
    activeTaskLoadControllers.delete(controller)
  }
}

function loadMoreTasks() {
  if (loadingTasks.value || loadingMoreTasks.value) return
  if (tasks.value.length >= tasksTotal.value) return
  loadTasks({ append: true })
}

async function refreshLiveTaskState() {
  if ((!hasRunningTask.value && !hasBusyFile.value) || refreshingLiveState.value) return
  refreshingLiveState.value = true
  try {
    await Promise.all([
      loadTasks({ silent: true, preserveLoaded: true }),
      loadFiles({ silent: true }),
      loadIngestHealth({ silent: true }),
    ])
  } finally {
    if (!componentDisposed) refreshingLiveState.value = false
  }
}

function handleVisibilityChange() {
  if (document.visibilityState === 'visible') refreshLiveTaskState()
}

function triggerUpload() {
  if (!uploadDisabled.value) fileInput.value?.click()
}

function handleDrop(e) {
  if (uploadDisabled.value) return
  const dropped = Array.from(e.dataTransfer?.files || [])
  if (dropped.length) uploadFiles(dropped)
}

function handleFileSelect(e) {
  if (uploadDisabled.value) {
    e.target.value = ''
    return
  }
  const selected = Array.from(e.target.files || [])
  if (selected.length) uploadFiles(selected)
  e.target.value = ''
}

async function uploadFiles(fileList) {
  if (uploadDisabled.value) return
  const filesToUpload = fileList.filter(file => file.name.toLowerCase().endsWith('.json'))
  const skipped = fileList.length - filesToUpload.length
  if (skipped > 0) toast(`已跳过 ${skipped} 个非 .json 文件`, 'error')
  if (filesToUpload.length === 0) {
    toast('请上传 .json 文件', 'error')
    return
  }
  uploading.value = true
  uploadTotal.value = filesToUpload.length
  uploadDone.value = 0
  uploadCurrentName.value = filesToUpload[0]?.name || ''
  const errors = []
  try {
    for (const file of filesToUpload) {
      if (componentDisposed) break
      uploadCurrentName.value = file.name
      activeUploadController = new AbortController()
      try {
        await uploadFile(file, { signal: activeUploadController.signal })
        if (componentDisposed) break
        uploadDone.value += 1
      } catch (e) {
        if (componentDisposed) break
        errors.push(`${file.name}: ${e.message}`)
      } finally {
        activeUploadController = null
      }
    }
    if (componentDisposed) return
    if (uploadDone.value > 0) {
      toast(
        uploadTotal.value === 1 ? '文件上传成功' : `已上传 ${uploadDone.value}/${uploadTotal.value} 个文件`,
        'success',
      )
      await loadFiles()
    }
    if (errors.length > 0) {
      toast(`上传失败：${formatUploadErrors(errors)}`, 'error')
    }
  } finally {
    if (!componentDisposed) {
      uploading.value = false
      uploadTotal.value = 0
      uploadDone.value = 0
      uploadCurrentName.value = ''
    }
  }
}

function formatUploadErrors(errors) {
  const visible = errors.slice(0, 3).join('；')
  const remaining = errors.length - 3
  return remaining > 0 ? `${visible}；另有 ${remaining} 个文件失败` : visible
}

async function startImport(f) {
  const key = fileKey(f)
  if (ingestStartLocked.value || isFileBusy(f)) return
  setBusy(startingFileIds, key, true)
  try {
    const result = await startFileTask(f)
    if (componentDisposed) return
    toast(result.message || `${modeLabel(result.mode)}任务已启动`, 'success')
  } catch (e) {
    if (!componentDisposed) toast(e.message, 'error')
  } finally {
    if (!componentDisposed) setBusy(startingFileIds, key, false)
  }
}

async function startFileTask(f) {
  const mode = reconcileSelectedMode(f)
  const params = f.upload_id ? { upload_id: f.upload_id, mode } : { file_id: f.file_id, mode }
  const controller = new AbortController()
  activeTaskStartControllers.add(controller)
  try {
    const result = await startIngest(params, { signal: controller.signal })
    if (componentDisposed) return { ...result, mode }
    rememberStartedTask(f, result, mode)
    connectWS(result.task_id)
    refreshStartedTaskLists()
    return { ...result, mode }
  } finally {
    activeTaskStartControllers.delete(controller)
  }
}

function rememberStartedTask(file, result, mode) {
  taskLoadSeq += 1
  fileLoadSeq += 1
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')
  const startedMode = result.mode || mode
  tasks.value = mergeTasks(tasks.value, [{
    task_id: result.task_id,
    status: 'running',
    logs: '',
    created_at: now,
    updated_at: now,
    file_id: file.file_id,
    mode: startedMode,
    error: null,
    can_cancel: true,
    progress: 0,
    stage: 'starting',
    message: result.message || '',
    eta: null,
    log_tail: '',
  }])
  files.value = files.value.map((item) => {
    if (fileKey(item) !== fileKey(file)) return item
    return {
      ...item,
      ingest_status: 'running',
      ingest_status_reason: null,
      task_id: result.task_id,
      task_status: 'running',
      task_mode: startedMode,
    }
  })
}

function refreshStartedTaskLists() {
  if (componentDisposed) return
  void Promise.all([
    loadTasks({ silent: true, preserveLoaded: true }),
    loadFiles({ silent: true }),
    loadIngestHealth({ silent: true }),
  ])
}

async function startBatchImport() {
  if (ingestStartLocked.value || hasBusyFile.value) return
  const queue = pendingFiles.value.slice()
  if (queue.length === 0) {
    toast('没有待处理文件', 'info')
    return
  }

  batchRunning.value = true
  batchTotal.value = queue.length
  batchDone.value = 0
  const failures = []

  try {
    for (const file of queue) {
      if (componentDisposed) break
      batchCurrentName.value = file.filename || shortFileId(file.file_id)
      try {
        const result = await startFileTask(file)
        batchCurrentTaskId.value = result.task_id
        const status = await waitForTask(result.task_id)
        if (status.status !== 'completed') {
          failures.push(`${batchCurrentName.value}: ${status.error || taskStatusLabel(status.status)}`)
          if (status.status === 'cancelled') break
        }
      } catch (e) {
        failures.push(`${batchCurrentName.value}: ${e.message}`)
        if (e?.status === 409 || e?.uncertainTaskState) break
      } finally {
        batchDone.value += 1
        batchCurrentTaskId.value = ''
        if (!componentDisposed) {
          await Promise.all([loadTasks({ silent: true }), loadFiles({ silent: true })])
        }
      }
    }

    if (componentDisposed) return
    if (failures.length > 0) {
      toast(`批量导入完成，失败 ${failures.length} 个：${formatUploadErrors(failures)}`, 'error')
    } else {
      toast(`批量导入完成，共 ${queue.length} 个文件`, 'success')
    }
  } finally {
    if (!componentDisposed) {
      batchRunning.value = false
      batchTotal.value = 0
      batchDone.value = 0
      batchCurrentName.value = ''
      batchCurrentTaskId.value = ''
    }
  }
}

async function waitForTask(taskId) {
  let consecutiveErrors = 0
  let lastError = null
  while (true) {
    await delay(TASK_POLL_MS)
    if (componentDisposed) {
      const err = new Error('批量导入页面已关闭')
      err.batchStopped = true
      throw err
    }
    const controller = new AbortController()
    activeTaskStatusControllers.add(controller)
    try {
      const status = await getIngestStatus(taskId, { signal: controller.signal })
      if (componentDisposed) {
        throw batchStoppedError()
      }
      consecutiveErrors = 0
      lastError = null
      mergeTaskStatus(taskId, status)
      if (['completed', 'error', 'cancelled'].includes(status.status)) return status
    } catch (e) {
      if (e?.batchStopped) throw e
      if (componentDisposed) throw batchStoppedError()
      lastError = e
      if (e?.status === 404) throw e
      consecutiveErrors += 1
      if (consecutiveErrors >= TASK_POLL_MAX_ERRORS) {
        const err = new Error(`连续 ${consecutiveErrors} 次无法获取导入状态：${lastError?.message || '网络异常'}`)
        err.uncertainTaskState = true
        throw err
      }
    } finally {
      activeTaskStatusControllers.delete(controller)
    }
  }
}

function batchStoppedError() {
  const err = new Error('批量导入页面已关闭')
  err.batchStopped = true
  return err
}

function delay(ms) {
  return new Promise(resolve => window.setTimeout(resolve, ms))
}

async function cancelTask(taskId) {
  if (cancellingTaskIds.value.has(taskId)) return
  setBusy(cancellingTaskIds, taskId, true)
  const controller = new AbortController()
  activeTaskCancelControllers.add(controller)
  try {
    const status = await cancelIngest(taskId, { signal: controller.signal })
    if (componentDisposed) return
    mergeTaskStatus(taskId, status)
    toast('已请求取消', 'info')
    await Promise.all([
      loadTasks({ silent: true, preserveLoaded: true }),
      loadFiles({ silent: true }),
      loadIngestHealth({ silent: true }),
    ])
  } catch (e) {
    if (!componentDisposed) toast(e.message, 'error')
  } finally {
    activeTaskCancelControllers.delete(controller)
    if (!componentDisposed) setBusy(cancellingTaskIds, taskId, false)
  }
}

function connectWS(taskId) {
  if (componentDisposed) return
  if (wsConnections[taskId]) return
  wsConnections[taskId] = connectIngestWS(taskId, {
    onMessage(data) {
      if (componentDisposed) {
        wsConnections[taskId]?.close()
        delete wsConnections[taskId]
        delete wsProgress[taskId]
        return
      }
      wsProgress[taskId] = data
      mergeTaskStatus(taskId, data)
      if (['completed', 'error', 'cancelled'].includes(data.status)) {
        if (!batchRunning.value) {
          if (data.status === 'completed') toast('导入完成', 'success')
          else if (data.status === 'error') toast('导入失败：' + (data.error || ''), 'error')
        }
        wsConnections[taskId]?.close()
        delete wsConnections[taskId]
        delete wsProgress[taskId]
        void Promise.all([
          loadTasks({ silent: true, preserveLoaded: true }),
          loadFiles({ silent: true }),
          loadIngestHealth({ silent: true }),
        ])
      }
    },
    onError() { delete wsConnections[taskId] },
    onClose() { delete wsConnections[taskId] },
  })
}

function mergeTaskStatus(taskId, data) {
  const task = tasks.value.find(t => t.task_id === taskId)
  if (!task) return
  task.status = data.status
  task.error = data.error
  task.file_id = data.file_id || task.file_id
  task.mode = data.mode || task.mode
  task.progress = data.progress ?? task.progress
  task.stage = data.stage || task.stage
  task.message = data.message ?? task.message
  task.eta = data.eta ?? null
  task.log_tail = data.log_tail ?? task.log_tail
  task.can_cancel = data.can_cancel ?? task.can_cancel
  task.updated_at = data.updated_at || task.updated_at
}

function syncLiveTaskConnections() {
  const liveIds = new Set(tasks.value.filter(isTaskLive).map(t => t.task_id))
  tasks.value.filter(isTaskLive).forEach(t => connectWS(t.task_id))
  Object.keys(wsConnections).forEach((taskId) => {
    if (!liveIds.has(taskId)) {
      wsConnections[taskId].close()
      delete wsConnections[taskId]
    }
  })
  Object.keys(wsProgress).forEach((taskId) => {
    if (!liveIds.has(taskId)) delete wsProgress[taskId]
  })
}

function isTaskLive(task) {
  return task.status === 'running' || task.status === 'cancel_requested'
}

function isTaskTerminalStatus(status) {
  return TERMINAL_TASK_STATUSES.has(status)
}

function isFileBusy(file) {
  return file.ingest_status === 'running' || file.ingest_status === 'cancel_requested'
}

function hasUnknownIndexStatus(file) {
  return file.ingest_status === 'up_to_date' && file.session_chunks == null
}

function fileProgress(file) {
  const task = tasks.value.find(t => t.task_id === file.task_id && isTaskLive(t))
    || tasks.value.find(t => t.file_id === file.file_id && isTaskLive(t))
  return task ? taskProgress(task) : null
}

function taskProgress(task) {
  const liveProgress = wsProgress[task.task_id]
  if (liveProgress && (!isTaskTerminalStatus(task.status) || liveProgress.status === task.status)) {
    return liveProgress
  }
  if (task.progress == null && !task.stage) return null
  return {
    task_id: task.task_id,
    status: task.status,
    progress: task.progress || 0,
    stage: task.stage || task.status,
    message: task.message || '',
    eta: task.eta || null,
    log_tail: task.log_tail || '',
    mode: task.mode,
  }
}

function taskFileLabel(task) {
  if (!task?.file_id) return ''
  const file = files.value.find(item => item.file_id === task.file_id)
  return file?.filename || shortFileId(task.file_id)
}

function shortFileId(fileId) {
  return String(fileId || '').split(/[\\/]/).filter(Boolean).pop() || String(fileId || '')
}

function fileKey(file) {
  return file.upload_id || file.file_id
}

function defaultMode(file) {
  if (file.ingest_status === 'never') return 'full'
  if (file.ingest_status === 'changed') return 'incremental'
  if (lacksSessionChunks(file)) return 'chunks'
  return 'incremental'
}

function selectedMode(file) {
  const options = availableModes(file)
  const requested = selectedModes[fileKey(file)] || defaultMode(file)
  if (options.some(item => item.value === requested)) return requested
  const preferred = defaultMode(file)
  if (options.some(item => item.value === preferred)) return preferred
  return options[0]?.value || 'incremental'
}

function reconcileSelectedMode(file) {
  const key = fileKey(file)
  const mode = selectedMode(file)
  if (selectedModes[key] !== mode) selectedModes[key] = mode
  return mode
}

function availableModes(file) {
  if (file.ingest_status === 'up_to_date' && !hasUnknownIndexStatus(file)) {
    return modesSupportedByIndexState(file, modeOptions)
  }
  if (file.ingest_status === 'changed' || hasUnknownIndexStatus(file)) {
    return modeOptions.filter(mode => ['incremental', 'full', 'rebuild'].includes(mode.value))
  }
  return modeOptions.filter(mode => mode.value === 'full' || mode.value === 'rebuild')
}

function modesSupportedByIndexState(file, options) {
  return options.filter((mode) => {
    if (modeRequiresSessionChunks(mode.value) && !hasSessionChunks(file)) return false
    if (mode.value === 'summary' && !summaryModeAvailable()) return false
    if (['embeddings', 'vector'].includes(mode.value) && !embeddingModeAvailable()) return false
    return true
  })
}

function modeRequiresSessionChunks(mode) {
  return ['summary', 'embeddings', 'vector'].includes(mode)
}

function hasSessionChunks(file) {
  return Number(file?.session_chunks || 0) > 0
}

function summaryModeAvailable() {
  return ingestHealth.value?.summary_model_configured === true
}

function embeddingModeAvailable() {
  return ingestHealth.value?.embedding_configured === true && ingestHealth.value?.vector_index_available === true
}

function lacksSessionChunks(file) {
  return file?.ingest_status === 'up_to_date' && file?.session_chunks != null && !hasSessionChunks(file)
}

function hasIndexGaps(file) {
  return file?.ingest_status === 'up_to_date' && (
    lacksSessionChunks(file)
    || (summaryModeAvailable() && Number(file?.missing_summary_chunks || 0) > 0)
    || (embeddingModeAvailable() && Number(file?.missing_vector_chunks || 0) > 0)
  )
}

function canBatchImport(file) {
  return file.ingest_status === 'never'
    || file.ingest_status === 'changed'
    || hasUnknownIndexStatus(file)
    || hasIndexGaps(file)
}

function setBusy(setRef, id, busy) {
  const next = new Set(setRef.value)
  if (busy) next.add(id)
  else next.delete(id)
  setRef.value = next
}

function formatSize(bytes) {
  if (!bytes) return '—'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}

function formatDate(iso) {
  if (!iso) return '—'
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(iso) ? `${iso.replace(' ', 'T')}Z` : iso
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function sourceLabel(source) {
  return source === 'upload' ? '上传' : '本地'
}

function displayFilePath(file) {
  const fileId = String(file?.file_id || '').replace(/\\/g, '/')
  if (!fileId) return ''
  if (file?.source === 'upload') return 'uploads/' + shortFileId(fileId)
  const parts = fileId.split('/').filter(Boolean)
  if (parts.length <= 2) return fileId
  return `.../${parts.slice(-2).join('/')}`
}

function ingestStatusLabel(status) {
  if (!status) return '未导入'
  const map = {
    never: '未导入',
    up_to_date: '已导入',
    changed: '需重新导入',
    running: '导入中',
    cancel_requested: '取消中',
    cancelled: '已取消',
    completed: '已完成',
    error: '失败',
  }
  return map[status] || status || '未知'
}

function statusReasonLabel(reason) {
  const map = {
    file_changed: '文件已变化',
    parser_version_stale: '解析规则已升级',
  }
  return map[reason] || ''
}

function taskStatusLabel(status) {
  const map = {
    running: '运行中',
    cancel_requested: '取消中',
    cancelled: '已取消',
    completed: '已完成',
    error: '失败',
  }
  return map[status] || status
}

function latestTaskLabel(file) {
  if (!file?.task_status || ['running', 'cancel_requested'].includes(file.task_status)) return ''
  if (!['error', 'cancelled'].includes(file.task_status)) return ''
  return `最近${modeLabel(file.task_mode)}${taskStatusLabel(file.task_status)}`
}

function statusBadgeClass(status) {
  const map = {
    never: 'badge-never',
    up_to_date: 'badge-ok',
    changed: 'badge-warning',
    running: 'badge-running',
    cancel_requested: 'badge-cancel_requested',
    cancelled: 'badge-cancelled',
    completed: 'badge-completed',
    error: 'badge-error',
  }
  return map[status] || 'badge-never'
}

function modeLabel(mode) {
  return modeOptions.find(item => item.value === mode)?.label || mode || '增量导入'
}

function modeHelp(mode, file) {
  const target = file?.source === 'upload' ? '上传 JSON' : '当前 JSON'
  const messageScope = file?.source === 'upload' ? '上传 JSON 关联消息' : '当前 JSON 关联消息'
  const map = {
    incremental: `检查${target}，按需解析并补齐缺失索引。`,
    full: `重新解析${target}，并补齐必要索引、摘要和向量。`,
    rebuild: `重新解析${target}，并强制重建其关联索引、会话分块、摘要和向量。`,
    fts: `重建${messageScope}的全文索引，不调用模型或 embedding。`,
    chunks: `重建${target}关联会话的会话块。`,
    summary: `处理${target}已入库会话块，可能调用摘要模型。`,
    embeddings: `处理${target}已入库会话块，可能调用 embedding API。`,
    vector: `处理${target}已入库会话块，可能调用 embedding API。`,
  }
  return map[mode] || '启动导入任务。'
}

function stageLabel(stage) {
  const map = {
    starting: '启动中',
    parsing: '解析中',
    indexing: '索引中',
    chunking: '分块中',
    summary: '生成摘要',
    embedding: '生成向量',
    completed: '已完成',
    cancelling: '取消中',
    cancelled: '已取消',
    error: '失败',
  }
  return map[stage] || stage || '处理中'
}

async function loadIngestHealth(options = {}) {
  const silent = options?.silent === true
  const seq = ++ingestHealthSeq
  const visibleSeq = silent ? 0 : ++ingestHealthVisibleSeq
  const controller = new AbortController()
  activeHealthControllers.add(controller)
  if (!silent) loadingIngestHealth.value = true
  try {
    const data = await healthCheck({ signal: controller.signal })
    if (componentDisposed) return
    if (seq !== ingestHealthSeq) return
    ingestHealth.value = data
    files.value.forEach((file) => {
      reconcileSelectedMode(file)
    })
    ingestHealthError.value = ''
  } catch (e) {
    if (componentDisposed) return
    if (seq !== ingestHealthSeq) return
    ingestHealthError.value = `索引诊断加载失败：${e.message}`
    if (!silent) toast(e.message, 'error')
  } finally {
    activeHealthControllers.delete(controller)
    if (!componentDisposed && !silent && visibleSeq === ingestHealthVisibleSeq) loadingIngestHealth.value = false
  }
}

function diagnosticStatusLabel(status) {
  const map = { warning: '警告', error: '错误', degraded: '降级' }
  return map[status] || status || '异常'
}

function diagnosticComponentLabel(component) {
  const map = {
    database: '数据库',
    vector_index: '向量索引',
  }
  return map[component] || component || '索引'
}

function progressMessage(progress) {
  const message = String(progress?.message || '').trim()
  if (!message) return ''
  return message.length > 160 ? `${message.slice(0, 160)}…` : message
}

function importButtonLabel(file) {
  const key = fileKey(file)
  if (startingFileIds.value.has(key)) return '启动中…'
  if (file.ingest_status === 'running' || file.ingest_status === 'cancel_requested') return '导入中'
  return '开始'
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

.empty-state {
  padding: var(--space-6);
  text-align: center;
}

.index-diagnostics {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4);
  border-color: rgba(245, 158, 11, 0.35);
}

.index-diagnostics-main {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.index-diagnostic-item {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.diagnostic-component {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
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

.stale-error {
  margin-top: var(--space-3);
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

.upload-area:focus-visible {
  border-color: var(--border-focus);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.14);
}

.upload-area.disabled {
  cursor: wait;
  opacity: 0.65;
}

.upload-icon {
  color: var(--text-muted);
}

.file-input-hidden {
  display: none;
}

.upload-progress {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
  padding: var(--space-3);
  justify-content: center;
}

.upload-progress-text {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.upload-progress-text .text-muted {
  max-width: min(560px, 70vw);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.section-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  justify-content: flex-end;
}

.section-title {
  font-size: var(--text-lg);
  font-weight: 600;
}

.batch-progress {
  margin-top: var(--space-3);
  padding: var(--space-3) var(--space-4);
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.file-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4);
}

.file-main {
  min-width: 0;
}

.file-title-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.file-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
  font-weight: 600;
}

.file-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: 4px;
}

.file-path {
  max-width: min(360px, 100%);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badge-source,
.badge-never {
  background: rgba(148, 163, 184, 0.12);
  color: var(--text-muted);
}

.file-progress {
  margin-top: var(--space-3);
}

.file-actions {
  display: grid;
  grid-template-columns: minmax(124px, 150px) minmax(72px, auto);
  align-items: center;
  gap: var(--space-2);
}

.mode-select {
  height: 32px;
  min-width: 0;
  padding-top: var(--space-1);
  padding-bottom: var(--space-1);
  font-size: var(--text-xs);
}

.import-btn {
  min-width: 72px;
}

.task-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.load-more-tasks {
  align-self: center;
  min-width: 180px;
}

.task-item {
  padding: var(--space-4);
}

.task-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.task-file {
  max-width: min(360px, 100%);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-progress {
  margin-bottom: var(--space-3);
}

.progress-info {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}

.eta {
  margin-top: 4px;
}

.progress-message {
  margin-top: 4px;
  overflow-wrap: anywhere;
}

.task-log {
  margin-top: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: rgba(0, 0, 0, 0.2);
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

@media (max-width: 768px) {
  .upload-area {
    padding: var(--space-6) var(--space-4);
  }

  .file-item {
    grid-template-columns: 1fr;
    align-items: stretch;
    gap: var(--space-3);
  }

  .file-title-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .file-actions {
    grid-template-columns: 1fr auto;
  }

  .mode-select {
    width: 100%;
  }
}
</style>
