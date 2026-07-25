import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DataLedger } from "@/components/product/data-ledger";
import { MetricBand, MetricItem } from "@/components/product/metric-band";
import { PageIntro } from "@/components/product/page-intro";
import { ProductSurface } from "@/components/product/product-surface";
import { SettingsSection } from "@/components/product/settings-section";
import { StatusSurface, type StatusSurfaceTone } from "@/components/product/status-surface";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PageIntro", () => {
  it("provides the page h1 and optional context without adding a decorative heading icon", () => {
    const { container } = render(
      <PageIntro
        action={<a href="/dashboard/calls">Review calls</a>}
        description="A concise view of today’s call handling."
        dynamicContext
        eyebrow="Sunday, 26 July"
        title="Operations overview"
      />,
    );

    expect(screen.getByRole("heading", { level: 1, name: "Operations overview" })).toBeInTheDocument();
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByText("Sunday, 26 July")).toBeInTheDocument();
    expect(screen.getByText("A concise view of today’s call handling.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review calls" })).toHaveAttribute("href", "/dashboard/calls");
    expect(container.querySelector("[data-dynamic-context='true']")).toBeInTheDocument();
    expect(container.querySelector("svg")).not.toBeInTheDocument();
  });
});

describe("ProductSurface", () => {
  it("composes optional header, action, body, and footer slots in one labelled surface", () => {
    const { container } = render(
      <ProductSurface
        action={<a href="/settings">Configure</a>}
        as="article"
        description="Recent operational details."
        footer={<p>Updated just now</p>}
        title={<span>Call activity</span>}
        tone="subtle"
      >
        <p>Three calls handled</p>
      </ProductSurface>,
    );

    const surface = screen.getByRole("article", { name: "Call activity" });

    expect(surface).toHaveAttribute("data-tone", "subtle");
    expect(screen.getByRole("heading", { level: 2, name: "Call activity" })).toBeInTheDocument();
    expect(screen.getByText("Recent operational details.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Configure" })).toBeInTheDocument();
    expect(screen.getByText("Three calls handled")).toBeInTheDocument();
    expect(screen.getByText("Updated just now")).toBeInTheDocument();
    expect(container.querySelectorAll("[data-slot='product-surface']")).toHaveLength(1);
  });

  it("makes a titled div a described region for ReactNode titles", () => {
    render(
      <ProductSurface as="div" description="Current minute allocation and renewal context." title={<span>Usage</span>}>
        <p>42 minutes remaining</p>
      </ProductSurface>,
    );

    const surface = screen.getByRole("region", {
      description: "Current minute allocation and renewal context.",
      name: "Usage",
    });

    expect(surface).toHaveAccessibleDescription("Current minute allocation and renewal context.");
    expect(screen.getByRole("heading", { level: 2, name: "Usage" })).toBeInTheDocument();
  });

  it("does not turn an untitled div into an unnamed landmark", () => {
    const { container } = render(
      <ProductSurface as="div">
        <p>Supporting content</p>
      </ProductSurface>,
    );

    expect(container.querySelector("[data-slot='product-surface']")).not.toHaveAttribute("role");
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
});

describe("StatusSurface", () => {
  const tones: StatusSurfaceTone[] = [
    "neutral",
    "live",
    "ready",
    "processing",
    "paused",
    "warning",
    "attention",
    "inactive",
  ];

  it.each(tones)("exposes %s state through an icon and text as well as semantic color", (tone) => {
    render(
      <StatusSurface
        action={<a href="/activate">Review activation</a>}
        description="Forwarded calls can be answered."
        icon={<svg aria-label={`${tone} status`} />}
        label="Answering state"
        title="Presvo is answering"
        tone={tone}
      />,
    );

    const surface = screen.getByRole("region", { name: "Answering state" });

    expect(surface).toHaveAttribute("data-tone", tone);
    expect(screen.getByLabelText(`${tone} status`)).toBeInTheDocument();
    expect(screen.getByText("Answering state")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Presvo is answering" })).toBeInTheDocument();
    expect(screen.getByText("Forwarded calls can be answered.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review activation" })).toHaveAttribute("href", "/activate");
  });

  it("provides a static marker when a custom status icon is not supplied", () => {
    const { container } = render(
      <StatusSurface label="Paused" title="Presvo is paused" tone="paused">
        Review the readiness blockers.
      </StatusSurface>,
    );

    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(screen.getByText("Review the readiness blockers.")).toBeInTheDocument();
    expect(container.querySelector("[data-status-marker]")).toBeInTheDocument();
  });

  it.each([
    ["a false conditional", false],
    ["an empty string", ""],
    ["an empty child array", []],
  ])("provides a static marker when the icon is %s", (_case, icon) => {
    const { container } = render(<StatusSurface icon={icon} label="Paused" title="Presvo is paused" tone="paused" />);

    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(container.querySelector("[data-status-marker]")).toBeInTheDocument();
  });
});

describe("MetricBand", () => {
  it("groups metrics in one labelled region with comparison and accessible unavailable values", () => {
    render(
      <MetricBand label="Weekly performance">
        <MetricItem context="6 more than last week" label="Calls handled" state="positive" value={34} />
        <MetricItem context="Across completed calls" label="Average duration" value="2m 42s" />
        <MetricItem label="Follow-up rate" state="warning" value={null} />
      </MetricBand>,
    );

    const band = screen.getByRole("region", { name: "Weekly performance" });
    const callsValue = screen.getByText("34");

    expect(band).toHaveAttribute("data-slot", "metric-band");
    expect(screen.getByText("6 more than last week")).toBeInTheDocument();
    expect(screen.getByText("Across completed calls")).toBeInTheDocument();
    expect(callsValue).toHaveClass("tabular-nums");
    expect(callsValue.closest("[data-state]")).toHaveAttribute("data-state", "positive");
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
  });
});

describe("DataLedger", () => {
  const ledgerRows = (
    <DataLedger.Row>
      <DataLedger.Cell label="Caller" primary>
        <a href="/dashboard/calls/call-1">+33 1 84 80 20 20</a>
      </DataLedger.Cell>
      <DataLedger.Cell hideAt="md" label="Duration">
        2m 42s
      </DataLedger.Cell>
      <DataLedger.Action>
        <button type="button">Archive</button>
      </DataLedger.Action>
    </DataLedger.Row>
  );

  it("renders a semantic labelled list by default with real mobile labels and keyboard-accessible links", () => {
    render(
      <DataLedger header={<p>Latest calls</p>} label="Recent calls" pagination={<a href="?page=2">Next page</a>}>
        {ledgerRows}
      </DataLedger>,
    );

    const link = screen.getByRole("link", { name: "+33 1 84 80 20 20" });

    expect(screen.getByRole("list", { name: "Recent calls" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("listitem")).toBeInTheDocument();
    expect(screen.getByText("Caller")).toBeInTheDocument();
    expect(screen.getByText("Duration")).toBeInTheDocument();
    expect(screen.getByText("Latest calls")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Recent calls pagination" })).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/dashboard/calls/call-1");

    link.focus();
    expect(link).toHaveFocus();
  });

  it("renders native table semantics only when table mode is requested", () => {
    render(
      <DataLedger label="Recent calls" mode="table">
        {ledgerRows}
      </DataLedger>,
    );

    expect(screen.getByRole("table", { name: "Recent calls" })).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.getByRole("columnheader", { name: "Caller" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Duration" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Actions" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: /Caller/ })).toBeInTheDocument();
    expect(screen.getByText("Caller", { selector: "[data-slot='data-ledger-mobile-label']" })).toHaveClass("md:hidden");
  });

  it.each(["list", "table"] as const)("renders normal mapped arrays of multiple rows in %s mode", (mode) => {
    const rows = ["call-1", "call-2"].map((id, index) => (
      <DataLedger.Row key={id}>
        <DataLedger.Cell label="Caller" primary>
          <a href={`/dashboard/calls/${id}`}>Caller {index + 1}</a>
        </DataLedger.Cell>
        <DataLedger.Cell hideAt="md" label="Duration">
          {index + 1}m
        </DataLedger.Cell>
      </DataLedger.Row>
    ));

    render(
      <DataLedger label="Recent calls" mode={mode}>
        {rows}
      </DataLedger>,
    );

    expect(screen.getByRole("link", { name: "Caller 1" })).toHaveAttribute("href", "/dashboard/calls/call-1");
    expect(screen.getByRole("link", { name: "Caller 2" })).toHaveAttribute("href", "/dashboard/calls/call-2");
    expect(screen.getAllByRole(mode === "list" ? "listitem" : "row")).toHaveLength(mode === "list" ? 2 : 3);
  });

  it.each([
    "list",
    "table",
  ] as const)("filters unsupported ledger and row children before rendering %s semantics", (mode) => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(
      <DataLedger label="Recent calls" mode={mode}>
        <DataLedger.Row>
          <DataLedger.Cell label="Caller" primary>
            <a href="/dashboard/calls/call-1">Caller 1</a>
          </DataLedger.Cell>
          {false}
          {null}
          <p>Unsupported row child</p>
        </DataLedger.Row>
        {false}
        {null}
        <p>Loading more…</p>
      </DataLedger>,
    );

    expect(screen.getByRole("link", { name: "Caller 1" })).toBeInTheDocument();
    expect(screen.queryByText("Unsupported row child")).not.toBeInTheDocument();
    expect(screen.queryByText("Loading more…")).not.toBeInTheDocument();
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("uses explicit empty, error, and pagination regions without inventing rows", () => {
    const { rerender } = render(<DataLedger empty={<p>No calls yet</p>} label="Recent calls" />);

    expect(screen.getByRole("status", { name: "Recent calls empty" })).toHaveTextContent("No calls yet");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();

    rerender(
      <DataLedger
        empty={<p>No calls yet</p>}
        error={<p>Call history could not be loaded.</p>}
        label="Recent calls"
        pagination={<button type="button">Retry page</button>}
      />,
    );

    expect(screen.getByRole("alert", { name: "Recent calls error" })).toHaveTextContent(
      "Call history could not be loaded.",
    );
    expect(screen.queryByText("No calls yet")).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Recent calls pagination" })).toBeInTheDocument();
  });
});

describe("SettingsSection", () => {
  it("associates its heading, description, controls, validation, status, and action regions", () => {
    render(
      <SettingsSection
        action={<button type="button">Save identity</button>}
        description="The public identity callers hear."
        status="Saved two minutes ago"
        title="Agent identity"
        validation="The agent name is required."
      >
        <label htmlFor="agent-name">Agent name</label>
        <input id="agent-name" />
      </SettingsSection>,
    );

    const section = screen.getByRole("region", { name: "Agent identity" });

    expect(section).toHaveAccessibleDescription("The public identity callers hear.");
    expect(screen.getByRole("heading", { level: 2, name: "Agent identity" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Agent name" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("The agent name is required.");
    expect(screen.getByRole("status")).toHaveTextContent("Saved two minutes ago");
    expect(screen.getByRole("button", { name: "Save identity" })).toBeInTheDocument();
  });

  it("uses collision-safe heading and description associations for repeated ReactNode titles", () => {
    render(
      <>
        <SettingsSection description="Primary identity settings." title={<span>Agent identity</span>}>
          <p>Primary controls</p>
        </SettingsSection>
        <SettingsSection description="Fallback identity settings." title={<span>Agent identity</span>}>
          <p>Fallback controls</p>
        </SettingsSection>
      </>,
    );

    const sections = screen.getAllByRole("region", { name: "Agent identity" });

    expect(sections).toHaveLength(2);
    expect(sections[0]).toHaveAccessibleDescription("Primary identity settings.");
    expect(sections[1]).toHaveAccessibleDescription("Fallback identity settings.");
  });
});
