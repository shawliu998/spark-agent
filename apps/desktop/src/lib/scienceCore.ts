import { ScienceCoreClient } from "@spark/research-sdk";

const configuredBaseUrl = import.meta.env.VITE_SCIENCE_CORE_URL?.trim();

/** Shared typed boundary to the local science-core process. */
export const scienceCore = new ScienceCoreClient(
  configuredBaseUrl ? { baseUrl: configuredBaseUrl } : undefined,
);
