"use client";

import { useEffect, useRef } from "react";

type Series = { name: string; data: number[]; type?: "bar" | "line"; color?: string };

/** ECharts laddas dynamiskt så att startsidan inte behöver bära biblioteket. */
export function TrendChart({ categories, series, height = 260 }: { categories: string[]; series: Series[]; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let disposed = false;
    let chart: { setOption: (o: unknown) => void; resize: () => void; dispose: () => void } | null = null;
    const onResize = () => chart?.resize();
    import("echarts").then((echarts) => {
      if (disposed || !el.current) return;
      chart = echarts.init(el.current, undefined, { renderer: "svg" });
      chart.setOption({
        grid: { left: 60, right: 16, top: 30, bottom: 30 },
        legend: { top: 0, textStyle: { fontSize: 11 } },
        tooltip: {
          trigger: "axis",
          valueFormatter: (v: number) => `${Math.round(v).toLocaleString("sv-SE")} kr`,
        },
        xAxis: { type: "category", data: categories, axisLabel: { fontSize: 10 } },
        yAxis: {
          type: "value",
          axisLabel: { fontSize: 10, formatter: (v: number) => `${Math.round(v / 1000).toLocaleString("sv-SE")} tkr` },
        },
        series: series.map((s) => ({
          name: s.name,
          type: s.type ?? "bar",
          data: s.data,
          smooth: s.type === "line",
          itemStyle: s.color ? { color: s.color } : undefined,
          lineStyle: s.color ? { color: s.color, width: 2 } : undefined,
        })),
      });
      window.addEventListener("resize", onResize);
    });
    return () => {
      disposed = true;
      window.removeEventListener("resize", onResize);
      chart?.dispose();
    };
  }, [categories, series]);
  return <div ref={el} style={{ height }} role="img" aria-label="Diagram över månadsutfall" />;
}
