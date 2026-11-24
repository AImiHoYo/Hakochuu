import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { LogMonitorPanel } from "./LogMonitorPanel";

export function FloatingLogDrawer() {
  const [open, setOpen] = useState(false);
  const location = useLocation();

  // 已在独立 /logs 页面时不再重复遮挡
  if (location.pathname === "/logs") {
    return null;
  }

  return (
    <>
      {open ? (
        <aside className="hk-log-drawer hk-page-enter" aria-label="服务端实时日志菜单">
          <div className="hk-log-drawer__head">
            <div className="hk-log-drawer__title">
              <span>CHRONICLE</span>
              <span className="hk-tag-brace">LOGS</span>
            </div>
            <div className="hk-log-drawer__actions">
              <Link
                to="/logs"
                className="hk-cta-link"
                onClick={() => setOpen(false)}
                style={{ fontSize: "var(--text-xs)" }}
              >
                <span>FULL VIEW</span>
                <span className="hk-btn-circle hk-btn-circle--sm" aria-hidden="true">
                  <span className="hk-btn-circle__arrow">→</span>
                </span>
              </Link>
              <button
                type="button"
                className="hk-btn hk-btn--ghost hk-btn--sm"
                onClick={() => setOpen(false)}
                aria-label="收起日志菜单"
              >
                ×
              </button>
            </div>
          </div>

          <div className="hk-divider-diamond" style={{ marginBottom: "0.65rem" }}>
            <span className="hk-divider-diamond__line" />
          </div>

          <LogMonitorPanel compact />
        </aside>
      ) : null}

      <button
        type="button"
        className={`hk-log-fab ${open ? "hk-log-fab--open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={open ? "收起服务日志" : "展开服务日志"}
        title="服务端实时日志 (Logs)"
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
          aria-hidden="true"
        >
          <path d="M2.5 4.5L6 8L2.5 11.5" />
          <path d="M7.5 11.5H13.5" />
        </svg>
        <span>LOG</span>
      </button>
    </>
  );
}
