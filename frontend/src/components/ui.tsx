import { Component, useEffect, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Compass } from "lucide-react";
import type { Provenance } from "../adapters/contracts";
export function Mark() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <Compass size={28} />
    </div>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Panel({
  title,
  children,
  aside,
  className = "",
}: {
  title: string;
  children: ReactNode;
  aside?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <h2>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}
export function Freshness({ value }: { value: Provenance }) {
  return (
    <span className={`freshness ${value.freshness}`}>
      {value.freshness === "fresh"
        ? "Fixed fixture snapshot"
        : value.freshness.toUpperCase()}{" "}
      ·{" "}
      {value.observedAt
        ? new Date(value.observedAt)
            .toISOString()
            .replace("T", " ")
            .replace(".000Z", " UTC")
        : "Source time unavailable"}
    </span>
  );
}
export function EvidenceDetails({ value }: { value: Provenance }) {
  return (
    <div className="evidence-details">
      <Badge tone="amber">Synthetic evidence</Badge>
      <h3>{value.source}</h3>
      <p>{value.summary}</p>
      <dl>
        <dt>Record</dt>
        <dd>{value.id}</dd>
        <dt>Classification</dt>
        <dd>{value.classification}</dd>
        <dt>Source time / freshness</dt>
        <dd>
          <Freshness value={value} />
        </dd>
        <dt>Authority</dt>
        <dd>Context only. No trading or approval authority.</dd>
      </dl>
    </div>
  );
}
export function EvidenceButton({ value }: { value: Provenance }) {
  return (
    <Dialog.Root>
      <Dialog.Trigger className="button secondary">
        Inspect evidence <span aria-hidden="true">↗</span>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="evidence-drawer">
          <div className="panel-heading">
            <Dialog.Title>Evidence inspector</Dialog.Title>
            <Dialog.Close className="icon-button" aria-label="Close evidence">
              <X size={20} />
            </Dialog.Close>
          </div>
          <Dialog.Description>
            Source, freshness and limits for this synthetic record.
          </Dialog.Description>
          <EvidenceDetails value={value} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function QueryState({
  error,
  loading,
  retry,
}: {
  error: Error | null;
  loading: boolean;
  retry: () => void;
}) {
  if (error)
    return (
      <div className="empty error" role="alert">
        <h2>Data unavailable</h2>
        <p>{error.message}</p>
        <button onClick={retry}>Retry fixture request</button>
      </div>
    );
  if (loading)
    return (
      <div className="empty" role="status">
        Loading fixture data…
      </div>
    );
  return null;
}
export function useVisible() {
  const [visible, setVisible] = useState(!document.hidden);
  useEffect(() => {
    const fn = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", fn);
    return () => document.removeEventListener("visibilitychange", fn);
  }, []);
  return visible;
}
export class VisualBoundary extends Component<
  { children: ReactNode; fallback?: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed
      ? (this.props.fallback ?? (
          <div role="alert" className="empty">
            Visualization unavailable. Use the details below.
          </div>
        ))
      : this.props.children;
  }
}

/** Listen live: OS/browser preference changes must stop active motion immediately. */
export function useMotionPreference() {
  const [reduced, setReduced] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}

export function useMedia(query: string) {
  const [matches, setMatches] = useState(
    () => window.matchMedia(query).matches,
  );
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    media.addEventListener("change", update);
    update();
    return () => media.removeEventListener("change", update);
  }, [query]);
  return matches;
}
