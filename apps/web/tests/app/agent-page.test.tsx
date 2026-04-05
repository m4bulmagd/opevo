import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BackendApiError } from "@/lib/api/backend-client";

const getAgentConfigMock = vi.fn();
const patchAgentConfigMock = vi.fn();

vi.mock("@/lib/api/agent", () => ({
  getAgentConfig: getAgentConfigMock,
  patchAgentConfig: patchAgentConfigMock,
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

describe("agent page", () => {
  it("renders editable settings and guarded enable copy", async () => {
    getAgentConfigMock.mockResolvedValueOnce({
      agent_name: "Ava",
      owner_context: "Reception for North Clinic",
      system_prompt: "Be helpful.",
      knowledge_base: "Open weekdays",
      pipeline_mode: "sts",
      is_enabled: true,
    });

    const { default: Page } = await import("@/app/(app)/dashboard/agent/page");
    render(await Page());

    expect(screen.getByDisplayValue("Ava")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Reception for North Clinic")).toBeInTheDocument();
    expect(screen.getByText(/Enable call routing/i)).toBeInTheDocument();
    expect(screen.getByText(/This is operationally significant/i)).toBeInTheDocument();
  });

  it("returns a conflict message for guarded enable failures", async () => {
    patchAgentConfigMock.mockRejectedValueOnce(new BackendApiError("Phone number not found", 409));

    const { saveAgentSettingsAction } = await import("@/app/(app)/dashboard/agent/actions");
    const result = await saveAgentSettingsAction({ is_enabled: true });

    expect(result.status).toBe("error");
    expect(result.message).toMatch(/Phone number not found/i);
  });
});
