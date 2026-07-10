<script setup>
import { computed } from "vue";

const props = defineProps({
  message: {
    type: Object,
    required: true,
  },
});

const metricChips = computed(() => {
  const metrics = props.message.metrics || {};
  const chips = [];

  if (typeof metrics.total_latency_ms === "number") {
    chips.push(`${metrics.total_latency_ms} ms`);
  }

  if (metrics.agent_mode) {
    chips.push(metrics.agent_mode);
  }

  if (typeof metrics.step_count === "number") {
    chips.push(`${metrics.step_count} steps`);
  }

  if (typeof metrics.tool_call_count === "number") {
    chips.push(`${metrics.tool_call_count} tools`);
  }

  const sourceCount =
    typeof metrics.source_count === "number"
      ? metrics.source_count
      : metrics.used_source_count;
  if (typeof sourceCount === "number") {
    chips.push(`${sourceCount} sources`);
  }

  return chips;
});

const providerLabel = computed(() =>
  [props.message.provider, props.message.model].filter(Boolean).join(" / "),
);

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

      <div
        v-if="providerLabel || metricChips.length"
        class="mt-3 flex flex-wrap items-center gap-2 text-xs"
        :class="message.role === 'user' ? 'text-primary-foreground/80' : 'text-muted-foreground'"
      >
        <span v-if="providerLabel" class="break-all">{{ providerLabel }}</span>
        <span
          v-for="chip in metricChips"
          :key="chip"
          class="rounded border px-2 py-0.5"
          :class="
            message.role === 'user'
              ? 'border-primary-foreground/30'
              : 'border-border bg-muted/50'
          "
        >
          {{ chip }}
        </span>
      </div>
    </div>
  </article>
</template>
