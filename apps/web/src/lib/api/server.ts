import "server-only";

import { cookies } from "next/headers";
import { cache } from "react";

import type {
  ActionDetail,
  ActionSummary,
  AnalysisRevision,
  AnalyticsEnvelope,
  Business,
  ComparisonRow,
  DatasetDetail,
  DatasetPage,
  IncidentDetail,
  Integration,
  InvestigationDetail,
  InvestigationSummary,
  IssuePage,
  Me,
  PlanDetail,
  PlanSummary,
  ProductRow,
  SegmentSummary,
  VersionComparison,
  VersionSummary,
} from "./types";
import { toApiProblem } from "./types";

function backendUrl(): string {
  const value = process.env.BACKEND_URL;
  if (!value) {
    throw new Error("BACKEND_URL must be set");
  }
  return new URL(value).origin;
}

/** GET from the backend with the visitor's cookies; returns null when unauthenticated. */
async function backendGet<T>(path: string): Promise<{ status: number; data: T | null }> {
  const cookieHeader = (await cookies()).toString();
  const response = await fetch(`${backendUrl()}${path}`, {
    headers: cookieHeader ? { Cookie: cookieHeader } : {},
    cache: "no-store",
  });
  if (response.status === 401 || response.status === 404) {
    return { status: response.status, data: null };
  }
  if (!response.ok) {
    throw await toApiProblem(response);
  }
  return { status: response.status, data: (await response.json()) as T };
}

/** The signed-in user and memberships, or null. Deduplicated per request. */
export const getMe = cache(async (): Promise<Me | null> => {
  return (await backendGet<Me>("/api/v1/auth/me")).data;
});

export async function getBusiness(businessId: string): Promise<Business | null> {
  return (await backendGet<Business>(`/api/v1/businesses/${encodeURIComponent(businessId)}`))
    .data;
}

export async function listDatasets(businessId: string): Promise<DatasetPage | null> {
  return (
    await backendGet<DatasetPage>(
      `/api/v1/businesses/${encodeURIComponent(businessId)}/datasets?limit=20`,
    )
  ).data;
}

export async function getDataset(
  businessId: string,
  datasetId: string,
): Promise<DatasetDetail | null> {
  return (
    await backendGet<DatasetDetail>(
      `/api/v1/businesses/${encodeURIComponent(businessId)}/datasets/${encodeURIComponent(datasetId)}`,
    )
  ).data;
}

export async function listIssues(
  businessId: string,
  datasetId: string,
  limit = 50,
): Promise<IssuePage | null> {
  return (
    await backendGet<IssuePage>(
      `/api/v1/businesses/${encodeURIComponent(businessId)}/datasets/${encodeURIComponent(datasetId)}/issues?limit=${limit}`,
    )
  ).data;
}

function datasetPath(businessId: string, datasetId: string): string {
  return `/api/v1/businesses/${encodeURIComponent(businessId)}/datasets/${encodeURIComponent(datasetId)}`;
}

/** Analytics reads: null when not found or not yet available (404/409). */
async function analyticsGet<T>(path: string): Promise<T | null> {
  const cookieHeader = (await cookies()).toString();
  const response = await fetch(`${backendUrl()}${path}`, {
    headers: cookieHeader ? { Cookie: cookieHeader } : {},
    cache: "no-store",
  });
  if (response.status === 404 || response.status === 409 || response.status === 401) {
    return null;
  }
  if (!response.ok) {
    throw await toApiProblem(response);
  }
  return (await response.json()) as T;
}

export function getAnalysis(businessId: string, datasetId: string) {
  return analyticsGet<AnalysisRevision>(`${datasetPath(businessId, datasetId)}/analysis`);
}

export function getComparison(businessId: string, datasetId: string) {
  return analyticsGet<AnalyticsEnvelope<ComparisonRow[]>>(
    `${datasetPath(businessId, datasetId)}/metrics/comparison`,
  );
}

export function getProducts(businessId: string, datasetId: string, limit = 15) {
  return analyticsGet<AnalyticsEnvelope<ProductRow[]>>(
    `${datasetPath(businessId, datasetId)}/products?limit=${limit}`,
  );
}

export function getSegments(businessId: string, datasetId: string) {
  return analyticsGet<AnalyticsEnvelope<SegmentSummary>>(`${datasetPath(businessId, datasetId)}/segments`);
}

export function getIncident(businessId: string, datasetId: string, incidentId: string) {
  return analyticsGet<IncidentDetail>(
    `${datasetPath(businessId, datasetId)}/incidents/${encodeURIComponent(incidentId)}`,
  );
}

function investigationPath(businessId: string, investigationId: string): string {
  return `/api/v1/businesses/${encodeURIComponent(businessId)}/investigations/${encodeURIComponent(investigationId)}`;
}

export async function listInvestigations(
  businessId: string,
  datasetId: string,
): Promise<InvestigationSummary[]> {
  const page = await analyticsGet<{ items: InvestigationSummary[] }>(
    `/api/v1/businesses/${encodeURIComponent(businessId)}/investigations?dataset_id=${encodeURIComponent(datasetId)}`,
  );
  return page?.items ?? [];
}

export function getInvestigation(businessId: string, investigationId: string) {
  return analyticsGet<InvestigationDetail>(investigationPath(businessId, investigationId));
}

export async function listVersions(businessId: string, investigationId: string): Promise<VersionSummary[]> {
  const page = await analyticsGet<{ items: VersionSummary[] }>(
    `${investigationPath(businessId, investigationId)}/versions`,
  );
  return page?.items ?? [];
}

export function compareVersions(businessId: string, investigationId: string, from: number, to: number) {
  return analyticsGet<VersionComparison>(
    `${investigationPath(businessId, investigationId)}/versions/compare?from_version=${from}&to_version=${to}`,
  );
}

export async function listPlans(businessId: string, investigationId: string): Promise<PlanSummary[]> {
  const page = await analyticsGet<{ items: PlanSummary[] }>(
    `${investigationPath(businessId, investigationId)}/recovery-plans`,
  );
  return page?.items ?? [];
}

export function getPlan(businessId: string, planId: string) {
  return analyticsGet<PlanDetail>(
    `/api/v1/businesses/${encodeURIComponent(businessId)}/recovery-plans/${encodeURIComponent(planId)}`,
  );
}

export async function listActions(businessId: string, planId: string): Promise<ActionSummary[]> {
  const page = await analyticsGet<{ items: ActionSummary[] }>(
    `/api/v1/businesses/${encodeURIComponent(businessId)}/recovery-plans/${encodeURIComponent(planId)}/actions`,
  );
  return page?.items ?? [];
}

export function getAction(businessId: string, actionId: string) {
  return analyticsGet<ActionDetail>(
    `/api/v1/businesses/${encodeURIComponent(businessId)}/actions/${encodeURIComponent(actionId)}`,
  );
}

export function listIntegrations(businessId: string) {
  return analyticsGet<{ available: boolean; items: Integration[] }>(
    `/api/v1/businesses/${encodeURIComponent(businessId)}/integrations`,
  );
}
