"use client";

interface TabsProps {
  tabs: string[];
  active: number;
  onChange: (index: number) => void;
}

export default function Tabs({ tabs, active, onChange }: TabsProps) {
  return (
    <nav className="tab-nav">
      {tabs.map((tab, i) => (
        <button
          key={tab}
          className={`tab-item ${i === active ? "active" : ""}`}
          onClick={() => onChange(i)}
        >
          {tab}
        </button>
      ))}
    </nav>
  );
}
