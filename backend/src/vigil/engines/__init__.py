"""Research engines. Each engine is a pure function
``analyse(snapshot, settings) -> EngineResult`` — no I/O, no clock, no
randomness. See ``vigil.engines.base`` for shared helpers.
"""

from vigil.engines.base import ENGINE_NAMES, run_all_engines

__all__ = ["ENGINE_NAMES", "run_all_engines"]
