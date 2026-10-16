import type { components } from "./schema";

type Schemas = components["schemas"];

export type Me = Schemas["MeResponse"];
export type User = Schemas["UserOut"];
export type Business = Schemas["BusinessOut"];
export type Dataset = Schemas["DatasetOut"];
export type DatasetPage = Schemas["Page_DatasetOut_"];
export type DatasetDetail = Schemas["DatasetDetail"];
export type DatasetFile = Schemas["DatasetFileOut"];
export type IssuePage = Schemas["Page_IssueOut_"];
export type Issue = Schemas["IssueOut"];
export type IssueSummary = Schemas["IssueSummaryOut"];

/** Column mapping stored per file (services/api app/ingestion/mapping.py). */
export interface ColumnMapping {
  source: string | null;
  status: "exact" | "proposed" | "ambiguous" | "unmapped" | "confirmed";
  candidates: string[];
  required: boolean;
}
export interface FileMapping {
  columns: Record<string, ColumnMapping>;
  ignored: string[];
  pii_dropped: string[];
  needs_confirmation: boolean;
}

/** RFC 9457 problem details returned by the API for every error. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  request_id: string | null;
  errors?: { field: string; code: string; message: string }[];
}

export class ApiProblem extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;
  /** Field-level explanations, e.g. "orders.quantity: required column not found". */
  readonly details: string[];

  constructor(problem: Problem, retryAfterSeconds: number | null = null) {
    super(problem.detail);
    this.name = "ApiProblem";
    this.status = problem.status;
    this.code = problem.code;
    this.requestId = problem.request_id;
    this.retryAfterSeconds = retryAfterSeconds;
    this.details = (problem.errors ?? []).map((e) =>
      e.field ? `${e.field}: ${e.message}` : e.message,
    );
  }
}

export async function toApiProblem(response: Response): Promise<ApiProblem> {
  const retryAfter = Number(response.headers.get("Retry-After"));
  const retryAfterSeconds = Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null;
  try {
    const body = (await response.json()) as Partial<Problem>;
    if (typeof body.code === "string" && typeof body.detail === "string") {
      return new ApiProblem(
        {
          type: body.type ?? "about:blank",
          title: body.title ?? response.statusText,
          status: response.status,
          detail: body.detail,
          code: body.code,
          request_id: body.request_id ?? null,
          errors: Array.isArray(body.errors) ? body.errors : undefined,
        },
        retryAfterSeconds,
      );
    }
  } catch {
    // Non-JSON error body (e.g. a proxy failure): fall through to a generic problem.
  }
  return new ApiProblem(
    {
      type: "about:blank",
      title: response.statusText || "Error",
      status: response.status,
      detail: "The request could not be completed.",
      code: "unexpected_response",
      request_id: response.headers.get("X-Request-ID"),
    },
    retryAfterSeconds,
  );
}
