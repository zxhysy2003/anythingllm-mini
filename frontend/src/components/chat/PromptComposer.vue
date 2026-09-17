<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false,
  },
  pending: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: "",
  },
  hasConversation: {
    type: Boolean,
    default: false,
  },
  successCount: {
    type: Number,
    default: 0,
  },
});

const emit = defineEmits(["submit"]);

const message = ref("");
const runType = ref("chat");
const agentMode = ref("react_text");

const trimmedMessage = computed(() => message.value.trim());
const canSubmit = computed(
  () =>
    props.hasConversation &&
    !props.disabled &&
    !props.pending &&
    Boolean(trimmedMessage.value),
);

function submit() {
  if (!canSubmit.value) {
    return;
  }

  emit("submit", {
    message: trimmedMessage.value,
    runType: runType.value,
    agentMode: agentMode.value,
  });
}

function handleKeydown(event) {
  if (event.isComposing || event.key !== "Enter" || event.shiftKey) {
    return;
  }

  event.preventDefault();
  submit();
}

watch(
  () => props.successCount,
  () => {
    message.value = "";
  },
);
</script>

<template>
  <form class="border-t border-border bg-background p-4" @submit.prevent="submit">
    <div class="space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div
          class="inline-grid grid-cols-2 overflow-hidden rounded-md border border-border bg-muted p-1 text-sm"
        >
          <button
            class="rounded px-3 py-1.5 font-medium transition"
            :class="
              runType === 'chat'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            "
            :disabled="pending"
            type="button"
            @click="runType = 'chat'"
          >
            Chat
          </button>
          <button
            class="rounded px-3 py-1.5 font-medium transition"
            :class="
              runType === 'agent'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            "
            :disabled="pending"
            type="button"
            @click="runType = 'agent'"
          >
            Agent
          </button>
        </div>

        <label
          v-if="runType === 'agent'"
          class="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <span>Mode</span>
          <select
            v-model="agentMode"
            class="h-9 rounded-md border border-border bg-background px-2 text-sm text-foreground outline-none transition focus:border-primary"
            :disabled="pending"
          >
            <option value="react_text">react_text</option>
            <option value="native_tool_calling">native_tool_calling</option>
          </select>
        </label>
      </div>

      <div
        v-if="error"
        class="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
      >
        {{ error }}
      </div>

      <textarea
        v-model="message"
        class="min-h-24 w-full resize-y rounded-md border border-border bg-background px-3 py-3 text-sm leading-6 outline-none transition placeholder:text-muted-foreground focus:border-primary disabled:cursor-not-allowed disabled:bg-muted"
        :disabled="disabled || pending || !hasConversation"
        :placeholder="hasConversation ? 'Ask anything...' : 'Select or create a conversation.'"
        rows="3"
        @keydown="handleKeydown"
      />

      <div class="flex items-center justify-end">
        <button
          class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
          :disabled="!canSubmit"
          type="submit"
        >
          {{ pending ? "Sending..." : "Send" }}
        </button>
      </div>
    </div>
  </form>
</template>
