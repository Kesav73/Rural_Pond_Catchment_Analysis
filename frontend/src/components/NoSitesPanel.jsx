// "Nothing found" is a real answer, so it gets the same weight as a results list — with why,
// and what to try instead — rather than one small status sentence.
export default function NoSitesPanel({ summary, outsideShape }) {
  const checked = summary.zones_in_view;
  const noHollows = checked === 0;

  const rows = [
    ["Natural hollows checked", checked],
    ["On or next to existing water", summary.excluded_water],
    ["Long and narrow (stream channels)", summary.excluded_shape],
  ];
  if (outsideShape) rows.push(["Outside the shape you drew", outsideShape]);

  return (
    <section className="panel panel-empty" aria-live="polite">
      <div className="empty-icon" aria-hidden="true">
        ∅
      </div>
      <h2 className="empty-title">No suitable pond sites in this area</h2>
      <p className="empty-lead">
        {noHollows
          ? `The ground here has no natural hollow at least ${summary.min_depth_m} m deep — it is too flat or slopes evenly, so there is nowhere water would collect on its own.`
          : "There were natural hollows, but every one was ruled out:"}
      </p>
      {!noHollows && (
        <table className="empty-breakdown">
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label}>
                <td>{label}</td>
                <td>{value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!noHollows && (
        <p className="empty-note">Rejected hollows are shown in grey on the map — hover one to see why.</p>
      )}
      <h3 className="empty-subtitle">Try</h3>
      <ul className="empty-hints">
        <li>Drawing a larger area around this one</li>
        <li>An area with more rise and fall in the land (near hills or valley edges)</li>
        {!noHollows && summary.excluded_water > 0 && (
          <li>Nearby land away from existing tanks and rivers — those are already water</li>
        )}
      </ul>
    </section>
  );
}
