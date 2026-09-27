import { lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Shield, Radio, Wallet } from "lucide-react";
import { usePreview } from "../context";
import {
  Badge,
  Panel,
  Freshness,
  QueryState,
  EvidenceButton,
  VisualBoundary,
} from "../components/ui";
const EquityChart = lazy(() => import("../components/EquityChart"));
const money = (n: number | null) =>
  n === null
    ? "UNAVAILABLE"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      }).format(n);
export default function Overview() {
  const { adapter, scenario } = usePreview();
  const q = useQuery({
    queryKey: ["overview", scenario],
    queryFn: ({ signal }) => adapter.overview(scenario, signal),
  });
  const d = q.data;
  return (
    <>
      <QueryState
        error={q.error}
        loading={q.isPending}
        retry={() => void q.refetch()}
      />
      {d && (
        <>
          <div className="snapshot-line">
            <Freshness value={d.provenance} />
            <span>All amounts are synthetic USD equivalents</span>
          </div>
          <div className="stats">
            <Panel title="Account equity" aside={<Wallet size={17} />}>
              <div className="stat-value">{money(d.equity)}</div>
              <p>Fixture account · No venue connection</p>
            </Panel>
            <Panel title="Period P&L" aside={<ArrowUpRight size={17} />}>
              <div className="stat-value mint">{money(d.pnl)}</div>
              <p>Sample period · 29 Aug–27 Sep 2026</p>
            </Panel>
            <Panel title="Gross exposure" aside={<Radio size={17} />}>
              <div className="stat-value">
                {d.exposure === null ? "UNAVAILABLE" : `${d.exposure}%`}
              </div>
              <p>Illustrative allocation · Not a risk limit</p>
            </Panel>
            <Panel title="Control state" aside={<Shield size={17} />}>
              <div className="stat-control">{d.control}</div>
              <p>Trading controls are unavailable</p>
            </Panel>
          </div>
          <div className="overview-grid">
            <Panel
              title="Account trajectory"
              aside={<Badge>30 fixture points</Badge>}
            >
              <div className="chart-summary">
                <strong>{money(d.equity)}</strong>
                <span className="quiet">Synthetic series · USD equivalent</span>
              </div>
              {d.points.length ? (
                <VisualBoundary>
                  <Suspense
                    fallback={
                      <div className="chart loading">Loading chart…</div>
                    }
                  >
                    <EquityChart points={d.points} />
                  </Suspense>
                </VisualBoundary>
              ) : (
                <div className="empty chart">Equity history unavailable</div>
              )}
              <details className="chart-data">
                <summary>Accessible chart data</summary>
                <div className="table-scroll">
                  <table>
                    <caption>Synthetic daily equity</caption>
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>USD equivalent</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.points.map((p) => (
                        <tr key={p.time}>
                          <td>{p.time}</td>
                          <td>{money(p.value)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </Panel>
            <div className="overview-side">
              <Panel title="Needs You" aside={<Badge>Preview</Badge>}>
                <div className="callout-symbol">
                  <Shield size={23} />
                </div>
                <h3>{d.needsYou ?? "Approval feed not connected"}</h3>
                <p>
                  No approval records are supplied. This does not establish that
                  no owner action is needed.
                </p>
                <a className="text-link" href="#luffy">
                  Open LUFFY <ArrowUpRight size={15} />
                </a>
              </Panel>
              <Panel title="System visibility">
                <div className="status-row">
                  <span>Data source</span>
                  <Badge>Synthetic</Badge>
                </div>
                <div className="status-row">
                  <span>Protection verification</span>
                  <Badge tone="amber">Unavailable</Badge>
                </div>
                <div className="status-row">
                  <span>Production health</span>
                  <Badge>Unknown</Badge>
                </div>
                <a className="text-link" href="#live-system">
                  Inspect topology <ArrowUpRight size={15} />
                </a>
              </Panel>
            </div>
          </div>
          <div className="bottom-grid">
            <Panel title="Positions" aside={<Badge>Fixture snapshot</Badge>}>
              {d.positions === null ? (
                <div className="empty">Position data unavailable</div>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Instrument</th>
                        <th>Side</th>
                        <th>Notional</th>
                        <th>Unrealized P&L</th>
                        <th>Protection</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.positions.map((p) => (
                        <tr key={p.symbol}>
                          <td className="strong">{p.symbol}</td>
                          <td>{p.side}</td>
                          <td>{money(p.notional)}</td>
                          <td className={p.pnl >= 0 ? "mint" : "rose"}>
                            {money(p.pnl)}
                          </td>
                          <td>
                            <Badge tone="amber">{p.protection}</Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
            <Panel title="Evidence note">
              <p>Research context stays separate from permission to trade.</p>
              <Badge tone="amber">INCONCLUSIVE</Badge>
              <div className="space-top">
                <EvidenceButton value={d.provenance} />
              </div>
            </Panel>
          </div>
        </>
      )}
    </>
  );
}
