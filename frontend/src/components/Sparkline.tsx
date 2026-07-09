/** Minimal dependency-free sparkline: gradient area + line + head dot. */
export function Sparkline({
  values,
  color,
  width = 132,
  height = 40,
}: {
  values: (number | null)[];
  color: string;
  width?: number;
  height?: number;
}) {
  const pts = values.map((v, i) => ({ v, i })).filter((p): p is { v: number; i: number } => p.v !== null);
  if (pts.length < 2) {
    return (
      <div className="flex h-10 items-center text-xs text-ink-600" style={{ width }}>
        série insuficiente
      </div>
    );
  }

  const xs = pts.map((p) => p.i);
  const ys = pts.map((p) => p.v);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const padY = height * 0.16;
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;

  const px = (i: number) => ((i - minX) / spanX) * (width - 6) + 3;
  const py = (v: number) => height - padY - ((v - minY) / spanY) * (height - 2 * padY);

  const line = pts.map((p, k) => `${k === 0 ? "M" : "L"}${px(p.i).toFixed(1)},${py(p.v).toFixed(1)}`).join(" ");
  const area = `${line} L${px(maxX).toFixed(1)},${height} L${px(minX).toFixed(1)},${height} Z`;
  const last = pts[pts.length - 1];
  const gid = `spark-${color.replace(/[^a-z]/gi, "")}-${width}`;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.32" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={px(last.i)} cy={py(last.v)} r="2.6" fill={color} />
      <circle cx={px(last.i)} cy={py(last.v)} r="5" fill={color} opacity="0.22" />
    </svg>
  );
}
