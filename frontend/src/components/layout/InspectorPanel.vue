<script setup>
defineProps({
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
});

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
    </div>
  </aside>
</template>
