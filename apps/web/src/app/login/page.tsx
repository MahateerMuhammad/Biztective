import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { getMe } from "@/lib/api/server";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage() {
  if (await getMe()) {
    redirect("/");
  }
  return (
    <main className="flex min-h-dvh items-center justify-center p-4">
      <LoginForm />
    </main>
  );
}
