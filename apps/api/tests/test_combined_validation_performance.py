"""Bound CPU for malformed reserved-token near misses at the response ceiling."""
import multiprocessing
import time

import pytest


def _measure_rejection(connection, raw):
    # Spawn inherits the isolated runner environment; imports never use live roots.
    from app.engine.combined_prefix_validation import validate_auxiliary_cut
    from app.engine.free_body_protocol import FreeBodyError

    connection.send(('ready', 0.0))
    started = time.perf_counter()
    try:
        validate_auxiliary_cut(raw, 'tagged_text')
    except FreeBodyError:
        connection.send(('rejected', time.perf_counter() - started))
    else:
        connection.send(('accepted', time.perf_counter() - started))
    finally:
        connection.close()


@pytest.mark.parametrize('region', ['primary', 'auxiliary', 'semantic'])
def test_near_limit_whitespace_tokens_reject_without_pathological_backtracking(region):
    spaces = ' ' * 99000
    if region == 'primary':
        raw = '<' + spaces + 'x'
    elif region == 'auxiliary':
        raw = '<m c="C1">Fine.</m><panels><' + spaces + 'x'
    else:
        raw = '<m c="C1">Fine.<' + spaces + 'a invalid></m><panels>'
    assert len(raw) < 100000
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    worker = context.Process(target=_measure_rejection, args=(child, raw))
    worker.start()
    child.close()
    try:
        assert parent.poll(10.0), 'isolated parser worker failed to initialize'
        assert parent.recv()[0] == 'ready'
        # Enforce the deadline outside the worker, even if its regex holds the GIL.
        assert parent.poll(4.0), 'bounded malformed response stalled the parser'
        outcome, elapsed = parent.recv()
        assert outcome == 'rejected'
        assert elapsed < 2.0, f'Unexpected parser CPU cost: {elapsed:.3f}s'
        worker.join(5.0)
        assert worker.exitcode == 0
    finally:
        parent.close()
        if worker.is_alive():
            worker.terminate()
            worker.join(5.0)
        if worker.is_alive():
            worker.kill()
            worker.join(5.0)
        worker.close()
