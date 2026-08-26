from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import signal
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio.common import VersioningBehavior, WorkerDeploymentVersion
from temporalio.worker import Worker, WorkerDeploymentConfig

from lakebase_agentworkflow.activities import (
    call_model,
    crash_worker_process,
    execute_tool,
    load_recent_context,
    record_event,
    record_message,
    record_review_decision,
    record_review_pending,
    record_run_completed,
    record_run_started,
    record_tool_call,
    record_tool_result,
    record_turn_metric,
    record_workflow_metric_completed,
    record_workflow_metric_started,
)
from lakebase_agentworkflow.helpers.env import load_env_file
from lakebase_agentworkflow.helpers.temporal_client import connect_temporal
from lakebase_agentworkflow.workflows import LakebaseAgentWorkflow, WorkerCrashDemoWorkflow


logger = logging.getLogger(__name__)


ACTIVITIES = [
    call_model,
    crash_worker_process,
    execute_tool,
    load_recent_context,
    record_event,
    record_message,
    record_review_decision,
    record_review_pending,
    record_run_completed,
    record_run_started,
    record_tool_call,
    record_tool_result,
    record_turn_metric,
    record_workflow_metric_completed,
    record_workflow_metric_started,
]


@dataclass(frozen=True)
class WorkerRuntimeSettings:
    activity_executor_max_workers: int
    max_cached_workflows: int
    max_concurrent_workflow_tasks: int | None
    max_concurrent_activities: int | None
    max_concurrent_local_activities: int | None
    max_concurrent_workflow_task_polls: int | None
    max_concurrent_activity_task_polls: int | None
    max_activities_per_second: float | None
    max_task_queue_activities_per_second: float | None
    graceful_shutdown_seconds: float
    ready_file: Path
    default_versioning_behavior: VersioningBehavior

    @classmethod
    def from_env(cls) -> "WorkerRuntimeSettings":
        return cls(
            activity_executor_max_workers=_positive_int_env(
                "TEMPORAL_WORKER_ACTIVITY_EXECUTOR_MAX_WORKERS",
                default=32,
            ),
            max_cached_workflows=_positive_int_env(
                "TEMPORAL_WORKER_MAX_CACHED_WORKFLOWS",
                default=1000,
            ),
            max_concurrent_workflow_tasks=_positive_int_env(
                "TEMPORAL_WORKER_MAX_CONCURRENT_WORKFLOW_TASKS"
            ),
            max_concurrent_activities=_positive_int_env(
                "TEMPORAL_WORKER_MAX_CONCURRENT_ACTIVITIES",
                default=32,
            ),
            max_concurrent_local_activities=_positive_int_env(
                "TEMPORAL_WORKER_MAX_CONCURRENT_LOCAL_ACTIVITIES"
            ),
            max_concurrent_workflow_task_polls=_positive_int_env(
                "TEMPORAL_WORKER_MAX_CONCURRENT_WORKFLOW_TASK_POLLS"
            ),
            max_concurrent_activity_task_polls=_positive_int_env(
                "TEMPORAL_WORKER_MAX_CONCURRENT_ACTIVITY_TASK_POLLS"
            ),
            max_activities_per_second=_positive_float_env(
                "TEMPORAL_WORKER_MAX_ACTIVITIES_PER_SECOND"
            ),
            max_task_queue_activities_per_second=_positive_float_env(
                "TEMPORAL_WORKER_MAX_TASK_QUEUE_ACTIVITIES_PER_SECOND"
            ),
            graceful_shutdown_seconds=_nonnegative_float_env(
                "TEMPORAL_WORKER_GRACEFUL_SHUTDOWN_SECONDS",
                default=30.0,
            ),
            ready_file=Path(
                os.getenv("TEMPORAL_WORKER_READY_FILE", "/tmp/temporal-worker-ready")
            ),
            default_versioning_behavior=_versioning_behavior_env(),
        )

    def worker_kwargs(self) -> dict[str, Any]:
        return {
            "max_cached_workflows": self.max_cached_workflows,
            "max_concurrent_workflow_tasks": self.max_concurrent_workflow_tasks,
            "max_concurrent_activities": self.max_concurrent_activities,
            "max_concurrent_local_activities": self.max_concurrent_local_activities,
            "max_concurrent_workflow_task_polls": self.max_concurrent_workflow_task_polls,
            "max_concurrent_activity_task_polls": self.max_concurrent_activity_task_polls,
            "max_activities_per_second": self.max_activities_per_second,
            "max_task_queue_activities_per_second": (
                self.max_task_queue_activities_per_second
            ),
            "graceful_shutdown_timeout": timedelta(
                seconds=self.graceful_shutdown_seconds
            ),
            "deployment_config": self.deployment_config(),
        }

    def deployment_config(self) -> WorkerDeploymentConfig | None:
        deployment_name = os.getenv("TEMPORAL_DEPLOYMENT_NAME") or os.getenv(
            "TEMPORAL_WORKER_DEPLOYMENT_NAME"
        )
        build_id = os.getenv("TEMPORAL_WORKER_BUILD_ID")
        if not deployment_name and not build_id:
            return None
        if not deployment_name or not build_id:
            raise ValueError(
                "TEMPORAL_DEPLOYMENT_NAME and TEMPORAL_WORKER_BUILD_ID must be set together"
            )
        return WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(
                deployment_name=deployment_name,
                build_id=build_id,
            ),
            use_worker_versioning=True,
            default_versioning_behavior=self.default_versioning_behavior,
        )


