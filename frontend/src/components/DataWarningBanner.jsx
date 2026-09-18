// A result computed without one of its data sources is weaker than it looks; say which, plainly,
// instead of tucking it onto the end of a status line.
const SOURCES = [
  {
    key: "worldcover_available",
    name: "ESA WorldCover water map",
    effect: "existing ponds and rivers may not all have been screened out",
  },
  {
    key: "swir_available",
    name: "Sentinel-2 water check",
    effect: "seasonal water may not have been screened out",
  },
  {
    key: "rainfall_available",
    name: "Rainfall history",
    effect: "water volumes could not be estimated",
  },
];

export default function DataWarningBanner({ summary }) {
  if (!summary) return null;
  const missing = SOURCES.filter((source) => summary[source.key] === false);
  if (!missing.length) return null;
  return (
    <div className="banner banner-warn" role="status">
      <strong>Some data was unavailable for this run</strong>
      <ul>
        {missing.map((source) => (
          <li key={source.key}>
            {source.name} — {source.effect}.
          </li>
        ))}
      </ul>
      <span className="banner-foot">Running again later may give a fuller result.</span>
    </div>
  );
}
