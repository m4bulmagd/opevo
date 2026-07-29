import tailwindcss from "@tailwindcss/postcss";
import postcss, { type Declaration, type Root, type Rule } from "postcss";
import { describe, expect, it } from "vitest";

import { readFile } from "node:fs/promises";
import path from "node:path";

const durations = ["feedback", "state", "entrance"] as const;

function declarationsForSelector(root: Root, selector: string) {
  const values = new Map<string, string>();

  root.walkRules(selector, (rule) => {
    rule.walkDecls((declaration) => {
      values.set(declaration.prop, declaration.value);
    });
  });

  return values;
}

describe("Presvo Tailwind theme tokens", () => {
  it("preserves the approved light and dark Presvo visual contract", async () => {
    const globalsPath = path.resolve(process.cwd(), "src/app/globals.css");
    const root = postcss.parse(await readFile(globalsPath, "utf8"), { from: globalsPath });
    const light = declarationsForSelector(root, ":root");
    const dark = declarationsForSelector(root, ".dark");
    let fontSans: string | undefined;

    root.walkAtRules("theme", (theme) => {
      theme.walkDecls("--font-sans", (declaration) => {
        fontSans = declaration.value;
      });
    });

    expect(Object.fromEntries(light)).toMatchObject({
      "--radius": "0.875rem",
      "--background": "oklch(0.976 0.004 120)",
      "--foreground": "oklch(0.245 0.012 150)",
      "--card": "oklch(1 0 0)",
      "--primary": "oklch(0.42 0.045 152)",
      "--border": "oklch(0.923 0.006 135)",
      "--shadow-card": "0 1px 2px oklch(0.245 0.012 150 / 0.04), 0 8px 24px oklch(0.245 0.012 150 / 0.04)",
      "--shadow-raised": "0 2px 4px oklch(0.245 0.012 150 / 0.05), 0 16px 40px oklch(0.245 0.012 150 / 0.07)",
    });
    expect(Object.fromEntries(dark)).toMatchObject({
      "--background": "oklch(0.19 0.012 150)",
      "--foreground": "oklch(0.965 0.005 130)",
      "--card": "oklch(0.235 0.014 152)",
      "--primary": "oklch(0.78 0.075 152)",
      "--border": "oklch(1 0 0 / 10%)",
      "--shadow-card": "0 1px 2px oklch(0 0 0 / 0.25), 0 8px 24px oklch(0 0 0 / 0.2)",
      "--shadow-raised": "0 2px 4px oklch(0 0 0 / 0.3), 0 16px 40px oklch(0 0 0 / 0.28)",
    });
    expect(fontSans).toBe('ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif');
  });

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

  it("fully removes animation and transition duration for reduced motion", async () => {
    const globalsPath = path.resolve(process.cwd(), "src/app/globals.css");
    const root = postcss.parse(await readFile(globalsPath, "utf8"), { from: globalsPath });
    const reducedMotionDurations: string[] = [];

    root.walkAtRules("media", (media) => {
      if (!media.params.includes("prefers-reduced-motion: reduce")) return;
      media.walkDecls(/^(animation|transition)-duration$/, (declaration) => {
        reducedMotionDurations.push(declaration.value);
      });
    });

    expect(reducedMotionDurations).toEqual(["0s", "0s"]);
  });

  it("publishes matching browser chrome colors for the approved light and dark themes", async () => {
    const layoutPath = path.resolve(process.cwd(), "src/app/layout.tsx");
    const layout = await readFile(layoutPath, "utf8");

    expect(layout).toContain("export const viewport: Viewport");
    expect(layout).toContain('media: "(prefers-color-scheme: light)", color: "#f8f9f6"');
    expect(layout).toContain('media: "(prefers-color-scheme: dark)", color: "#101511"');
  });
});
