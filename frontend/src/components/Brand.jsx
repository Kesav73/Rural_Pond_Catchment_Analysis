export default function Brand() {
  return (
    <div className="brand">
      <svg
        className="brand-icon"
        viewBox="0 0 24 24"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <path
          d="M12 2C12 2 5 10.5 5 15.2C5 19.1 8.13 22 12 22C15.87 22 19 19.1 19 15.2C19 10.5 12 2 12 2Z"
          fill="currentColor"
        />
      </svg>
      <div>
        <h1>Village Pond Planner</h1>
        <p className="brand-sub">Rainwater-harvesting site planning for rural India</p>
      </div>
    </div>
  );
}
