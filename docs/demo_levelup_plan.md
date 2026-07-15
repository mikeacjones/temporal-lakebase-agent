# Demo Level-Up Plan: Loan Underwriting Agent

Scope: rework `temporal-lakebase-agentworkflow` from an IP/weather toy into a loan underwriting demo that gives a Databricks sales team a rehearsed durability moment, a governance/human-in-the-loop story, live operational telemetry, and a visible Unity Catalog round trip. Foundation model provider stays as-is (scripted/OpenAI) — no change to `call_model` provider wiring.

> **Terminology update (July 2026):** the outbound Lakebase-to-Unity-Catalog capability described below is now named **Lakebase Change Data Feed**. It is a Public Preview, append-only Delta change-history feed—not SCD Type 2. The inbound Unity-Catalog-to-Lakebase capability remains a **synced table**.

Build order: 1 → 3 → 2 → 5 → 4. The use case is the foundation everything else sits on; the review gate is the emotional core of the pitch; crash recovery is the rehearsed punchline once there's a multi-turn case to interrupt; the UC round trip and dashboard are polish layered on top.

---

## 1. Use Case: Loan Underwriting Agent

Replace the IP/weather brief with an agent that underwrites a personal loan application: pulls credit and income signals, checks debt-to-income against policy, produces a recommend/decline/refer recommendation with rationale, and (per section 3) stops for human sign-off before it's final.

**Request shape.** Update `AgentRequest` (`helpers/models.py`) to carry loan context instead of `client_ip`/`location_hint`:
- `applicant_id: str`
- `loan_amount: float`
- `loan_purpose: str`
- `metadata` keeps free-form extras (state, channel, etc.)

