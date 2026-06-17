import { createRouter, createWebHistory } from 'vue-router'
import ChatView from './views/ChatView.vue'
import SettingsView from './views/SettingsView.vue'

const routes = [
  { path: '/', name: 'chat', component: ChatView, meta: { title: '对话' } },
  { path: '/settings', name: 'settings', component: SettingsView, meta: { title: '设置' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = `${to.meta.title || '对话'} · WeChat RAG`
})

export default router
