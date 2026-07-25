import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BackendApiError } from "@/lib/api/backend-client";

const getAgentConfigMock = vi.fn();
const patchAgentConfigMock = vi.fn();
const getAccountMock = vi.fn();

vi.mock("@/lib/api/agent", () => ({
  getAgentConfig: getAgentConfigMock,
  patchAgentConfig: patchAgentConfigMock,
}));

vi.mock("@/lib/api/account", () => ({
  getAccount: getAccountMock,
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

describe("agent page", () => {
  beforeEach(() => {
    getAccountMock.mockReset().mockResolvedValue({
      status: "active",
      serving: true,
      deactivation: null,
      reactivation_allowed: false,
      blocker: null,
    });
  });

  it("renders editable settings and guarded enable copy", async () => {
    getAgentConfigMock.mockResolvedValueOnce({
      agent_name: "Ava",
      owner_context: "Reception for North Clinic",
      system_prompt: "Be helpful.",
      knowledge_base: "Open weekdays",
      pipeline_mode: "stt_llm_tts",
      is_enabled: true,
    });

    const { default: Page } = await import("@/app/(app)/dashboard/agent/page");
    render(await Page());

    expect(screen.getByDisplayValue("Ava")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Reception for North Clinic")).toBeInTheDocument();
    expect(screen.getByText(/Enable call routing/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Billing, number assignment, and setup must be complete before routing can go live/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^STS$/i)).not.toBeInTheDocument();
  });

  it("returns a conflict message for guarded enable failures", async () => {
    patchAgentConfigMock.mockRejectedValueOnce(new BackendApiError("Agent setup incomplete", 409));

    const { saveAgentSettingsAction } = await import("@/app/(app)/dashboard/agent/actions");
    const result = await saveAgentSettingsAction({ is_enabled: true });

    expect(result.status).toBe("error");
    expect(result.message).toMatch(/billing is active, your number is assigned, and setup is complete/i);
  });

  it.each([
    "deactivating",
    "inactive",
  ] as const)("preserves saved settings but disables every mutation while the account is %s", async (status) => {
    getAccountMock.mockResolvedValueOnce({
      status,
      serving: false,
      deactivation: status === "deactivating" ? { state: "draining_call", requested_at: "2026-07-24T10:00:00Z" } : null,
      reactivation_allowed: status === "inactive",
      blocker: status === "deactivating" ? "account_deactivating" : "account_inactive",
    });
    getAgentConfigMock.mockResolvedValueOnce({
      agent_name: "Ava",
      owner_context: "Reception for North Clinic",
      system_prompt: "Be helpful.",
      knowledge_base: "Open weekdays",
      pipeline_mode: "stt_llm_tts",
      is_enabled: false,
    });

    const { default: Page } = await import("@/app/(app)/dashboard/agent/page");
    render(await Page());

    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue("Ava")).toBeDisabled();
    expect(screen.getByDisplayValue("Reception for North Clinic")).toBeDisabled();
    expect(screen.getByDisplayValue("Be helpful.")).toBeDisabled();
    expect(screen.getByDisplayValue("Open weekdays")).toBeDisabled();
    expect(screen.getByRole("switch", { name: /Enable call routing/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Save agent settings/i })).toBeDisabled();
  });

  it("maps account lifecycle mutation blockers without exposing backend details", async () => {
    patchAgentConfigMock.mockRejectedValueOnce(
      new BackendApiError({ code: "account_inactive", provider_number_id: "pn_secret" }, 409),
    );

    const { saveAgentSettingsAction } = await import("@/app/(app)/dashboard/agent/actions");
    const result = await saveAgentSettingsAction({ agent_name: "Changed" });

    expect(result).toMatchObject({
      status: "error",
      message: "Reactivate Presvo before changing agent settings.",
    });
    expect(JSON.stringify(result)).not.toContain("pn_secret");
  });
});
