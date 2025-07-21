"use client";
// Shrecknet hero with left-aligned true 2x2 grid, logo top, login+hero text right
export const dynamic = "force-dynamic";
import { useAuth } from "./components/auth/AuthProvider";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, Suspense } from "react";
import { useTranslation } from "./hooks/useTranslation";
import Image from "next/image";
import AuthCard from "./components/auth/AuthCard";
import Starfield from "./components/template/Starfield";
import Features from "./components/landing/Features";

function LoginPageContent() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const firstLogin = searchParams.get("firstLogin");
  const { t } = useTranslation();

  useEffect(() => {
    if (!loading && user) {
      router.replace("/main");
    }
  }, [loading, user, router]);

  if (loading) {
    return (
      <div className="w-full text-center py-10 text-primary">{t("loading")}</div>
    );
  }
  if (user) return null;

  return (
    <main className="relative min-h-screen w-full flex items-center justify-center overflow-hidden bg-black">
      {/* Background + Starfield */}
      <Image
        src="/images/background.png"
        alt="Galaxy background"
        fill
        priority
        className="object-cover object-center z-0 pointer-events-none"
        style={{ filter: "brightness(0.90) blur(1.5px)" }}
      />
      <Starfield numStars={300} />
      

      <section className="relative z-10 w-full flex flex-col md:flex-row items-center justify-center gap-0 md:gap-8 lg:gap-16 px-2 md:px-10 py-12 animate-fadeIn">
        {/* LEFT: Logo top, then true 2x2 features grid */}
        <div className="hidden md:flex ">                    
        <Features forceGrid />
          
        </div>
        {/* RIGHT: Hero text + login */}
        <div className="flex flex-col items-center w-full max-w-lg md:max-w-xl lg:max-w-2xl px-3 md:px-0">
          <h1 className="text-3xl md:text-5xl font-extrabold tracking-tight text-center bg-gradient-to-r from-[#e0c3fc] via-[#7b2ff2] to-[#5f166e] bg-clip-text text-transparent drop-shadow-lg mb-1 animate-fadeIn">
            {t("welcome")}
          </h1>
          <div className="mb-3 text-lg md:text-xl font-semibold text-center text-[var(--primary)] animate-fadeIn">
            {t("tagline")}
          </div>
          <div className="mb-8 max-w-xl mx-auto text-base md:text-lg text-center text-white/90 animate-fadeIn">
            {t("description")}
          </div>
          <div
            className="w-full max-w-md mx-auto rounded-2xl shadow-2xl border border-white/20 backdrop-blur-[18px] bg-white/10 p-7 animate-fadeIn flex flex-col gap-2"
            style={{ boxShadow: "0 8px 60px 0 #7b2ff244, 0 2px 10px #2e205988" }}
          >
          <AuthCard initialError={firstLogin ? t("first_login_error") : undefined} />
          </div>
        </div>
      </section>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginPageContent />
    </Suspense>
  );
}
