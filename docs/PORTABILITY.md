# Installation and portability

Copy the complete `LUFFY` directory, including its independent `.git`, `venv`,
`data`, `.env`, `private`, application source, frontend assets, models and evidence.
Preserve file modes and symlinks. Do not copy a running SQLite installation:
use `./luffy stop` and confirm that database writers and observers have stopped,
or use a separately verified SQLite snapshot procedure. Never remove WAL files
manually. Stop the old installation before allowing the copy to access the same
trading account. A successful file copy is not runtime portability verification.

The supported initial target is Ubuntu 24.04 x86_64 with CPython 3.12 and
compatible system shared libraries. The copied virtual environment contains
application packages; its Python interpreter uses the host's `/usr/bin/python3`.
Use `./venv/bin/python -m ...`, not old absolute entry-point shebangs or an old
`venv/bin/activate` path. Git, Bash, util-linux and a working systemd user manager
are required by startup and independent monitoring. Browser-based optional
collection additionally needs its compatible Playwright browser and OS libraries.
Consult the captured dependency/package manifests for the verified host versions.

Set installation-specific values in the private `.env` file:

```dotenv
LUFFY_DASHBOARD_HOST=127.0.0.1
LUFFY_OWNER_IPC_DIR=private/owner
LUFFY_RECOVERY_ROOT=/path/on/separate/recovery/filesystem
```

Choose the Dashboard address explicitly for the target host. Recovery must remain
on a separate writable filesystem with at least the existing minimum free space;
the launcher rejects a same-filesystem path. That mount is an OS prerequisite,
not a scattered application dependency. Existing values remain the default when
an override is absent. The endpoint and API credentials are unchanged by these
path settings. Supply credentials securely and preserve existing owner keys at
`private/owner/keys`; never put secrets in Git or a public manifest. Private owner
directories must be mode 0700 and key files mode 0600; their ancestors must not
be group/other writable. Keep `.env` private as well.

Commands from any current working directory:

```bash
/path/to/LUFFY/luffy verify           # imports, path closure, stores, prerequisites
/path/to/LUFFY/luffy verify --sqlite  # adds full SQLite quick_check; can be slow
/path/to/LUFFY/luffy start-kernel     # existing contained startup, exact Git revision
/path/to/LUFFY/luffy status
/path/to/LUFFY/luffy start-dashboard  # only after Kernel readiness passes
/path/to/LUFFY/luffy stop             # authenticated freeze, then graceful stop
```

Stop requires a working LIVE Dashboard owner-control route while the Kernel is
running; an unconfirmed freeze refuses shutdown instead of silently leaving
persisted ACTIVE intent. There is no local control-state fallback. Startup
deliberately retains the supported recovery fence. Resume through the
authenticated Dashboard only after fresh Supervisor checks pass; copying a
directory must not grant trading authority. Shutdown keeps the independent
observers available. Before moving files, record and stop the installation's
specific bootstrap timers listed in `data/bootstrap/<token>/observers.json`.
The next supported startup creates new timers for the new absolute location.
Do not reuse old unit definitions on another machine.

Launcher output resides in `logs/kernel-launch.log` and
`logs/dashboard-launch.log`; application temporary files use `tmp/` inside the
installation, including when the caller has a machine-specific `TMPDIR`.
Historical provenance strings and immutable research
records keep their original identities. They are not rewritten to disguise old
paths. The portability verification report must separately establish whether
those historical paths have any active consumer.

`verify` is a local prerequisite check. Final portability acceptance additionally
requires starting Kernel and Dashboard with the old application paths unavailable,
observing a completed cycle and fresh protection evidence, testing authenticated
Dashboard queries, and verifying graceful shutdown and state continuity. Record
any unavailable OS isolation or live-data limitation explicitly.
