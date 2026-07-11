<script setup>
import { computed } from "vue";

import SourceList from "@/components/chat/SourceList.vue";

const props = defineProps({
  selectedWorkspace: {
    type: Object,
    default: null,
  },
  selectedConversation: {
    type: Object,
    default: null,
  },
  workspaceCount: {
    type: Number,
    default: 0,
  },
  conversationCount: {
    type: Number,
    default: 0,
  },
  messageCount: {
    type: Number,
    default: 0,
  },
  selectedAssistantMessage: {
    type: Object,
    default: null,
  },
  lastRunType: {
    type: String,
    default: "",
  },
  lastRunMetrics: {
    type: Object,
    default: null,
  },
});

defineEmits(["locate-message"]);

const activeMetrics = computed(() => {
  if (props.selectedAssistantMessage) {
    return props.selectedAssistantMessage.metrics || {};
  }

  return props.lastRunMetrics || {};
});

const hasMetrics = computed(() => Object.keys(activeMetrics.value).length > 0);
const isAgentMetrics = computed(
  () =>
    (!props.selectedAssistantMessage && props.lastRunType === "agent") ||
    Boolean(activeMetrics.value.agent_mode) ||
    "step_count" in activeMetrics.value ||
    "tool_call_count" in activeMetrics.value,
);

const metricRows = computed(() => {
  const metrics = activeMetrics.value;
  const rows = [];

  if (typeof metrics.total_latency_ms === "number") {
    rows.push(["Latency", `${metrics.total_latency_ms} ms`]);
  }

  if (isAgentMetrics.value) {
    if (metrics.agent_mode) {
      rows.push(["Mode", metrics.agent_mode]);
    }
    if (typeof metrics.step_count === "number") {
      rows.push(["Steps", metrics.step_count]);
    }
    if (typeof metrics.tool_call_count === "number") {
      rows.push(["Tools", metrics.tool_call_count]);
    }
    if (typeof metrics.source_count === "number") {
      rows.push(["Sources", metrics.source_count]);
    }
    return rows;
  }

  if (typeof metrics.retrieved_count === "number") {
    rows.push(["Retrieved", metrics.retrieved_count]);
  }
  if (typeof metrics.used_source_count === "number") {
    rows.push(["Sources", metrics.used_source_count]);
  }

  return rows;
});

const agentInvocationId = computed(
  () => activeMetrics.value.agent_invocation_id || "",
);

function formatDate(value) {
  if (!value) {
    return "";
  }

  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
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
  <aside
    class="hidden bg-muted/30 p-4 lg:block lg:max-h-[calc(100vh-3.5rem)] lg:overflow-y-auto"
  >
    <div class="space-y-5">
      <section class="rounded-md border border-border bg-background p-4">
        <p class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Selection
        </p>

        <dl class="mt-4 space-y-4 text-sm">
          <div>
            <dt class="text-xs text-muted-foreground">Workspaces</dt>
            <dd class="mt-1 font-medium">{{ workspaceCount }}</dd>
          </div>
          <div>
            <dt class="text-xs text-muted-foreground">Conversations</dt>
            <dd class="mt-1 font-medium">{{ conversationCount }}</dd>
          </div>
          <div>
            <dt class="text-xs text-muted-foreground">Messages</dt>
            <dd class="mt-1 font-medium">{{ messageCount }}</dd>
          </div>
        </dl>
      </section>

      <section class="rounded-md border border-border bg-background p-4">
        <p class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Workspace
        </p>

        <div v-if="selectedWorkspace" class="mt-4 space-y-3 text-sm">
          <p class="break-words font-medium">{{ selectedWorkspace.name }}</p>
          <dl class="space-y-3">
            <div>
              <dt class="text-xs text-muted-foreground">ID</dt>
              <dd class="mt-1 break-all font-mono text-xs">{{ selectedWorkspace.id }}</dd>
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <dt class="text-xs text-muted-foreground">Mode</dt>
                <dd class="mt-1">{{ selectedWorkspace.chat_mode }}</dd>
              </div>
              <div>
                <dt class="text-xs text-muted-foreground">History</dt>
                <dd class="mt-1">{{ selectedWorkspace.history_limit }}</dd>
              </div>
              <div>
                <dt class="text-xs text-muted-foreground">Top K</dt>
                <dd class="mt-1">{{ selectedWorkspace.top_k }}</dd>
              </div>
              <div>
                <dt class="text-xs text-muted-foreground">Threshold</dt>
                <dd class="mt-1">{{ selectedWorkspace.similarity_threshold }}</dd>
              </div>
            </div>
          </dl>
        </div>

        <p v-else class="mt-4 text-sm leading-6 text-muted-foreground">
          No workspace selected.
        </p>
      </section>

      <section class="rounded-md border border-border bg-background p-4">
        <p class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Conversation
        </p>

        <div v-if="selectedConversation" class="mt-4 space-y-3 text-sm">
          <p class="break-words font-medium">{{ selectedConversation.title }}</p>
          <dl class="space-y-3">
            <div>
              <dt class="text-xs text-muted-foreground">ID</dt>
              <dd class="mt-1 break-all font-mono text-xs">
                {{ selectedConversation.id }}
              </dd>
            </div>
            <div>
              <dt class="text-xs text-muted-foreground">Updated</dt>
              <dd class="mt-1">{{ formatDate(selectedConversation.updated_at) }}</dd>
            </div>
          </dl>
        </div>

        <p v-else class="mt-4 text-sm leading-6 text-muted-foreground">
          No conversation selected.
        </p>
      </section>

      <section class="rounded-md border border-border bg-background p-4">
        <p class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Run Metrics
        </p>

        <div v-if="hasMetrics" class="mt-4 text-sm">
          <dl class="grid grid-cols-2 gap-3">
            <div v-for="[label, value] in metricRows" :key="label">
              <dt class="text-xs text-muted-foreground">{{ label }}</dt>
              <dd class="mt-1 font-medium">{{ value }}</dd>
            </div>

            <div v-if="agentInvocationId" class="col-span-2">
              <dt class="text-xs text-muted-foreground">Invocation</dt>
              <dd class="mt-1 break-all font-mono text-xs">
                {{ agentInvocationId }}
              </dd>
            </div>
          </dl>
        </div>

        <p v-else class="mt-4 text-sm leading-6 text-muted-foreground">
          No assistant metrics yet.
        </p>
      </section>

      <SourceList
        :low-score-threshold="selectedWorkspace?.similarity_threshold"
        :message-id="selectedAssistantMessage?.id || ''"
        :metrics="selectedAssistantMessage?.metrics || {}"
        :sources="selectedAssistantMessage?.sources || []"
        @locate="$emit('locate-message', $event)"
      />
    </div>
  </aside>
</template>
