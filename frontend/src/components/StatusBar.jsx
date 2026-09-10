export default function StatusBar({ text, tone, busy }) {
  return (
    <div className={`status status-${tone}`}>
      {busy && <span className="spinner" />}
      <span>{text}</span>
    </div>
  );
}