async def main() -> None:
    load_env_file()
    client = await connect_temporal()
    settings = WorkerRuntimeSettings.from_env()
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "lakebase-agent-task-queue")
    stop_event = asyncio.Event()
    registered_signals = _register_shutdown_signals(stop_event)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=settings.activity_executor_max_workers
    ) as activity_executor:
        worker = Worker(
            client,
            task_queue=task_queue,
            workflows=[LakebaseAgentWorkflow, WorkerCrashDemoWorkflow],
            activities=ACTIVITIES,
            activity_executor=activity_executor,
            **settings.worker_kwargs(),
        )
        try:
            async with worker:
                settings.ready_file.parent.mkdir(parents=True, exist_ok=True)
                settings.ready_file.touch()
                logger.info(
                    "Worker polling task queue %r with activity concurrency %s",
                    task_queue,
                    settings.max_concurrent_activities,
                )
                await stop_event.wait()
                logger.info("Worker shutdown requested")
        finally:
            settings.ready_file.unlink(missing_ok=True)
            _remove_shutdown_signals(registered_signals)


def _register_shutdown_signals(stop_event: asyncio.Event) -> list[signal.Signals]:
    loop = asyncio.get_running_loop()
    registered: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue
        registered.append(sig)
    return registered


def _remove_shutdown_signals(signals: list[signal.Signals]) -> None:
    loop = asyncio.get_running_loop()
    for sig in signals:
        loop.remove_signal_handler(sig)


def _positive_int_env(name: str, default: int | None = None) -> int | None:
    raw_value = os.getenv(name)
    if raw_value in {None, ""}:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float_env(name: str, default: float | None = None) -> float | None:
    value = _nonnegative_float_env(name, default=default)
    if value is not None and value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _nonnegative_float_env(
    name: str,
    default: float | None = None,
) -> float | None:
    raw_value = os.getenv(name)
    if raw_value in {None, ""}:
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < 0:
        raise ValueError(f"{name} must be zero or greater")
    return value


def _versioning_behavior_env() -> VersioningBehavior:
    raw_value = os.getenv(
        "TEMPORAL_WORKER_DEFAULT_VERSIONING_BEHAVIOR",
        "PINNED",
    ).strip().upper()
    try:
        return VersioningBehavior[raw_value]
    except KeyError as exc:
        allowed = ", ".join(behavior.name for behavior in VersioningBehavior)
        raise ValueError(
            "TEMPORAL_WORKER_DEFAULT_VERSIONING_BEHAVIOR must be one of " + allowed
        ) from exc


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    asyncio.run(main())
