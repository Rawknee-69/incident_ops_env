from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class IncidentMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()
        self.episodes_started = 0
        self.episodes_completed = 0
        self.episodes_by_task = defaultdict(int)
        self.request_counts = defaultdict(int)
        self.endpoint_latencies = defaultdict(list)
        self.recent_episode_scores = deque(maxlen=50)
        self.step_rewards = deque(maxlen=100)
        self.step_timestamps = deque(maxlen=1000)
        self.action_type_counts = defaultdict(int)
        self.invalid_action_count = 0
        self.no_op_count = 0
        self.active_sessions = {}

    def record_request(self, endpoint: str, latency_ms: float) -> None:
        with self._lock:
            self.request_counts[endpoint] += 1
            self.endpoint_latencies[endpoint].append(latency_ms)
            if len(self.endpoint_latencies[endpoint]) > 1000:
                self.endpoint_latencies[endpoint] = self.endpoint_latencies[endpoint][-1000:]

    def record_episode_start(self, session_id: str, task_id: int) -> None:
        with self._lock:
            self.episodes_started += 1
            self.episodes_by_task[str(task_id)] += 1
            self.active_sessions[session_id] = {"task_id": task_id, "step": 0, "started_at": time.time()}

    def record_episode_end(self, session_id: str, score: float, steps: int) -> None:
        with self._lock:
            self.episodes_completed += 1
            if session_id in self.active_sessions:
                task_id = self.active_sessions[session_id]["task_id"]
                self.recent_episode_scores.append(
                    {"task_id": task_id, "score": score, "steps_used": steps, "timestamp": time.time()}
                )
                del self.active_sessions[session_id]

    def record_step(self, session_id: str, action_type: str, reward: float, is_valid: bool) -> None:
        with self._lock:
            self.action_type_counts[action_type] += 1
            self.step_rewards.append(reward)
            self.step_timestamps.append(time.time())
            if not is_valid:
                self.invalid_action_count += 1
            if action_type == "no_op":
                self.no_op_count += 1
            if session_id in self.active_sessions:
                self.active_sessions[session_id]["step"] += 1

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            recent_steps = [ts for ts in self.step_timestamps if now - ts <= 60.0]
            all_scores = [x["score"] for x in self.recent_episode_scores]
            task_avgs: dict[str, float] = {"task_1": 0.0, "task_2": 0.0, "task_3": 0.0}
            by_task = defaultdict(list)
            for item in self.recent_episode_scores:
                by_task[f"task_{item['task_id']}"].append(item["score"])
            for key in task_avgs:
                vals = by_task.get(key, [])
                task_avgs[key] = round(sum(vals) / len(vals), 3) if vals else 0.0

            latency = {}
            for endpoint, values in self.endpoint_latencies.items():
                if not values:
                    continue
                sorted_values = sorted(values)
                p50 = sorted_values[int(0.5 * (len(sorted_values) - 1))]
                latency[endpoint] = {"p50": round(p50, 2), "count": len(values)}

            return {
                "server": {
                    "uptime_seconds": round(now - self._start_time, 1),
                    "active_sessions": len(self.active_sessions),
                    "active_sessions_detail": self.active_sessions,
                },
                "episodes": {
                    "started": self.episodes_started,
                    "completed": self.episodes_completed,
                    "by_task": dict(self.episodes_by_task),
                },
                "scores": {
                    "average_by_task": task_avgs,
                    "distribution": {
                        "0.0-0.2": sum(1 for s in all_scores if s < 0.2),
                        "0.2-0.4": sum(1 for s in all_scores if 0.2 <= s < 0.4),
                        "0.4-0.6": sum(1 for s in all_scores if 0.4 <= s < 0.6),
                        "0.6-0.8": sum(1 for s in all_scores if 0.6 <= s < 0.8),
                        "0.8-1.0": sum(1 for s in all_scores if s >= 0.8),
                    },
                    "recent": list(self.recent_episode_scores)[-10:],
                },
                "steps": {
                    "per_second": round(len(recent_steps) / 60.0, 2),
                    "invalid_action_rate": round(
                        self.invalid_action_count / max(1, sum(self.action_type_counts.values())),
                        3,
                    ),
                    "no_op_rate": round(self.no_op_count / max(1, sum(self.action_type_counts.values())), 3),
                    "action_type_distribution": dict(self.action_type_counts),
                    "recent_rewards": list(self.step_rewards)[-20:],
                },
                "api": {"request_counts": dict(self.request_counts), "latency_stats": latency},
            }


metrics = IncidentMetrics()
