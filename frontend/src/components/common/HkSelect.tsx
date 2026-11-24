import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface HkSelectOption {
  value: string;
  label: string;
}

interface HkSelectProps {
  value: string;
  options: HkSelectOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  compact?: boolean;
  ariaLabel?: string;
  className?: string;
}

interface MenuPosition {
  left: number;
  width: number;
  top?: number;
  bottom?: number;
}

export function HkSelect({
  value,
  options,
  onChange,
  placeholder = "请选择",
  disabled = false,
  compact = false,
  ariaLabel,
  className = "",
}: HkSelectProps) {
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState<number>(-1);
  const [menuPos, setMenuPos] = useState<MenuPosition | null>(null);

  const selectedIndex = options.findIndex((o) => o.value === value);
  const selectedOption = selectedIndex >= 0 ? options[selectedIndex] : null;

  const updatePosition = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;
    const openUpward = spaceBelow < 210 && spaceAbove > spaceBelow;

    if (openUpward) {
      setMenuPos({
        left: rect.left,
        width: rect.width,
        bottom: window.innerHeight - rect.top + 4,
      });
    } else {
      setMenuPos({
        left: rect.left,
        width: rect.width,
        top: rect.bottom + 4,
      });
    }
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    updatePosition();
  }, [open, updatePosition]);

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    };

    const handleReposition = () => updatePosition();

    document.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("resize", handleReposition);
    window.addEventListener("scroll", handleReposition, true);

    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("resize", handleReposition);
      window.removeEventListener("scroll", handleReposition, true);
    };
  }, [open, updatePosition]);

  const handleOpenToggle = () => {
    if (disabled) return;
    setOpen((prev) => {
      const next = !prev;
      if (next) {
        setHighlightedIndex(selectedIndex >= 0 ? selectedIndex : 0);
      }
      return next;
    });
  };

  const commitSelection = (idx: number) => {
    const opt = options[idx];
    if (!opt) return;
    onChange(opt.value);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;

    if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        setOpen(false);
      }
      return;
    }

    if (e.key === "Tab") {
      setOpen(false);
      return;
    }

    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        setHighlightedIndex(selectedIndex >= 0 ? selectedIndex : 0);
      } else if (options.length > 0) {
        setHighlightedIndex((prev) => (prev + 1) % options.length);
      }
      return;
    }

    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        setHighlightedIndex(selectedIndex >= 0 ? selectedIndex : Math.max(0, options.length - 1));
      } else if (options.length > 0) {
        setHighlightedIndex((prev) => (prev - 1 + options.length) % options.length);
      }
      return;
    }

    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        setHighlightedIndex(selectedIndex >= 0 ? selectedIndex : 0);
      } else if (highlightedIndex >= 0) {
        commitSelection(highlightedIndex);
      }
    }
  };

  return (
    <div className={`hk-cselect ${compact ? "hk-cselect--compact" : ""} ${className}`}>
      <button
        ref={triggerRef}
        type="button"
        className={`hk-cselect__trigger ${open ? "hk-cselect__trigger--open" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={handleOpenToggle}
        onKeyDown={handleKeyDown}
      >
        <span
          className={`hk-cselect__value ${!selectedOption ? "hk-cselect__value--placeholder" : ""}`}
        >
          {selectedOption ? selectedOption.label : placeholder}
        </span>
        <svg
          className={`hk-cselect__arrow ${open ? "hk-cselect__arrow--open" : ""}`}
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
          aria-hidden="true"
        >
          <path d="M2.25 4.5L6 8.25L9.75 4.5" />
        </svg>
      </button>

      {open && menuPos
        ? createPortal(
            <div
              ref={menuRef}
              role="listbox"
              className={`hk-cselect__menu ${compact ? "hk-cselect__menu--compact" : ""}`}
              style={{
                left: menuPos.left,
                width: menuPos.width,
                top: menuPos.top,
                bottom: menuPos.bottom,
              }}
            >
              {options.length === 0 ? (
                <div className="hk-cselect__empty">暂无可选项</div>
              ) : (
                options.map((opt, idx) => {
                  const isSelected = opt.value === value;
                  const isActive = idx === highlightedIndex;
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      className={`hk-cselect__option ${
                        isSelected ? "hk-cselect__option--selected" : ""
                      } ${isActive ? "hk-cselect__option--active" : ""}`}
                      onMouseEnter={() => setHighlightedIndex(idx)}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => commitSelection(idx)}
                    >
                      <span className="hk-cselect__option-label">{opt.label}</span>
                      {isSelected ? <span className="hk-cselect__diamond" aria-hidden="true" /> : null}
                    </button>
                  );
                })
              )}
            </div>,
            document.body
          )
        : null}
    </div>
  );
}
