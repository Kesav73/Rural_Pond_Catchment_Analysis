// Rank 1 is the strongest green and fades down the list, so the ordering is readable at a glance
// without having to open each popup or card.
const RANK_COLORS = ["#00e676", "#66dd55", "#a8d63a", "#d4c22c", "#f0a020"];

export function rankColor(rank) {
  return RANK_COLORS[Math.min(rank - 1, RANK_COLORS.length - 1)];
}

// Interpolates a diverging blue -> tan -> red ramp, low elevation to high.
export function elevationColor(t) {
  const stops = [
    [0.0, [33, 102, 172]],
    [0.25, [103, 169, 207]],
    [0.5, [253, 219, 199]],
    [0.75, [239, 138, 98]],
    [1.0, [178, 24, 43]],
  ];
  t = Math.max(0, Math.min(1, t));
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, c0] = stops[i];
    const [t1, c1] = stops[i + 1];
    if (t >= t0 && t <= t1) {
      const f = t1 === t0 ? 0 : (t - t0) / (t1 - t0);
      const c = c0.map((v, idx) => Math.round(v + (c1[idx] - v) * f));
      return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
    }
  }
  return "rgb(178, 24, 43)";
}
