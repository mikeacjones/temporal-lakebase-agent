import type {
  CostMetric,
  LoanRecommendation,
  OpsMetrics,
  ReviewDecisionPayload,
  ReviewSubmissionResponse,
  RetryMetric,
  RunMetrics,
  StartWorkflowRequest,
  StartWorkflowResponse,
  WorkflowRun,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";
const DEMO_ONLY =
  import.meta.env.VITE_DEMO_MODE === "true" || import.meta.env.VITE_USE_API === "false";
const ALLOW_DEMO_FALLBACK = import.meta.env.VITE_ALLOW_DEMO_FALLBACK === "true";

const hoursAgo = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString();

let demoCounter = 4;
let demoRuns: WorkflowRun[] = [
  {
    run_id: "demo-loan-003",
    workflow_id: "lakebase-agent-demo-loan-003",
    user_id: "loan-officer@example.com",
    user_request:
      "Underwrite a $25,000 personal loan for applicant-borderline (debt consolidation).",
    status: "awaiting_review",
    final_response: null,
    turns: 5,
    tool_results: [
      {
        tool_call_id: "borderline-credit",
        name: "credit_check",
        success: true,
        content: "Credit score 665; 5 open trade lines; one 30-day-late flag.",
        data: {
          credit_score: 665,
          open_trade_lines: 5,
          delinquency_flags: ["one_30_day_late"],
          existing_monthly_debt: 2400,
        },
      },
      {
        tool_call_id: "borderline-income",
        name: "income_verification",
        success: true,
        content: "Verified annual income is $76,000 against $82,000 stated.",
        data: {
          stated_annual_income: 82000,
          verified_annual_income: 76000,
          employment_status: "full_time",
          verification_status: "verified",
        },
      },
      {
        tool_call_id: "borderline-dti",
        name: "debt_to_income_calc",
        success: true,
        content: "Projected debt-to-income ratio is 50.9%.",
        data: { dti: 0.5091, existing_monthly_debt: 2400 },
      },
      {
        tool_call_id: "borderline-policy",
        name: "policy_lookup",
        success: true,
        content: "Credit and DTI are inside the manual-review band.",
        data: {
          policy_source: "fixture_fallback",
          fallback_reason: "synthetic_demo",
          policy: {
            loan_purpose: "debt_consolidation",
            min_credit_score: 690,
            max_dti: 0.45,
            decline_below_credit_score: 620,
            decline_above_dti: 0.55,
          },
          decision: "refer",
          credit_score: 665,
          dti: 0.5091,
        },
      },
    ],
    usage: { input_tokens: 1284, output_tokens: 318, total_tokens: 1602 },
    created_at: hoursAgo(0.25),
    updated_at: hoursAgo(0.04),
    metadata: {
      applicant_id: "applicant-borderline",
      loan_amount: 25000,
      loan_purpose: "debt_consolidation",
      review: { status: "pending", round: 5 },
    },
    recommendation: {
      decision: "refer",
      rationale:
        "Credit quality and projected DTI are inside the manual-review band. Verified income and a non-material delinquency flag support an exception review.",
      dti: 0.5091,
      credit_score: 665,
    },
    review_decision: null,
    messages: [
      {
        role: "user",
        content: "Underwrite this $25,000 debt-consolidation loan.",
      },
      {
        role: "assistant",
        content: "I’ll verify credit, income, affordability, and policy eligibility.",
      },
      {
        role: "tool",
        name: "credit_check",
        content: "Credit score 665; 5 open trade lines; one 30-day-late flag.",
      },
      {
        role: "tool",
        name: "income_verification",
        content: "Verified annual income is $76,000 against $82,000 stated.",
      },
      {
        role: "tool",
        name: "debt_to_income_calc",
        content: "Projected debt-to-income ratio is 50.9%.",
      },
      {
        role: "tool",
        name: "policy_lookup",
        content: "Credit passes; DTI is inside the manual-review band.",
      },
      {
        role: "assistant",
        content: "Recommendation: REFER for underwriter review.",
        metadata: {
          recommendation: {
            decision: "refer",
            rationale:
              "Credit quality is acceptable, but projected DTI is inside the manual-review band.",
            dti: 0.5091,
            credit_score: 665,
          },
        },
      },
    ],
  },
  {
    run_id: "demo-loan-002",
    workflow_id: "lakebase-agent-demo-loan-002",
    user_id: "loan-officer@example.com",
    user_request: "Underwrite a $25,000 personal loan for applicant-clean (debt consolidation).",
    status: "completed",
    final_response: "The underwriter accepted the model recommendation. Loan recommendation: approve.",
    turns: 5,
    tool_results: [],
    usage: { input_tokens: 1090, output_tokens: 244, total_tokens: 1334 },
    created_at: hoursAgo(3.2),
    updated_at: hoursAgo(2.9),
    metadata: {
      applicant_id: "applicant-clean",
      loan_amount: 25000,
      loan_purpose: "debt_consolidation",
    },
    recommendation: {
      decision: "approve",
      rationale: "Strong credit, stable verified income, and projected DTI below policy limits.",
      dti: 0.286,
      credit_score: 756,
    },
    review_decision: {
      reviewer_id: "underwriter@example.com",
      decision: "approve",
      rationale: "Evidence supports the recommendation and policy checks are satisfied.",
      review_id: "demo-loan-002-review-5",
      decision_id: "demo-loan-002-review-5:seed",
    },
    messages: [
      { role: "user", content: "Underwrite this $25,000 debt-consolidation loan." },
      {
        role: "assistant",
        content: "Recommendation: APPROVE. Strong credit, stable income, and DTI within policy.",
      },
    ],
  },
  {
    run_id: "demo-loan-001",
    workflow_id: "lakebase-agent-demo-loan-001",
    user_id: "loan-officer@example.com",
    user_request: "Underwrite a $40,000 personal loan for applicant-decline (home improvement).",
    status: "completed",
    final_response: "The underwriter accepted the model recommendation. Loan recommendation: decline.",
    turns: 5,
    tool_results: [],
    usage: { input_tokens: 1170, output_tokens: 271, total_tokens: 1441 },
    created_at: hoursAgo(7.8),
    updated_at: hoursAgo(7.4),
    metadata: {
      applicant_id: "applicant-decline",
      loan_amount: 40000,
      loan_purpose: "home_improvement",
    },
    recommendation: {
      decision: "decline",
      rationale: "Credit score and projected DTI both fall outside current policy limits.",
      dti: 0.574,
      credit_score: 598,
    },
    review_decision: {
      reviewer_id: "underwriter@example.com",
      decision: "approve",
      rationale: "Policy failures were confirmed against the supporting tool evidence.",
      review_id: "demo-loan-001-review-5",
      decision_id: "demo-loan-001-review-5:seed",
    },
    messages: [
      { role: "user", content: "Underwrite this $40,000 home-improvement loan." },
      {
        role: "assistant",
        content: "Recommendation: DECLINE. Credit and affordability checks fail policy.",
      },
    ],
  },
];

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail =
      body && typeof body === "object" && "detail" in body ? String(body.detail) : "";
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function fetchRuns(): Promise<{ runs: WorkflowRun[]; demo: boolean }> {
  if (DEMO_ONLY) {
    return { runs: [...demoRuns], demo: true };
  }
  try {
    const data = await json<{ runs: WorkflowRun[] }>("/api/runs");
    return { runs: data.runs, demo: false };
  } catch (error) {
    if (!ALLOW_DEMO_FALLBACK) throw error;
    return { runs: [...demoRuns], demo: true };
  }
}

export async function startWorkflow(
  request: StartWorkflowRequest,
  allowDemoFallback = false
): Promise<StartWorkflowResponse & { demo: boolean }> {
  if (DEMO_ONLY || allowDemoFallback) {
    const run = createDemoRun(request);
    demoRuns = [run, ...demoRuns];
    return { run, demo: true };
  }
  try {
    const data = await json<StartWorkflowResponse>("/api/workflows", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    return { ...data, demo: false };
  } catch (error) {
    if (!allowDemoFallback) throw error;
    const run = createDemoRun(request);
    demoRuns = [run, ...demoRuns];
    return { run, demo: true };
  }
}

export async function submitReview(
  runId: string,
  payload: ReviewDecisionPayload,
  allowDemoFallback = false
): Promise<ReviewSubmissionResponse & { demo: boolean }> {
  if (DEMO_ONLY || allowDemoFallback) {
    const run = updateDemoReview(runId, payload);
    return {
      accepted: true,
      run_id: run.run_id,
      workflow_id: run.workflow_id,
      decision: payload.decision,
      demo: true,
    };
  }
  try {
    const data = await json<ReviewSubmissionResponse>(`/api/runs/${runId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return { ...data, demo: false };
  } catch (error) {
    if (!allowDemoFallback) throw error;
    const run = updateDemoReview(runId, payload);
    return {
      accepted: true,
      run_id: run.run_id,
      workflow_id: run.workflow_id,
      decision: payload.decision,
      demo: true,
    };
  }
}

export async function fetchOpsMetrics(): Promise<{ metrics: OpsMetrics; demo: boolean }> {
  if (DEMO_ONLY) return { metrics: demoMetrics(), demo: true };
  try {
    const [runs, retries, cost] = await Promise.all([
      json<RunMetrics>("/api/metrics/runs"),
      json<RetryMetric[]>("/api/metrics/retries"),
      json<CostMetric[]>("/api/metrics/cost?hours=24"),
    ]);
    return { metrics: { runs, retries, cost }, demo: false };
  } catch (error) {
    if (!ALLOW_DEMO_FALLBACK) throw error;
    return { metrics: demoMetrics(), demo: true };
  }
}

function createDemoRun(request: StartWorkflowRequest): WorkflowRun {
  const now = new Date().toISOString();
  const runId = `demo-loan-${String(demoCounter++).padStart(3, "0")}`;
  const recommendation = demoRecommendation(request.applicant_id);
  const purpose = request.loan_purpose.replace(/_/g, " ");
  return {
    run_id: runId,
    workflow_id: `lakebase-agent-${runId}`,
    user_id: request.user_id,
    user_request: request.user_request,
    status: "awaiting_review",
    final_response: null,
    turns: 5,
    tool_results: [demoPolicyResult(runId, request.loan_purpose, recommendation)],
    usage: { input_tokens: 1160, output_tokens: 282, total_tokens: 1442 },
    created_at: now,
    updated_at: now,
    metadata: {
      ...request.metadata,
      applicant_id: request.applicant_id,
      loan_amount: request.loan_amount,
      loan_purpose: request.loan_purpose,
      review: { status: "pending", round: 5 },
    },
    recommendation,
    review_decision: null,
    messages: [
      {
        role: "user",
        content: `Underwrite a $${request.loan_amount.toLocaleString()} ${purpose} loan for ${request.applicant_id}.`,
      },
      {
        role: "assistant",
        content: `Recommendation: ${recommendation.decision.toUpperCase()}. ${recommendation.rationale}`,
        metadata: { recommendation },
      },
    ],
  };
}

function demoRecommendation(applicantId: string): LoanRecommendation {
  const recommendations: Record<string, LoanRecommendation> = {
    "applicant-clean": {
      decision: "approve",
      rationale: "Strong credit, stable verified income, and projected DTI below policy limits.",
      dti: 0.1749,
      credit_score: 785,
    },
    "applicant-decline": {
      decision: "decline",
      rationale: "Credit score and projected DTI both fall outside current policy limits.",
      dti: 1.0048,
      credit_score: 585,
    },
  };
  return (
    recommendations[applicantId] ?? {
      decision: "refer",
      rationale:
        "The application is inside the manual-review band and needs an underwriter judgment.",
      dti: 0.5091,
      credit_score: 665,
    }
  );
}

function updateDemoReview(runId: string, payload: ReviewDecisionPayload): WorkflowRun {
  const index = demoRuns.findIndex((run) => run.run_id === runId);
  if (index < 0) throw new Error("Run not found");
  const current = demoRuns[index];
  const requestMoreInfo = payload.decision === "request_more_info";
  const followUpRecommendation = requestMoreInfo && current.recommendation
    ? {
        ...current.recommendation,
        rationale: `${current.recommendation.rationale} Additional reviewer question addressed: ${payload.rationale}`,
      }
    : current.recommendation;
  const updated: WorkflowRun = {
    ...current,
    // The calling component shows the brief RUNNING transition optimistically;
    // the next demo poll then reveals the durable second review round.
    status: requestMoreInfo ? "awaiting_review" : "completed",
    turns: requestMoreInfo ? current.turns + 1 : current.turns,
    final_response: requestMoreInfo
      ? null
      : payload.decision === "approve"
        ? `The underwriter accepted the model recommendation. Loan recommendation: ${current.recommendation?.decision ?? "refer"}.`
        : `The underwriter denied the model recommendation. ${payload.rationale}`,
    recommendation: followUpRecommendation,
    review_decision: requestMoreInfo ? null : payload,
    metadata: {
      ...current.metadata,
      review: requestMoreInfo
        ? { status: "pending", round: current.turns + 1 }
        : {
            status: "decided",
            round: current.turns,
            review_id: payload.review_id,
            decision_id: payload.decision_id,
            reviewer_id: payload.reviewer_id,
            decision: payload.decision,
            rationale: payload.rationale,
          },
    },
    updated_at: new Date().toISOString(),
    messages: requestMoreInfo
      ? [
          ...current.messages,
          {
            role: "user",
            content: `Reviewer requested more information: ${payload.rationale}`,
            metadata: { source: "review_signal", reviewer_id: payload.reviewer_id },
          },
          {
            role: "assistant",
            content: `Updated recommendation: ${followUpRecommendation?.decision.toUpperCase() ?? "REFER"}. ${followUpRecommendation?.rationale ?? "Additional information reviewed."}`,
            metadata: followUpRecommendation
              ? { recommendation: followUpRecommendation }
              : undefined,
          },
        ]
      : current.messages,
  };
  demoRuns = demoRuns.map((run, runIndex) => (runIndex === index ? updated : run));
  return updated;
}

function demoPolicyResult(
  runId: string,
  loanPurpose: string,
  recommendation: LoanRecommendation
): WorkflowRun["tool_results"][number] {
  const policies: Record<string, Record<string, number | string>> = {
    debt_consolidation: {
      loan_purpose: "debt_consolidation",
      min_credit_score: 690,
      max_dti: 0.45,
      decline_below_credit_score: 620,
      decline_above_dti: 0.55,
    },
    home_improvement: {
      loan_purpose: "home_improvement",
      min_credit_score: 700,
      max_dti: 0.43,
      decline_below_credit_score: 620,
      decline_above_dti: 0.55,
    },
  };
  return {
    tool_call_id: `${runId}-tool-4`,
    name: "policy_lookup",
    success: true,
    content: `Fixture policy produced a ${recommendation.decision} recommendation.`,
    data: {
      policy_source: "fixture_fallback",
      fallback_reason: "synthetic_demo",
      policy: policies[loanPurpose] ?? {
        loan_purpose: "default",
        min_credit_score: 700,
        max_dti: 0.43,
        decline_below_credit_score: 620,
        decline_above_dti: 0.55,
      },
      ...recommendation,
    },
  };
}

function demoMetrics(): OpsMetrics {
  const counts = demoRuns.reduce<RunMetrics["counts_by_status"]>((result, run) => {
    result[run.status] = (result[run.status] ?? 0) + 1;
    return result;
  }, {});
  const terminal = (counts.completed ?? 0) + (counts.failed ?? 0) + (counts.cancelled ?? 0);
  const cost = Array.from({ length: 12 }, (_, index): CostMetric => {
    const input = 380 + ((index * 137) % 620);
    const output = 90 + ((index * 61) % 210);
    return {
      bucket_start: hoursAgo(11 - index),
      input_tokens: input,
      output_tokens: output,
      total_tokens: input + output,
      turn_count: 1 + (index % 4),
    };
  });
  return {
    runs: {
      total_runs: demoRuns.length,
      completed_runs: counts.completed ?? 0,
      success_rate: terminal ? (counts.completed ?? 0) / terminal : 0,
      p50_latency_ms: 14800,
      p95_latency_ms: 31200,
      counts_by_status: counts,
    },
    retries: [
      {
        activity_type: "income_verification",
        attempt_count: 7,
        retry_count: 2,
        last_attempt_completed_at: hoursAgo(0.7),
      },
      {
        activity_type: "policy_lookup",
        attempt_count: 5,
        retry_count: 1,
        last_attempt_completed_at: hoursAgo(2.3),
      },
      {
        activity_type: "credit_check",
        attempt_count: 4,
        retry_count: 0,
        last_attempt_completed_at: hoursAgo(0.2),
      },
      {
        activity_type: "debt_to_income_calc",
        attempt_count: 4,
        retry_count: 0,
        last_attempt_completed_at: hoursAgo(0.15),
      },
    ],
    cost,
  };
}
