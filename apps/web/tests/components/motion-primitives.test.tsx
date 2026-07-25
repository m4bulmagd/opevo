import { createElement, Fragment, type HTMLAttributes, type ReactNode } from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActionState } from "@/components/motion/action-state";
import { AnimatedStatusBadge } from "@/components/motion/animated-status-badge";
import { ChangedNumber } from "@/components/motion/changed-number";
import { PresvoMotionProvider } from "@/components/motion/presvo-motion-provider";

const motionMocks = vi.hoisted(() => ({
  animate: vi.fn(),
  motionProps: [] as Array<Record<string, unknown>>,
  reduced: false,
}));

vi.mock("motion/react", async () => {
  const passthrough =
    (tag: "span") =>
    ({
      children,
      variants: _variants,
      initial: _initial,
      animate: _animate,
      exit: _exit,
      transition: _transition,
      layout: _layout,
      ...props
    }: HTMLAttributes<HTMLSpanElement> & {
      children?: ReactNode;
      variants?: unknown;
      initial?: unknown;
      animate?: unknown;
      exit?: unknown;
      transition?: unknown;
      layout?: unknown;
    }) => {
      motionMocks.motionProps.push({ animate: _animate, transition: _transition, variants: _variants });
      return createElement(tag, props, children);
    };

  return {
    AnimatePresence: ({ children }: { children: ReactNode }) => createElement(Fragment, null, children),
    MotionConfig: ({ children, reducedMotion }: { children: ReactNode; reducedMotion: string }) => (
      <div data-reduced-motion={reducedMotion}>{children}</div>
    ),
    animate: motionMocks.animate,
    motion: {
      span: passthrough("span"),
    },
    useReducedMotion: () => motionMocks.reduced,
  };
});

beforeEach(() => {
  motionMocks.animate.mockReset();
  motionMocks.motionProps.length = 0;
  motionMocks.reduced = false;
  motionMocks.animate.mockImplementation(
    (_from: number, to: number, options: { onUpdate?: (value: number) => void }) => {
      options.onUpdate?.(to);
      return { stop: vi.fn() };
    },
  );
});

afterEach(cleanup);

function containsInfiniteRepeat(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsInfiniteRepeat);
  if (typeof value !== "object" || value === null) return false;

  return Object.entries(value).some(
    ([key, nestedValue]) =>
      (key === "repeat" && nestedValue === Number.POSITIVE_INFINITY) || containsInfiniteRepeat(nestedValue),
  );
}

describe("Presvo motion primitives", () => {
  it("configures user reduced-motion preferences once for authenticated motion islands", () => {
    render(
      <PresvoMotionProvider>
        <span>Workspace</span>
      </PresvoMotionProvider>,
    );

    expect(screen.getByText("Workspace").parentElement).toHaveAttribute("data-reduced-motion", "user");
  });

  it("renders an icon and status text without pulse or looping animation", () => {
    render(<AnimatedStatusBadge tone="processing" label="Preparing summary" icon={<svg aria-label="Processing" />} />);

    expect(screen.getByLabelText("Processing")).toBeInTheDocument();
    expect(screen.getByText("Preparing summary")).toBeInTheDocument();
    expect(document.querySelector("[data-status-pulse]")).not.toBeInTheDocument();
    const selectedVariants = motionMocks.motionProps.map(({ variants }) => variants).filter(Boolean);

    expect(selectedVariants).not.toHaveLength(0);
    expect(selectedVariants.some(containsInfiniteRepeat)).toBe(false);
  });

  it("shows the initial number immediately without starting numeric animation", () => {
    render(<ChangedNumber value={42} />);

    expect(screen.getByText("42")).toBeInTheDocument();
    expect(motionMocks.animate).not.toHaveBeenCalled();
  });

  it("animates only a later number change from the previously rendered value", () => {
    const { rerender } = render(<ChangedNumber value={42} />);

    rerender(<ChangedNumber value={84} />);

    expect(motionMocks.animate).toHaveBeenCalledTimes(1);
    expect(motionMocks.animate).toHaveBeenCalledWith(
      42,
      84,
      expect.objectContaining({ onUpdate: expect.any(Function) }),
    );
    expect(screen.getByText("84")).toBeInTheDocument();
  });

  it("swaps changed numbers immediately when reduced motion is requested", () => {
    motionMocks.reduced = true;
    const { rerender } = render(<ChangedNumber value={42} />);

    rerender(<ChangedNumber value={84} />);

    expect(screen.getByText("84")).toBeInTheDocument();
    expect(motionMocks.animate).not.toHaveBeenCalled();
  });

  it("stops an in-flight number animation and shows the authoritative value when reduced motion is enabled", () => {
    const controls = { stop: vi.fn() };
    motionMocks.animate.mockImplementationOnce(
      (_from: number, _to: number, options: { onUpdate?: (value: number) => void }) => {
        options.onUpdate?.(63);
        return controls;
      },
    );
    const { rerender } = render(<ChangedNumber value={42} />);

    rerender(<ChangedNumber value={84} />);
    expect(screen.getByText("63")).toBeInTheDocument();

    motionMocks.reduced = true;
    rerender(<ChangedNumber value={84} />);

    expect(controls.stop).toHaveBeenCalledTimes(1);
    expect(screen.getByText("84")).toBeInTheDocument();
  });

  it("keeps an in-flight animation running when only duration changes and uses the new duration on the next value", () => {
    const controls = { stop: vi.fn() };
    motionMocks.animate.mockImplementationOnce(
      (_from: number, _to: number, options: { onUpdate?: (value: number) => void }) => {
        options.onUpdate?.(63);
        return controls;
      },
    );
    const { rerender } = render(<ChangedNumber value={42} duration={0.24} />);

    rerender(<ChangedNumber value={84} duration={0.24} />);
    rerender(<ChangedNumber value={84} duration={0.5} />);

    expect(motionMocks.animate).toHaveBeenCalledTimes(1);
    expect(controls.stop).not.toHaveBeenCalled();
    expect(screen.getByText("63")).toBeInTheDocument();

    rerender(<ChangedNumber value={100} duration={0.5} />);

    expect(controls.stop).toHaveBeenCalledTimes(1);
    expect(motionMocks.animate).toHaveBeenCalledTimes(2);
    expect(motionMocks.animate).toHaveBeenLastCalledWith(84, 100, expect.objectContaining({ duration: 0.5 }));
  });

  it.each([
    ["pending", "Saving"],
    ["error", "Try again"],
  ] as const)("announces %s action feedback politely", (phase, copy) => {
    render(<ActionState phase={phase} idle="Save" pending="Saving" success="Saved" error="Try again" />);

    const liveRegion = screen.getByText(copy).closest("[aria-live]");
    expect(liveRegion).toHaveAttribute("aria-live", "polite");
  });
});
