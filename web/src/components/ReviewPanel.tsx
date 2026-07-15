import { useEffect, useMemo, useState } from "react";
import type {
  LoanRecommendation,
  ReviewDecision,
  ReviewDecisionPayload,
  WorkflowRun,
} from "../types";

const DECISION_LABEL: Record<LoanRecommendation["decision"], string> = {
  approve: "Approve",
  decline: "Decline",
  refer: "Refer",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseRecommendation(value: unknown): LoanRecommendation | null {
  if (!isRecord(value)) return null;
  const decision = value.decision;
  if (decision !== "approve" && decision !== "decline" && decision !== "refer") return null;
  if (
    typeof value.rationale !== "string" ||
    typeof value.dti !== "number" ||
    typeof value.credit_score !== "number"
  ) {
    return null;
  }
  return {
    decision,
    rationale: value.rationale,
    dti: value.dti,
    credit_score: value.credit_score,
  };
}

export function recommendationForRun(run: WorkflowRun): LoanRecommendation | null {
  const direct = parseRecommendation(run.recommendation);
  if (direct) return direct;
  const fromRunMetadata = parseRecommendation(run.metadata?.recommendation);
  if (fromRunMetadata) return fromRunMetadata;
  for (const message of [...run.messages].reverse()) {
    const candidate = parseRecommendation(message.metadata?.recommendation ?? message.metadata);
    if (candidate) return candidate;
  }
  return null;
}

function displayPurpose(value: unknown): string {
  return typeof value === "string" ? value.replace(/_/g, " ") : "Personal loan";
}

function displayMoney(value: unknown): string {
  if (typeof value !== "number") return "—";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

interface PolicyEvidence {
  source: string;
  live: boolean;
  minCreditScore: number | null;
  maxDti: number | null;
}

function policyEvidenceForRun(run: WorkflowRun): PolicyEvidence | null {
  const result = [...run.tool_results]
    .reverse()
    .find((toolResult) => toolResult.name === "policy_lookup" && toolResult.success);
  if (!result) return null;
  const data = result.data;
  const policy = isRecord(data.policy) ? data.policy : null;
  const source = typeof data.policy_source === "string" ? data.policy_source : "fixture_fallback";
  return {
    source,
    live: source === "lakebase_synced_table",
    minCreditScore:
      policy && typeof policy.min_credit_score === "number" ? policy.min_credit_score : null,
    maxDti: policy && typeof policy.max_dti === "number" ? policy.max_dti : null,
  };
}

interface ReviewPanelProps {
  run: WorkflowRun;
  liveData: boolean;
  onSubmit: (payload: ReviewDecisionPayload) => Promise<void>;
}

export function ReviewPanel({ run, liveData, onSubmit }: ReviewPanelProps) {
  const recommendation = useMemo(() => recommendationForRun(run), [run]);
  const policyEvidence = useMemo(() => policyEvidenceForRun(run), [run]);
  const [reviewerId, setReviewerId] = useState("underwriter@example.com");
  const [rationale, setRationale] = useState(
    "Reviewed the supporting evidence and policy checks."
  );
  const [submitting, setSubmitting] = useState<ReviewDecision | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSubmitted(false);
    setError(null);
  }, [run.run_id]);

  async function decide(decision: ReviewDecision) {
    if (!reviewerId.trim() || !rationale.trim() || submitting) return;
    setSubmitting(decision);
    setError(null);
    try {
      const reviewMetadata = isRecord(run.metadata?.review) ? run.metadata.review : null;
      const reviewRound =
        reviewMetadata && typeof reviewMetadata.round === "number"
          ? reviewMetadata.round
          : run.turns;
      const reviewId = `${run.run_id}-review-${reviewRound}`;
      await onSubmit({
        reviewer_id: reviewerId.trim(),
        decision,
        rationale: rationale.trim(),
        review_id: reviewId,
        decision_id: `${reviewId}:${crypto.randomUUID()}`,
        metadata: { source: "loan_review_ui" },
      });
      setSubmitted(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not submit review decision");
    } finally {
      setSubmitting(null);
    }
  }

  return (
    <section className="review-panel" aria-labelledby="review-heading">
      <div className="review-panel__accent" aria-hidden="true" />
      <div className="review-panel__body">
        <header className="review-panel__head">
          <div>
            <span className="eyebrow eyebrow--review">Human decision required</span>
            <h3 id="review-heading">Underwriter review</h3>
            <p>
              {liveData
                ? "Temporal is holding the execution. Lakebase is serving the live review state; the policy source used for this run is shown below."
                : "This synthetic view demonstrates Temporal's durable review gate. The policy evidence below is explicitly marked as fixture data."}
            </p>
          </div>
          {recommendation && (
            <span className={`recommendation recommendation--${recommendation.decision}`}>
              Model says {DECISION_LABEL[recommendation.decision]}
            </span>
          )}
        </header>

        {recommendation ? (
          <>
            <div className="review-facts">
              <div>
                <span>Credit score</span>
                <strong>{recommendation.credit_score}</strong>
              </div>
              <div>
                <span>Projected DTI</span>
                <strong>{(recommendation.dti * 100).toFixed(1)}%</strong>
              </div>
              <div>
                <span>Loan amount</span>
                <strong>{displayMoney(run.metadata?.loan_amount)}</strong>
              </div>
              <div>
                <span>Purpose</span>
                <strong className="capitalize">{displayPurpose(run.metadata?.loan_purpose)}</strong>
              </div>
            </div>

            <div className="review-rationale">
              <span>Model rationale</span>
              <p>{recommendation.rationale}</p>
            </div>

            {policyEvidence && (
              <div className={`policy-proof ${policyEvidence.live ? "policy-proof--live" : "policy-proof--fallback"}`}>
                <div>
                  <span>Policy source</span>
                  <strong>
                    {policyEvidence.live
                      ? "Unity Catalog → Lakebase synced table"
                      : "Fixture policy fallback"}
                  </strong>
                </div>
                <div>
                  <span>Approval limits</span>
                  <strong>
                    {policyEvidence.minCreditScore === null
                      ? "Thresholds unavailable"
                      : `Credit ≥ ${policyEvidence.minCreditScore} · DTI ≤ ${((policyEvidence.maxDti ?? 0) * 100).toFixed(1)}%`}
                  </strong>
                </div>
                <code>{policyEvidence.source}</code>
              </div>
            )}
          </>
        ) : (
          <div className="review-pending">
            <span className="review-pending__pulse" />
            Draft recommendation is being replicated to the review view.
          </div>
        )}

        <div className="review-form">
          <label className="field">
            <span>Reviewer</span>
            <input
              value={reviewerId}
              onChange={(event) => setReviewerId(event.target.value)}
              disabled={submitted}
            />
          </label>
          <label className="field review-form__rationale">
            <span>Decision rationale</span>
            <textarea
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
              rows={1}
              disabled={submitted}
            />
          </label>
        </div>

        {error && <div className="inline-error">{error}</div>}

        <div className="review-actions">
          {submitted ? (
            <div className="review-submitted">
              <span aria-hidden="true">✓</span>
              {liveData
                ? "Temporal accepted the signal. Waiting for the workflow to resume and Lakebase to record the reviewed outcome…"
                : "The synthetic review was accepted. Updating the demo workflow outcome…"}
            </div>
          ) : (
            <>
              <button
                className="btn btn--accept"
                disabled={!recommendation || !reviewerId.trim() || !rationale.trim() || !!submitting}
                onClick={() => decide("approve")}
              >
                {submitting === "approve" ? "Submitting…" : "Approve"}
              </button>
              <button
                className="btn btn--deny"
                disabled={!recommendation || !reviewerId.trim() || !rationale.trim() || !!submitting}
                onClick={() => decide("deny")}
              >
                {submitting === "deny" ? "Submitting…" : "Deny"}
              </button>
              <button
                className="btn btn--secondary review-actions__more"
                disabled={!recommendation || !reviewerId.trim() || !rationale.trim() || !!submitting}
                onClick={() => decide("request_more_info")}
              >
                {submitting === "request_more_info" ? "Submitting…" : "Request more info"}
              </button>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

export function ReviewAudit({ run, liveData }: { run: WorkflowRun; liveData: boolean }) {
  const decision = run.review_decision;
  if (!decision) return null;
  const label: Record<ReviewDecision, string> = {
    approve: "Recommendation approved",
    deny: "Recommendation denied",
    request_more_info: "More information requested",
  };
  return (
    <section className="review-audit" aria-label="Human review decision">
      <span className="review-audit__icon" aria-hidden="true">
        ✓
      </span>
      <div>
        <span className="eyebrow">Human review recorded</span>
        <strong>{label[decision.decision]}</strong>
        <p>{decision.rationale}</p>
        <div className="review-audit__paths">
          <span>
            {liveData
              ? "Stored in Lakebase · agent_review_decisions"
              : "Synthetic demo · production target is agent_review_decisions"}
          </span>
          <span>
            When enabled · Lakebase CDF (Public Preview) → lb_agent_review_decisions_history
          </span>
        </div>
      </div>
      <span className="review-audit__reviewer">{decision.reviewer_id}</span>
    </section>
  );
}
