import { Routes, Route, NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  ArrowLeftRight,
  TrendingUp,
  MessageSquare,
  ClipboardCheck,
} from "lucide-react";
import Dashboard from "./pages/Dashboard";
import Executions from "./pages/Executions";
import PnL from "./pages/PnL";
import Chat from "./pages/Chat";
import Decisions from "./pages/Decisions";

const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/executions", label: "成交记录", icon: ArrowLeftRight },
  { to: "/pnl", label: "盈亏分析", icon: TrendingUp },
  { to: "/decisions", label: "决策日志", icon: ClipboardCheck },
  { to: "/chat", label: "AI 助手", icon: MessageSquare },
];

export default function App() {
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
        <div className="p-4 border-t text-xs text-muted-foreground">
          v0.1.0 · 长桥 + Claude
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/executions" element={<Executions />} />
          <Route path="/pnl" element={<PnL />} />
          <Route path="/decisions" element={<Decisions />} />
          <Route path="/chat" element={<Chat />} />
        </Routes>
      </main>
    </div>
  );
}
