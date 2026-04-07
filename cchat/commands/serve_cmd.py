"""Start the cchat web UI server."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("serve", help="Start the cchat web UI server")
    p.add_argument(
        "--port",
        type=int,
        default=8411,
        help="Port to listen on (default: 8411)",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ImportError:
        raise SystemExit(
            "uvicorn is required for 'cchat serve'. "
            "Install it with: uv tool install --force --editable '.[serve]'"
        )

    from cchat.web.app import create_app

    app = create_app()
    print(f"Starting cchat web UI at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
