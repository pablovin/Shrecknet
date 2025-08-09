// AuthCard.tsx — Improved: glassy, theme-matching, elegant but compatible
"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "./AuthProvider";
import {
  getUserCount,
  loginUser,
  registerUser,
  userExists,
} from "../../lib/usersApi";
import { useTranslation } from "../../hooks/useTranslation";

// Material 3 Floating Label Field
function M3TextField({
  label = "",
  name = "",
  type = "text",
  value = "",
  onChange = () => {},
  autoComplete = "",
  required = false,
}) {
  return (
    <div className="relative my-1 w-full">
      <input
        name={name}
        type={type}
        value={value}
        onChange={onChange}
        required={required}
        autoComplete={autoComplete}
        className="peer w-full px-4 pt-6 pb-2 text-black bg-white/90 border-2 border-[var(--primary)] rounded-xl outline-none focus:border-[var(--accent)] transition-colors placeholder-transparent backdrop-blur-lg shadow-inner"
        placeholder=" "
      />
      <label
        htmlFor={name}
        className="absolute left-3 top-1.5 text-sm text-[var(--primary)] font-medium transition-all duration-200 pointer-events-none peer-placeholder-shown:top-4 peer-placeholder-shown:left-4 peer-placeholder-shown:text-base peer-placeholder-shown:text-[var(--accent)] peer-focus:top-1.5 peer-focus:left-3 peer-focus:text-sm peer-focus:text-[var(--primary)]"
      >
        {label}
      </label>
    </div>
  );
}

enum FormMode {
  LOGIN,
  REGISTER,
  FIRST_USER,
}

