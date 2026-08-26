from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
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
    from lakebase_agentworkflow.helpers.models import (
        AgentMessage,
        AgentRequest,
        AgentResult,
        AgentRunStatus,
        AgentSignalPayload,
        AgentSnapshot,
        LakebaseEvent,
        LoanRecommendation,
        MessageRecord,
        ModelRequest,
        RecentContextRequest,
        ReviewDecision,
        ReviewDecisionPayload,
        ReviewDecisionRecord,
        ReviewPendingRecord,
        RunCompletedRecord,
        RunStartedRecord,
        TokenUsage,
        ToolCatalog,
        ToolCall,
        ToolCallRecord,
        ToolExecutionRequest,
        ToolName,
        ToolResult,
        ToolResultRecord,
        TurnMetricRecord,
        WorkerCrashRequest,
        WorkerCrashResult,
        WorkflowMetricCompletedRecord,
        WorkflowMetricStartedRecord,
    )


MODEL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=4,
)
TOOL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)
LAKEBASE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=5,
)
WORKER_CRASH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=2,
)


@workflow.defn(name="WorkerCrashDemoWorkflow")
class WorkerCrashDemoWorkflow:
    @workflow.run
    async def run(self, request: WorkerCrashRequest) -> WorkerCrashResult:
        return await workflow.execute_activity(
            crash_worker_process,
            request,
            start_to_close_timeout=timedelta(seconds=10),
            schedule_to_close_timeout=timedelta(minutes=2),
            retry_policy=WORKER_CRASH_RETRY,
        )


