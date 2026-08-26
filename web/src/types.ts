export type RunStatus =
  | "pending"
  | "running"
  | "waiting_for_tool"
  | "awaiting_review"
  | "completed"
  | "failed"
  | "cancelled";

export type UnderwritingDecision = "approve" | "decline" | "refer";
export type ReviewDecision = "approve" | "deny" | "request_more_info";

export interface AgentMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  name?: string | null;
  metadata?: Record<string, unknown>;
}

export interface ToolResult {
  tool_call_id: string;
  name:
    | "credit_check"
    | "income_verification"
    | "debt_to_income_calc"
    | "policy_lookup"
    | "save_note";
  success: boolean;
  content: string;
  data: Record<string, unknown>;
  error?: string | null;
}

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface LoanRecommendation {
  decision: UnderwritingDecision;
  rationale: string;
  dti: number;
  credit_score: number;
}

export interface ReviewDecisionPayload {
  reviewer_id: string;
  decision: ReviewDecision;
  rationale: string;
  review_id: string;
  decision_id: string;
  metadata?: Record<string, unknown>;
}

export interface WorkflowRun {
  run_id: string;
  workflow_id: string;
  user_id: string;
  user_request: string;
  status: RunStatus;
  final_response?: string | null;
  turns: number;
  tool_results: ToolResult[];
  usage: TokenUsage;
  created_at: string;
  updated_at: string;
  messages: AgentMessage[];
  metadata?: Record<string, unknown>;
  recommendation?: LoanRecommendation | null;
  review_decision?: ReviewDecisionPayload | null;
}

export interface StartWorkflowRequest {
  user_request: string;
  user_id: string;
  applicant_id: string;
  loan_amount: number;
  loan_purpose: string;
  model: string;
  max_turns?: number;
  demo_model_failure_attempts?: number;
  metadata?: Record<string, unknown>;
}

export interface StartWorkflowResponse {
  run: WorkflowRun;
}

export interface ReviewSubmissionResponse {
  accepted: boolean;
  run_id: string;
  workflow_id: string;
  decision: ReviewDecision;
}

export interface RuntimeConfig {
  temporal_ui_url: string;
  temporal_namespace: string;
  demo_controls_enabled: boolean;
}

export interface DemoControlResponse {
  accepted: boolean;
  control_id: string;
  workflow_id: string;
  message: string;
}

export interface RunMetrics {
  total_runs: number;
  completed_runs: number;
  success_rate: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  counts_by_status: Partial<Record<RunStatus, number>>;
}

export interface RetryMetric {
  activity_type: string;
  attempt_count: number;
  retry_count: number;
  last_attempt_completed_at?: string | null;
}

export interface CostMetric {
  bucket_start: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  turn_count: number;
}

export interface OpsMetrics {
  runs: RunMetrics;
  retries: RetryMetric[];
  cost: CostMetric[];
}
