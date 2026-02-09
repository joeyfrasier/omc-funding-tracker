"use client";

interface MetricCardProps {
  label: string;
  value: string | number;
  delta?: string;
  deltaColor?: "green" | "red" | "gray";
}

export default function MetricCard({ label, value, delta, deltaColor = "gray" }: MetricCardProps) {
  const colorMap = { green: "text-[var(--color-ws-green)]", red: "text-[var(--color-ws-orange)]", gray: "text-gray-500" };

  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      {delta && <div className={`metric-delta ${colorMap[deltaColor]}`}>{delta}</div>}
    </div>
  );
}
