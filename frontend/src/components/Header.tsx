"use client";

export default function Header() {
  const now = new Date();
  const formatted = now.toLocaleDateString("en-US", {
    month: "short", day: "2-digit", year: "numeric",
  }) + " · " + now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });

  return (
    <header className="border-b-2 border-[var(--color-ws-gray)] pb-4 mb-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <span className="text-2xl font-[900] tracking-tight text-black" style={{ fontFamily: "'Archivo', sans-serif" }}>
            Worksuite
          </span>
          <div className="w-px h-6 bg-[var(--color-ws-gray)]" />
          <span className="text-lg font-bold text-black">OMC Funding Tracker</span>
          <span className="badge badge-orange">Read-Only</span>
        </div>
        <span className="text-xs text-gray-400">{formatted}</span>
      </div>
    </header>
  );
}
