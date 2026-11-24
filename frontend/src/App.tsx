import { useEffect, useState } from "react";
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { FloatingLogDrawer } from "./components/logs/FloatingLogDrawer";
import { useScrollReveal } from "./hooks/useScrollReveal";
import { LogsPage } from "./pages/LogsPage";
import { OnboardingPage } from "./pages/OnboardingPage";
import { SettingsPage } from "./pages/SettingsPage";
import { WorkspacePage } from "./pages/WorkspacePage";

interface FooterCapabilities {
  deploy_mode: string;
  mock_engines: boolean;
}

function AppLayout() {
  const location = useLocation();
  const [cap, setCap] = useState<FooterCapabilities | null>(null);

  useScrollReveal(location.pathname);

  useEffect(() => {
    fetch("/v1/system/capabilities")
      .then((r) => (r.ok ? r.json() : null))
      .then((data: FooterCapabilities | null) => {
        if (data) setCap(data);
      })
      .catch(() => {
        /* 网关离线时保持待机提示 */
      });
  }, []);

  const engineSummary = cap
    ? `GATEWAY ONLINE · ${cap.deploy_mode.toUpperCase()} · ${cap.mock_engines ? "MOCK ENGINE" : "GPU ENGINE"}`
    : "GATEWAY STANDBY · LOCALHOST:23333";

  return (
    <div className="hk-shell">
      <header className="hk-header hk-header--frosted">
        <div className="hk-header__inner">
          <Link to="/" className="hk-brand hk-motion-fade">
            <svg
              className="hk-brand__ornament"
              viewBox="0 0 32 32"
              fill="none"
              stroke="currentColor"
              strokeWidth="1"
              aria-hidden="true"
            >
              <path d="M16 4C18.5 9.5 22.5 13.5 28 16C22.5 18.5 18.5 22.5 16 28C13.5 22.5 9.5 18.5 4 16C9.5 13.5 13.5 9.5 16 4Z" />
              <circle cx="16" cy="16" r="2.2" />
            </svg>
            <div>
              <div className="hk-brand__title">HaKochuu</div>
              <div className="hk-brand__sub">Voice &amp; Avatar Atelier</div>
            </div>
          </Link>

          <nav className="hk-nav" aria-label="主导航">
            <NavLink to="/" end>
              STAGE
            </NavLink>
            <NavLink to="/onboarding">PERSONA</NavLink>
            <NavLink to="/settings">ATELIER</NavLink>
          </nav>
        </div>
      </header>

      <main className="hk-main">
        <div key={location.pathname} className="hk-page-enter">
          <Routes>
            <Route path="/" element={<WorkspacePage />} />
            <Route path="/onboarding" element={<OnboardingPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/logs" element={<LogsPage />} />
          </Routes>
        </div>
      </main>

      <footer className="hk-footer">
        <div className="hk-footer__inner">
          <div className="hk-footer__groups">
            <span className="hk-footer__group">
              <span
                className={`hk-status-diamond ${
                  cap ? "hk-status-diamond--ok" : "hk-status-diamond--down"
                }`}
              />
              <span>{engineSummary}</span>
            </span>
            <span className="hk-footer__group">
              <Link to="/" className="hk-motion-fade">
                STAGE
              </Link>
              <span>/</span>
              <Link to="/onboarding" className="hk-motion-fade">
                PERSONA
              </Link>
              <span>/</span>
              <Link to="/settings" className="hk-motion-fade">
                ATELIER
              </Link>
              <span>/</span>
              <Link to="/logs" className="hk-motion-fade">
                CHRONICLE
              </Link>
            </span>
          </div>
          <div>© HaKochuu Atelier</div>
        </div>
      </footer>

      <FloatingLogDrawer />
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AppLayout />
    </BrowserRouter>
  );
}

export default App;
