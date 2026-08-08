import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CapabilityBadge } from "@/components/product/capability-badge";
import { PageIntro } from "@/components/product/page-intro";
import { ProductSurface } from "@/components/product/product-surface";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

describe("Opevo design-system primitives", () => {
  it("uses scoped feedback transitions instead of animating every property", () => {
    render(
      <>
        <Button>Save</Button>
        <Badge>Ready</Badge>
      </>,
    );

    const button = screen.getByRole("button", { name: "Save" });
    const badge = screen.getByText("Ready");

    expect(button).toHaveClass("transition-[color,background-color,border-color,box-shadow,transform]");
    expect(button).not.toHaveClass("transition-all");
    expect(badge).toHaveClass("transition-[color,background-color,border-color,box-shadow]");
    expect(badge).not.toHaveClass("transition-all");
  });

  it("never animates every CSS property in the retained UI primitives", async () => {
    const primitivesDirectory = path.resolve(process.cwd(), "src/components/ui");
    const primitiveFiles = (await readdir(primitivesDirectory)).filter((file) => file.endsWith(".tsx"));
    const sources = await Promise.all(
      primitiveFiles.map(async (file) => ({
        file,
        source: await readFile(path.join(primitivesDirectory, file), "utf8"),
      })),
    );

    expect(sources.filter(({ source }) => source.includes("transition-all")).map(({ file }) => file)).toEqual([]);
  });

  it("uses the approved compact control and card geometry", () => {
    render(
      <>
        <Input aria-label="Company" />
        <Card aria-label="Call activity" />
      </>,
    );

    expect(screen.getByRole("textbox", { name: "Company" })).toHaveClass("h-9", "rounded-md");
    expect(screen.getByLabelText("Call activity")).toHaveClass(
      "rounded-xl",
      "border",
      "border-border",
      "bg-card",
      "shadow-card",
    );
    expect(screen.getByLabelText("Call activity")).not.toHaveClass("ring-1", "shadow-xs");
  });

  it("uses the template surface and page-heading hierarchy", () => {
    const { container } = render(
      <>
        <PageIntro description="Today’s call handling." title="Operations overview" />
        <ProductSurface title="Call activity">Three calls handled</ProductSurface>
      </>,
    );

    expect(screen.getByRole("heading", { level: 1, name: "Operations overview" })).toHaveClass(
      "text-xl",
      "sm:text-2xl",
    );
    expect(container.querySelector("[data-slot='product-surface']")).toHaveClass(
      "rounded-xl",
      "border",
      "border-border",
      "bg-card",
      "shadow-card",
    );
  });

  it("names every capability state in visible text", () => {
    render(
      <>
        <CapabilityBadge status="live" />
        <CapabilityBadge status="preview" />
        <CapabilityBadge status="unavailable" />
      </>,
    );

    expect(screen.getByText("Live")).toHaveAttribute("data-capability-status", "live");
    expect(screen.getByText("Preview")).toHaveAttribute("data-capability-status", "preview");
    expect(screen.getByText("Unavailable")).toHaveAttribute("data-capability-status", "unavailable");
  });
});
