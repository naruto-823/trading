/**
 * useQuoteWebSocket
 *
 * 通过 WebSocket 订阅实时报价，服务端每 3 秒推送一次。
 * 断线后自动指数退避重连（1s → 2s → 4s → … → 最大 30s）。
 * 组件卸载或 symbols 变化时自动关闭旧连接。
 */

import { useEffect, useRef, useState, useCallback } from "react";
import type { QuoteData } from "@/api/client";

type QuoteMap = Record<string, QuoteData>;

type ConnectionStatus = "connecting" | "connected" | "reconnecting" | "closed";

const WS_URL = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/api/ws/quotes`;
const MAX_RECONNECT_DELAY_MS = 30_000;

export function useQuoteWebSocket(symbols: string[]): {
  quoteMap: QuoteMap;
  status: ConnectionStatus;
} {
  const [quoteMap, setQuoteMap] = useState<QuoteMap>({});
  const [status, setStatus] = useState<ConnectionStatus>("closed");

  // 用 ref 持有 symbols，避免 effect 频繁重建
  const symbolsRef = useRef<string[]>(symbols);
  symbolsRef.current = symbols;

  // 用 ref 持有 WebSocket 实例，方便在 cleanup 里关闭
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectDelayRef = useRef<number>(1_000);
  const unmountedRef = useRef(false);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (unmountedRef.current) return;
    if (symbolsRef.current.length === 0) return;

    setStatus("connecting");
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      if (unmountedRef.current) {
        ws.close();
        return;
      }
      // 发送 symbol 列表
      ws.send(JSON.stringify({ symbols: symbolsRef.current }));
      setStatus("connected");
      reconnectDelayRef.current = 1_000; // 连接成功后重置退避时间
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data as string);
        if (message.type === "quotes" && Array.isArray(message.data)) {
          const updatedMap: QuoteMap = {};
          for (const quote of message.data as QuoteData[]) {
            updatedMap[quote.symbol] = quote;
          }
          setQuoteMap((prev) => ({ ...prev, ...updatedMap }));
        }
      } catch {
        // 忽略解析错误
      }
    };

    ws.onerror = () => {
      // onerror 后必然触发 onclose，在 onclose 里统一处理重连
    };

    ws.onclose = () => {
      wsRef.current = null;
      if (unmountedRef.current) return;

      setStatus("reconnecting");
      const delay = reconnectDelayRef.current;
      reconnectDelayRef.current = Math.min(delay * 2, MAX_RECONNECT_DELAY_MS);

      reconnectTimerRef.current = setTimeout(() => {
        if (!unmountedRef.current) connect();
      }, delay);
    };
  }, []);

  useEffect(() => {
    unmountedRef.current = false;

    if (symbols.length === 0) {
      setStatus("closed");
      return;
    }

    // 关闭旧连接（symbols 变化时）
    clearReconnectTimer();
    if (wsRef.current) {
      // 标记为主动关闭，防止触发重连
      const oldWs = wsRef.current;
      wsRef.current = null;
      oldWs.close();
    }

    reconnectDelayRef.current = 1_000;
    connect();

    return () => {
      unmountedRef.current = true;
      clearReconnectTimer();
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setStatus("closed");
    };
    // symbols.join 作为依赖，避免数组引用变化导致不必要的重连
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols.join(",")]);

  return { quoteMap, status };
}
