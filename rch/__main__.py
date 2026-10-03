"""Allow ``python -m rch``.

The GUI used to be reachable as ``python -m rch.web``. It is served by
``clipper.app`` now, which covers both pages in one process - use ``rch web`` or
``python -m clipper.app``.
"""
from .cli import main

if __name__ == "__main__":
    main()
