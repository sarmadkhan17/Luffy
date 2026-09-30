"""Thin owner-channel adapters: authenticate, parse, map to OwnerRequest, render.

No adapter may import the state machine, Supervisor, Risk, Executor, the
exchange factory or trading credentials (tests/test_owner_interface_boundary.py).
"""
