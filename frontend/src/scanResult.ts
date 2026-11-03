/** Server metadata is authoritative. Reason parsing is only for older payloads. */
export interface ScanClassification {
  schema: "scan-result-classification.v1";
  category: string;
  label: string;
  description: string;
  tone: "positive" | "neutral" | "warning";
  show_metrics: boolean;
  reason_codes: string[];
}

export interface ScanResultInput {
  action: string;
  score: number | null;
  executed: boolean;
  evaluated?: boolean;
  skip_reason?: string | null;
  strategy_checks?: { strategy: string; reasons: string[] }[];
  classification?: ScanClassification;
}

const schema = "scan-result-classification.v1";
const ineligible = new Set(["ineligible_regime", "ineligible_state", "ineligible_market", "ineligible_symbol", "NO_ELIGIBLE_STRATEGY"]);
function reasonCategory(reason: string): string {
  if (ineligible.has(reason)) return "not_applicable";
  if (reason === "emitted_signal") return "signal";
  if (reason === "returned_none") return "no_signal";
  if (reason === "INPUT_UNAVAILABLE" || /^(required_input_missing:|missing:|no_snapshot:)/.test(reason)) return "inputs_unavailable";
  if (/^(pre_filtered|prefiltered)(:|$)/.test(reason) || reason === "pre-filtered") return "prefiltered";
  if (["blocked", "execution_recovery_pending", "entries not allowed"].includes(reason) || /^(blocked:|state=)/.test(reason)) return "blocked";
  return "checks_incomplete";
}

export function scanClassification(c: ScanResultInput): ScanClassification {
  const canonical = c.classification;
  if (canonical?.schema === schema && typeof canonical.category === "string" &&
      typeof canonical.label === "string" && canonical.label.trim() && typeof canonical.show_metrics === "boolean") {
    return canonical;
  }
  const codes: string[] = [];
  let malformed = false;
  if (c.strategy_checks != null) {
    if (!Array.isArray(c.strategy_checks)) malformed = true;
    else for (const check of c.strategy_checks) {
      if (!Array.isArray(check?.reasons) || !check.reasons.length) { malformed = true; continue; }
      for (const reason of check.reasons) {
        if (typeof reason === "string" && reason) codes.push(reason);
        else malformed = true;
      }
    }
  }
  const kinds = new Set(codes.map(reasonCategory));
  const skip = typeof c.skip_reason === "string" ? c.skip_reason : "";
  const action = typeof c.action === "string" && c.action ? c.action : "Unknown";
  const scored = typeof c.score === "number" && Number.isFinite(c.score) && c.score !== 0;
  const evaluated = c.evaluated !== false && action !== "NOT_EVALUATED";
  let metrics = evaluated && (action !== "HOLD" || scored);
  const result = (category: string, label: string, tone: ScanClassification["tone"] = "neutral"): ScanClassification => ({
    schema, category, label, tone, show_metrics: metrics,
    description: "Legacy scan telemetry; server classification was not supplied.",
    reason_codes: [...new Set([...(skip ? [skip] : []), ...codes])],
  });
  if (c.executed === true) return result("order_sent", "Order sent", "positive");
  const skipKind = skip ? reasonCategory(skip) : null;
  if (skipKind === "not_applicable") { metrics = false; return result("not_applicable", "Not applicable"); }
  if (skipKind === "blocked" || skipKind === "prefiltered") {
    metrics = metrics && skipKind !== "prefiltered";
    return result(skipKind, skipKind === "blocked" ? "Blocked" : "Pre-filtered", "warning");
  }
  if (!evaluated) { metrics = false; return result("inputs_unavailable", "Inputs unavailable", "warning"); }
  if (action === "BUY" || action === "SELL" || scored) return result(action === "BUY" || action === "SELL" ? "signal" : "decision", action);
  metrics = false;
  if (malformed || kinds.has("checks_incomplete") || kinds.has("inputs_unavailable")) return result("checks_incomplete", "Checks incomplete", "warning");
  if (!kinds.size) return result("checks_unavailable", "Checks unavailable", "warning");
  if (kinds.size === 1 && kinds.has("not_applicable")) return result("not_applicable", "Not applicable");
  if ([...kinds].every(kind => kind === "no_signal" || kind === "not_applicable")) return result("no_signal", "No signal");
  if (kinds.has("blocked") || kinds.has("prefiltered")) {
    if (kinds.size === 1) {
      const kind = [...kinds][0];
      return result(kind, kind === "blocked" ? "Blocked" : "Pre-filtered", "warning");
    }
    return result("checks_incomplete", "Checks incomplete", "warning");
  }
  return result("signal", "Signal emitted");
}

export function scanResult(c: ScanResultInput): string {
  return scanClassification(c).label;
}
