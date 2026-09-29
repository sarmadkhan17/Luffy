/** Evidence output routing. With EVIDENCE_SINK set, every write a browser
 * suite would make under `evidence/` goes to the sink instead, so a
 * regression run never overwrites historical evidence. Unset: unchanged. */
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";

export function ev(path: string) {
  const sink = process.env.EVIDENCE_SINK;
  const out = sink ? join(sink, path.replace(/^evidence\/?/, "")) : path;
  mkdirSync(dirname(out), { recursive: true });
  return out;
}
