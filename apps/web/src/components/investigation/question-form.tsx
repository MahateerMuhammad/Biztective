"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { postJson } from "@/lib/api/client";
import { ApiProblem, type Clarification } from "@/lib/api/types";
import { useHydrated } from "@/lib/use-hydrated";

const FREE_TEXT_MAX = 1000;
/** The question changed under the person (answered elsewhere, or the investigation moved on). */
const RELOAD_CODES = new Set(["stale_checkpoint", "already_answered", "invalid_state"]);

function newKey(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

export function QuestionForm({
  businessId,
  investigationId,
  clarification,
}: {
  businessId: string;
  investigationId: string;
  clarification: Clarification;
}) {
  const hydrated = useHydrated();
  const router = useRouter();
  const [option, setOption] = useState<string>("");
  const [freeText, setFreeText] = useState("");
  // One key per distinct answer: a retried submission is replayed, a changed one is new.
  const [key, setKey] = useState(newKey);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<{ title: string; detail: string } | null>(null);
  const empty = option === "" && freeText.trim() === "";

  function change(update: () => void) {
    update();
    setKey(newKey());
    setError(null);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (empty || clarification.checkpoint_id === null) return;
    setSubmitting(true);
    setError(null);
    const body: Record<string, string> = { checkpoint_id: clarification.checkpoint_id };
    if (option) body.option_id = option;
    if (freeText.trim()) body.free_text = freeText;
    try {
      await postJson(
        `/api/v1/businesses/${businessId}/investigations/${investigationId}/clarifications/${clarification.id}/answer`,
        body,
        key,
      );
      router.refresh();
    } catch (caught) {
      if (caught instanceof ApiProblem && RELOAD_CODES.has(caught.code)) {
        setError({ title: "This question has changed", detail: `${caught.message} Showing the latest state.` });
        router.refresh();
      } else {
        setError({
          title: "Your answer was not saved",
          detail: caught instanceof ApiProblem ? caught.message : "Check your connection and try again.",
        });
      }
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="grid gap-4" aria-describedby={`why-${clarification.id}`}>
      <fieldset className="grid gap-2">
        <legend className="mb-2 font-medium">{clarification.question}</legend>
        <div role="radiogroup" aria-label="Answer options" className="grid gap-2">
          {clarification.options.map((o) => {
            const id = `answer-${clarification.id}-${o.id}`;
            return (
              <div key={o.id} className="flex items-center gap-2">
                <input
                  id={id}
                  type="radio"
                  name={`answer-${clarification.id}`}
                  value={o.id}
                  checked={option === o.id}
                  onChange={() => change(() => setOption(o.id))}
                  className="size-4"
                />
                <Label htmlFor={id} className="font-normal">
                  {o.label}
                </Label>
              </div>
            );
          })}
        </div>
      </fieldset>
      <div className="grid gap-1">
        <Label htmlFor={`text-${clarification.id}`}>Anything else Biztective should know? (optional)</Label>
        <textarea
          id={`text-${clarification.id}`}
          className="min-h-20 rounded-md border bg-background p-2 text-sm"
          maxLength={FREE_TEXT_MAX}
          value={freeText}
          onChange={(event) => {
            const value = event.target.value;
            change(() => setFreeText(value));
          }}
          aria-describedby={`text-help-${clarification.id}`}
        />
        <p id={`text-help-${clarification.id}`} className="text-[13px] text-muted-foreground">
          {freeText.length}/{FREE_TEXT_MAX}. Your words are stored as written, attributed to you
          and shown to your team. Biztective never treats them as instructions or as approval of
          anything.
        </p>
      </div>
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertTitle>{error.title}</AlertTitle>
          <AlertDescription>{error.detail}</AlertDescription>
        </Alert>
      ) : null}
      <div>
        <Button type="submit" disabled={!hydrated || empty || submitting}>
          {submitting ? "Sending…" : "Send answer"}
        </Button>
      </div>
    </form>
  );
}
