import { ScienceCoreClient } from "@spark/research-sdk";

const configuredBaseUrl = import.meta.env.VITE_SCIENCE_CORE_URL?.trim();
const configuredToken = import.meta.env.VITE_SCIENCE_CORE_TOKEN?.trim();

/** Shared typed boundary to the local science-core process. */
export const scienceCore = new ScienceCoreClient({
  baseUrl: configuredBaseUrl,
  token: configuredToken,
});
