import type { BusinessProfileDraft } from "@/lib/types/activation";

type ReceptionistPreviewProps = {
  draft: BusinessProfileDraft;
};

const text = (value: string | null | undefined, fallback: string) => value?.trim() || fallback;

export function ReceptionistPreview({ draft }: ReceptionistPreviewProps) {
  return (
    <aside
      className="sticky top-6 flex flex-col gap-4 rounded-2xl border bg-muted/30 p-5"
      aria-labelledby="knowledge-preview-title"
    >
      <div>
        <p className="font-medium text-primary text-sm">What callers can hear</p>
        <h2 id="knowledge-preview-title" className="font-semibold text-xl">
          Knowledge preview
        </h2>
      </div>
      <p className="text-sm leading-6">
        <strong>{text(draft.receptionist_name, "Your receptionist")}</strong> answers for{" "}
        {text(draft.business_name, "your business")}.
      </p>
      <p className="text-muted-foreground text-sm leading-6">
        {text(draft.public_description, "Add a public description to explain what your business does.")}
      </p>
      {(draft.faqs ?? []).length > 0 ? (
        <div className="flex flex-col gap-3">
          <h3 className="font-medium text-sm">Common questions</h3>
          {(draft.faqs ?? []).map((faq, index) => (
            <div className="rounded-lg border bg-background/80 p-3 text-sm" key={`preview-${index.toString()}`}>
              <p className="font-medium">{text(faq.question, `Question ${index + 1}`)}</p>
              <p className="mt-1 text-muted-foreground">{text(faq.answer, "Add the answer callers should hear.")}</p>
            </div>
          ))}
        </div>
      ) : null}
      {draft.special_instructions?.trim() ? (
        <p className="text-muted-foreground text-sm">
          <strong className="text-foreground">Special instructions:</strong> {draft.special_instructions}
        </p>
      ) : null}
      {draft.escalation_notes?.trim() ? (
        <p className="text-muted-foreground text-sm">
          <strong className="text-foreground">Escalation:</strong> {draft.escalation_notes}
        </p>
      ) : null}
    </aside>
  );
}
