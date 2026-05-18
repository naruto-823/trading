// 持仓护栏规则：分类 + 集中度阈值 + 杠杆识别

export type PositionKind = "leveraged_etf" | "etf" | "option" | "stock";

/** 单只持仓占组合的预警阈值（按种类区分） */
const CONCENTRATION_THRESHOLD: Record<PositionKind, number> = {
  stock: 0.08, // 单股 > 8% 警告
  leveraged_etf: 0.02, // 杠杆 ETF > 2% 警告（任何金额都标杠杆 tag）
  etf: 0.30, // 普通 ETF > 30% 才警告（分散度高，可以更集中）
  option: 0.05, // 期权 > 5% 警告
};

// 期权 symbol 模式：底层代码 + YYMMDD + C/P + strike(8 位)
const OPTION_SYMBOL_RE = /^[A-Z]+\d{6}[CP]\d{6,}\.US$/;

// 杠杆 ETF：名字里含 2x/3x、Bull/Bear、Leveraged、Daily Long/Short
const LEVERAGED_NAME_RE = /\b(2x|3x|Bull|Bear|Leveraged|Daily\s+(Long|Short))\b/i;

// 普通 ETF 识别：名字含 ETF / Trust / Fund / 指数
const ETF_NAME_RE = /\b(ETF|Trust|Fund)\b|指数/i;

export function classifyPosition(symbol: string, name: string): PositionKind {
  if (OPTION_SYMBOL_RE.test(symbol)) return "option";
  if (LEVERAGED_NAME_RE.test(name)) return "leveraged_etf";
  if (ETF_NAME_RE.test(name)) return "etf";
  return "stock";
}

export interface ConcentrationWarning {
  /** 占组合比例 0-1 */
  ratio: number;
  /** 是否触发警告（红色） */
  warn: boolean;
  /** 短标签，如 "⚡杠杆" / "🛡️ETF" / null */
  kindTag: string | null;
}

export function evaluatePosition(
  symbol: string,
  name: string,
  hkdValue: number,
  totalPortfolioHkd: number,
): ConcentrationWarning {
  const kind = classifyPosition(symbol, name);
  const ratio = totalPortfolioHkd > 0 ? Math.abs(hkdValue) / totalPortfolioHkd : 0;
  const warn = ratio >= CONCENTRATION_THRESHOLD[kind];

  let kindTag: string | null = null;
  if (kind === "leveraged_etf") kindTag = "⚡杠杆";
  // option 已经独立 section，不在 stock 表里重复标
  // 普通 etf / stock 不挂标签

  return { ratio, warn, kindTag };
}
