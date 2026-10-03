import { useEffect, useMemo, useState } from "react";
import type { GraphData } from "../adapters/contracts";
import { evidencePath } from "../views/graphPath";
export default function PathTrace({
  data,
  source,
  onPathChange,
}: {
  data: GraphData;
  source: string;
  onPathChange?: (ids: string[]) => void;
}) {
  const [target, setTarget] = useState("");
  const path = useMemo(
    () => (target ? evidencePath(data, source, target) : null),
    [data, source, target],
  );
  useEffect(() => {
    onPathChange?.(path?.map((edge) => edge.id) ?? []);
  }, [path, onPathChange]);
  const label = (id: string) =>
    data.nodes.find((n) => n.id === id)?.label ?? id;
  return (
    <details className="path-trace">
      <summary>Trace a directed path</summary>
      <label>
        Destination
        <select
          aria-label="Path destination"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
        >
          <option value="">Choose a record</option>
          {data.nodes
            .filter((n) => n.id !== source)
            .map((n) => (
              <option key={n.id} value={n.id}>
                {n.label}
              </option>
            ))}
        </select>
      </label>
      <p className="quiet">
        Uses returned connections only, including records outside the drawn
        scope. A path is not causal or verified evidence.
      </p>
      {target && (
        <div role="status">
          {path === null ? (
            <p>
              No directed path in the returned records. This does not establish
              that none exists in the full knowledge store.
            </p>
          ) : (
            <ol>
              {path.map((e) => (
                <li key={e.id}>
                  <strong>
                    {label(e.source)} → {label(e.target)}
                  </strong>
                  <p>
                    {e.kind} · {e.relation}
                  </p>
                  <small>
                    Connection {e.id}
                    {e.evidenceId
                      ? ` · Evidence ${e.evidenceId}`
                      : " · Evidence identifier unavailable"}
                  </small>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </details>
  );
}
