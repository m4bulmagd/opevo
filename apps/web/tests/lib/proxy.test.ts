import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const protectMock = vi.fn();
const clerkProxyInvocationMock = vi.fn();
const clerkMiddlewareMock = vi.fn();
const createRouteMatcherMock = vi.fn();
const nextResponseMock = vi.fn(() => ({ kind: "next-response" }));

vi.mock("next/server", () => ({
  NextResponse: { next: nextResponseMock },
}));

vi.mock("@clerk/nextjs/server", () => ({
  clerkMiddleware: clerkMiddlewareMock,
  createRouteMatcher: createRouteMatcherMock,
}));

type ProxyRequest = { url: string };
type ClerkMiddlewareCallback = (auth: { protect: typeof protectMock }, request: ProxyRequest) => Promise<void>;

describe("activation proxy protection", () => {
  beforeEach(() => {
    vi.resetModules();
    protectMock.mockReset().mockResolvedValue(undefined);
    clerkProxyInvocationMock.mockReset();
    nextResponseMock.mockClear();
    createRouteMatcherMock.mockReset().mockImplementation((patterns: string[]) => {
      return (request: ProxyRequest) => {
        const pathname = new URL(request.url).pathname;
        return patterns.some((pattern) => pathname.startsWith(pattern.replace("(.*)", "")));
      };
    });
    clerkMiddlewareMock.mockReset().mockImplementation((callback: ClerkMiddlewareCallback) => {
      return async (request: ProxyRequest) => {
        clerkProxyInvocationMock(request.url);
        return callback({ protect: protectMock }, request);
      };
    });
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it.each(["/activate", "/dashboard"])("protects %s through the Clerk middleware callback", async (path) => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "clerk");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_proxy");
    vi.stubEnv("CLERK_SECRET_KEY", "sk_test_proxy");

    const { default: proxy } = await import("@/proxy");
    await proxy({ url: `https://opevo.test${path}` } as never, {} as never);

    expect(clerkProxyInvocationMock).toHaveBeenCalledWith(`https://opevo.test${path}`);
    expect(protectMock).toHaveBeenCalledOnce();
    expect(createRouteMatcherMock).toHaveBeenCalledWith(["/activate(.*)", "/dashboard(.*)"]);
    expect(nextResponseMock).not.toHaveBeenCalled();
  });

  it("passes through guarded local development without invoking Clerk protection", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");

    const { default: proxy } = await import("@/proxy");
    const response = await proxy({ url: "https://opevo.test/activate" } as never, {} as never);

    expect(response).toEqual({ kind: "next-response" });
    expect(nextResponseMock).toHaveBeenCalledOnce();
    expect(clerkProxyInvocationMock).not.toHaveBeenCalled();
    expect(protectMock).not.toHaveBeenCalled();
  });

  it("cannot initialize with incomplete development Clerk configuration", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "clerk");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");

    await expect(import("@/proxy")).rejects.toThrow("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY");
  });
});
