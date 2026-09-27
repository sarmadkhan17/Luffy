import { useId } from "react";
import { motion, MotionConfig } from "motion/react";
import { useVisible, useMotionPreference } from "./ui";
export type RequestState =
  "idle" | "pending" | "responding" | "error" | "cancelled";
export default function Avatar({ state }: { state: RequestState }) {
  const reduced = useMotionPreference(),
    visible = useVisible(),
    id = useId().replaceAll(":", "");
  const active =
    visible && !reduced && (state === "pending" || state === "responding");
  return (
    <MotionConfig reducedMotion="user">
      <div
        className={`mechanical-avatar ${state}`}
        data-state={state}
        data-animating={active}
        role="img"
        aria-label={`LUFFY mechanical core: ${state}`}
      >
        <svg viewBox="0 0 320 320" aria-hidden="true">
          <defs>
            <linearGradient id={`${id}-metal`} x1="0" y1="0" x2="1" y2="1">
              <stop stopColor="#d0b780" />
              <stop offset=".25" stopColor="#34464e" />
              <stop offset=".5" stopColor="#a9b6b8" />
              <stop offset=".75" stopColor="#24363d" />
              <stop offset="1" stopColor="#b59865" />
            </linearGradient>
            <radialGradient id={`${id}-light`}>
              <stop stopColor={state === "error" ? "#ffb3aa" : "#fbe2ac"} />
              <stop offset=".2" stopColor="#aa8550" />
              <stop offset=".55" stopColor="#223c44" />
              <stop offset="1" stopColor="#0b151e" />
            </radialGradient>
          </defs>
          <circle
            cx="160"
            cy="160"
            r="153"
            fill="none"
            stroke="#46606a"
            strokeDasharray="1 8"
          />
          <path
            d="M160 1V22M160 298V319M1 160H22M298 160H319"
            stroke="#c4a86e"
          />
          <circle
            cx="160"
            cy="160"
            r="138"
            fill="#101d26"
            stroke={`url(#${id}-metal)`}
            strokeWidth="13"
          />
          <motion.g
            style={{ transformOrigin: "160px 160px" }}
            animate={{ transform: active ? "rotate(360deg)" : "rotate(0deg)" }}
            transition={
              active
                ? {
                    duration: state === "pending" ? 16 : 10,
                    repeat: Infinity,
                    ease: "linear",
                  }
                : { duration: 0 }
            }
          >
            {Array.from({ length: 36 }, (_, i) => (
              <path
                key={i}
                d="M155 24H165V36H155Z"
                transform={`rotate(${i * 10} 160 160)`}
                fill={i % 3 === 0 ? "#b69b68" : "#577078"}
                stroke="#0d1a23"
              />
            ))}
            <circle
              cx="160"
              cy="160"
              r="114"
              fill="none"
              stroke={`url(#${id}-metal)`}
              strokeWidth="7"
              strokeDasharray="80 12 18 12"
            />
            {Array.from({ length: 12 }, (_, i) => (
              <g key={i} transform={`rotate(${i * 30} 160 160)`}>
                <rect
                  x="153"
                  y="37"
                  width="14"
                  height="13"
                  rx="3"
                  fill="#233842"
                  stroke="#879897"
                />
                <path d="M156 43H164" stroke="#e0c895" />
              </g>
            ))}
          </motion.g>
          <circle
            cx="160"
            cy="160"
            r="97"
            fill="#0b1620"
            stroke="#67808a"
            strokeWidth="2"
          />
          <circle
            cx="160"
            cy="160"
            r="84"
            fill="none"
            stroke="#b59865"
            strokeWidth="5"
            strokeDasharray="116 16"
          />
          <circle
            cx="160"
            cy="160"
            r="65"
            fill={`url(#${id}-light)`}
            stroke="#70868b"
            strokeWidth="9"
          />
          {Array.from({ length: 8 }, (_, i) => (
            <path
              key={i}
              d="M160 103 183 135 172 151"
              transform={`rotate(${i * 45} 160 160)`}
              fill="none"
              stroke="#c8aa76"
              strokeWidth="2"
            />
          ))}
          <circle
            cx="160"
            cy="160"
            r="23"
            fill={`url(#${id}-light)`}
            stroke="#dfc18b"
            strokeWidth="3"
          />
          <circle
            cx="160"
            cy="160"
            r="10"
            fill={state === "error" ? "#ee9b93" : "#f9deb0"}
          />
          <path
            d="M45 160H125M195 160H275M160 45V87M160 233V275"
            stroke="#b3dcd5"
            strokeOpacity=".6"
          />
        </svg>
        <span className="core-caption">
          {state === "idle" ? "READY FOR YOUR MESSAGE" : state.toUpperCase()}
        </span>
      </div>
    </MotionConfig>
  );
}
