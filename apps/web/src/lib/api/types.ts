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

/** A piece of server-rendered text: plain text, an evidence value, or an entity label. */
export type Segment =
  | { text: string }
  | { value: string; evidence_id: string; field: string }
  | { entity: string; kind: string };

export interface CriterionView {
  key: string;
  description: string;
  polarity: string;
  source: "data" | "human";
  satisfied: boolean | null;
  basis: Segment[][] | unknown[];
}

export interface HypothesisView {
  id: string;
  template: string;
  claim: string;
  source: "catalog" | "llm_prioritized" | "human_context";
  status: "pending" | "supported" | "refuted" | "inconclusive" | "needs_human_context";
  confidence: string;
  data_confidence: string | null;
  human_confidence: string | null;
  interpretation: "intended" | "unintended" | null;
  conflicts: string[];
  criteria: CriterionView[];
}

export interface Person {
  id: string;
  display_name: string | null;
}

export interface HumanContext {
  human_input_id: string;
  clarification_id: string;
  criterion: string;
  question: string | null;
  option_id: string | null;
  option_label: string | null;
  effect: "confirm" | "deny" | "intended" | "unsure" | "unstructured";
  /** Untrusted text typed by a person: shown as plain text only. */
  free_text: string | null;
  answered_by: Person;
  answered_at: string;
}

export interface Recommendation {
  template: string | null;
  kind: "fix" | "monitor" | "answer";
  text: string;
}

export interface InvestigationIncident {
  id: string;
  rank: number;
  key: string;
  metric: string;
  metric_label: string;
  dimension: string;
  value: string | null;
  severity: string;
  investigated: boolean;
  observations: Segment[][];
  evidence: { id: string; kind: string; requested_by: string }[];
  root_cause_hypothesis_id: string | null;
  explained_by_context: string[];
  recommendations: Recommendation[];
  human_context: HumanContext[];
  hypotheses: HypothesisView[];
  data_gaps: { template: string; criterion: string; reason: string }[];
}

export interface Clarification {
  id: string;
  incident_id: string;
  incident_key: string;
  criterion: string;
  trigger: string;
  status: "open" | "awaiting" | "answered" | "applied";
  question: string;
  claim: string | null;
  why: string | null;
  options: { id: string; label: string }[];
  response_schema: Record<string, unknown> | null;
  checkpoint_id: string | null;
  asked_at: string | null;
  answered_at: string | null;
  applied_at: string | null;
  answer: {
    human_input_id: string;
    option_id: string | null;
    option_label: string | null;
    effect: string;
    free_text: string | null;
    answered_by: Person;
    answered_at: string;
  } | null;
}

export type InvestigationStatus =
  | "queued"
  | "running"
  | "awaiting_human"
  | "completed"
  | "completed_no_incidents"
  | "insufficient_data"
  | "failed";

export interface InvestigationSummary {
  id: string;
  dataset_id: string;
  revision_id: string;
  status: InvestigationStatus;
  phase: string | null;
  llm_mode: "llm" | "deterministic";
  llm_disabled_reason: string | null;
  error_code: string | null;
  graph_version: string;
  catalog_version: string;
  last_seq: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface Findings {
  id: string;
  version: number;
  source: "llm" | "deterministic";
  label: string;
  rendered: {
    summary: Segment[];
    root_causes: { hypothesis_id: string; statement: Segment[]; evidence_refs: string[] }[];
    explained_by_context?: { hypothesis_id: string; incident_id: string; statement: Segment[] }[];
    conflicts?: { hypothesis_id: string; incident_id: string; statement: Segment[] }[];
    alternatives_considered: { hypothesis_id: string; why_not: Segment[]; evidence_refs: string[] }[];
    caveats: Segment[][];
    recommendations?: (Recommendation & { incident_id: string })[];
  };
  created_at: string;
}

export interface InvestigationDetail extends InvestigationSummary {
  version: number | null;
  awaiting: Clarification | null;
  currency: string;
  restrictions: Restriction[];
  incidents: InvestigationIncident[];
  findings: Findings | null;
}

export interface VersionSummary {
  version: number;
  snapshot_hash: string;
  caused_by: {
    human_input_id: string;
    criterion: string;
    option_id: string | null;
    effect: string;
    answered_by: string;
    answered_at: string;
  } | null;
  created_at: string;
}

type Change<T> = { from: T; to: T };
type AddedRemoved = { added: string[]; removed: string[] };

export interface VersionComparison {
  from_version: number;
  to_version: number;
  caused_by: VersionSummary["caused_by"];
  measurements_preserved: boolean;
  incidents: {
    key: string;
    added?: boolean;
    removed?: boolean;
    measurements_preserved?: boolean;
    changed_evidence?: string[];
    new_evidence?: string[];
    impact?: string;
    root_cause?: Change<string | null> | null;
    explained_by_context?: AddedRemoved;
    hypotheses?: {
      template: string;
      added?: boolean;
      now?: Record<string, unknown>;
      changes?: Record<string, Change<unknown>>;
    }[];
    recommendations?: AddedRemoved;
    human_context_added?: { criterion: string; option_id: string | null; effect: string }[];
  }[];
  findings: {
    summary: Change<string> | null;
    root_causes: AddedRemoved;
    explained_by_context: AddedRemoved;
    conflicts: AddedRemoved;
    caveats: AddedRemoved;
  };
}
