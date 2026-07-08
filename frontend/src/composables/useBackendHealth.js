import { onMounted, ref } from "vue";

import { getHealth } from "@/api/client";

export function useBackendHealth() {
  const status = ref("checking");
  const error = ref("");

  async function refresh() {
    status.value = "checking";
    error.value = "";

    try {
      const payload = await getHealth();
      status.value = payload.status === "ok" ? "ok" : "unknown";
    } catch (exc) {
      status.value = "offline";
      error.value = exc instanceof Error ? exc.message : "Backend is unavailable";
    }
  }

  onMounted(refresh);

  return {
    status,
    error,
    refresh,
  };
}
