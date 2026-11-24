import { useRef, useState } from "react";

interface AudioFilePickerProps {
  accept?: string;
  selectedFileName?: string | null;
  placeholder?: string;
  buttonLabel?: string;
  disabled?: boolean;
  onSelect: (file: File | null) => void;
}

export function AudioFilePicker({
  accept = "audio/*",
  selectedFileName,
  placeholder = "拖拽音频文件至此，或点击右侧选择 (.wav / .flac / .mp3)",
  buttonLabel = "选择音频",
  disabled = false,
  onSelect,
}: AudioFilePickerProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const handleOpen = () => {
    if (disabled) return;
    inputRef.current?.click();
  };

  const handleChange = (file: File | null) => {
    onSelect(file);
    if (inputRef.current) {
      inputRef.current.value = "";
    }
  };

  return (
    <div
      className={`hk-file-drop ${dragOver ? "hk-file-drop--over" : ""} ${
        disabled ? "hk-file-drop--disabled" : ""
      }`}
      onClick={handleOpen}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        if (disabled) return;
        const file = e.dataTransfer.files?.[0] ?? null;
        if (file) {
          handleChange(file);
        }
      }}
      role="button"
      tabIndex={disabled ? -1 : 0}
      onKeyDown={(e) => {
        if (!disabled && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          handleOpen();
        }
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hk-file-drop__input"
        disabled={disabled}
        onChange={(e) => handleChange(e.target.files?.[0] ?? null)}
      />

      <span
        className={`hk-file-drop__text ${
          selectedFileName ? "hk-file-drop__text--selected" : ""
        }`}
      >
        {selectedFileName || placeholder}
      </span>

      <span className="hk-btn hk-btn--sm hk-file-drop__btn">
        {selectedFileName ? "更换音频" : buttonLabel}
      </span>
    </div>
  );
}
