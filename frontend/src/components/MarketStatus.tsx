import { useEffect, useState } from "react";
import { Clock } from "lucide-react";

type Session = "pre" | "regular" | "post" | "closed";

const SESSION_LABEL: Record<Session, string> = {
  pre: "盘前交易",
  regular: "盘中交易",
  post: "盘后交易",
  closed: "休市",
};

const SESSION_STYLE: Record<Session, string> = {
  pre: "bg-amber-100 text-amber-700 border-amber-200",
  regular: "bg-emerald-100 text-emerald-700 border-emerald-200",
  post: "bg-sky-100 text-sky-700 border-sky-200",
  closed: "bg-slate-100 text-slate-600 border-slate-200",
};

/**
 * 把 UTC 时间转成美东时间（自动处理夏令时）。
 * 与后端 _et_now 同样的轻量算法，避免 client 引入额外时区库。
 */
function getEtDate(now: Date): Date {
  const utcMs = now.getTime();
  const year = now.getUTCFullYear();

  const marchFirst = Date.UTC(year, 2, 1);
  const marchFirstDow = new Date(marchFirst).getUTCDay();
  const marchFirstSundayOffset = (7 - marchFirstDow) % 7;
  // 3 月第二个周日 02:00 ET = 07:00 UTC
  const dstStart = marchFirst + (marchFirstSundayOffset + 7) * 86_400_000 + 7 * 3600_000;

  const novFirst = Date.UTC(year, 10, 1);
  const novFirstDow = new Date(novFirst).getUTCDay();
  const novFirstSundayOffset = (7 - novFirstDow) % 7;
  // 11 月第一个周日 02:00 EDT = 06:00 UTC
  const dstEnd = novFirst + novFirstSundayOffset * 86_400_000 + 6 * 3600_000;

  const isDst = utcMs >= dstStart && utcMs < dstEnd;
  const offsetMs = (isDst ? -4 : -5) * 3600_000;
  return new Date(utcMs + offsetMs);
}

function getUsSession(now: Date): Session {
  const et = getEtDate(now);
  const dow = et.getUTCDay(); // 注意：et 已经是偏移过的 Date，这里用 UTC* 系列读取
  if (dow === 0 || dow === 6) return "closed";
  const minutes = et.getUTCHours() * 60 + et.getUTCMinutes();
  if (minutes >= 4 * 60 && minutes < 9 * 60 + 30) return "pre";
  if (minutes >= 9 * 60 + 30 && minutes < 16 * 60) return "regular";
  if (minutes >= 16 * 60 && minutes < 20 * 60) return "post";
  return "closed";
}

function formatEt(now: Date): string {
  const et = getEtDate(now);
  const hh = String(et.getUTCHours()).padStart(2, "0");
  const mm = String(et.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm} ET`;
}

export default function MarketStatus() {
  const [now, setNow] = useState<Date>(() => new Date());

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(timer);
  }, []);

  const session = getUsSession(now);

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-muted-foreground">🇺🇸 美股</span>
      <span
        className={`inline-flex items-center px-2 py-0.5 rounded-full border font-medium ${SESSION_STYLE[session]}`}
      >
        <span className="relative flex h-2 w-2 mr-1.5">
          {session !== "closed" && (
            <span className="absolute inline-flex h-full w-full rounded-full bg-current opacity-60 animate-ping" />
          )}
          <span className="relative inline-flex rounded-full h-2 w-2 bg-current" />
        </span>
        {SESSION_LABEL[session]}
      </span>
      <span className="text-muted-foreground inline-flex items-center gap-1">
        <Clock className="h-3 w-3" />
        {formatEt(now)}
      </span>
    </div>
  );
}

export { getUsSession, getEtDate };
