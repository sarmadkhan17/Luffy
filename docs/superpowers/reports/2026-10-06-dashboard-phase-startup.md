# Dashboard phase startup contract

`config.yaml` → `dashboard.startup_mode` is required, with exactly `READ_ONLY_GUI` or `LIVE`. Missing/invalid modes refuse before application attachment or process replacement. No environment override or inferred phase exists.

`READ_ONLY_GUI` is the current architecture/GUI setting. Direct startup and restart use the same phase checker and do not require a Kernel, protection receipt or fresh runtime heartbeat. This grants read access only. The request boundary rejects writes, GraphQL mutations/subscriptions, legacy provider reads and WebSockets before handler execution. Historical GraphQL queries and local evidence reads remain available. The gateway independently refuses IPC requests, quotes/marks are not configured, chat is disabled, and bootstrap advertises no controls. Kernel process evidence remains STOPPED or UNAVAILABLE; missing/stale telemetry is not promoted to live work. The UI labels the phase and terminal NEXT history.

`LIVE` preserves the unchanged OBS-02 reader, including exact verified Kernel identity/revision, instance-bound heartbeat, chronology, successful-work freshness, independent health evidence, boot/control FROZEN and operator hold. It additionally retains existing OBS-01 preflight before startup/replacement. Read-only mode never fabricates those live facts or satisfies a future activation gate. Kernel startup code and trading policy are unchanged.

The frontend Tracker parser now accepts the existing backend CLOSED/BLOCKED terminal literals and checks row-status consistency. Canonical tracker, STATE and NEXT are unchanged.

Validation: 84 backend tests passed (phase, existing readiness and authenticated owner API); 24 frontend tests passed (phase/Tracker, live adapter and stage 8); production frontend build passed. Runtime Dashboard restart and real-browser Tracker verification are performed separately after this commit. No Kernel startup or provider calls are authorized by this change.
