"use client";
import AuthGuard from "../../components/auth/AuthGuard";
import DashboardLayout from "../../components/DashboardLayout";
import { useAuth } from "../../components/auth/AuthProvider";
import { useTranslation } from "../../hooks/useTranslation";
import { useState, useEffect } from "react";
import { getNews, createNews } from "../../lib/newsAPI";
import useRoleRedirect from "../../hooks/useRoleRedirect";

export default function NewsAdminPage() {
  const { token } = useAuth();
  const { t } = useTranslation();
  const allowed = useRoleRedirect("system admin");
  const [items, setItems] = useState([]);
  const [form, setForm] = useState({ title: "", type: "news", description: "" });

  useEffect(() => {
    if (!token) return;
    getNews(token).then(setItems).catch(() => {});
  }, [token]);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!token) return;
    await createNews(form, token);
    setForm({ title: "", type: "news", description: "" });
    const data = await getNews(token);
    setItems(data);
  }

  if (!allowed) return null;

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="space-y-4">
          <h1 className="text-2xl font-bold">{t("newsboard")}</h1>
          <form onSubmit={handleSubmit} className="space-y-2">
            <input
              className="w-full p-2 border rounded"
              placeholder="Title"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              required
            />
            <select
              className="w-full p-2 border rounded"
              value={form.type}
              onChange={(e) => setForm({ ...form, type: e.target.value })}
            >
              <option value="feature">feature</option>
              <option value="content">content</option>
              <option value="news">news</option>
            </select>
            <textarea
              className="w-full p-2 border rounded"
              placeholder="Description"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              required
            />
            <button
              type="submit"
              className="px-4 py-2 bg-[var(--primary)] text-[var(--primary-foreground)] rounded"
            >
              {t("add_news")}
            </button>
          </form>
          <ul className="space-y-2">
            {items.map((n) => (
              <li key={n.id} className="border p-2 rounded">
                <div className="flex justify-between">
                  <span className="font-semibold">{n.title}</span>
                  <span className="text-xs">{new Date(n.created_at).toLocaleDateString()}</span>
                </div>
                <span className="text-xs italic">{n.type}</span>
                <p className="text-sm">{n.description}</p>
              </li>
            ))}
          </ul>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
