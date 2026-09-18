export default function ErrorPanel({ message, onRetry }) {
  return (
    <div className="banner banner-error" role="alert">
      <strong>The analysis could not finish</strong>
      <p>{message}</p>
      <button className="btn btn-outline" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}
