import { describe, expect, it } from "vitest";

import { THEME_PRESET_VALUES } from "@/lib/preferences/theme";

describe("preferences", () => {
  it("keeps the template preset list", () => {
    expect(THEME_PRESET_VALUES).toEqual(["default", "brutalist", "soft-pop", "tangerine"]);
  });
});
