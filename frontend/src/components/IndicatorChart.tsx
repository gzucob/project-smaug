"use client";

/**
 * Evolution of a single indicator: the closed exercises plus the current rolling
 * window, drawn as bars or as a line.
 *
 * Charted with Recharts rather than hand-rolled SVG because the reading only
 * works against a ruler: a value axis, grid lines, the zero baseline and the
 * asset's own historical average. A bare bar with its number printed on top
 * carries no scale — the reader cannot see how far this year sits from a
 * normal year, which is the whole question a multiple raises.
 *
 * The current point keeps a visual basis of its own (hollow bar / dashed segment):
 * it is a 12-month window, not one more closed exercise, and averaging it into
 * the reference line would quietly change what the line means.
 *
 * **Colour marks direction, not identity** (#145): single-value bars and lines
 * are blue above zero and red below it. Balance-sheet series use fixed colors
 * for each filed statement category, not as a judgement about direction.
 */
import { useState } from "react";
import { FiTarget } from "react-icons/fi";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { DASH } from "@/lib/format";
import { axisFormatter, valueFormatter } from "@/lib/indicators";
import type { FormatKind } from "@/lib/indicators";

export type ChartMode = "bars" | "line";

const AXIS_TICK = {
  fill: "var(--color-copy-400)",
  fontSize: 11,
  fontFamily: "var(--font-mono)",
};

interface Point {
  label: string;
  /** Every period — what the bars plot. */
  value: number | null;
  /** The containing quantity, when this is a paired chart. */
  envelope: number | null;
  /** Closed exercises only — the solid line. */
  closed: number | null;
  /** The current window and the exercise before it — the dashed tail. */
  live: number | null;
  ghost: boolean;
  [key: string]: string | number | boolean | null | undefined;
}

/**
 * The larger quantity a paired chart draws `values` inside of.
 *
 * Two figures that are read together — revenue with net income, assets with
 * liabilities — are drawn as nested bars rather than side by side: the envelope
 * is the outer bar at low opacity, `values` the solid one within it, so the
 * **empty area is the difference** (the margin, the equity). Side-by-side bars
 * make the reader subtract two heights by eye, which is the comparison the pair
 * exists to spare them.
 *
 * Both bars still take their colour from their own sign (#145), so a loss-making
 * year turns its inner bar red and drops below the baseline while the envelope
 * stays blue — which is precisely the year worth noticing.
 */
export interface EnvelopeSeries {
  values: (number | null)[];
  label: string;
}

/** One plotted series in a multi-series statement chart. */
export interface ChartSeries {
  key: string;
  label: string;
  type: "bar" | "line";
  values: (number | null)[];
  color: string;
  /** Separate category axes keep paired bars centred on the same year. */
  xAxisId?: string;
  barSize?: number;
  maxBarSize?: number;
  showDots?: boolean;
  lineType?: "linear" | "monotone";
  outlined?: boolean;
}

type HistoryRange = "5" | "10" | "max";

