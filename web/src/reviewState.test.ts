import { describe, expect, it } from "vitest";
import { reconcilePendingReview, type PendingReviewSubmission } from "./reviewState";
import type { ReviewDecisionPayload, WorkflowRun } from "./types";

function run(overrides: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    run_id: "run-1",
    workflow_id: "workflow-1",
    user_id: "reviewer@example.com",
    user_request: "Underwrite this loan",
    status: "awaiting_review",
    turns: 5,
    tool_results: [],
    usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
    created_at: "2026-08-26T16:00:00Z",
    updated_at: "2026-08-26T16:01:00Z",
    messages: [],
    metadata: { review: { status: "pending", round: 5 } },
    review_decision: null,
    ...overrides,
  };
}

function pending(decision: ReviewDecisionPayload["decision"]): PendingReviewSubmission {
  return {
    payload: {
      reviewer_id: "reviewer@example.com",
      decision,
      rationale: "Reviewed",
      review_id: "run-1-review-5",
      decision_id: "run-1-review-5:decision-1",
    },
    acceptedAt: "2026-08-26T16:02:00Z",
  };
}

describe("reconcilePendingReview", () => {
  it("does not let a stale poll reopen an accepted approval", () => {
    const result = reconcilePendingReview(run(), pending("approve"));

    expect(result.keepPending).toBe(true);
    expect(result.run.status).toBe("completed");
    expect(result.run.review_decision?.decision).toBe("approve");
  });

  it("holds the accepted result while Lakebase still reports awaiting review", () => {
    const submission = pending("deny");
    const result = reconcilePendingReview(
      run({ review_decision: submission.payload }),
      submission
    );

    expect(result.keepPending).toBe(true);
    expect(result.run.status).toBe("completed");
    expect(result.run.review_decision?.decision).toBe("deny");
  });

  it("releases the optimistic result after the recorded decision advances", () => {
    const submission = pending("approve");
    const polled = run({ status: "running", review_decision: submission.payload });
    const result = reconcilePendingReview(polled, submission);

    expect(result).toEqual({ run: polled, keepPending: false });
  });

  it("allows a later review round to reopen after requesting more information", () => {
    const polled = run({
      turns: 6,
      metadata: { review: { status: "pending", round: 6 } },
    });
    const result = reconcilePendingReview(polled, pending("request_more_info"));

    expect(result).toEqual({ run: polled, keepPending: false });
  });
});
