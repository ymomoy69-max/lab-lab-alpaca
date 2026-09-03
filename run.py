#!/usr/bin/env python3
"""Run Nexus Agent dashboard."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"), override=True)

if __name__ == "__main__":
    try:
        import alpaca  # noqa: F401
    except ModuleNotFoundError:
        venv_py = os.path.join(ROOT, ".venv", "bin", "python")
        print(
            "Missing dependencies (alpaca-py). Use the project venv:\n"
            f"  {venv_py} run.py\n"
            "Or install once:\n"
            f"  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)

    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    print(f"Nexus dashboard listening on 0.0.0.0:{port}", flush=True)
    uvicorn.run(
        "server.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