export function IndicatorChart({
  labels,
  values,
  ghostLast,
  color,
  formatKind,
  mode,
  average,
  height = 264,
  envelope = null,
  seriesLabel,
  series = null,
  periodYears = [],
  dateLabels = false,
  frequencyLabel,
  reference = null,
  showExtremes = false,
}: {
  labels: string[];
  values: (number | null)[];
  ghostLast: boolean;
  /** Non-directional accent — the average's reference line. Marks use up/down. */
  color: string;
  /** Named, not passed: a Server Component parent cannot hand over a function. */
  formatKind: FormatKind;
  mode: ChartMode;
  /** Mean of the closed exercises, drawn as the reference line. */
  average: number | null;
  height?: number;
  /** The containing quantity, drawn around `values` as a hollow outer bar. */
  envelope?: EnvelopeSeries | null;
  /** Independent bars and lines with a clickable legend and year range. */
  series?: ChartSeries[] | null;
  /** Calendar year for each corresponding point; used by the range selector. */
  periodYears?: number[];
  /** Names `values` in the tooltip — only needed when a pair makes it ambiguous. */
  seriesLabel?: string;
  /** ISO session labels retain the full date in tooltips. */
  dateLabels?: boolean;
  /** Labels custom-series frequency when only one filed period type is available. */
  frequencyLabel?: string;
  /** A named comparison value, such as the first close in a selected range. */
  reference?: { value: number; label: string } | null;
  showExtremes?: boolean;
}) {
  const [enabledSeries, setEnabledSeries] = useState<string[]>(() =>
    (series ?? []).map((item) => item.key),
  );
  const [highlightedSeries, setHighlightedSeries] = useState<string | null>(null);
  const [previewSeries, setPreviewSeries] = useState<string | null>(null);
  const [historyRange, setHistoryRange] = useState<HistoryRange>("5");
  const format = axisFormatter(formatKind);
  const readable = valueFormatter(formatKind);
  const chartSeries = series ?? [];
  const seriesXAxisIds = Array.from(
    new Set(
      chartSeries.flatMap((item) => (item.xAxisId ? [item.xAxisId] : [])),
    ),
  );
  const visibleSeries = chartSeries.filter((item) => enabledSeries.includes(item.key));
  const hasCustomSeries = chartSeries.length > 0;
  const activeSeries = [previewSeries, highlightedSeries].find((key) =>
    key !== null && visibleSeries.some((item) => item.key === key)) ?? null;
  const resolvedPoints = dataExtremes(labels, values);
  const seriesOpacity = (key: string) => !activeSeries || activeSeries === key ? 1 : 0.25;
  const latestYear = Math.max(...periodYears, 0);
  const firstVisibleYear =
    historyRange === "max"
      ? Number.NEGATIVE_INFINITY
      : latestYear - Number(historyRange) + 1;
  const visibleIndexes = labels.flatMap((_, index) => {
    if (!hasCustomSeries || historyRange === "max") return [index];
    const year = periodYears[index];
    return year !== undefined && year >= firstVisibleYear ? [index] : [];
  });
  const data: Point[] = visibleIndexes.map((index) => {
    const label = labels[index] ?? "";
    const i = index;
    const value = values[i] ?? null;
    const ghost = ghostLast && i === labels.length - 1;
    const tail = ghostLast && i >= labels.length - 2;
    return {
      label,
      value,
      envelope: envelope?.values[i] ?? null,
      closed: ghost ? null : value,
      live: tail ? value : null,
      ghost,
      ...Object.fromEntries(
        chartSeries.map((item) => [
          item.key,
          item.values[i] ?? null,
        ]),
      ),
    };
  });

  // The custom-series chart uses only the currently visible series and years
  // to set its ruler. The ordinary single-series charts retain their existing
  // value + envelope scale.
  const present = hasCustomSeries
    ? visibleSeries.flatMap((item) =>
        visibleIndexes
          .map((index) => item.values[index] ?? null)
          .filter((value): value is number => value !== null),
      )
    : [...values, ...(envelope?.values ?? [])].filter(
        (value): value is number => value !== null,
      );
  // Financial bars retain their zero baseline. Daily prices frame the observed
  // range so that changes within the selected period remain readable.
  const extrema = [...present, ...(reference ? [reference.value] : [])];
  const max = extrema.length ? Math.max(...extrema) : 0;
  const min = extrema.length ? Math.min(...extrema) : 0;
  const { domain, ticks } = axisScale(min, max, !dateLabels);
  const hasVisibleData = visibleSeries.some((item) =>
    visibleIndexes.some((index) => item.values[index] != null),
  );
  const toggleSeries = (key: string) => {
    if (highlightedSeries === key) setHighlightedSeries(null);
    setEnabledSeries((enabled) =>
      enabled.includes(key)
        ? enabled.filter((item) => item !== key)
        : [...enabled, key],
    );
  };

  return (
    <div className="w-full">
      {hasCustomSeries && (
        <div
          className={`mb-2 flex items-center gap-3 ${frequencyLabel ? "justify-between" : "justify-end"}`}
        >
          {frequencyLabel && (
            <span
              aria-label="Periodicidade dos dados"
              className="rounded-full border border-accent-400 px-3 py-1 text-[0.65rem] font-semibold tracking-wide text-accent-300"
            >
              {frequencyLabel}
            </span>
          )}
          <div
            className="inline-flex items-center border-b border-vault-700"
            role="group"
            aria-label="Intervalo do gráfico"
          >
            {([
              ["5", "5 anos"],
              ["10", "10 anos"],
              ["max", "Máx"],
            ] as const).map(([range, label]) => {
              const selected = historyRange === range;
              return (
                <button
                  key={range}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => setHistoryRange(range)}
                  className={`min-h-11 border-b-2 px-3 py-1.5 text-xs font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-up ${
                    selected
                      ? "border-up text-copy-50"
                      : "border-transparent text-copy-500 hover:text-copy-200"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>
      )}
      {hasCustomSeries && !hasVisibleData ? (
        <div
          className="flex items-center justify-center text-center text-xs text-copy-500"
          style={{ height }}
          role="status"
        >
          {visibleSeries.length === 0
            ? "Selecione uma série na legenda para exibi-la."
            : "Sem dados para as séries selecionadas neste intervalo."}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          {/* The right margin holds the last x label ("Atual"), which sits on
              the plot edge in line mode and would otherwise be clipped. */}
          <ComposedChart data={data} accessibilityLayer margin={{ top: showExtremes ? 28 : 10, right: 34, bottom: showExtremes ? 14 : 2, left: 2 }}>
          <CartesianGrid
            stroke="var(--color-copy-200)"
            strokeOpacity={0.1}
            strokeDasharray="3 4"
            vertical={false}
          />
          <XAxis
            dataKey="label"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: "var(--color-vault-700)" }}
            // Default interval, not `0`: on a phone the year labels would
            // otherwise run into each other. Recharts drops the ones that do
            // not fit and always keeps the trailing period.
            ticks={dateLabels ? data
              .filter((point, index) => index === 0 ||
                point.label.slice(0, 7) !== data[index - 1].label.slice(0, 7))
              .map((point) => point.label) : undefined}
            minTickGap={dateLabels ? 40 : 12}
            tickFormatter={dateLabels ? (value: string) =>
              new Date(`${value}T00:00:00Z`).toLocaleDateString("pt-BR", {
                month: "short", year: "2-digit", timeZone: "UTC",
              }) : undefined}
          />
          {seriesXAxisIds.map((axisId) => (
            <XAxis key={axisId} dataKey="label" xAxisId={axisId} hide />
          ))}
          <YAxis
            domain={domain}
            ticks={ticks}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={58}
            tickFormatter={(value: number) => format(value)}
          />
          <Tooltip
            // A composed chart draws a line cursor, not a band — keep it a
            // hairline so it points without competing with the bars.
            cursor={{ stroke: "var(--color-copy-400)", strokeOpacity: 0.6, strokeWidth: 1, strokeDasharray: "2 3" }}
            content={(props) => (
              <ChartTooltip
                label={typeof props.label === "string"
                  ? dateLabels
                    ? props.label.split("-").reverse().join("/")
                    : props.label
                  : ""}
                value={pointOf(data, props.label)}
                format={readable}
                seriesLabel={seriesLabel}
                envelopeLabel={envelope?.label}
                series={hasCustomSeries ? visibleSeries : null}
                highlightedSeries={activeSeries}
              />
            )}
          />

          {min < 0 && <ReferenceLine y={0} stroke="var(--color-ink-600)" strokeWidth={1} />}

          {/* The envelope rides its own x axis so Recharts centres it in the
              category on its own, concentric with the inner bar. Bars sharing an
              axis are laid out side by side, and no `barGap` makes two different
              widths share a centre — they end up offset from the tick instead. */}
          {!hasCustomSeries && envelope && <XAxis dataKey="label" xAxisId="envelope" hide />}
          {mode === "bars" && !hasCustomSeries && envelope && (
            <Bar
              dataKey="envelope"
              xAxisId="envelope"
              isAnimationActive={false}
              radius={[3, 3, 0, 0]}
              maxBarSize={54}
            >
              {data.map((d) => {
                const mark = (d.envelope ?? 0) < 0 ? "var(--color-down)" : "var(--color-up)";
                return (
                  <Cell
                    key={d.label}
                    fill={mark}
                    // Faint enough to read as the container rather than as a
                    // second reading competing with the one inside it.
                    fillOpacity={d.ghost ? 0.07 : 0.18}
                    stroke={mark}
                    strokeOpacity={d.ghost ? 0.35 : 0.5}
                    strokeDasharray={d.ghost ? "3 2" : undefined}
                  />
                );
              })}
            </Bar>
          )}

          {mode === "bars" && hasCustomSeries &&
            visibleSeries
              .filter((item) => item.type === "bar")
              .map((item) => (
                <Bar
                  key={item.key}
                  dataKey={item.key}
                  xAxisId={item.xAxisId}
                  isAnimationActive={false}
                  barSize={item.barSize}
                  maxBarSize={item.maxBarSize ?? 34}
                  radius={[2, 2, 0, 0]}
                  stroke="var(--color-vault-950)"
                  strokeWidth={1}
                  opacity={seriesOpacity(item.key)}
                >
                  {data.map((point) => (
                    <Cell
                      key={point.label}
                      fill={item.color}
                      fillOpacity={point.ghost ? 0.16 : item.outlined ? 0.15 : 0.9}
                      stroke={item.color}
                      strokeOpacity={point.ghost ? 0.55 : 1}
                      strokeDasharray={point.ghost ? "3 2" : undefined}
                    />
                  ))}
                </Bar>
              ))}

          {mode === "bars" && hasCustomSeries &&
            visibleSeries
              .filter((item) => item.type === "line")
              .map((item) => (
                <Line
                  key={item.key}
                  dataKey={item.key}
                  type={item.lineType ?? "linear"}
                  stroke={item.color}
                  strokeWidth={activeSeries === item.key ? 2.5 : 2}
                  opacity={seriesOpacity(item.key)}
                  dot={
                    item.showDots
                      ? {
                          r: 3.5,
                          fill: item.color,
                          stroke: "var(--color-vault-950)",
                          strokeWidth: 1,
                        }
                      : false
                  }
                  activeDot={{ r: 4.5 }}
                  isAnimationActive={false}
                  connectNulls={false}
                />
              ))}

          {mode === "bars" && !hasCustomSeries && (
            <Bar
              dataKey="value"
              isAnimationActive={false}
              radius={[3, 3, 0, 0]}
              // Narrower when nested, so the envelope stays visible around it.
              maxBarSize={envelope ? 26 : 54}
            >
              {data.map((d) => {
                const mark = (d.value ?? 0) < 0 ? "var(--color-down)" : "var(--color-up)";
                return (
                  <Cell
                    key={d.label}
                    fill={mark}
                    fillOpacity={d.ghost ? 0.16 : 0.85}
                    stroke={d.ghost ? mark : undefined}
                    strokeDasharray={d.ghost ? "3 2" : undefined}
                  />
                );
              })}
            </Bar>
          )}

          {mode === "line" && (
            <>
              <Line
                dataKey="closed"
                type="linear"
                stroke="var(--color-up)"
                strokeWidth={2}
                dot={values.length >= 7 ? false : { r: 2, fill: "var(--color-up)", stroke: "none" }}
                activeDot={{ r: 4.5 }}
                isAnimationActive={false}
                connectNulls={false}
              />
              <Line
                dataKey="live"
                type="linear"
                stroke="var(--color-up)"
                strokeWidth={2}
                strokeDasharray="5 4"
                strokeOpacity={0.75}
                dot={{
                  r: 3,
                  fill: "var(--color-vault-900)",
                  stroke: "var(--color-up)",
                  strokeWidth: 1.5,
                }}
                activeDot={{ r: 4.5 }}
                isAnimationActive={false}
                connectNulls={false}
              />
            </>
          )}

          {reference && (
            <ReferenceLine
              y={reference.value}
              stroke="var(--color-copy-400)"
              strokeDasharray="2 4"

            />
          )}

          {showExtremes && resolvedPoints && (
            <>
              <ReferenceDot x={resolvedPoints.high.label} y={resolvedPoints.high.value} r={3}
                fill="var(--color-up)" stroke="var(--color-canvas-900)"
                label={{ value: "Máx.", position: "top", fill: "var(--color-copy-200)", fontSize: 11 }} />
              {resolvedPoints.low.label !== resolvedPoints.high.label && <ReferenceDot
                x={resolvedPoints.low.label} y={resolvedPoints.low.value} r={3}
                fill="var(--color-up)" stroke="var(--color-canvas-900)"
                label={{ value: "Mín.", position: "bottom", fill: "var(--color-copy-200)", fontSize: 11 }} />}
            </>
          )}

          {/* Last, so it reads over the bars. It carries no inline label: the
              text would sit behind a bar — the dashed swatch on the "média"
              stat is the legend. */}
          {average !== null && (
            <ReferenceLine
              y={average}
              stroke={color}
              strokeOpacity={0.5}
              strokeDasharray="6 4"
            />
          )}
          </ComposedChart>
        </ResponsiveContainer>
      )}
      {(reference || average !== null || values.some((value) => value === null)) && (
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-xs text-copy-400">
          {reference && <span className="inline-flex items-center gap-2" title="Primeiro valor disponível no intervalo selecionado">
            <span aria-hidden="true" className="w-5 border-t border-dotted border-copy-400" />
            Primeiro fechamento: <span className="nums text-copy-200">{readable(reference.value)}</span>
          </span>}
          {average !== null && <span className="inline-flex items-center gap-2">
            <span aria-hidden="true" className="w-5 border-t border-dashed" style={{ borderColor: color }} />
            Média dos exercícios: <span className="nums text-copy-200">{readable(average)}</span>
          </span>}
          {!hasCustomSeries && values.some((value) => value === null) && <span>Lacunas: dados não disponíveis</span>}
        </div>
      )}
      {showExtremes && resolvedPoints && <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-xs text-copy-400">
        {([['Máximo', resolvedPoints.high], ['Mínimo', resolvedPoints.low]] as const).map(([label, point]) => (
          <span key={label}>{label}: <span className="nums text-copy-200">{readable(point.value)}</span>
            {' · '}{dateLabels ? point.label.split('-').reverse().join('/') : point.label}
          </span>
        ))}
      </div>}
      {hasCustomSeries && <div className="mt-3 flex flex-wrap justify-center gap-2" role="group" aria-label="Séries do gráfico">
        {chartSeries.map((item) => {
          const selected = enabledSeries.includes(item.key);
          return <div key={item.key} className="inline-flex items-center">
            <button type="button" aria-pressed={selected} onClick={() => toggleSeries(item.key)}
              onMouseEnter={() => setPreviewSeries(item.key)} onMouseLeave={() => setPreviewSeries(null)}
              onFocus={() => setPreviewSeries(item.key)} onBlur={() => setPreviewSeries(null)}
              className={`min-h-11 flex items-center gap-2 rounded px-2 text-xs focus-visible:outline-2 focus-visible:outline-up ${selected ? "text-copy-200" : "text-copy-400 line-through"}`}>
              <span aria-hidden="true" className={item.type === 'line' ? 'w-4 border-t-2' : 'h-2.5 w-2.5 border'}
                style={{ borderColor: item.color, backgroundColor: item.type === 'bar' && !item.outlined ? item.color : undefined }} />
              {item.label}
            </button>
            <button type="button" aria-label={`Destacar ${item.label}`} title={`Destacar ${item.label}`}
              aria-pressed={highlightedSeries === item.key} disabled={!selected}
              onClick={() => setHighlightedSeries((current) => current === item.key ? null : item.key)}
              className={`min-h-11 min-w-11 flex items-center justify-center rounded focus-visible:outline-2 focus-visible:outline-up disabled:opacity-30 ${highlightedSeries === item.key ? "text-accent-300 bg-accent-400/10" : "text-copy-400"}`}>
              <FiTarget size={14} aria-hidden="true" />
            </button>
          </div>;
        })}
      </div>}
      <details className="mt-2 text-xs text-copy-400">
        <summary className="min-h-11 cursor-pointer py-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-400">
          Ver valores do gráfico
        </summary>
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-xs">
            <caption className="sr-only">Valores por período, nas mesmas unidades do gráfico</caption>
            <thead>
              <tr className="border-b border-copy-200/10">
                <th scope="col" className="py-2 pr-4 text-left font-medium">Período</th>
                {(hasCustomSeries ? visibleSeries.map((item) => item.label) : [
                  ...(envelope ? [envelope.label] : []), seriesLabel ?? "Valor",
                ]).map((label) => <th key={label} scope="col" className="px-2 text-right font-medium">{label}</th>)}
              </tr>
            </thead>
            <tbody>
              {(dateLabels ? [...data].reverse() : data).map((point) => (
                <tr key={point.label}>
                  <th scope="row" className="whitespace-nowrap py-2 pr-4 text-left font-normal">
                    {dateLabels ? point.label.split("-").reverse().join("/") : point.label}
                    {point.ghost ? " · 12 meses" : ""}
                  </th>
                  {(hasCustomSeries ? visibleSeries.map((item) => {
                    const amount = point[item.key];
                    return typeof amount === "number" ? amount : null;
                  }) : [...(envelope ? [point.envelope] : []), point.value]).map((amount, index) => (
                    <td key={index} className="nums whitespace-nowrap px-2 text-right text-copy-200">
                      {amount === null ? DASH : readable(amount)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

/**
 * A value axis on round numbers, spanning zero for financial indicators.
 *
 * Recharts' own ticks land on the raw data extremes ("+53,5%"), which reads as
 * a measurement rather than a ruler. Snapping the domain to a 1/2/5 step gives
 * grid lines a reader can subtract in their head, and puts zero exactly on one.
 */
function axisScale(min: number, max: number, includeZero = true): { domain: [number, number]; ticks: number[] } {
  const padding = (max - min || Math.abs(max) || 1) * 0.05;
  const lo = includeZero ? Math.min(0, min) : min - padding;
  const hi = includeZero ? Math.max(0, max) : max + padding;
  const span = hi - lo || Math.abs(hi) || 1;
  const rough = span / 5;
  const mag = 10 ** Math.floor(Math.log10(rough));
  const norm = rough / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;

  const start = Math.floor(lo / step) * step;
  const end = Math.max(Math.ceil(hi / step) * step, start + step);
  const count = Math.round((end - start) / step);
  const ticks = Array.from({ length: count + 1 }, (_, i) => start + i * step);
  return { domain: [start, end], ticks };
}

function pointOf(data: Point[], label: unknown): Point | undefined {
  return data.find((d) => d.label === label);
}

function ChartTooltip({
  label,
  value,
  format,
  seriesLabel,
  envelopeLabel,
  series,
  highlightedSeries,
}: {
  label: string;
  value: Point | undefined;
  format: (n: number) => string;
  seriesLabel?: string;
  envelopeLabel?: string;
  series?: ChartSeries[] | null;
  highlightedSeries?: string | null;
}) {
  if (!value) return null;
  const mark = (value.value ?? 0) < 0 ? "var(--color-down)" : "var(--color-up)";
  // On a paired chart the envelope is named and shown first: it is the larger
  // quantity, and reading it before the part makes the difference legible.
  const paired = envelopeLabel !== undefined;
  return (
    <div className="bg-canvas-900 px-3 py-2 text-xs">
      <div className="text-[0.68rem] text-copy-400">{label}</div>
      {series && series.length > 0 ? (
        <div className="mt-1 space-y-0.5">
          {series.map((item) => {
            const raw = value[item.key];
            const amount = typeof raw === "number" ? raw : null;
            return (
              <div
                key={item.key}
                className={`grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-5 ${highlightedSeries === item.key ? "font-semibold" : ""}`}
              >
                <span className="flex items-center gap-1.5 text-[0.68rem] text-copy-400">
                  {item.type === "line" ? (
                    <span
                      aria-hidden="true"
                      className="h-px w-3"
                      style={{ backgroundColor: item.color }}
                    />
                  ) : (
                    <span
                      aria-hidden="true"
                      className="h-1.5 w-1.5 rounded-[1px]"
                      style={{ backgroundColor: item.color }}
                    />
                  )}
                  {item.label}
                </span>
                <span className="nums text-ink-200">
                  {amount === null ? DASH : format(amount)}
                </span>
              </div>
            );
          })}
        </div>
      ) : paired ? (
        <div className="mt-1 flex items-baseline justify-between gap-4">
          <span className="text-[0.68rem] text-copy-400">{envelopeLabel}</span>
          <span className="nums text-ink-200">
            {value.envelope === null ? DASH : format(value.envelope)}
          </span>
        </div>
      ) : null}
      {series && series.length > 0 ? null : paired ? (
        <div className="flex items-baseline justify-between gap-4">
          <span className="text-[0.68rem] text-copy-400">{seriesLabel}</span>
          <span className="nums font-semibold" style={{ color: mark }}>
            {value.value === null ? DASH : format(value.value)}
          </span>
        </div>
      ) : (
        <div className="nums mt-0.5 text-sm font-semibold" style={{ color: mark }}>
          {value.value === null ? DASH : format(value.value)}
        </div>
      )}
      {value.ghost && (
        <div className="mt-1 text-[0.62rem] text-copy-600">período atual · 12 meses</div>
      )}
    </div>
  );
}

/** Observed extrema only: gaps never contribute a synthetic zero. */
function dataExtremes(labels: string[], values: (number | null)[]) {
  let low: { label: string; value: number } | null = null;
  let high: { label: string; value: number } | null = null;
  for (const [index, value] of values.entries()) {
    if (value === null || !Number.isFinite(value)) continue;
    const point = { label: labels[index], value };
    if (!low || value < low.value) low = point;
    if (!high || value > high.value) high = point;
  }
  return low && high ? { low, high } : null;
}
