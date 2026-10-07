/** Glacier motion is presentation only. Values update immediately; motion never
 * creates intermediate financial numbers, activity, freshness or authority. */
import { useEffect, useRef, type ReactNode } from "react";
import { LazyMotion, MotionConfig, domAnimation, m, useAnimate } from "motion/react";
import { useMotionPreference } from "./ui";
export const glacierMotion = {
  micro: 0.14, ui: 0.24, data: 0.45, story: 0.65,
  ease: [0.22, 1, 0.36, 1] as const,
};
export function GlacierMotion({ children }: { children: ReactNode }) {
  return <LazyMotion features={domAnimation} strict><MotionConfig reducedMotion="user">{children}</MotionConfig></LazyMotion>;
}
export function RouteReveal({ route, children }: { route: string; children: ReactNode }) {
  const reduced = useMotionPreference();
  return <m.div className="gl-route" data-testid="route-content" data-route={route}
    initial={reduced || route === "tracker" ? false : { opacity: 0.8, y: 4 }}
    animate={{ opacity: 1, y: 0 }} transition={{ duration: reduced || route === "tracker" ? 0 : glacierMotion.ui, ease: glacierMotion.ease }}>
    {children}
  </m.div>;
}
/** Brief emphasis ONLY after a changed, rendered primitive value. */
export function ChangeValue({ value, children }: { value: string | number | null; children?: ReactNode }) {
  const previous = useRef(value);
  const reduced = useMotionPreference();
  const [scope, animate] = useAnimate();
  useEffect(() => {
    const changed = !Object.is(previous.current, value);
    previous.current = value;
    if (!changed || reduced) return;
    const control = animate(scope.current, { backgroundColor: ["rgba(86,204,242,.18)", "rgba(86,204,242,0)"] }, { duration: glacierMotion.data });
    return () => control.stop();
  }, [value, reduced, animate, scope]);
  return <span ref={scope} className="gl-change">{children ?? value}</span>;
}
export function DataBar({ width, className = "", style }: { width: string; className?: string; style?: React.CSSProperties }) {
  const reduced = useMotionPreference();
  return <m.span className={className} style={{ ...style, display: "block" }} initial={false} animate={{ width }} transition={{ duration: reduced ? 0 : glacierMotion.data, ease: glacierMotion.ease }} />;
}
