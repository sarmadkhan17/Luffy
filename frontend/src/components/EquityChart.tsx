import { useEffect, useRef } from "react";
import { createChart, AreaSeries, ColorType } from "lightweight-charts";
export default function EquityChart({
  points,
}: {
  points: { time: string; value: number }[];
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      height: 235,
      layout: {
        background: { type: ColorType.Solid, color: "#101b27" },
        textColor: "#a4b5c8",
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: "#182635" },
        horzLines: { color: "#203040" },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      handleScroll: false,
      handleScale: false,
    });
    const series = chart.addSeries(AreaSeries, {
      lineColor: "#76d3c1",
      topColor: "#76d3c12c",
      bottomColor: "#76d3c100",
      lineWidth: 2,
      priceLineVisible: false,
    });
    series.setData(points);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);
  return (
    <div
      className="chart"
      ref={ref}
      role="group"
      aria-label="Synthetic account equity chart; values available in accessible chart data"
    />
  );
}
