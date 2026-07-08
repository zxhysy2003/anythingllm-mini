<script setup>
defineProps({
  status: {
    type: String,
    required: true,
  },
  error: {
    type: String,
    default: "",
  },
});

defineEmits(["refresh"]);
</script>

<template>
  <button
    class="inline-flex min-w-28 items-center justify-center gap-2 rounded-md border border-border px-3 py-2 text-xs font-medium transition hover:bg-muted"
    type="button"
    :title="error || 'Refresh backend health'"
    @click="$emit('refresh')"
  >
    <span
      class="h-2 w-2 rounded-full"
      :class="{
        'bg-emerald-500': status === 'ok',
        'bg-amber-500': status === 'checking' || status === 'unknown',
        'bg-destructive': status === 'offline',
      }"
    />
    <span>
      {{ status === "ok" ? "Backend online" : status === "checking" ? "Checking" : "Backend offline" }}
    </span>
  </button>
</template>
