<script setup>
defineProps({
  message: {
    type: Object,
    required: true,
  },
});

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
  <article
    class="flex"
    :class="message.role === 'user' ? 'justify-end' : 'justify-start'"
  >
    <div
      class="max-w-[min(42rem,100%)] rounded-md px-4 py-3"
      :class="
        message.role === 'user'
          ? 'bg-primary text-primary-foreground'
          : 'border border-border bg-background text-foreground'
      "
    >
      <div
        class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs"
        :class="message.role === 'user' ? 'text-primary-foreground/75' : 'text-muted-foreground'"
      >
        <span class="font-medium capitalize">{{ message.role }}</span>
        <span v-if="message.created_at">{{ formatDate(message.created_at) }}</span>
      </div>
      <p class="mt-2 whitespace-pre-wrap break-words text-sm leading-6">
        {{ message.content }}
      </p>
    </div>
  </article>
</template>
