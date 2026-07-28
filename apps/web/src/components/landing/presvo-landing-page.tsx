import Link from "next/link";

import { ArrowRight, BriefcaseBusiness, Check, Clock3, FileText, PhoneCall, ShieldCheck, Sparkles } from "lucide-react";

import { LandingMotionFade, LandingMotionGroup, LandingMotionItem } from "@/components/landing/landing-motion";
import { CapabilityBadge } from "@/components/product/capability-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const PUBLIC_NAVIGATION = [
  { href: "#product", label: "Product" },
  { href: "#how-it-works", label: "How it works" },
  { href: "#questions", label: "Questions" },
] as const;

const PRODUCT_FEATURES = [
  {
    icon: PhoneCall,
    title: "Answer the calls you miss",
    description: "Conditionally forward unanswered, busy, or unreachable calls while keeping your existing line.",
  },
  {
    icon: FileText,
    title: "Return with the context",
    description: "Review structured call details, summaries, transcripts, and follow-up signals in one calm workspace.",
  },
  {
    icon: ShieldCheck,
    title: "Choose when it goes live",
    description: "Presvo stays off until your forwarding test succeeds and you explicitly approve activation.",
  },
] as const;

const ENTRY_STEPS = [
  {
    title: "Share your business context",
    description: "Add your hours, services, existing French number, and the details callers need.",
  },
  {
    title: "Provision your Presvo line",
    description: "Activate the starter plan, then explicitly approve one French number for conditional forwarding.",
  },
  {
    title: "Verify before going live",
    description: "Test missed-call forwarding and choose when Presvo may begin answering.",
  },
] as const;

const QUESTIONS = [
  {
    question: "Does Presvo replace my existing number?",
    answer:
      "No. Your existing French business number stays with your carrier. You conditionally forward only unanswered, busy, and unreachable calls to Presvo.",
  },
  {
    question: "When does Presvo start answering?",
    answer:
      "Only after your profile, plan, French number, and forwarding test are ready—and after you explicitly choose Go live.",
  },
  {
    question: "Can I review what happened on a call?",
    answer:
      "Yes. Your authenticated workspace presents call history, follow-up signals, transcripts, and recording availability when the backend provides them.",
  },
] as const;

function PresvoMark() {
  return (
    <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground">
      <PhoneCall aria-hidden="true" className="size-4" />
    </span>
  );
}

function PrimaryEntryAction({ isAuthenticated }: { isAuthenticated: boolean }) {
  return (
    <Button asChild className="min-h-11">
      <Link href={isAuthenticated ? "/dashboard" : "/sign-up"}>
        {isAuthenticated ? "Open dashboard" : "Start with Presvo"}
        <ArrowRight aria-hidden="true" data-icon="inline-end" />
      </Link>
    </Button>
  );
}

