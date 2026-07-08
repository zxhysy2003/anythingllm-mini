<script setup>
import HealthBadge from "@/components/ui/HealthBadge.vue";
import { useBackendHealth } from "@/composables/useBackendHealth";

import InspectorPanel from "./InspectorPanel.vue";
import SidebarPanel from "./SidebarPanel.vue";

const { status, error, refresh } = useBackendHealth();
</script>

<template>
  <div class="min-h-screen bg-background text-foreground">
    <header class="flex h-14 items-center justify-between border-b border-border px-4">
      <div>
        <p class="text-sm font-semibold leading-none">AnythingLLM Mini</p>
        <p class="mt-1 text-xs text-muted-foreground">Vue frontend phase 0</p>
      </div>

      <HealthBadge :status="status" :error="error" @refresh="refresh" />
    </header>

    <main class="grid min-h-[calc(100vh-3.5rem)] grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)_320px]">
      <SidebarPanel />

      <section class="flex min-h-[calc(100vh-3.5rem)] flex-col border-b border-border lg:border-x lg:border-b-0">
        <div class="border-b border-border px-5 py-4">
          <p class="text-sm font-medium">Chat workspace shell</p>
          <p class="mt-1 text-sm text-muted-foreground">
            Phase 0 only proves the Vue shell, layout, and Vite proxy.
          </p>
        </div>

        <div class="flex flex-1 items-center justify-center px-6 py-10">
          <div class="max-w-xl text-center">
            <p class="text-2xl font-semibold tracking-normal">Ready for workspace chat</p>
            <p class="mt-3 text-sm leading-6 text-muted-foreground">
              The next phase will load workspaces and conversations from the FastAPI backend.
              This screen intentionally keeps the interaction minimal so the project can grow
              one Vue concept at a time.
            </p>
          </div>
        </div>

        <div class="border-t border-border p-4">
          <div class="rounded-md border border-border bg-muted px-4 py-3 text-sm text-muted-foreground">
            Prompt composer placeholder
          </div>
        </div>
      </section>

      <InspectorPanel />
    </main>
  </div>
</template>
