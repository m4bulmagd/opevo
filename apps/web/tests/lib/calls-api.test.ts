import { beforeEach, describe, expect, it, vi } from "vitest";

const backendFetchMock = vi.fn();

vi.mock("@/lib/api/backend-client", () => ({
  backendFetch: backendFetchMock,
}));

describe("calls API client", () => {
  beforeEach(() => {
    backendFetchMock.mockReset();
  });

  it("returns the complete page and sends normalized named options", async () => {
    const page = {
      calls: [],
      total: 47,
      limit: 20,
      offset: 20,
      has_more: true,
    };
    backendFetchMock.mockResolvedValueOnce(page);
    const { listCalls } = await import("@/lib/api/calls");

    await expect(listCalls({ limit: 20, offset: 20, query: " opening hours " })).resolves.toEqual(page);
    expect(backendFetchMock).toHaveBeenCalledWith("/api/calls?limit=20&offset=20&q=opening+hours");
  });

  it("omits q for an unfiltered request", async () => {
    backendFetchMock.mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 5,
      offset: 0,
      has_more: false,
    });
    const { listCalls } = await import("@/lib/api/calls");

    await listCalls({ limit: 5 });

    expect(backendFetchMock).toHaveBeenCalledWith("/api/calls?limit=5&offset=0");
  });
});
