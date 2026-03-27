from __future__ import annotations

import time
import uuid

from incident_ops_env.server.environment import IncidentOpsEnvironment


SESSION_TTL_SECONDS = 30 * 60


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, IncidentOpsEnvironment] = {}
        self._last_access: dict[str, float] = {}

    def create_or_get_session(self, session_id: str | None = None) -> tuple[str, IncidentOpsEnvironment]:
        self._cleanup_expired()
        sid = session_id or str(uuid.uuid4())
        if sid not in self._sessions:
            self._sessions[sid] = IncidentOpsEnvironment()
        self._last_access[sid] = time.time()
        return sid, self._sessions[sid]

    def get_session(self, session_id: str) -> IncidentOpsEnvironment | None:
        self._cleanup_expired()
        env = self._sessions.get(session_id)
        if env is not None:
            self._last_access[session_id] = time.time()
        return env

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired = [sid for sid, ts in self._last_access.items() if (now - ts) > SESSION_TTL_SECONDS]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._last_access.pop(sid, None)
