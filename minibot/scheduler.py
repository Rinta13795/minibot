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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


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
        """初始化调度器。

        Args:
            workspace: 工作目录，任务清单存为 workspace / "cron.json"。
            on_trigger: 任务到点的回调，签名 (job: CronJob) -> None。
                        典型实现：lambda job: core.chat(job.prompt)。

        TODO:
            - self.cron_path = workspace / "cron.json"
            - self.on_trigger = on_trigger
            - self._jobs: dict[str, CronJob] = {}
            - self._timer: threading.Timer | None = None
            - self._running = False
            - load_jobs()
        """
        raise NotImplementedError("TODO: __init__")

    # ---------- 持久化 ----------

    def load_jobs(self) -> None:
        """从 cron.json 加载任务到内存。

        TODO:
            - 文件不存在则 self._jobs = {}
            - json.load → 反序列化成 CronJob 列表
        """
        raise NotImplementedError("TODO: load_jobs")

    def save_jobs(self) -> None:
        """把当前内存中的任务写回 cron.json（原子写）。

        TODO:
            - asdict(job) for job in self._jobs.values()
            - tmp + os.replace
        """
        raise NotImplementedError("TODO: save_jobs")

    # ---------- 任务管理 ----------

    def add_job(self, job: CronJob) -> None:
        """新增任务并落盘。

        Args:
            job: 新任务，id 若重复会覆盖。

        TODO:
            - 校验 cron 表达式合法（用 croniter 试解析）
            - self._jobs[job.id] = job
            - save_jobs() + _rearm()
        """
        raise NotImplementedError("TODO: add_job")

    def remove_job(self, job_id: str) -> bool:
        """删除任务。

        Returns:
            True 表示成功删除，False 表示 id 不存在。

        TODO: pop + save + _rearm
        """
        raise NotImplementedError("TODO: remove_job")

    def list_jobs(self) -> list[CronJob]:
        """返回所有任务（含 disabled 的）。

        TODO: list(self._jobs.values())
        """
        raise NotImplementedError("TODO: list_jobs")

    # ---------- 运行控制 ----------

    def run_forever(self) -> None:
        """阻塞运行调度循环，直到 stop()。

        实现可以是：
            - 简单方案：while self._running: sleep(next_wake_seconds); _tick()
            - 进阶方案：用 threading.Timer / asyncio.sleep

        TODO: 实现循环 + _tick 调用
        """
        raise NotImplementedError("TODO: run_forever")

    def stop(self) -> None:
        """优雅停止调度。

        TODO:
            - self._running = False
            - cancel self._timer
        """
        raise NotImplementedError("TODO: stop")

    # ---------- 内部 ----------

    def _next_due_job(self) -> tuple[CronJob, float] | None:
        """找出最快需要触发的任务。

        Returns:
            (job, fire_at_ts) 元组；没有任务时返回 None。

        TODO:
            - 对每个 enabled job 调 croniter.get_next(datetime)
            - 取最小值
        """
        raise NotImplementedError("TODO: _next_due_job")

    def _tick(self, job: CronJob) -> None:
        """触发一次任务执行。

        步骤：
            1. self.on_trigger(job)
            2. job.last_run_ts = time.time()
            3. save_jobs()
            4. _rearm()

        TODO: 实现 + try/except 包裹 on_trigger，避免单个任务异常拖垮调度器
        """
        raise NotImplementedError("TODO: _tick")
