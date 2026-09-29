# V2.1 correction evidence

Final: 59 Playwright, 35 component/adapter and 24 affected backend tests passed. Production build and typecheck passed. No deployment or production runtime contact.

Desktop screenshots: all nine routes. Mobile screenshots: all nine routes, plus Knowledge path detail. `accessibility.json` has zero violations for all 18 route/viewport combinations. Screenshots are actual rendered LIVE-adapter pages against an isolated FastAPI server with a temporary journal/vault and fake Owner Interface/LLM; they are not production observations.

- `browser-results.json`, `playwright.log`: final full suite.
- `component-tests.txt`, `backend-tests.txt`, `typecheck.txt`: affected checks.
- `build-audit.json`: emitted assets, gzip sizes, forbidden label and sourcemap checks.
- `performance-before-v2.json`: preserved pre-pass V2 measurement; historical comparison only.
- `performance-live.json`: final local browser performance, heap/churn and test-server RSS.
- `knowledge-cache-performance.json`: paired uncached/cold/warm local worktree vault reads, excluding HTTP transport.
- `changed-files.txt`, `source-sha256.json`, `delta.patch`: exact V2.1 source/test/config changes relative to captured dirty V2 state.
- `baseline-sha256.json`: pre-pass inventory; not a Git revision.
- `artifact-files.txt`: exact new report/evidence artifact paths.
- `evidence-preservation.json`: old evidence paths restored byte-for-byte after legacy suites overwrote them.
- `prior-suite-output/`: this run's legacy-suite captures and synthetic stress measurements; separate from the final LIVE screenshots here.

Reproduce from the worktree:

```sh
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend test
/home/sarmad/trader/venv/bin/python -m pytest tests/test_owner_frontend_api.py tests/test_owner_read_cache.py -q
OWNER_EVIDENCE_DIR=evidence/v2.1 LUFFY_PYTHON=/home/sarmad/trader/venv/bin/python PW_RESULTS=evidence/v2.1/browser-results.json npm --prefix frontend run test:browser
```

Some older browser suites still write historical `evidence/` paths. Review in a disposable copy, or preserve those files before rerunning. The existing V2/foundation evidence was restored after this pass.

See `docs/superpowers/reports/2026-09-29-owner-frontend-v2.1.md` for A–O, remaining backend parity gaps and the **NO route-switch** recommendation.
