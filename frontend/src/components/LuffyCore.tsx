import { m } from "motion/react";
import { useMotionPreference } from "./ui";
import { glacierMotion } from "./motion";
export const corePresentation: Record<string, [string, string, string]> = {
  idle: ["Ready", "#a0e4fa", "At rest. No provider activity is implied."],
  reading: ["Reading evidence", "#a0e4fa", "A local evidence read is pending; this is separate from external chat."],
  pending: ["Query pending", "#f2b44a", "Waiting for this conversation request. No trading authority."],
  responding: ["Replying", "#3ddc97", "A response is arriving for this request."],
  error: ["No reply", "#ff7b72", "The request failed. Inspect the reported error below."],
  unavailable: ["Unavailable", "#8d9fb2", "Provider availability is not established. No activity is inferred."],
  provider_disabled: ["Provider disabled", "#8d9fb2", "External LLM is disabled. Local evidence inspection remains separate."],
  read_only: ["READ_ONLY", "#a0e4fa", "Evidence inspection only. Conversation, controls and provider actions are disabled."],
};
/** Angular mechanical register, never an assistant face or perpetual idle orb. */
export function LuffyCore({ state }: { state: string }) {
  const reduced = useMotionPreference();
  const [label, color] = corePresentation[state] ?? corePresentation.unavailable;
  const angles: Record<string, number[]> = {
    idle: [0, 30, 0], reading: [45, -30, 45], pending: [90, 0, -45],
    responding: [135, 60, 0], error: [0, 0, 45], unavailable: [0, 0, 0],
    provider_disabled: [0, 0, 0], read_only: [45, 45, 0],
  };
  const locked = ["read_only", "provider_disabled", "unavailable"].includes(state);
  return <svg className="gl-machine-core" viewBox="0 0 240 240" role="img" aria-label={`LUFFY core: ${label}`} data-testid="luffy-core" data-state={state} style={{ color }}>
    <defs><radialGradient id="core-depth"><stop stopColor={color} stopOpacity=".14" /><stop offset="1" stopColor={color} stopOpacity="0" /></radialGradient></defs>
    <circle cx="120" cy="120" r="114" fill="url(#core-depth)" />
    <circle cx="120" cy="120" r="106" fill="none" stroke="currentColor" strokeOpacity=".2" />
    {[78, 61, 42].map((r, i) => <m.g key={r} style={{ originX: 0.5, originY: 0.5 }} initial={false}
      animate={{ rotate: (angles[state] ?? angles.unavailable)[i] }} transition={{ duration: reduced ? 0 : glacierMotion.story, ease: glacierMotion.ease }}>
      <rect x={120-r} y={120-r} width={r*2} height={r*2} rx={i === 1 ? 24 : 4} fill="none" stroke="currentColor" strokeWidth={i === 1 ? 3 : 1} strokeOpacity={locked ? .35 : .65} strokeDasharray={i === 0 ? "28 12" : undefined} />
    </m.g>)}
    <path d={state === "error" ? "M108 108l24 24m0-24l-24 24" : state === "unavailable" ? "M104 110v20h10m12-20h10v20m-22-10h12" : state === "provider_disabled" ? "M105 120a15 15 0 1 0 30 0 15 15 0 1 0-30 0m-24 0h18" : locked ? "M109 118v-8a11 11 0 0 1 22 0v8m-25 0h28v24h-28z" : state === "responding" ? "M106 110h28m-28 10h22m-22 10h16" : state === "reading" ? "M106 105h28v30h-28zM111 114h18m-18 8h13" : state === "pending" ? "M108 107h24l-24 26h24z" : "M108 108h24v24h-24z"} fill="none" stroke="currentColor" strokeWidth="2" />
  </svg>;
}
