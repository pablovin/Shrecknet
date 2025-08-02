"use client";
import ModalContainer from "../template/modalContainer";
import { useTranslation } from "../../hooks/useTranslation";

export default function NewsDialog({ open, onClose, news }) {
  const { t } = useTranslation();
  if (!open) return null;
  return (
    <ModalContainer title={t("newsboard")} onClose={onClose} className="max-w-2xl">
      {(!news || news.length === 0) ? (
        <p>{t("no_news")}</p>
      ) : (
        <ul className="space-y-4">
          {news.map((n) => (
            <li key={n.id} className="border-b pb-2">
              <div className="flex justify-between">
                <span className="font-semibold">{n.title}</span>
                <span className="text-xs">{new Date(n.created_at).toLocaleDateString()}</span>
              </div>
              <span className="text-xs italic">{n.type}</span>
              <p className="text-sm mt-1 whitespace-pre-line">{n.description}</p>
            </li>
          ))}
        </ul>
      )}
    </ModalContainer>
  );
}
