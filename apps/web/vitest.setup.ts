import "@testing-library/jest-dom/vitest";

import { vi } from "vitest";

vi.mock("server-only", () => ({}));

process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ??= "pk_test_codex";
process.env.CLERK_SECRET_KEY ??= "sk_test_codex";

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

const canvasContext2DMock = {
  beginPath: vi.fn(),
  fillRect: vi.fn(),
  lineTo: vi.fn(),
  moveTo: vi.fn(),
  stroke: vi.fn(),
  fillStyle: "",
  lineWidth: 1,
  strokeStyle: "",
};

Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
  writable: true,
  value: vi.fn(() => canvasContext2DMock),
});
