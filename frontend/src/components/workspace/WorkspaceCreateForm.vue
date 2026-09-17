<script setup>
import { ref, watch } from "vue";

const props = defineProps({
  pending: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: "",
  },
});

const emit = defineEmits(["create"]);
const name = ref("");

watch(
  () => props.pending,
  (pending, wasPending) => {
    if (wasPending && !pending && !props.error) {
      name.value = "";
    }
  },
);

function submit() {
  const trimmed = name.value.trim();

  if (!trimmed || props.pending) {
    return;
  }

  emit("create", trimmed);
}
</script>

<template>
  <form class="space-y-2" @submit.prevent="submit">
    <label class="sr-only" for="workspace-name">Workspace name</label>
    <input
      id="workspace-name"
      v-model="name"
      class="h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none transition placeholder:text-muted-foreground focus:border-accent focus:ring-2 focus:ring-accent/20"
      :disabled="pending"
      maxlength="255"
      placeholder="New workspace"
      type="text"
    />
    <button
      class="inline-flex h-9 w-full items-center justify-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
      :disabled="pending || !name.trim()"
      type="submit"
    >
      {{ pending ? "Creating..." : "Create workspace" }}
    </button>
    <p v-if="error" class="text-xs leading-5 text-destructive">{{ error }}</p>
  </form>
</template>
