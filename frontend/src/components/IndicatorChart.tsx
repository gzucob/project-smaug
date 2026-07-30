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
 *
 * **Colour marks direction, not identity** (#145): a bar or line is blue above
 * zero and red below it, in every chart. `color` is left for the things that
 * have no direction — the average's reference line — where the group or sector
 * hue still says which family the reader is looking at.
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
  /** The containing quantity, when this is a paired chart. */
  envelope: number | null;
  /** Closed exercises only — the solid line. */
  closed: number | null;
  /** The TTM window and the exercise before it — the dashed tail. */
  live: number | null;
  ghost: boolean;
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
  /** Names `values` in the tooltip — only needed when a pair makes it ambiguous. */
  seriesLabel?: string;
}) {
  const format = axisFormatter(formatKind);
  const readable = valueFormatter(formatKind);
  const lastIndex = values.length - 1;
  const data: Point[] = labels.map((label, i) => {
    const value = values[i] ?? null;
    const ghost = ghostLast && i === lastIndex;
    const tail = ghostLast && i >= lastIndex - 1;
    return {
      label,
      value,
      envelope: envelope?.values[i] ?? null,
      closed: ghost ? null : value,
      live: tail ? value : null,
      ghost,
    };
  });

  // The axis has to contain both series, or the envelope would be clipped by a
  // scale built for the smaller figure inside it.
  const present = [...values, ...(envelope?.values ?? [])].filter(
    (v): v is number => v !== null,
  );
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
                seriesLabel={seriesLabel}
                envelopeLabel={envelope?.label}
              />
            )}
          />

          {min < 0 && <ReferenceLine y={0} stroke="var(--color-ink-600)" strokeWidth={1} />}

          {/* The envelope rides its own x axis so Recharts centres it in the
              category on its own, concentric with the inner bar. Bars sharing an
              axis are laid out side by side, and no `barGap` makes two different
              widths share a centre — they end up offset from the tick instead. */}
          {envelope && <XAxis dataKey="label" xAxisId="envelope" hide />}
          {mode === "bars" && envelope && (
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

          {mode === "bars" && (
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
                type="monotone"
                stroke="var(--color-up)"
                strokeWidth={2}
                dot={{ r: 3, fill: "var(--color-up)", stroke: "none" }}
                activeDot={{ r: 4.5 }}
                isAnimationActive={false}
                connectNulls={false}
              />
              <Line
                dataKey="live"
                type="monotone"
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
  seriesLabel,
  envelopeLabel,
}: {
  label: string;
  value: Point | undefined;
  format: (n: number) => string;
  seriesLabel?: string;
  envelopeLabel?: string;
}) {
  if (!value) return null;
  const mark = (value.value ?? 0) < 0 ? "var(--color-down)" : "var(--color-up)";
  // On a paired chart the envelope is named and shown first: it is the larger
  // quantity, and reading it before the part makes the difference legible.
  const paired = envelopeLabel !== undefined;
  return (
    <div className="panel px-3 py-2 text-xs shadow-lg">
      <div className="text-[0.68rem] uppercase tracking-wide text-ink-500">{label}</div>
      {paired && (
        <div className="mt-1 flex items-baseline justify-between gap-4">
          <span className="text-[0.62rem] text-ink-500">{envelopeLabel}</span>
          <span className="nums text-ink-200">
            {value.envelope === null ? "n/d" : format(value.envelope)}
          </span>
        </div>
      )}
      {paired ? (
        <div className="flex items-baseline justify-between gap-4">
          <span className="text-[0.62rem] text-ink-500">{seriesLabel}</span>
          <span className="nums font-semibold" style={{ color: mark }}>
            {value.value === null ? "n/d" : format(value.value)}
          </span>
        </div>
      ) : (
        <div className="nums mt-0.5 text-sm font-semibold" style={{ color: mark }}>
          {value.value === null ? "n/d" : format(value.value)}
        </div>
      )}
      {value.ghost && (
        <div className="mt-1 text-[0.62rem] text-ink-600">janela de 12 meses</div>
      )}
    </div>
  );
}
