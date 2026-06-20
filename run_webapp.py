"""
Convenience launcher — run from project root with .venv active:
    python run_webapp.py
"""
from webapp.app import app

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    print(f"[EPL Scheduler] Dashboard at http://127.0.0.1:{args.port}")
    app.run(debug=args.debug, port=args.port)
