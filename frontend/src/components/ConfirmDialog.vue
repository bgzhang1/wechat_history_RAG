<template>
  <Teleport to="body">
    <div class="confirm-backdrop" role="presentation" @click.self="requestCancel" @keydown.esc.stop.prevent="requestCancel">
      <section
        class="confirm-dialog glass-card-elevated"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        :aria-describedby="messageId"
      >
        <div class="confirm-icon" :class="{ danger }">
          <svg v-if="danger" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
            <line x1="12" y1="9" x2="12" y2="13"/>
            <line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <svg v-else width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/>
            <path d="M12 16v-4"/>
            <path d="M12 8h.01"/>
          </svg>
        </div>

        <div class="confirm-copy">
          <h2 :id="titleId" class="confirm-title">{{ title }}</h2>
          <p :id="messageId" class="confirm-message">{{ message }}</p>
        </div>

        <div class="confirm-actions">
          <button ref="cancelButton" class="btn btn-secondary" type="button" :disabled="busy" @click="requestCancel">
            {{ cancelText }}
          </button>
          <button :class="['btn', danger ? 'btn-danger' : 'btn-primary']" type="button" :disabled="busy" @click="$emit('confirm')">
            <div v-if="busy" class="spinner spinner-confirm"></div>
            {{ busy ? busyText : confirmText }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { nextTick, onMounted, ref } from 'vue'

const props = defineProps({
  title: { type: String, required: true },
  message: { type: String, required: true },
  confirmText: { type: String, default: '确认' },
  cancelText: { type: String, default: '取消' },
  busyText: { type: String, default: '处理中…' },
  danger: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm', 'cancel'])
const cancelButton = ref(null)
const titleId = `confirm-title-${Math.random().toString(36).slice(2)}`
const messageId = `confirm-message-${Math.random().toString(36).slice(2)}`

onMounted(() => {
  nextTick(() => cancelButton.value?.focus())
})

function requestCancel() {
  if (props.busy) return
  emit('cancel')
}
</script>

<style scoped>
.confirm-backdrop {
  position: fixed;
  inset: 0;
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4);
  background: rgba(2, 6, 23, 0.72);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  animation: fadeIn var(--transition-fast) ease-out;
}

.confirm-dialog {
  width: min(420px, 100%);
  padding: var(--space-5);
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-4);
}

.confirm-icon {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent-blue);
  background: rgba(59, 130, 246, 0.12);
}

.confirm-icon.danger {
  color: var(--accent-red);
  background: rgba(239, 68, 68, 0.12);
}

.confirm-copy {
  min-width: 0;
}

.confirm-title {
  font-size: var(--text-lg);
  line-height: 1.4;
  margin-bottom: var(--space-1);
}

.confirm-message {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: 1.6;
}

.confirm-actions {
  grid-column: 1 / -1;
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.spinner-confirm {
  width: 14px;
  height: 14px;
  border-width: 1.5px;
}

@media (max-width: 480px) {
  .confirm-dialog {
    grid-template-columns: 1fr;
  }

  .confirm-actions {
    flex-direction: column-reverse;
  }
}
</style>
