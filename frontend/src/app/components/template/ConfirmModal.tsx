import { useState } from "react";
import { useTranslation } from "@/app/hooks/useTranslation";

export function ConfirmModal({
  open,
  title,
  message,
  onConfirm,
  onCancel,
  loading = false,
}) {
  const { t } = useTranslation();
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[1200] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-[var(--card-bg)] rounded-3xl shadow-2xl p-8 max-w-sm w-full border border-[var(--border)] flex flex-col items-center">
        {title && (
          <h2 className="text-xl font-bold text-[var(--primary)] mb-4">
            {title}
          </h2>
        )}
        <p className="text-md text-[var(--foreground)] text-center mb-6">
          {message}
        </p>
        <div className="flex gap-3 w-full justify-center mt-2">
          <button
            className="px-6 py-2 rounded-xl font-semibold border border-[var(--border)] text-[var(--primary)] bg-[var(--card-bg)] hover:bg-[var(--surface-variant)] transition"
            onClick={onCancel}
            disabled={loading}
          >
            {t("cancel")}
          </button>
          <button
            className="px-6 py-2 rounded-xl font-bold shadow bg-red-600 text-white hover:bg-red-700 transition disabled:opacity-50"
            onClick={onConfirm}
            disabled={loading}
          >
            {loading ? t("deleting") : t("delete_button")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ConfirmDeleteWorldModal({
  world,
  open,
  onConfirm,
  onCancel,
  loading,
}) {
  const [input, setInput] = useState("");
  const match = input.trim() === world?.name?.trim();
  const { t } = useTranslation();

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[1200] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-[var(--card-bg)] rounded-3xl shadow-2xl p-8 max-w-lg w-full border border-red-200 flex flex-col items-center">
        <h2 className="text-2xl font-bold text-red-600 mb-3">
          {t("delete_world")}
        </h2>
        <p className="text-md text-[var(--foreground)] text-center mb-4">
          {t("confirm_world_delete")} <b>{""}</b> <br />
          <span className="text-[var(--primary)] font-bold">{world?.name}</span>
          ?
          <br />
          <span className="text-red-700">{t("delete_world_warning")}</span>
        </p>
        <p className="text-sm mb-2 text-[var(--foreground)]/70 text-center">
          {t("please_type")} <b>{world?.name}</b> {t("to_confirm")}
        </p>
        <input
          className="w-full max-w-xs px-4 py-2 rounded-xl border border-red-400 bg-[var(--background)] focus:outline-none focus:ring-2 focus:ring-red-500 mb-4 text-center text-lg"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={loading}
          autoFocus
        />
        <div className="flex gap-3 w-full justify-center mt-2">
          <button
            className="px-6 py-2 rounded-xl font-bold shadow bg-red-600 text-white hover:bg-red-700 transition disabled:opacity-50"
            onClick={() => onConfirm()}
            disabled={!match || loading}
          >
            {loading ? t("deleting") : t("delete_button")}
          </button>
          <button
            className="px-6 py-2 rounded-xl font-semibold border border-[var(--border)] text-[var(--primary)] bg-[var(--card-bg)] hover:bg-[var(--surface-variant)] transition"
            onClick={onCancel}
            disabled={loading}
          >
            {t("cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
