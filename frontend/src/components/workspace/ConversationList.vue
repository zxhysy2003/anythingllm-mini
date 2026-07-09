<script setup>
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
  selectedId: {
    type: String,
    default: "",
  },
});

defineEmits(["select", "retry"]);

function formatDate(value) {
  if (!value) {
    return "";
  }

  try {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return "";
  }
}
</script>

<template>
  <div class="space-y-2">
    <div v-if="loading" class="space-y-2">
      <div class="h-14 rounded-md border border-border bg-background" />
      <div class="h-14 rounded-md border border-border bg-background" />
    </div>

    <div
      v-else-if="error"
      class="rounded-md border border-destructive/30 bg-destructive/5 p-3"
    >
      <p class="text-xs leading-5 text-destructive">{{ error }}</p>
      <button
        class="mt-2 text-xs font-medium text-foreground underline decoration-border underline-offset-4"
        type="button"
        @click="$emit('retry')"
      >
        Retry
      </button>
    </div>

    <p
      v-else-if="items.length === 0"
      class="rounded-md border border-border bg-background p-3 text-sm leading-5 text-muted-foreground"
    >
      No conversations yet.
    </p>

    <button
      v-for="conversation in items"
      v-else
      :key="conversation.id"
      class="w-full rounded-md border p-3 text-left transition hover:bg-background"
      :class="
        conversation.id === selectedId
          ? 'border-accent bg-background shadow-sm'
          : 'border-border bg-background/70'
      "
      type="button"
      @click="$emit('select', conversation.id)"
    >
      <span class="block truncate text-sm font-medium">{{ conversation.title }}</span>
      <span class="mt-1 block text-xs text-muted-foreground">
        {{ formatDate(conversation.updated_at) }}
      </span>
    </button>
  </div>
</template>
