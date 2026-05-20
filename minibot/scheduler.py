"""scheduler.py — 定时调度器。

对应 Nanobot 的 cron/service.py（极简版）。

支持的 cron 表达式：标准 5 字段 "min hour day mon weekday"。
依赖第三方库 croniter 做下次触发时间计算（见 requirements.txt）。

设计选择：
    - 任务持久化到 workspace/cron.json，重启可恢复。
    - 单线程定时器（threading.Timer），每次触发后重新计算下一次。
    - 每个任务的 action 是一个字符串 prompt，触发时通过 core.chat() 跑一遍。

config.json 里的任务示例：
    "scheduled_tasks": [
        {"id": "morning_brief", "cron": "0 9 * * *", "prompt": "汇报昨天的服务器健康状况"}
    ]
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from croniter import croniter


@dataclass
class CronJob:
    """单个定时任务的数据模型。

    Attributes:
        id: 任务唯一 ID。
        cron: 5 字段 cron 表达式。
        prompt: 触发时要丢给 LLM 的指令。
        enabled: 是否激活。
        last_run_ts: 上次成功运行的 Unix 时间戳（秒），0 表示从未运行。
        metadata: 任意附加信息（如来源、tag）。
    """

    id: str
    cron: str
    prompt: str
    enabled: bool = True
    last_run_ts: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class Scheduler:
    """轻量级 cron 调度器。

    职责：
        1. 持久化 / 加载任务列表。
        2. 计算每个任务的下一次触发时间。
        3. 到点回调 on_trigger（由 core.py 注入，通常是 core.chat）。
    """

    def __init__(
        self,
        workspace: Path,
        on_trigger: Callable[[CronJob], None],
    ) -> None:
        self.cron_path = workspace / "cron.json"
        self.on_trigger = on_trigger
        self._jobs: dict[str, CronJob] = {}
        self._timer: threading.Timer | None = None
        self._running = False
        self.load_jobs()

    # ---------- 持久化 ----------

    def load_jobs(self) -> None:
        """从 cron.json 加载任务到内存。"""
        if not self.cron_path.exists():
            self._jobs = {}
            return
        with open(self.cron_path, encoding="utf-8") as f:
            data = json.load(f)
        self._jobs = {item["id"]: CronJob(**item) for item in data}

    def save_jobs(self) -> None:
        """把当前内存中的任务写回 cron.json（原子写）。"""
        tmp = self.cron_path.with_suffix(".tmp")
        self.cron_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([asdict(job) for job in self._jobs.values()], f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.cron_path)

    # ---------- 任务管理 ----------

    def add_job(self, job: CronJob) -> None:
        """新增任务并落盘。id 重复时覆盖。"""
        croniter(job.cron)  # validate expression; raises ValueError if invalid
        self._jobs[job.id] = job
        self.save_jobs()
        if self._running:
            self._rearm()

    def remove_job(self, job_id: str) -> bool:
        """删除任务。返回 True 表示成功，False 表示 id 不存在。"""
        if job_id not in self._jobs:
            return False
        del self._jobs[job_id]
        self.save_jobs()
        if self._running:
            self._rearm()
        return True

    def list_jobs(self) -> list[CronJob]:
        """返回所有任务（含 disabled 的）。"""
        return list(self._jobs.values())

    # ---------- 运行控制 ----------

    def run_forever(self) -> None:
        """阻塞运行调度循环，直到 stop()。"""
        self._running = True
        self._rearm()
        while self._running:
            time.sleep(1)

    def stop(self) -> None:
        """优雅停止调度。"""
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    # ---------- 内部 ----------

    def _next_due_job(self) -> tuple[CronJob, float] | None:
        """找出最快需要触发的任务。返回 (job, fire_at_unix_ts) 或 None。"""
        now = time.time()
        best_job: CronJob | None = None
        best_ts: float | None = None
        for job in self._jobs.values():
            if not job.enabled:
                continue
            cit = croniter(job.cron, now)
            next_ts: float = cit.get_next(float)
            if best_ts is None or next_ts < best_ts:
                best_job = job
                best_ts = next_ts
        if best_job is None or best_ts is None:
            return None
        return (best_job, best_ts)

    def _rearm(self) -> None:
        """取消旧定时器，按最近一个任务重新设置。"""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        result = self._next_due_job()
        if result is None:
            return

        job, fire_at = result
        delay = max(0.0, fire_at - time.time())
        self._timer = threading.Timer(delay, self._tick, args=[job])
        self._timer.daemon = True
        self._timer.start()

    def _tick(self, job: CronJob) -> None:
        """触发一次任务执行，异常不向上传播避免拖垮调度器。"""
        try:
            self.on_trigger(job)
        except Exception as exc:
            print(f"[scheduler] job '{job.id}' raised: {exc}")
        job.last_run_ts = time.time()
        try:
            self.save_jobs()
        except Exception:
            pass
        if self._running:
            self._rearm()
