<script setup>
import MessageItem from "./MessageItem.vue";

defineProps({
  items: {
    type: Array,
    default: () => [],
  },
  loading: {
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
});

defineEmits(["retry"]);
</script>

<template>
  <div class="flex-1 overflow-y-auto px-5 py-5">
    <div v-if="loading" class="space-y-4">
      <div class="h-24 max-w-2xl rounded-md border border-border bg-muted" />
      <div class="ml-auto h-20 max-w-xl rounded-md bg-primary/10" />
    </div>

    <div
      v-else-if="error"
      class="rounded-md border border-destructive/30 bg-destructive/5 p-4"
    >
      <p class="text-sm leading-6 text-destructive">{{ error }}</p>
      <button
        class="mt-3 text-sm font-medium text-foreground underline decoration-border underline-offset-4"
        type="button"
        @click="$emit('retry')"
      >
        Retry
      </button>
    </div>

    <div
      v-else-if="!hasConversation"
      class="flex min-h-full items-center justify-center text-center"
    >
      <p class="max-w-sm text-sm leading-6 text-muted-foreground">
        Select or create a conversation.
      </p>
    </div>

    <div
      v-else-if="items.length === 0"
      class="flex min-h-full items-center justify-center text-center"
    >
      <p class="max-w-sm text-sm leading-6 text-muted-foreground">
        This conversation has no saved messages.
      </p>
    </div>

    <div v-else class="space-y-4">
      <MessageItem v-for="message in items" :key="message.id" :message="message" />
    </div>
  </div>
</template>
