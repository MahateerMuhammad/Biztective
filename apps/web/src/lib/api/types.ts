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

/** Data-quality restriction attached to analytics responses (AMD-10). */
export interface Restriction {
  code: string;
  message: string;
  affects: string[];
}

export type MetricValues = Record<string, string | number | null>;

export interface AnalyticsEnvelope<T> {
  dataset_id: string;
  currency: string;
  as_of: string | null;
  period: Record<string, unknown> | null;
  restrictions: Restriction[];
  data: T;
}

export interface ComparisonRow {
  dimension: string;
  value: string | null;
  current: MetricValues;
  previous: MetricValues | null;
  change: Record<string, { abs: string | number | null; rel: string | null }> | null;
}

export interface ProductRow {
  product_id: string;
  product_name: string;
  category: string;
  metrics: MetricValues;
}

export interface SegmentSummary {
  revision_id: string;
  customers: number;
  segments: { segment: string; customers: number; share: string | null; monetary_365: string }[];
  rules: Record<string, string>;
}

export interface EvidenceRef {
  id: string;
  kind: string;
  result_hash: string;
}

export interface IncidentSummary {
  id: string;
  key: string;
  rank: number;
  severity: "critical" | "high" | "medium" | "low";
  status: string;
  metric: string;
  dimension: string;
  value: string | null;
  impact: string;
  affected: Record<string, number>;
  context: Record<string, unknown>;
  current_value: string | null;
  baseline_median: string | null;
  z: number | null;
  onset_week: string | null;
  evidence: EvidenceRef[];
}

export interface AnalysisRevision {
  id: string;
  status: "queued" | "running" | "completed" | "insufficient_data" | "failed";
  as_of: string | null;
  current_window: [string, string] | null;
  baseline_window: [string, string] | null;
  restrictions: Restriction[];
  summary: Record<string, unknown>;
  revision_hash: string | null;
  incidents: IncidentSummary[];
}

export interface IncidentDetail extends Omit<IncidentSummary, "evidence"> {
  stats: Record<string, unknown>;
  evidence: { id: string; kind: string; params: Record<string, unknown>; result: Record<string, unknown>; result_hash: string }[];
}
