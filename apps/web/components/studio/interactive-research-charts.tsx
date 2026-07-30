"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type {
  CandlestickData,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

import {
  MarketChartSeries,
  TradeChartRecord,
} from "@/lib/api";

type EquityLine = {
  id: string;
  label: string;
  color: string;
  points: Array<{
    t: string;
    value: number;
  }>;
};

export function InteractiveTradingChart({
  market,
  trades,
  strategyLabel,
}: {
  market: MarketChartSeries;
  trades: TradeChartRecord[];
  strategyLabel: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef =
    useRef<ISeriesApi<"Candlestick"> | null>(null);
  const selectedPriceLinesRef = useRef<IPriceLine[]>([]);
  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  const [chartReady, setChartReady] = useState(false);
  const [hoverSummary, setHoverSummary] = useState<{
    time: string;
    open: number;
    high: number;
    low: number;
    close: number;
  } | null>(null);

  const candles = useMemo(
    () =>
      dedupeByTime(
        market.candles.map((item) => ({
          time: toTimestamp(item.t),
          open: item.open,
          high: item.high,
          low: item.low,
          close: item.close,
        })),
      ),
    [market.candles],
  );
  const volumes = useMemo(
    () =>
      dedupeByTime(
        market.candles.map((item) => ({
          time: toTimestamp(item.t),
          value: item.volume,
          color:
            item.close >= item.open
              ? "rgba(61,214,176,.35)"
              : "rgba(251,113,133,.35)",
        })),
      ),
    [market.candles],
  );
  const sortedTrades = useMemo(
    () =>
      [...trades].sort(
        (left, right) =>
          Date.parse(left.entry_time) - Date.parse(right.entry_time),
      ),
    [trades],
  );
  const selectedTrade =
    sortedTrades.find((item) => item.trade_id === selectedTradeId) ?? null;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !candles.length) return;
    setChartReady(false);
    let disposed = false;
    let chart: IChartApi | null = null;
    let resizeObserver: ResizeObserver | null = null;
    void import("lightweight-charts").then(
      ({
        CandlestickSeries,
        ColorType,
        CrosshairMode,
        HistogramSeries,
        createChart,
        createSeriesMarkers,
      }) => {
        if (disposed) return;
        chart = createChart(container, {
          width: container.clientWidth,
          height: 430,
          layout: {
            background: { type: ColorType.Solid, color: "transparent" },
            textColor: "rgba(203,213,225,.82)",
            attributionLogo: true,
          },
          grid: {
            vertLines: { color: "rgba(148,163,184,.08)" },
            horzLines: { color: "rgba(148,163,184,.08)" },
          },
          crosshair: { mode: CrosshairMode.Normal },
          rightPriceScale: {
            borderColor: "rgba(148,163,184,.18)",
          },
          timeScale: {
            borderColor: "rgba(148,163,184,.18)",
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 4,
            barSpacing: 8,
            minBarSpacing: 2,
          },
          handleScroll: {
            mouseWheel: true,
            pressedMouseMove: true,
            horzTouchDrag: true,
            vertTouchDrag: false,
          },
          handleScale: {
            axisPressedMouseMove: true,
            mouseWheel: true,
            pinch: true,
          },
          localization: {
            locale: "zh-CN",
          },
        });
        const candleSeries = chart.addSeries(CandlestickSeries, {
          upColor: "#26a69a",
          downColor: "#ef5350",
          borderVisible: false,
          wickUpColor: "#5eead4",
          wickDownColor: "#fda4af",
          priceLineVisible: false,
        });
        candleSeries.setData(candles);
        const volumeSeries = chart.addSeries(HistogramSeries, {
          priceFormat: { type: "volume" },
          priceScaleId: "",
          lastValueVisible: false,
          priceLineVisible: false,
        });
        volumeSeries.priceScale().applyOptions({
          scaleMargins: { top: 0.82, bottom: 0 },
        });
        volumeSeries.setData(volumes);
        const showMarkerText = sortedTrades.length <= 120;
        createSeriesMarkers(
          candleSeries,
          buildTradeMarkers(sortedTrades, showMarkerText),
        );
        chart.timeScale().fitContent();
        chart.subscribeCrosshairMove((event) => {
          if (!event.time) {
            setHoverSummary(null);
            return;
          }
          const candle = event.seriesData.get(candleSeries);
          if (!candle || !("open" in candle)) return;
          setHoverSummary({
            time: formatChartTime(event.time),
            open: candle.open,
            high: candle.high,
            low: candle.low,
            close: candle.close,
          });
        });
        chart.subscribeClick((event) => {
          if (!event.time || !sortedTrades.length) return;
          const clickedAt = timeToUnix(event.time);
          const interval = estimateCandleInterval(candles);
          const closest = nearestTrade(sortedTrades, clickedAt);
          if (closest && closest.distance <= Math.max(interval * 1.5, 3_600)) {
            setSelectedTradeId(closest.trade.trade_id);
          }
        });
        chartRef.current = chart;
        candleSeriesRef.current = candleSeries;
        setChartReady(true);
        setSelectedTradeId((current) =>
          sortedTrades.some((trade) => trade.trade_id === current)
            ? current
            : (sortedTrades.at(-1)?.trade_id ?? null),
        );
        resizeObserver = new ResizeObserver(([entry]) => {
          chart?.applyOptions({ width: Math.floor(entry.contentRect.width) });
        });
        resizeObserver.observe(container);
      },
    );
    return () => {
      disposed = true;
      resizeObserver?.disconnect();
      chart?.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
    };
  }, [candles, sortedTrades, volumes]);

  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    if (!candleSeries) return;
    for (const line of selectedPriceLinesRef.current) {
      candleSeries.removePriceLine(line);
    }
    selectedPriceLinesRef.current = [];
    if (!selectedTrade) return;
    const candidates = [
      {
        price: selectedTrade.entry_price,
        color: "#7dd3fc",
        title: "入场",
        lineStyle: 0,
      },
      {
        price: selectedTrade.exit_price,
        color: "#fbbf24",
        title: "离场",
        lineStyle: 0,
      },
      {
        price: selectedTrade.stop_price,
        color: "#fb7185",
        title: "止损",
        lineStyle: 2,
      },
      {
        price: selectedTrade.take_profit_price,
        color: "#3dd6b0",
        title: "止盈",
        lineStyle: 2,
      },
    ];
    selectedPriceLinesRef.current = candidates.flatMap((candidate) =>
      candidate.price === null
        ? []
        : [
            candleSeries.createPriceLine({
              price: candidate.price,
              color: candidate.color,
              lineWidth: 1,
              lineStyle: candidate.lineStyle,
              axisLabelVisible: true,
              title: candidate.title,
            }),
          ],
    );
    const interval = estimateCandleInterval(candles);
    const entryTime = Date.parse(selectedTrade.entry_time) / 1_000;
    const exitTime = Date.parse(selectedTrade.exit_time) / 1_000;
    chartRef.current?.timeScale().setVisibleRange({
      from: (Math.min(entryTime, exitTime) - interval * 12) as UTCTimestamp,
      to: (Math.max(entryTime, exitTime) + interval * 12) as UTCTimestamp,
    });
  }, [candles, chartReady, selectedTrade]);

  const selectedTradeIndex = selectedTrade
    ? sortedTrades.findIndex((item) => item.trade_id === selectedTrade.trade_id)
    : -1;

  return (
    <section className="rounded-xl border border-white/10 bg-black/10 p-3">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="text-sm font-medium text-slate-200">
            行情K线、成交量与交易点
          </div>
          <div className="mt-1 text-xs leading-5 text-slate-500">
            {strategyLabel} · {market.timeframe}。滚轮或双指缩放，按住拖动平移；
            点击交易点附近可查看对应交易。
            {market.aggregated
              ? " 交易时间保留原始成交时间，标记对齐到最近一根展示K线。"
              : ""}
          </div>
        </div>
        <ChartToolbar chartRef={chartRef} disabled={!chartReady} />
      </div>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-[11px] text-slate-400">
        <LegendMarker color="#3dd6b0" shape="triangle-up" label="多头入场" />
        <LegendMarker color="#fb7185" shape="triangle-down" label="空头入场" />
        <LegendMarker color="#fbbf24" shape="square" label="离场" />
        <span>{sortedTrades.length.toLocaleString("zh-CN")} 笔真实交易</span>
      </div>
      {selectedTrade ? (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2 text-[11px] text-slate-400">
          <span className="text-slate-500">当前交易四条价格线：</span>
          <PriceLineLegend color="#7dd3fc" label="入场" />
          <PriceLineLegend color="#fbbf24" label="离场" />
          {selectedTrade.stop_price !== null ? (
            <PriceLineLegend color="#fb7185" label="止损" dashed />
          ) : null}
          {selectedTrade.take_profit_price !== null ? (
            <PriceLineLegend color="#3dd6b0" label="止盈" dashed />
          ) : null}
        </div>
      ) : null}

      <div
        ref={containerRef}
        className="mt-2 min-h-[430px] w-full touch-pan-y"
        aria-label={`${strategyLabel} 行情K线、成交量和交易点`}
      />

      {hoverSummary ? (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 rounded-lg bg-white/[0.03] px-3 py-2 text-[11px] tabular-nums text-slate-400">
          <span>{hoverSummary.time}</span>
          <span>开 {formatPrice(hoverSummary.open)}</span>
          <span>高 {formatPrice(hoverSummary.high)}</span>
          <span>低 {formatPrice(hoverSummary.low)}</span>
          <span>收 {formatPrice(hoverSummary.close)}</span>
        </div>
      ) : null}

      {sortedTrades.length ? (
        <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
          <label className="block">
            <span className="mb-1 block text-xs text-slate-500">
              查看具体交易
            </span>
            <select
              value={selectedTrade?.trade_id ?? ""}
              onChange={(event) =>
                setSelectedTradeId(event.target.value || null)
              }
              className="min-h-11 w-full rounded-xl border border-white/10 bg-[#071017] px-3 text-sm text-slate-200"
            >
              <option value="">选择一笔交易</option>
              {sortedTrades.map((trade, index) => (
                <option
                  key={`${trade.trade_id}-${trade.entry_time}`}
                  value={trade.trade_id}
                >
                  #{index + 1} {sideLabel(trade.side)} ·{" "}
                  {new Date(trade.entry_time).toLocaleString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                    hour12: false,
                  })}
                </option>
              ))}
            </select>
          </label>
          <div className="flex items-end gap-2">
            <button
              type="button"
              disabled={selectedTradeIndex <= 0}
              onClick={() =>
                setSelectedTradeId(
                  sortedTrades[selectedTradeIndex - 1]?.trade_id ?? null,
                )
              }
              className="min-h-11 rounded-xl border border-white/10 px-3 text-xs text-slate-300 disabled:opacity-40"
            >
              上一笔
            </button>
            <button
              type="button"
              disabled={
                selectedTradeIndex < 0 ||
                selectedTradeIndex >= sortedTrades.length - 1
              }
              onClick={() =>
                setSelectedTradeId(
                  sortedTrades[selectedTradeIndex + 1]?.trade_id ?? null,
                )
              }
              className="min-h-11 rounded-xl border border-white/10 px-3 text-xs text-slate-300 disabled:opacity-40"
            >
              下一笔
            </button>
          </div>
        </div>
      ) : null}

      {selectedTrade ? <TradeDetail trade={selectedTrade} /> : null}
    </section>
  );
}

