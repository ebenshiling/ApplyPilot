"""Request-local reuse for display calculations; never a persistent authority cache."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import hashlib
import inspect
import json
from pathlib import Path

_snapshot = ContextVar("display_read_snapshot", default=None)


@contextmanager
def read_scope(app_dir):
    root = str(Path(app_dir).resolve())
    active = _snapshot.get()
    if active is not None and active[0] == root:
        yield
        return
    token = _snapshot.set((root, {}))
    try:
        yield
    finally:
        _snapshot.reset(token)


def display_read(function):
    """Open a bounded input snapshot around one read-model calculation."""
    signature = inspect.signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        values = signature.bind(*args, **kwargs).arguments
        with read_scope(values["app_dir"]):
            return function(*args, **kwargs)
    return wrapped


def reuse_read(function):
    """Reuse pure reads only while a display_read/read_scope is active.

    Outside that scope the original function always executes. Mutable results
    are copied so preview overrides cannot alter another caller's inputs.
    """
    signature = inspect.signature(function)
    name = (function.__module__, function.__qualname__)

    @wraps(function)
    def wrapped(*args, **kwargs):
        scope = _snapshot.get()
        if scope is None:
            return function(*args, **kwargs)
        values = signature.bind(*args, **kwargs)
        values.apply_defaults()
        root = values.arguments.get("app_dir")
        if root is None:
            from applypilot.ai_settings import current_workspace
            from applypilot.config import APP_DIR
            root = current_workspace() or APP_DIR
        if str(Path(root).resolve()) != scope[0]:
            return function(*args, **kwargs)
        from applypilot.country_context import current
        context = current()
        country_key = (context.workspace, context.country_code, context.revision,
                       hashlib.sha256((context.config_json + context.preferences_json).encode()).digest()) if context else None
        inputs = json.dumps(values.arguments, sort_keys=True, default=str)
        key = (name, country_key, inputs)
        cache = scope[1]
        if key not in cache:
            cache[key] = function(*args, **kwargs)
        return deepcopy(cache[key])
    return wrapped
