from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


DEFAULT_UPLOAD_DIR = Path("/tmp/incident_ops_env_uploads")
SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{3,80}$")


class ScenarioRegistry:
    def __init__(self, upload_dir: Path | None = None) -> None:
        self.upload_dir = upload_dir or DEFAULT_UPLOAD_DIR
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def list_uploaded_ids(self) -> list[str]:
        scenario_ids: list[str] = []
        for path in sorted(self.upload_dir.glob("*.json")):
            scenario_ids.append(path.stem)
        return scenario_ids

    def get_uploaded_path(self, scenario_id: str) -> Path | None:
        if not SCENARIO_ID_RE.match(scenario_id):
            return None
        path = self.upload_dir / f"{scenario_id}.json"
        if path.exists():
            return path
        return None

    def save_uploaded_scenario(self, scenario: dict[str, Any]) -> Path:
        scenario_id = str(scenario.get("scenario_id", ""))
        if not SCENARIO_ID_RE.match(scenario_id):
            raise ValueError(
                "scenario_id must be 3-80 characters and use only letters, numbers, hyphen, underscore."
            )
        path = self.upload_dir / f"{scenario_id}.json"
        path.write_text(json.dumps(scenario, indent=2), encoding="utf-8")
        return path
