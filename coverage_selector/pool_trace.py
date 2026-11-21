"""Complement native execution traces with worker dependencies and pickle boundaries."""

import concurrent.futures
import pickle


def run_in_collector(pool, fn, /, *args, **kwargs):
    f = concurrent.futures.Future()
    try:
        if pool._initializer and not getattr(pool, "_br_initialized", False):
            pool._initializer(*pool._initargs)
            pool._br_initialized = True
        call = pickle.loads(pickle.dumps((fn, args, kwargs)))
        result = call[0](*call[1], **call[2])
        f.set_result(pickle.loads(pickle.dumps(result)))
    except Exception as e:
        f.set_exception(e)
    return f


def install():
    concurrent.futures.ProcessPoolExecutor.submit = run_in_collector
