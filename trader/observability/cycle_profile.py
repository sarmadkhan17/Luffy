"""Opt-in, one-cycle standard-library profiling; no arguments or payload capture."""
import cProfile
import logging
import os
from pathlib import Path
import tempfile
import time


def profile_cycle(cycle, directory):
    """Keep the callback's result/exception even if saving diagnostics fails."""
    profile = cProfile.Profile()
    try:
        return profile.runcall(cycle)
    finally:
        temporary = None
        try:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.cycle-profile-', delete=False) as f:
                temporary = Path(f.name)
            profile.dump_stats(str(temporary))
            target = directory / f'kernel-cycle-{os.getpid()}-{time.time_ns()}.prof'
            os.replace(temporary, target)
            logging.getLogger(__name__).info('cycle profile saved: %s', target.name)
        except Exception as exc:
            logging.getLogger(__name__).warning('cycle profile save failed: %s', type(exc).__name__)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
