import { useEffect, useMemo, useState } from "react";
import { fetchOpsMetrics } from "../api";
import type { CostMetric, OpsMetrics, RunStatus } from "../types";

const STATUS_ORDER: RunStatus[] = [
  "awaiting_review",
  "running",
  "waiting_for_tool",
  "pending",
  "completed",
  "failed",
  "cancelled",
];

const STATUS_LABEL: Record<RunStatus, string> = {
  pending: "Pending",
  running: "Running",
  waiting_for_tool: "Waiting for tool",
  awaiting_review: "Awaiting review",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

function formatCount(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function formatLatency(value: number | null): string {
  if (value === null) return "—";
  if (value < 1000) return `${Math.round(value)}ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}s`;
}

function shortTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric" }).format(new Date(value));
}

interface TokenChartProps {
  rows: CostMetric[];
}

function TokenChart({ rows }: TokenChartProps) {
  const chart = useMemo(() => {
    if (rows.length < 2) return null;
    const width = 720;
    const height = 220;
    const inset = { top: 14, right: 14, bottom: 34, left: 48 };
    const innerWidth = width - inset.left - inset.right;
    const innerHeight = height - inset.top - inset.bottom;
    const maximum = Math.max(...rows.map((row) => row.total_tokens), 1);
    const ceiling = Math.ceil(maximum / 500) * 500 || 500;
    const points = rows.map((row, index) => ({
      x: inset.left + (index / (rows.length - 1)) * innerWidth,
      y: inset.top + innerHeight - (row.total_tokens / ceiling) * innerHeight,
      row,
    }));
    return { width, height, inset, innerWidth, innerHeight, ceiling, points };
  }, [rows]);

  if (!chart) {
    return <div className="chart-empty">Token trend appears after at least two metric buckets.</div>;
  }

  const path = chart.points.map((point) => `${point.x},${point.y}`).join(" ");
  const labelIndexes = new Set([0, Math.floor((rows.length - 1) / 2), rows.length - 1]);

  return (
    <div className="token-chart">
      <svg
        viewBox={`0 0 ${chart.width} ${chart.height}`}
        role="img"
        aria-label="Total input and output tokens by hour"
      >
        {[0, 0.5, 1].map((fraction) => {
          const y = chart.inset.top + chart.innerHeight * fraction;
          const value = Math.round(chart.ceiling * (1 - fraction));
          return (
            <g key={fraction}>
              <line
                className="token-chart__grid"
                x1={chart.inset.left}
                x2={chart.inset.left + chart.innerWidth}
                y1={y}
                y2={y}
              />
              <text className="token-chart__axis" x={chart.inset.left - 9} y={y + 4} textAnchor="end">
                {formatCount(value)}
              </text>
            </g>
          );
        })}
        <polyline className="token-chart__line" points={path} />
        {chart.points.map((point, index) => (
          <g key={point.row.bucket_start}>
            <circle className="token-chart__point" cx={point.x} cy={point.y} r={4}>
              <title>
                {shortTime(point.row.bucket_start)}: {formatCount(point.row.total_tokens)} tokens across{" "}
                {formatCount(point.row.turn_count)} turns
              </title>
            </circle>
            {labelIndexes.has(index) && (
              <text
                className="token-chart__axis"
                x={point.x}
                y={chart.height - 8}
                textAnchor={index === 0 ? "start" : index === rows.length - 1 ? "end" : "middle"}
              >
                {shortTime(point.row.bucket_start)}
              </text>
            )}
          </g>
        ))}
      </svg>
    </div>
  );
}

interface OpsDashboardProps {
  onDataModeChange: (demo: boolean) => void;
}

export function OpsDashboard({ onDataModeChange }: OpsDashboardProps) {
  const [metrics, setMetrics] = useState<OpsMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const result = await fetchOpsMetrics();
        if (cancelled) return;
        setMetrics(result.metrics);
        setUpdatedAt(new Date());
        setError(null);
        onDataModeChange(result.demo);
      } catch (err: unknown) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load operational metrics");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const interval = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [onDataModeChange]);

  const statusRows = metrics
    ? STATUS_ORDER.map((status) => ({
        status,
        count: metrics.runs.counts_by_status[status] ?? 0,
      })).filter((row) => row.count > 0)
    : [];
  const maxStatusCount = Math.max(...statusRows.map((row) => row.count), 1);
  const maxRetries = Math.max(...(metrics?.retries.map((row) => row.retry_count) ?? []), 1);
  const awaitingReview = metrics?.runs.counts_by_status.awaiting_review ?? 0;
  const totalTokens = metrics?.cost.reduce((sum, row) => sum + row.total_tokens, 0) ?? 0;
  const totalTurns = metrics?.cost.reduce((sum, row) => sum + row.turn_count, 0) ?? 0;

  return (
    <main className="ops">
      <header className="ops__header">
        <div>
          <span className="eyebrow">Temporal + Lakebase telemetry</span>
          <h1>Live operations</h1>
          <p>
            Temporal emits execution telemetry; Lakebase serves this operational view from
            agent_ops every five seconds.
          </p>
        </div>
        <div className="freshness" aria-live="polite">
          <span className="freshness__pulse" />
          {loading && !updatedAt
            ? "Connecting…"
            : `Live · refreshed ${updatedAt?.toLocaleTimeString([], {
                hour: "numeric",
                minute: "2-digit",
                second: "2-digit",
              })}`}
        </div>
      </header>

      {error && <div className="error ops__error">{error}</div>}

      {!metrics ? (
        <div className="ops-loading" aria-label="Loading metrics">
          <div />
          <div />
          <div />
        </div>
      ) : (
        <>
          <section className="ops-kpis" aria-label="Key workflow metrics">
            <article className="kpi-card kpi-card--review">
              <span>Awaiting human review</span>
              <strong>{formatCount(awaitingReview)}</strong>
              <small>Durably paused workflows</small>
            </article>
            <article className="kpi-card">
              <span>Success rate</span>
              <strong>{Math.round(metrics.runs.success_rate * 100)}%</strong>
              <small>Completed ÷ terminal runs</small>
            </article>
            <article className="kpi-card">
              <span>Median latency</span>
              <strong>{formatLatency(metrics.runs.p50_latency_ms)}</strong>
              <small>p50 end-to-end</small>
            </article>
            <article className="kpi-card">
              <span>Tail latency</span>
              <strong>{formatLatency(metrics.runs.p95_latency_ms)}</strong>
              <small>p95 end-to-end</small>
            </article>
          </section>

          <div className="ops-grid">
            <section className="ops-card" aria-labelledby="outcomes-heading">
              <header className="ops-card__head">
                <div>
                  <h2 id="outcomes-heading">Run outcomes</h2>
                  <p>{formatCount(metrics.runs.total_runs)} workflows · all observed statuses</p>
                </div>
                <span className="source-badge">Lakebase · agent_workflow_metrics</span>
              </header>
              {statusRows.length ? (
                <div className="bar-list">
                  {statusRows.map((row) => (
                    <div className="bar-row" key={row.status}>
                      <div className="bar-row__label">
                        <span className={`status-dot status-dot--${row.status}`} />
                        <span>{STATUS_LABEL[row.status]}</span>
                      </div>
                      <div className="bar-row__track">
                        <span
                          className={`bar-row__fill bar-row__fill--${row.status}`}
                          style={{ width: `${Math.max(5, (row.count / maxStatusCount) * 100)}%` }}
                        />
                      </div>
                      <strong>{formatCount(row.count)}</strong>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="chart-empty">No workflow metrics have been recorded yet.</div>
              )}
            </section>

            <section className="ops-card" aria-labelledby="retries-heading">
              <header className="ops-card__head">
                <div>
                  <h2 id="retries-heading">Activity retries</h2>
                  <p>Automatic recovery by activity type</p>
                </div>
                <span className="source-badge">Lakebase · agent_activity_retry_counts</span>
              </header>
              {metrics.retries.length ? (
                <div className="retry-list">
                  {metrics.retries.map((row) => (
                    <div className="retry-row" key={row.activity_type}>
                      <div className="retry-row__meta">
                        <span>{row.activity_type.replace(/_/g, " ")}</span>
                        <span>
                          {formatCount(row.retry_count)} retries · {formatCount(row.attempt_count)} attempts
                        </span>
                      </div>
                      <div className="retry-row__track">
                        <span
                          className={row.retry_count ? "retry-row__fill" : "retry-row__fill retry-row__fill--zero"}
                          style={{
                            width: row.retry_count
                              ? `${Math.max(8, (row.retry_count / maxRetries) * 100)}%`
                              : "3px",
                          }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="chart-empty">No activity attempts have been recorded yet.</div>
              )}
            </section>

            <section className="ops-card ops-card--wide" aria-labelledby="tokens-heading">
              <header className="ops-card__head">
                <div>
                  <h2 id="tokens-heading">Token usage trend</h2>
                  <p>Input + output tokens by hour · trailing 24 hours</p>
                </div>
                <div className="token-summary">
                  <span>{formatCount(totalTokens)} tokens</span>
                  <small>{formatCount(totalTurns)} model turns</small>
                </div>
              </header>
              <TokenChart rows={metrics.cost} />
              <footer className="ops-card__foot">
                <span className="legend-line" aria-hidden="true" />
                Total tokens
                <span className="ops-card__source">Lakebase · agent_turn_metrics</span>
              </footer>
            </section>
          </div>
        </>
      )}
    </main>
  );
}
