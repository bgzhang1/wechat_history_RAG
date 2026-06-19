from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = ROOT / "frontend"


class FrontendPackageTests(unittest.TestCase):
    def test_runtime_imports_are_declared_as_frontend_dependencies(self) -> None:
        package = self._read_json(FRONTEND_ROOT / "package.json")
        dependencies = package.get("dependencies", {})

        required_runtime_packages = {
            "vue",
            "vue-router",
            "marked",
            "dompurify",
        }

        missing = sorted(required_runtime_packages - set(dependencies))
        self.assertEqual(missing, [], f"前端运行依赖缺少声明：{missing}")

    def test_package_lock_root_dependencies_match_package_json(self) -> None:
        package = self._read_json(FRONTEND_ROOT / "package.json")
        lockfile = self._read_json(FRONTEND_ROOT / "package-lock.json")

        package_dependencies = package.get("dependencies", {})
        lock_root_dependencies = lockfile.get("packages", {}).get("", {}).get("dependencies", {})

        for name, version in package_dependencies.items():
            self.assertEqual(
                lock_root_dependencies.get(name),
                version,
                f"package-lock.json 顶层依赖未同步：{name}",
            )

    def test_api_path_parameters_are_url_encoded(self) -> None:
        api_source = (FRONTEND_ROOT / "src" / "api" / "api.js").read_text(encoding="utf-8")

        expected_fragments = [
            "function pathSegment(value)",
            "function queryString(params = {})",
            "post(`/chat/${pathSegment(sessionId)}/abort`, payload, options)",
            "get(`/chat/${pathSegment(sessionId)}/messages${query ? `?${query}` : ''}`, options)",
            "get(`/chat/${pathSegment(sessionId)}/status`, options)",
            "patch(`/chat/${pathSegment(sessionId)}`, { title }, options)",
            "del(`/chat/${pathSegment(sessionId)}`, null, options)",
            "get(`/ingest/status/${pathSegment(taskId)}`, options)",
            "post(`/ingest/tasks/${pathSegment(taskId)}/cancel`, null, options)",
            "wsUrl(`/ws/ingest/${pathSegment(taskId)}`)",
        ]
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, api_source)

    def test_upload_file_accepts_external_abort_signal(self) -> None:
        api_source = (FRONTEND_ROOT / "src" / "api" / "api.js").read_text(encoding="utf-8")

        self.assertIn("export function uploadFile(file, options = {})", api_source)
        self.assertIn("createAbortController(UPLOAD_TIMEOUT_MS, options.signal)", api_source)

    def test_ingest_file_listing_accepts_external_abort_signal(self) -> None:
        api_source = (FRONTEND_ROOT / "src" / "api" / "api.js").read_text(encoding="utf-8")

        self.assertIn("function get(path, options = {}) { return request(path, options) }", api_source)
        self.assertIn("export function getIngestFiles(limit = 100, offset = 0, options = {})", api_source)
        self.assertIn("return get(`/ingest/files?${queryString({ limit, offset })}`, options)", api_source)
        self.assertIn("export async function getAllIngestFiles(pageSize = 500, options = {})", api_source)
        self.assertIn("const page = await getIngestFiles(safePageSize, offset, options)", api_source)

    def test_ingest_task_and_health_reads_accept_external_abort_signal(self) -> None:
        api_source = (FRONTEND_ROOT / "src" / "api" / "api.js").read_text(encoding="utf-8")

        self.assertIn("function post(path, body, options = {})", api_source)
        self.assertIn("return request(path, { ...options, method: 'POST'", api_source)
        self.assertIn("export function healthCheck(options = {}) { return get('/health', options) }", api_source)
        self.assertIn("export function getIngestStatus(taskId, options = {})", api_source)
        self.assertIn("return get(`/ingest/status/${pathSegment(taskId)}`, options)", api_source)
        self.assertIn("export function getIngestTasks(limit = 50, offset = 0, options = {})", api_source)
        self.assertIn("return get(`/ingest/tasks?${queryString({ limit, offset })}`, options)", api_source)
        self.assertIn("export function cancelIngest(taskId, options = {})", api_source)
        self.assertIn("return post(`/ingest/tasks/${pathSegment(taskId)}/cancel`, null, options)", api_source)

    def test_chat_listing_and_message_reads_accept_external_abort_signal(self) -> None:
        api_source = (FRONTEND_ROOT / "src" / "api" / "api.js").read_text(encoding="utf-8")

        self.assertIn("export function getSessions(limit = 100, offset = 0, options = {})", api_source)
        self.assertIn("return get(`/chat/sessions?${queryString({ limit, offset })}`, options)", api_source)
        self.assertIn("export function getMessages(sessionId, limit = 500, offset = 0, options = {})", api_source)
        self.assertIn("const query = queryString({ limit, offset })", api_source)
        self.assertIn("return get(`/chat/${pathSegment(sessionId)}/messages${query ? `?${query}` : ''}`, options)", api_source)
        self.assertIn("export function getSessionStatus(sessionId, options = {})", api_source)
        self.assertIn("return get(`/chat/${pathSegment(sessionId)}/status`, options)", api_source)

    def test_chat_and_settings_mutations_accept_external_abort_signal(self) -> None:
        api_source = (FRONTEND_ROOT / "src" / "api" / "api.js").read_text(encoding="utf-8")

        expected_fragments = [
            "export function abortChat(sessionId, payload = null, options = {})",
            "return post(`/chat/${pathSegment(sessionId)}/abort`, payload, options)",
            "export function renameSession(sessionId, title, options = {})",
            "return patch(`/chat/${pathSegment(sessionId)}`, { title }, options)",
            "export function deleteSession(sessionId, options = {})",
            "return del(`/chat/${pathSegment(sessionId)}`, null, options)",
            "export function batchDeleteSessions(sessionIds, options = {})",
            "return post('/chat/sessions/delete', { session_ids: sessionIds }, options)",
            "export function updateSettings(data, options = {}) { return post('/settings', data, options) }",
            "export function resetSettings(options = {}) { return post('/settings/reset', null, options) }",
        ]
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, api_source)

    def test_status_settings_logs_stats_and_suggestions_accept_external_abort_signal(self) -> None:
        api_source = (FRONTEND_ROOT / "src" / "api" / "api.js").read_text(encoding="utf-8")

        expected_fragments = [
            "export function getSettings(options = {}) { return get('/settings', options) }",
            "export function getStatsSummary(options = {}) { return get('/stats/summary', options) }",
            "export function getStatsDetailed(params = {}, options = {})",
            "return get(`/stats?${q}`, options)",
            "export function getThreads(limit = 50, offset = 0, options = {})",
            "return get(`/stats/threads?${queryString({ limit, offset })}`, options)",
            "export function getSenders(limit = 50, offset = 0, options = {})",
            "return get(`/stats/senders?${queryString({ limit, offset })}`, options)",
            "export function healthDiagnostics(options = {}) { return get('/health/diagnostics', options) }",
            "export function getSuggestions(query, limit = 10, options = {})",
            "return get(`/suggestions?${queryString({ query, limit })}`, options)",
            "export function getLogs(level = 'error', limit = 100, options = {})",
            "return get(`/logs?${queryString({ level, limit })}`, options)",
        ]
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, api_source)

    def test_api_query_parameters_are_built_with_url_search_params(self) -> None:
        api_source = (FRONTEND_ROOT / "src" / "api" / "api.js").read_text(encoding="utf-8")

        self.assertIn("const query = new URLSearchParams()", api_source)
        self.assertIn("query.set(key, String(value))", api_source)
        self.assertNotIn("encodeURIComponent(query)}&limit=${limit}", api_source)
        self.assertNotIn("level=${level}&limit=${limit}", api_source)

    def test_api_error_messages_extract_nested_details_and_field_paths(self) -> None:
        api_source = (FRONTEND_ROOT / "src" / "api" / "api.js").read_text(encoding="utf-8")

        self.assertIn("function firstErrorText(...values)", api_source)
        self.assertIn("function errorTextFromValue(value)", api_source)
        self.assertIn("body?.error?.details", api_source)
        self.assertIn("const messages = value.map(errorTextFromValue).filter(Boolean)", api_source)
        self.assertIn("const message = firstErrorText(value.msg, value.message, value.detail, value.type)", api_source)
        self.assertIn("const fieldPath = fieldPathFromLoc(value.loc || value.field || value.path)", api_source)
        self.assertIn("return fieldPath ? `${fieldPath}: ${message}` : message", api_source)
        self.assertIn("if (Array.isArray(loc)) return loc.filter((item) => item !== 'body').map(String).join('.')", api_source)

    def test_chat_sse_has_connect_timeout_without_timing_out_active_streams(self) -> None:
        api_source = (FRONTEND_ROOT / "src" / "api" / "api.js").read_text(encoding="utf-8")

        self.assertIn("clearTimeout: () => {", api_source)
        self.assertIn("const abortState = createAbortController(REQUEST_TIMEOUT_MS, controller.signal)", api_source)
        self.assertIn("signal: abortState.signal", api_source)
        self.assertIn("abortState.clearTimeout()", api_source)
        self.assertIn("if (abortState.didTimeout())", api_source)
        self.assertIn("abortState.cleanup()", api_source)

        chat_pos = api_source.index("export function chatSSE")
        abort_state_pos = api_source.index("const abortState = createAbortController(REQUEST_TIMEOUT_MS, controller.signal)", chat_pos)
        fetch_signal_pos = api_source.index("signal: abortState.signal", abort_state_pos)
        clear_timeout_pos = api_source.index("abortState.clearTimeout()", fetch_signal_pos)
        reader_pos = api_source.index("const reader = res.body.getReader()", clear_timeout_pos)
        cleanup_pos = api_source.index("abortState.cleanup()", reader_pos)
        self.assertLess(abort_state_pos, fetch_signal_pos)
        self.assertLess(fetch_signal_pos, clear_timeout_pos)
        self.assertLess(clear_timeout_pos, reader_pos)
        self.assertLess(reader_pos, cleanup_pos)

    def test_ingest_batch_stops_on_backend_running_conflict(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("e?.status === 409 || e?.uncertainTaskState", ingest_panel)

    def test_settings_view_can_open_ingest_tab_from_query_param(self) -> None:
        settings_view = (FRONTEND_ROOT / "src" / "views" / "SettingsView.vue").read_text(encoding="utf-8")

        self.assertIn("const tab = typeof route.query.tab === 'string' ? route.query.tab : ''", settings_view)
        self.assertIn("return tabKeys.has(tab) ? tab : 'settings'", settings_view)
        self.assertIn("<IngestPanel     v-if=\"activeTab === 'ingest'\" />", settings_view)
        self.assertIn("tab: tab === 'settings' ? undefined : tab", settings_view)

    def test_ingest_panel_keeps_upload_entry_visible_when_backend_is_unavailable(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        upload_section_pos = ingest_panel.index('<div class="upload-section glass-card">')
        file_error_pos = ingest_panel.index('v-else-if="filesError && files.length === 0"')
        task_error_pos = ingest_panel.index('v-else-if="tasksError && tasks.length === 0"')
        upload_action_pos = ingest_panel.index("@click=\"triggerUpload\"", upload_section_pos)

        self.assertLess(upload_section_pos, file_error_pos)
        self.assertLess(upload_section_pos, task_error_pos)
        self.assertLess(upload_section_pos, upload_action_pos)
        self.assertIn("文件列表加载失败", ingest_panel)
        self.assertIn("任务列表加载失败", ingest_panel)

    def test_ingest_task_merge_preserves_live_task_priority(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("function taskSortKey(task)", ingest_panel)
        self.assertIn("const liveRank = isTaskLive(task) ? 1 : 0", ingest_panel)
        self.assertIn("return sortTasks(Array.from(byId.values()))", ingest_panel)
        self.assertIn("function mergeRefreshedTasks(existing, incoming)", ingest_panel)
        self.assertIn("return sortTasks(incoming.map(task => ({ ...(byId.get(task.task_id) || {}), ...task })))", ingest_panel)

    def test_ingest_batch_progress_includes_current_task_progress(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("const batchCurrentTaskId = ref('')", ingest_panel)
        self.assertIn("const currentProgress = batchCurrentTaskProgress.value / 100", ingest_panel)
        self.assertIn("batchCurrentTaskId.value = result.task_id", ingest_panel)
        self.assertIn("taskProgress(task)?.progress", ingest_panel)

    def test_ingest_terminal_task_progress_ignores_stale_ws_progress(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("const TERMINAL_TASK_STATUSES = new Set(['completed', 'error', 'cancelled'])", ingest_panel)
        self.assertIn("function isTaskTerminalStatus(status)", ingest_panel)

        progress_pos = ingest_panel.index("function taskProgress(task)")
        live_progress_pos = ingest_panel.index("const liveProgress = wsProgress[task.task_id]", progress_pos)
        terminal_guard_pos = ingest_panel.index(
            "if (liveProgress && (!isTaskTerminalStatus(task.status) || liveProgress.status === task.status))",
            live_progress_pos,
        )
        fallback_pos = ingest_panel.index("if (task.progress == null && !task.stage) return null", terminal_guard_pos)

        self.assertLess(progress_pos, live_progress_pos)
        self.assertLess(live_progress_pos, terminal_guard_pos)
        self.assertLess(terminal_guard_pos, fallback_pos)

    def test_ingest_panel_hides_summary_and_vector_modes_without_session_chunks(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("function modesSupportedByIndexState(file, options)", ingest_panel)
        self.assertIn("if (modeRequiresSessionChunks(mode.value) && !hasSessionChunks(file)) return false", ingest_panel)
        self.assertIn("if (mode.value === 'summary' && !summaryModeAvailable()) return false", ingest_panel)
        self.assertIn("if (['embeddings', 'vector'].includes(mode.value) && !embeddingModeAvailable()) return false", ingest_panel)
        self.assertIn("function modeRequiresSessionChunks(mode)", ingest_panel)
        self.assertIn("return ['summary', 'embeddings', 'vector'].includes(mode)", ingest_panel)
        self.assertIn("function hasSessionChunks(file)", ingest_panel)
        self.assertIn("return Number(file?.session_chunks || 0) > 0", ingest_panel)
        self.assertIn("function summaryModeAvailable()", ingest_panel)
        self.assertIn("return ingestHealth.value?.summary_model_configured === true", ingest_panel)
        self.assertIn("function embeddingModeAvailable()", ingest_panel)
        self.assertIn(
            "return ingestHealth.value?.embedding_configured === true && ingestHealth.value?.vector_index_available === true",
            ingest_panel,
        )

    def test_ingest_panel_batches_up_to_date_files_with_incomplete_indexes(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("if (lacksSessionChunks(file)) return 'chunks'", ingest_panel)
        self.assertIn("function lacksSessionChunks(file)", ingest_panel)
        self.assertIn(
            "return file?.ingest_status === 'up_to_date' && file?.session_chunks != null && !hasSessionChunks(file)",
            ingest_panel,
        )
        self.assertIn("function hasIndexGaps(file)", ingest_panel)
        self.assertIn("|| (summaryModeAvailable() && Number(file?.missing_summary_chunks || 0) > 0)", ingest_panel)
        self.assertIn("|| (embeddingModeAvailable() && Number(file?.missing_vector_chunks || 0) > 0)", ingest_panel)
        self.assertIn("|| hasIndexGaps(file)", ingest_panel)

    def test_ingest_cancel_merges_response_and_refreshes_silently(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        cancel_pos = ingest_panel.index("async function cancelTask(taskId)")
        controller_pos = ingest_panel.index("const controller = new AbortController()", cancel_pos)
        track_pos = ingest_panel.index("activeTaskCancelControllers.add(controller)", controller_pos)
        request_pos = ingest_panel.index("const status = await cancelIngest(taskId, { signal: controller.signal })", track_pos)
        merge_pos = ingest_panel.index("mergeTaskStatus(taskId, status)", request_pos)
        toast_pos = ingest_panel.index("toast('已请求取消', 'info')", merge_pos)
        tasks_refresh_pos = ingest_panel.index("loadTasks({ silent: true, preserveLoaded: true })", toast_pos)
        files_refresh_pos = ingest_panel.index("loadFiles({ silent: true })", tasks_refresh_pos)
        health_refresh_pos = ingest_panel.index("loadIngestHealth({ silent: true })", files_refresh_pos)
        cleanup_pos = ingest_panel.index("activeTaskCancelControllers.delete(controller)", health_refresh_pos)

        self.assertLess(cancel_pos, request_pos)
        self.assertLess(controller_pos, track_pos)
        self.assertLess(track_pos, request_pos)
        self.assertLess(request_pos, merge_pos)
        self.assertLess(merge_pos, toast_pos)
        self.assertLess(toast_pos, tasks_refresh_pos)
        self.assertLess(tasks_refresh_pos, files_refresh_pos)
        self.assertLess(files_refresh_pos, health_refresh_pos)
        self.assertLess(health_refresh_pos, cleanup_pos)

    def test_ingest_batch_stops_frontend_queue_after_component_unmount(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("let componentDisposed = false", ingest_panel)
        self.assertIn("componentDisposed = true", ingest_panel)
        mounted_pos = ingest_panel.index("onMounted(async () =>")
        await_pos = ingest_panel.index("await Promise.all([loadFiles(), loadTasks(), loadIngestHealth()])", mounted_pos)
        guard_pos = ingest_panel.index("if (componentDisposed) return", await_pos)
        timer_pos = ingest_panel.index("liveRefreshTimer = window.setInterval", guard_pos)
        self.assertIn("if (componentDisposed) break", ingest_panel)
        self.assertIn("if (!componentDisposed) {", ingest_panel)
        self.assertIn("批量导入页面已关闭", ingest_panel)
        self.assertLess(await_pos, guard_pos)
        self.assertLess(guard_pos, timer_pos)

    def test_ingest_upload_queue_stops_after_component_unmount(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("let activeUploadController = null", ingest_panel)
        self.assertIn("activeUploadController?.abort()", ingest_panel)
        upload_pos = ingest_panel.index("async function uploadFiles")
        first_guard_pos = ingest_panel.index("if (componentDisposed) break", upload_pos)
        controller_pos = ingest_panel.index("activeUploadController = new AbortController()", first_guard_pos)
        upload_call_pos = ingest_panel.index("await uploadFile(file, { signal: activeUploadController.signal })", controller_pos)
        success_guard_pos = ingest_panel.index("if (componentDisposed) break", upload_call_pos)
        error_push_pos = ingest_panel.index("errors.push(`${file.name}: ${e.message}`)", success_guard_pos)
        clear_pos = ingest_panel.index("activeUploadController = null", error_push_pos)
        final_guard_pos = ingest_panel.index("if (componentDisposed) return", error_push_pos)
        toast_pos = ingest_panel.index("toast(", final_guard_pos)

        self.assertLess(first_guard_pos, controller_pos)
        self.assertLess(controller_pos, upload_call_pos)
        self.assertLess(upload_call_pos, success_guard_pos)
        self.assertLess(success_guard_pos, error_push_pos)
        self.assertLess(error_push_pos, clear_pos)
        self.assertLess(error_push_pos, final_guard_pos)
        self.assertLess(final_guard_pos, toast_pos)

    def test_ingest_panel_ignores_late_async_results_after_unmount(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("const activeFileLoadControllers = new Set()", ingest_panel)
        self.assertIn("activeFileLoadControllers.forEach(controller => controller.abort())", ingest_panel)
        self.assertIn("activeFileLoadControllers.clear()", ingest_panel)
        self.assertIn("const activeTaskLoadControllers = new Set()", ingest_panel)
        self.assertIn("activeTaskLoadControllers.forEach(controller => controller.abort())", ingest_panel)
        self.assertIn("activeTaskLoadControllers.clear()", ingest_panel)
        self.assertIn("const activeTaskStatusControllers = new Set()", ingest_panel)
        self.assertIn("activeTaskStatusControllers.forEach(controller => controller.abort())", ingest_panel)
        self.assertIn("activeTaskStatusControllers.clear()", ingest_panel)
        self.assertIn("const activeTaskStartControllers = new Set()", ingest_panel)
        self.assertIn("activeTaskStartControllers.forEach(controller => controller.abort())", ingest_panel)
        self.assertIn("activeTaskStartControllers.clear()", ingest_panel)
        self.assertIn("const activeTaskCancelControllers = new Set()", ingest_panel)
        self.assertIn("activeTaskCancelControllers.forEach(controller => controller.abort())", ingest_panel)
        self.assertIn("activeTaskCancelControllers.clear()", ingest_panel)
        self.assertIn("const activeHealthControllers = new Set()", ingest_panel)
        self.assertIn("activeHealthControllers.forEach(controller => controller.abort())", ingest_panel)
        self.assertIn("activeHealthControllers.clear()", ingest_panel)

        load_files_pos = ingest_panel.index("async function loadFiles")
        controller_pos = ingest_panel.index("const controller = new AbortController()", load_files_pos)
        track_pos = ingest_panel.index("activeFileLoadControllers.add(controller)", controller_pos)
        files_fetch_pos = ingest_panel.index(
            "const data = await getAllIngestFiles(500, { signal: controller.signal })",
            load_files_pos,
        )
        files_guard_pos = ingest_panel.index("if (componentDisposed) return", files_fetch_pos)
        files_assign_pos = ingest_panel.index("files.value = data.items || []", files_guard_pos)
        cleanup_pos = ingest_panel.index("activeFileLoadControllers.delete(controller)", files_assign_pos)
        self.assertLess(controller_pos, track_pos)
        self.assertLess(track_pos, files_fetch_pos)
        self.assertLess(files_fetch_pos, files_guard_pos)
        self.assertLess(files_guard_pos, files_assign_pos)
        self.assertLess(files_assign_pos, cleanup_pos)
        self.assertIn(
            "if (!componentDisposed && !silent && visibleSeq === fileVisibleLoadSeq) loadingFiles.value = false",
            ingest_panel,
        )

        load_tasks_pos = ingest_panel.index("async function loadTasks")
        tasks_controller_pos = ingest_panel.index("const controller = new AbortController()", load_tasks_pos)
        tasks_track_pos = ingest_panel.index("activeTaskLoadControllers.add(controller)", tasks_controller_pos)
        tasks_fetch_pos = ingest_panel.index(
            "const data = await getIngestTasks(limit, offset, { signal: controller.signal })",
            tasks_track_pos,
        )
        tasks_guard_pos = ingest_panel.index("if (componentDisposed) return", tasks_fetch_pos)
        tasks_assign_pos = ingest_panel.index("tasks.value =", tasks_guard_pos)
        tasks_cleanup_pos = ingest_panel.index("activeTaskLoadControllers.delete(controller)", tasks_assign_pos)
        self.assertLess(tasks_controller_pos, tasks_track_pos)
        self.assertLess(tasks_track_pos, tasks_fetch_pos)
        self.assertLess(tasks_fetch_pos, tasks_guard_pos)
        self.assertLess(tasks_guard_pos, tasks_assign_pos)
        self.assertLess(tasks_assign_pos, tasks_cleanup_pos)
        self.assertIn("if (!componentDisposed && !silent) {", ingest_panel)

        wait_pos = ingest_panel.index("async function waitForTask")
        status_controller_pos = ingest_panel.index("const controller = new AbortController()", wait_pos)
        status_track_pos = ingest_panel.index("activeTaskStatusControllers.add(controller)", status_controller_pos)
        status_fetch_pos = ingest_panel.index(
            "const status = await getIngestStatus(taskId, { signal: controller.signal })",
            status_track_pos,
        )
        status_cleanup_pos = ingest_panel.index("activeTaskStatusControllers.delete(controller)", status_fetch_pos)
        stopped_pos = ingest_panel.index("err.batchStopped = true", wait_pos)
        rethrow_pos = ingest_panel.index("if (e?.batchStopped) throw e", stopped_pos)
        self.assertLess(status_controller_pos, status_track_pos)
        self.assertLess(status_track_pos, status_fetch_pos)
        self.assertLess(status_fetch_pos, status_cleanup_pos)
        self.assertLess(stopped_pos, rethrow_pos)
        self.assertIn("if (componentDisposed) throw batchStoppedError()", ingest_panel)

        start_task_pos = ingest_panel.index("async function startFileTask")
        start_controller_pos = ingest_panel.index("const controller = new AbortController()", start_task_pos)
        start_track_pos = ingest_panel.index("activeTaskStartControllers.add(controller)", start_controller_pos)
        start_fetch_pos = ingest_panel.index(
            "const result = await startIngest(params, { signal: controller.signal })",
            start_track_pos,
        )
        start_cleanup_pos = ingest_panel.index("activeTaskStartControllers.delete(controller)", start_fetch_pos)
        self.assertLess(start_controller_pos, start_track_pos)
        self.assertLess(start_track_pos, start_fetch_pos)
        self.assertLess(start_fetch_pos, start_cleanup_pos)

        connect_pos = ingest_panel.index("function connectWS(taskId)")
        connect_guard_pos = ingest_panel.index("if (componentDisposed) return", connect_pos)
        message_guard_pos = ingest_panel.index("if (componentDisposed) {", connect_guard_pos)
        close_pos = ingest_panel.index("wsConnections[taskId]?.close()", message_guard_pos)
        self.assertLess(connect_pos, connect_guard_pos)
        self.assertLess(connect_guard_pos, message_guard_pos)
        self.assertLess(message_guard_pos, close_pos)

        health_pos = ingest_panel.index("async function loadIngestHealth")
        health_controller_pos = ingest_panel.index("const controller = new AbortController()", health_pos)
        health_track_pos = ingest_panel.index("activeHealthControllers.add(controller)", health_controller_pos)
        health_fetch_pos = ingest_panel.index(
            "const data = await healthCheck({ signal: controller.signal })",
            health_track_pos,
        )
        health_guard_pos = ingest_panel.index("if (componentDisposed) return", health_fetch_pos)
        health_assign_pos = ingest_panel.index("ingestHealth.value = data", health_guard_pos)
        health_cleanup_pos = ingest_panel.index("activeHealthControllers.delete(controller)", health_assign_pos)
        self.assertLess(health_controller_pos, health_track_pos)
        self.assertLess(health_track_pos, health_fetch_pos)
        self.assertLess(health_fetch_pos, health_guard_pos)
        self.assertLess(health_guard_pos, health_assign_pos)
        self.assertLess(health_assign_pos, health_cleanup_pos)
        self.assertIn(
            "if (!componentDisposed && !silent && visibleSeq === ingestHealthVisibleSeq) loadingIngestHealth.value = false",
            ingest_panel,
        )

    def test_ingest_panel_ignores_late_results_after_newer_requests(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("let fileLoadSeq = 0", ingest_panel)
        self.assertIn("let taskLoadSeq = 0", ingest_panel)
        self.assertIn("let ingestHealthSeq = 0", ingest_panel)
        self.assertIn("let fileVisibleLoadSeq = 0", ingest_panel)
        self.assertIn("let taskVisibleLoadSeq = 0", ingest_panel)
        self.assertIn("let taskAppendVisibleLoadSeq = 0", ingest_panel)
        self.assertIn("let ingestHealthVisibleSeq = 0", ingest_panel)

        load_files_pos = ingest_panel.index("async function loadFiles")
        file_seq_pos = ingest_panel.index("const seq = ++fileLoadSeq", load_files_pos)
        file_fetch_pos = ingest_panel.index(
            "const data = await getAllIngestFiles(500, { signal: controller.signal })",
            file_seq_pos,
        )
        file_seq_guard_pos = ingest_panel.index("if (seq !== fileLoadSeq) return", file_fetch_pos)
        file_assign_pos = ingest_panel.index("files.value = data.items || []", file_seq_guard_pos)
        file_catch_pos = ingest_panel.index("} catch (e) {", file_assign_pos)
        file_catch_guard_pos = ingest_panel.index("if (seq !== fileLoadSeq) return", file_catch_pos)
        file_loading_pos = ingest_panel.index("visibleSeq === fileVisibleLoadSeq", file_catch_guard_pos)
        self.assertLess(file_seq_pos, file_fetch_pos)
        self.assertLess(file_fetch_pos, file_seq_guard_pos)
        self.assertLess(file_seq_guard_pos, file_assign_pos)
        self.assertLess(file_catch_pos, file_catch_guard_pos)
        self.assertLess(file_catch_guard_pos, file_loading_pos)

        load_tasks_pos = ingest_panel.index("async function loadTasks")
        task_seq_pos = ingest_panel.index("const seq = ++taskLoadSeq", load_tasks_pos)
        task_fetch_pos = ingest_panel.index(
            "const data = await getIngestTasks(limit, offset, { signal: controller.signal })",
            task_seq_pos,
        )
        task_seq_guard_pos = ingest_panel.index("if (seq !== taskLoadSeq) return", task_fetch_pos)
        task_assign_pos = ingest_panel.index("tasks.value =", task_seq_guard_pos)
        task_catch_pos = ingest_panel.index("} catch (e) {", task_assign_pos)
        task_catch_guard_pos = ingest_panel.index("if (seq !== taskLoadSeq) return", task_catch_pos)
        task_loading_pos = ingest_panel.index("visibleSeq === taskVisibleLoadSeq", task_catch_guard_pos)
        self.assertLess(task_seq_pos, task_fetch_pos)
        self.assertLess(task_fetch_pos, task_seq_guard_pos)
        self.assertLess(task_seq_guard_pos, task_assign_pos)
        self.assertLess(task_catch_pos, task_catch_guard_pos)
        self.assertLess(task_catch_guard_pos, task_loading_pos)

        remember_pos = ingest_panel.index("function rememberStartedTask")
        invalidate_pos = ingest_panel.index("taskLoadSeq += 1", remember_pos)
        file_invalidate_pos = ingest_panel.index("fileLoadSeq += 1", invalidate_pos)
        optimistic_pos = ingest_panel.index("tasks.value = mergeTasks", invalidate_pos)
        file_optimistic_pos = ingest_panel.index("files.value = files.value.map", optimistic_pos)
        self.assertLess(invalidate_pos, optimistic_pos)
        self.assertLess(invalidate_pos, file_invalidate_pos)
        self.assertLess(file_invalidate_pos, file_optimistic_pos)

        health_pos = ingest_panel.index("async function loadIngestHealth")
        health_seq_pos = ingest_panel.index("const seq = ++ingestHealthSeq", health_pos)
        health_fetch_pos = ingest_panel.index("const data = await healthCheck({ signal: controller.signal })", health_seq_pos)
        health_seq_guard_pos = ingest_panel.index("if (seq !== ingestHealthSeq) return", health_fetch_pos)
        health_assign_pos = ingest_panel.index("ingestHealth.value = data", health_seq_guard_pos)
        health_loading_pos = ingest_panel.index("visibleSeq === ingestHealthVisibleSeq", health_assign_pos)
        self.assertLess(health_seq_pos, health_fetch_pos)
        self.assertLess(health_fetch_pos, health_seq_guard_pos)
        self.assertLess(health_seq_guard_pos, health_assign_pos)
        self.assertLess(health_assign_pos, health_loading_pos)

    def test_ingest_task_list_does_not_append_during_full_refresh(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        load_tasks_pos = ingest_panel.index("async function loadTasks")
        append_refresh_guard_pos = ingest_panel.index("if (append && loadingTasks.value) return", load_tasks_pos)
        duplicate_guard_pos = ingest_panel.index(
            "if (append ? loadingMoreTasks.value : loadingTasks.value) return",
            append_refresh_guard_pos,
        )
        task_seq_pos = ingest_panel.index("const seq = ++taskLoadSeq", duplicate_guard_pos)
        load_more_pos = ingest_panel.index("function loadMoreTasks()")
        load_more_busy_guard_pos = ingest_panel.index(
            "if (loadingTasks.value || loadingMoreTasks.value) return",
            load_more_pos,
        )
        load_more_count_guard_pos = ingest_panel.index(
            "if (tasks.value.length >= tasksTotal.value) return",
            load_more_busy_guard_pos,
        )
        append_call_pos = ingest_panel.index("loadTasks({ append: true })", load_more_count_guard_pos)

        self.assertLess(append_refresh_guard_pos, duplicate_guard_pos)
        self.assertLess(duplicate_guard_pos, task_seq_pos)
        self.assertLess(load_more_pos, load_more_busy_guard_pos)
        self.assertLess(load_more_busy_guard_pos, load_more_count_guard_pos)
        self.assertLess(load_more_count_guard_pos, append_call_pos)

    def test_ingest_preserve_loaded_task_refresh_keeps_loaded_pages_with_backend_limit(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("const TASKS_BACKEND_PAGE_LIMIT = 200", ingest_panel)

        load_tasks_pos = ingest_panel.index("async function loadTasks")
        loaded_count_pos = ingest_panel.index("const loadedTaskCount = tasks.value.length", load_tasks_pos)
        capped_limit_pos = ingest_panel.index(
            "Math.min(TASKS_BACKEND_PAGE_LIMIT, Math.max(TASKS_PAGE_SIZE, loadedTaskCount))",
            loaded_count_pos,
        )
        target_pos = ingest_panel.index("let targetCount = preserveLoaded && !append", capped_limit_pos)
        loop_pos = ingest_panel.index(
            "while (!componentDisposed && seq === taskLoadSeq && items.length < targetCount)",
            target_pos,
        )
        next_limit_pos = ingest_panel.index(
            "const nextLimit = Math.min(TASKS_BACKEND_PAGE_LIMIT, targetCount - items.length)",
            loop_pos,
        )
        next_fetch_pos = ingest_panel.index(
            "const page = await getIngestTasks(nextLimit, items.length, { signal: controller.signal })",
            next_limit_pos,
        )
        shrink_target_pos = ingest_panel.index("targetCount = Math.min(targetCount, totalCount)", next_fetch_pos)
        preserve_merge_pos = ingest_panel.index(
            "? mergeRefreshedTasks(tasks.value, items).slice(0, targetCount)",
            shrink_target_pos,
        )

        self.assertLess(loaded_count_pos, capped_limit_pos)
        self.assertLess(capped_limit_pos, target_pos)
        self.assertLess(target_pos, loop_pos)
        self.assertLess(loop_pos, next_limit_pos)
        self.assertLess(next_limit_pos, next_fetch_pos)
        self.assertLess(next_fetch_pos, shrink_target_pos)
        self.assertLess(shrink_target_pos, preserve_merge_pos)

    def test_ingest_panel_preserves_stale_file_and_task_lists_during_refresh_failures(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn('v-if="filesError && files.length > 0"', ingest_panel)
        self.assertIn('v-if="loadingFiles && files.length === 0"', ingest_panel)
        self.assertIn('v-else-if="filesError && files.length === 0"', ingest_panel)
        self.assertIn('v-if="tasksError && tasks.length > 0"', ingest_panel)
        self.assertIn('v-if="loadingTasks && tasks.length === 0"', ingest_panel)
        self.assertIn('v-else-if="tasksError && tasks.length === 0"', ingest_panel)
        self.assertIn(".stale-error", ingest_panel)

        file_stale_error_pos = ingest_panel.index('v-if="filesError && files.length > 0"')
        file_loading_pos = ingest_panel.index('v-if="loadingFiles && files.length === 0"', file_stale_error_pos)
        file_empty_error_pos = ingest_panel.index('v-else-if="filesError && files.length === 0"', file_loading_pos)
        file_list_pos = ingest_panel.index('<div v-else class="file-list">', file_empty_error_pos)
        task_stale_error_pos = ingest_panel.index('v-if="tasksError && tasks.length > 0"')
        task_loading_pos = ingest_panel.index('v-if="loadingTasks && tasks.length === 0"', task_stale_error_pos)
        task_empty_error_pos = ingest_panel.index('v-else-if="tasksError && tasks.length === 0"', task_loading_pos)
        task_list_pos = ingest_panel.index('<div v-else class="task-list">', task_empty_error_pos)

        self.assertLess(file_stale_error_pos, file_loading_pos)
        self.assertLess(file_loading_pos, file_empty_error_pos)
        self.assertLess(file_empty_error_pos, file_list_pos)
        self.assertLess(task_stale_error_pos, task_loading_pos)
        self.assertLess(task_loading_pos, task_empty_error_pos)
        self.assertLess(task_empty_error_pos, task_list_pos)

    def test_ingest_changed_files_default_to_incremental_without_index_only_modes(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("if (file.ingest_status === 'never') return 'full'", ingest_panel)
        self.assertIn("if (file.ingest_status === 'changed') return 'incremental'", ingest_panel)
        self.assertIn("if (file.ingest_status === 'changed')", ingest_panel)
        self.assertIn("['incremental', 'full', 'rebuild'].includes(mode.value)", ingest_panel)

    def test_ingest_file_row_disables_actions_from_file_busy_status(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("function isFileBusy(file)", ingest_panel)
        self.assertIn("file.ingest_status === 'running' || file.ingest_status === 'cancel_requested'", ingest_panel)
        self.assertGreaterEqual(ingest_panel.count("isFileBusy(f)"), 3)

    def test_ingest_batch_disables_from_file_busy_status_when_tasks_are_stale(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("const hasBusyFile = computed(() => files.value.some(isFileBusy))", ingest_panel)
        self.assertIn("ingestStartLocked || hasBusyFile || pendingFiles.length === 0", ingest_panel)
        self.assertIn("if (ingestStartLocked.value || hasBusyFile.value) return", ingest_panel)

    def test_ingest_starting_task_state_disables_other_start_actions(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("const hasStartingTask = computed(() => startingFileIds.value.size > 0)", ingest_panel)
        self.assertIn("hasStartingTask.value", ingest_panel)
        self.assertIn(":disabled=\"ingestStartLocked || isFileBusy(f)\"", ingest_panel)
        self.assertIn("if (ingestStartLocked.value || isFileBusy(f)) return", ingest_panel)
        self.assertIn("if (ingestStartLocked.value || hasBusyFile.value) return", ingest_panel)

        start_import_pos = ingest_panel.index("async function startImport")
        guard_pos = ingest_panel.index(
            "if (ingestStartLocked.value || isFileBusy(f)) return",
            start_import_pos,
        )
        set_busy_pos = ingest_panel.index("setBusy(startingFileIds, key, true)", guard_pos)
        batch_pos = ingest_panel.index("async function startBatchImport")
        batch_guard_pos = ingest_panel.index(
            "if (ingestStartLocked.value || hasBusyFile.value) return",
            batch_pos,
        )
        batch_queue_pos = ingest_panel.index("const queue = pendingFiles.value.slice()", batch_guard_pos)

        self.assertLess(guard_pos, set_busy_pos)
        self.assertLess(batch_guard_pos, batch_queue_pos)

    def test_ingest_upload_state_disables_start_actions(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn(
            "() => uploading.value || batchRunning.value || hasStartingTask.value || hasRunningTask.value || hasBusyFile.value",
            ingest_panel,
        )
        self.assertIn(":disabled=\"ingestStartLocked || hasBusyFile || pendingFiles.length === 0\"", ingest_panel)
        self.assertGreaterEqual(ingest_panel.count(":disabled=\"ingestStartLocked || isFileBusy(f)\""), 2)
        self.assertIn("if (ingestStartLocked.value || isFileBusy(f)) return", ingest_panel)
        self.assertIn("if (ingestStartLocked.value || hasBusyFile.value) return", ingest_panel)

        upload_state_pos = ingest_panel.index("const uploading = ref(false)")
        lock_pos = ingest_panel.index("const ingestStartLocked = computed", upload_state_pos)
        start_import_pos = ingest_panel.index("async function startImport")
        import_guard_pos = ingest_panel.index("if (ingestStartLocked.value || isFileBusy(f)) return", start_import_pos)
        batch_pos = ingest_panel.index("async function startBatchImport")
        batch_guard_pos = ingest_panel.index("if (ingestStartLocked.value || hasBusyFile.value) return", batch_pos)

        self.assertLess(upload_state_pos, lock_pos)
        self.assertLess(start_import_pos, import_guard_pos)
        self.assertLess(batch_pos, batch_guard_pos)

    def test_ingest_live_refresh_continues_when_file_is_busy_but_tasks_are_stale(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        refresh_pos = ingest_panel.index("async function refreshLiveTaskState")
        guard_pos = ingest_panel.index("!hasRunningTask.value && !hasBusyFile.value", refresh_pos)
        load_tasks_pos = ingest_panel.index("loadTasks({ silent: true, preserveLoaded: true })", guard_pos)
        load_files_pos = ingest_panel.index("loadFiles({ silent: true })", guard_pos)

        self.assertLess(refresh_pos, guard_pos)
        self.assertLess(guard_pos, load_tasks_pos)
        self.assertLess(guard_pos, load_files_pos)

    def test_ingest_task_is_visible_and_connected_before_refreshing_lists(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("function rememberStartedTask(file, result, mode)", ingest_panel)
        self.assertIn("tasks.value = mergeTasks(tasks.value", ingest_panel)
        self.assertIn("files.value = files.value.map", ingest_panel)
        self.assertIn("ingest_status: 'running'", ingest_panel)
        self.assertIn("task_status: 'running'", ingest_panel)
        self.assertIn("task_mode: startedMode", ingest_panel)
        remember_pos = ingest_panel.index("rememberStartedTask(f, result, mode)")
        connect_pos = ingest_panel.index("connectWS(result.task_id)")
        refresh_call_pos = ingest_panel.index("refreshStartedTaskLists()", connect_pos)
        refresh_def_pos = ingest_panel.index("function refreshStartedTaskLists()")
        silent_load_pos = ingest_panel.index("loadTasks({ silent: true, preserveLoaded: true })", refresh_def_pos)
        remember_def_pos = ingest_panel.index("function rememberStartedTask")
        task_merge_pos = ingest_panel.index("tasks.value = mergeTasks", remember_def_pos)
        file_map_pos = ingest_panel.index("files.value = files.value.map", task_merge_pos)

        self.assertLess(remember_pos, connect_pos)
        self.assertLess(connect_pos, refresh_call_pos)
        self.assertLess(refresh_call_pos, refresh_def_pos)
        self.assertLess(refresh_def_pos, silent_load_pos)
        self.assertLess(task_merge_pos, file_map_pos)

    def test_chat_batch_delete_clears_selection_after_success(self) -> None:
        chat_view = (FRONTEND_ROOT / "src" / "views" / "ChatView.vue").read_text(encoding="utf-8")
        sidebar = (FRONTEND_ROOT / "src" / "components" / "SessionSidebar.vue").read_text(encoding="utf-8")

        self.assertIn(':selection-clear-token="batchSelectionClearToken"', chat_view)
        self.assertIn("const batchSelectionClearToken = ref(0)", chat_view)
        self.assertIn("batchSelectionClearToken.value += 1", chat_view)
        self.assertIn("selectionClearToken: { type: Number, default: 0 }", sidebar)
        self.assertIn("watch(() => props.selectionClearToken", sidebar)

    def test_session_sidebar_drops_busy_sessions_from_batch_selection(self) -> None:
        sidebar = (FRONTEND_ROOT / "src" / "components" / "SessionSidebar.vue").read_text(encoding="utf-8")

        self.assertIn("const busyIds = new Set(sessions.filter(isBusy).map(session => session.session_id))", sidebar)
        self.assertIn("&& !busyIds.has(id)", sidebar)
        self.assertIn("if (!session || isBusy(session)) return", sidebar)

    def test_session_sidebar_does_not_rename_while_locked_or_busy(self) -> None:
        sidebar = (FRONTEND_ROOT / "src" / "components" / "SessionSidebar.vue").read_text(encoding="utf-8")
        chat_view = (FRONTEND_ROOT / "src" / "views" / "ChatView.vue").read_text(encoding="utf-8")

        self.assertIn(':disabled="locked || isBusy(s)" title="重命名"', sidebar)
        self.assertIn("watch(() => props.locked", sidebar)
        self.assertIn("if (locked) cancelRename()", sidebar)
        self.assertIn("if (props.locked || isBusy(session)) return", sidebar)
        self.assertIn("if (props.locked) {\n    cancelRename()\n    return\n  }", sidebar)
        self.assertIn("if (isGenerating.value) {\n    toast('请先停止当前生成', 'info')\n    return\n  }", chat_view)

        locked_watch_pos = sidebar.index("watch(() => props.locked")
        cancel_pos = sidebar.index("if (locked) cancelRename()", locked_watch_pos)
        start_rename_pos = sidebar.index("function startRename(session)")
        start_guard_pos = sidebar.index("if (props.locked || isBusy(session)) return", start_rename_pos)
        confirm_pos = sidebar.index("function confirmRename(sid)")
        confirm_guard_pos = sidebar.index("if (props.locked) {", confirm_pos)
        emit_pos = sidebar.index("emit('rename'", confirm_guard_pos)
        handle_rename_pos = chat_view.index("async function handleRename")
        generating_guard_pos = chat_view.index("if (isGenerating.value) {", handle_rename_pos)
        controller_pos = chat_view.index("const controller = sessionMutationController()", generating_guard_pos)

        self.assertLess(locked_watch_pos, cancel_pos)
        self.assertLess(start_rename_pos, start_guard_pos)
        self.assertLess(confirm_pos, confirm_guard_pos)
        self.assertLess(confirm_guard_pos, emit_pos)
        self.assertLess(handle_rename_pos, generating_guard_pos)
        self.assertLess(generating_guard_pos, controller_pos)

    def test_session_sidebar_uses_defined_status_color_tokens_and_handles_bad_dates(self) -> None:
        sidebar = (FRONTEND_ROOT / "src" / "components" / "SessionSidebar.vue").read_text(encoding="utf-8")
        css_tokens = (FRONTEND_ROOT / "src" / "index.css").read_text(encoding="utf-8")

        self.assertNotIn("--accent-amber", sidebar)
        self.assertIn("--accent-yellow", css_tokens)
        self.assertIn("if (Number.isNaN(d.getTime())) return ''", sidebar)

    def test_session_sidebar_icon_actions_are_touch_friendly(self) -> None:
        sidebar = (FRONTEND_ROOT / "src" / "components" / "SessionSidebar.vue").read_text(encoding="utf-8")

        self.assertIn(".session-checkbox input {\n  width: 18px;\n  height: 18px;", sidebar)
        self.assertIn(".session-actions .btn-ghost.btn-icon {\n  width: 32px;\n  height: 32px;", sidebar)

    def test_session_sidebar_mobile_cards_keep_predictable_scroll_width(self) -> None:
        sidebar = (FRONTEND_ROOT / "src" / "components" / "SessionSidebar.vue").read_text(encoding="utf-8")

        self.assertIn("flex: 0 0 min(280px, calc(100vw - var(--space-6)));", sidebar)
        self.assertIn("flex-wrap: wrap;", sidebar)
        self.assertIn("-webkit-line-clamp: 2;", sidebar)
        self.assertIn("white-space: nowrap;", sidebar)

    def test_time_formatters_hide_invalid_dates(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")
        logs_panel = (FRONTEND_ROOT / "src" / "components" / "LogsPanel.vue").read_text(encoding="utf-8")
        stats_panel = (FRONTEND_ROOT / "src" / "components" / "StatsPanel.vue").read_text(encoding="utf-8")

        self.assertIn("if (Number.isNaN(date.getTime())) return '—'", ingest_panel)
        self.assertIn("if (Number.isNaN(date.getTime())) return ''", logs_panel)
        self.assertIn("if (Number.isNaN(date.getTime())) return '—'", stats_panel)

    def test_settings_tabs_do_not_force_horizontal_overflow_on_mobile(self) -> None:
        settings_view = (FRONTEND_ROOT / "src" / "views" / "SettingsView.vue").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", settings_view)
        self.assertIn("min-height: 36px;", settings_view)
        self.assertNotIn("flex-wrap: nowrap;", settings_view)

    def test_stats_tables_use_fixed_columns_for_mobile_fit(self) -> None:
        stats_panel = (FRONTEND_ROOT / "src" / "components" / "StatsPanel.vue").read_text(encoding="utf-8")
        css_tokens = (FRONTEND_ROOT / "src" / "index.css").read_text(encoding="utf-8")

        self.assertIn('class="stats-table threads-table"', stats_panel)
        self.assertIn('class="stats-table senders-table"', stats_panel)
        self.assertIn(".stats-table {\n  table-layout: fixed;", stats_panel)
        self.assertIn(".stats-table th,\n  .stats-table td", stats_panel)
        self.assertIn("min-height: 32px;", css_tokens)

    def test_stats_tables_use_response_offset_for_pagination_state(self) -> None:
        stats_panel = (FRONTEND_ROOT / "src" / "components" / "StatsPanel.vue").read_text(encoding="utf-8")

        self.assertIn("const statsError = computed(() =>", stats_panel)
        self.assertIn("概览加载失败", stats_panel)
        self.assertIn("会话统计加载失败", stats_panel)
        self.assertIn("发送人统计加载失败", stats_panel)
        self.assertIn("function safePageOffset(value, fallback)", stats_panel)
        self.assertIn("const pageOffset = safePageOffset(data.offset, safeOffset)", stats_panel)
        self.assertIn(
            "threadsPage.value = { total_count: data.total_count, returned: data.returned, offset: pageOffset }",
            stats_panel,
        )
        self.assertIn(
            "sendersPage.value = { total_count: data.total_count, returned: data.returned, offset: pageOffset }",
            stats_panel,
        )
        self.assertIn("threadsOffset.value = pageOffset", stats_panel)
        self.assertIn("sendersOffset.value = pageOffset", stats_panel)

    def test_stats_tables_keep_applied_page_size_when_limit_refresh_fails(self) -> None:
        stats_panel = (FRONTEND_ROOT / "src" / "components" / "StatsPanel.vue").read_text(encoding="utf-8")

        self.assertIn("const threadsAppliedLimit = ref(20)", stats_panel)
        self.assertIn("const sendersAppliedLimit = ref(20)", stats_panel)
        self.assertIn("function safePageLimit(value, fallback)", stats_panel)

        threads_pos = stats_panel.index("async function loadThreads(offset)")
        threads_requested_pos = stats_panel.index(
            "const requestedLimit = safePageLimit(threadsLimit.value, threadsAppliedLimit.value)",
            threads_pos,
        )
        threads_fetch_pos = stats_panel.index("const data = await getThreads(requestedLimit, safeOffset", threads_requested_pos)
        threads_limit_commit_pos = stats_panel.index("threadsLimit.value = requestedLimit", threads_fetch_pos)
        threads_applied_commit_pos = stats_panel.index("threadsAppliedLimit.value = requestedLimit", threads_limit_commit_pos)
        threads_catch_pos = stats_panel.index("} catch (e) {", threads_applied_commit_pos)
        threads_revert_pos = stats_panel.index(
            "if (threadsLoaded.value) threadsLimit.value = threadsAppliedLimit.value",
            threads_catch_pos,
        )
        threads_error_pos = stats_panel.index("threadsError.value =", threads_revert_pos)

        senders_pos = stats_panel.index("async function loadSendersList(offset)")
        senders_requested_pos = stats_panel.index(
            "const requestedLimit = safePageLimit(sendersLimit.value, sendersAppliedLimit.value)",
            senders_pos,
        )
        senders_fetch_pos = stats_panel.index("const data = await getSenders(requestedLimit, safeOffset", senders_requested_pos)
        senders_limit_commit_pos = stats_panel.index("sendersLimit.value = requestedLimit", senders_fetch_pos)
        senders_applied_commit_pos = stats_panel.index("sendersAppliedLimit.value = requestedLimit", senders_limit_commit_pos)
        senders_catch_pos = stats_panel.index("} catch (e) {", senders_applied_commit_pos)
        senders_revert_pos = stats_panel.index(
            "if (sendersLoaded.value) sendersLimit.value = sendersAppliedLimit.value",
            senders_catch_pos,
        )
        senders_error_pos = stats_panel.index("sendersError.value =", senders_revert_pos)

        self.assertLess(threads_requested_pos, threads_fetch_pos)
        self.assertLess(threads_fetch_pos, threads_limit_commit_pos)
        self.assertLess(threads_limit_commit_pos, threads_applied_commit_pos)
        self.assertLess(threads_catch_pos, threads_revert_pos)
        self.assertLess(threads_revert_pos, threads_error_pos)
        self.assertLess(senders_requested_pos, senders_fetch_pos)
        self.assertLess(senders_fetch_pos, senders_limit_commit_pos)
        self.assertLess(senders_limit_commit_pos, senders_applied_commit_pos)
        self.assertLess(senders_catch_pos, senders_revert_pos)
        self.assertLess(senders_revert_pos, senders_error_pos)

    def test_stats_refresh_preserves_current_table_pages(self) -> None:
        stats_panel = (FRONTEND_ROOT / "src" / "components" / "StatsPanel.vue").read_text(encoding="utf-8")

        self.assertIn("async function loadAllStats({ preservePagination = false } = {})", stats_panel)
        self.assertIn("loadThreads(preservePagination ? threadsOffset.value : 0)", stats_panel)
        self.assertIn("loadSendersList(preservePagination ? sendersOffset.value : 0)", stats_panel)
        self.assertIn("await loadAllStats({ preservePagination: true })", stats_panel)

        mounted_pos = stats_panel.index("onMounted(async () =>")
        initial_load_pos = stats_panel.index("await loadAllStats()", mounted_pos)
        refresh_pos = stats_panel.index("async function refreshAll()")
        preserving_refresh_pos = stats_panel.index("await loadAllStats({ preservePagination: true })", refresh_pos)
        load_all_pos = stats_panel.index("async function loadAllStats({ preservePagination = false } = {})")
        threads_pos = stats_panel.index("loadThreads(preservePagination ? threadsOffset.value : 0)", load_all_pos)
        senders_pos = stats_panel.index("loadSendersList(preservePagination ? sendersOffset.value : 0)", threads_pos)

        self.assertLess(mounted_pos, initial_load_pos)
        self.assertLess(refresh_pos, preserving_refresh_pos)
        self.assertLess(load_all_pos, threads_pos)
        self.assertLess(threads_pos, senders_pos)

    def test_stats_panel_does_not_show_default_zero_dashboard_after_initial_load_failure(self) -> None:
        stats_panel = (FRONTEND_ROOT / "src" / "components" / "StatsPanel.vue").read_text(encoding="utf-8")

        self.assertIn("const summaryLoaded = ref(false)", stats_panel)
        self.assertIn("const threadsLoaded = ref(false)", stats_panel)
        self.assertIn("const sendersLoaded = ref(false)", stats_panel)
        self.assertIn("const hasStatsData = computed(() => summaryLoaded.value || threadsLoaded.value || sendersLoaded.value)", stats_panel)
        self.assertIn('v-if="loading && !hasStatsData"', stats_panel)
        self.assertIn('v-else-if="statsError && !hasStatsData"', stats_panel)
        self.assertIn('id="btn-retry-stats"', stats_panel)
        self.assertIn('<div v-if="summaryLoaded" class="stats-cards">', stats_panel)
        self.assertIn('v-if="summaryLoaded && summary.time_span"', stats_panel)
        self.assertIn('v-if="summaryLoaded && summary.message_types?.length"', stats_panel)
        self.assertIn("summaryLoaded.value = true", stats_panel)
        self.assertIn("threadsLoaded.value = true", stats_panel)
        self.assertIn("sendersLoaded.value = true", stats_panel)

        initial_loading_pos = stats_panel.index('v-if="loading && !hasStatsData"')
        initial_error_pos = stats_panel.index('v-else-if="statsError && !hasStatsData"')
        dashboard_pos = stats_panel.index("<template v-else>")
        summary_cards_pos = stats_panel.index('<div v-if="summaryLoaded" class="stats-cards">', dashboard_pos)
        summary_success_pos = stats_panel.index("summaryLoaded.value = true")
        summary_clear_error_pos = stats_panel.index("summaryError.value = ''", summary_success_pos)
        threads_success_pos = stats_panel.index("threadsLoaded.value = true")
        senders_success_pos = stats_panel.index("sendersLoaded.value = true")

        self.assertLess(initial_loading_pos, initial_error_pos)
        self.assertLess(initial_error_pos, dashboard_pos)
        self.assertLess(dashboard_pos, summary_cards_pos)
        self.assertLess(summary_success_pos, summary_clear_error_pos)
        self.assertLess(summary_success_pos, threads_success_pos)
        self.assertLess(summary_success_pos, senders_success_pos)

    def test_chat_input_closes_suggestions_when_generation_or_disabled_state_changes(self) -> None:
        chat_input = (FRONTEND_ROOT / "src" / "components" / "ChatInput.vue").read_text(encoding="utf-8")

        self.assertIn("onMounted, onUnmounted, watch", chat_input)
        self.assertIn("() => [props.disabled, props.isGenerating]", chat_input)
        self.assertIn("if (disabled || isGenerating) closeSuggestions()", chat_input)

    def test_chat_input_aborts_stale_suggestion_requests(self) -> None:
        chat_input = (FRONTEND_ROOT / "src" / "components" / "ChatInput.vue").read_text(encoding="utf-8")

        self.assertIn("let activeSuggestController = null", chat_input)
        self.assertIn("activeSuggestController?.abort()", chat_input)
        debounce_pos = chat_input.index("function debounceSuggest()")
        abort_pos = chat_input.index("activeSuggestController?.abort()", debounce_pos)
        controller_pos = chat_input.index("const controller = new AbortController()", abort_pos)
        assign_pos = chat_input.index("activeSuggestController = controller", controller_pos)
        fetch_pos = chat_input.index("const res = await getSuggestions(val, 8, { signal: controller.signal })", assign_pos)
        cleanup_pos = chat_input.index("if (activeSuggestController === controller) activeSuggestController = null", fetch_pos)
        close_pos = chat_input.index("function closeSuggestions()")
        close_abort_pos = chat_input.index("activeSuggestController?.abort()", close_pos)

        self.assertLess(debounce_pos, abort_pos)
        self.assertLess(abort_pos, controller_pos)
        self.assertLess(controller_pos, assign_pos)
        self.assertLess(assign_pos, fetch_pos)
        self.assertLess(fetch_pos, cleanup_pos)
        self.assertLess(close_pos, close_abort_pos)

    def test_chat_input_bounds_suggestion_insertions_and_long_labels(self) -> None:
        chat_input = (FRONTEND_ROOT / "src" / "components" / "ChatInput.vue").read_text(encoding="utf-8")

        self.assertIn("const question = text.value.trim().slice(0, MAX_QUESTION_CHARS)", chat_input)
        self.assertIn("const boundedText = nextText.slice(0, MAX_QUESTION_CHARS)", chat_input)
        self.assertIn("const boundedCursor = Math.min(nextCursor, boundedText.length)", chat_input)
        self.assertIn("inputEl.value?.setSelectionRange(boundedCursor, boundedCursor)", chat_input)
        self.assertIn(".suggestion-value {\n  flex: 1;\n  min-width: 0;", chat_input)
        self.assertIn("overflow-wrap: anywhere;", chat_input)

    def test_chat_input_clears_blur_timer_on_focus_and_unmount(self) -> None:
        chat_input = (FRONTEND_ROOT / "src" / "components" / "ChatInput.vue").read_text(encoding="utf-8")

        self.assertIn("let blurTimer = null", chat_input)

        focus_pos = chat_input.index("function onFocus()")
        focus_clear_pos = chat_input.index("clearTimeout(blurTimer)", focus_pos)
        focus_reset_pos = chat_input.index("blurTimer = null", focus_clear_pos)
        self.assertLess(focus_pos, focus_clear_pos)
        self.assertLess(focus_clear_pos, focus_reset_pos)

        blur_pos = chat_input.index("function onBlur()")
        blur_clear_pos = chat_input.index("clearTimeout(blurTimer)", blur_pos)
        blur_set_pos = chat_input.index("blurTimer = setTimeout", blur_clear_pos)
        blur_reset_pos = chat_input.index("blurTimer = null", blur_set_pos)
        self.assertLess(blur_pos, blur_clear_pos)
        self.assertLess(blur_clear_pos, blur_set_pos)
        self.assertLess(blur_set_pos, blur_reset_pos)

        unmount_pos = chat_input.index("onUnmounted(() =>")
        unmount_clear_pos = chat_input.index("clearTimeout(blurTimer)", unmount_pos)
        unmount_reset_pos = chat_input.index("blurTimer = null", unmount_clear_pos)
        close_pos = chat_input.index("closeSuggestions()", unmount_reset_pos)
        self.assertLess(unmount_pos, unmount_clear_pos)
        self.assertLess(unmount_clear_pos, unmount_reset_pos)
        self.assertLess(unmount_reset_pos, close_pos)

    def test_chat_input_placeholder_does_not_embed_keyboard_shortcuts(self) -> None:
        chat_input = (FRONTEND_ROOT / "src" / "components" / "ChatInput.vue").read_text(encoding="utf-8")

        self.assertIn("输入你的问题…", chat_input)
        self.assertNotIn("Enter 发送", chat_input)
        self.assertNotIn("Shift+Enter", chat_input)

    def test_chat_input_matches_backend_question_length_limit(self) -> None:
        chat_input = (FRONTEND_ROOT / "src" / "components" / "ChatInput.vue").read_text(encoding="utf-8")
        schemas = (ROOT / "backend" / "schemas.py").read_text(encoding="utf-8")

        self.assertIn("const MAX_QUESTION_CHARS = 8000", chat_input)
        self.assertIn(':maxlength="MAX_QUESTION_CHARS"', chat_input)
        self.assertIn("max_length=8000", schemas)

    def test_chat_view_sends_trimmed_question_text(self) -> None:
        chat_view = (FRONTEND_ROOT / "src" / "views" / "ChatView.vue").read_text(encoding="utf-8")

        self.assertIn("const questionText = question.trim()", chat_view)
        self.assertIn("if (isGenerating.value || loadingMessages.value || !questionText) return", chat_view)
        self.assertIn("currentQuestion = questionText", chat_view)
        self.assertIn("content: questionText", chat_view)
        self.assertIn("chatSSE(questionText, activeSessionId.value", chat_view)

    def test_chat_view_disables_send_while_session_messages_are_loading(self) -> None:
        chat_view = (FRONTEND_ROOT / "src" / "views" / "ChatView.vue").read_text(encoding="utf-8")

        self.assertIn(':disabled="loadingMessages"', chat_view)
        self.assertIn("if (isGenerating.value || loadingMessages.value || !questionText) return", chat_view)

        template_pos = chat_view.index("<ChatInput")
        disabled_pos = chat_view.index(':disabled="loadingMessages"', template_pos)
        handle_send_pos = chat_view.index("async function handleSend(question)")
        trim_pos = chat_view.index("const questionText = question.trim()", handle_send_pos)
        guard_pos = chat_view.index("if (isGenerating.value || loadingMessages.value || !questionText) return", trim_pos)
        local_message_pos = chat_view.index("messages.value.push({ id: `local-user-${Date.now()}`", guard_pos)

        self.assertLess(template_pos, disabled_pos)
        self.assertLess(trim_pos, guard_pos)
        self.assertLess(guard_pos, local_message_pos)

    def test_chat_view_caps_stop_partial_answer_payload(self) -> None:
        chat_view = (FRONTEND_ROOT / "src" / "views" / "ChatView.vue").read_text(encoding="utf-8")

        self.assertIn("const MAX_STOP_PARTIAL_ANSWER_CHARS = 20000", chat_view)
        self.assertIn("function stopPartialAnswerPayload(value)", chat_view)
        self.assertIn("return String(value || '').trim().slice(0, MAX_STOP_PARTIAL_ANSWER_CHARS)", chat_view)
        self.assertIn("const stoppedAnswer = stopPartialAnswerPayload(streamingText.value)", chat_view)
        self.assertIn("const partialAnswer = stopPartialAnswerPayload(streamingText.value)", chat_view)

        stop_pos = chat_view.index("async function handleStop()")
        stop_payload_pos = chat_view.index("const stoppedAnswer = stopPartialAnswerPayload(streamingText.value)", stop_pos)
        stop_request_pos = chat_view.index("partial_answer: stoppedAnswer", stop_payload_pos)
        stop_timeout_pos = chat_view.index("{ timeoutMs: STOP_REQUEST_TIMEOUT_MS }", stop_request_pos)
        local_display_pos = chat_view.index("content: streamingText.value + '\\n\\n*（已停止生成）*'", stop_request_pos)
        self.assertLess(stop_payload_pos, stop_request_pos)
        self.assertLess(stop_request_pos, stop_timeout_pos)
        self.assertLess(stop_request_pos, local_display_pos)

        cleanup_pos = chat_view.index("function cleanupActiveStream()")
        cleanup_payload_pos = chat_view.index("const partialAnswer = stopPartialAnswerPayload(streamingText.value)", cleanup_pos)
        cleanup_request_pos = chat_view.index("partial_answer: partialAnswer", cleanup_payload_pos)
        cleanup_timeout_pos = chat_view.index("{ timeoutMs: STOP_REQUEST_TIMEOUT_MS }", cleanup_request_pos)
        self.assertLess(cleanup_payload_pos, cleanup_request_pos)
        self.assertLess(cleanup_request_pos, cleanup_timeout_pos)

    def test_chat_view_invalidates_late_stream_callbacks_on_unmount_cleanup(self) -> None:
        chat_view = (FRONTEND_ROOT / "src" / "views" / "ChatView.vue").read_text(encoding="utf-8")

        cleanup_pos = chat_view.index("function cleanupActiveStream()")
        guard_pos = chat_view.index("if (!currentStream) return", cleanup_pos)
        increment_pos = chat_view.index("activeStreamRun += 1", cleanup_pos)
        abort_pos = chat_view.index("currentStream.abort()", cleanup_pos)

        self.assertLess(guard_pos, increment_pos)
        self.assertLess(increment_pos, abort_pos)

    def test_chat_view_ignores_late_async_results_after_unmount(self) -> None:
        chat_view = (FRONTEND_ROOT / "src" / "views" / "ChatView.vue").read_text(encoding="utf-8")

        self.assertIn("let componentDisposed = false", chat_view)
        self.assertIn("componentDisposed = true", chat_view)
        self.assertIn("let sessionLoadSeq = 0", chat_view)
        self.assertIn("const activeSessionLoadControllers = new Set()", chat_view)
        self.assertIn("const activeSessionMutationControllers = new Set()", chat_view)
        self.assertIn("let activeMessagesController = null", chat_view)
        self.assertIn("let activeOlderMessagesController = null", chat_view)
        self.assertIn("abortListLoads()", chat_view)
        self.assertIn("abortMessageLoads()", chat_view)
        self.assertIn("abortSessionMutations()", chat_view)

        load_sessions_pos = chat_view.index("async function loadSessions({ append = false, force = false } = {})")
        duplicate_guard_pos = chat_view.index(
            "if (append ? loadingMoreSessions.value : (loadingSessions.value && !force)) return",
            load_sessions_pos,
        )
        sessions_seq_pos = chat_view.index("const seq = append ? sessionLoadSeq : ++sessionLoadSeq", load_sessions_pos)
        sessions_abort_pos = chat_view.index("if (!append) abortListLoads()", sessions_seq_pos)
        sessions_controller_pos = chat_view.index("const controller = new AbortController()", sessions_abort_pos)
        sessions_track_pos = chat_view.index("activeSessionLoadControllers.add(controller)", sessions_controller_pos)
        sessions_fetch_pos = chat_view.index(
            "const data = await getSessions(SESSIONS_PAGE_SIZE, offset, { signal: controller.signal })",
            sessions_track_pos,
        )
        sessions_guard_pos = chat_view.index("if (componentDisposed || seq !== sessionLoadSeq) return", sessions_fetch_pos)
        sessions_assign_pos = chat_view.index("sessions.value =", sessions_guard_pos)
        sessions_cleanup_pos = chat_view.index("activeSessionLoadControllers.delete(controller)", sessions_assign_pos)
        self.assertLess(load_sessions_pos, duplicate_guard_pos)
        self.assertLess(duplicate_guard_pos, sessions_seq_pos)
        self.assertLess(sessions_seq_pos, sessions_abort_pos)
        self.assertLess(sessions_abort_pos, sessions_controller_pos)
        self.assertLess(sessions_controller_pos, sessions_track_pos)
        self.assertLess(sessions_track_pos, sessions_fetch_pos)
        self.assertLess(sessions_fetch_pos, sessions_guard_pos)
        self.assertLess(sessions_guard_pos, sessions_assign_pos)
        self.assertLess(sessions_assign_pos, sessions_cleanup_pos)
        self.assertIn("if (!componentDisposed && seq === sessionLoadSeq) toast(e.message, 'error')", chat_view)
        self.assertIn("if (!componentDisposed) {\n      if (append) loadingMoreSessions.value = false", chat_view)
        self.assertIn("else if (seq === sessionLoadSeq) loadingSessions.value = false", chat_view)

        select_pos = chat_view.index("async function selectSession")
        select_seq_pos = chat_view.index("const seq = ++messageLoadSeq", select_pos)
        select_abort_pos = chat_view.index("abortMessageLoads()", select_seq_pos)
        messages_controller_pos = chat_view.index("const controller = new AbortController()", select_abort_pos)
        messages_fetch_pos = chat_view.index(
            "const data = await getMessages(sid, MESSAGES_PAGE_SIZE, 0, { signal: controller.signal })",
            messages_controller_pos,
        )
        messages_guard_pos = chat_view.index(
            "if (componentDisposed || seq !== messageLoadSeq || activeSessionId.value !== sid) return",
            messages_fetch_pos,
        )
        messages_assign_pos = chat_view.index("messages.value = items", messages_guard_pos)
        messages_cleanup_pos = chat_view.index(
            "if (activeMessagesController === controller) activeMessagesController = null",
            messages_assign_pos,
        )
        self.assertLess(select_seq_pos, select_abort_pos)
        self.assertLess(select_abort_pos, messages_controller_pos)
        self.assertLess(messages_controller_pos, messages_fetch_pos)
        self.assertLess(messages_fetch_pos, messages_guard_pos)
        self.assertLess(messages_guard_pos, messages_assign_pos)
        self.assertLess(messages_assign_pos, messages_cleanup_pos)
        self.assertIn(
            "if (!componentDisposed && seq === messageLoadSeq && activeSessionId.value === sid) loadingMessages.value = false",
            chat_view,
        )
        self.assertIn(
            "if (!componentDisposed && seq === messageLoadSeq && activeSessionId.value === sid) loadingOlderMessages.value = false",
            chat_view,
        )
        self.assertIn(
            "const data = await getMessages(sid, MESSAGES_PAGE_SIZE, loadedMessagesCount.value, { signal: controller.signal })",
            chat_view,
        )
        self.assertIn("if (activeOlderMessagesController === controller) activeOlderMessagesController = null", chat_view)

        stop_pos = chat_view.index("streamToAbort?.abort()")
        stop_guard_pos = chat_view.index("if (componentDisposed) return", stop_pos)
        stop_refresh_pos = chat_view.index("loadSessions({ force: true })", stop_guard_pos)
        self.assertLess(stop_pos, stop_guard_pos)
        self.assertLess(stop_guard_pos, stop_refresh_pos)

        self.assertIn("const controller = sessionMutationController()", chat_view)
        self.assertIn("await renameSession(sessionId, title, { signal: controller.signal })", chat_view)
        self.assertIn("releaseSessionMutationController(controller)", chat_view)
        self.assertIn("activeSessionMutationControllers.forEach(controller => controller.abort())", chat_view)
        self.assertIn("activeSessionMutationControllers.clear()", chat_view)

    def test_chat_view_does_not_append_sessions_during_full_refresh(self) -> None:
        chat_view = (FRONTEND_ROOT / "src" / "views" / "ChatView.vue").read_text(encoding="utf-8")

        load_sessions_pos = chat_view.index("async function loadSessions({ append = false, force = false } = {})")
        refresh_guard_pos = chat_view.index("if (append && loadingSessions.value) return", load_sessions_pos)
        duplicate_guard_pos = chat_view.index(
            "if (append ? loadingMoreSessions.value : (loadingSessions.value && !force)) return",
            refresh_guard_pos,
        )
        seq_pos = chat_view.index("const seq = append ? sessionLoadSeq : ++sessionLoadSeq", duplicate_guard_pos)
        load_more_pos = chat_view.index("function loadMoreSessions()")
        load_more_busy_guard_pos = chat_view.index(
            "if (loadingSessions.value || loadingMoreSessions.value) return",
            load_more_pos,
        )
        load_more_count_guard_pos = chat_view.index(
            "if (sessions.value.length >= sessionsTotal.value) return",
            load_more_busy_guard_pos,
        )
        append_call_pos = chat_view.index("loadSessions({ append: true })", load_more_count_guard_pos)

        self.assertLess(refresh_guard_pos, duplicate_guard_pos)
        self.assertLess(duplicate_guard_pos, seq_pos)
        self.assertLess(load_more_pos, load_more_busy_guard_pos)
        self.assertLess(load_more_busy_guard_pos, load_more_count_guard_pos)
        self.assertLess(load_more_count_guard_pos, append_call_pos)

    def test_chat_view_forces_sidebar_refresh_after_session_state_changes(self) -> None:
        chat_view = (FRONTEND_ROOT / "src" / "views" / "ChatView.vue").read_text(encoding="utf-8")

        self.assertIn("async function loadSessions({ append = false, force = false } = {})", chat_view)
        self.assertIn("loadingSessions.value && !force", chat_view)

        done_pos = chat_view.index("onDone(data)")
        done_refresh_pos = chat_view.index("loadSessions({ force: true }) // refresh sidebar", done_pos)
        error_pos = chat_view.index("onError(data)")
        error_refresh_pos = chat_view.index("loadSessions({ force: true })", error_pos)
        stop_pos = chat_view.index("async function handleStop()")
        stop_refresh_pos = chat_view.index("loadSessions({ force: true })", stop_pos)
        rename_pos = chat_view.index("async function handleRename")
        rename_refresh_pos = chat_view.index("await loadSessions({ force: true })", rename_pos)
        delete_pos = chat_view.index("async function handleDelete")
        delete_refresh_pos = chat_view.index("await loadSessions({ force: true })", delete_pos)
        batch_pos = chat_view.index("async function handleBatchDelete")
        batch_refresh_pos = chat_view.index("await loadSessions({ force: true })", batch_pos)

        self.assertLess(done_pos, done_refresh_pos)
        self.assertLess(error_pos, error_refresh_pos)
        self.assertLess(stop_pos, stop_refresh_pos)
        self.assertLess(rename_pos, rename_refresh_pos)
        self.assertLess(delete_pos, delete_refresh_pos)
        self.assertLess(batch_pos, batch_refresh_pos)

    def test_chat_view_advances_history_offset_by_returned_page(self) -> None:
        chat_view = (FRONTEND_ROOT / "src" / "views" / "ChatView.vue").read_text(encoding="utf-8")

        older_filter_pos = chat_view.index(
            "const older = items.filter(message => !message.id || !existingIds.has(message.id))"
        )
        prepend_pos = chat_view.index("messages.value = [...older, ...messages.value]", older_filter_pos)
        offset_pos = chat_view.index("loadedMessagesCount.value += items.length", prepend_pos)

        self.assertLess(older_filter_pos, prepend_pos)
        self.assertLess(prepend_pos, offset_pos)
        self.assertNotIn("loadedMessagesCount.value += older.length", chat_view)
        self.assertIn("await deleteSession(sessionId, { signal: controller.signal })", chat_view)
        self.assertIn("const result = await batchDeleteSessions(ids, { signal: controller.signal })", chat_view)
        self.assertIn("activeSessionLoadControllers.forEach(controller => controller.abort())", chat_view)
        self.assertIn("activeSessionLoadControllers.clear()", chat_view)
        self.assertIn("activeMessagesController?.abort()", chat_view)
        self.assertIn("activeOlderMessagesController?.abort()", chat_view)

    def test_chat_view_advances_history_offset_after_persisted_local_exchange(self) -> None:
        chat_view = (FRONTEND_ROOT / "src" / "views" / "ChatView.vue").read_text(encoding="utf-8")

        helper_pos = chat_view.index("function recordPersistedLocalExchange()")
        loaded_increment_pos = chat_view.index("loadedMessagesCount.value += 2", helper_pos)
        total_increment_pos = chat_view.index("messagesTotal.value += 2", loaded_increment_pos)

        done_pos = chat_view.index("onDone(data)")
        assistant_push_pos = chat_view.index("messages.value.push({", done_pos)
        done_record_pos = chat_view.index("recordPersistedLocalExchange()", assistant_push_pos)
        done_refresh_pos = chat_view.index("loadSessions({ force: true }) // refresh sidebar", done_record_pos)

        error_pos = chat_view.index("onError(data)")
        error_refresh_pos = chat_view.index("loadSessions({ force: true })", error_pos)
        error_block = chat_view[error_pos:error_refresh_pos]

        stop_pos = chat_view.index("async function handleStop()")
        abort_result_pos = chat_view.index(
            "const abortResult = await Promise.race([abortRequest, delay(STOP_REQUEST_TIMEOUT_MS)])",
            stop_pos,
        )
        stop_record_pos = chat_view.index(
            "if (abortResult && abortSessionId && stoppedQuestion) recordPersistedLocalExchange()",
            abort_result_pos,
        )
        stop_refresh_pos = chat_view.index("loadSessions({ force: true })", stop_record_pos)

        self.assertLess(helper_pos, loaded_increment_pos)
        self.assertLess(loaded_increment_pos, total_increment_pos)
        self.assertLess(assistant_push_pos, done_record_pos)
        self.assertLess(done_record_pos, done_refresh_pos)
        self.assertNotIn("recordPersistedLocalExchange()", error_block)
        self.assertLess(abort_result_pos, stop_record_pos)
        self.assertLess(stop_record_pos, stop_refresh_pos)

    def test_health_panel_links_diagnostics_to_actionable_settings_tabs(self) -> None:
        health_panel = (FRONTEND_ROOT / "src" / "components" / "HealthPanel.vue").read_text(encoding="utf-8")

        self.assertIn("import { useRouter } from 'vue-router'", health_panel)
        self.assertIn("function actionTarget(check)", health_panel)
        self.assertIn("Object.prototype.hasOwnProperty.call(check || {}, 'action_target')", health_panel)
        self.assertIn("return check.action_target || null", health_panel)
        self.assertIn("database: 'ingest'", health_panel)
        self.assertIn("vector_index: 'ingest'", health_panel)
        self.assertIn("chat_model: 'settings'", health_panel)
        self.assertNotIn("embedding_model: 'settings'", health_panel)
        self.assertIn("chat_sessions: 'logs'", health_panel)
        self.assertIn("router.replace({", health_panel)
        self.assertIn("const loadError = ref('')", health_panel)
        self.assertIn("健康诊断加载失败", health_panel)
        self.assertIn('id="btn-retry-health"', health_panel)
        self.assertIn('v-else-if="health.has_data === false"', health_panel)
        self.assertIn("暂无数据", health_panel)
        self.assertIn("function healthSummaryFromDiagnostics(data = {})", health_panel)
        self.assertIn("const totalMessages = Number(dbStats.total_messages ?? 0)", health_panel)
        self.assertIn("const summaryStatus = data.summary_status || {}", health_panel)
        self.assertIn("summary_model_configured: Boolean(summaryStatus.configured)", health_panel)
        self.assertIn("summary_model: summaryStatus.model || ''", health_panel)
        self.assertIn("summary_model_missing: summaryStatus.missing || []", health_panel)
        self.assertIn("vector_index_available: Boolean(data.vector_index_available)", health_panel)
        self.assertIn("has_data: totalMessages > 0", health_panel)
        self.assertIn("const d = await healthDiagnostics({ signal: controller.signal })", health_panel)
        self.assertNotIn("healthCheck({ signal: controller.signal })", health_panel)

    def test_health_panel_preserves_stale_diagnostics_during_refresh_failures(self) -> None:
        health_panel = (FRONTEND_ROOT / "src" / "components" / "HealthPanel.vue").read_text(encoding="utf-8")

        self.assertIn("import { ref, computed, onMounted, onUnmounted, inject } from 'vue'", health_panel)
        self.assertIn("const hasDiagnosticData = computed(() => Boolean(health.value.status || diagnostics.value.overall))", health_panel)
        self.assertIn('v-if="loading && !hasDiagnosticData"', health_panel)
        self.assertIn('v-else-if="loadError && !hasDiagnosticData"', health_panel)
        self.assertIn('<div v-if="loadError" class="error-state stale-error glass-card">', health_panel)
        self.assertIn('<div v-if="loading" class="spinner spinner-sm"></div>', health_panel)
        self.assertIn(".stale-error", health_panel)
        self.assertIn(".spinner-sm", health_panel)

        initial_loading_pos = health_panel.index('v-if="loading && !hasDiagnosticData"')
        initial_error_pos = health_panel.index('v-else-if="loadError && !hasDiagnosticData"')
        stale_error_pos = health_panel.index('<div v-if="loadError" class="error-state stale-error glass-card">')
        header_pos = health_panel.index('<div class="health-header">', stale_error_pos)
        spinner_pos = health_panel.index('<div v-if="loading" class="spinner spinner-sm"></div>', header_pos)
        svg_pos = health_panel.index("<svg v-else", spinner_pos)

        self.assertLess(initial_loading_pos, initial_error_pos)
        self.assertLess(initial_error_pos, stale_error_pos)
        self.assertLess(stale_error_pos, header_pos)
        self.assertLess(header_pos, spinner_pos)
        self.assertLess(spinner_pos, svg_pos)

    def test_ingest_panel_surfaces_index_diagnostics_from_health_check(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("healthCheck,", ingest_panel)
        self.assertIn("const indexDiagnostics = computed", ingest_panel)
        self.assertIn("['database', 'vector_index'].includes", ingest_panel)
        self.assertIn("loadIngestHealth()", ingest_panel)
        self.assertIn("刷新诊断", ingest_panel)
        self.assertIn("diagnosticComponentLabel", ingest_panel)
        self.assertIn("索引诊断加载失败", ingest_panel)

    def test_ingest_panel_explains_mode_scope_and_uses_backend_start_message(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn(':title="modeHelp(selectedMode(f), f)"', ingest_panel)
        self.assertIn("function modeHelp(mode, file)", ingest_panel)
        self.assertIn("{ value: 'rebuild', label: '强制重建' }", ingest_panel)
        self.assertIn("强制重建其关联索引", ingest_panel)
        self.assertIn("可能调用 embedding API", ingest_panel)
        self.assertIn("不调用模型或 embedding", ingest_panel)
        self.assertIn("关联消息", ingest_panel)
        self.assertIn("全文索引", ingest_panel)
        self.assertIn("toast(result.message ||", ingest_panel)

    def test_ingest_panel_revalidates_selected_mode_before_starting_tasks(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("function selectedMode(file)", ingest_panel)
        self.assertIn("function reconcileSelectedMode(file)", ingest_panel)
        self.assertIn("if (options.some(item => item.value === requested)) return requested", ingest_panel)
        self.assertIn("if (selectedModes[key] !== mode) selectedModes[key] = mode", ingest_panel)

        load_files_pos = ingest_panel.index("async function loadFiles")
        load_reconcile_pos = ingest_panel.index("reconcileSelectedMode(file)", load_files_pos)
        start_pos = ingest_panel.index("async function startFileTask")
        start_reconcile_pos = ingest_panel.index("const mode = reconcileSelectedMode(f)", start_pos)
        params_pos = ingest_panel.index("const params = f.upload_id", start_reconcile_pos)
        health_pos = ingest_panel.index("async function loadIngestHealth")
        health_assign_pos = ingest_panel.index("ingestHealth.value = data", health_pos)
        health_reconcile_pos = ingest_panel.index("reconcileSelectedMode(file)", health_assign_pos)

        self.assertLess(load_files_pos, load_reconcile_pos)
        self.assertLess(start_pos, start_reconcile_pos)
        self.assertLess(start_reconcile_pos, params_pos)
        self.assertLess(health_assign_pos, health_reconcile_pos)

    def test_ingest_panel_disambiguates_same_named_json_files_with_file_path(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn('class="file-path"', ingest_panel)
        self.assertIn(':title="f.file_id"', ingest_panel)
        self.assertIn("function displayFilePath(file)", ingest_panel)
        self.assertIn("shortFileId(fileId)", ingest_panel)

    def test_ingest_panel_shows_per_file_index_status(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("f.session_chunks != null", ingest_panel)
        self.assertIn("会话块 {{ f.session_chunks }}", ingest_panel)
        self.assertIn("缺摘要 {{ f.missing_summary_chunks }}", ingest_panel)
        self.assertIn("缺向量 {{ f.missing_vector_chunks }}", ingest_panel)
        self.assertIn("向量不可用", ingest_panel)
        self.assertIn("hasUnknownIndexStatus(f)", ingest_panel)
        self.assertIn("索引状态未知", ingest_panel)
        self.assertIn("file.ingest_status === 'up_to_date' && file.session_chunks == null", ingest_panel)
        self.assertIn("statusReasonLabel(f.ingest_status_reason)", ingest_panel)
        self.assertIn("parser_version_stale: '解析规则已升级'", ingest_panel)
        self.assertIn("file_changed: '文件已变化'", ingest_panel)

    def test_ingest_panel_reimports_unknown_index_scope_before_index_only_modes(self) -> None:
        ingest_panel = (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8")

        self.assertIn("file.ingest_status === 'up_to_date' && !hasUnknownIndexStatus(file)", ingest_panel)
        self.assertIn("file.ingest_status === 'changed' || hasUnknownIndexStatus(file)", ingest_panel)
        self.assertIn("['incremental', 'full', 'rebuild'].includes(mode.value)", ingest_panel)
        self.assertIn("function canBatchImport(file)", ingest_panel)
        self.assertIn("return file.ingest_status === 'never'", ingest_panel)
        self.assertIn("|| file.ingest_status === 'changed'", ingest_panel)
        self.assertIn("|| hasUnknownIndexStatus(file)", ingest_panel)
        self.assertIn("|| hasIndexGaps(file)", ingest_panel)

    def test_settings_panel_validates_and_normalizes_payload_before_save(self) -> None:
        settings_panel = (FRONTEND_ROOT / "src" / "components" / "SettingsPanel.vue").read_text(encoding="utf-8")

        self.assertIn("const NUMBER_FIELDS = {", settings_panel)
        self.assertIn("function buildSettingsPayload()", settings_panel)
        self.assertIn("function normalizeNumberField(field, rule)", settings_panel)
        self.assertIn("const payload = buildSettingsPayload()", settings_panel)
        self.assertIn("await updateSettings(payload, { signal: controller.signal })", settings_panel)
        self.assertIn("system_prompt: systemPrompt", settings_panel)
        self.assertIn("chat_model: String(form.chat_model || '').trim()", settings_panel)
        self.assertIn("if (form[field] === '' || form[field] == null)", settings_panel)
        self.assertIn("if (!Number.isFinite(value))", settings_panel)
        self.assertIn("if (rule.integer && !Number.isInteger(value))", settings_panel)

    def test_settings_panel_uses_backend_available_tools(self) -> None:
        settings_panel = (FRONTEND_ROOT / "src" / "components" / "SettingsPanel.vue").read_text(encoding="utf-8")

        self.assertIn('v-for="tool in availableTools"', settings_panel)
        self.assertIn("const DEFAULT_AVAILABLE_TOOLS = [", settings_panel)
        self.assertIn("const availableTools = ref([...DEFAULT_AVAILABLE_TOOLS])", settings_panel)
        self.assertIn("function toolsFromSettingsResponse(data)", settings_panel)
        self.assertIn("const backendTools = normalizeToolNames(data?.available_tools)", settings_panel)
        self.assertIn("return normalizeToolNames([...DEFAULT_AVAILABLE_TOOLS, ...normalizeToolNames(data?.enabled_tools)])", settings_panel)
        self.assertIn("function enabledToolsWithinAvailable(enabledTools, tools)", settings_panel)
        self.assertIn("availableTools.value = toolsFromSettingsResponse(data)", settings_panel)
        self.assertIn("form.enabled_tools = enabledToolsWithinAvailable(form.enabled_tools, availableTools.value)", settings_panel)
        self.assertIn("enabled_tools: enabledTools", settings_panel)
        self.assertNotIn("const allTools = ['search_messages', 'semantic_search', 'get_context', 'browse_by_time', 'get_stats']", settings_panel)

    def test_settings_panel_locks_form_controls_while_saving(self) -> None:
        settings_panel = (FRONTEND_ROOT / "src" / "components" / "SettingsPanel.vue").read_text(encoding="utf-8")

        self.assertIn('<fieldset class="form-grid" :disabled="saving">', settings_panel)
        self.assertIn("</fieldset>", settings_panel)
        self.assertIn("min-inline-size: 0;", settings_panel)
        self.assertIn("border: 0;", settings_panel)
        self.assertIn('<button class="btn btn-primary" @click="save" :disabled="saving"', settings_panel)
        self.assertIn('<button class="btn btn-secondary" @click="reset" :disabled="saving"', settings_panel)

        fieldset_pos = settings_panel.index('<fieldset class="form-grid" :disabled="saving">')
        first_input_pos = settings_panel.index('id="input-system-prompt"', fieldset_pos)
        close_pos = settings_panel.index("</fieldset>", first_input_pos)
        actions_pos = settings_panel.index('<div class="form-actions">', close_pos)
        css_pos = settings_panel.index(".form-grid {")
        reset_css_pos = settings_panel.index("border: 0;", css_pos)

        self.assertLess(fieldset_pos, first_input_pos)
        self.assertLess(first_input_pos, close_pos)
        self.assertLess(close_pos, actions_pos)
        self.assertLess(css_pos, reset_css_pos)

    def test_settings_panel_does_not_show_default_form_after_initial_load_failure(self) -> None:
        settings_panel = (FRONTEND_ROOT / "src" / "components" / "SettingsPanel.vue").read_text(encoding="utf-8")

        self.assertIn('v-else-if="loadError"', settings_panel)
        self.assertIn("const loadError = ref('')", settings_panel)
        self.assertIn("loadError.value = `设置加载失败：${e.message}`", settings_panel)
        self.assertIn("loadError.value = ''", settings_panel)
        self.assertIn('id="btn-retry-settings"', settings_panel)
        loading_pos = settings_panel.index('<div v-if="loading"')
        error_pos = settings_panel.index('<div v-else-if="loadError"')
        form_pos = settings_panel.index('<template v-else>')
        self.assertLess(loading_pos, error_pos)
        self.assertLess(error_pos, form_pos)

    def test_settings_panel_ignores_late_async_results_after_unmount(self) -> None:
        settings_panel = (FRONTEND_ROOT / "src" / "components" / "SettingsPanel.vue").read_text(encoding="utf-8")

        self.assertIn("onMounted, onUnmounted", settings_panel)
        self.assertIn("let componentDisposed = false", settings_panel)
        self.assertIn("let activeSettingsLoadController = null", settings_panel)
        self.assertIn("let activeSettingsMutationController = null", settings_panel)
        self.assertIn("let settingsLoadSeq = 0", settings_panel)
        self.assertIn("activeSettingsLoadController?.abort()", settings_panel)
        self.assertIn("activeSettingsLoadController = null", settings_panel)
        self.assertIn("activeSettingsMutationController?.abort()", settings_panel)
        self.assertIn("activeSettingsMutationController = null", settings_panel)

        seq_pos = settings_panel.index("const seq = ++settingsLoadSeq")
        controller_pos = settings_panel.index("const controller = new AbortController()", seq_pos)
        track_pos = settings_panel.index("activeSettingsLoadController = controller", controller_pos)
        load_pos = settings_panel.index("const data = await getSettings({ signal: controller.signal })", track_pos)
        load_guard_pos = settings_panel.index("if (componentDisposed || seq !== settingsLoadSeq) return", load_pos)
        load_assign_pos = settings_panel.index("Object.assign(form, data)", load_guard_pos)
        load_cleanup_pos = settings_panel.index("if (activeSettingsLoadController === controller) activeSettingsLoadController = null", load_assign_pos)
        save_prepare_pos = settings_panel.index("activeSettingsMutationController = controller", load_cleanup_pos)
        save_pos = settings_panel.index("const data = await updateSettings(payload, { signal: controller.signal })")
        save_guard_pos = settings_panel.index("if (componentDisposed) return", save_pos)
        save_toast_pos = settings_panel.index("toast('设置已保存'", save_guard_pos)
        reset_pos = settings_panel.index("const data = await resetSettings({ signal: controller.signal })")
        reset_guard_pos = settings_panel.index("if (componentDisposed) return", reset_pos)
        reset_toast_pos = settings_panel.index("toast('已恢复默认设置'", reset_guard_pos)
        mutation_cleanup_pos = settings_panel.index(
            "if (activeSettingsMutationController === controller) activeSettingsMutationController = null",
            save_toast_pos,
        )

        self.assertLess(seq_pos, controller_pos)
        self.assertLess(controller_pos, track_pos)
        self.assertLess(track_pos, load_pos)
        self.assertLess(load_pos, load_guard_pos)
        self.assertLess(load_guard_pos, load_assign_pos)
        self.assertLess(load_assign_pos, load_cleanup_pos)
        self.assertLess(load_cleanup_pos, save_prepare_pos)
        self.assertLess(save_prepare_pos, save_pos)
        self.assertLess(save_pos, save_guard_pos)
        self.assertLess(save_guard_pos, save_toast_pos)
        self.assertLess(save_toast_pos, mutation_cleanup_pos)
        self.assertLess(reset_pos, reset_guard_pos)
        self.assertLess(reset_guard_pos, reset_toast_pos)
        self.assertIn("if (!componentDisposed && seq === settingsLoadSeq) loading.value = false", settings_panel)
        self.assertIn("if (!componentDisposed) saving.value = false", settings_panel)

    def test_logs_panel_surfaces_load_errors_without_clearing_previous_results(self) -> None:
        logs_panel = (FRONTEND_ROOT / "src" / "components" / "LogsPanel.vue").read_text(encoding="utf-8")

        self.assertIn("const logsError = ref('')", logs_panel)
        self.assertIn('v-if="logsError"', logs_panel)
        self.assertIn("日志加载失败，当前显示上次成功结果", logs_panel)
        self.assertIn("日志加载失败：${e.message}", logs_panel)
        self.assertIn("logsError.value = ''", logs_panel)
        self.assertIn('v-if="loading && logs.length === 0"', logs_panel)
        self.assertIn('v-else-if="!loading && logs.length === 0"', logs_panel)
        self.assertIn('v-if="logs.length > 0"', logs_panel)
        self.assertIn('<div v-if="loading" class="spinner spinner-sm"></div>', logs_panel)
        self.assertNotIn("logs.value = []", logs_panel)
        self.assertNotIn('v-if="!loading && logs.length > 0"', logs_panel)

        initial_loading_pos = logs_panel.index('v-if="loading && logs.length === 0"')
        error_banner_pos = logs_panel.index('v-if="logsError"', initial_loading_pos)
        logs_list_pos = logs_panel.index('v-if="logs.length > 0"', error_banner_pos)
        spinner_pos = logs_panel.index('<div v-if="loading" class="spinner spinner-sm"></div>')
        svg_pos = logs_panel.index("<svg v-else", spinner_pos)
        fetch_pos = logs_panel.index("async function fetchLogs()")
        success_pos = logs_panel.index("logs.value = data", fetch_pos)
        clear_error_pos = logs_panel.index("logsError.value = ''", success_pos)
        catch_pos = logs_panel.index("} catch (e) {", clear_error_pos)
        error_assign_pos = logs_panel.index("logsError.value = logs.value.length", catch_pos)
        toast_pos = logs_panel.index("toast(e.message, 'error')", error_assign_pos)

        self.assertLess(spinner_pos, svg_pos)
        self.assertLess(initial_loading_pos, error_banner_pos)
        self.assertLess(error_banner_pos, logs_list_pos)
        self.assertLess(success_pos, clear_error_pos)
        self.assertLess(clear_error_pos, catch_pos)
        self.assertLess(catch_pos, error_assign_pos)
        self.assertLess(error_assign_pos, toast_pos)

    def test_status_panels_ignore_late_async_results_after_unmount(self) -> None:
        sources = {
            "HealthPanel.vue": (FRONTEND_ROOT / "src" / "components" / "HealthPanel.vue").read_text(encoding="utf-8"),
            "LogsPanel.vue": (FRONTEND_ROOT / "src" / "components" / "LogsPanel.vue").read_text(encoding="utf-8"),
            "StatsPanel.vue": (FRONTEND_ROOT / "src" / "components" / "StatsPanel.vue").read_text(encoding="utf-8"),
        }

        for name, source in sources.items():
            with self.subTest(panel=name):
                self.assertIn("onUnmounted", source)
                self.assertIn("let componentDisposed = false", source)
                self.assertIn("componentDisposed = true", source)
                self.assertIn("if (componentDisposed", source)

        health = sources["HealthPanel.vue"]
        self.assertIn("let activeRefreshController = null", health)
        self.assertIn("activeRefreshController?.abort()", health)
        health_controller_pos = health.index("const controller = new AbortController()")
        health_track_pos = health.index("activeRefreshController = controller", health_controller_pos)
        health_fetch_pos = health.index("const d = await healthDiagnostics({ signal: controller.signal })", health_track_pos)
        health_guard_pos = health.index("if (componentDisposed || seq !== refreshSeq) return", health_fetch_pos)
        health_summary_pos = health.index("health.value = healthSummaryFromDiagnostics(d)", health_guard_pos)
        health_cleanup_pos = health.index("if (activeRefreshController === controller) activeRefreshController = null", health_guard_pos)
        self.assertLess(health_controller_pos, health_track_pos)
        self.assertLess(health_track_pos, health_fetch_pos)
        self.assertLess(health_fetch_pos, health_guard_pos)
        self.assertLess(health_guard_pos, health_summary_pos)
        self.assertLess(health_guard_pos, health_cleanup_pos)
        self.assertIn("if (!componentDisposed && seq === refreshSeq) loading.value = false", health)

        logs = sources["LogsPanel.vue"]
        self.assertIn("let activeLogsController = null", logs)
        self.assertIn("activeLogsController?.abort()", logs)
        logs_controller_pos = logs.index("const controller = new AbortController()")
        logs_track_pos = logs.index("activeLogsController = controller", logs_controller_pos)
        logs_fetch_pos = logs.index("await getLogs(level.value, limit.value, { signal: controller.signal })", logs_track_pos)
        logs_guard_pos = logs.index("if (componentDisposed || seq !== requestSeq) return", logs_fetch_pos)
        logs_cleanup_pos = logs.index("if (activeLogsController === controller) activeLogsController = null", logs_guard_pos)
        self.assertLess(logs_controller_pos, logs_track_pos)
        self.assertLess(logs_track_pos, logs_fetch_pos)
        self.assertLess(logs_fetch_pos, logs_guard_pos)
        self.assertLess(logs_guard_pos, logs_cleanup_pos)
        self.assertIn("if (!componentDisposed && seq === requestSeq) loading.value = false", logs)

        stats = sources["StatsPanel.vue"]
        self.assertIn("let activeSummaryController = null", stats)
        self.assertIn("let activeThreadsController = null", stats)
        self.assertIn("let activeSendersController = null", stats)
        self.assertIn("activeSummaryController?.abort()", stats)
        self.assertIn("activeThreadsController?.abort()", stats)
        self.assertIn("activeSendersController?.abort()", stats)
        stats_fetch_pos = stats.index("const data = await getStatsSummary({ signal: controller.signal })")
        stats_guard_pos = stats.index("if (componentDisposed || seq !== summaryRequestSeq) return", stats_fetch_pos)
        self.assertLess(stats_fetch_pos, stats_guard_pos)
        self.assertIn("const data = await getThreads(requestedLimit, safeOffset, { signal: controller.signal })", stats)
        self.assertIn("const data = await getSenders(requestedLimit, safeOffset, { signal: controller.signal })", stats)
        self.assertIn("if (!componentDisposed) refreshing.value = false", stats)
        self.assertIn("if (!componentDisposed && seq === threadsRequestSeq) threadsLoading.value = false", stats)
        self.assertIn("if (!componentDisposed && seq === sendersRequestSeq) sendersLoading.value = false", stats)

    def test_router_redirects_unknown_paths_to_chat_view(self) -> None:
        router_source = (FRONTEND_ROOT / "src" / "router.js").read_text(encoding="utf-8")

        self.assertIn("{ path: '/:pathMatch(.*)*', redirect: '/' }", router_source)

    def test_app_toast_normalizes_complex_error_objects_without_throwing(self) -> None:
        app_source = (FRONTEND_ROOT / "src" / "App.vue").read_text(encoding="utf-8")

        self.assertIn("function toastTextFromValue(message)", app_source)
        self.assertIn("message?.error?.message", app_source)
        self.assertIn("message?.body?.error?.message", app_source)
        self.assertIn("message?.body?.error?.details", app_source)
        self.assertIn("function detailTextFromValue(detail)", app_source)
        self.assertIn("function detailTextFromValueInner(detail, seen)", app_source)
        self.assertIn("Array.isArray(detail)", app_source)
        self.assertIn("if (seen.has(detail)) return '[Circular]'", app_source)
        self.assertIn("detail.map((item) => detailTextFromValueInner(item, seen)).filter(Boolean).join('；')", app_source)
        self.assertIn("firstDetailTextWithSeen(seen, detail.msg, detail.message, detail.detail, detail.type)", app_source)
        self.assertIn("fieldPathFromDetail(detail.loc || detail.field || detail.path)", app_source)
        self.assertIn("function scalarToastText(value)", app_source)
        self.assertIn("function safeJsonStringify(value)", app_source)
        self.assertIn("const seen = new WeakSet()", app_source)
        self.assertIn("return '[Circular]'", app_source)
        self.assertIn("serialized !== 'null' && serialized !== '{}'", app_source)
        self.assertNotIn("if (message?.detail) return String(message.detail)", app_source)

    def test_app_toast_deduplicates_identical_active_messages(self) -> None:
        app_source = (FRONTEND_ROOT / "src" / "App.vue").read_text(encoding="utf-8")

        self.assertIn("if (toasts.value.some(t => t.type === type && t.message === normalized)) return", app_source)

    def test_chat_messages_marks_tool_error_results_visually(self) -> None:
        chat_messages = (FRONTEND_ROOT / "src" / "components" / "ChatMessages.vue").read_text(encoding="utf-8")

        self.assertIn("const TOOL_ERROR_PREFIXES = ['错误：', '工具执行错误：', '工具参数不是合法 JSON']", chat_messages)
        self.assertIn("function isToolError(evt)", chat_messages)
        self.assertIn("if (evt?.error === true) return true", chat_messages)
        self.assertIn("summary.startsWith(prefix)", chat_messages)
        self.assertIn("isToolError(evt) ? 'tool-badge-error' : 'tool-badge-result'", chat_messages)
        self.assertIn('v-if="isToolError(evt)"', chat_messages)
        self.assertIn(".tool-badge-error", chat_messages)
        self.assertIn("color: var(--accent-red)", chat_messages)

    def test_chat_messages_markdown_sanitizer_blocks_autoloading_remote_media(self) -> None:
        chat_messages = (FRONTEND_ROOT / "src" / "components" / "ChatMessages.vue").read_text(encoding="utf-8")

        self.assertIn("const MARKDOWN_SANITIZE_CONFIG = {", chat_messages)
        self.assertIn("ALLOWED_ATTR: ['href', 'title', 'target', 'rel']", chat_messages)
        self.assertIn("ALLOWED_URI_REGEXP:", chat_messages)
        self.assertIn(r"\/(?!\/)", chat_messages)
        self.assertIn("FORBID_TAGS: [", chat_messages)
        for tag in ["'img'", "'iframe'", "'video'", "'audio'", "'source'", "'picture'", "'style'"]:
            self.assertIn(tag, chat_messages)
        self.assertIn("DOMPurify.addHook('afterSanitizeAttributes'", chat_messages)
        self.assertIn("node.setAttribute('target', '_blank')", chat_messages)
        self.assertIn("node.setAttribute('rel', 'noopener noreferrer')", chat_messages)
        self.assertIn("DOMPurify.sanitize(marked.parse(text), MARKDOWN_SANITIZE_CONFIG)", chat_messages)

    def test_chat_markdown_headings_are_sized_for_message_bubbles(self) -> None:
        css = (FRONTEND_ROOT / "src" / "index.css").read_text(encoding="utf-8")

        self.assertIn(".markdown-body h1,\n.markdown-body h2,\n.markdown-body h3,\n.markdown-body h4", css)
        self.assertIn("letter-spacing: 0;", css)
        self.assertIn(".markdown-body h1 { font-size: var(--text-xl); }", css)
        self.assertIn(".markdown-body h2 { font-size: var(--text-lg); }", css)
        self.assertIn(".markdown-body h3,\n.markdown-body h4,\n.markdown-body h5,\n.markdown-body h6", css)

    def test_chat_messages_clears_stale_prepend_anchor_when_older_page_adds_no_messages(self) -> None:
        chat_messages = (FRONTEND_ROOT / "src" / "components" / "ChatMessages.vue").read_text(encoding="utf-8")

        self.assertIn("const pendingPrependAnchor = ref(null)", chat_messages)
        self.assertIn("function restorePrependScroll()", chat_messages)
        self.assertIn("watch(\n  () => props.messages.length", chat_messages)
        self.assertIn("if (pendingPrependAnchor.value && length > previousLength)", chat_messages)
        self.assertIn("restorePrependScroll()", chat_messages)
        self.assertIn("watch(\n  () => props.loadingOlder", chat_messages)
        self.assertIn("if (!loading && wasLoading && pendingPrependAnchor.value)", chat_messages)
        self.assertIn("pendingPrependAnchor.value = null", chat_messages)

        length_watch_pos = chat_messages.index("watch(\n  () => props.messages.length")
        restore_pos = chat_messages.index("restorePrependScroll()", length_watch_pos)
        loading_watch_pos = chat_messages.index("watch(\n  () => props.loadingOlder", restore_pos)
        clear_pos = chat_messages.index("pendingPrependAnchor.value = null", loading_watch_pos)

        self.assertLess(length_watch_pos, restore_pos)
        self.assertLess(restore_pos, loading_watch_pos)
        self.assertLess(loading_watch_pos, clear_pos)

    def test_common_panels_keep_repeated_empty_and_alignment_styles_in_css(self) -> None:
        sources = {
            "ChatMessages.vue": (FRONTEND_ROOT / "src" / "components" / "ChatMessages.vue").read_text(encoding="utf-8"),
            "HealthPanel.vue": (FRONTEND_ROOT / "src" / "components" / "HealthPanel.vue").read_text(encoding="utf-8"),
            "IngestPanel.vue": (FRONTEND_ROOT / "src" / "components" / "IngestPanel.vue").read_text(encoding="utf-8"),
            "LogsPanel.vue": (FRONTEND_ROOT / "src" / "components" / "LogsPanel.vue").read_text(encoding="utf-8"),
            "SettingsPanel.vue": (FRONTEND_ROOT / "src" / "components" / "SettingsPanel.vue").read_text(encoding="utf-8"),
            "StatsPanel.vue": (FRONTEND_ROOT / "src" / "components" / "StatsPanel.vue").read_text(encoding="utf-8"),
            "SessionSidebar.vue": (FRONTEND_ROOT / "src" / "components" / "SessionSidebar.vue").read_text(encoding="utf-8"),
        }

        self.assertNotIn('style="width:12px;height:12px;border-width:1.5px;"', sources["ChatMessages.vue"])
        self.assertNotIn('style="margin-left: 4px;"', sources["HealthPanel.vue"])
        self.assertNotIn('style="color: var(--text-muted);"', sources["IngestPanel.vue"])
        self.assertNotIn('style="display:none"', sources["IngestPanel.vue"])
        self.assertNotIn('style="align-self: flex-end;"', sources["LogsPanel.vue"])
        self.assertNotIn('style="padding: 2rem;"', sources["LogsPanel.vue"])
        self.assertNotIn('style="width:14px;height:14px;border-width:1.5px;"', sources["SettingsPanel.vue"])
        self.assertNotIn('style="text-align:center;"', sources["StatsPanel.vue"])
        self.assertNotIn('style="padding: 2rem;"', sources["SessionSidebar.vue"])
        self.assertIn("spinner-tool", sources["ChatMessages.vue"])
        self.assertIn("config-badge", sources["HealthPanel.vue"])
        self.assertIn("file-input-hidden", sources["IngestPanel.vue"])
        self.assertIn("refresh-button", sources["LogsPanel.vue"])
        self.assertIn("spinner-save", sources["SettingsPanel.vue"])
        self.assertIn("empty-table-cell", sources["StatsPanel.vue"])
        self.assertIn("session-empty", sources["SessionSidebar.vue"])

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