**Tools.** Replace `ToolName.IP_LOOKUP` / `WEATHER_LOOKUP` with:
- `credit_check` — returns a fixture credit score, open trade lines, delinquency flags for `applicant_id`.
- `income_verification` — returns stated vs. verified income and employment status.
- `debt_to_income_calc` — takes verified income + existing debt + `loan_amount`, returns DTI ratio.
- `policy_lookup` — checks DTI/credit score against underwriting policy thresholds, returns pass/fail per rule. (This is the tool section 5 later swaps to read from a real Lakebase-synced Unity Catalog table instead of a Python dict — build it now with a fixture dict so it's swappable.)
- Keep `save_note` as-is; it's a good "agent remembers prior context" beat for repeat applicants.

Mirror the existing `WEATHER_FIXTURES` pattern: an `APPLICANT_FIXTURES` dict keyed by `applicant_id` with 4-5 canned profiles that deliberately span outcomes — a clean approve, a policy-borderline case that should route to referral, and a decline. Borderline/referral is the one you'll drive through the crash-recovery and review-gate demos, since it naturally takes multiple turns and warrants human judgment.

**Scripted model logic.** Rewrite `scripted_model_decision` to sequence `credit_check` → `income_verification` → `debt_to_income_calc` → `policy_lookup`, then synthesize a final message containing a structured recommendation: `{decision: approve|decline|refer, rationale, dti, credit_score}`. Put this structured payload in `ModelDecision.metadata` so downstream code (review gate, UI) doesn't have to parse prose.

**Lakebase.** No new tables needed yet for this section — `agent_runs.metadata` and `agent_messages` already carry arbitrary JSON, which is enough to store applicant_id/loan_amount and the tool trail. Section 3 adds the review-specific table.

**Starter script.** Update `starter.py` args from `--client-ip`/`--location` to `--applicant-id`/`--loan-amount`/`--loan-purpose`. Update the README example command accordingly.

---

## 2. Crash Recovery Demo Script

The rehearsed "wow" moment: kill the worker mid-run, show Temporal resumes the exact loan file with no lost or duplicated state.

- Pick the borderline applicant fixture so the run naturally spans 4 tool calls before reaching a recommendation — enough turns to have a clean window to kill the process.
- Add `scripts/demo_crash_recovery.sh`:
  1. Start Temporal dev server (or assume already running) and the worker in the background, capture its PID.
  2. Start the workflow via `starter.py` with the borderline applicant.
  3. Sleep briefly (long enough for `credit_check` + `income_verification` to have completed, short enough that `policy_lookup` hasn't run) — tune by watching activity timestamps once, then hardcode the sleep.
  4. `kill -9 $WORKER_PID` — hard kill, not graceful shutdown, to prove nothing app-level is doing the saving.
  5. Print a message and pause (`read -p`) so the presenter can point at `temporal workflow show --workflow-id <id>` and the Lakebase `agent_messages` rows already persisted, showing the run is durably "waiting," not lost.
  6. Restart the worker.
  7. Poll `temporal workflow describe` or the UI until the run reaches `COMPLETED`/`AWAITING_REVIEW`, proving it resumed from the exact next activity rather than replaying from scratch or losing the tool results collected before the kill.
- Add a "Live Demo: Crash Recovery" section to the README with the narration script (what to say at each pause), not just the commands — the presenter should be able to run this cold.

---

## 3. Human Review Gate on the Recommendation

Every recommendation — not just high-value loans, to keep the demo simple and always show the gate — stops for human sign-off before the run is marked complete. This is the strongest "durable execution" proof point: the workflow can sit paused for seconds or days with zero polling cost and resume exactly where it left off.

**Workflow change (`workflows.py`).** When the model returns `final=True` with a recommendation payload, don't complete immediately:
1. Set `self._snapshot.status = AgentRunStatus.AWAITING_REVIEW` (new enum value in `helpers/models.py`).
2. Record the draft recommendation via a new `record_review_pending` activity/table (below).
3. `await workflow.wait_condition(lambda: self._review_decision is not None)` — populated by a new signal.
4. Optionally race the wait against a timer (`asyncio.wait` with `workflow.sleep(timedelta(hours=X))`) to demonstrate a Temporal timer firing an escalation notification if no reviewer acts in time — a good secondary "durability over long real time" beat, but treat as optional polish.
5. On decision, record it, then proceed to `_complete` with the reviewer's disposition as the final status/message (approved recommendation stands, or reviewer override reason becomes the final response).

**New signal.** `submit_review_decision(payload: ReviewDecisionPayload)` where `ReviewDecisionPayload` has `reviewer_id`, `decision: approve|deny|request_more_info`, `rationale`. `request_more_info` should append a synthetic user message and let the loop run another turn — reuses the existing `add_user_message` machinery conceptually but keep it a distinct signal so the UI can present a dedicated review action, not a chat box.

**New Lakebase table.** `agent_review_decisions`: `run_id`, `reviewer_id`, `decision`, `rationale`, `model_recommendation` (JSONB snapshot of what the model proposed), `decided_at`. Add to `sql/001_agent_ops_schema.sql` alongside the existing tables; this is UI-serving data (not operational metrics), so it belongs in the same category as `agent_notes`/`agent_notifications`.

**API + UI.** Add `POST /api/runs/{run_id}/review` in `api.py` that signals the workflow. Add a review panel to the UI: when a run's status is `AWAITING_REVIEW`, show the model's recommendation, rationale, and DTI/credit inputs, with Approve/Deny/Request-More-Info actions. This is the screen you'll have on-screen the longest during the customer demo — worth the most visual polish of anything in this plan.

---

## 4. Live Ops Dashboard

Surfaces the operational telemetry the schema already captures but nothing currently displays — makes the retry/observability pitch demonstrable instead of descriptive.

- New API endpoints in `api.py`:
  - `GET /api/metrics/runs` — success rate, p50/p95 end-to-end latency, count by status (including the new `AWAITING_REVIEW`), sourced from `agent_workflow_metrics`.
  - `GET /api/metrics/retries` — retry counts by `activity_type`, sourced from the existing `agent_activity_retry_counts` view.
  - `GET /api/metrics/cost` — token usage trend over time from `agent_turn_metrics`.
- New UI tab/route (e.g. `/ops`) with three panels: run outcomes (including a live count of runs currently sitting in `AWAITING_REVIEW`, which ties directly back to section 3), retries-by-activity-type, and token cost trend. Poll on an interval so it visibly updates during a live demo.
- No new Lakebase tables required — this section is purely reads against what already exists.

---

## 5. Unity Catalog Loop Closure

Makes the Lakebase and Unity Catalog integration story tangible instead of a paragraph in `architecture.md`.

- **Changes out:** follow the existing steps in `docs/production_setup.md` to enable Lakebase Change Data Feed on the `agent_ops` schema, targeting `agentworkflow_lakehouse`. After a demo run (including a reviewed loan), query the Delta change-history table directly (`databricks` CLI or a SQL warehouse query) and show the reviewed postimage — this is the "screen-share the row appearing in Unity Catalog" moment.
- **Policy in (the stronger proof point):** create a small UC gold table, e.g. `agentworkflow_lakehouse.governed.underwriting_policy_limits` (columns like `min_credit_score`, `max_dti`, `loan_purpose`), and create a Lakebase synced table from it. Repoint the `policy_lookup` tool at that synced table instead of the Python fixture dict. This demonstrates both directions in one demo: governed policy curated in Unity Catalog, served to the agent at low latency via Lakebase, with the changes generated by its decisions captured back in Unity Catalog for audit/analytics. That closed loop is the single clearest "why Lakebase, why not just call Postgres yourself" argument in the whole demo.
- Document both directions in `docs/architecture.md` under "Unity Catalog Integration" with the concrete table names used, replacing the current hypothetical description.
