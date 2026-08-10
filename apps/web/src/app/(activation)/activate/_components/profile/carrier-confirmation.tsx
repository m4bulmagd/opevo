"use client";

import { useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { formatFrenchNumber, normalizeFrenchNumber } from "@/lib/phone-numbers";
import type { CarrierCode } from "@/lib/types/activation";

import { lookupCarrierAction } from "../../actions";

const CARRIERS: Array<{ value: CarrierCode; label: string }> = [
  { value: "orange", label: "Orange" },
  { value: "sfr", label: "SFR" },
  { value: "bouygues", label: "Bouygues Telecom" },
  { value: "free", label: "Free" },
  { value: "other", label: "Other" },
];

type CarrierConfirmationProps = {
  phoneNumber: string;
  confirmedCarrier: CarrierCode | null;
  onPhoneChange: (value: string) => void;
  onConfirm: (carrier: CarrierCode) => void;
  onSaveBeforeLookup: () => Promise<boolean>;
  initialDetectedCarrier?: string | null;
  initialDetectedCarrierCode?: CarrierCode | null;
  initialLookupError?: string | null;
  phoneValidationError?: string;
  carrierValidationError?: string;
  inputRef?: React.Ref<HTMLInputElement>;
};

export function CarrierConfirmation({
  phoneNumber,
  confirmedCarrier,
  onPhoneChange,
  onConfirm,
  onSaveBeforeLookup,
  initialDetectedCarrier,
  initialDetectedCarrierCode,
  initialLookupError,
  phoneValidationError,
  carrierValidationError,
  inputRef,
}: CarrierConfirmationProps) {
  const [detectedLabel, setDetectedLabel] = useState(initialDetectedCarrier ?? null);
  const [detectedCode, setDetectedCode] = useState<CarrierCode | null>(initialDetectedCarrierCode ?? null);
  const [lookupError, setLookupError] = useState<string | null>(initialLookupError ?? null);
  const [phoneError, setPhoneError] = useState<string | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const checkedNumbersRef = useRef(new Set<string>());
  const normalized = normalizeFrenchNumber(phoneNumber);
  const validationError = phoneValidationError ?? carrierValidationError ?? phoneError;

  const checkCarrier = async () => {
    if (!normalized) {
      setPhoneError("Enter a valid French number, for example 06 12 34 56 78.");
      return;
    }
    setPhoneError(null);
    setLookupError(null);
    setLookingUp(true);
    const saved = await onSaveBeforeLookup();
    if (!saved) {
      setLookupError("Save the number before checking its carrier.");
      setLookingUp(false);
      return;
    }
    const result = await lookupCarrierAction({});
    setLookingUp(false);
    if (result.status === "error") {
      setDetectedLabel(null);
      setDetectedCode(null);
      setLookupError(result.message);
      return;
    }
    setDetectedLabel(result.data.carrier_name ?? "Carrier detected");
    setDetectedCode(result.data.normalized_carrier);
  };

  const checkOnceOnBlur = () => {
    if (!normalized) {
      setPhoneError("Enter a valid French number, for example 06 12 34 56 78.");
      return;
    }
    setPhoneError(null);
    if (checkedNumbersRef.current.has(normalized)) return;
    checkedNumbersRef.current.add(normalized);
    void checkCarrier();
  };

  return (
    <div className="flex flex-col gap-4 rounded-xl border bg-muted/20 p-4">
      <Field data-invalid={Boolean(validationError)}>
        <FieldLabel htmlFor="existing-phone">Existing French number</FieldLabel>
        <Input
          id="existing-phone"
          name="existing_phone_e164"
          ref={inputRef}
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          aria-invalid={Boolean(validationError)}
          aria-describedby={
            validationError ? "existing-phone-description existing-phone-error" : "existing-phone-description"
          }
          value={formatFrenchNumber(phoneNumber)}
          onChange={(event) => {
            setPhoneError(null);
            setDetectedLabel(null);
            setDetectedCode(null);
            onPhoneChange(formatFrenchNumber(event.target.value));
          }}
          onBlur={checkOnceOnBlur}
        />
        <FieldDescription id="existing-phone-description">
          French numbers stay on your current line; Opevo only handles the missed-call route you configure later.
        </FieldDescription>
        {validationError ? <FieldError id="existing-phone-error">{validationError}</FieldError> : null}
      </Field>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" disabled={lookingUp} onClick={() => void checkCarrier()}>
          {lookingUp ? "Checking…" : "Check carrier"}
        </Button>
        {confirmedCarrier ? (
          <Badge variant="secondary">
            Confirmed: {CARRIERS.find((carrier) => carrier.value === confirmedCarrier)?.label ?? confirmedCarrier}
          </Badge>
        ) : null}
      </div>

      {detectedLabel && detectedCode ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-background p-3">
          <div>
            <p className="font-medium text-sm">Suggested carrier</p>
            <p className="text-muted-foreground text-sm">{detectedLabel}</p>
          </div>
          <Button type="button" onClick={() => onConfirm(detectedCode)}>
            Confirm carrier
          </Button>
        </div>
      ) : null}

      {lookupError ? (
        <Alert variant="destructive">
          <AlertTitle>Carrier check unavailable</AlertTitle>
          <AlertDescription>{lookupError}</AlertDescription>
        </Alert>
      ) : null}

      {lookupError ? (
        <Button className="self-start" type="button" variant="outline" onClick={() => void checkCarrier()}>
          Retry carrier check
        </Button>
      ) : null}

      {detectedCode || lookupError ? (
        <Field>
          <FieldLabel htmlFor="manual-carrier">Choose carrier manually</FieldLabel>
          <NativeSelect
            className="w-full"
            id="manual-carrier"
            name="confirmed_carrier"
            value={confirmedCarrier ?? ""}
            onChange={(event) => {
              const value = event.target.value as CarrierCode;
              if (value) onConfirm(value);
            }}
          >
            <NativeSelectOption value="">Select your carrier</NativeSelectOption>
            {CARRIERS.map((carrier) => (
              <NativeSelectOption key={carrier.value} value={carrier.value}>
                {carrier.label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
      ) : null}
    </div>
  );
}
