# SQLite storage operation

The canonical candles.db uses SQLite WAL journal mode. SQLite verified the transition against a fresh backup with quick_check=ok and identical data pages. Default synchronous durability remains FULL. See results/candle-wal-transition.json.

Use a local filesystem supporting SQLite locks and shared memory. Do not copy a running database by copying only its main file. Use the supported backup procedure / SQLite backup API, or stop Kernel and scheduled observers before copying the complete installation. Never manually remove WAL or SHM files.

The recovery generation pre-candle-wal-20261021T140406 contains a verified candle database before the transition. It is a component snapshot, not a substitute for a consistent full-system restore. Retain the entire recovery set until separate owner authorization.

Evidence files cited here are installation-local and not committed; verify a database with `sqlite3 <db> 'PRAGMA quick_check;'`.