export default function AuthCard({
  initialError = null,
}: {
  initialError?: string | null;
}) {
  const [formMode, setFormMode] = useState<FormMode>(FormMode.LOGIN);
  const [usersExist, setUsersExist] = useState<boolean | null>(null);
  const [form, setForm] = useState({
    email: "",
    password: "",
    nickname: "",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  const [info, setInfo] = useState<string | null>(null);

  const { setToken, setRedirectAfterLogin, getRedirectAfterLogin } = useAuth();
  const router = useRouter();
  const { t } = useTranslation();

  useEffect(() => {
    getUserCount()
      .then((count) => {
        setUsersExist(count > 0);
        setFormMode(count > 0 ? FormMode.LOGIN : FormMode.FIRST_USER);
      })
      .catch(() => setUsersExist(true));
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm({ ...form, [e.target.name]: e.target.value });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setInfo(null);
    try {
      if (formMode === FormMode.LOGIN) {
        const { access_token } = await loginUser({
          email: form.email,
          password: form.password,
        });
        setToken(access_token);
        const redirectTo = getRedirectAfterLogin() || "/worlds";
        setRedirectAfterLogin(null);
        router.replace(redirectTo);
      } else {
        const role =
          formMode === FormMode.FIRST_USER ? "system admin" : "player";
        await registerUser({
          nickname: form.nickname,
          email: form.email,
          password: form.password,
          role,
          image_url: "/images/default/avatars/logo.png",
        });
        const { access_token } = await loginUser({
          email: form.email,
          password: form.password,
        });
        setToken(access_token);
        setRedirectAfterLogin(null);
        router.replace("/worlds");
      }
    } catch (err: unknown) {
      if (
        formMode === FormMode.LOGIN &&
        typeof err === "object" &&
        err !== null &&
        (err as Record<string, unknown>).detail ===
          "Incorrect email or password"
      ) {
        try {
          const exists = await userExists(form.email);
          if (!exists) {
            setFormMode(FormMode.REGISTER);
            setInfo(t("welcome_first_login"));
            return;
          }
        } catch (e) {}
      }
      if (typeof err === "object" && err !== null) {
        const errObj = err as Record<string, unknown>;
        setError(
          (typeof errObj.detail === "string" && errObj.detail) ||
            (typeof errObj.message === "string" && errObj.message) ||
            "Unknown error!" + err,
        );
      } else {
        setError("Unknown error!");
      }
    } finally {
      setLoading(false);
    }
  };

  if (usersExist === null)
    return <div className="text-white text-center py-8">{t("loading")}</div>;

  return (
    <div
      className="relative bg-white/0 rounded-2xl shadow-2xl p-6 sm:p-9 max-w-[400px] w-full border border-white/0 animate-fadeIn transition-all duration-200 backdrop-blur-[16px]"
      style={{ boxShadow: "0 8px 60px 0 #7b2ff244, 0 2px 10px #2e205988" }}
    >
      <div className="absolute inset-0 bg-gradient-to-br from-[#29196655] via-[#7b2ff211] to-[#36205a44] rounded-2xl pointer-events-none z-0" />
      <div className="relative z-10">
        <div className="flex w-full mb-6 rounded-xl overflow-hidden border border-white/20 backdrop-blur-sm">
          {usersExist && (
            <button
              type="button"
              onClick={() => {
                setFormMode(FormMode.LOGIN);
                setError(null);
                setInfo(null);
                setForm({ email: "", password: "", nickname: "" });
              }}
              className={`flex-1 py-2 text-sm font-semibold transition-colors ${formMode === FormMode.LOGIN ? "bg-gradient-to-r from-[#7b2ff2] to-[#e0c3fc] text-white" : "text-[var(--primary)] hover:bg-white/10 hover:text-white"}`}
            >
              {t("login_tab")}
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              setFormMode(usersExist ? FormMode.REGISTER : FormMode.FIRST_USER);
              setError(null);
              setInfo(null);
              setForm({ email: "", password: "", nickname: "" });
            }}
            className={`flex-1 py-2 text-sm font-semibold transition-colors ${formMode !== FormMode.LOGIN ? "bg-gradient-to-r from-[#7b2ff2] to-[#e0c3fc] text-white" : "text-[var(--primary)] hover:bg-white/10 hover:text-white"}`}
          >
            {t("create_account_tab")}
          </button>
        </div>
        <h3 className="font-bold text-xl mb-5 text-white/90 text-left drop-shadow-sm">
          {formMode === FormMode.LOGIN
            ? t("sign_in_adventure")
            : formMode === FormMode.REGISTER
              ? t("create_your_account")
              : t("welcome_builder")}
        </h3>
        {formMode === FormMode.FIRST_USER && (
          <div className="font-sans text-base text-[var(--accent)] mb-5 text-left">
            {t("no_users_found")} <br />
            <span className="font-semibold text-yellow-400">
              {t("setup_first_account")}
            </span>
          </div>
        )}
        {info && (
          <div className="w-full mb-4 py-2 px-4 bg-green-600/90 rounded-lg text-white text-center text-sm">
            {info}
          </div>
        )}
        {error && (
          <div className="w-full mb-4 py-2 px-4 bg-red-600/90 rounded-lg text-white text-center text-sm">
            {error}
          </div>
        )}
        <form className="w-full flex flex-col gap-5" onSubmit={handleSubmit}>
          {(formMode === FormMode.REGISTER ||
            formMode === FormMode.FIRST_USER) && (
            <M3TextField
              label={t("nickname")}
              name="nickname"
              value={form.nickname}
              onChange={handleChange}
              autoComplete="nickname"
              required
            />
          )}
          <M3TextField
            label={t("email")}
            name="email"
            type="email"
            value={form.email}
            onChange={handleChange}
            autoComplete="email"
            required
          />
          <M3TextField
            label={t("password")}
            name="password"
            type="password"
            value={form.password}
            onChange={handleChange}
            autoComplete={
              formMode === FormMode.LOGIN ? "current-password" : "new-password"
            }
            required
          />
          <button
            className="relative w-full py-3 min-h-[48px] mt-2 rounded-xl bg-gradient-to-r from-[#7b2ff2] to-[#e0c3fc] text-white font-bold text-lg shadow-md hover:from-[#5f166e] hover:to-[#7b2ff2] hover:scale-[1.03] transition-all uppercase tracking-wide overflow-hidden"
            type="submit"
            disabled={loading}
          >
            {loading
              ? t("processing")
              : formMode === FormMode.LOGIN
                ? t("sign_in")
                : t("create_account")}
          </button>
          {formMode === FormMode.LOGIN && (
            <p className="text-sm text-[var(--accent)] text-center mt-2">
              {t("forgot_password_contact_admin")}
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
