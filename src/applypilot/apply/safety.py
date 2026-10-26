"""Released application execution with an optional operator emergency stop."""

import os

MESSAGE = ("Live application automation is disabled by the service operator "
           "(APPLYPILOT_LIVE_APPLICATIONS_DISABLED); no browser worker or external submission was started.")


class SubmissionContained(ValueError):
    pass


def require_live_execution():
    if os.environ.get("APPLYPILOT_LIVE_APPLICATIONS_DISABLED", "").casefold() in {"1", "true", "yes"}:
        raise SubmissionContained(MESSAGE)


def status():
    try:
        require_live_execution()
    except SubmissionContained:
        return {"live_execution_enabled": False, "reason": MESSAGE, "manual_application_available": True}
    return {"live_execution_enabled": True, "reason": "Application workflows are available; review the packet and approve submission.", "manual_application_available": True}


def contained_browser_action(function):
    """Check the service stop at each existing browser actuator entry point."""
    def contained(*args, **kwargs):
        require_live_execution()
        return function(*args, **kwargs)
    contained.__name__ = function.__name__
    contained.__doc__ = function.__doc__
    return contained
