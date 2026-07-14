import { useEffect, useMemo, useState } from "react";
import { scienceCore } from "@/lib/scienceCore";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export interface SourcePdfBlobState {
  url: string | null;
  loading: boolean;
  error: string | null;
}

/** Loads a PDF with Bearer authentication and owns the resulting object URL. */
export function useSourcePdfBlob(
  sourceId: string | null,
  pageIndex: number,
): SourcePdfBlobState {
  const [baseUrl, setBaseUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setBaseUrl(null);
    setError(null);
    if (!sourceId) {
      setLoading(false);
      return () => controller.abort();
    }

    setLoading(true);
    void scienceCore
      .fetchSourceBlob(sourceId, { signal: controller.signal })
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setBaseUrl(objectUrl);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [sourceId]);

  const url = useMemo(
    () => (baseUrl ? `${baseUrl}#page=${Math.max(0, pageIndex) + 1}` : null),
    [baseUrl, pageIndex],
  );

  return { url, loading, error };
}
