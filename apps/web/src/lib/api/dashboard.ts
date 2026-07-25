import { backendFetch } from "@/lib/api/backend-client";
import type { DashboardMetrics } from "@/lib/types/dashboard";

export async function getDashboardMetrics(): Promise<DashboardMetrics> {
  return backendFetch<DashboardMetrics>("/api/dashboard/metrics");
}
