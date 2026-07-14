import Link from "next/link";

import {
  ArrowRight,
  Bot,
  Check,
  ChevronRight,
  ClipboardCheck,
  Clock3,
  FileText,
  MessageSquare,
  PhoneCall,
  ShieldCheck,
  Sparkles,
  Waves,
} from "lucide-react";

import {
  LandingAmbientGlow,
  LandingMotionCard,
  LandingMotionFade,
  LandingMotionGroup,
  LandingMotionItem,
} from "@/components/landing/landing-motion";
import { Button } from "@/components/ui/button";
import SonicWaveformCanvas from "@/components/ui/sonic-waveform";
import { getServerSessionState } from "@/lib/auth/server-session";

const featureCards = [
  {
    icon: Clock3,
    title: "Instant Response Every Time",
    description: "Presvo answers inbound calls quickly so clients are not left waiting or sent to voicemail.",
  },
  {
    icon: Waves,
    title: "24/7 Call Handling",
    description: "Stay available after hours, during meetings, and through busy periods without adding headcount.",
  },
  {
    icon: FileText,
    title: "Summaries You Can Review Fast",
    description: "Each completed conversation is easier to review with structured notes, transcripts, and recordings.",
  },
  {
    icon: MessageSquare,
    title: "Made for Small-Team Follow-Up",
    description:
      "Keep context clear so the next action is obvious when you return a call or continue the conversation.",
  },
] as const;

const operationalPoints = [
  "Handles missed-call windows before they become lost business",
  "Works well for solo operators and small teams",
  "Captures transcripts with call review",
  "Helps you stay current without staying glued to the phone",
  "Keeps customer communication organized",
] as const;

const steps = [
  {
    title: "Connect your business flow",
    description: "Set the assistant up around your business context, availability, and preferred handling style.",
  },
  {
    title: "Presvo handles the call",
    description:
      "Inbound calls are answered consistently, even when you are unavailable or already speaking with someone else.",
  },
  {
    title: "Review and follow up",
    description: "Open the dashboard to review summaries, transcripts, recordings, and the next action to take.",
  },
] as const;

const secondaryFeatures = [
  {
    icon: Bot,
    title: "Live call management",
    description: "A calmer way to stay responsive when the phone rings at the wrong time.",
  },
  {
    icon: ClipboardCheck,
    title: "Real follow-up context",
    description: "See what happened quickly instead of piecing calls together from memory.",
  },
  {
    icon: ShieldCheck,
    title: "Built for dependable handling",
    description: "A professional front line for people who cannot afford to sound disorganized.",
  },
] as const;

const faqs = [
  {
    question: "Who is Presvo for?",
    answer:
      "Presvo is designed for professional individuals and small businesses that need reliable call coverage without a dedicated receptionist.",
  },
  {
    question: "What happens after a call finishes?",
    answer:
      "You can review the call inside the dashboard with summary details, transcript history, and recording access when available.",
  },
  {
    question: "Can I manage the assistant myself?",
    answer:
      "Yes. The dashboard is built so you can update agent settings, review calls, and monitor usage without extra tooling.",
  },
  {
    question: "Do signed-in users still see the landing page?",
    answer:
      "Yes. The landing page stays public, but signed-in users see dashboard actions instead of sign-in and sign-up prompts.",
  },
] as const;

const linkMotionClass = "transition-all duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:-translate-y-px";

const buttonMotionClass =
  "transition-all duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:-translate-y-0.5 active:translate-y-0";

