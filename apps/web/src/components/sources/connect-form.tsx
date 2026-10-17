"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { postJson } from "@/lib/api/client";
import { ApiProblem, type SourceConnection, type SourceSyncResult, type SourceTestResult } from "@/lib/api/types";
import { useHydrated } from "@/lib/use-hydrated";

const ROLES = ["orders", "products", "customers", "returns"] as const;
type Role = (typeof ROLES)[number];

// Same select styling as the column mapping review.
const selectClass = "h-11 w-full rounded-lg border border-input bg-background px-3 text-[15px]";

interface FormError {
  message: string;
  details: string[];
}

function errorFrom(error: unknown, fallback: string): FormError {
  if (error instanceof ApiProblem) return { message: error.message, details: error.details };
  return { message: fallback, details: [] };
}

function ErrorAlert({ error }: { error: FormError }) {
  return (
    <Alert variant="destructive" role="alert">
      <AlertTitle>{error.message}</AlertTitle>
      {error.details.length > 0 ? (
        <AlertDescription>
          <ul className="list-disc pl-4">
            {error.details.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </AlertDescription>
      ) : null}
    </Alert>
  );
}

interface Fields {
  host: string;
  port: string;
  database: string;
  username: string;
  schema: string;
}

/** Test a PostgreSQL connection, map its tables, then save it. The password lives only in the form. */
export function ConnectForm({ businessId }: { businessId: string }) {
  const router = useRouter();
  const hydrated = useHydrated();
  const passwordRef = useRef<HTMLInputElement>(null);
  const [fields, setFields] = useState<Fields>({ host: "", port: "5432", database: "", username: "", schema: "public" });
  const [tables, setTables] = useState<string[] | null>(null);
  const [mapping, setMapping] = useState<Record<Role, string>>({ orders: "", products: "", customers: "", returns: "" });
  const [pending, setPending] = useState<"test" | "save" | null>(null);
  const [error, setError] = useState<FormError | null>(null);
  const [saved, setSaved] = useState(false);

  function update(name: keyof Fields, value: string) {
    setFields((f) => ({ ...f, [name]: value }));
    setTables(null); // a changed connection must be tested again
    setSaved(false);
  }

  function takePassword(): string {
    const input = passwordRef.current;
    const value = input?.value ?? "";
    if (input) input.value = "";
    return value;
  }

  function body(password: string) {
    return { ...fields, port: Number(fields.port), password };
  }

  async function onTest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const password = takePassword();
    setPending("test");
    setError(null);
    setSaved(false);
    try {
      const result = await postJson<SourceTestResult>(`/api/v1/businesses/${businessId}/sources/test`, body(password));
      const found = result?.tables ?? [];
      setTables(found);
      setMapping({
        orders: found.includes("orders") ? "orders" : "",
        products: found.includes("products") ? "products" : "",
        customers: found.includes("customers") ? "customers" : "",
        returns: found.includes("returns") ? "returns" : "",
      });
    } catch (caught) {
      setTables(null);
      setError(errorFrom(caught, "The connection test failed. Check your network and try again."));
    } finally {
      setPending(null);
    }
  }

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const password = takePassword();
    setPending("save");
    setError(null);
    try {
      await postJson<SourceConnection>(`/api/v1/businesses/${businessId}/sources`, { ...body(password), tables: mapping });
      setSaved(true);
      setTables(null);
      router.refresh();
    } catch (caught) {
      setError(errorFrom(caught, "The connection could not be saved. Try again."));
    } finally {
      setPending(null);
    }
  }

  const mapped = ROLES.every((role) => mapping[role] !== "");

  return (
    <div className="grid gap-5">
      <form className="grid gap-4" onSubmit={tables ? onSave : onTest} noValidate aria-label="PostgreSQL connection">
        <div className="grid gap-4 sm:grid-cols-2">
          {(
            [
              ["host", "Host", "text"],
              ["port", "Port", "number"],
              ["database", "Database", "text"],
              ["username", "Username", "text"],
              ["schema", "Schema", "text"],
            ] as const
          ).map(([name, label, type]) => (
            <div key={name} className="grid gap-1.5">
              <Label htmlFor={`pg-${name}`}>{label}</Label>
              <Input
                id={`pg-${name}`}
                name={name}
                type={type}
                required
                autoComplete="off"
                spellCheck={false}
                value={fields[name]}
                onChange={(e) => update(name, e.target.value)}
              />
            </div>
          ))}
          <div className="grid gap-1.5">
            <Label htmlFor="pg-password">Password</Label>
            <Input
              id="pg-password"
              ref={passwordRef}
              name="password"
              type="password"
              required
              autoComplete="new-password"
              aria-describedby="pg-password-hint"
            />
            <p id="pg-password-hint" className="text-[13px] text-muted-foreground">
              Use a read-only database user. The password is sent once and is not stored.
            </p>
          </div>
        </div>

        {tables ? (
          <fieldset className="grid gap-3 rounded-lg border border-border p-4">
            <legend className="px-1 text-[15px] font-medium">Connection valid — choose the tables</legend>
            <p className="text-[13px] text-muted-foreground">
              Found {tables.length} {tables.length === 1 ? "table" : "tables"}. No data has been copied yet.
            </p>
            <ul aria-label="Discovered tables" className="flex flex-wrap gap-2 text-[13px]">
              {tables.map((t) => (
                <li key={t} className="rounded-lg bg-muted px-2 py-0.5">{t}</li>
              ))}
            </ul>
            <div className="grid gap-4 sm:grid-cols-2">
              {ROLES.map((role) => (
                <div key={role} className="grid gap-1.5">
                  <Label htmlFor={`map-${role}`} className="capitalize">{role} table</Label>
                  <select
                    id={`map-${role}`}
                    className={selectClass}
                    value={mapping[role]}
                    onChange={(e) => setMapping((m) => ({ ...m, [role]: e.target.value }))}
                    required
                  >
                    <option value="">Choose a table…</option>
                    {tables.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
            <p className="text-[13px] text-muted-foreground">Enter the password again to save.</p>
          </fieldset>
        ) : null}

        {error ? <ErrorAlert error={error} /> : null}
        {saved ? (
          <p role="status" className="text-[15px]">
            Connection saved. Use “Sync now” below to copy the data.
          </p>
        ) : null}

        <div className="flex flex-wrap gap-3">
          {tables ? (
            <>
              <Button type="submit" disabled={!hydrated || pending !== null || !mapped}>
                {pending === "save" ? "Saving…" : "Save connection"}
              </Button>
              <Button type="button" variant="outline" disabled={pending !== null} onClick={() => setTables(null)}>
                Change details
              </Button>
            </>
          ) : (
            <Button type="submit" disabled={!hydrated || pending !== null}>
              {pending === "test" ? "Testing…" : "Test connection"}
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}

/** Start a sync of a saved connection; the password is asked for every time. */
export function SyncForm({ businessId, connection }: { businessId: string; connection: SourceConnection }) {
  const router = useRouter();
  const hydrated = useHydrated();
  const passwordRef = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<FormError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const input = passwordRef.current;
    const password = input?.value ?? "";
    if (input) input.value = "";
    setPending(true);
    setError(null);
    setNotice(null);
    try {
      const result = await postJson<SourceSyncResult>(
        `/api/v1/businesses/${businessId}/sources/${connection.id}/sync`,
        { password },
      );
      if (result) {
        if (result.status === "unchanged") setNotice("No changes since the last sync — opening your current data.");
        router.push(`/businesses/${businessId}/datasets/${result.dataset_id}`);
        return;
      }
      setPending(false);
    } catch (caught) {
      setError(errorFrom(caught, "The sync could not start. Try again."));
      setPending(false);
    }
  }

  const id = `sync-pw-${connection.id}`;
  return (
    <form className="grid gap-2" onSubmit={onSubmit} noValidate aria-label={`Sync ${connection.database}`}>
      <Label htmlFor={id}>Password</Label>
      <div className="flex flex-wrap gap-2">
        <Input id={id} ref={passwordRef} type="password" autoComplete="new-password" required className="max-w-xs" />
        <Button type="submit" disabled={!hydrated || pending}>
          {pending ? "Syncing…" : "Sync now"}
        </Button>
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      {notice ? <p role="status" className="text-[13px]">{notice}</p> : null}
    </form>
  );
}
