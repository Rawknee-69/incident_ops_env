import uvicorn

from incident_ops_env.server.app import app


def main() -> None:
    uvicorn.run("incident_ops_env.server.app:app", host="0.0.0.0", port=7860)


if __name__ == "__main__":
    main()

