import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BackendApiError } from "@/lib/api/backend-client";
import type { AccountStatus } from "@/lib/types/account";
import type { AgentConfig } from "@/lib/types/agent";

const getAgentConfigForRequestMock = vi.fn();
const patchAgentConfigMock = vi.fn();
const getAccountMock = vi.fn();

vi.mock("@/lib/api/agent", () => ({
  patchAgentConfig: patchAgentConfigMock,
}));

vi.mock("@/lib/api/request-data", () => ({
  getAgentConfigForRequest: getAgentConfigForRequestMock,
}));

vi.mock("@/lib/api/account", () => ({
  getAccount: getAccountMock,
}));

vi.mock("next/cache", () => ({
  revalidatePath: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const activeAccount: AccountStatus = {
  status: "active",
  serving: true,
  deactivation: null,
  reactivation_allowed: false,
  blocker: null,
};

const configuredAgent: AgentConfig = {
  agent_name: "Ava",
  owner_context: "Reception for North Clinic",
  system_prompt: "Be helpful.",
  knowledge_base: "Open weekdays",
  pipeline_mode: "stt_llm_tts",
  is_enabled: true,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });

  return { promise, reject, resolve };
}

async function renderAgentPage({
  account = activeAccount,
  config = configuredAgent,
}: {
  account?: AccountStatus;
  config?: AgentConfig;
} = {}) {
  getAccountMock.mockResolvedValueOnce(account);
  getAgentConfigForRequestMock.mockResolvedValueOnce(config);

  const { default: Page } = await import("@/app/(app)/dashboard/agent/page");
  return render(await Page());
}

describe("agent page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses the whitespace-normalized configured name as the single page heading", async () => {
    await renderAgentPage({
      config: {
        ...configuredAgent,
        agent_name: "  Ava Stone  ",
      },
    });

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Ava Stone" })).toBeInTheDocument();
  });

  it.each(["", "  \n  "])("falls back to Receptionist when the configured name is %j", async (agentName) => {
    await renderAgentPage({
      config: {
        ...configuredAgent,
        agent_name: agentName,
      },
    });

    expect(screen.getByRole("heading", { level: 1, name: "Receptionist" })).toBeInTheDocument();
  });

  it("renders a long configured name completely in the page heading", async () => {
    const longName = "Amandine, the North Clinic Patient Reception Specialist";

    await renderAgentPage({
      config: {
        ...configuredAgent,
        agent_name: longName,
      },
    });

    const heading = screen.getByRole("heading", { level: 1, name: longName });
    expect(heading).toHaveTextContent(longName);
    expect(heading).not.toHaveClass("truncate");
  });

  it("leads with PageIntro and an honest enabled runtime status before the settings controls", async () => {
    await renderAgentPage();

    const intro = document.querySelector('[data-slot="page-intro"]');
    const runtime = screen.getByRole("region", { name: "Enabled" });
    const firstControl = screen.getByRole("textbox", { name: "Agent name" });

    expect(intro).toBeInTheDocument();
    expect(runtime).toHaveAttribute("data-tone", "neutral");
    expect(runtime).toHaveTextContent("Call routing is enabled in the saved agent configuration");
    expect(runtime).toHaveTextContent("Account readiness currently permits Presvo to serve calls");
    expect(runtime).not.toHaveTextContent(/answering|ready|live/i);
    expect(runtime.compareDocumentPosition(firstControl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows a general account-requirements warning when enabled routing cannot serve", async () => {
    await renderAgentPage({
      account: {
        ...activeAccount,
        serving: false,
        blocker: "customer_not_ready",
      },
    });

    const runtime = screen.getByRole("region", { name: "Action needed" });
    const firstControl = screen.getByRole("textbox", { name: "Agent name" });

    expect(runtime).toHaveTextContent("Call routing is enabled in the saved agent configuration");
    expect(runtime).toHaveTextContent(/account requirements do not currently permit serving/i);
    expect(runtime).toHaveTextContent(/review overview for the next step/i);
    expect(runtime).not.toHaveTextContent(/setup incomplete|review activation/i);
    expect(runtime).not.toHaveTextContent("customer_not_ready");
    expect(runtime.compareDocumentPosition(firstControl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps an intentionally disabled agent paused before evaluating readiness", async () => {
    await renderAgentPage({
      account: {
        ...activeAccount,
        serving: false,
        blocker: "customer_not_ready",
      },
      config: {
        ...configuredAgent,
        is_enabled: false,
      },
    });

    const runtime = screen.getByRole("region", { name: "Paused" });
    expect(runtime).toHaveTextContent("Call routing is disabled in the saved agent configuration");
    expect(runtime).toHaveTextContent(/readiness.*after you enable/i);
    expect(runtime).not.toHaveTextContent(/action needed|setup incomplete|review activation|customer_not_ready/i);
    expect(runtime).not.toHaveTextContent(/permits serving|answering|ready|live/i);
  });

  it.each([
    ["deactivating", "Deactivating", "Account deactivation is in progress"],
    ["inactive", "Inactive", "This agent configuration is read-only"],
  ] as const)("leads with the %s account lifecycle state", async (status, label, copy) => {
    const account: AccountStatus = {
      status,
      serving: false,
      deactivation: status === "deactivating" ? { state: "draining_call", requested_at: "2026-07-24T10:00:00Z" } : null,
      reactivation_allowed: status === "inactive",
      blocker: status === "deactivating" ? "account_deactivating" : "account_inactive",
    };

    await renderAgentPage({ account });

    const runtime = screen.getByRole("region", { name: label });
    const firstControl = screen.getByRole("textbox", { name: "Agent name" });
    expect(runtime).toHaveTextContent(copy);
    expect(runtime).toHaveTextContent(/read-only/i);
    expect(runtime.compareDocumentPosition(firstControl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it.each([
    ["requested", "Request accepted"],
    ["disabling_routing", "Stopping new calls"],
    ["canceling_subscription", "Canceling subscription"],
    ["draining_call", "Waiting for an active call to finish"],
    ["releasing_number", "Releasing your Presvo number"],
    ["finalizing", "Finalizing your account"],
  ] as const)("maps the bounded %s deactivation progress to human copy", async (state, progressCopy) => {
    await renderAgentPage({
      account: {
        status: "deactivating",
        serving: false,
        deactivation: {
          state,
          requested_at: "2026-07-24T10:00:00Z",
        },
        reactivation_allowed: false,
        blocker: "account_deactivating",
      },
    });

    const runtime = screen.getByRole("region", { name: "Deactivating" });
    expect(runtime).toHaveTextContent(/settings are read-only/i);
    expect(runtime).toHaveTextContent(progressCopy);
    expect(runtime).not.toHaveTextContent(state);
  });

  it.each([
    ["attention_required", "account_deactivating"],
    ["draining_call", "deactivation_attention_required"],
  ] as const)("presents %s / %s deactivation cleanup as attention required", async (state, blocker) => {
    await renderAgentPage({
      account: {
        status: "deactivating",
        serving: false,
        deactivation: {
          state,
          requested_at: "2026-07-24T10:00:00Z",
        },
        reactivation_allowed: false,
        blocker,
      },
    });

    const runtime = screen.getByRole("region", { name: "Attention required" });
    expect(runtime).toHaveAttribute("data-tone", "attention");
    expect(runtime).toHaveTextContent(/account cleanup needs attention/i);
    expect(runtime).toHaveTextContent(/settings are read-only/i);
    expect(runtime).not.toHaveTextContent(/attention_required|deactivation_attention_required/);
  });

  it.each([
    ["reactivation_not_ready", false, "Reactivation unavailable", "Reactivation is not ready"],
    ["deactivation_attention_required", false, "Attention required", "Account cleanup needs attention"],
    ["account_inactive", true, "Inactive", "This agent configuration is read-only"],
  ] as const)("maps inactive account blocker %s without exposing internal state", async (blocker, reactivationAllowed, label, title) => {
    await renderAgentPage({
      account: {
        status: "inactive",
        serving: false,
        deactivation: null,
        reactivation_allowed: reactivationAllowed,
        blocker,
      },
    });

    const runtime = screen.getByRole("region", { name: label });
    expect(runtime).toHaveTextContent(title);
    expect(runtime).toHaveTextContent(/settings are read-only/i);
    expect(runtime).not.toHaveTextContent(blocker);
  });

  it.each([
    ["reactivation_not_ready", "releasing_number", "Reactivation unavailable", "Releasing your Presvo number"],
    ["deactivation_attention_required", "finalizing", "Attention required", "Finalizing your account"],
  ] as const)("retains mapped inactive cleanup progress for %s", async (blocker, deactivationState, label, progressCopy) => {
    await renderAgentPage({
      account: {
        status: "inactive",
        serving: false,
        deactivation: {
          state: deactivationState,
          requested_at: "2026-07-24T10:00:00Z",
        },
        reactivation_allowed: false,
        blocker,
      },
    });

    const runtime = screen.getByRole("region", { name: label });
    expect(runtime).toHaveTextContent(progressCopy);
    expect(runtime).not.toHaveTextContent(blocker);
    expect(runtime).not.toHaveTextContent(deactivationState);
  });

  it("groups the preserved controls into exactly four named settings regions", async () => {
    await renderAgentPage();

    const sections = document.querySelectorAll('[data-slot="settings-section"]');
    expect(sections).toHaveLength(4);

    for (const name of ["Identity", "Call handling", "Business context", "Instructions"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }

    expect(screen.getByLabelText("Agent name")).toMatchObject({ id: "agent_name", name: "agent_name" });
    expect(screen.getByRole("switch", { name: "Enable call routing" })).toHaveAttribute("id", "is_enabled");
    expect(screen.getByLabelText("Owner context")).toMatchObject({
      id: "owner_context",
      name: "owner_context",
    });
    expect(screen.getByLabelText("System prompt")).toMatchObject({
      id: "system_prompt",
      name: "system_prompt",
    });
    expect(screen.getByLabelText("Knowledge base")).toMatchObject({
      id: "knowledge_base",
      name: "knowledge_base",
    });
  });

  it("saves every editable value with the complete fixed-pipeline payload and presents pending then success", async () => {
    const save = deferred<AgentConfig>();
    patchAgentConfigMock.mockReturnValueOnce(save.promise);
    await renderAgentPage();

    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "Mina" } });
    fireEvent.click(screen.getByRole("switch", { name: "Enable call routing" }));
    fireEvent.change(screen.getByLabelText("Owner context"), { target: { value: "   " } });
    fireEvent.change(screen.getByLabelText("System prompt"), { target: { value: "Keep answers concise." } });
    fireEvent.change(screen.getByLabelText("Knowledge base"), { target: { value: "Open Tuesday to Saturday." } });
    fireEvent.click(screen.getByRole("button", { name: "Save agent settings" }));

    const pendingPhase = await waitFor(() => {
      const phase = document.querySelector('[data-phase="pending"]');
      expect(phase).toBeInTheDocument();
      return phase as HTMLElement;
    });
    const pendingButton = pendingPhase.closest("button");
    expect(pendingButton).toBeDisabled();
    expect(within(pendingPhase).getByText("Saving settings")).toBeInTheDocument();
    expect(patchAgentConfigMock).toHaveBeenCalledWith({
      agent_name: "Mina",
      owner_context: null,
      system_prompt: "Keep answers concise.",
      knowledge_base: "Open Tuesday to Saturday.",
      pipeline_mode: "stt_llm_tts",
      is_enabled: false,
    });

    await act(async () => {
      save.resolve({
        agent_name: "Mina Server",
        owner_context: null,
        system_prompt: "Keep answers concise.",
        knowledge_base: "Open Tuesday to Saturday.",
        pipeline_mode: "stt_llm_tts",
        is_enabled: false,
      });
    });

    const successPhase = await waitFor(() => {
      const phase = document.querySelector('[data-phase="success"]');
      expect(phase).toBeInTheDocument();
      return phase as HTMLElement;
    });
    expect(within(successPhase).getByText("Settings saved")).toBeInTheDocument();
    expect(screen.getByLabelText("Agent name")).toHaveValue("Mina Server");
    expect(screen.getByRole("status", { name: "Save feedback" })).toHaveTextContent("Agent settings saved.");
    expect(toast.success).toHaveBeenCalledWith("Agent settings saved.");

    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "Unsaved Mina" } });

    expect(document.querySelector('[data-phase="idle"]')).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Save feedback" })).toBeEmptyDOMElement();
  });

  it("preserves a backend-selected sts pipeline when saving unchanged settings", async () => {
    const stsConfig = {
      ...configuredAgent,
      pipeline_mode: "sts",
    } satisfies AgentConfig;
    patchAgentConfigMock.mockResolvedValueOnce(stsConfig);
    await renderAgentPage({ config: stsConfig });

    fireEvent.click(screen.getByRole("button", { name: "Save agent settings" }));

    await waitFor(() =>
      expect(patchAgentConfigMock).toHaveBeenCalledWith({
        ...stsConfig,
      }),
    );
  });

  it("presents an idle action and truthful error feedback outside the save control", async () => {
    patchAgentConfigMock.mockRejectedValueOnce(new BackendApiError("Provider unavailable", 502));
    await renderAgentPage();

    const idleButton = screen.getByRole("button", { name: "Save agent settings" });
    expect(within(idleButton).getByText("Save agent settings").closest("[data-phase]")).toHaveAttribute(
      "data-phase",
      "idle",
    );

    fireEvent.click(idleButton);

    const errorPhase = await waitFor(() => {
      const phase = document.querySelector('[data-phase="error"]');
      expect(phase).toBeInTheDocument();
      return phase as HTMLElement;
    });
    expect(within(errorPhase).getByText("Try saving again")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Save feedback" })).toHaveTextContent(
      "Failed to update telephony state. Try again in a moment.",
    );
    expect(toast.error).toHaveBeenCalledWith("Failed to update telephony state. Try again in a moment.");

    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "Ava updated" } });

    expect(document.querySelector('[data-phase="idle"]')).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Save feedback" })).toBeEmptyDOMElement();
  });

  it("clears prior feedback throughout a direct retry and replaces it with the new result", async () => {
    const retry = deferred<AgentConfig>();
    patchAgentConfigMock
      .mockRejectedValueOnce(new BackendApiError("Provider unavailable", 502))
      .mockReturnValueOnce(retry.promise);
    await renderAgentPage();

    fireEvent.click(screen.getByRole("button", { name: "Save agent settings" }));

    const errorPhase = await waitFor(() => {
      const phase = document.querySelector('[data-phase="error"]');
      expect(phase).toBeInTheDocument();
      return phase as HTMLElement;
    });
    expect(screen.getByRole("status", { name: "Save feedback" })).toHaveTextContent(
      "Failed to update telephony state. Try again in a moment.",
    );

    fireEvent.click(errorPhase.closest("button") as HTMLButtonElement);

    const pendingPhase = await waitFor(() => {
      const phase = document.querySelector('[data-phase="pending"]');
      expect(phase).toBeInTheDocument();
      return phase as HTMLElement;
    });
    expect(pendingPhase.closest("button")).toBeDisabled();
    expect(screen.getByRole("status", { name: "Save feedback" })).toBeEmptyDOMElement();
    expect(screen.queryByText("Failed to update telephony state. Try again in a moment.")).not.toBeInTheDocument();

    await act(async () => {
      retry.resolve({
        ...configuredAgent,
        agent_name: "Ava refreshed",
      });
    });

    await waitFor(() => expect(document.querySelector('[data-phase="success"]')).toBeInTheDocument());
    expect(screen.getByLabelText("Agent name")).toHaveValue("Ava refreshed");
    expect(screen.getByRole("status", { name: "Save feedback" })).toHaveTextContent("Agent settings saved.");
  });

  it.each([
    "deactivating",
    "inactive",
  ] as const)("preserves saved settings but disables every mutation while the account is %s", async (status) => {
    const account: AccountStatus = {
      status,
      serving: false,
      deactivation: status === "deactivating" ? { state: "draining_call", requested_at: "2026-07-24T10:00:00Z" } : null,
      reactivation_allowed: status === "inactive",
      blocker: status === "deactivating" ? "account_deactivating" : "account_inactive",
    };

    await renderAgentPage({
      account,
      config: {
        ...configuredAgent,
        is_enabled: false,
      },
    });

    expect(
      screen.getByText("These saved settings are read-only while the account is deactivating or inactive."),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("Ava")).toBeDisabled();
    expect(screen.getByDisplayValue("Reception for North Clinic")).toBeDisabled();
    expect(screen.getByDisplayValue("Be helpful.")).toBeDisabled();
    expect(screen.getByDisplayValue("Open weekdays")).toBeDisabled();
    expect(screen.getByRole("switch", { name: "Enable call routing" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save agent settings" })).toBeDisabled();
  });

  it("does not expose runtime architecture choices", async () => {
    await renderAgentPage();

    expect(screen.queryByText(/\b(?:pipeline|STS|STT|LLM|TTS)\b/i)).not.toBeInTheDocument();
  });

  it("returns a conflict message for guarded enable failures", async () => {
    patchAgentConfigMock.mockRejectedValueOnce(new BackendApiError("Agent setup incomplete", 409));

    const { saveAgentSettingsAction } = await import("@/app/(app)/dashboard/agent/actions");
    const result = await saveAgentSettingsAction({ is_enabled: true });

    expect(result.status).toBe("error");
    expect(result.message).toMatch(/billing is active, your number is assigned, and setup is complete/i);
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
