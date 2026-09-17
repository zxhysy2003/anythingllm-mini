<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  sources: {
    type: Array,
    default: () => [],
  },
  messageId: {
    type: String,
    default: "",
  },
  metrics: {
    type: Object,
    default: () => ({}),
  },
  lowScoreThreshold: {
    type: Number,
    default: null,
  },
});

const emit = defineEmits(["locate"]);

const isOpen = ref(false);
const expandedSourceIndex = ref(-1);

const sourceItems = computed(() =>
  props.sources.map((source, index) => {
    const score = typeof source.score === "number" ? source.score : null;
    const preview = String(source.text || "");

    return {
      chunkLabel: Number.isInteger(source.chunk_index)
        ? `Chunk ${source.chunk_index}`
        : "Chunk unavailable",
      filename: source.original_filename || "Unnamed document",
      index,
      isLowConfidence:
        score !== null &&
        typeof props.lowScoreThreshold === "number" &&
        score < props.lowScoreThreshold,
      preview: preview.length > 180 ? `${preview.slice(0, 180)}...` : preview,
      scoreLabel: score === null ? "Score unavailable" : `Score ${score.toFixed(3)}`,
      text: preview,
    };
  }),
);

const emptyMessage = computed(() => {
  if (!props.messageId) {
    return "Select an assistant message to inspect sources.";
  }
  if (props.metrics.query_refused) {
    return "No source met this workspace's retrieval threshold.";
  }
  if (props.metrics.has_context === false) {
    return "This response used no document context.";
  }
  return "No sources were recorded for this assistant message.";
});

function toggleSource(index) {
  expandedSourceIndex.value = expandedSourceIndex.value === index ? -1 : index;
  if (props.messageId) {
    emit("locate", props.messageId);
  }
}

watch(
  () => props.messageId,
  () => {
    expandedSourceIndex.value = -1;
    isOpen.value = false;
  },
);
</script>

<template>
  <section class="rounded-md border border-border bg-background p-4">
    <button
      class="flex w-full items-center justify-between gap-3 text-left"
      type="button"
      :aria-expanded="isOpen"
      @click="isOpen = !isOpen"
    >
      <span class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Sources
      </span>
      <span class="text-xs text-muted-foreground">{{ sourceItems.length }}</span>
    </button>

    <div v-if="isOpen" class="mt-4 space-y-3">
      <p v-if="sourceItems.length === 0" class="text-sm leading-6 text-muted-foreground">
        {{ emptyMessage }}
      </p>

      <button
        v-for="source in sourceItems"
        :key="`${source.filename}-${source.index}`"
        class="w-full rounded-md border border-border p-3 text-left transition hover:bg-muted/60"
        type="button"
        @click="toggleSource(source.index)"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="truncate text-sm font-medium">{{ source.filename }}</p>
            <p class="mt-1 text-xs text-muted-foreground">{{ source.chunkLabel }}</p>
          </div>
          <span
            class="shrink-0 text-xs"
            :class="source.isLowConfidence ? 'text-destructive' : 'text-muted-foreground'"
          >
            {{ source.scoreLabel }}
          </span>
        </div>

        <p
          class="mt-3 whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground"
          :class="expandedSourceIndex === source.index ? '' : 'max-h-16 overflow-hidden'"
        >
          {{ expandedSourceIndex === source.index ? source.text : source.preview }}
        </p>

        <p v-if="source.isLowConfidence" class="mt-2 text-xs text-destructive">
          Low confidence for this workspace threshold.
        </p>
      </button>
    </div>
  </section>
</template>
