import { useOwnerTelemetry } from "../useOwnerTelemetry";
import HealthStatus from "../components/HealthStatus";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { usePreview } from "../context";
import {
  Badge,
  Panel,
  QueryState,
  Freshness,
  EvidenceButton,
} from "../components/ui";
import {
  RecordTable,
  PagingBar,
  SourceStrip,
  Unavailable,
  Timeline,
  RecordDrawer,
  value,
} from "../components/workspace";
import { RecordLink } from "../components/records";
export function Decisions({ compact = false }: { compact?: boolean }) {
  const { adapter } = usePreview();
  const [offset, setOffset] = useState(0);
  const q = useQuery({
    queryKey: ["decisions", offset],
    queryFn: ({ signal }) => adapter.decisions!(signal, offset),
    enabled: !!adapter.decisions,
    refetchInterval: 30000,
  });
  if (!adapter.decisions)
    return (
      <Unavailable title="Decisions">
        Decision records are not exposed by this adapter.
      </Unavailable>
    );
  return (
    <Panel
      className="workspace-panel"
      title={compact ? "Recent decisions / rejections" : "Decision journal"}
      aside={<Badge>Recorded activity</Badge>}
    >
      <QueryState
        error={q.error}
        loading={q.isPending}
        retry={() => void q.refetch()}
      />
      {q.data && !q.error && (
        <>
          <SourceStrip
            source={q.data.source}
            at={q.data.generatedAt}
            note="Read time is not event freshness. Rejected decisions retain their recorded reason."
          />
          {compact ? (
            <p className="paging-bar">
              Showing latest {Math.min(q.data.rows.length, 5)} decisions from
              the returned window.{" "}
              <a href="#trades">Browse decision history ↗</a>
            </p>
          ) : (
            <PagingBar
              count={Math.min(q.data.rows.length, 100)}
              limit={100}
              offset={offset}
              hasMore={q.data.paging?.hasMore}
              onPage={q.data.paging ? setOffset : undefined}
              busy={q.isFetching}
            />
          )}
          <RecordTable
            key={offset}
            title="Decisions"
            rows={q.data.rows.slice(0, compact ? 5 : 100)}
            filterKey="executed"
            columns={
              compact
                ? [
                    ["symbol", "Instrument"],
                    ["action", "Decision"],
                    ["skip_reason", "Reason"],
                    ["ts", "Recorded at"],
                  ]
                : [
                    ["symbol", "Instrument"],
                    ["action", "Decision"],
                    ["executed", "Executed flag"],
                    ["skip_reason", "Rejection / skip reason"],
                    ["ts", "Recorded at"],
                  ]
            }
            detail={(r) => (
              <>
                <Timeline events={[{ label: "Decision recorded", at: r.ts }]} />
                {typeof r.id === "string" && adapter.ownerRead && (
                  <p>
                    <RecordLink kind="decision" id={r.id}>
                      Open the decision record (cycle, votes, trades, outcome) ↗
                    </RecordLink>
                  </p>
                )}
              </>
            )}
          />
        </>
      )}
    </Panel>
  );
}
export function RuntimeStatus({ compact = false }: { compact?: boolean }) {
  const { adapter, scenario } = usePreview();
  const q = useQuery({
    queryKey: ["graph", "system", scenario],
    queryFn: ({ signal }) => adapter.graph("system", scenario, signal),
    refetchInterval: 30000,
  });
  const runtime = useOwnerTelemetry(
    q.data ?? {
      nodes: [],
      edges: [],
      events: [],
      provenance: {
        id: "missing",
        source: "system",
        observedAt: null,
        freshness: "unavailable",
        summary: "",
        classification: "unavailable",
      },
    },
    true,
  );
  const nodes = runtime.nodes.filter(
    (n) =>
      !compact ||
      ["kernel", "risk", "supervisor", "research", "orchestrator"].includes(
        n.id,
      ),
  );
  return (
    <Panel
      className="workspace-panel"
      title={compact ? "Operating status" : "Current runtime evidence"}
      aside={<a href="#live-system">Topology ↗</a>}
    >
      <QueryState
        error={q.error}
        loading={q.isPending}
        retry={() => void q.refetch()}
      />
      {q.data && !q.error && (
        <>
          <SourceStrip
            source={q.data.provenance.source}
            at={q.data.provenance.observedAt}
            note="Activity records do not establish component health."
          />
          <div className="runtime-cards">
            {nodes?.map((n) => (
              <article key={n.id}>
                <div className="panel-heading">
                  <h3>{n.label}</h3>
                  <HealthStatus node={n} />
                </div>
                <p>{n.evidence.summary}</p>
                <Freshness value={n.evidence} />
                <EvidenceButton value={n.evidence} />
              </article>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}

export function Investigations() {
  const { adapter } = usePreview();
  const q = useQuery({
    queryKey: ["investigations"],
    queryFn: ({ signal }) => adapter.investigations!(signal),
    enabled: !!adapter.investigations,
    refetchInterval: 30000,
  });
  return (
    <Panel
      className="workspace-panel"
      title="Investigations"
      aside={
        <Badge tone="amber">
          {q.error ? "UNAVAILABLE" : (q.data?.status ?? "Unavailable")}
        </Badge>
      }
    >
      <QueryState
        error={q.error}
        loading={!!adapter.investigations && q.isPending}
        retry={() => void q.refetch()}
      />
      {q.data && !q.error && (
        <>
          <p className="quiet">
            Source: /api/investigations/latest · bounded to 16 dossiers.
            Recorded investigations do not establish current scanning activity.
          </p>
          <RecordDrawer row={q.data.health} title="investigation health" />
          {q.data.cases.length === 0 ? (
            <p>No dossiers returned. Source status: {q.data.status}.</p>
          ) : (
            <div className="runtime-cards">
              {q.data.cases.map((c, i) => {
                const r = (
                  typeof c.investigation === "object" &&
                  c.investigation !== null
                    ? c.investigation
                    : {}
                ) as Record<string, unknown>;
                return (
                  <article key={i}>
                    <h3>{value(r.id ?? r.symbol)}</h3>
                    <p>{value(r.status ?? r.state)}</p>
                    <RecordDrawer row={c} title={value(r.id ?? "dossier")} />
                  </article>
                );
              })}
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
export default Decisions;