export function PresvoLandingPage({ isAuthenticated }: { isAuthenticated: boolean }) {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <a
        className="sr-only rounded-md bg-background px-3 py-2 focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:ring-3 focus:ring-ring/50"
        href="#landing-content"
      >
        Skip to main content
      </a>

      <header className="sticky top-0 z-20 border-border border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex min-h-16 w-full max-w-6xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
          <Link
            className="inline-flex min-h-11 items-center gap-3 rounded-lg font-semibold tracking-tight outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            href="/"
          >
            <PresvoMark />
            Presvo
          </Link>

          <nav aria-label="Public navigation" className="hidden items-center gap-1 md:flex">
            {PUBLIC_NAVIGATION.map((item) => (
              <Button asChild key={item.href} variant="ghost">
                <a href={item.href}>{item.label}</a>
              </Button>
            ))}
          </nav>

          <div className="flex items-center gap-2">
            {isAuthenticated ? (
              <Button asChild className="min-h-11">
                <Link href="/dashboard">Dashboard</Link>
              </Button>
            ) : (
              <>
                <Button asChild className="hidden min-h-11 sm:inline-flex" variant="ghost">
                  <Link href="/sign-in">Log in</Link>
                </Button>
                <Button asChild className="min-h-11">
                  <Link href="/sign-up">Sign up</Link>
                </Button>
              </>
            )}
          </div>
        </div>
      </header>

      <div id="landing-content">
        <section className="mx-auto grid w-full max-w-6xl items-center gap-10 px-5 py-14 sm:px-8 sm:py-20 lg:grid-cols-[minmax(0,1fr)_minmax(24rem,0.88fr)] lg:py-24">
          <LandingMotionGroup className="flex flex-col items-start">
            <LandingMotionItem>
              <Badge className="border-primary/20 bg-primary-soft text-accent-foreground" variant="outline">
                <Sparkles aria-hidden="true" />
                Built for French businesses
              </Badge>
            </LandingMotionItem>
            <LandingMotionItem>
              <h1 className="mt-5 max-w-3xl font-semibold text-4xl leading-[1.08] tracking-tight sm:text-5xl lg:text-6xl">
                Never let a missed call become missed business.
              </h1>
            </LandingMotionItem>
            <LandingMotionItem>
              <p className="mt-5 max-w-xl text-base text-muted-foreground leading-7 sm:text-lg sm:leading-8">
                Presvo answers the calls you cannot take, gives callers a consistent reception, and returns the
                conversation to you with clear follow-up context.
              </p>
            </LandingMotionItem>
            <LandingMotionItem>
              <div className="mt-7 flex flex-wrap items-center gap-3">
                <PrimaryEntryAction isAuthenticated={isAuthenticated} />
                {isAuthenticated ? null : (
                  <Button asChild className="min-h-11" variant="outline">
                    <Link href="/sign-in">Log in</Link>
                  </Button>
                )}
              </div>
            </LandingMotionItem>
            <LandingMotionItem>
              <ul className="mt-7 grid gap-2 text-muted-foreground text-sm sm:grid-cols-2">
                {[
                  "One French Presvo number",
                  "Explicit go-live approval",
                  "Conditional forwarding only",
                  "60-minute starter allowance",
                ].map((item) => (
                  <li className="flex items-center gap-2" key={item}>
                    <span className="grid size-5 shrink-0 place-items-center rounded-full bg-primary-soft text-accent-foreground">
                      <Check aria-hidden="true" className="size-3" />
                    </span>
                    {item}
                  </li>
                ))}
              </ul>
            </LandingMotionItem>
          </LandingMotionGroup>

          <LandingMotionFade delay={0.12}>
            <section
              aria-label="Product overview"
              className="rounded-2xl border border-border bg-card p-5 shadow-raised sm:p-6"
            >
              <div className="flex items-center justify-between gap-3 border-border border-b pb-4">
                <div className="flex min-w-0 items-center gap-3">
                  <PresvoMark />
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-sm">Your Presvo receptionist</p>
                    <p className="truncate text-muted-foreground text-xs">Missed-call coverage</p>
                  </div>
                </div>
                <CapabilityBadge status="live" />
              </div>

              <div className="mt-5 grid gap-3 sm:grid-cols-3">
                {[
                  ["Market", "France (+33)"],
                  ["Starter", "60 min"],
                  ["Routing", "Conditional"],
                ].map(([label, value]) => (
                  <div className="rounded-xl border border-border bg-muted/35 p-3" key={label}>
                    <p className="text-label">{label}</p>
                    <p className="mt-1 font-semibold text-sm tabular-nums">{value}</p>
                  </div>
                ))}
              </div>

              <div className="mt-4 rounded-xl border border-border bg-background p-4 shadow-card">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="font-medium text-sm">Illustrative call view</p>
                    <p className="mt-1 text-muted-foreground text-xs">A visual example, not live account data.</p>
                  </div>
                  <CapabilityBadge status="preview" />
                </div>
                <div className="mt-4 flex items-start gap-3 border-border border-t pt-4">
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary-soft text-accent-foreground">
                    <PhoneCall aria-hidden="true" className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-3">
                      <p className="truncate font-medium text-sm">New customer enquiry</p>
                      <span className="shrink-0 text-muted-foreground text-xs tabular-nums">02:18</span>
                    </div>
                    <p className="mt-1 text-muted-foreground text-xs leading-5">
                      Request captured with a clear reason, contact details, and follow-up signal.
                    </p>
                  </div>
                </div>
              </div>
            </section>
          </LandingMotionFade>
        </section>

        <LandingMotionFade className="border-border border-y bg-card/60" delay={0.08}>
          <div className="mx-auto grid w-full max-w-6xl gap-4 px-5 py-6 text-muted-foreground text-sm sm:grid-cols-3 sm:px-8">
            {[
              ["France-first setup", "French number and carrier-aware forwarding"],
              ["Backend-confirmed actions", "No fake payment, provisioning, or go-live success"],
              ["Resume safely", "Your server-owned activation stage is preserved"],
            ].map(([title, description]) => (
              <div className="flex items-start gap-3" key={title}>
                <Check aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
                <div>
                  <p className="font-medium text-foreground">{title}</p>
                  <p className="mt-1 text-xs leading-5">{description}</p>
                </div>
              </div>
            ))}
          </div>
        </LandingMotionFade>

        <section className="mx-auto w-full max-w-6xl px-5 py-16 sm:px-8 sm:py-20" id="product">
          <LandingMotionFade>
            <div className="max-w-2xl">
              <p className="text-label">Product</p>
              <h2 className="mt-2 font-semibold text-3xl tracking-tight">A calm front line for the calls you miss.</h2>
              <p className="mt-3 text-muted-foreground leading-7">
                Presvo keeps call handling focused: reliable reception, useful context, and explicit operational
                control.
              </p>
            </div>
          </LandingMotionFade>
          <div className="mt-8 grid gap-4 md:grid-cols-3">
            {PRODUCT_FEATURES.map(({ icon: Icon, title, description }, index) => (
              <LandingMotionFade className="h-full" delay={0.06 + index * 0.05} key={title}>
                <article className="h-full rounded-xl border border-border bg-card p-5 shadow-card">
                  <span className="grid size-10 place-items-center rounded-xl bg-primary-soft text-accent-foreground">
                    <Icon aria-hidden="true" className="size-4" />
                  </span>
                  <h3 className="mt-5 font-semibold text-lg">{title}</h3>
                  <p className="mt-2 text-muted-foreground text-sm leading-6">{description}</p>
                </article>
              </LandingMotionFade>
            ))}
          </div>
        </section>

        <section className="border-border border-y bg-muted/40" id="how-it-works">
          <div className="mx-auto w-full max-w-6xl px-5 py-16 sm:px-8 sm:py-20">
            <LandingMotionFade className="max-w-2xl">
              <p className="text-label">How it works</p>
              <h2 className="mt-2 font-semibold text-3xl tracking-tight">Set up carefully. Go live deliberately.</h2>
            </LandingMotionFade>
            <ol className="mt-8 grid gap-4 lg:grid-cols-3">
              {ENTRY_STEPS.map((step, index) => (
                <li className="rounded-xl border border-border bg-card p-5 shadow-card" key={step.title}>
                  <span className="grid size-8 place-items-center rounded-full bg-primary font-semibold text-primary-foreground text-sm">
                    {index + 1}
                  </span>
                  <h3 className="mt-5 font-semibold">{step.title}</h3>
                  <p className="mt-2 text-muted-foreground text-sm leading-6">{step.description}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section
          className="mx-auto grid w-full max-w-6xl gap-10 px-5 py-16 sm:px-8 sm:py-20 lg:grid-cols-[0.78fr_1.22fr]"
          id="questions"
        >
          <LandingMotionFade>
            <p className="text-label">Questions</p>
            <h2 className="mt-2 font-semibold text-3xl tracking-tight">Know what changes before you activate.</h2>
            <p className="mt-3 text-muted-foreground text-sm leading-6">
              Presvo keeps your existing number, separates payment from provisioning consent, and waits for your go-live
              approval.
            </p>
          </LandingMotionFade>
          <div className="grid gap-3">
            {QUESTIONS.map(({ question, answer }) => (
              <details className="group rounded-xl border border-border bg-card p-5 shadow-card" key={question}>
                <summary className="cursor-pointer font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
                  {question}
                </summary>
                <p className="mt-3 text-muted-foreground text-sm leading-6">{answer}</p>
              </details>
            ))}
          </div>
        </section>

        <LandingMotionFade className="px-5 pb-16 sm:px-8 sm:pb-20">
          <section className="mx-auto flex w-full max-w-6xl flex-col items-start justify-between gap-6 rounded-2xl bg-primary p-6 text-primary-foreground shadow-raised sm:p-8 md:flex-row md:items-center">
            <div>
              <div className="flex items-center gap-2 text-primary-foreground/80 text-xs uppercase tracking-[0.08em]">
                <BriefcaseBusiness aria-hidden="true" className="size-4" />
                Your missed-call receptionist
              </div>
              <h2 className="mt-2 max-w-xl font-semibold text-2xl tracking-tight">
                Give every missed call a clear next step.
              </h2>
            </div>
            <Button asChild className="min-h-11 bg-primary-foreground text-primary hover:bg-primary-foreground/90">
              <Link href={isAuthenticated ? "/dashboard" : "/sign-up"}>
                {isAuthenticated ? "Open dashboard" : "Create your account"}
                <ArrowRight aria-hidden="true" data-icon="inline-end" />
              </Link>
            </Button>
          </section>
        </LandingMotionFade>
      </div>

      <footer className="border-border border-t bg-card">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-5 py-6 text-muted-foreground text-xs sm:flex-row sm:items-center sm:justify-between sm:px-8">
          <div className="flex items-center gap-2">
            <Clock3 aria-hidden="true" className="size-4" />
            France-first activation with explicit operational consent.
          </div>
          <p>© {new Date().getFullYear()} Presvo</p>
        </div>
      </footer>
    </main>
  );
}
