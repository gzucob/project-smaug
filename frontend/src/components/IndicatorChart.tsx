"use client";

/**
 * Evolution of a single indicator: the closed exercises plus the trailing TTM
 * window, drawn as bars or as a line.
 *
 * Charted with Recharts rather than hand-rolled SVG because the reading only
 * works against a ruler: a value axis, grid lines, the zero baseline and the
 * asset's own historical average. A bare bar with its number printed on top
 * carries no scale — the reader cannot see how far this year sits from a
 * normal year, which is the whole question a multiple raises.
 *
 * The TTM point keeps a visual basis of its own (hollow bar / dashed segment):
 * it is a 12-month window, not one more closed exercise, and averaging it into
 * the reference line would quietly change what the line means.
 */
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { axisFormatter, valueFormatter } from "@/lib/indicators";
import type { FormatKind } from "@/lib/indicators";

export type ChartMode = "bars" | "line";

const AXIS_TICK = {
  fill: "var(--color-ink-500)",
  fontSize: 11,
  fontFamily: "var(--font-mono)",
};

interface Point {
  label: string;
  /** Every period — what the bars plot. */
  value: number | null;
  /** Closed exercises only — the solid line. */
  closed: number | null;
  /** The TTM window and the exercise before it — the dashed tail. */
  live: number | null;
  ghost: boolean;
}

export function IndicatorChart({
  labels,
  values,
  ghostLast,
  color,
  formatKind,
  mode,
  average,
  height = 264,
}: {
  labels: string[];
  values: (number | null)[];
  ghostLast: boolean;
  color: string;
  /** Named, not passed: a Server Component parent cannot hand over a function. */
  formatKind: FormatKind;
  mode: ChartMode;
  /** Mean of the closed exercises, drawn as the reference line. */
  average: number | null;
  height?: number;
}) {
  const format = axisFormatter(formatKind);
  const readable = valueFormatter(formatKind);
  const lastIndex = values.length - 1;
  const data: Point[] = labels.map((label, i) => {
    const value = values[i] ?? null;
    const ghost = ghostLast && i === lastIndex;
    const tail = ghostLast && i >= lastIndex - 1;
    return { label, value, closed: ghost ? null : value, live: tail ? value : null, ghost };
  });

  const present = values.filter((v): v is number => v !== null);
  // The axis always contains zero: a bar cut off below its baseline overstates
  // the variation, and on the line it is the difference between "fell" and
  // "fell to near nothing".
  const max = Math.max(0, ...present);
  const min = Math.min(0, ...present);
  const { domain, ticks } = axisScale(min, max);

  return (
    <div className="w-full">
      <ResponsiveContainer width="100%" height={height}>
        {/* The right margin holds the last x label ("12 meses"), which sits on
            the plot edge in line mode and would otherwise be clipped. */}
        <ComposedChart data={data} margin={{ top: 10, right: 34, bottom: 2, left: 2 }}>
          <CartesianGrid
            stroke="var(--color-vault-700)"
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
            minTickGap={4}
          />
          <YAxis
            domain={domain}
            ticks={ticks}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={58}
            tickFormatter={format}
          />
          <Tooltip
            // A composed chart draws a line cursor, not a band — keep it a
            // hairline so it points without competing with the bars.
            cursor={{ stroke: "var(--color-ink-400)", strokeOpacity: 0.28, strokeWidth: 1 }}
            content={(props) => (
              <ChartTooltip
                label={typeof props.label === "string" ? props.label : ""}
                value={pointOf(data, props.label)}
                format={readable}
                color={color}
              />
            )}
          />

          {min < 0 && <ReferenceLine y={0} stroke="var(--color-ink-600)" strokeWidth={1} />}

          {mode === "bars" && (
            <Bar dataKey="value" isAnimationActive={false} radius={[3, 3, 0, 0]} maxBarSize={54}>
              {data.map((d) => (
                <Cell
                  key={d.label}
                  fill={(d.value ?? 0) < 0 ? "var(--color-down)" : color}
                  fillOpacity={d.ghost ? 0.16 : 0.85}
                  stroke={d.ghost ? color : undefined}
                  strokeDasharray={d.ghost ? "3 2" : undefined}
                />
              ))}
            </Bar>
          )}

          {mode === "line" && (
            <>
              <Line
                dataKey="closed"
                type="monotone"
                stroke={color}
                strokeWidth={2}
                dot={{ r: 3, fill: color, stroke: "none" }}
                activeDot={{ r: 4.5 }}
                isAnimationActive={false}
                connectNulls={false}
              />
              <Line
                dataKey="live"
                type="monotone"
                stroke={color}
                strokeWidth={2}
                strokeDasharray="5 4"
                strokeOpacity={0.75}
                dot={{ r: 3, fill: "var(--color-vault-900)", stroke: color, strokeWidth: 1.5 }}
                activeDot={{ r: 4.5 }}
                isAnimationActive={false}
                connectNulls={false}
              />
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
    </div>
  );
}

/**
 * A value axis on round numbers, spanning zero.
 *
 * Recharts' own ticks land on the raw data extremes ("+53,5%"), which reads as
 * a measurement rather than a ruler. Snapping the domain to a 1/2/5 step gives
 * grid lines a reader can subtract in their head, and puts zero exactly on one.
 */
function axisScale(min: number, max: number): { domain: [number, number]; ticks: number[] } {
  const lo = Math.min(0, min);
  const hi = Math.max(0, max);
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
  color,
}: {
  label: string;
  value: Point | undefined;
  format: (n: number) => string;
  color: string;
}) {
  if (!value) return null;
  return (
    <div className="panel px-3 py-2 text-xs shadow-lg">
      <div className="text-[0.68rem] uppercase tracking-wide text-ink-500">{label}</div>
      <div className="nums mt-0.5 text-sm font-semibold" style={{ color }}>
        {value.value === null ? "n/d" : format(value.value)}
      </div>
      {value.ghost && (
        <div className="mt-1 text-[0.62rem] text-ink-600">janela de 12 meses</div>
      )}
    </div>
  );
}
