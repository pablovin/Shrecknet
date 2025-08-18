"use client";
import Image from "next/image";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useTables } from "@/app/lib/useTables";
import Link from "next/link";
import { Users2, Book, Calendar, ArrowRight } from "lucide-react";

interface TableMember {
  id: number;
  nickname: string;
  image_url?: string | null;
}

interface TableItem {
  id: number;
  name: string;
  world_name: string;
  crest_url?: string | null;
  members: TableMember[];
  latest_session?: string | null;
  next_session?: string | null;
}

export default function UserTablesPage() {
  const { tables } = useTables();
  const now = Date.now();
  const sortedTables = [...tables].sort((a, b) => {
    const aDiff = a.next_session
      ? new Date(a.next_session).getTime() - now
      : Infinity;
    const bDiff = b.next_session
      ? new Date(b.next_session).getTime() - now
      : Infinity;
    return aDiff - bDiff;
  });

  return (
    <DashboardLayout>
      <div className="w-full max-w-5xl mx-auto px-2 py-10 min-h-screen relative">
        <div className="flex justify-between items-center mb-8">
          <h1 className="text-3xl font-extrabold font-serif text-[var(--primary)] tracking-wider flex items-center gap-2">
            <Users2 className="w-7 h-7 text-[var(--primary)]" />
            My Adventuring Parties
          </h1>
        </div>

        <ul className="grid gap-7 sm:grid-cols-1">
          {sortedTables.map((t: TableItem) => (
            <li
              key={t.id}
              className="relative flex flex-col min-h-[154px] rounded-3xl border border-[var(--primary)]/15 bg-white shadow-lg overflow-hidden group hover:shadow-2xl hover:border-[var(--primary-dark)] transition"
            >
              <div className="flex items-center gap-4 px-5 pt-5 pb-2">
                <Image
                  src={t.crest_url || "/images/worlds/new_game.png"}
                  alt={t.name}
                  width={72}
                  height={72}
                  className="w-16 h-16 rounded-xl object-cover border-1 border-[var(--primary)] bg-white shadow-md"
                />
                <div className="flex-1 min-w-0">
                  <Link
                    href={`/user_table/sessions/${t.id}`}
                    className="block text-xl font-serif font-bold truncate text-[var(--primary)] hover:underline transition"
                  >
                    {t.name}
                  </Link>
                  <span className="inline-flex items-center gap-1 text-xs font-semibold bg-[var(--primary)]/10 text-[var(--primary)] rounded px-2 py-0.5 mt-1 mr-2">
                    <Book className="w-4 h-4 inline" /> {t.world_name}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <ArrowRight className="w-5 h-5 text-[var(--primary)]" />
                  <Link
                    href={`/user_table/sessions/${t.id}`}
                    className="text-xs font-semibold text-[var(--primary)] underline"
                  >
                    Sessions
                  </Link>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-3 px-5 pb-2">
                {t.members.map((m: TableMember) => (
                  <div
                    key={m.id}
                    className="flex items-center gap-2 bg-[var(--primary)]/5 rounded-full px-2 py-1 shadow text-xs"
                  >
                    <Image
                      src={m.image_url || "/images/avatars/default.png"}
                      alt={m.nickname}
                      width={28}
                      height={28}
                      className="w-7 h-7 rounded-full object-cover border-2 border-[var(--primary)]"
                    />
                    <span className="font-semibold text-[var(--primary-dark)]">
                      {m.nickname}
                    </span>
                  </div>
                ))}
              </div>
              <div className="flex flex-wrap justify-between items-center px-5 pb-3 pt-2 border-t border-[var(--primary)] bg-[var(--primary)]/5 mt-3">
                <div className="flex items-center gap-2 text-xs text-[var(--primary-dark)]">
                  <Calendar className="w-4 h-4" />
                  {t.latest_session ? (
                    <span>
                      Last:{" "}
                      {new Date(t.latest_session).toLocaleDateString(
                        undefined,
                        {
                          timeZone: "UTC",
                        },
                      )}{" "}
                      (
                      {Math.floor(
                        (Date.now() - new Date(t.latest_session).getTime()) /
                          (1000 * 60 * 60 * 24),
                      )}{" "}
                      days ago)
                    </span>
                  ) : (
                    <span>No sessions yet</span>
                  )}
                </div>
                {t.next_session && (
                  <div className="flex items-center gap-2 text-xs text-[var(--primary)] font-semibold">
                    <Calendar className="w-4 h-4" />
                    Next:{" "}
                    {new Date(t.next_session).toLocaleDateString(undefined, {
                      timeZone: "UTC",
                    })}{" "}
                    (in{" "}
                    {Math.ceil(
                      (new Date(t.next_session).getTime() - Date.now()) /
                        (1000 * 60 * 60 * 24),
                    )}{" "}
                    days)
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </DashboardLayout>
  );
}
