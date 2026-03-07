"""Backward-compatible wrapper — imports from claw_recall.api.web."""
from claw_recall.api.web import *  # noqa: F401,F403
from claw_recall.api.web import app  # explicit for WSGI/systemd

if __name__ == '__main__':
    import argparse
    from claw_recall.search.engine import preload_embedding_cache
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', '-p', type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    print(f"Recall Web Interface running at http://localhost:{args.port}")
    preload_embedding_cache()
    app.run(host=args.host, port=args.port, debug=False)
