import { lazy, type ComponentType } from "react";
/** React.lazy whose load failure is announced and bounded. Assets are served
 * behind the dashboard's auth Guard, so after a session ends an unloaded chunk
 * fails to load; LIVE mode re-checks the session on this event instead of
 * leaving a broken view. The error still reaches the view's boundary.
 *
 * A chunk that neither loads nor fails within CHUNK_TIMEOUT_MS is reported as
 * failed rather than leaving a Suspense fallback on screen indefinitely.
 *
 * Recovery is a page reload. The browser keeps a failed dynamic import in its
 * module map, so a new React.lazy for the same chunk sends no request and fails
 * again; only a fresh document loads the chunk anew. Once any chunk has failed,
 * a boundary's Retry and the next navigation reload the current URL (hash
 * route and query kept). */
export const CHUNK_ERROR = "luffy:chunk-error";
export const CHUNK_TIMEOUT_MS = 20000;

export class ChunkLoadError extends Error {
  constructor(readonly reason: "timeout" | "failed") {
    super(
      reason === "timeout"
        ? `The view's code did not arrive within ${CHUNK_TIMEOUT_MS / 1000}s.`
        : "The view's code could not be loaded.",
    );
    this.name = "ChunkLoadError";
  }
}

function bounded<T>(load: () => Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new ChunkLoadError("timeout")), ms);
    load().then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      () => {
        clearTimeout(timer);
        reject(new ChunkLoadError("failed"));
      },
    );
  });
}

let chunkFailed = false;
/** The reload seam (replaced in unit tests; jsdom cannot reload). */
export const page = { reload: () => window.location.reload() };

/** If a chunk has failed in this document, reload the current URL and return
 * true; otherwise do nothing and return false. */
export function retryFailedChunks(): boolean {
  if (!chunkFailed) return false;
  page.reload();
  return true;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyChunk<T extends ComponentType<any>>(
  load: () => Promise<{ default: T }>,
  timeoutMs = CHUNK_TIMEOUT_MS,
) {
  return lazy(() =>
    bounded(load, timeoutMs).catch((e) => {
      chunkFailed = true;
      window.dispatchEvent(new Event(CHUNK_ERROR));
      throw e;
    }),
  );
}
