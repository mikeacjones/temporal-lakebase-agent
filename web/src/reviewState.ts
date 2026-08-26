import type { ReviewDecisionPayload, WorkflowRun } from "./types";

export interface PendingReviewSubmission {
  payload: ReviewDecisionPayload;
  acceptedAt: string;
}

interface ReconciledReviewRun {
  run: WorkflowRun;
  keepPending: boolean;
}

function reviewRoundFromId(reviewId: string): number | null {
  const match = reviewId.match(/-review-(\d+)$/);
  return match ? Number(match[1]) : null;
}

function currentReviewRound(run: WorkflowRun): number | null {
  const review = run.metadata?.review;
  if (typeof review !== "object" || review === null || !("round" in review)) return null;
  return typeof review.round === "number" ? review.round : null;
}

export function reconcilePendingReview(
  polledRun: WorkflowRun,
  pending: PendingReviewSubmission | undefined
): ReconciledReviewRun {
  if (!pending) return { run: polledRun, keepPending: false };

  const submittedRound = reviewRoundFromId(pending.payload.review_id);
  const polledRound = currentReviewRound(polledRun);
  const movedToNextReviewRound =
    pending.payload.decision === "request_more_info" &&
    submittedRound !== null &&
    polledRound !== null &&
    polledRound > submittedRound;
  const terminal = ["completed", "failed", "cancelled"].includes(polledRun.status);
  const recordedDecision = polledRun.review_decision;
  const conflictingDecision =
    recordedDecision !== null &&
    recordedDecision !== undefined &&
    recordedDecision.decision_id !== pending.payload.decision_id;
  const acceptedDecisionAdvanced =
    recordedDecision?.decision_id === pending.payload.decision_id &&
    polledRun.status !== "awaiting_review";

  if (terminal || movedToNextReviewRound || conflictingDecision || acceptedDecisionAdvanced) {
    return { run: polledRun, keepPending: false };
  }

  return {
    run: {
      ...polledRun,
      status: pending.payload.decision === "request_more_info" ? "running" : "completed",
      review_decision: pending.payload,
      updated_at: pending.acceptedAt,
    },
    keepPending: true,
  };
}
