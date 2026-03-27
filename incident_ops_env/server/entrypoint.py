from __future__ import annotations

import os


def main() -> None:
    import uvicorn
    workers = int(os.environ.get("WORKERS", "1"))
    uvicorn.run(
        "incident_ops_env.server.app:app",
        host="0.0.0.0",
        port=7860,
        workers=workers,
    )


if __name__ == "__main__":
    main()
