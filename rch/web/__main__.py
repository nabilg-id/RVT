"""Allow ``python -m rch.web`` to start the GUI directly."""
from .server import run_server

if __name__ == "__main__":
    run_server()
