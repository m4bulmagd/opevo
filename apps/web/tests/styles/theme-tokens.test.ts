import tailwindcss from "@tailwindcss/postcss";
import postcss, { type Declaration, type Rule } from "postcss";
import { describe, expect, it } from "vitest";

import { readFile } from "node:fs/promises";
import path from "node:path";

const durations = ["feedback", "state", "entrance"] as const;

describe("Presvo Tailwind theme tokens", () => {
  it("compiles the semantic transition-duration utilities from globals.css", async () => {
    const globalsPath = path.resolve(process.cwd(), "src/app/globals.css");
    const globals = await readFile(globalsPath, "utf8");
    const result = await postcss([tailwindcss({ base: process.cwd() })]).process(
      `${globals}\n@source inline("duration-feedback duration-state duration-entrance");`,
      { from: globalsPath },
    );

    for (const duration of durations) {
      let utility: Rule | undefined;

      result.root.walkRules(`.duration-${duration}`, (rule) => {
        utility = rule;
      });

      const transitionDuration = utility?.nodes.find(
        (node): node is Declaration => node.type === "decl" && node.prop === "transition-duration",
      );

      expect(transitionDuration?.value).toBe(`var(--motion-duration-${duration})`);
    }
  });
});
