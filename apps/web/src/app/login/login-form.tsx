"use client";

import {
  BarChart3,
  Package,
  Percent,
  Receipt,
  ShieldCheck,
  ShoppingCart,
  TrendingUp,
  Users,
  Wallet,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  AnimatedForm,
  AuthTabs,
  Ripple,
  TechOrbitDisplay,
  type FormField,
  type Orbit,
} from "@/components/ui/modern-animated-sign-in";
import { SunMoonThemeToggle } from "@/components/ui/sun-moon-theme-toggle";
import { postJson, resetCsrfToken } from "@/lib/api/client";
import { ApiProblem, type User } from "@/lib/api/types";

export function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    if (error.status === 401) {
      return "Invalid email or password.";
    }
    if (error.status === 429) {
      const minutes = Math.max(1, Math.ceil((error.retryAfterSeconds ?? 900) / 60));
      return `Too many sign-in attempts. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
    }
    if (error.status === 422) {
      return "Enter a valid email address and password.";
    }
  }
  return "Sign-in failed. Please try again.";
}

const ORBITS: Orbit[] = [
  { radius: 100, duration: 18, icons: [BarChart3, TrendingUp] },
  { radius: 150, duration: 26, reverse: true, icons: [ShoppingCart, Package] },
  { radius: 210, duration: 34, icons: [Users, Receipt] },
  { radius: 270, duration: 42, reverse: true, icons: [Percent, Wallet] },
  { radius: 320, duration: 50, icons: [ShieldCheck] },
];

// Matches the server's password policy (MIN_PASSWORD_LENGTH / MAX_PASSWORD_LENGTH), so a
// password that could never have been set is caught before a request is spent on it.
const FIELDS: FormField[] = [
  {
    id: "email",
    label: "Email",
    type: "email",
    placeholder: "Enter your email address",
    autoComplete: "username",
    required: true,
    maxLength: 254,
  },
  {
    id: "password",
    label: "Password",
    type: "password",
    placeholder: "Enter your password",
    autoComplete: "current-password",
    required: true,
    minLength: 12,
    maxLength: 128,
  },
];

export function LoginForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(values: Record<string, string>) {
    setSubmitting(true);
    setError(null);
    try {
      await postJson<User>("/api/v1/auth/login", { email: values.email ?? "", password: values.password ?? "" });
      resetCsrfToken(); // the token is bound to the session, which just changed
      router.replace("/");
      router.refresh();
    } catch (caught) {
      setError(loginErrorMessage(caught));
      setSubmitting(false);
    }
  }

  return (
    <AuthTabs
      corner={<SunMoonThemeToggle storageKey="root-theme" size={36} duration={900} />}
      aside={
        <>
          <Ripple />
          <TechOrbitDisplay text="ROOT" caption="Find out why your numbers changed." orbits={ORBITS} />
        </>
      }
    >
      <AnimatedForm
        header="Welcome back"
        subHeader="Sign in to ROOT"
        helper="Use the account provided for your business."
        fields={FIELDS}
        submitLabel="Sign in"
        submittingLabel="Signing in…"
        error={error}
        submitting={submitting}
        onSubmit={(values) => void onSubmit(values)}
        footer={
          <p className="text-sm text-muted-foreground">
            Forgot password? Ask whoever set up your ROOT account to reset it.
          </p>
        }
      />
    </AuthTabs>
  );
}
