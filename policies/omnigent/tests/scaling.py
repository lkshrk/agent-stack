import time
from contextlib import contextmanager

import agent_stack_policies._shell as shell

CEILING = 0.15
SIZE_RATIO = 8
GROWTH_LIMIT = 1.5 * SIZE_RATIO


@contextmanager
def lifted_limits():
    saved = shell.STATEMENT_LIMIT, shell.WORD_LIMIT, shell.CHARACTER_LIMIT
    shell.STATEMENT_LIMIT = shell.WORD_LIMIT = shell.CHARACTER_LIMIT = 10**9
    try:
        yield
    finally:
        shell.STATEMENT_LIMIT, shell.WORD_LIMIT, shell.CHARACTER_LIMIT = saved


def elapsed(call):
    shell._walk_cached.cache_clear()
    started = time.perf_counter()
    call()
    return time.perf_counter() - started


def cpu(call):
    """CPU seconds of `call`; unlike wall time, unaffected by other load or a CPU quota."""
    shell._walk_cached.cache_clear()
    started = time.process_time()
    call()
    return time.process_time() - started


def growth(measure, size, runs=3):
    """Best-of-`runs` `measure(size * SIZE_RATIO)` over `measure(size)`, with the walk limits lifted; `measure(n)` returns CPU seconds."""
    with lifted_limits():
        small = min(measure(size) for _ in range(runs))
        large = min(measure(size * SIZE_RATIO) for _ in range(runs))
    return large / small
