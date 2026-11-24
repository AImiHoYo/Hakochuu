import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { HkSelect } from "../common/HkSelect";
import {
  LogServiceId,
  LogServiceInfo,
  fetchServiceLogs,
  listLogServices,
  subscribeServiceLogStream,
} from "../../services/api";

const DEFAULT_SERVICES: Array<{ service: LogServiceId; label: string; port: number }> = [
  { service: "gateway", label: "Gateway", port: 23333 },
  { service: "vllm", label: "vLLM", port: 11434 },
  { service: "asr", label: "ASR", port: 23336 },
  { service: "tts", label: "TTS", port: 23334 },
  { service: "vc", label: "Seed-VC", port: 23335 },
];

const MAX_BUFFER_LINES = 2000;

function classifyLineLevel(line: string): "error" | "warn" | "debug" | "info" {
  const upper = line.toUpperCase();
  if (/\b(ERROR|CRITICAL|EXCEPTION|TRACEBACK)\b/.test(upper)) {
    return "error";
  }
  if (/\bWARN(?:ING)?\b/.test(upper) || upper.includes("DEBUG_WARNING")) {
    return "warn";
  }
  if (/\bDEBUG\b/.test(upper) || line.includes("[Debug]")) {
    return "debug";
  }
  return "info";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

interface LogMonitorPanelProps {
  compact?: boolean;
}

export function LogMonitorPanel({ compact = false }: LogMonitorPanelProps) {
  const [services, setServices] = useState<LogServiceInfo[]>([]);
  const [activeService, setActiveService] = useState<LogServiceId>("gateway");
  const [tailLines, setTailLines] = useState<number>(compact ? 100 : 200);
  const [levelFilter, setLevelFilter] = useState<string>("");
  const [keyword, setKeyword] = useState<string>("");
  const [debouncedKeyword, setDebouncedKeyword] = useState<string>("");

  const [streaming, setStreaming] = useState<boolean>(true);
  const [autoScroll, setAutoScroll] = useState<boolean>(true);
  const [streamStatus, setStreamStatus] = useState<"connecting" | "live" | "paused" | "error">(
    "connecting"
  );
  const [lines, setLines] = useState<string[]>([]);
  const [copyHint, setCopyHint] = useState<string>("");
  const [loadingSnapshot, setLoadingSnapshot] = useState<boolean>(false);

  const viewerRef = useRef<HTMLDivElement | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedKeyword(keyword.trim());
    }, 280);
    return () => window.clearTimeout(timer);
  }, [keyword]);

  const refreshServices = useCallback(async () => {
    try {
      const list = await listLogServices();
      setServices(list);
    } catch {
      /* 后端不可达时保留默认列表 */
    }
  }, []);

  useEffect(() => {
    void refreshServices();
    const timer = window.setInterval(() => {
      void refreshServices();
    }, 8000);
    return () => window.clearInterval(timer);
  }, [refreshServices]);

  const loadSnapshot = useCallback(async () => {
    setLoadingSnapshot(true);
    try {
      const resp = await fetchServiceLogs(activeService, {
        lines: tailLines,
        level: levelFilter || undefined,
        q: debouncedKeyword || undefined,
      });
      setLines(resp.lines);
    } catch (err) {
      setLines([`[前端提示] 日志拉取失败: ${err instanceof Error ? err.message : String(err)}`]);
    } finally {
      setLoadingSnapshot(false);
    }
  }, [activeService, tailLines, levelFilter, debouncedKeyword]);

  useEffect(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    if (!streaming) {
      setStreamStatus("paused");
      void loadSnapshot();
      return;
    }

    setLines([]);
    setStreamStatus("connecting");

    const es = subscribeServiceLogStream(
      activeService,
      {
        lines: tailLines,
        level: levelFilter || undefined,
        q: debouncedKeyword || undefined,
      },
      (line) => {
        setStreamStatus("live");
        setLines((prev) => {
          const next = [...prev, line];
          return next.length > MAX_BUFFER_LINES ? next.slice(-MAX_BUFFER_LINES) : next;
        });
      },
      () => {
        setStreamStatus("error");
      }
    );

    es.onopen = () => {
      setStreamStatus("live");
    };
    esRef.current = es;

    return () => {
      es.close();
      if (esRef.current === es) {
        esRef.current = null;
      }
    };
  }, [activeService, tailLines, levelFilter, debouncedKeyword, streaming, loadSnapshot]);

  useEffect(() => {
    if (autoScroll && viewerRef.current) {
      viewerRef.current.scrollTop = viewerRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  const activeMeta = useMemo(() => {
    const found = services.find((s) => s.service === activeService);
    if (found) return found;
    const def = DEFAULT_SERVICES.find((d) => d.service === activeService) ?? DEFAULT_SERVICES[0];
    return {
      ...def,
      log_file: "",
      available: false,
      size_bytes: 0,
      updated_at: null,
      healthy: false,
    };
  }, [services, activeService]);

  const handleCopy = async () => {
    const text = lines.join("\n");
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopyHint("已复制");
      window.setTimeout(() => setCopyHint(""), 1800);
    } catch {
      setCopyHint("复制失败");
    }
  };

  const handleDownload = () => {
    const text = lines.join("\n");
    if (!text) return;
    const blob = new Blob([text + "\n"], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${activeService}-${new Date().toISOString().replace(/[:.]/g, "-")}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const tabItems =
    services.length > 0
      ? services
      : DEFAULT_SERVICES.map((d) => ({
          ...d,
          available: false,
          size_bytes: 0,
          updated_at: null,
          healthy: false,
          log_file: "",
        }));

  const statusTag =
    streamStatus === "live"
      ? "LIVE"
      : streamStatus === "connecting"
        ? "SYNC"
        : streamStatus === "paused"
          ? "PAUSED"
          : "OFFLINE";

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: 0, flex: 1 }}>
      {/* 5 个服务进程切换 Tab */}
      <div className="hk-tabs" style={{ gap: compact ? "1rem" : "1.75rem", marginBottom: "0.75rem" }}>
        {tabItems.map((item) => {
          const isActive = item.service === activeService;
          return (
            <button
              key={item.service}
              type="button"
              className={`hk-tab ${isActive ? "hk-tab--active" : ""}`}
              onClick={() => setActiveService(item.service)}
            >
              <span
                className={`hk-status-diamond ${
                  item.healthy ? "hk-status-diamond--ok" : "hk-status-diamond--down"
                }`}
              />
              <span>{item.label}</span>
              <span className="hk-num hk-secondary" style={{ fontSize: "var(--text-xs)" }}>
                :{item.port}
              </span>
            </button>
          );
        })}
      </div>

      {/* 元信息行 */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "0.65rem",
          fontSize: "var(--text-xs)",
          color: "var(--color-ink-secondary)",
          marginBottom: "0.65rem",
        }}
      >
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.85rem" }}>
          <span className="hk-tag-brace">{statusTag}</span>
          <span>
            端口：<strong>{activeMeta.healthy ? "在线" : "离线"}</strong>
          </span>
          <span>
            文件：
            {activeMeta.available
              ? `${activeMeta.log_file || activeService} (${formatBytes(activeMeta.size_bytes)})`
              : "未生成"}
          </span>
          <span className="hk-num">{lines.length} lines</span>
        </div>
        <button
          type="button"
          className="hk-btn hk-btn--ghost hk-btn--sm"
          onClick={() => void refreshServices()}
        >
          刷新状态
        </button>
      </div>

      {/* 筛选与控制工具栏 */}
      <div className="hk-log-toolbar">
        <HkSelect
          compact
          className="hk-log-toolbar__select"
          value={String(tailLines)}
          ariaLabel="尾部行数"
          options={[
            { value: "100", label: "100 行" },
            { value: "200", label: "200 行" },
            { value: "500", label: "500 行" },
          ]}
          onChange={(v) => setTailLines(Number(v))}
        />

        <HkSelect
          compact
          className="hk-log-toolbar__select"
          value={levelFilter}
          ariaLabel="日志级别"
          options={[
            { value: "", label: "全部级别" },
            { value: "ERROR", label: "ERROR" },
            { value: "WARNING", label: "WARNING" },
            { value: "INFO", label: "INFO" },
            { value: "DEBUG", label: "DEBUG" },
          ]}
          onChange={setLevelFilter}
        />

        <input
          type="text"
          className="hk-input hk-log-toolbar__search"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="过滤关键字 (latency_ms / gen_id)..."
        />

        <label className="hk-checkbox">
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(e) => setAutoScroll(e.target.checked)}
          />
          自动滚屏
        </label>

        <button
          type="button"
          className={`hk-btn hk-btn--sm ${streaming ? "hk-btn--primary" : ""}`}
          onClick={() => setStreaming((s) => !s)}
        >
          {streaming ? "暂停" : "恢复"}
        </button>

        {!streaming ? (
          <button
            type="button"
            className="hk-btn hk-btn--ghost hk-btn--sm"
            onClick={() => void loadSnapshot()}
            disabled={loadingSnapshot}
          >
            {loadingSnapshot ? "加载中" : "快照"}
          </button>
        ) : null}

        <button
          type="button"
          className="hk-btn hk-btn--ghost hk-btn--sm"
          onClick={() => void handleCopy()}
        >
          {copyHint || "复制"}
        </button>

        {!compact ? (
          <button
            type="button"
            className="hk-btn hk-btn--ghost hk-btn--sm"
            onClick={handleDownload}
          >
            下载
          </button>
        ) : null}

        <button
          type="button"
          className="hk-btn hk-btn--ghost hk-btn--sm"
          onClick={() => setLines([])}
        >
          清屏
        </button>
      </div>

      {/* 去黑化暖深棕日志视口 */}
      <div
        ref={viewerRef}
        className="hk-terminal"
        style={{
          minHeight: compact ? 220 : 460,
          maxHeight: compact ? 310 : 620,
          flex: 1,
        }}
      >
        {lines.length === 0 ? (
          <div style={{ opacity: 0.65, padding: "0.75rem 0.25rem" }}>
            {activeMeta.available
              ? "暂无匹配日志行，等待新日志写入或调整过滤条件。"
              : "该服务日志文件尚未生成，启动服务端后将自动显示。"}
          </div>
        ) : (
          lines.map((line, idx) => {
            const lvl = classifyLineLevel(line);
            return (
              <div key={idx} className={`hk-terminal__line hk-terminal__line--${lvl}`}>
                {line}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
