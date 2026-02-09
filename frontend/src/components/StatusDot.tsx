"use client";

export default function StatusDot({ status }: { status: "ok" | "warn" | "error" }) {
  return <span className={`status-dot status-${status}`} />;
}
