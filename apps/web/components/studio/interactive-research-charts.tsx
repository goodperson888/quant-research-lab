"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import type { EChartsOption, EChartsType } from "echarts";
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
  apiFetch,
  MarketChartSeries,
  MarketWindowResponse,
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

type TradingChartView = "overview" | "trade";

export function InteractiveTradingChart({
  market,
  trades,
  strategyLabel,
  bundleId,
  marketTimeframe,
}: {
  market: MarketChartSeries;
  trades: TradeChartRecord[];
  strategyLabel: string;
  bundleId: string;
  marketTimeframe: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef =
    useRef<ISeriesApi<"Candlestick"> | null>(null);
  const selectedPriceLinesRef = useRef<IPriceLine[]>([]);
  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<TradingChartView>("overview");
  const [chartReady, setChartReady] = useState(false);
  const [hoverSummary, setHoverSummary] = useState<{
    time: string;
    open: number;
    high: number;
    low: number;
    close: number;
  } | null>(null);

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
  const selectedTradeCenter = selectedTrade
    ? new Date(
        (Date.parse(selectedTrade.entry_time) +
          Date.parse(selectedTrade.exit_time)) /
          2,
      ).toISOString()
    : null;
  const marketWindow = useQuery({
    queryKey: [
      "run-bundle-market-window",
      bundleId,
      marketTimeframe,
      selectedTradeCenter,
    ],
    queryFn: () =>
      apiFetch<MarketWindowResponse>(
        `/api/run-bundles/${encodeURIComponent(bundleId)}/market-window?market_timeframe=${encodeURIComponent(marketTimeframe)}&center_time=${encodeURIComponent(selectedTradeCenter ?? "")}&bars=360`,
      ),
    enabled: viewMode === "trade" && Boolean(selectedTradeCenter),
    retry: false,
  });
  const displayMarket =
    viewMode === "trade" &&
    marketWindow.data?.available &&
    marketWindow.data.market_series
      ? marketWindow.data.market_series
      : market;
  const candles = useMemo(
    () =>
      dedupeByTime(
        displayMarket.candles.map((item) => ({
          time: toTimestamp(item.t),
          open: item.open,
          high: item.high,
          low: item.low,
          close: item.close,
        })),
      ),
    [displayMarket.candles],
  );
  const volumes = useMemo(
    () =>
      dedupeByTime(
        displayMarket.candles.map((item) => ({
          time: toTimestamp(item.t),
          value: item.volume,
          color:
            item.close >= item.open
              ? "rgba(61,214,176,.35)"
              : "rgba(251,113,133,.35)",
        })),
      ),
    [displayMarket.candles],
  );
  const chartTrades = useMemo(
    () => tradesWithinCandles(sortedTrades, candles),
    [candles, sortedTrades],
  );

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
          lastValueVisible: false,
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
        const showMarkerText = chartTrades.length <= 40;
        createSeriesMarkers(
          candleSeries,
          buildTradeMarkers(chartTrades, showMarkerText),
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
          if (!event.time || !chartTrades.length) return;
          const clickedAt = timeToUnix(event.time);
          const interval = estimateCandleInterval(candles);
          const closest = nearestTrade(chartTrades, clickedAt);
          if (closest && closest.distance <= Math.max(interval * 1.5, 3_600)) {
            setSelectedTradeId(closest.trade.trade_id);
            setViewMode("trade");
          }
        });
        chartRef.current = chart;
        candleSeriesRef.current = candleSeries;
        setChartReady(true);
        setSelectedTradeId((current) =>
          sortedTrades.some((trade) => trade.trade_id === current)
            ? current
            : null,
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
  }, [candles, chartTrades, sortedTrades, volumes]);

  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    if (!candleSeries) return;
    for (const line of selectedPriceLinesRef.current) {
      candleSeries.removePriceLine(line);
    }
    selectedPriceLinesRef.current = [];
    if (!selectedTrade || viewMode !== "trade") return;
    const candidates = [
      {
        price: selectedTrade.entry_price,
        color: "#7dd3fc",
        title: "入场",
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
  }, [candles, chartReady, selectedTrade, viewMode]);

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
            {strategyLabel} · {displayMarket.timeframe}。
            {viewMode === "overview"
              ? " 当前把整个回测期间压缩到一张图，展示全部买卖点。"
              : " 当前只看一笔交易前后的原周期K线和交易计划。"}
            滚轮或双指缩放，按住拖动平移。
            {displayMarket.aggregated
              ? " 交易时间保留原始成交时间，标记对齐到最近一根展示K线。"
              : ""}
          </div>
        </div>
        <ChartToolbar chartRef={chartRef} disabled={!chartReady} />
      </div>

      <div
        className="mt-3 inline-flex rounded-xl border border-white/10 bg-black/10 p-1"
        role="tablist"
        aria-label="行情图查看范围"
      >
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === "overview"}
          onClick={() => setViewMode("overview")}
          className={
            viewMode === "overview"
              ? "min-h-11 rounded-lg bg-sky-300/10 px-4 text-xs text-sky-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
              : "min-h-11 rounded-lg px-4 text-xs text-slate-400 transition hover:bg-white/[0.04] hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
          }
        >
          完整回测走势
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === "trade"}
          disabled={!sortedTrades.length}
          onClick={() => {
            setSelectedTradeId(
              (current) => current ?? sortedTrades[0]?.trade_id ?? null,
            );
            setViewMode("trade");
          }}
          className={
            viewMode === "trade"
              ? "min-h-11 rounded-lg bg-sky-300/10 px-4 text-xs text-sky-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
              : "min-h-11 rounded-lg px-4 text-xs text-slate-400 transition hover:bg-white/[0.04] hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300 disabled:cursor-not-allowed disabled:opacity-40"
          }
        >
          逐笔交易复盘
        </button>
      </div>

      <div className="mt-2 text-[11px] leading-5 text-slate-500">
        {viewMode === "overview"
          ? "适合先看策略在整个回测期内何时交易、是否集中在某段行情。点击图上的买卖点可直接进入该笔交易复盘。"
          : "适合检查一笔交易的入场、止损、止盈和离场是否合理；可用下方选择器切换交易。"}
      </div>

      {viewMode === "trade" && marketWindow.isPending ? (
        <div className="mt-2 text-xs text-sky-100/80">
          正在读取选中交易附近的原周期 K 线……
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-[11px] text-slate-400">
        <LegendMarker color="#3dd6b0" shape="triangle-up" label="多头入场" />
        <LegendMarker color="#fb7185" shape="triangle-down" label="空头入场" />
        <LegendMarker color="#fbbf24" shape="square" label="离场" />
        <span>{sortedTrades.length.toLocaleString("zh-CN")} 笔真实交易</span>
      </div>
      {selectedTrade && viewMode === "trade" ? (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2 text-[11px] text-slate-400">
          <span className="text-slate-500">当前交易计划线与成交点：</span>
          <PriceLineLegend color="#7dd3fc" label="入场" />
          {selectedTrade.stop_price !== null ? (
            <PriceLineLegend color="#fb7185" label="止损" dashed />
          ) : null}
          {selectedTrade.take_profit_price !== null ? (
            <PriceLineLegend color="#3dd6b0" label="止盈" dashed />
          ) : null}
          <LegendMarker color="#fbbf24" shape="square" label="离场点" />
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

      {sortedTrades.length && viewMode === "trade" ? (
        <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
          <label className="block">
            <span className="mb-1 block text-xs text-slate-500">
              查看具体交易
            </span>
            <select
              value={selectedTrade?.trade_id ?? ""}
              onChange={(event) => {
                const tradeId = event.target.value || null;
                setSelectedTradeId(tradeId);
                setViewMode(tradeId ? "trade" : "overview");
              }}
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
                {
                  setSelectedTradeId(
                    sortedTrades[selectedTradeIndex - 1]?.trade_id ?? null,
                  );
                  setViewMode("trade");
                }
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
                {
                  setSelectedTradeId(
                    sortedTrades[selectedTradeIndex + 1]?.trade_id ?? null,
                  );
                  setViewMode("trade");
                }
              }
              className="min-h-11 rounded-xl border border-white/10 px-3 text-xs text-slate-300 disabled:opacity-40"
            >
              下一笔
            </button>
          </div>
        </div>
      ) : null}

      {selectedTrade && viewMode === "trade" ? (
        <TradeDetail trade={selectedTrade} />
      ) : null}
    </section>
  );
}

export function InteractiveEquityChart({
  series,
  title,
  description,
}: {
  series: EquityLine[];
  title: string;
  description: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<EChartsType | null>(null);
  const [chartReady, setChartReady] = useState(false);
  const curveStats = useMemo(
    () =>
      series.map((item) => ({
        id: item.id,
        label: item.label,
        color: item.color,
        changes: countEquityChanges(item.points),
        finalReturn:
          (item.points[item.points.length - 1]?.value ?? 1) - 1,
      })),
    [series],
  );
  const coverage = useMemo(() => {
    const timestamps = series.flatMap((item) =>
      item.points
        .map((point) => Date.parse(point.t))
        .filter(Number.isFinite),
    );
    if (!timestamps.length) return null;
    return {
      start: Math.min(...timestamps),
      end: Math.max(...timestamps),
    };
  }, [series]);
  const sparse = curveStats.every((item) => item.changes <= 2);
  const chartHeight = sparse ? 300 : 380;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !series.length) return;
    setChartReady(false);
    let disposed = false;
    let chart: EChartsType | null = null;
    let resizeObserver: ResizeObserver | null = null;
    void import("echarts").then((echarts) => {
        if (disposed) return;
        const prefersReducedMotion = window.matchMedia(
          "(prefers-reduced-motion: reduce)",
        ).matches;
        chart = echarts.init(container, null, {
          renderer: "canvas",
          width: container.clientWidth,
          height: chartHeight,
        });
        const option: EChartsOption = {
          animation: !prefersReducedMotion,
          animationDuration: 220,
          backgroundColor: "transparent",
          aria: {
            enabled: true,
            description: `${title}，包含 ${series.length} 条资金曲线。`,
          },
          grid: {
            left: 12,
            right: 18,
            top: 18,
            bottom: 74,
            containLabel: true,
          },
          tooltip: {
            trigger: "axis",
            confine: true,
            backgroundColor: "rgba(5,15,22,.96)",
            borderColor: "rgba(125,211,252,.25)",
            textStyle: { color: "#cbd5e1", fontSize: 12 },
            axisPointer: {
              type: "line",
              lineStyle: { color: "rgba(125,211,252,.45)" },
            },
            valueFormatter: (value) => {
              const numeric = Array.isArray(value)
                ? Number(value.at(-1))
                : Number(value);
              return Number.isFinite(numeric)
                ? `${numeric >= 0 ? "+" : ""}${numeric.toFixed(2)}%`
                : "—";
            },
          },
          xAxis: {
            type: "time",
            boundaryGap: [0, 0],
            axisLine: { lineStyle: { color: "rgba(148,163,184,.18)" } },
            axisLabel: {
              color: "rgba(148,163,184,.82)",
              hideOverlap: true,
              formatter: {
                year: "{yyyy}年",
                month: "{yyyy}-{MM}",
                day: "{MM}-{dd}",
                hour: "{MM}-{dd}\n{HH}:{mm}",
              },
            },
            splitLine: {
              show: true,
              lineStyle: { color: "rgba(148,163,184,.07)" },
            },
          },
          yAxis: {
            type: "value",
            scale: true,
            axisLabel: {
              color: "rgba(148,163,184,.82)",
              formatter: (value: number) => `${value.toFixed(2)}%`,
            },
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: {
              lineStyle: { color: "rgba(148,163,184,.08)" },
            },
          },
          dataZoom: [
            {
              type: "inside",
              xAxisIndex: 0,
              filterMode: "none",
              zoomOnMouseWheel: true,
              moveOnMouseMove: true,
              moveOnMouseWheel: false,
            },
            {
              type: "slider",
              xAxisIndex: 0,
              filterMode: "none",
              height: 24,
              bottom: 12,
              borderColor: "rgba(148,163,184,.16)",
              backgroundColor: "rgba(255,255,255,.02)",
              fillerColor: "rgba(56,189,248,.12)",
              dataBackground: {
                lineStyle: { color: "rgba(125,211,252,.35)" },
                areaStyle: { color: "rgba(56,189,248,.06)" },
              },
              selectedDataBackground: {
                lineStyle: { color: "rgba(125,211,252,.7)" },
                areaStyle: { color: "rgba(56,189,248,.12)" },
              },
              handleStyle: {
                color: "#0f2532",
                borderColor: "rgba(125,211,252,.65)",
              },
              textStyle: { color: "rgba(148,163,184,.8)" },
            },
          ],
          series: series.map((item, index) => ({
            id: item.id,
            name: item.label,
            type: "line",
            showSymbol: false,
            symbol: "none",
            step: "end",
            lineStyle: {
              color: item.color,
              width: 2,
              type:
                index % 3 === 0
                  ? "solid"
                  : index % 3 === 1
                    ? "dashed"
                    : "dotted",
            },
            itemStyle: { color: item.color },
            emphasis: { focus: "series" },
            data: item.points.map((point) => [
              Date.parse(point.t),
              (point.value - 1) * 100,
            ]),
          })),
        };
        chart.setOption(option);
        chartRef.current = chart;
        setChartReady(true);
        resizeObserver = new ResizeObserver(([entry]) => {
          chart?.resize({
            width: Math.floor(entry.contentRect.width),
            height: chartHeight,
          });
        });
        resizeObserver.observe(container);
      });
    return () => {
      disposed = true;
      resizeObserver?.disconnect();
      chart?.dispose();
      chartRef.current = null;
    };
  }, [chartHeight, series, title]);

  return (
    <section className="rounded-xl border border-white/10 bg-black/10 p-3">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="text-sm font-medium text-slate-200">
            {title}
          </div>
          <div className="mt-1 text-xs leading-5 text-slate-500">
            {description} 底部时间滑块始终保留完整覆盖范围，可拖动两端查看任意区间。
          </div>
        </div>
        <ChartButton
          label="恢复完整区间"
          disabled={!chartReady}
          onClick={() =>
            chartRef.current?.dispatchAction({
              type: "dataZoom",
              start: 0,
              end: 100,
            })
          }
        />
      </div>
      {sparse ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {curveStats.map((item) => (
            <div
              key={item.id}
              className="rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 py-2 text-xs"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="truncate text-slate-300">{item.label}</span>
                <span
                  className="tabular-nums"
                  style={{ color: item.color }}
                >
                  {formatSignedPercent(item.finalReturn)}
                </span>
              </div>
              <div className="mt-1 text-[11px] text-slate-500">
                资金仅变化 {item.changes} 次，折线信息有限
              </div>
            </div>
          ))}
        </div>
      ) : null}
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
        {coverage ? (
          <span className="text-slate-500">
            完整数据：{formatEquityDate(coverage.start)} —{" "}
            {formatEquityDate(coverage.end)}
          </span>
        ) : null}
      </div>
      <div
        ref={containerRef}
        className="mt-2 w-full"
        style={{ height: chartHeight }}
        aria-label={`${title}，可通过底部时间滑块缩放`}
      />
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

function tradesWithinCandles(
  trades: TradeChartRecord[],
  candles: CandlestickData<Time>[],
) {
  if (!candles.length) return [];
  const start = timeToUnix(candles[0].time);
  const end = timeToUnix(candles[candles.length - 1].time);
  return trades.filter((trade) => {
    const entry = Date.parse(trade.entry_time) / 1_000;
    const exit = Date.parse(trade.exit_time) / 1_000;
    return entry <= end && exit >= start;
  });
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

function countEquityChanges(points: Array<{ t: string; value: number }>) {
  let changes = 0;
  for (let index = 1; index < points.length; index += 1) {
    if (Math.abs(points[index].value - points[index - 1].value) > 1e-8) {
      changes += 1;
    }
  }
  return changes;
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

function formatEquityDate(timestamp: number) {
  return new Date(timestamp).toLocaleDateString("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
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
