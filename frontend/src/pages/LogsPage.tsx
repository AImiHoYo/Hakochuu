import { LogMonitorPanel } from "../components/logs/LogMonitorPanel";

export function LogsPage() {
  return (
    <section className="hk-container-wide">
      <header className="hk-page-header">
        <div className="hk-page-header__motif" aria-hidden="true">
          <svg width="26" height="18" viewBox="0 0 26 18" fill="none" stroke="currentColor" strokeWidth="1">
            <path d="M13 2C14.8 6 17.5 8.5 22 9C17.5 9.5 14.8 12 13 16C11.2 12 8.5 9.5 4 9C8.5 8.5 11.2 6 13 2Z" />
          </svg>
        </div>
        <h1 className="hk-page-header__title">CHRONICLE</h1>
        <p className="hk-page-header__subtitle">Runtime Service Stream · 5-Node Monitor</p>
      </header>

      <div className="hk-divider-diamond" style={{ marginBottom: "1.75rem" }}>
        <span className="hk-divider-diamond__line" />
      </div>

      <div className="hk-reveal">
        <LogMonitorPanel />
      </div>
    </section>
  );
}
