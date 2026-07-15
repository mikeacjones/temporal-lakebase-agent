import type { WorkflowRun } from "../types";
import { TemporalMark } from "./TemporalLogo";

const TEMPORAL_UI = import.meta.env.VITE_TEMPORAL_UI ?? "http://localhost:8233";
const TEMPORAL_NAMESPACE = import.meta.env.VITE_TEMPORAL_NAMESPACE ?? "default";

const STATUS_LABEL: Record<WorkflowRun["status"], string> = {
  pending: "Pending",
  running: "Running",
  waiting_for_tool: "Waiting for tool",
  awaiting_review: "Awaiting review",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

interface RunCardProps {
  run: WorkflowRun;
  selected: boolean;
  onSelect: (run: WorkflowRun) => void;
}

export function RunCard({ run, selected, onSelect }: RunCardProps) {
  const workflowUrl = `${TEMPORAL_UI}/namespaces/${encodeURIComponent(TEMPORAL_NAMESPACE)}/workflows/${encodeURIComponent(run.workflow_id)}`;
  const applicantId =
    typeof run.metadata?.applicant_id === "string" ? run.metadata.applicant_id : null;

  return (
    <article className={`run-card ${selected ? "run-card--selected" : ""}`}>
      <button className="run-card__button" onClick={() => onSelect(run)}>
        <span className={`run-card__status status-dot status-dot--${run.status}`} />
        <span className="run-card__body">
          <span className="run-card__title">{run.user_request}</span>
          {applicantId && <span className="run-card__applicant">{applicantId}</span>}
          <span className="run-card__meta">
            <span className="mono">{run.run_id}</span>
            <span>{formatTime(run.updated_at)}</span>
          </span>
          <span className="run-card__label">{STATUS_LABEL[run.status]}</span>
        </span>
      </button>
      <a className="run-card__link" href={workflowUrl} target="_blank" rel="noreferrer">
        <TemporalMark size={14} />
      </a>
    </article>
  );
}
