import { afterEach, describe, expect, it, vi } from "vitest";

async function loadRealtimeCapability(value?: string) {
  vi.resetModules();
  if (value === undefined) {
    vi.stubEnv("NEXT_PUBLIC_REALTIME_ENABLED", undefined);
  } else {
    vi.stubEnv("NEXT_PUBLIC_REALTIME_ENABLED", value);
  }
  const { APP_CONFIG } = await import("@/config/app-config");
  return APP_CONFIG.capabilities.realtime;
}

describe("app realtime capability", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each([undefined, "false", "TRUE", "1"])("defaults realtime off for %s", async (value) => {
    expect(await loadRealtimeCapability(value)).toBe(false);
  });

  it("enables realtime only for an explicit true value", async () => {
    expect(await loadRealtimeCapability("true")).toBe(true);
  });
});
