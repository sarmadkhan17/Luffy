import { lazy, type ComponentType } from "react";
/** React.lazy whose load failure is announced. Assets are served behind the
 * dashboard's auth Guard, so after a session ends an unloaded chunk fails to
 * load; LIVE mode re-checks the session on this event instead of leaving a
 * broken view. The error still reaches the view's boundary. */
export const CHUNK_ERROR = "luffy:chunk-error";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyChunk<T extends ComponentType<any>>(
  load: () => Promise<{ default: T }>,
) {
  return lazy(() =>
    load().catch((e) => {
      window.dispatchEvent(new Event(CHUNK_ERROR));
      throw e;
    }),
  );
}
