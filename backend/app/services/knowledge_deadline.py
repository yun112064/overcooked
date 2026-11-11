"""Local optional knowledge has a bounded budget; it must leave time to save the meal."""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
import math
import threading
import time

LOCAL_CALL_SECONDS = 30.0
KNOWLEDGE_SECTION_SECONDS = 45.0
RESULT_SAVE_RESERVE_SECONDS = 10.0
_job_deadline = ContextVar('knowledge_job_deadline', default=None)
_section_deadline = ContextVar('knowledge_section_deadline', default=None)
_local_slot = threading.BoundedSemaphore(1)


class LocalKnowledgeTimeout(Exception):
    pass


@contextmanager
def job_deadline_scope(deadline):
    token = _job_deadline.set(deadline)
    try:
        yield
    finally:
        _job_deadline.reset(token)


@contextmanager
def optional_knowledge_scope():
    deadline = time.monotonic() + KNOWLEDGE_SECTION_SECONDS
    if _job_deadline.get() is not None:
        deadline = min(deadline, _job_deadline.get() - RESULT_SAVE_RESERVE_SECONDS)
    token = _section_deadline.set(deadline)
    try:
        yield
    finally:
        _section_deadline.reset(token)


def local_timeout(configured):
    value = float(configured)
    if not math.isfinite(value) or value <= 0:
        raise LocalKnowledgeTimeout('LOCAL_KNOWLEDGE_INVALID_TIMEOUT')
    budget = min(value, LOCAL_CALL_SECONDS)
    now = time.monotonic()
    for deadline in (_section_deadline.get(),
                     None if _job_deadline.get() is None else _job_deadline.get() - RESULT_SAVE_RESERVE_SECONDS):
        if deadline is not None:
            budget = min(budget, deadline - now)
    if budget <= 0:
        raise LocalKnowledgeTimeout('LOCAL_KNOWLEDGE_TIME_BUDGET_EXHAUSTED')
    return budget


def bounded_local_call(call, timeout):
    """Bound caller wall time, including a server that keeps trickling response bytes.

    No paid calls or database writes may run here. On timeout late output is discarded.
    The daemon owns/closes its HTTP client. One slot prevents orphaned calls accumulating
    in this worker; remote Ollama cancellation itself is not guaranteed.
    """
    slot = _local_slot
    if not slot.acquire(blocking=False):
        raise LocalKnowledgeTimeout('LOCAL_KNOWLEDGE_BUSY')
    done, result = threading.Event(), {}
    context = copy_context()

    def run():
        try:
            result['value'] = context.run(call)
        except Exception as exc:
            result['error'] = exc
        finally:
            slot.release()
            done.set()

    thread = threading.Thread(target=run, daemon=True, name='optional-local-knowledge')
    try:
        thread.start()
    except Exception:
        slot.release()
        raise
    if not done.wait(timeout):
        raise LocalKnowledgeTimeout('LOCAL_KNOWLEDGE_TIMEOUT')
    if 'error' in result:
        raise result['error']
    return result['value']
