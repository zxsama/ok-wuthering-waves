"""Import-only compatibility for the browser-backed Linux runtime.

The project MouseResetTask imports win32api at module load time but exits before
using it for browser devices.  Native cursor operations remain unsupported and
fail loudly if a non-browser path reaches this shim.
"""


def _unsupported(*_args, **_kwargs):
    raise RuntimeError("native Windows cursor APIs are unavailable in Docker")


GetCursorPos = _unsupported
SetCursorPos = _unsupported
