import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CarrierConfirmation } from "@/app/(activation)/activate/_components/profile/carrier-confirmation";

const { lookupCarrierMock } = vi.hoisted(() => ({ lookupCarrierMock: vi.fn() }));

vi.mock("@/app/(activation)/activate/actions", () => ({
  lookupCarrierAction: lookupCarrierMock,
}));

describe("carrier confirmation", () => {
  beforeEach(() => lookupCarrierMock.mockReset());

  it("saves a valid French number before the automatic first-blur lookup and requires confirmation", async () => {
    const onSaveBeforeLookup = vi.fn().mockResolvedValue(true);
    const onConfirm = vi.fn();
    lookupCarrierMock.mockResolvedValue({
      status: "success",
      data: {
        normalized_number: "+33612345678",
        country_code: "FR",
        carrier_name: "Orange France",
        normalized_carrier: "orange",
        number_type: "mobile",
        looked_up_at: "2026-07-17T10:00:00Z",
      },
      message: "Carrier check complete.",
    });

    render(
      <CarrierConfirmation
        confirmedCarrier={null}
        onConfirm={onConfirm}
        onPhoneChange={vi.fn()}
        onSaveBeforeLookup={onSaveBeforeLookup}
        phoneNumber="06 12 34 56 78"
      />,
    );
    fireEvent.blur(screen.getByLabelText(/Existing French number/i));

    await waitFor(() => expect(onSaveBeforeLookup).toHaveBeenCalledTimes(1));
    expect(lookupCarrierMock).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Orange France/i)).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Confirm carrier/i }));
    expect(onConfirm).toHaveBeenCalledWith("orange");

    fireEvent.blur(screen.getByLabelText(/Existing French number/i));
    await waitFor(() => expect(lookupCarrierMock).toHaveBeenCalledTimes(1));
  });

  it("formats French numbers as the customer types", () => {
    const onPhoneChange = vi.fn();
    const view = render(
      <CarrierConfirmation
        confirmedCarrier={null}
        onConfirm={vi.fn()}
        onPhoneChange={onPhoneChange}
        onSaveBeforeLookup={vi.fn()}
        phoneNumber=""
      />,
    );

    fireEvent.change(screen.getByLabelText(/Existing French number/i), { target: { value: "0612345678" } });
    expect(onPhoneChange).toHaveBeenCalledWith("06 12 34 56 78");
    view.rerender(
      <CarrierConfirmation
        confirmedCarrier={null}
        onConfirm={vi.fn()}
        onPhoneChange={onPhoneChange}
        onSaveBeforeLookup={vi.fn()}
        phoneNumber="06 12 34 56 78"
      />,
    );
    expect(screen.getByLabelText(/Existing French number/i)).toHaveValue("06 12 34 56 78");
  });

  it("does not look up invalid numbers and shows French formatting guidance", () => {
    render(
      <CarrierConfirmation
        confirmedCarrier={null}
        onConfirm={vi.fn()}
        onPhoneChange={vi.fn()}
        onSaveBeforeLookup={vi.fn()}
        phoneNumber="06 12"
      />,
    );
    fireEvent.blur(screen.getByLabelText(/Existing French number/i));

    expect(screen.getByRole("alert")).toHaveTextContent(/valid French number/i);
    expect(lookupCarrierMock).not.toHaveBeenCalled();
  });

  it("resumes a persisted failed lookup with retry and manual choices visible", () => {
    render(
      <CarrierConfirmation
        confirmedCarrier={null}
        initialLookupError="We couldn't check your carrier. Choose it manually or retry."
        onConfirm={vi.fn()}
        onPhoneChange={vi.fn()}
        onSaveBeforeLookup={vi.fn()}
        phoneNumber="+33612345678"
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/couldn't check your carrier/i);
    expect(screen.getByRole("button", { name: /Retry carrier check/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Choose carrier manually/i)).toBeInTheDocument();
  });

  it("offers retry and all five manual carriers immediately after lookup failure", async () => {
    const onConfirm = vi.fn();
    lookupCarrierMock.mockResolvedValue({
      status: "error",
      code: "carrier_lookup_unavailable",
      message: "We couldn't check your carrier. Choose it manually to continue.",
    });
    render(
      <CarrierConfirmation
        confirmedCarrier={null}
        onConfirm={onConfirm}
        onPhoneChange={vi.fn()}
        onSaveBeforeLookup={vi.fn().mockResolvedValue(true)}
        phoneNumber="+33 6 12 34 56 78"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Check carrier/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Choose it manually/i);
    expect(screen.getByRole("button", { name: /Retry carrier check/i })).toBeInTheDocument();
    const carrier = screen.getByLabelText(/Choose carrier manually/i);
    expect(carrier).toContainHTML("Orange");
    expect(carrier).toContainHTML("SFR");
    expect(carrier).toContainHTML("Bouygues Telecom");
    expect(carrier).toContainHTML("Free");
    expect(carrier).toContainHTML("Other");

    fireEvent.change(carrier, { target: { value: "bouygues" } });
    expect(onConfirm).toHaveBeenCalledWith("bouygues");

    fireEvent.click(screen.getByRole("button", { name: /Retry carrier check/i }));
    await waitFor(() => expect(lookupCarrierMock).toHaveBeenCalledTimes(2));
  });
});
