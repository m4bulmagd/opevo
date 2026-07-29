import { describe, expect, it } from "vitest";

import { formatFrenchNumber, normalizeFrenchNumber } from "@/lib/phone-numbers";

describe("French phone number helpers", () => {
  it("normalizes supported French customer formats to E.164", () => {
    expect(normalizeFrenchNumber("06 12 34 56 78")).toBe("+33612345678");
  });

  it("rejects French country codes followed by a local trunk prefix", () => {
    expect(normalizeFrenchNumber("+33 (0)6 12 34 56 78")).toBeNull();
  });

  it("formats an E.164 French number for customer display", () => {
    expect(formatFrenchNumber("+33612345678")).toBe("06 12 34 56 78");
  });
});
