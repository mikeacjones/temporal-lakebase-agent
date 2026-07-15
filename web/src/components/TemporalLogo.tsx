interface MarkProps {
  size?: number;
  className?: string;
  variant?: "light" | "dark";
}

export function TemporalMark({ size = 28, className, variant = "dark" }: MarkProps) {
  return (
    <img
      className={className}
      src={`/temporal-symbol-${variant}.png`}
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
    />
  );
}

interface LogoProps {
  size?: number;
  tone?: "light" | "dark";
}

export function TemporalLogo({ size = 26, tone = "light" }: LogoProps) {
  const wordColor = tone === "light" ? "var(--off-white)" : "var(--space-black)";
  return (
    <span className="logo" aria-label="Temporal">
      <TemporalMark className="logo__mark" size={size} variant="light" />
      <span className="logo__wordmark" style={{ color: wordColor }}>
        temporal
      </span>
    </span>
  );
}
