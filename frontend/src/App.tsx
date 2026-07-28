import { Routes, Route, NavLink } from "react-router-dom";
import { FormEvent, useEffect, useState } from "react";
import {
  LayoutDashboard,
  ArrowLeftRight,
  TrendingUp,
  MessageSquare,
  ClipboardCheck,
  Bell,
  LogOut,
  ShieldCheck,
  LoaderCircle,
} from "lucide-react";
import Dashboard from "./pages/Dashboard";
import Executions from "./pages/Executions";
import PnL from "./pages/PnL";
import Chat from "./pages/Chat";
import Decisions from "./pages/Decisions";
import Alerts from "./pages/Alerts";

const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/executions", label: "成交记录", icon: ArrowLeftRight },
  { to: "/pnl", label: "盈亏分析", icon: TrendingUp },
  { to: "/decisions", label: "决策日志", icon: ClipboardCheck },
  { to: "/alerts", label: "价格告警", icon: Bell },
  { to: "/chat", label: "AI 助手", icon: MessageSquare },
];

export default function App() {
  const [authState, setAuthState] = useState<"loading" | "authenticated" | "anonymous" | "broker-setup">(
    "loading",
  );

  async function finishAuthentication() {
    try {
      const response = await fetch("/api/broker/credentials");
      const result = await response.json();
      setAuthState(response.ok && result?.data?.configured ? "authenticated" : "broker-setup");
    } catch {
      setAuthState("broker-setup");
    }
  }

  useEffect(() => {
    fetch("/api/auth/session")
      .then((response) => (response.ok ? finishAuthentication() : setAuthState("anonymous")))
      .catch(() => setAuthState("anonymous"));
  }, []);

  if (authState === "loading") {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-950 text-slate-300">
        <LoaderCircle className="h-6 w-6 animate-spin" />
      </div>
    );
  }

  if (authState === "anonymous") {
    return <LoginPage onSuccess={finishAuthentication} />;
  }

  if (authState === "broker-setup") {
    return <BrokerSetupPage onSuccess={() => setAuthState("authenticated")} />;
  }

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.assign("/");
  }

  return (
    <div className="flex h-screen bg-background">
      {/* Sidebar */}
      <aside className="w-56 border-r bg-card flex flex-col">
        <div className="p-4 border-b">
          <h1 className="text-lg font-bold tracking-tight">🤖 AI Trading</h1>
        </div>
        <nav className="flex-1 p-2 space-y-1">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                }`
              }
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 border-t space-y-2">
          <button
            onClick={logout}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <LogOut className="h-3.5 w-3.5" />
            安全退出
          </button>
          <div className="px-2 text-xs text-muted-foreground">v0.1.0 · 长桥 + Claude</div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/executions" element={<Executions />} />
          <Route path="/pnl" element={<PnL />} />
          <Route path="/decisions" element={<Decisions />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/chat" element={<Chat />} />
        </Routes>
      </main>
    </div>
  );
}

function BrokerSetupPage({ onSuccess }: { onSuccess: () => void }) {
  const [appKey, setAppKey] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch("/api/broker/credentials", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_key: appKey, app_secret: appSecret, access_token: accessToken }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => null);
        setError(result?.detail ?? "保存失败，请检查凭证");
        return;
      }
      onSuccess();
    } catch {
      setError("暂时无法连接服务器，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-950 px-6 py-10 text-white">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_-20%,rgba(59,130,246,0.24),transparent_42%)]" />
      <div className="relative w-full max-w-lg rounded-2xl border border-white/10 bg-white/[0.06] p-8 shadow-2xl backdrop-blur-xl">
        <div className="mb-7 flex items-start gap-4">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-blue-500/15 ring-1 ring-blue-400/25">
            <ShieldCheck className="h-5 w-5 text-blue-300" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold">连接你的长桥账户</h1>
            <p className="mt-2 text-sm leading-6 text-slate-400">
              每个用户使用独立数据库和独立凭证。凭证加密保存，不会展示给其他用户。
            </p>
          </div>
        </div>

        <form onSubmit={submit} className="space-y-4">
          {[
            ["App Key", appKey, setAppKey, "输入 Longbridge App Key"],
            ["App Secret", appSecret, setAppSecret, "输入 Longbridge App Secret"],
            ["Access Token", accessToken, setAccessToken, "输入 Longbridge Access Token"],
          ].map(([label, value, setter, placeholder]) => (
            <label key={label as string} className="block">
              <span className="mb-2 block text-xs font-medium text-slate-300">{label as string}</span>
              <input
                type="password"
                value={value as string}
                onChange={(event) => (setter as (value: string) => void)(event.target.value)}
                placeholder={placeholder as string}
                required
                className="h-11 w-full rounded-lg border border-white/10 bg-black/20 px-3 text-sm outline-none transition placeholder:text-slate-600 focus:border-blue-400/60 focus:ring-2 focus:ring-blue-500/15"
              />
            </label>
          ))}

          {error && <p className="text-sm text-rose-300">{error}</p>}
          <button
            type="submit"
            disabled={submitting}
            className="flex h-11 w-full items-center justify-center rounded-lg bg-blue-500 text-sm font-semibold transition hover:bg-blue-400 disabled:opacity-60"
          >
            {submitting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : "安全保存并进入"}
          </button>
        </form>
      </div>
    </div>
  );
}

function LoginPage({ onSuccess }: { onSuccess: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch(`/api/auth/${mode === "login" ? "login" : "register"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => null);
        setError(result?.detail ?? (mode === "login" ? "用户名或密码不正确" : "注册失败，请检查填写内容"));
        return;
      }
      onSuccess();
    } catch {
      setError("暂时无法连接服务器，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-950 px-6 text-white">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_-20%,rgba(59,130,246,0.24),transparent_42%)]" />
      <div className="absolute left-1/2 top-1/2 h-80 w-80 -translate-x-1/2 -translate-y-1/2 rounded-full bg-cyan-500/10 blur-3xl" />

      <div className="relative w-full max-w-sm rounded-2xl border border-white/10 bg-white/[0.06] p-8 shadow-2xl shadow-black/40 backdrop-blur-xl">
        <div className="mb-8">
          <div className="mb-5 flex h-11 w-11 items-center justify-center rounded-xl bg-blue-500/15 ring-1 ring-blue-400/25">
            <ShieldCheck className="h-5 w-5 text-blue-300" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">AI Trading</h1>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            {mode === "login" ? "登录你的私人交易工作台" : "创建独立的交易工作台账号"}
          </p>
        </div>

        <div className="mb-5 grid grid-cols-2 rounded-lg bg-black/20 p-1 text-sm">
          {(["login", "register"] as const).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => {
                setMode(item);
                setError("");
              }}
              className={`rounded-md py-2 transition ${
                mode === item ? "bg-white/10 text-white shadow-sm" : "text-slate-500 hover:text-slate-300"
              }`}
            >
              {item === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="space-y-4">
          <label className="block">
            <span className="mb-2 block text-xs font-medium text-slate-300">用户名</span>
            <input
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="h-11 w-full rounded-lg border border-white/10 bg-black/20 px-3 text-sm outline-none transition placeholder:text-slate-600 focus:border-blue-400/60 focus:ring-2 focus:ring-blue-500/15"
              placeholder="输入用户名"
              required
            />
          </label>

          <label className="block">
            <span className="mb-2 block text-xs font-medium text-slate-300">密码</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="h-11 w-full rounded-lg border border-white/10 bg-black/20 px-3 text-sm outline-none transition placeholder:text-slate-600 focus:border-blue-400/60 focus:ring-2 focus:ring-blue-500/15"
              placeholder="输入密码"
              required
            />
          </label>

          {error && <p className="text-sm text-rose-300">{error}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 flex h-11 w-full items-center justify-center rounded-lg bg-blue-500 text-sm font-semibold text-white transition hover:bg-blue-400 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? (
              <LoaderCircle className="h-4 w-4 animate-spin" />
            ) : mode === "login" ? (
              "进入工作台"
            ) : (
              "创建账号并进入"
            )}
          </button>
        </form>

        <p className="mt-6 text-center text-xs text-slate-500">会话使用安全签名的 HttpOnly Cookie，仅限此设备</p>
      </div>
    </div>
  );
}
