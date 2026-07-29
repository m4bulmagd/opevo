import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantPreview } from "@/components/agent/assistant-preview";

describe("assistant configuration Preview", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("keeps advanced configuration, voice playback, and the test call local-only", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<AssistantPreview agentName="Lea" />);

    const preview = screen.getByRole("region", { name: "Advanced assistant Preview" });
    expect(within(preview).getByText("Preview", { exact: true })).toBeVisible();
    expect(preview).toHaveTextContent("reset on reload");
    expect(within(preview).getByRole("radio", { name: /Professional/i })).toBeChecked();
    expect(within(preview).getByRole("combobox", { name: "Preview language" })).toHaveAttribute(
      "name",
      "preview-language",
    );
    expect(within(preview).getByRole("slider", { name: "Preview speaking speed" })).toHaveAttribute(
      "name",
      "preview-speaking-speed",
    );

    fireEvent.click(within(preview).getByRole("radio", { name: /Warm/i }));
    fireEvent.change(within(preview).getByRole("combobox", { name: "Preview language" }), {
      target: { value: "fr-FR" },
    });
    fireEvent.change(within(preview).getByRole("slider", { name: "Preview speaking speed" }), {
      target: { value: "1.15" },
    });

    const voices = within(preview).getByRole("radiogroup", { name: "Preview assistant voice" });
    fireEvent.click(within(voices).getByRole("radio", { name: /Inès/i }));
    fireEvent.click(within(voices).getByRole("button", { name: "Preview Inès locally" }));
    expect(within(preview).getByRole("status", { name: "Voice preview status" })).toHaveTextContent(
      "Previewing Inès locally",
    );

    fireEvent.click(within(preview).getByRole("button", { name: "Test assistant Preview" }));
    const dialog = screen.getByRole("dialog", { name: "Test Lea" });
    expect(within(dialog).getByText("Preview", { exact: true })).toBeVisible();
    expect(within(dialog).getByRole("status", { name: "Preview call status" })).toHaveTextContent("Connecting");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(900);
    });
    expect(within(dialog).getByRole("status", { name: "Preview call status" })).toHaveTextContent("Lea is speaking");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1700);
    });
    expect(within(dialog).getByRole("status", { name: "Preview call status" })).toHaveTextContent("Listening to you");

    fireEvent.click(within(dialog).getByRole("button", { name: "End preview" }));
    expect(within(dialog).getByRole("status", { name: "Preview call status" })).toHaveTextContent("Preview ended");
    fireEvent.click(within(dialog).getByRole("button", { name: "Restart preview" }));
    expect(within(dialog).getByRole("status", { name: "Preview call status" })).toHaveTextContent("Connecting");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));

    fireEvent.click(within(preview).getByRole("button", { name: "Reset Preview settings" }));
    expect(within(preview).getByRole("radio", { name: /Professional/i })).toBeChecked();
    expect(within(voices).getByRole("radio", { name: /Camille/i })).toBeChecked();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