@workflow.defn(name="LoanUnderwritingWorkflow")
class LakebaseAgentWorkflow:
    def __init__(self) -> None:
        self._pending_user_messages: list[AgentSignalPayload] = []
        self._pending_review_decisions: list[ReviewDecisionPayload] = []
        self._seen_review_decision_ids: set[str] = set()
        self._snapshot: AgentSnapshot | None = None
        self._cancel_requested = False
        self._usage = TokenUsage()
        self._tool_results: list[ToolResult] = []

    @workflow.run
    async def run(self, request: AgentRequest) -> AgentResult:
        try:
            return await self._run_agent(request)
        except Exception as exc:
            terminal_statuses = {
                AgentRunStatus.COMPLETED,
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
            }
            if self._snapshot and self._snapshot.status not in terminal_statuses:
                try:
                    await self._complete(
                        request.run_id,
                        AgentRunStatus.FAILED,
                        f"The underwriting workflow failed during {type(exc).__name__}.",
                        self._snapshot.current_turn,
                        self._usage,
                        self._tool_results,
                        recommendation=self._snapshot.recommendation,
                        review_decision=self._snapshot.review_decision,
                    )
                except Exception as persistence_exc:
                    workflow.logger.error(
                        "Could not project terminal workflow failure: %s",
                        persistence_exc,
                    )
            raise

    async def _run_agent(self, request: AgentRequest) -> AgentResult:
        workflow_info = workflow.info()
        metrics_enabled = workflow.patched("lakebase-operational-metrics-v1")
        self._snapshot = AgentSnapshot(run_id=request.run_id, status=AgentRunStatus.RUNNING)
        run_metadata = {
            **request.metadata,
            "applicant_id": request.applicant_id,
            "loan_amount": request.loan_amount,
            "loan_purpose": request.loan_purpose,
            "demo_model_failure_attempts": request.demo_model_failure_attempts,
        }
        messages = [
            AgentMessage(
                role="user",
                content=request.user_request,
                metadata={"source": "initial_request", **run_metadata},
            )
        ]
        usage = self._usage
        tool_results = self._tool_results
        pending_model_failure_attempts = request.demo_model_failure_attempts

        await workflow.execute_activity(
            record_run_started,
            RunStartedRecord(
                run_id=request.run_id,
                workflow_id=workflow.info().workflow_id,
                user_id=request.user_id,
                user_request=request.user_request,
                metadata=run_metadata,
            ),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=LAKEBASE_RETRY,
        )
        await workflow.execute_activity(
            record_event,
            LakebaseEvent(
                event_id=f"{request.run_id}-event-started",
                run_id=request.run_id,
                event_type="agent.run_started",
                payload={"workflow_id": workflow.info().workflow_id},
            ),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=LAKEBASE_RETRY,
        )
        if metrics_enabled:
            await self._record_operational_metric(
                record_workflow_metric_started,
                WorkflowMetricStartedRecord(
                    run_id=request.run_id,
                    workflow_id=workflow_info.workflow_id,
                    workflow_run_id=workflow_info.run_id,
                    workflow_type=workflow_info.workflow_type,
                    task_queue=workflow_info.task_queue,
                    started_at=workflow_info.workflow_start_time,
                    metadata={"namespace": workflow_info.namespace},
                ),
            )
        await self._record_message(
            request.run_id,
            0,
            messages[0],
            f"{request.run_id}-message-initial",
        )

        recent_context = await workflow.execute_activity(
            load_recent_context,
            RecentContextRequest(run_id=request.run_id, user_id=request.user_id),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=LAKEBASE_RETRY,
        )
        if recent_context:
            messages.insert(
                0,
                AgentMessage(
                    role="system",
                    content="Recent saved notes:\n" + "\n".join(recent_context),
                    metadata={"source": "lakebase_recent_context"},
                ),
            )

        for turn in range(1, request.max_turns + 1):
            if self._cancel_requested:
                return await self._complete(
                    request.run_id,
                    AgentRunStatus.CANCELLED,
                    "The workflow was cancelled by signal.",
                    turn - 1,
                    usage,
                    tool_results,
                )

            self._snapshot.current_turn = turn
            turn_started_at = workflow.now()
            model_latency_ms = 0
            tool_latency_ms = 0
            tool_call_count = 0
            turn_usage = TokenUsage()
            await self._drain_pending_signals(request.run_id, turn, messages)

            try:
                model_started_at = workflow.now()
                decision = await workflow.execute_activity(
                    call_model,
                    ModelRequest(
                        run_id=request.run_id,
                        turn=turn,
                        model=request.model,
                        user_id=request.user_id,
                        messages=messages,
                        applicant_id=request.applicant_id,
                        loan_amount=request.loan_amount,
                        loan_purpose=request.loan_purpose,
                        tools=ToolCatalog.default(),
                        simulated_failure_attempts=pending_model_failure_attempts,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    schedule_to_close_timeout=timedelta(minutes=3),
                    retry_policy=MODEL_RETRY,
                )
                pending_model_failure_attempts = 0
                model_latency_ms = _latency_ms(model_started_at, workflow.now())
            except Exception as exc:
                if metrics_enabled:
                    await self._record_turn_metric(
                        request.run_id,
                        turn,
                        "failed",
                        turn_started_at,
                        model_latency_ms,
                        tool_latency_ms,
                        tool_call_count,
                        turn_usage,
                        {"failed_stage": "model", "error_type": type(exc).__name__},
                    )
                raise
            turn_usage = decision.usage
            usage = TokenUsage(
                input_tokens=usage.input_tokens + decision.usage.input_tokens,
                output_tokens=usage.output_tokens + decision.usage.output_tokens,
                total_tokens=usage.total_tokens + decision.usage.total_tokens,
            )
            self._usage = usage

            assistant_message = AgentMessage(
                role="assistant",
                content=decision.assistant_message,
                metadata={
                    **decision.metadata,
                    "provider": decision.metadata.get("provider", "unknown"),
                },
            )
            messages.append(assistant_message)
            self._snapshot.last_message = assistant_message.content
            await self._record_message(
                request.run_id,
                turn,
                assistant_message,
                f"{request.run_id}-message-turn-{turn}-assistant",
            )

            if decision.final or not decision.tool_calls:
                recommendation = _recommendation_from_decision(decision.metadata, tool_results)
                if recommendation is None:
                    if metrics_enabled:
                        await self._record_turn_metric(
                            request.run_id,
                            turn,
                            "failed",
                            turn_started_at,
                            model_latency_ms,
                            tool_latency_ms,
                            tool_call_count,
                            turn_usage,
                            {
                                "final": True,
                                "failed_stage": "recommendation_validation",
                            },
                        )
                    return await self._complete(
                        request.run_id,
                        AgentRunStatus.FAILED,
                        "The model did not produce a structured underwriting recommendation.",
                        turn,
                        usage,
                        tool_results,
                    )
                if metrics_enabled:
                    await self._record_turn_metric(
                        request.run_id,
                        turn,
                        "awaiting_review",
                        turn_started_at,
                        model_latency_ms,
                        tool_latency_ms,
                        tool_call_count,
                        turn_usage,
                        {
                            "final": True,
                            "recommendation": recommendation.model_dump(mode="json"),
                        },
                    )
                review = await self._await_review(
                    request.run_id,
                    turn,
                    recommendation,
                    usage,
                )
                if review is None:
                    return await self._complete(
                        request.run_id,
                        AgentRunStatus.CANCELLED,
                        "The workflow was cancelled while awaiting human review.",
                        turn,
                        usage,
                        tool_results,
                        recommendation=recommendation,
                    )
                if review.decision == ReviewDecision.REQUEST_MORE_INFO:
                    follow_up = AgentMessage(
                        role="user",
                        content=(
                            f"Human reviewer {review.reviewer_id} requested more "
                            f"information: {review.rationale}"
                        ),
                        metadata={
                            "source": "review_request_more_info",
                            "reviewer_id": review.reviewer_id,
                            "review_decision": review.decision.value,
                            **review.metadata,
                        },
                    )
                    messages.append(follow_up)
                    await self._record_message(
                        request.run_id,
                        turn,
                        follow_up,
                        f"{request.run_id}-message-review-{turn}-more-info",
                    )
                    if self._snapshot:
                        self._snapshot.status = AgentRunStatus.RUNNING
                        self._snapshot.last_message = follow_up.content
                    continue

                return await self._complete(
                    request.run_id,
                    AgentRunStatus.COMPLETED,
                    _reviewed_final_response(recommendation, review),
                    turn,
                    usage,
                    tool_results,
                    recommendation=recommendation,
                    review_decision=review,
                )

            self._snapshot.status = AgentRunStatus.WAITING_FOR_TOOL
            for tool_index, tool_call in enumerate(decision.tool_calls, start=1):
                tool_call = _canonicalize_tool_call(
                    tool_call,
                    request,
                    tool_results,
                    turn=turn,
                    tool_index=tool_index,
                )
                await workflow.execute_activity(
                    record_tool_call,
                    ToolCallRecord(run_id=request.run_id, turn=turn, tool_call=tool_call),
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=LAKEBASE_RETRY,
                )
                try:
                    tool_started_at = workflow.now()
                    result = await workflow.execute_activity(
                        execute_tool,
                        ToolExecutionRequest(
                            run_id=request.run_id,
                            turn=turn,
                            user_id=request.user_id,
                            tool_call=tool_call,
                            applicant_id=request.applicant_id,
                            loan_amount=request.loan_amount,
                            loan_purpose=request.loan_purpose,
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                        schedule_to_close_timeout=timedelta(minutes=2),
                        heartbeat_timeout=timedelta(seconds=30),
                        retry_policy=TOOL_RETRY,
                    )
                    tool_latency_ms += _latency_ms(tool_started_at, workflow.now())
                    tool_call_count += 1
                except Exception as exc:
                    if metrics_enabled:
                        await self._record_turn_metric(
                            request.run_id,
                            turn,
                            "failed",
                            turn_started_at,
                            model_latency_ms,
                            tool_latency_ms,
                            tool_call_count,
                            turn_usage,
                            {
                                "failed_stage": "tool",
                                "tool_name": tool_call.name.value,
                                "error_type": type(exc).__name__,
                            },
                        )
                    raise
                tool_results.append(result)
                await workflow.execute_activity(
                    record_tool_result,
                    ToolResultRecord(run_id=request.run_id, turn=turn, result=result),
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=LAKEBASE_RETRY,
                )

                tool_message = AgentMessage(
                    role="tool",
                    name=result.name,
                    content=result.content,
                    metadata={"success": result.success, "data": result.data},
                )
                messages.append(tool_message)
                await self._record_message(
                    request.run_id,
                    turn,
                    tool_message,
                    f"{request.run_id}-message-tool-{result.tool_call_id}",
                )
                self._snapshot.tool_call_count += 1
            self._snapshot.status = AgentRunStatus.RUNNING
            if metrics_enabled:
                await self._record_turn_metric(
                    request.run_id,
                    turn,
                    "completed",
                    turn_started_at,
                    model_latency_ms,
                    tool_latency_ms,
                    tool_call_count,
                    turn_usage,
                    {
                        "final": False,
                        "tool_names": [call.name.value for call in decision.tool_calls],
                    },
                )

        return await self._complete(
            request.run_id,
            AgentRunStatus.FAILED,
            "The agent reached max_turns before producing a final answer.",
            request.max_turns,
            usage,
            tool_results,
        )

    @workflow.signal
    async def add_user_message(self, payload: AgentSignalPayload) -> None:
        self._pending_user_messages.append(payload)
        if self._snapshot:
            self._snapshot.pending_signal_count = len(self._pending_user_messages)

    @workflow.signal
    async def submit_review_decision(self, payload: ReviewDecisionPayload) -> None:
        if (
            self._snapshot is None
            or self._snapshot.status != AgentRunStatus.AWAITING_REVIEW
            or self._pending_review_decisions
        ):
            return
        expected_review_id = (
            f"{self._snapshot.run_id}-review-{self._snapshot.current_turn}"
        )
        if payload.review_id != expected_review_id:
            return
        if payload.decision_id in self._seen_review_decision_ids:
            return
        self._seen_review_decision_ids.add(payload.decision_id)
        self._pending_review_decisions.append(payload)

    @workflow.signal
    async def request_cancel(self) -> None:
        self._cancel_requested = True

    @workflow.query
    def status(self) -> AgentSnapshot | None:
        return self._snapshot

    async def _await_review(
        self,
        run_id: str,
        turn: int,
        recommendation: LoanRecommendation,
        usage: TokenUsage,
    ) -> ReviewDecisionPayload | None:
        if self._snapshot:
            self._snapshot.status = AgentRunStatus.AWAITING_REVIEW
            self._snapshot.recommendation = recommendation
            self._snapshot.review_decision = None

        await workflow.execute_activity(
            record_review_pending,
            ReviewPendingRecord(
                run_id=run_id,
                recommendation=recommendation,
                review_round=turn,
                usage=usage,
            ),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=LAKEBASE_RETRY,
        )
        await workflow.execute_activity(
            record_event,
            LakebaseEvent(
                event_id=f"{run_id}-event-review-pending-{turn}",
                run_id=run_id,
                event_type="agent.review_pending",
                payload={
                    "turn": turn,
                    "recommendation": recommendation.model_dump(mode="json"),
                },
            ),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=LAKEBASE_RETRY,
        )
        await workflow.wait_condition(
            lambda: bool(self._pending_review_decisions) or self._cancel_requested
        )
        if self._cancel_requested:
            return None

        review = self._pending_review_decisions.pop(0)
        if self._snapshot:
            self._snapshot.status = AgentRunStatus.RUNNING
            self._snapshot.review_decision = review
        await workflow.execute_activity(
            record_review_decision,
            ReviewDecisionRecord(
                review_id=f"{run_id}-review-{turn}",
                decision_id=review.decision_id,
                review_round=turn,
                run_id=run_id,
                reviewer_id=review.reviewer_id,
                decision=review.decision,
                rationale=review.rationale,
                model_recommendation=recommendation,
                decided_at=workflow.now(),
            ),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=LAKEBASE_RETRY,
        )
        await workflow.execute_activity(
            record_event,
            LakebaseEvent(
                event_id=f"{run_id}-event-review-decided-{turn}",
                run_id=run_id,
                event_type="agent.review_decided",
                payload={
                    "turn": turn,
                    "reviewer_id": review.reviewer_id,
                    "decision": review.decision.value,
                    "rationale": review.rationale,
                },
            ),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=LAKEBASE_RETRY,
        )
        return review

    async def _drain_pending_signals(
        self,
        run_id: str,
        turn: int,
        messages: list[AgentMessage],
    ) -> None:
        while self._pending_user_messages:
            payload = self._pending_user_messages.pop(0)
            message = AgentMessage(
                role="user",
                content=payload.content,
                metadata={"source": "signal", **payload.metadata},
            )
            messages.append(message)
            await self._record_message(
                run_id,
                turn,
                message,
                f"{run_id}-message-signal-{payload.message_id}",
            )
        if self._snapshot:
            self._snapshot.pending_signal_count = 0

    async def _record_message(
        self,
        run_id: str,
        turn: int,
        message: AgentMessage,
        message_id: str,
    ) -> None:
        await workflow.execute_activity(
            record_message,
            MessageRecord(
                message_id=message_id,
                run_id=run_id,
                turn=turn,
                message=message,
            ),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=LAKEBASE_RETRY,
        )

    async def _record_turn_metric(
        self,
        run_id: str,
        turn: int,
        status: str,
        started_at: datetime,
        model_latency_ms: int,
        tool_latency_ms: int,
        tool_call_count: int,
        usage: TokenUsage,
        metadata: dict[str, Any],
    ) -> None:
        workflow_info = workflow.info()
        completed_at = workflow.now()
        await self._record_operational_metric(
            record_turn_metric,
            TurnMetricRecord(
                run_id=run_id,
                workflow_id=workflow_info.workflow_id,
                workflow_run_id=workflow_info.run_id,
                workflow_type=workflow_info.workflow_type,
                turn=turn,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                latency_ms=_latency_ms(started_at, completed_at),
                model_latency_ms=model_latency_ms,
                tool_latency_ms=tool_latency_ms,
                tool_call_count=tool_call_count,
                usage=usage,
                metadata=metadata,
            ),
        )

    async def _record_operational_metric(self, activity_fn: Any, record: Any) -> None:
        try:
            await workflow.execute_activity(
                activity_fn,
                record,
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=LAKEBASE_RETRY,
            )
        except Exception as exc:
            workflow.logger.warning("Could not record operational metric: %s", exc)

    async def _complete(
        self,
        run_id: str,
        status: AgentRunStatus,
        final_response: str,
        turns: int,
        usage: TokenUsage,
        tool_results: list[ToolResult],
        recommendation: LoanRecommendation | None = None,
        review_decision: ReviewDecisionPayload | None = None,
    ) -> AgentResult:
        if self._snapshot:
            self._snapshot.status = status
            self._snapshot.last_message = final_response
            self._snapshot.current_turn = turns
            if recommendation is not None:
                self._snapshot.recommendation = recommendation
            if review_decision is not None:
                self._snapshot.review_decision = review_decision

        await workflow.execute_activity(
            record_run_completed,
            RunCompletedRecord(
                run_id=run_id,
                status=status,
                final_response=final_response,
                turns=turns,
                usage=usage,
            ),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=LAKEBASE_RETRY,
        )
        await workflow.execute_activity(
            record_event,
            LakebaseEvent(
                event_id=f"{run_id}-event-run-{status.value}",
                run_id=run_id,
                event_type=f"agent.run_{status.value}",
                payload={"turns": turns, "total_tokens": usage.total_tokens},
            ),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=LAKEBASE_RETRY,
        )
        workflow_info = workflow.info()
        completed_at = workflow.now()
        if workflow.patched("lakebase-operational-metrics-v1"):
            await self._record_operational_metric(
                record_workflow_metric_completed,
                WorkflowMetricCompletedRecord(
                    run_id=run_id,
                    workflow_id=workflow_info.workflow_id,
                    workflow_run_id=workflow_info.run_id,
                    workflow_type=workflow_info.workflow_type,
                    status=status,
                    started_at=workflow_info.workflow_start_time,
                    completed_at=completed_at,
                    latency_ms=_latency_ms(workflow_info.workflow_start_time, completed_at),
                    turn_count=turns,
                    metadata={"total_tokens": usage.total_tokens},
                ),
            )
        return AgentResult(
            run_id=run_id,
            status=status,
            final_response=final_response,
            turns=turns,
            tool_results=tool_results,
            usage=usage,
            recommendation=recommendation,
            review_decision=review_decision,
        )


def _latency_ms(started_at: datetime, completed_at: datetime) -> int:
    return max(0, int((completed_at - started_at).total_seconds() * 1000))


def _recommendation_from_decision(
    metadata: dict[str, Any],
    tool_results: list[ToolResult],
) -> LoanRecommendation | None:
    raw_recommendation = metadata.get("recommendation")
    if isinstance(raw_recommendation, dict):
        try:
            return LoanRecommendation.model_validate(raw_recommendation)
        except ValueError:
            pass

    for result in reversed(tool_results):
        if result.name != ToolName.POLICY_LOOKUP or not result.success:
            continue
        try:
            return LoanRecommendation(
                decision=result.data["decision"],
                rationale=str(result.data["rationale"]),
                dti=float(result.data["dti"]),
                credit_score=int(result.data["credit_score"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _canonicalize_tool_call(
    tool_call: ToolCall,
    request: AgentRequest,
    tool_results: list[ToolResult],
    *,
    turn: int,
    tool_index: int,
) -> ToolCall:
    """Keep model-selected tools while sourcing underwriting facts from trusted state."""
    if tool_call.name == ToolName.CREDIT_CHECK:
        arguments = {"applicant_id": request.applicant_id}
    elif tool_call.name == ToolName.INCOME_VERIFICATION:
        arguments = {"applicant_id": request.applicant_id}
    elif tool_call.name == ToolName.DEBT_TO_INCOME_CALC:
        credit = _latest_tool_data(tool_results, ToolName.CREDIT_CHECK)
        income = _latest_tool_data(tool_results, ToolName.INCOME_VERIFICATION)
        arguments = {
            "loan_amount": request.loan_amount,
            "loan_term_months": 36,
        }
        if credit is not None:
            arguments["existing_monthly_debt"] = credit.get("existing_monthly_debt")
        if income is not None:
            arguments["verified_annual_income"] = income.get("verified_annual_income")
    elif tool_call.name == ToolName.POLICY_LOOKUP:
        credit = _latest_tool_data(tool_results, ToolName.CREDIT_CHECK)
        income = _latest_tool_data(tool_results, ToolName.INCOME_VERIFICATION)
        dti = _latest_tool_data(tool_results, ToolName.DEBT_TO_INCOME_CALC)
        arguments = {"loan_purpose": request.loan_purpose}
        if credit is not None:
            arguments.update(
                {
                    "credit_score": credit.get("credit_score"),
                    "delinquency_flags": credit.get("delinquency_flags"),
                    "open_trade_lines": credit.get("open_trade_lines"),
                }
            )
        if income is not None:
            arguments.update(
                {
                    "employment_status": income.get("employment_status"),
                    "verification_status": income.get("verification_status"),
                }
            )
        if dti is not None:
            arguments["dti"] = dti.get("dti")
    else:
        arguments = tool_call.arguments
    return ToolCall(
        id=f"{request.run_id}-tool-{turn}-{tool_index}",
        name=tool_call.name,
        arguments=arguments,
    )


def _latest_tool_data(
    tool_results: list[ToolResult],
    tool_name: ToolName,
) -> dict[str, Any] | None:
    for result in reversed(tool_results):
        if result.name == tool_name and result.success:
            return result.data
    return None


def _reviewed_final_response(
    recommendation: LoanRecommendation,
    review: ReviewDecisionPayload,
) -> str:
    if review.decision == ReviewDecision.APPROVE:
        return (
            f"Human review approved the model's {recommendation.decision.value} "
            f"recommendation. Reviewer {review.reviewer_id}: {review.rationale}"
        )
    return (
        f"Human reviewer {review.reviewer_id} denied and overrode the model's "
        f"{recommendation.decision.value} recommendation. "
        f"Rationale: {review.rationale}"
    )
