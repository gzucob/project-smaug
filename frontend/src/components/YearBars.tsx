/**
 * Minimal dependency-free vertical bar chart for a per-year series.
 *
 * Draws one bar per closed year with a zero baseline, so a negative value
 * (a loss) drops below the line. Positive bars take the sector accent; negative
 * bars take the "down" colour. Missing years render as a faint stub. The value
 * label sits at the bar's outer end; the year sits under the baseline.
 */
const VIEW_W = 320;
const VIEW_H = 168;
const PAD_TOP = 24; // room for the value label above a positive bar
const PAD_BOTTOM = 22; // room for the year label under the baseline
const BAR_RATIO = 0.56; // bar width as a fraction of its slot

export function YearBars({
  labels,
  values,
  color,
  format,
}: {
  labels: string[];
  values: (number | null)[];
  color: string;
  format: (n: number) => string;
}) {
  const present = values.filter((v): v is number => v !== null);
  if (present.length < 1) {
    return (
      <div className="flex h-[120px] items-center justify-center text-xs text-ink-600">
        série insuficiente
      </div>
    );
  }

  const maxV = Math.max(0, ...present);
  const minV = Math.min(0, ...present);
  const range = maxV - minV || 1;
  const plotH = VIEW_H - PAD_TOP - PAD_BOTTOM;
  const yOf = (v: number) => PAD_TOP + ((maxV - v) / range) * plotH;
  const baseY = yOf(0);

  const slot = VIEW_W / values.length;
  const barW = slot * BAR_RATIO;

  return (
    <svg
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      className="block w-full"
      style={{ height: "auto" }}
      role="img"
    >
      {/* zero baseline */}
      <line
        x1={0}
        x2={VIEW_W}
        y1={baseY}
        y2={baseY}
        stroke="var(--color-ink-700)"
        strokeWidth={1}
      />
      {values.map((v, i) => {
        const cx = slot * i + slot / 2;
        const x = cx - barW / 2;
        const year = labels[i];
        if (v === null) {
          return (
            <g key={i}>
              <rect
                x={x}
                y={baseY - 3}
                width={barW}
                height={3}
                fill="var(--color-ink-700)"
                opacity={0.5}
              />
              <text
                x={cx}
                y={VIEW_H - 6}
                textAnchor="middle"
                fill="var(--color-ink-600)"
                fontSize={11}
              >
                {year}
              </text>
            </g>
          );
        }
        const y = yOf(v);
        const positive = v >= 0;
        const top = positive ? y : baseY;
        const h = Math.max(1, Math.abs(y - baseY));
        const barColor = positive ? color : "var(--color-down)";
        const labelY = positive ? top - 6 : baseY + h + 12;
        return (
          <g key={i}>
            <rect x={x} y={top} width={barW} height={h} rx={2} fill={barColor} opacity={0.85} />
            <text
              x={cx}
              y={labelY}
              textAnchor="middle"
              className="nums"
              fill="var(--color-ink-300)"
              fontSize={10.5}
            >
              {format(v)}
            </text>
            <text
              x={cx}
              y={VIEW_H - 6}
              textAnchor="middle"
              fill="var(--color-ink-500)"
              fontSize={11}
            >
              {year}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
