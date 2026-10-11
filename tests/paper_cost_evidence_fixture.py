"""TEST-ONLY frozen authoritative fixtures; never imported by production.

Historical import name. The implementation lives in tests/authority_cost_fixtures.py
(the two files were byte-identical); this module only re-exports it so existing
`from tests.paper_cost_evidence_fixture import ...` call sites keep working.
"""
from tests.authority_cost_fixtures import *  # noqa: F401,F403