export function InteractiveEquityChart({
  series,
}: {
  series: EquityLine[];
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [chartReady, setChartReady] = useState(false);
  const [hoverValues, setHoverValues] = useState<{
    time: string;
    values: Array<{ label: string; value: number; color: string }>;
  } | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !series.length) return;
    setChartReady(false);
    let disposed = false;
    let chart: IChartApi | null = null;
    let resizeObserver: ResizeObserver | null = null;
    void import("lightweight-charts").then(
      ({ ColorType, CrosshairMode, LineSeries, LineStyle, createChart }) => {
        if (disposed) return;
        chart = createChart(container, {
          width: container.clientWidth,
          height: 340,
          layout: {
            background: { type: ColorType.Solid, color: "transparent" },
            textColor: "rgba(203,213,225,.82)",
            attributionLogo: true,
          },
          grid: {
            vertLines: { color: "rgba(148,163,184,.08)" },
            horzLines: { color: "rgba(148,163,184,.08)" },
          },
          crosshair: { mode: CrosshairMode.Normal },
          rightPriceScale: {
            borderColor: "rgba(148,163,184,.18)",
          },
          timeScale: {
            borderColor: "rgba(148,163,184,.18)",
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 3,
            minBarSpacing: 1,
          },
          handleScroll: {
            mouseWheel: true,
            pressedMouseMove: true,
            horzTouchDrag: true,
            vertTouchDrag: false,
          },
          handleScale: {
            axisPressedMouseMove: true,
            mouseWheel: true,
            pinch: true,
          },
          localization: {
            locale: "zh-CN",
            priceFormatter: (value: number) =>
              `${((value - 1) * 100).toFixed(2)}%`,
          },
        });
        const chartSeries = series.map((item, index) => {
          const line = chart?.addSeries(LineSeries, {
            color: item.color,
            lineWidth: 2,
            lineStyle:
              index % 3 === 0
                ? LineStyle.Solid
                : index % 3 === 1
                  ? LineStyle.Dashed
                  : LineStyle.Dotted,
            priceLineVisible: false,
            lastValueVisible: true,
            title: item.label,
          });
          line?.setData(
            dedupeByTime(
              item.points.map((point) => ({
                time: toTimestamp(point.t),
                value: point.value,
              })),
            ),
          );
          return { definition: item, line };
        });
        chart.timeScale().fitContent();
        chart.subscribeCrosshairMove((event) => {
          if (!event.time) {
            setHoverValues(null);
            return;
          }
          const values = chartSeries.flatMap(({ definition, line }) => {
            if (!line) return [];
            const data = event.seriesData.get(line);
            return data && "value" in data
              ? [
                  {
                    label: definition.label,
                    value: data.value,
                    color: definition.color,
                  },
                ]
              : [];
          });
          setHoverValues({
            time: formatChartTime(event.time),
            values,
          });
        });
        chartRef.current = chart;
        setChartReady(true);
        resizeObserver = new ResizeObserver(([entry]) => {
          chart?.applyOptions({ width: Math.floor(entry.contentRect.width) });
        });
        resizeObserver.observe(container);
      },
    );
    return () => {
      disposed = true;
      resizeObserver?.disconnect();
      chart?.remove();
      chartRef.current = null;
    };
  }, [series]);

  return (
    <section className="rounded-xl border border-white/10 bg-black/10 p-3">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="text-sm font-medium text-slate-200">
            多策略资金曲线
          </div>
          <div className="mt-1 text-xs leading-5 text-slate-500">
            不同线型与颜色共同区分策略；支持滚轮、双指缩放和拖动平移。“全周期”可随时恢复完整覆盖范围。
          </div>
        </div>
        <ChartToolbar chartRef={chartRef} disabled={!chartReady} />
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
        {series.map((item, index) => (
          <span key={item.id} className="flex items-center gap-1.5">
            <span
              className={
                index % 3 === 0
                  ? "h-0.5 w-4"
                  : index % 3 === 1
                    ? "h-0 w-4 border-t border-dashed"
                    : "h-0 w-4 border-t border-dotted"
              }
              style={{
                color: item.color,
                backgroundColor: index % 3 === 0 ? item.color : undefined,
              }}
            />
            {item.label}
          </span>
        ))}
      </div>
      <div
        ref={containerRef}
        className="mt-2 min-h-[340px] w-full touch-pan-y"
        aria-label="多策略可缩放资金曲线"
      />
      {hoverValues?.values.length ? (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 rounded-lg bg-white/[0.03] px-3 py-2 text-[11px] tabular-nums text-slate-400">
          <span>{hoverValues.time}</span>
          {hoverValues.values.map((item) => (
            <span key={item.label} style={{ color: item.color }}>
              {item.label} {formatSignedPercent(item.value - 1)}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function ChartToolbar({
  chartRef,
  disabled,
}: {
  chartRef: React.MutableRefObject<IChartApi | null>;
  disabled: boolean;
}) {
  return (
    <div
      className="flex max-w-full gap-2 overflow-x-auto pb-1"
      aria-label="图表缩放与平移"
    >
      <ChartButton
        label="左移"
        disabled={disabled}
        onClick={() => panChart(chartRef.current, -0.25)}
      />
      <ChartButton
        label="放大"
        disabled={disabled}
        onClick={() => zoomChart(chartRef.current, 0.6)}
      />
      <ChartButton
        label="缩小"
        disabled={disabled}
        onClick={() => zoomChart(chartRef.current, 1.6)}
      />
      <ChartButton
        label="右移"
        disabled={disabled}
        onClick={() => panChart(chartRef.current, 0.25)}
      />
      <ChartButton
        label="全周期"
        disabled={disabled}
        onClick={() => chartRef.current?.timeScale().fitContent()}
      />
    </div>
  );
}

function ChartButton({
  label,
  disabled,
  onClick,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="min-h-11 shrink-0 rounded-xl border border-white/10 px-3 text-xs text-slate-300 transition hover:border-white/20 hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {label}
    </button>
  );
}

function TradeDetail({ trade }: { trade: TradeChartRecord }) {
  const profitable = (trade.net_return ?? trade.net_pnl ?? 0) >= 0;
  return (
    <div className="mt-3 rounded-xl border border-white/[0.08] bg-white/[0.025] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-medium text-slate-100">
          {sideLabel(trade.side)} · {splitLabel(trade.split)}
        </div>
        <span
          className={
            profitable
              ? "rounded-full bg-emerald-300/10 px-2 py-1 text-xs text-emerald-200"
              : "rounded-full bg-rose-300/10 px-2 py-1 text-xs text-rose-200"
          }
        >
          {trade.net_return !== null
            ? formatSignedPercent(trade.net_return)
            : trade.net_pnl !== null
              ? `${trade.net_pnl >= 0 ? "+" : ""}${trade.net_pnl.toFixed(2)}`
              : "未记录盈亏"}
        </span>
      </div>
      <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
        <TradeMetric
          label="入场"
          value={`${formatDateTime(trade.entry_time)} · ${formatPrice(trade.entry_price)}`}
        />
        <TradeMetric
          label="离场"
          value={`${formatDateTime(trade.exit_time)} · ${formatPrice(trade.exit_price)}`}
        />
        <TradeMetric
          label="止损 / 止盈"
          value={`${nullablePrice(trade.stop_price)} / ${nullablePrice(trade.take_profit_price)}`}
        />
        <TradeMetric
          label="退出原因"
          value={exitReasonLabel(trade.exit_reason)}
        />
      </div>
    </div>
  );
}

function TradeMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-black/10 p-3">
      <div className="text-slate-500">{label}</div>
      <div className="mt-1 break-words leading-5 text-slate-200">{value}</div>
    </div>
  );
}

function LegendMarker({
  color,
  shape,
  label,
}: {
  color: string;
  shape: "triangle-up" | "triangle-down" | "square";
  label: string;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        className={
          shape === "square"
            ? "h-2.5 w-2.5"
            : shape === "triangle-up"
              ? "h-0 w-0 border-x-[5px] border-b-[8px] border-x-transparent"
              : "h-0 w-0 border-x-[5px] border-t-[8px] border-x-transparent"
        }
        style={
          shape === "square"
            ? { backgroundColor: color }
            : shape === "triangle-up"
              ? { borderBottomColor: color }
              : { borderTopColor: color }
        }
      />
      {label}
    </span>
  );
}

function PriceLineLegend({
  color,
  label,
  dashed = false,
}: {
  color: string;
  label: string;
  dashed?: boolean;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        className={
          dashed
            ? "h-0 w-5 border-t border-dashed"
            : "h-0.5 w-5"
        }
        style={{
          color,
          backgroundColor: dashed ? undefined : color,
          borderColor: dashed ? color : undefined,
        }}
      />
      {label}
    </span>
  );
}

function buildTradeMarkers(
  trades: TradeChartRecord[],
  showText: boolean,
): SeriesMarker<Time>[] {
  return trades
    .flatMap((trade) => {
      const long = trade.side === "long";
      const profitable = (trade.net_return ?? trade.net_pnl ?? 0) >= 0;
      return [
        {
          time: toTimestamp(trade.entry_time),
          position: long ? "belowBar" : "aboveBar",
          shape: long ? "arrowUp" : "arrowDown",
          color: long ? "#3dd6b0" : "#fb7185",
          text: showText ? (long ? "多开" : "空开") : undefined,
        },
        {
          time: toTimestamp(trade.exit_time),
          position: long ? "aboveBar" : "belowBar",
          shape: "square",
          color: profitable ? "#fbbf24" : "#fda4af",
          text: showText ? (long ? "多平" : "空平") : undefined,
        },
      ] satisfies SeriesMarker<Time>[];
    })
    .sort((left, right) => timeToUnix(left.time) - timeToUnix(right.time));
}

function nearestTrade(trades: TradeChartRecord[], time: number) {
  let best: { trade: TradeChartRecord; distance: number } | null = null;
  for (const trade of trades) {
    const distance = Math.min(
      Math.abs(Date.parse(trade.entry_time) / 1_000 - time),
      Math.abs(Date.parse(trade.exit_time) / 1_000 - time),
    );
    if (!best || distance < best.distance) best = { trade, distance };
  }
  return best;
}

function estimateCandleInterval(candles: CandlestickData<Time>[]) {
  if (candles.length < 2) return 3_600;
  const intervals = candles
    .slice(1, 20)
    .map(
      (item, index) =>
        timeToUnix(item.time) - timeToUnix(candles[index].time),
    )
    .filter((value) => value > 0);
  return intervals.length
    ? intervals.sort((left, right) => left - right)[
        Math.floor(intervals.length / 2)
      ]
    : 3_600;
}

function zoomChart(chart: IChartApi | null, factor: number) {
  const range = chart?.timeScale().getVisibleLogicalRange();
  if (!chart || !range) return;
  const center = (range.from + range.to) / 2;
  const halfWidth = Math.max(((range.to - range.from) * factor) / 2, 5);
  chart.timeScale().setVisibleLogicalRange({
    from: center - halfWidth,
    to: center + halfWidth,
  });
}

function panChart(chart: IChartApi | null, fraction: number) {
  const range = chart?.timeScale().getVisibleLogicalRange();
  if (!chart || !range) return;
  const movement = (range.to - range.from) * fraction;
  chart.timeScale().setVisibleLogicalRange({
    from: range.from + movement,
    to: range.to + movement,
  });
}

function dedupeByTime<T extends { time: Time }>(items: T[]): T[] {
  return Array.from(
    new Map(
      items
        .filter((item) => Number.isFinite(timeToUnix(item.time)))
        .sort((left, right) => timeToUnix(left.time) - timeToUnix(right.time))
        .map((item) => [timeToUnix(item.time), item]),
    ).values(),
  );
}

function toTimestamp(value: string): UTCTimestamp {
  return Math.floor(Date.parse(value) / 1_000) as UTCTimestamp;
}

function timeToUnix(time: Time): number {
  if (typeof time === "number") return time;
  if (typeof time === "string") return Date.parse(time) / 1_000;
  return Date.UTC(time.year, time.month - 1, time.day) / 1_000;
}

function formatChartTime(time: Time) {
  return new Date(timeToUnix(time) * 1_000).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}

function formatPrice(value: number) {
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function nullablePrice(value: number | null) {
  return value === null ? "未记录" : formatPrice(value);
}

function formatSignedPercent(value: number) {
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
}

function sideLabel(side: TradeChartRecord["side"]) {
  if (side === "long") return "多头";
  if (side === "short") return "空头";
  return "方向未知";
}

function splitLabel(split: string) {
  return {
    train: "训练集",
    validation: "验证集",
    smoke: "试跑区间",
    full: "全区间",
  }[split] ?? split;
}

function exitReasonLabel(reason: string) {
  return {
    take_profit: "止盈",
    stop_loss: "止损",
    time_stop: "时间止损 / 到时离场",
    signal_exit: "离场信号",
    force_exit: "强制离场",
    liquidation: "清算",
    unknown: "未记录",
  }[reason] ?? reason;
}