export default async function Page() {
  const session = await getServerSessionState();
  const isAuthenticated = session.isAuthenticated;

  return (
    <main className="min-h-screen bg-[linear-gradient(180deg,oklch(0.99_0.01_252)_0%,oklch(0.992_0.003_240)_40%,oklch(0.985_0.004_240)_100%)] text-slate-950">
      <div className="relative overflow-hidden">
        <SonicWaveformCanvas />
        <div className="absolute inset-x-0 top-0 h-[45rem] bg-[radial-gradient(circle_at_center,oklch(0.85_0.12_255/0.42),transparent_58%)]" />

        <div className="relative mx-auto flex w-full max-w-6xl flex-col px-6 pt-6 pb-6 sm:px-8 lg:px-10">
          <LandingMotionFade delay={0.04}>
            <header className="flex items-center justify-between gap-4">
              <Link
                href="/"
                className={`${linkMotionClass} inline-flex items-center gap-3 font-semibold text-slate-900 text-sm uppercase tracking-[0.18em]`}
              >
                <span className="flex size-9 items-center justify-center rounded-full border border-slate-200 bg-white/90 shadow-sm transition-shadow duration-300 hover:shadow-md">
                  <PhoneCall className="size-4 text-[oklch(0.55_0.18_257)]" />
                </span>
                Presvo
              </Link>

              <div className="hidden items-center gap-7 text-slate-600 text-sm md:flex">
                <a href="#features" className={`${linkMotionClass} hover:text-slate-950`}>
                  Features
                </a>
                <a href="#how-it-works" className={`${linkMotionClass} hover:text-slate-950`}>
                  How it works
                </a>
                <a href="#faq" className={`${linkMotionClass} hover:text-slate-950`}>
                  FAQ
                </a>
              </div>

              <div className="flex items-center gap-3">
                {isAuthenticated ? (
                  <Button
                    asChild
                    size="sm"
                    className={`${buttonMotionClass} rounded-full px-5 shadow-sm hover:shadow-md`}
                  >
                    <Link href="/dashboard">Dashboard</Link>
                  </Button>
                ) : (
                  <>
                    <Button asChild variant="ghost" size="sm" className={`${buttonMotionClass} rounded-full px-4`}>
                      <Link href="/sign-in">Log in</Link>
                    </Button>
                    <Button
                      asChild
                      size="sm"
                      className={`${buttonMotionClass} rounded-full px-5 shadow-sm hover:shadow-md`}
                    >
                      <Link href="/sign-up">Sign up</Link>
                    </Button>
                  </>
                )}
              </div>
            </header>
          </LandingMotionFade>

          <LandingMotionGroup className="mx-auto flex w-full max-w-4xl flex-col items-center pt-18 text-center sm:pt-24">
            <LandingMotionItem>
              <div className="inline-flex items-center gap-2 rounded-full border border-white/70 bg-white/75 px-4 py-2 font-medium text-slate-600 text-xs uppercase tracking-[0.22em] shadow-sm backdrop-blur">
                <Sparkles className="size-3.5 text-[oklch(0.58_0.17_257)]" />
                AI voice agents for professionals
              </div>
            </LandingMotionItem>

            <LandingMotionItem>
              <h1 className="mt-6 max-w-3xl font-medium text-4xl text-slate-950 leading-tight tracking-tight sm:text-5xl lg:text-6xl">
                AI Voice Agents That Handle Your Business Calls Smoothly
              </h1>
            </LandingMotionItem>

            <LandingMotionItem>
              <p className="mt-5 max-w-2xl text-base text-slate-600 leading-8 sm:text-lg">
                Presvo helps professional individuals and small businesses answer calls, stay available, and review
                every conversation without turning call handling into another full-time job.
              </p>
            </LandingMotionItem>

            <LandingMotionItem>
              <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
                {isAuthenticated ? (
                  <Button
                    asChild
                    size="lg"
                    className={`${buttonMotionClass} rounded-full px-6 shadow-sm hover:shadow-lg`}
                  >
                    <Link href="/dashboard">
                      Dashboard
                      <ArrowRight className="size-4 transition-transform duration-300 group-hover/button:translate-x-0.5" />
                    </Link>
                  </Button>
                ) : (
                  <>
                    <Button
                      asChild
                      size="lg"
                      className={`${buttonMotionClass} rounded-full px-6 shadow-sm hover:shadow-lg`}
                    >
                      <Link href="/sign-up">
                        Start with Presvo
                        <ArrowRight className="size-4 transition-transform duration-300 group-hover/button:translate-x-0.5" />
                      </Link>
                    </Button>
                    <Button
                      asChild
                      variant="outline"
                      size="lg"
                      className={`${buttonMotionClass} rounded-full border-white/80 bg-white/70 px-6 shadow-sm hover:shadow-md`}
                    >
                      <Link href="/sign-in">Log in</Link>
                    </Button>
                  </>
                )}
              </div>
            </LandingMotionItem>
          </LandingMotionGroup>
        </div>
      </div>

      <section id="features1" className="mx-auto w-full max-w-6xl px-6 pb-8 sm:px-8 lg:px-10">
        <LandingMotionFade delay={0.16}>
          <div className="w-full max-w-5xl rounded-[2rem] border border-white/70 bg-white/55 px-5 py-6 shadow-[0_40px_100px_-60px_rgba(82,110,255,0.85)] backdrop-blur sm:px-8 sm:py-8">
            <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
              <div className="rounded-[1.75rem] border border-slate-200/70 bg-[linear-gradient(145deg,rgba(255,255,255,0.9),rgba(236,242,255,0.98))] p-6 text-left shadow-sm">
                <div className="flex items-center justify-between gap-3 text-slate-500 text-sm">
                  <span className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1 shadow-xs">
                    <span className="size-2 rounded-full bg-emerald-500" />
                    Ready for inbound calls
                  </span>
                  <span>Calm coverage, all day</span>
                </div>

                <div className="mt-8 space-y-4">
                  <LandingMotionCard>
                    <div className="rounded-2xl border border-white/80 bg-white/90 p-4 shadow-sm transition-shadow duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:shadow-md">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="font-medium text-slate-950 text-sm">Incoming call answered</p>
                          <p className="mt-1 text-slate-500 text-sm">
                            Presvo handled the call while you were in a meeting.
                          </p>
                        </div>
                        <PhoneCall className="size-4 text-[oklch(0.58_0.17_257)]" />
                      </div>
                    </div>
                  </LandingMotionCard>

                  <LandingMotionCard delay={0.05}>
                    <div className="rounded-2xl border border-white/80 bg-white/90 p-4 shadow-sm transition-shadow duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:shadow-md">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="font-medium text-slate-950 text-sm">Summary prepared</p>
                          <p className="mt-1 text-slate-500 text-sm">
                            Review the reason for the call, key details, and follow-up notes.
                          </p>
                        </div>
                        <FileText className="size-4 text-[oklch(0.58_0.17_257)]" />
                      </div>
                    </div>
                  </LandingMotionCard>

                  <LandingMotionCard delay={0.1}>
                    <div className="rounded-2xl border border-white/80 bg-white/90 p-4 shadow-sm transition-shadow duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:shadow-md">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="font-medium text-slate-950 text-sm">Dashboard stays organized</p>
                          <p className="mt-1 text-slate-500 text-sm">
                            Calls, transcripts, recordings, and usage stay in one place.
                          </p>
                        </div>
                        <ClipboardCheck className="size-4 text-[oklch(0.58_0.17_257)]" />
                      </div>
                    </div>
                  </LandingMotionCard>
                </div>
              </div>

              <div className="flex flex-col justify-between rounded-[1.75rem] border border-white/80 bg-[linear-gradient(180deg,rgba(118,140,255,0.12),rgba(255,255,255,0.92))] p-6 text-left shadow-sm">
                <div>
                  <p className="font-medium text-slate-500 text-sm">What Presvo changes</p>
                  <p className="mt-3 max-w-sm font-medium text-2xl text-slate-950 leading-tight">
                    A more dependable front line for every call your business cannot afford to miss.
                  </p>
                </div>

                <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
                  <LandingMotionCard>
                    <div className="rounded-2xl border border-white/80 bg-white/90 p-4 transition-shadow duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:shadow-md">
                      <p className="font-medium text-3xl text-slate-950">24/7</p>
                      <p className="mt-1 text-slate-500 text-sm">Coverage for busy hours, evenings, and overlap.</p>
                    </div>
                  </LandingMotionCard>

                  <LandingMotionCard delay={0.05}>
                    <div className="rounded-2xl border border-white/80 bg-white/90 p-4 transition-shadow duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:shadow-md">
                      <p className="font-medium text-3xl text-slate-950">1 place</p>
                      <p className="mt-1 text-slate-500 text-sm">To review calls, summaries, settings, and usage.</p>
                    </div>
                  </LandingMotionCard>
                </div>
              </div>
            </div>
          </div>
        </LandingMotionFade>

        <LandingMotionFade
          delay={0.22}
          className="mt-10 flex flex-wrap items-center justify-center gap-x-8 gap-y-3 text-slate-400 text-sm"
        >
          <span>LiveKit</span>
          <span>Telnyx</span>
          <span>Clerk</span>
          <span>Stripe</span>
          <span>Dashboard review</span>
        </LandingMotionFade>
      </section>

      <section id="features" className="mx-auto w-full max-w-6xl px-6 py-8 sm:px-8 lg:px-10">
        <LandingMotionFade delay={0.2} className="mx-auto max-w-2xl text-center">
          <p className="font-medium text-[oklch(0.58_0.17_257)] text-sm uppercase tracking-[0.22em]">Core features</p>
          <h2 className="mt-3 font-medium text-3xl text-slate-950 tracking-tight sm:text-4xl">
            Powerful AI call handling for everyday business communication
          </h2>
          <p className="mt-4 text-base text-slate-600 leading-7">
            The product stays focused on what smaller operations need most: better availability, cleaner follow-up, and
            less time spent untangling what happened on the phone.
          </p>
        </LandingMotionFade>

        <div className="mt-12 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {featureCards.map(({ icon: Icon, title, description }, index) => (
            <LandingMotionCard key={title} delay={0.16 + index * 0.04}>
              <article className="h-full rounded-[1.75rem] border border-slate-200/80 bg-white px-6 py-6 shadow-[0_20px_60px_-45px_rgba(15,23,42,0.45)] transition-shadow duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:shadow-[0_28px_72px_-42px_rgba(15,23,42,0.38)]">
                <span className="flex size-11 items-center justify-center rounded-2xl bg-[oklch(0.95_0.03_252)] text-[oklch(0.58_0.17_257)]">
                  <Icon className="size-5" />
                </span>
                <h3 className="mt-6 font-medium text-lg text-slate-950">{title}</h3>
                <p className="mt-3 text-slate-600 text-sm leading-7">{description}</p>
              </article>
            </LandingMotionCard>
          ))}
        </div>

        <div className="mx-auto mt-10 max-w-2xl space-y-3">
          {operationalPoints.map((point, index) => (
            <LandingMotionCard key={point} delay={0.2 + index * 0.03}>
              <div className="flex items-center justify-between rounded-full border border-slate-200/80 bg-white px-5 py-3 text-slate-700 text-sm shadow-sm transition-shadow duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:shadow-md">
                <span className="inline-flex items-center gap-3">
                  <span className="flex size-7 items-center justify-center rounded-full bg-[oklch(0.58_0.17_257)]/10 text-[oklch(0.58_0.17_257)]">
                    <Check className="size-4" />
                  </span>
                  {point}
                </span>
                <ChevronRight className="size-4 text-slate-300" />
              </div>
            </LandingMotionCard>
          ))}
        </div>
      </section>

      <section className="mx-auto w-full max-w-4xl px-6 py-14 text-center sm:px-8">
        <LandingMotionFade delay={0.18}>
          <p className="font-medium text-[oklch(0.58_0.17_257)] text-sm uppercase tracking-[0.22em]">Why it matters</p>
          <h2 className="mt-4 font-medium text-3xl text-slate-950 tracking-tight sm:text-4xl">
            Why outdated call handling quietly costs you customers
          </h2>
          <p className="mt-4 text-base text-slate-600 leading-8 sm:text-lg">
            Missed calls, rushed notes, and unclear follow-up create friction that smaller businesses feel immediately.
            Presvo gives you a calmer system for staying responsive when you cannot answer every call yourself.
          </p>
        </LandingMotionFade>
      </section>

      <section
        id="how-it-works"
        className="bg-[linear-gradient(180deg,oklch(0.63_0.2_264),oklch(0.67_0.16_254))] px-6 py-18 text-white sm:px-8 lg:px-10"
      >
        <div className="mx-auto max-w-6xl">
          <LandingMotionFade delay={0.16} className="mx-auto max-w-2xl text-center">
            <p className="font-medium text-sm text-white/80 uppercase tracking-[0.22em]">How it works</p>
            <h2 className="mt-3 font-medium text-3xl tracking-tight sm:text-4xl">
              A simple flow that stays easy to trust
            </h2>
            <p className="mt-4 text-base text-white/78 leading-7">
              Presvo is meant to feel operationally clear: set it up, let it handle the call, and review what happened
              when you are ready.
            </p>
          </LandingMotionFade>

          <div className="mt-12 grid gap-5 lg:grid-cols-3">
            {steps.map((step, index) => (
              <LandingMotionCard key={step.title} delay={0.12 + index * 0.05}>
                <article className="h-full rounded-[1.75rem] border border-white/20 bg-white/12 p-6 shadow-lg backdrop-blur-sm transition-shadow duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:shadow-2xl">
                  <span className="inline-flex size-10 items-center justify-center rounded-full bg-white/18 font-semibold text-sm">
                    0{index + 1}
                  </span>
                  <h3 className="mt-6 font-medium text-xl">{step.title}</h3>
                  <p className="mt-3 text-sm text-white/78 leading-7">{step.description}</p>
                </article>
              </LandingMotionCard>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto w-full max-w-6xl px-6 py-18 sm:px-8 lg:px-10">
        <LandingMotionFade delay={0.18} className="mx-auto max-w-3xl text-center">
          <p className="font-medium text-[oklch(0.58_0.17_257)] text-sm uppercase tracking-[0.22em]">
            Built for real work
          </p>
          <h2 className="mt-3 font-medium text-3xl text-slate-950 tracking-tight sm:text-4xl">
            Advanced AI phone agents for smarter business communication
          </h2>
          <p className="mt-4 text-base text-slate-600 leading-7">
            Everything on the page points back to a simple outcome: helping smaller operations stay responsive without
            sounding scattered.
          </p>
        </LandingMotionFade>

        <div className="mt-12 grid gap-5 md:grid-cols-3">
          {secondaryFeatures.map(({ icon: Icon, title, description }, index) => (
            <LandingMotionCard key={title} delay={0.15 + index * 0.05}>
              <article className="h-full rounded-[1.75rem] border border-slate-200/80 bg-white px-6 py-6 shadow-sm transition-shadow duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:shadow-lg">
                <span className="flex size-11 items-center justify-center rounded-2xl bg-[oklch(0.95_0.03_252)] text-[oklch(0.58_0.17_257)]">
                  <Icon className="size-5" />
                </span>
                <h3 className="mt-6 font-medium text-lg text-slate-950">{title}</h3>
                <p className="mt-3 text-slate-600 text-sm leading-7">{description}</p>
              </article>
            </LandingMotionCard>
          ))}
        </div>
      </section>

      <section
        id="faq"
        className="mx-auto grid w-full max-w-6xl gap-10 px-6 py-12 sm:px-8 lg:grid-cols-[0.85fr_1.15fr] lg:px-10"
      >
        <LandingMotionFade delay={0.12}>
          <div>
            <p className="font-medium text-[oklch(0.58_0.17_257)] text-sm uppercase tracking-[0.22em]">FAQ</p>
            <h2 className="mt-3 font-medium text-3xl text-slate-950 tracking-tight sm:text-4xl">
              Frequently asked questions
            </h2>
            <p className="mt-4 max-w-md text-base text-slate-600 leading-7">
              A few practical questions people usually ask before trying call automation for the first time.
            </p>
          </div>
        </LandingMotionFade>

        <div className="space-y-4">
          {faqs.map(({ question, answer }, index) => (
            <LandingMotionCard key={question} delay={0.14 + index * 0.04}>
              <article className="rounded-[1.5rem] border border-slate-200/80 bg-white px-6 py-5 shadow-sm transition-shadow duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:shadow-lg">
                <h3 className="font-medium text-base text-slate-950">{question}</h3>
                <p className="mt-3 text-slate-600 text-sm leading-7">{answer}</p>
              </article>
            </LandingMotionCard>
          ))}
        </div>
      </section>

      <footer className="relative overflow-hidden bg-slate-950 px-6 py-14 text-slate-200 sm:px-8 lg:px-10">
        <LandingAmbientGlow className="absolute inset-x-0 bottom-[-11rem] mx-auto h-72 w-[34rem] rounded-full bg-[radial-gradient(circle,rgba(118,140,255,0.95),rgba(118,140,255,0.18)_46%,transparent_72%)] blur-3xl" />

        <LandingMotionFade
          delay={0.2}
          className="relative mx-auto grid w-full max-w-6xl gap-10 lg:grid-cols-[1fr_0.8fr_0.8fr_0.8fr]"
        >
          <div>
            <p className="font-medium text-slate-400 text-sm uppercase tracking-[0.22em]">Made for</p>
            <h2 className="mt-4 font-medium text-3xl text-white">Presvo</h2>
            <p className="mt-4 max-w-sm text-slate-400 text-sm leading-7">
              AI voice agents for professional individuals and small businesses that want calmer, more dependable call
              handling.
            </p>
          </div>

          <div>
            <p className="font-medium text-slate-500 text-sm uppercase tracking-[0.22em]">Product</p>
            <ul className="mt-4 space-y-3 text-slate-300 text-sm">
              <li>
                <a href="#features" className={`${linkMotionClass} hover:text-white`}>
                  Features
                </a>
              </li>
              <li>
                <a href="#how-it-works" className={`${linkMotionClass} hover:text-white`}>
                  How it works
                </a>
              </li>
              <li>
                <a href="#faq" className={`${linkMotionClass} hover:text-white`}>
                  FAQ
                </a>
              </li>
            </ul>
          </div>

          <div>
            <p className="font-medium text-slate-500 text-sm uppercase tracking-[0.22em]">Access</p>
            <ul className="mt-4 space-y-3 text-slate-300 text-sm">
              {isAuthenticated ? (
                <li>
                  <Link href="/dashboard" className={`${linkMotionClass} hover:text-white`}>
                    Dashboard
                  </Link>
                </li>
              ) : (
                <>
                  <li>
                    <Link href="/sign-in" className={`${linkMotionClass} hover:text-white`}>
                      Log in
                    </Link>
                  </li>
                  <li>
                    <Link href="/sign-up" className={`${linkMotionClass} hover:text-white`}>
                      Sign up
                    </Link>
                  </li>
                </>
              )}
            </ul>
          </div>

          <div>
            <p className="font-medium text-slate-500 text-sm uppercase tracking-[0.22em]">Positioning</p>
            <ul className="mt-4 space-y-3 text-slate-300 text-sm">
              <li>Calm</li>
              <li>Capable</li>
              <li>Polished</li>
            </ul>
          </div>
        </LandingMotionFade>
      </footer>
    </main>
  );
}
