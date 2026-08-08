import { useState } from "react";

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UnsavedChangesBar } from "@/components/forms/unsaved-changes-bar";
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes-guard";

function GuardHarness({ initialDirty = false }: { initialDirty?: boolean }) {
  const [dirty, setDirty] = useState(initialDirty);
  useUnsavedChangesGuard(dirty);

  return (
    <>
      <button type="button" onClick={() => setDirty((value) => !value)}>
        Toggle dirty
      </button>
      <a href="/dashboard/billing">Leave page</a>
    </>
  );
}

describe("unsaved changes foundations", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders no save bar while clean", () => {
    render(<UnsavedChangesBar dirty={false} pending={false} onDiscard={vi.fn()} onSave={vi.fn()} />);

    expect(screen.queryByRole("status", { name: "Unsaved changes" })).not.toBeInTheDocument();
  });

  it("renders the Opevo save bar and locks both actions while pending", () => {
    const onDiscard = vi.fn();
    const onSave = vi.fn();
    const view = render(<UnsavedChangesBar dirty pending={false} onDiscard={onDiscard} onSave={onSave} />);

    const status = screen.getByRole("status", { name: "Unsaved changes" });
    expect(status).toHaveTextContent("You have unsaved changes");
    expect(status).toHaveClass("sticky", "shadow-raised");

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(onDiscard).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledTimes(1);

    view.rerender(<UnsavedChangesBar dirty pending onDiscard={onDiscard} onSave={onSave} />);
    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Saving changes" })).toBeDisabled();
  });

  it("warns for unload and same-origin links only while dirty", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const view = render(<GuardHarness />);
    const link = screen.getByRole("link", { name: "Leave page" });

    link.addEventListener("click", (event) => event.preventDefault(), { once: true });
    fireEvent.click(link);
    expect(confirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Toggle dirty" }));
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(click);
    expect(confirm).toHaveBeenCalledWith("You have unsaved changes. Leave without saving?");
    expect(click.defaultPrevented).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Toggle dirty" }));
    view.unmount();
    expect(() => window.dispatchEvent(new Event("beforeunload", { cancelable: true }))).not.toThrow();
  });
});
