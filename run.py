"""Development entry point: `python run.py`. Use gunicorn in production (see README).

    python run.py                  # this computer only (http://127.0.0.1:5000)
    python run.py --host 0.0.0.0   # reachable from other devices on your network
    HOST=0.0.0.0 PORT=8000 python run.py

The debugger is turned off when listening beyond this computer: it allows running code from the browser.
"""
import argparse
import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")))
    args = parser.parse_args()
    local_only = args.host in ("127.0.0.1", "localhost", "::1")
    if not local_only:
        print(f" * Listening on all of your network at port {args.port}; debugger disabled.")
    app.run(host=args.host, port=args.port, debug=local_only)
