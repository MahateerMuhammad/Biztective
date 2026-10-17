"use client";

import { cn } from "cn";
import { Eye, EyeOff, LoaderCircle, type LucideIcon } from "lucide-react";
import {
  motion,
  useAnimation,
  useInView,
  useMotionTemplate,
  useMotionValue,
  useReducedMotion,
} from "motion/react";
import {
  useEffect,
  useRef,
  useState,
  type ComponentProps,
  type CSSProperties,
  type FormEvent,
  type ReactNode,
} from "react";

import { useHydrated } from "@/lib/use-hydrated";

/* ------------------------------------------------------------------ inputs */

const GLOW_RADIUS = 100;

/** Text input whose wrapper shows a blue glow that follows the pointer while hovered. */
export function Input({ className, type, ...props }: ComponentProps<"input">) {
  const [hovered, setHovered] = useState(false);
  const mouseX = useMotionValue(0);
  const mouseY = useMotionValue(0);
  const background = useMotionTemplate`radial-gradient(${hovered ? GLOW_RADIUS : 0}px circle at ${mouseX}px ${mouseY}px, #3b82f6, transparent 80%)`;

  return (
    <motion.div
      style={{ background }}
      onMouseMove={({ currentTarget, clientX, clientY }) => {
        const { left, top } = currentTarget.getBoundingClientRect();
        mouseX.set(clientX - left);
        mouseY.set(clientY - top);
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="group/input rounded-lg p-[2px] transition duration-300"
    >
      <input
        type={type}
        className={cn(
          "flex h-10 w-full rounded-md border-none bg-input px-3 py-2 text-sm text-foreground shadow-[0_0_1px_1px_var(--btn-border)] transition duration-300 placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:outline-none aria-invalid:ring-2 aria-invalid:ring-red-400 disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        {...props}
      />
    </motion.div>
  );
}

export function Label({ className, ...props }: ComponentProps<"label">) {
  return <label className={cn("text-sm leading-none font-medium text-foreground", className)} {...props} />;
}

/** Cyan/indigo glow line under a button, shown on hover. The button needs the `group/btn` class. */
export function BottomGradient() {
  return (
    <>
      <span className="absolute inset-x-0 -bottom-px block h-px w-full bg-gradient-to-r from-transparent via-cyan-500 to-transparent opacity-0 transition duration-500 group-hover/btn:opacity-100" />
      <span className="absolute inset-x-10 -bottom-px mx-auto block h-px w-1/2 bg-gradient-to-r from-transparent via-indigo-500 to-transparent opacity-0 blur-sm transition duration-500 group-hover/btn:opacity-100" />
    </>
  );
}

/* ----------------------------------------------------------------- reveals */

/** Slides its children up when scrolled into view, behind a box that sweeps across. */
export function BoxReveal({
  children,
  width = "fit-content",
  boxColor = "var(--skeleton)",
  duration = 0.3,
  className,
}: {
  children: ReactNode;
  width?: "fit-content" | "100%";
  boxColor?: string;
  duration?: number;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true });
  const content = useAnimation();
  const box = useAnimation();

  useEffect(() => {
    if (inView) {
      void box.start("visible");
      void content.start("visible");
    }
  }, [inView, box, content]);

  if (reduceMotion) {
    return <div className={className} style={{ width }}>{children}</div>;
  }
  return (
    <div ref={ref} className={cn("relative overflow-hidden", className)} style={{ width }}>
      <motion.div
        variants={{ hidden: { opacity: 0, y: 75 }, visible: { opacity: 1, y: 0 } }}
        initial="hidden"
        animate={content}
        transition={{ duration, delay: 0.25 }}
      >
        {children}
      </motion.div>
      <motion.div
        aria-hidden
        variants={{ hidden: { left: 0 }, visible: { left: "100%" } }}
        initial="hidden"
        animate={box}
        transition={{ duration, ease: "easeIn" }}
        className="absolute inset-y-1 right-0 left-0 z-20 rounded-sm"
        style={{ background: boxColor }}
      />
    </div>
  );
}

/* ------------------------------------------------------------- decoration */

/** Concentric, softly pulsing circles that fade out towards the bottom. */
export function Ripple({ count = 8, baseSize = 210, baseOpacity = 0.24 }: { count?: number; baseSize?: number; baseOpacity?: number }) {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 [mask-image:linear-gradient(to_bottom,white,transparent)] select-none"
    >
      {Array.from({ length: count }, (_, i) => {
        const size = baseSize + i * 70;
        return (
          <div
            key={i}
            className="absolute top-1/2 left-1/2 animate-ripple rounded-full border border-blue-500/25 bg-blue-500/10 dark:border-blue-300/20 dark:bg-blue-400/10 shadow-xl"
            style={{
              width: size,
              height: size,
              opacity: baseOpacity - i * 0.03,
              animationDelay: `${i * 0.06}s`,
              transform: "translate(-50%, -50%) scale(1)",
            }}
          />
        );
      })}
    </div>
  );
}

/** Places its children on a circle and rotates them around the centre. Positions are static
 * when the visitor prefers reduced motion. */
export function OrbitingCircles({
  children,
  radius,
  duration = 20,
  reverse = false,
  phase = 0,
}: {
  children: ReactNode[];
  radius: number;
  duration?: number;
  reverse?: boolean;
  /** Starting angle in degrees, so neighbouring orbits don't line up. */
  phase?: number;
}) {
  return children.map((child, i) => (
    <div
      key={i}
      className="absolute flex size-12 animate-orbit items-center justify-center rounded-full border border-blue-500/25 bg-blue-100/70 dark:border-blue-300/15 dark:bg-blue-950/60"
      style={
        {
          "--orbit-radius": `${radius}px`,
          "--orbit-start": `${phase + (360 / children.length) * i}deg`,
          "--orbit-duration": `${duration}s`,
          animationDirection: reverse ? "reverse" : "normal",
          transform: "rotate(var(--orbit-start)) translateY(var(--orbit-radius)) rotate(calc(-1 * var(--orbit-start)))",
        } as CSSProperties
      }
    >
      {child}
    </div>
  ));
}

export type Orbit = { radius: number; duration: number; reverse?: boolean; icons: LucideIcon[] };

export function TechOrbitDisplay({ text, caption, orbits }: { text: string; caption?: string; orbits: Orbit[] }) {
  return (
    <div aria-hidden className="relative flex size-full items-center justify-center overflow-hidden">
      <div className="relative z-10 text-center">
        <p className="bg-gradient-to-b from-foreground to-blue-500/60 dark:to-blue-300/60 bg-clip-text text-7xl font-semibold tracking-tight text-transparent">
          {text}
        </p>
        {caption ? <p className="mt-3 text-sm text-muted-foreground">{caption}</p> : null}
      </div>
      {orbits.map((orbit, i) => (
        <OrbitingCircles
          key={orbit.radius}
          radius={orbit.radius}
          duration={orbit.duration}
          reverse={orbit.reverse}
          phase={i * 47}
        >
          {orbit.icons.map((Icon, i) => (
            <Icon key={i} className="size-6 text-blue-600/80 dark:text-blue-300/80" strokeWidth={1.75} />
          ))}
        </OrbitingCircles>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------------- form */

export type FormField = {
  id: string;
  label: string;
  type: "email" | "password";
  placeholder: string;
  autoComplete: string;
  required?: boolean;
  minLength?: number;
  maxLength?: number;
};

function validate(field: FormField, value: string): string | null {
  if (field.required && value.trim() === "") return `${field.label} is required.`;
  if (field.type === "email" && !/^[^@\s]+@[^@\s]+$/.test(value.trim())) return "Enter a valid email address.";
  if (field.minLength && value.length < field.minLength) {
    return `${field.label} must be at least ${field.minLength} characters.`;
  }
  return null;
}

/**
 * Labelled fields with inline validation, a show/hide toggle for passwords, a server error area
 * and a submit button. `onSubmit` receives the values only once every field is valid.
 */
export function AnimatedForm({
  header,
  subHeader,
  helper,
  fields,
  submitLabel,
  submittingLabel,
  error,
  submitting,
  footer,
  onSubmit,
}: {
  header: string;
  subHeader: string;
  helper?: string;
  fields: FormField[];
  submitLabel: string;
  submittingLabel: string;
  error: string | null;
  submitting: boolean;
  footer?: ReactNode;
  onSubmit: (values: Record<string, string>) => void;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  // Until hydration a click would fall back to a native POST that reloads the page and drops
  // what was typed (method="post" keeps credentials out of the URL even then).
  const hydrated = useHydrated();
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [visible, setVisible] = useState<Record<string, boolean>>({});

  function values(): Record<string, string> {
    const data = new FormData(formRef.current ?? undefined);
    return Object.fromEntries(fields.map((f) => [f.id, String(data.get(f.id) ?? "")]));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const current = values();
    const found = Object.fromEntries(fields.map((f) => [f.id, validate(f, current[f.id] ?? "")]));
    setErrors(found);
    const firstInvalid = fields.find((f) => found[f.id]);
    if (firstInvalid) {
      document.getElementById(firstInvalid.id)?.focus();
      return;
    }
    onSubmit(current);
  }

  return (
    <section className="w-full max-w-sm">
      <BoxReveal>
        <h1 className="text-3xl font-bold text-foreground">{header}</h1>
      </BoxReveal>
      <BoxReveal className="mt-2">
        <p className="text-base text-muted-foreground">{subHeader}</p>
      </BoxReveal>
      {helper ? (
        <BoxReveal className="mt-1">
          <p className="text-xs text-muted-foreground">{helper}</p>
        </BoxReveal>
      ) : null}

      <form
        ref={formRef}
        method="post"
        noValidate
        aria-busy={submitting}
        onSubmit={handleSubmit}
        className="mt-8 grid gap-5"
      >
        {fields.map((field) => {
          const fieldError = errors[field.id];
          const describedBy = fieldError ? `${field.id}-error` : undefined;
          const isPassword = field.type === "password";
          const shown = visible[field.id] ?? false;
          return (
            <div key={field.id} className="grid gap-2">
              <BoxReveal>
                {/* The asterisk stays outside the label so the accessible name is just the label;
                    "required" itself is announced from the input's required attribute. */}
                <Label htmlFor={field.id}>{field.label}</Label>
                {field.required ? <span aria-hidden className="ml-0.5 text-sm text-red-400">*</span> : null}
              </BoxReveal>
              <BoxReveal width="100%">
                <div className="relative">
                  <Input
                    id={field.id}
                    name={field.id}
                    type={isPassword && shown ? "text" : field.type}
                    placeholder={field.placeholder}
                    autoComplete={field.autoComplete}
                    required={field.required}
                    maxLength={field.maxLength}
                    aria-invalid={fieldError || error ? true : undefined}
                    aria-describedby={describedBy}
                    className={isPassword ? "pr-10" : undefined}
                    onBlur={(e) => {
                      // Re-check on blur only after a first submit, so nothing is flagged early.
                      if (errors[field.id] !== undefined) {
                        const message = validate(field, e.currentTarget.value);
                        setErrors((prev) => ({ ...prev, [field.id]: message }));
                      }
                    }}
                  />
                  {isPassword ? (
                    <button
                      type="button"
                      aria-label={shown ? "Hide password" : "Show password"}
                      aria-pressed={shown}
                      aria-controls={field.id}
                      onClick={() => setVisible((prev) => ({ ...prev, [field.id]: !shown }))}
                      className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:outline-none"
                    >
                      {shown ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
                    </button>
                  ) : null}
                </div>
              </BoxReveal>
              {fieldError ? (
                <p id={`${field.id}-error`} className="text-xs text-red-400">
                  {fieldError}
                </p>
              ) : null}
            </div>
          );
        })}

        {error ? (
          <p role="alert" className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-200">
            {error}
          </p>
        ) : null}

        <BoxReveal width="100%">
          <button
            type="submit"
            disabled={!hydrated || submitting}
            className="group/btn relative flex h-10 w-full items-center justify-center gap-2 rounded-md bg-gradient-to-br from-zinc-800 to-zinc-900 font-medium text-white shadow-[0_1px_0_0_#ffffff20_inset,0_-1px_0_0_#ffffff20_inset] outline-none focus-visible:ring-2 focus-visible:ring-blue-400 disabled:cursor-wait disabled:opacity-70"
          >
            {submitting ? (
              <>
                <LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
                {submittingLabel}
              </>
            ) : (
              <>
                {submitLabel} <span aria-hidden>→</span>
              </>
            )}
            <BottomGradient />
          </button>
        </BoxReveal>
        {footer ? <BoxReveal>{footer}</BoxReveal> : null}
      </form>
    </section>
  );
}

/** Full-height sign-in layout: decoration on the left (large screens only), form on the right. */
export function AuthTabs({ aside, corner, children }: { aside: ReactNode; corner?: ReactNode; children: ReactNode }) {
  return (
    <div className="auth-surface relative flex min-h-dvh w-full bg-background text-foreground">
      {corner ? <div className="absolute top-4 right-4 z-20">{corner}</div> : null}
      <div className="relative hidden flex-1 items-center justify-center overflow-hidden lg:flex">{aside}</div>
      <main className="flex flex-1 items-center justify-center px-4 py-10 sm:px-6">{children}</main>
    </div>
  );
}
