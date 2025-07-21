import React from "react";

export type BarData = { label: string; value: number };

export default function SimpleBarChart({ data, colors }: { data: BarData[]; colors?: string[] }) {
  if (!data || data.length === 0) return null;
  const max = Math.max(...data.map(d => d.value), 1);
  const palette = colors && colors.length > 0
    ? colors
    : [
        "var(--chart-1)",
        "var(--chart-2)",
        "var(--chart-3)",
        "var(--chart-4)",
        "var(--chart-5)"
      ];
  return (
    <div className="space-y-1">
      {data.map((d, i) => (
        <div key={d.label} className="flex items-center gap-2 text-xs">
          <span className="w-20 truncate">{d.label}</span>
          <div className="flex-1 bg-[var(--muted)] rounded">
            <div
              className="h-2 rounded"
              style={{
                width: `${(d.value / max) * 100}%`,
                backgroundColor: palette[i % palette.length]
              }}
            ></div>
          </div>
          <span className="w-6 text-right">{d.value}</span>
        </div>
      ))}
    </div>
  );
}
