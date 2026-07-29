"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import type { BusinessHours, DayHours, Weekday } from "@/lib/types/activation";

const DAYS: Array<{ key: Weekday; label: string }> = [
  { key: "monday", label: "Monday" },
  { key: "tuesday", label: "Tuesday" },
  { key: "wednesday", label: "Wednesday" },
  { key: "thursday", label: "Thursday" },
  { key: "friday", label: "Friday" },
  { key: "saturday", label: "Saturday" },
  { key: "sunday", label: "Sunday" },
];

export function createDefaultBusinessHours(): BusinessHours {
  return Object.fromEntries(
    DAYS.map(({ key }) => [
      key,
      key === "saturday" || key === "sunday"
        ? { closed: true, intervals: [] }
        : { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
    ]),
  ) as BusinessHours;
}

function cloneHours(value: BusinessHours): BusinessHours {
  return Object.fromEntries(
    DAYS.map(({ key }) => [
      key,
      { ...value[key], intervals: value[key].intervals.map((interval) => ({ ...interval })) },
    ]),
  ) as BusinessHours;
}

export function validateBusinessHours(value: BusinessHours): { day: Weekday; index: number; message: string } | null {
  for (const { key, label } of DAYS) {
    const day = value[key];
    if (day.closed && day.intervals.length > 0) {
      return { day: key, index: 0, message: `${label} is closed and cannot contain hours.` };
    }
    if (!day.closed && day.intervals.length === 0) {
      return { day: key, index: 0, message: `${label} needs at least one interval.` };
    }
    const ordered = day.intervals
      .map((interval, index) => ({ ...interval, index }))
      .sort((left, right) => left.start.localeCompare(right.start));
    for (const interval of ordered) {
      if (!interval.start || !interval.end || interval.end <= interval.start) {
        return { day: key, index: interval.index, message: `${label} hours must end after they start.` };
      }
    }
    for (let index = 1; index < ordered.length; index += 1) {
      if (ordered[index - 1].end > ordered[index].start) {
        return { day: key, index: ordered[index].index, message: `${label} intervals cannot overlap.` };
      }
    }
  }
  return null;
}

type BusinessHoursEditorProps = {
  value: BusinessHours;
  onChange: (value: BusinessHours) => void;
  maxIntervalsPerDay: number;
  invalid?: boolean;
};

export function BusinessHoursEditor({ value, onChange, maxIntervalsPerDay, invalid }: BusinessHoursEditorProps) {
  const [hours, setHours] = useState(() => cloneHours(value));
  const [error, setError] = useState<ReturnType<typeof validateBusinessHours>>(null);
  const hoursRef = useRef(hours);
  const inputRefs = useRef(new Map<string, HTMLInputElement>());

  hoursRef.current = hours;

  useEffect(() => setHours(cloneHours(value)), [value]);

  const commit = (next: BusinessHours) => {
    setHours(next);
    onChange(next);
  };

  const updateDay = (day: Weekday, nextDay: DayHours) => {
    commit({ ...hours, [day]: nextDay });
    if (error?.day === day) setError(null);
  };

  const validateAndFocus = (event: React.FocusEvent<HTMLInputElement>) => {
    const nextError = validateBusinessHours(hours);
    setError(nextError);
    const nextControl = event.relatedTarget;
    let movingWithinEditor = false;
    for (const input of inputRefs.current.values()) {
      if (input === nextControl) movingWithinEditor = true;
    }
    if (nextError && !movingWithinEditor) {
      inputRefs.current.get(`${nextError.day}-${nextError.index}-start`)?.focus();
    }
  };

  useEffect(() => {
    if (!invalid) return;
    const nextError = validateBusinessHours(hoursRef.current);
    setError(nextError);
    if (nextError) {
      inputRefs.current.get(`${nextError.day}-${nextError.index}-start`)?.focus();
    }
  }, [invalid]);

  return (
    <FieldSet aria-invalid={invalid || Boolean(error)}>
      <FieldLegend>Weekly hours</FieldLegend>
      <FieldGroup className="gap-5">
        {DAYS.map(({ key, label }) => {
          const day = hours[key];
          return (
            <Field key={key} className="rounded-xl border bg-card/60 p-4" data-invalid={error?.day === key}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="font-medium text-sm">{label}</p>
                <FieldLabel htmlFor={`${key}-closed`} className="items-center">
                  <input
                    id={`${key}-closed`}
                    className="size-4 accent-primary"
                    name={`${key}_closed`}
                    type="checkbox"
                    aria-label={`${label} closed`}
                    checked={day.closed}
                    onChange={(event) =>
                      updateDay(
                        key,
                        event.target.checked
                          ? { closed: true, intervals: [] }
                          : { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
                      )
                    }
                  />
                  Closed
                </FieldLabel>
              </div>
              {!day.closed ? (
                <div className="flex flex-col gap-3">
                  {day.intervals.map((interval, intervalIndex) => (
                    <div
                      className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
                      key={`${key}-${intervalIndex.toString()}`}
                    >
                      <Field>
                        <FieldLabel htmlFor={`${key}-${intervalIndex}-start`}>
                          {label} start {intervalIndex + 1}
                        </FieldLabel>
                        <Input
                          id={`${key}-${intervalIndex}-start`}
                          aria-invalid={error?.day === key && error.index === intervalIndex}
                          name={`${key}_interval_${intervalIndex}_start`}
                          ref={(node) => {
                            if (node) inputRefs.current.set(`${key}-${intervalIndex}-start`, node);
                          }}
                          type="time"
                          value={interval.start}
                          onBlur={validateAndFocus}
                          onChange={(event) => {
                            const intervals = day.intervals.map((item, index) =>
                              index === intervalIndex ? { ...item, start: event.target.value } : item,
                            );
                            updateDay(key, { ...day, intervals });
                          }}
                        />
                      </Field>
                      <Field>
                        <FieldLabel htmlFor={`${key}-${intervalIndex}-end`}>
                          {label} end {intervalIndex + 1}
                        </FieldLabel>
                        <Input
                          id={`${key}-${intervalIndex}-end`}
                          aria-invalid={error?.day === key && error.index === intervalIndex}
                          name={`${key}_interval_${intervalIndex}_end`}
                          type="time"
                          value={interval.end}
                          onBlur={validateAndFocus}
                          onChange={(event) => {
                            const intervals = day.intervals.map((item, index) =>
                              index === intervalIndex ? { ...item, end: event.target.value } : item,
                            );
                            updateDay(key, { ...day, intervals });
                          }}
                        />
                      </Field>
                      {day.intervals.length > 1 ? (
                        <Button
                          className="self-end"
                          type="button"
                          variant="outline"
                          aria-label={`Remove ${label} interval ${intervalIndex + 1}`}
                          onClick={() =>
                            updateDay(key, {
                              ...day,
                              intervals: day.intervals.filter((_, index) => index !== intervalIndex),
                            })
                          }
                        >
                          Remove interval
                        </Button>
                      ) : null}
                    </div>
                  ))}
                  {day.intervals.length < maxIntervalsPerDay ? (
                    <Button
                      className="self-start"
                      type="button"
                      variant="outline"
                      onClick={() =>
                        updateDay(key, {
                          ...day,
                          intervals: [...day.intervals, { start: "", end: "" }],
                        })
                      }
                      aria-label={`Add interval for ${label}`}
                    >
                      Add interval
                    </Button>
                  ) : null}
                </div>
              ) : (
                <p className="text-muted-foreground text-sm">No calls are answered as open-business hours.</p>
              )}
              {error?.day === key ? <FieldError>{error.message}</FieldError> : null}
            </Field>
          );
        })}
      </FieldGroup>
    </FieldSet>
  );
}
