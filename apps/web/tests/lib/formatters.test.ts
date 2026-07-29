import { describe, expect, it } from "vitest";

import { formatCallTime, formatDuration, formatMinutes } from "@/lib/formatters";

describe("France-first formatters", () => {
  it("renders call timestamps in Europe/Paris independently of the server timezone", () => {
    expect(formatCallTime("2026-01-15T10:00:00Z")).toBe("Jan 15, 11:00");
    expect(formatCallTime("2026-07-01T22:30:00Z")).toBe("Jul 2, 00:30");
  });

  it("handles missing and invalid timestamps without throwing", () => {
    expect(formatCallTime(null)).toBe("No timestamp");
    expect(formatCallTime("not-a-date")).toBe("No timestamp");
  });

  it("keeps compact duration and billed-minute labels", () => {
    expect(formatDuration(null)).toBe("Unknown duration");
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(60)).toBe("1m");
    expect(formatDuration(125)).toBe("2m 5s");
    expect(formatMinutes(null)).toBe("0 min");
    expect(formatMinutes(3)).toBe("3 min");
  });
});
