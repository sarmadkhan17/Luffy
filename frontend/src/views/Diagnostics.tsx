/** Diagnostics — approved "Final · 9" design: one verdict, then every part
 * checked. Everything is read from the diagnostics, overview and activity
 * records; a check the records do not report is shown as "not reported", never
 * as a pass. The design's invented tile values are replaced by real ones. */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Clock, Cpu, Database, FileText, HardDrive, HeartPulse, Lock, Radar, RefreshCw, ScanEye, ShieldCheck, Stethoscope, TriangleAlert, Wrench, Gauge, Check, X, CircleHelp, type LucideIcon } from "lucide-react";
import { usePreview } from "../context";
import { useRead, rows as asRows, rec } from "../components/records";
import { Card, CardHead, DetailPanel, KV } from "../components/glass";
import { PageHeader } from "../components/PageHeader";
import { timestamp as utc } from "../time";

const G = "#3ddc97", A = "#f2b44a", R = "#ff7b72", GREY = "#8d9fb2";
type Lvl = "ok" | "warn" | "bad" | "none";
const COL: Record<Lvl, string> = { ok: G, warn: A, bad: R, none: GREY };
const STL: Record<Lvl, string> = { ok: "Healthy", warn: "Needs attention", bad: "Failing", none: "Not reported" };
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const stamp = (iso?: string | null) => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(+d) ? `${String(d.getUTCDate()).padStart(2, "0")} ${MON[d.getUTCMonth()]} ${d.toISOString().slice(11, 16)} UTC` : "UNAVAILABLE"; };
const age = (s: unknown) => (typeof s !== "number" ? "unknown age" : s < 120 ? `${Math.round(s)} s` : s < 7200 ? `${Math.round(s / 60)} min` : s < 172800 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`);
const gb = (b: unknown) => (typeof b === "number" ? `${(b / 1e9).toFixed(1)} GB` : "unavailable");
const human = (s: string) => s.replaceAll("_", " ").replaceAll(":", " · ");

interface Tile { id: string; l: string; g: string; icon: LucideIcon; lvl: Lvl; v: string; st: string; x: string; what: string; mean: string; facts: [string, string | null][]; log?: string[]; logTitle?: string; dots?: { t: string; lvl: Lvl }[]; pct?: number }

export default function Diagnostics() {
  const { adapter, session } = usePreview();
  const live = adapter.mode === "LIVE";
  const dq = useRead(live ? "diagnostics" : null, 60000);
  const oq = useRead(live ? "operations/log" : null, 120000);
  const ov = useQuery({ queryKey: ["overview", "normal"], queryFn: ({ signal }) => adapter.overview("normal", signal), staleTime: 10000, refetchInterval: 30000, refetchIntervalInBackground: false, enabled: live });
  const logQ = useQuery({ queryKey: ["logs"], queryFn: ({ signal }) => adapter.logs!(signal), enabled: live && !!adapter.logs, refetchInterval: 60000, refetchIntervalInBackground: false });
  const [filter, setFilter] = useState<"all" | "prob" | "ok">("all");
  const [sel, setSel] = useState<string | null>(null);

  const D = (dq.data ?? {}) as Record<string, any>;
  const L = ov.data?.live;
  const ev = (L?.observedEvidence ?? {}) as Record<string, any>;
  const sup = ev.protection?.supervisor as Record<string, any> | undefined;
  const hb = ev.heartbeat as Record<string, any> | null | undefined;
  const control = ov.data?.control ?? "UNAVAILABLE";
  const positions = ov.data?.positions ?? null;
  const incidents = asRows(D.incidents) as Record<string, any>[];
  const events = asRows(oq.data?.events) as Record<string, any>[];
  const supEvents = incidents.filter((e) => e.event === "supervisor_recovery");
  const recEvents = incidents.filter((e) => e.event === "reconcile");
  const reasons: string[] = Array.isArray(sup?.reasons) ? sup!.reasons : [];
  const setBy = incidents.find((e) => e.to_state && e.to_state === control);
  const probes = asRows(D.probes) as Record<string, any>[];
  const collectors = asRows(D.collectors) as Record<string, any>[];
  const files = asRows(D.storage?.files) as Record<string, any>[];
  const disk = D.storage?.disk as Record<string, number> | undefined;
  const host = D.resources?.host as Record<string, any> | undefined;
  const wd = D.watchdog as Record<string, any> | undefined;
  const lvlOf = (b: boolean | null | undefined): Lvl => (b === true ? "ok" : b === false ? "bad" : "none");

  const tiles: Tile[] = useMemo(() => {
    if (!dq.data && !ov.data) return [];
    const t: Tile[] = [];
    const hbFresh = L?.heartbeat?.freshness;
    t.push({ id: "heart", l: "Heartbeat", g: "Is LUFFY alive and ticking?", icon: HeartPulse, lvl: !L?.heartbeat ? "none" : hbFresh === "fresh" ? "ok" : "bad", v: !L?.heartbeat ? "—" : hbFresh === "fresh" ? "Fresh" : "Stale", st: !L?.heartbeat ? "Not reported" : hbFresh === "fresh" ? "Healthy" : "Failing",
      x: hb ? `Last heartbeat ${stamp(hb.observed_at)} (${age(hb.age_s)} ago; stale after ${age(hb.stale_after_s)}).` : "No readable kernel heartbeat.",
      what: "A small signal the kernel writes each cycle. If it stops arriving on time, the Supervisor assumes something is wrong and keeps trading frozen.",
      mean: hbFresh === "fresh" ? "The kernel is reporting on time." : "Trading cannot resume while the heartbeat is stale. The process state reads " + String(hb?.process_state ?? "unknown").toLowerCase() + ".",
      facts: [["Freshness", hbFresh ?? null], ["Process", hb?.process_state ?? null], ["Reported control state", hb?.reported_state ?? null], ["Last successful work", hb?.work_observed_at ? stamp(hb.work_observed_at) : null], ["Source", hb?.source ?? null]] });
    const exceeded = reasons.some((r) => /THRESHOLD_EXCEEDED|cycle/i.test(r));
    t.push({ id: "cycle", l: "Cycle freshness", g: "How recently a full scan succeeded", icon: Gauge, lvl: !hb ? "none" : exceeded ? "warn" : hbFresh === "fresh" ? "ok" : "warn", v: hb ? age(hb.work_age_s) : "—", st: exceeded ? "Too old" : hb ? (hbFresh === "fresh" ? "Healthy" : "Stale") : "Not reported",
      x: exceeded ? "The Supervisor reports the successful-cycle threshold exceeded." : "Age of the last successful cycle recorded in the heartbeat.",
      what: "One cycle = scan the coins, let the analysts vote, decide, and journal it. A cycle that has not succeeded recently means LUFFY is not reacting.",
      mean: "Per-cycle durations are not recorded, so only the age of the last successful cycle is shown.",
      facts: [["Last successful cycle", hb?.work_observed_at ? stamp(hb.work_observed_at) : null], ["Age", hb ? age(hb.work_age_s) : null], ["Supervisor flag", exceeded ? "threshold exceeded" : "none"]] });
    const supLvl: Lvl = !sup ? "none" : sup.outcome === "NEEDS_OWNER" ? "bad" : sup.outcome === "SAFE" && sup.freshness === "fresh" ? "ok" : "warn";
    t.push({ id: "sup", l: "Supervisor", g: "The safety officer", icon: ScanEye, lvl: supLvl, v: sup ? String(sup.outcome ?? "?") : "—", st: sup ? (sup.freshness === "fresh" ? "Current" : "Last pass stale") : "Not reported",
      x: sup ? `Last pass ${stamp(sup.observed_at)} (${age(sup.age_s)} ago) · stage ${sup.stage ?? "?"}.` : "No supervisor record is readable.",
      what: "The Supervisor watches LUFFY and decides whether trading is safe. It can freeze trading on its own, and lift a freeze it set itself.",
      mean: sup?.needs_owner ? "It is asking for you." : "It is not asking for the owner. " + (control === "FROZEN" || control === "HALTED" ? "A freeze it did not set can only be lifted by you." : ""),
      facts: [["Outcome", sup?.outcome ?? null], ["Stage", sup?.stage ?? null], ["Needs owner", sup ? String(sup.needs_owner) : null], ["Passes in the recent events", String(supEvents.length)], ["Reasons", reasons.length ? reasons.map(human).join("; ") : "none recorded"]],
      log: supEvents.slice(0, 8).map((e) => `${stamp(e.ts)}  ${e.detail?.outcome ?? "?"}  ${(e.detail?.reasons ?? []).map(human).join("; ")}`), logTitle: "Latest Supervisor passes" });
    t.push({ id: "gate", l: "Trading gate", g: "Can LUFFY open new trades?", icon: Lock, lvl: control === "ACTIVE" ? "ok" : control === "FROZEN" ? "warn" : control === "HALTED" ? "bad" : "none", v: control === "ACTIVE" ? "Open" : control === "UNAVAILABLE" ? "—" : "Closed", st: control === "ACTIVE" ? "Active" : control === "UNAVAILABLE" ? "Not reported" : control[0] + control.slice(1).toLowerCase(),
      x: control === "FROZEN" ? "New entries are blocked. Existing positions keep being managed where the kernel runs." : control === "ACTIVE" ? "New entries are permitted." : `Control state ${control}.`,
      what: "The final switch before any new order. When closed, LUFFY may still think and log decisions, but never opens a position.",
      mean: "It opens when you resume LUFFY (Trades → Resume); the Supervisor must also judge it safe.",
      facts: [["State", control], ["Last control change", setBy ? `${stamp(setBy.ts)} (${setBy.actor})` : "not in the recent events"], ["Supervisor says safe to activate", sup ? "see Supervisor" : null]] });
    const prot = L?.protection?.status ?? "UNAVAILABLE";
    t.push({ id: "stops", l: "Positions & stops", g: "Is the money protected?", icon: ShieldCheck, lvl: prot === "VERIFIED" || prot === "NO_POSITIONS" ? "ok" : prot === "UNPROTECTED" ? "bad" : "warn", v: positions ? String(positions.length) : "—", st: prot === "VERIFIED" ? "Protected" : prot === "UNPROTECTED" ? "Unprotected" : "Not verified",
      x: `${positions?.length ?? "?"} open in the journal; venue protection is ${prot.toLowerCase()}. Journal stops are records, not venue proof.`,
      what: "Checks that every open position on the exchange has a protective stop and matches what LUFFY wrote in its journal.",
      mean: prot === "VERIFIED" ? "Nothing to do." : "No current venue verification exists, so protection cannot be confirmed from here.",
      facts: [...(positions ?? []).map((p): [string, string | null] => [`${p.symbol.split("/")[0]} ${p.side}`, p.journalStop?.price ? `journal stop ${p.journalStop.price}` : "no stop recorded"]), ["Venue protection", prot]] });
    const rLast = recEvents[0];
    t.push({ id: "recon", l: "Reconcile", g: "Exchange vs journal", icon: RefreshCw, lvl: rLast ? "ok" : "none", v: String(recEvents.length), st: rLast ? "Ran" : "Not reported",
      x: rLast ? `${recEvents.length} reconcile run(s) in the recent control events; latest ${stamp(rLast.ts)}.` : "No reconcile run in the recent control events.",
      what: "LUFFY compares what the exchange says against its own records, so nothing drifts out of sync.",
      mean: "A run is recorded here; whether it found drift is in each run's detail, which is not summarised.",
      facts: [["Runs in recent events", String(recEvents.length)], ["Latest", rLast ? stamp(rLast.ts) : null]], dots: recEvents.slice(0, 8).map((e) => ({ t: stamp(e.ts).slice(0, 12), lvl: "ok" as Lvl })),
      log: recEvents.slice(0, 8).map((e) => `${stamp(e.ts)}  ${e.actor}`), logTitle: "Reconcile runs" });
    const rec2 = reasons.some((r) => /recovery/i.test(r));
    t.push({ id: "recov", l: "Recovery mode", g: "Storage / heartbeat recovery", icon: Wrench, lvl: rec2 ? "warn" : sup ? "ok" : "none", v: rec2 ? "On" : sup ? "Off" : "—", st: rec2 ? "Active" : sup ? "Normal" : "Not reported",
      x: rec2 ? "The Supervisor's last pass reports storage/heartbeat recovery." : "No recovery reason in the last Supervisor pass.",
      what: "A careful mode LUFFY enters after a problem with its storage or heartbeat: it double-checks state before doing anything.", mean: "It switches off by itself once the heartbeat is healthy again.",
      facts: [["Reason recorded", rec2 ? reasons.filter((r) => /recovery/i.test(r)).map(human).join("; ") : "none"]] });
    const pOk = probes.filter((p) => p.ok).length;
    t.push({ id: "probe", l: "Store probes", g: "Can LUFFY read its own data?", icon: Database, lvl: probes.length ? (pOk === probes.length ? "ok" : "bad") : "none", v: probes.length ? `${pOk}/${probes.length}` : "—", st: probes.length ? (pOk === probes.length ? "OK" : "Failing") : "Not reported",
      x: "Journal, newest decision, knowledge vault and dashboard build, each timed.", what: "Quick test reads against LUFFY's databases and files, timed in milliseconds.", mean: "A failing probe means a store the owner views depend on cannot be read.",
      dots: probes.map((p) => ({ t: String(p.probe).replaceAll("_", " "), lvl: (p.ok ? "ok" : "bad") as Lvl })), facts: probes.map((p): [string, string] => [String(p.probe), `${p.ok ? "ok" : "FAILED"} · ${p.ms} ms · ${p.detail}`]) });
    const cOk = collectors.filter((c) => c.freshness === "fresh" && c.status === "ok").length;
    t.push({ id: "coll", l: "Collectors", g: "Background data feeds", icon: Radar, lvl: !collectors.length ? "none" : cOk === collectors.length ? "ok" : collectors.some((c) => c.status === "error") ? "bad" : "warn", v: collectors.length ? `${cOk}/${collectors.length}` : "—", st: !collectors.length ? "Not reported" : cOk === collectors.length ? "Fresh" : "Stale",
      x: "Attention, investigation and learning feeds, from their *_health.json files.", what: "Small workers that keep gathering market attention, investigations and learning data in the background.", mean: "Stale or error means that feed has not reported recently; with the kernel stopped that is expected.",
      dots: collectors.map((c) => ({ t: String(c.file).replace("_health.json", ""), lvl: (c.freshness === "fresh" && c.status === "ok" ? "ok" : c.status === "error" ? "bad" : "warn") as Lvl })),
      facts: collectors.map((c): [string, string] => [String(c.file).replace("_health.json", ""), `${c.status} · ${c.freshness} · ${age(c.age_s)} old${c.last_error ? ` · ${c.last_error}` : ""}`]) });
    const wdOff = wd?.disabled_flag === true;
    t.push({ id: "wd", l: "Watchdog", g: "Restarts LUFFY if it dies", icon: Activity, lvl: !wd ? "none" : wdOff ? "warn" : "ok", v: !wd ? "—" : wdOff ? "Off" : "On", st: !wd ? "Not reported" : wdOff ? "Disabled by flag" : "Watching",
      x: wdOff ? `A disable flag (data/watchdog.off) is present${wd?.disabled_since ? ` since ${stamp(wd.disabled_since)}` : ""}.` : "No disable flag; the watchdog may restart stopped processes.",
      what: "An outside guard that restarts LUFFY's process if it crashes or goes stale.", mean: wdOff ? "While the flag exists nothing restarts the kernel automatically. The flag is created before an intentional stop and must be removed when restoring service." : "Nothing to do.",
      facts: [["Disable flag", wd ? (wdOff ? "present" : "absent") : null], ["Flag since", wd?.disabled_since ? stamp(wd.disabled_since) : null]], log: asRows(wd?.log_tail).length ? undefined : (Array.isArray(wd?.log_tail) ? (wd!.log_tail as string[]).slice(-12).map((s) => String(s).slice(0, 220)) : undefined), logTitle: "Watchdog log" });
    const free = disk ? disk.free / disk.total : null;
    t.push({ id: "disk", l: "Disk & memory", g: "Room on this machine", icon: HardDrive, lvl: free === null ? "none" : free < 0.1 ? "bad" : free < 0.2 ? "warn" : "ok", v: free === null ? "—" : `${Math.round(free * 100)}% free`, st: free === null ? "Not reported" : free < 0.2 ? "Low" : "Free", pct: free === null ? undefined : 1 - free,
      x: disk ? `${gb(disk.free)} free of ${gb(disk.total)}; memory available ${gb(host?.mem_available)} of ${gb(host?.mem_total)}.` : "No disk reading.", what: "How much room the machine has. A full disk breaks the journal and triggers recovery mode.", mean: free !== null && free >= 0.2 ? "Plenty of room." : "Free space is getting low.",
      facts: [["Disk free", gb(disk?.free)], ["Disk total", gb(disk?.total)], ["Memory available", gb(host?.mem_available)], ["CPU load (1/5/15 min)", Array.isArray(host?.loadavg) ? host!.loadavg.map((x: number) => x.toFixed(2)).join(" / ") : null], ["CPUs", host?.cpus != null ? String(host.cpus) : null], ["Dashboard memory", gb(D.resources?.dashboard_process?.rss)]] });
    t.push({ id: "store", l: "Storage", g: "LUFFY's database files", icon: Cpu, lvl: files.length ? "ok" : "none", v: String(files.length), st: files.length ? "Files" : "Not reported", x: "Size and last change of each database file in data/.", what: "Size and last-change time of each database file in data/.", mean: "Large files are normal for the journal and market stores.",
      facts: files.map((f): [string, string] => [String(f.file).replace("data/", ""), `${gb(f.bytes)} · changed ${stamp(f.modified_at)}`]) });
    const lines = logQ.data?.lines ?? null;
    t.push({ id: "logs", l: "Logs & build", g: "luffy.log and dashboard version", icon: FileText, lvl: lines ? "ok" : "none", v: lines ? "Readable" : "—", st: lines ? "Readable" : "Not reported", x: "Latest log lines and the running dashboard build.", what: "The raw log LUFFY writes, plus which version of the code and dashboard is running.", mean: "Open this tile for the newest log lines.",
      facts: [["Backend commit", session?.backend.commit ?? "not recorded"], ["Frontend build", session?.backend.frontend_build ?? null], ["Diagnostics read", D.api?.diagnostics_read_ms != null ? `${D.api.diagnostics_read_ms} ms` : null], ["Log last written", logQ.data?.observedAt ? stamp(logQ.data.observedAt) : null]],
      log: lines ? lines.slice(-40).map((s) => s.slice(0, 240)) : undefined, logTitle: "luffy.log (newest lines)" });
    return t;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dq.data, ov.data, oq.data, logQ.data]);

  const problems = tiles.filter((t) => t.lvl === "bad" || t.lvl === "warn");
  const shown = tiles.filter((t) => filter === "all" || (filter === "prob" ? t.lvl === "bad" || t.lvl === "warn" : t.lvl === "ok"));
  const verdict = !sup ? ["Unknown", GREY] as const : sup.outcome === "SAFE" ? ["Healthy", G] as const : sup.outcome === "NEEDS_OWNER" ? ["Needs you", R] as const : ["Degraded", A] as const;
  const headline = control === "FROZEN" ? "LUFFY is frozen and can't open new trades" : control === "HALTED" ? "LUFFY is halted" : control === "ACTIVE" ? (L?.heartbeat?.freshness === "fresh" ? "LUFFY is trading normally" : "LUFFY says ACTIVE but is not reporting") : "LUFFY's state can't be read";
  const checks: { l: string; ok: boolean | null }[] = [
    { l: "Exchange positions readable", ok: sup?.checks_are_current ? sup.venue_positions : null },
    { l: "Positions match the journal", ok: sup?.checks_are_current ? sup.reconciliation : null },
    { l: "Every position has its stop", ok: sup?.checks_are_current ? sup.venue_protection : null },
    { l: "Heartbeat is fresh", ok: L?.heartbeat ? L.heartbeat.freshness === "fresh" : null },
    { l: "Kernel process running", ok: ev.kernel_process?.state ? ev.kernel_process.state === "RUNNING" : L?.kernelState && L.kernelState !== "UNKNOWN" ? L.kernelState === "RUNNING" : null },
    { l: "Safe to open new trades", ok: sup ? supEvents[0]?.detail?.safe_to_activate === true : null },
  ];
  const passN = checks.filter((c) => c.ok === true).length;

  // day timeline: newest day with recorded activity, three lanes
  const day = events.map((e) => String(e.at).slice(0, 10)).sort().pop();
  const dayEv = events.filter((e) => String(e.at).slice(0, 10) === day);
  const xPos = (iso: string) => { const d = new Date(iso); return `${(((d.getUTCHours() * 60 + d.getUTCMinutes()) / 1440) * 100).toFixed(2)}%`; };
  const lanes = [
    { l: "Supervisor", marks: dayEv.filter((e) => e.type === "Safety" && e.facts?.event === "supervisor_recovery").map((e) => ({ at: e.at, c: e.tone === "bad" ? R : e.tone === "warn" ? A : G, t: `${stamp(e.at)} · ${e.result} · ${e.what}` })) },
    { l: "Scan passes", marks: dayEv.filter((e) => e.type === "Scan").map((e) => ({ at: e.at, c: e.tone === "ok" ? G : GREY, t: `${stamp(e.at)} · ${e.what}` })) },
    { l: "Reconcile", marks: dayEv.filter((e) => e.type === "Safety" && e.facts?.event === "reconcile").map((e) => ({ at: e.at, c: G, t: `${stamp(e.at)} · ${e.what}` })) },
  ];
  const scanHours = new Set(dayEv.filter((e) => e.type === "Scan").map((e) => new Date(e.at).getUTCHours()));
  let gap = 0, gapAt = 0, run = 0;
  if (scanHours.size) { const lo = Math.min(...scanHours), hi = Math.max(...scanHours); for (let h = lo; h <= hi; h++) { if (!scanHours.has(h)) { run++; if (run > gap) { gap = run; gapAt = h - run + 1; } } else run = 0; } }
  const scanPasses = dayEv.filter((e) => e.type === "Scan").reduce((a, e) => a + (e.n ?? 1), 0);

  const popTile = tiles.find((t) => t.id === sel) ?? null;
  const open = (id: string) => setSel(id);
  const reasonChips: [string, Lvl, string][] = [
    ...(L?.heartbeat && L.heartbeat.freshness !== "fresh" ? [["Heartbeat stale", "bad", "heart"] as [string, Lvl, string]] : []),
    ...reasons.filter((r) => !/heartbeat/i.test(r)).map((r): [string, Lvl, string] => [human(r), "warn", /cycle/i.test(r) ? "cycle" : /recovery/i.test(r) ? "recov" : "sup"]),
    ...(control === "FROZEN" || control === "HALTED" ? [[`${control[0] + control.slice(1).toLowerCase()} · ${setBy?.actor && setBy.actor !== "supervisor" ? `set by ${setBy.actor}` : "the Supervisor did not set it"}`, "bad", "gate"] as [string, Lvl, string]] : []),
    ...(wd?.disabled_flag ? [["Watchdog disabled by flag", "warn", "wd"] as [string, Lvl, string]] : []),
  ];

  return (
    <>
      <PageHeader title="Diagnostics">
        <span style={{ fontSize: 13, color: "var(--txt2)" }}>is LUFFY healthy? · one verdict, then every part checked</span>
      </PageHeader>
      {(dq.isError || ov.isError) && <div className="gl-err" role="alert">{((dq.error ?? ov.error) as Error).message} Nothing was substituted.</div>}
      {(dq.isPending || ov.isPending) && live && <p className="gl-empty">Running the checks…</p>}
      {!live && <p className="gl-empty">Diagnostics are unavailable in fixture mode.</p>}

      {tiles.length > 0 && (
        <>
          <div className="gl-row" style={{ alignItems: "stretch" }}>
            <Card need className="gl-verdict" label="Verdict">
              <div className="vring" aria-hidden="true" style={{ ["--vc" as string]: verdict[1] }}>
                <span className="vr1" /><span className="vr2" /><span className="vr3" />
                <span className="vcore">{verdict[0] === "Healthy" ? <Check size={34} color="#04140c" strokeWidth={2.4} /> : <TriangleAlert size={34} color="#1a1204" strokeWidth={2.2} />}</span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0, flex: 1 }}>
                <span style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <span className="gl-pill" style={{ background: `${verdict[1]}33`, color: verdict[1], textTransform: "uppercase", fontSize: 11 }}>{verdict[0]}</span>
                  <span className="m" style={{ fontSize: 12, color: "var(--txt2)" }}>{sup ? `Supervisor last checked ${stamp(sup.observed_at)}${sup.freshness === "fresh" ? "" : ` · ${age(sup.age_s)} ago · stale`}` : "no Supervisor record"}</span>
                </span>
                <span className="cg" style={{ fontSize: 28, color: "rgb(var(--hi))", lineHeight: 1.15 }}>{headline}</span>
                <span style={{ fontSize: 14, color: "#b9c5d1", lineHeight: 1.55 }}>
                  {positions ? `${positions.length} open position${positions.length === 1 ? "" : "s"} in the journal; venue protection is ${(L?.protection?.status ?? "UNAVAILABLE").toLowerCase()}. ` : ""}
                  {sup ? `The Supervisor rated LUFFY ${String(sup.outcome).toLowerCase()} at stage ${sup.stage ?? "?"}` : "No Supervisor rating is readable"}
                  {setBy ? `; the ${control.toLowerCase()} state was last changed by ${setBy.actor} on ${stamp(setBy.ts)}.` : "."}
                </span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {reasonChips.map(([l, lv, id]) => (<button key={l} type="button" className="why" onClick={() => open(id)}><i style={{ width: 8, height: 8, borderRadius: "50%", background: COL[lv] }} />{l}</button>))}
                </div>
              </div>
            </Card>
            <Card className="pad gl-checks" label="Safety checklist">
              <CardHead icon={<ShieldCheck size={16} />} title="Safety checklist"><span className="m" style={{ fontSize: 12, color: "var(--txt2)" }}>{passN} of {checks.length} pass</span></CardHead>
              {checks.map((c, i) => (
                <div key={c.l} className="chk gl-st" style={{ animationDelay: `${(i * 0.08).toFixed(2)}s` }}>
                  <span className="mark" style={{ background: c.ok === true ? G : c.ok === false ? R : "#ffffff1f" }}>{c.ok === true ? <Check size={14} color="#04140c" strokeWidth={3} /> : c.ok === false ? <X size={14} color="#2a0604" strokeWidth={3} /> : <CircleHelp size={14} color={GREY} />}</span>
                  <span style={{ fontSize: 14, color: c.ok === false ? "#ffb4ab" : c.ok === null ? GREY : "#d5dee8" }}>{c.l}{c.ok === null ? " — not reported" : ""}</span>
                </div>
              ))}
              <span style={{ fontSize: 12, color: GREY }}>{sup?.checks_are_current ? `From the Supervisor's latest verification · ${stamp(sup.observed_at)}` : "The Supervisor's last verification is stale and recorded no per-check results, so those checks read “not reported”, not failed."}</span>
            </Card>
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
            <div role="group" aria-label="Show" className="gl-seg">
              {([["all", "All", tiles.length], ["prob", "Problems", problems.length], ["ok", "Healthy", tiles.filter((t) => t.lvl === "ok").length]] as const).map(([k, l, n]) => (
                <button key={k} type="button" className="gl-segb" aria-pressed={filter === k} onClick={() => setFilter(k)}>{l} <span className="m">{n}</span></button>
              ))}
            </div>
            <span style={{ flex: 1 }} />
            {(["ok", "warn", "bad", "none"] as Lvl[]).map((lv) => (<span key={lv} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#c9d4df" }}><i style={{ width: 9, height: 9, borderRadius: "50%", background: COL[lv] }} />{STL[lv]}</span>))}
          </div>

          <section className="gl-tiles" aria-label="Checks">
            {shown.map((k) => (
              <button key={k.id} type="button" className="gl-card gl-tile2" style={{ borderColor: `${COL[k.lvl]}99`, ["--tc" as string]: COL[k.lvl] }} onClick={() => open(k.id)}>
                <span style={{ display: "flex", alignItems: "center", gap: 10, width: "100%" }}>
                  <span className="tic" style={{ background: `${COL[k.lvl]}1f`, borderColor: `${COL[k.lvl]}66`, boxShadow: k.lvl === "ok" ? undefined : `0 0 18px ${COL[k.lvl]}55` }}><k.icon size={20} color={COL[k.lvl]} strokeWidth={1.8} aria-hidden="true" /></span>
                  <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}><b style={{ fontSize: 15 }}>{k.l}</b><span style={{ fontSize: 12, color: GREY }}>{k.g}</span></span>
                </span>
                <span style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                  <span className="m" style={{ fontSize: 26, color: COL[k.lvl] }}>{k.v}</span>
                  <span className="gl-pill" style={{ background: `${COL[k.lvl]}24`, color: COL[k.lvl], textTransform: "uppercase", fontSize: 10 }}>{k.st}</span>
                </span>
                <span style={{ fontSize: 13, color: "#b9c5d1", lineHeight: 1.45, textAlign: "left" }}>{k.x}</span>
                {k.pct !== undefined && <span className="gl-meter" style={{ width: "100%" }}><i style={{ width: `${Math.round(k.pct * 100)}%`, background: COL[k.lvl] }} /></span>}
                {k.dots && <span style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>{k.dots.map((d, i) => (<span key={i} title={d.t} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, color: "#c9d4df" }}><i style={{ width: 7, height: 7, borderRadius: "50%", background: COL[d.lvl] }} />{d.t}</span>))}</span>}
              </button>
            ))}
            {shown.length === 0 && <p className="gl-empty">Nothing in this view.</p>}
          </section>

          <Card className="pad" label="Health through the day">
            <CardHead icon={<Clock size={16} />} title="Health through the day" sub={`${day ? stamp(`${day}T00:00:00Z`).slice(0, 6) : "—"} (UTC) · supervisor passes, scans and reconciles · hover a mark`} />
            <div style={{ overflowX: "auto" }}>
              <div style={{ minWidth: 760, display: "flex", flexDirection: "column", gap: 10 }}>
                {lanes.map((ln) => (
                  <div key={ln.l} style={{ display: "grid", gridTemplateColumns: "150px 1fr", gap: 12, alignItems: "center" }}>
                    <span style={{ display: "flex", flexDirection: "column" }}><b style={{ fontSize: 13 }}>{ln.l}</b><span style={{ fontSize: 11, color: GREY }}>{ln.marks.length} recorded</span></span>
                    <div className="gl-lane">{ln.marks.map((m, i) => (<span key={i} className="mk" title={m.t} style={{ left: xPos(m.at), background: m.c }} />))}</div>
                  </div>
                ))}
                <div style={{ display: "grid", gridTemplateColumns: "150px 1fr", gap: 12 }}><span /><div style={{ position: "relative", height: 16 }}>{[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => (<span key={h} className="m" style={{ position: "absolute", left: `${(h / 24) * 100}%`, transform: h === 24 ? "translateX(-100%)" : h === 0 ? "none" : "translateX(-50%)", fontSize: 11, color: GREY }}>{String(h).padStart(2, "0")}:00</span>))}</div></div>
              </div>
            </div>
            <span style={{ fontSize: 13, color: "#b9c5d1" }}>
              {scanHours.size ? <>{scanPasses} scan passes were recorded{gap > 0 ? <>, with no scan in the {gap} hour{gap === 1 ? "" : "s"} starting {String(gapAt).padStart(2, "0")}:00</> : ""}. Scans are bucketed per UTC hour.</> : "No scan passes were recorded on this day."}
            </span>
          </Card>
        </>
      )}

      <DetailPanel open={!!popTile} onClose={() => setSel(null)} title={popTile?.l ?? ""} description={popTile ? `${STL[popTile.lvl]} · ${popTile.g}` : undefined}>
        {popTile && (
          <>
            <span className="gl-pill" style={{ alignSelf: "flex-start", background: `${COL[popTile.lvl]}24`, color: COL[popTile.lvl], textTransform: "uppercase", fontSize: 10 }}>{popTile.st}</span>
            <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>What this is</h3>
            <p style={{ margin: 0, fontSize: 14, lineHeight: 1.55, color: "#c9d4df" }}>{popTile.what}</p>
            <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>What it means for you</h3>
            <p style={{ margin: 0, fontSize: 14, lineHeight: 1.55, color: "#c9d4df" }}>{popTile.mean}</p>
            <KV items={popTile.facts} />
            {popTile.log && popTile.log.length > 0 && (
              <>
                <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>{popTile.logTitle}</h3>
                <div className="m gl-log2">{popTile.log.map((s, i) => (<span key={i}>{s}</span>))}</div>
              </>
            )}
            <p className="gl-note">Read-only evidence. A value shown as not reported is absent from the records, not assumed healthy.</p>
          </>
        )}
      </DetailPanel>
    </>
  );
}
