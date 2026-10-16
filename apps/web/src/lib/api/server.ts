import "server-only";

import { cookies } from "next/headers";
import { cache } from "react";

import type { Business, DatasetDetail, DatasetPage, IssuePage, Me } from "./types";
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
