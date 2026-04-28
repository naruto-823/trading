"""Mock 数据：模拟长桥 API 返回，用于无凭证时跑通全流程"""

import random
from datetime import datetime, timedelta

MOCK_ACCOUNT_BALANCES = [
    {
        "currency": "HKD",
        "total_cash": 158320.50,
        "net_assets": 892456.78,
        "market_value": 734136.28,
        "unrealized_pl": 45230.15,
    },
    {
        "currency": "USD",
        "total_cash": 12580.30,
        "net_assets": 98750.60,
        "market_value": 86170.30,
        "unrealized_pl": 8920.45,
    },
]

MOCK_POSITIONS = [
    {
        "symbol": "700.HK",
        "symbol_name": "腾讯控股",
        "quantity": 200,
        "available_quantity": 200,
        "cost_price": 320.50,
        "current_price": 368.80,
        "market_value": 73760.00,
        "unrealized_pl": 9660.00,
        "unrealized_pl_ratio": 0.1508,
        "currency": "HKD",
    },
    {
        "symbol": "9988.HK",
        "symbol_name": "阿里巴巴-SW",
        "quantity": 500,
        "available_quantity": 500,
        "cost_price": 85.20,
        "current_price": 118.60,
        "market_value": 59300.00,
        "unrealized_pl": 16700.00,
        "unrealized_pl_ratio": 0.3920,
        "currency": "HKD",
    },
    {
        "symbol": "3690.HK",
        "symbol_name": "美团-W",
        "quantity": 300,
        "available_quantity": 300,
        "cost_price": 142.30,
        "current_price": 165.40,
        "market_value": 49620.00,
        "unrealized_pl": 6930.00,
        "unrealized_pl_ratio": 0.1624,
        "currency": "HKD",
    },
    {
        "symbol": "AAPL.US",
        "symbol_name": "Apple Inc.",
        "quantity": 50,
        "available_quantity": 50,
        "cost_price": 178.50,
        "current_price": 198.30,
        "market_value": 9915.00,
        "unrealized_pl": 990.00,
        "unrealized_pl_ratio": 0.1109,
        "currency": "USD",
    },
    {
        "symbol": "NVDA.US",
        "symbol_name": "NVIDIA Corp.",
        "quantity": 30,
        "available_quantity": 30,
        "cost_price": 680.20,
        "current_price": 875.50,
        "market_value": 26265.00,
        "unrealized_pl": 5859.00,
        "unrealized_pl_ratio": 0.2872,
        "currency": "USD",
    },
    {
        "symbol": "TSLA.US",
        "symbol_name": "Tesla Inc.",
        "quantity": 20,
        "available_quantity": 20,
        "cost_price": 245.80,
        "current_price": 218.60,
        "market_value": 4372.00,
        "unrealized_pl": -544.00,
        "unrealized_pl_ratio": -0.1107,
        "currency": "USD",
    },
]


def _generate_mock_executions() -> list[dict]:
    """生成模拟成交记录"""
    symbols = ["700.HK", "9988.HK", "3690.HK", "AAPL.US", "NVDA.US", "TSLA.US"]
    sides = ["Buy", "Sell"]
    base_time = datetime.utcnow() - timedelta(days=90)
    executions = []

    for i in range(25):
        symbol = random.choice(symbols)
        market = symbol.rsplit(".", 1)[1]
        side = random.choice(sides)
        is_hk = market == "HK"

        if symbol == "700.HK":
            price = round(random.uniform(300, 380), 2)
            qty = random.choice([100, 200, 400])
        elif symbol == "9988.HK":
            price = round(random.uniform(75, 125), 2)
            qty = random.choice([100, 200, 500])
        elif symbol == "3690.HK":
            price = round(random.uniform(130, 175), 2)
            qty = random.choice([100, 200, 300])
        elif symbol == "AAPL.US":
            price = round(random.uniform(165, 210), 2)
            qty = random.choice([10, 20, 50])
        elif symbol == "NVDA.US":
            price = round(random.uniform(600, 900), 2)
            qty = random.choice([5, 10, 30])
        else:
            price = round(random.uniform(200, 280), 2)
            qty = random.choice([10, 20, 50])

        trade_time = base_time + timedelta(days=random.randint(0, 90), hours=random.randint(9, 15))

        executions.append({
            "execution_id": f"EXE{1000 + i}",
            "order_id": f"ORD{1000 + i}",
            "symbol": symbol,
            "side": side,
            "price": price,
            "quantity": qty,
            "trade_done_at": trade_time,
            "currency": "HKD" if is_hk else "USD",
        })

    executions.sort(key=lambda x: x["trade_done_at"])
    return executions


def _generate_mock_orders() -> list[dict]:
    """生成模拟订单记录"""
    symbols = ["700.HK", "9988.HK", "3690.HK", "AAPL.US", "NVDA.US", "TSLA.US"]
    sides = ["Buy", "Sell"]
    statuses = ["FilledStatus", "FilledStatus", "FilledStatus", "CancelledStatus", "FilledStatus"]
    base_time = datetime.utcnow() - timedelta(days=90)
    orders = []

    for i in range(20):
        symbol = random.choice(symbols)
        market = symbol.rsplit(".", 1)[1]
        side = random.choice(sides)
        status = random.choice(statuses)

        if symbol == "700.HK":
            price = round(random.uniform(300, 380), 2)
            qty = random.choice([100, 200, 400])
        elif symbol == "9988.HK":
            price = round(random.uniform(75, 125), 2)
            qty = random.choice([100, 200, 500])
        elif symbol == "3690.HK":
            price = round(random.uniform(130, 175), 2)
            qty = random.choice([100, 200, 300])
        elif symbol == "AAPL.US":
            price = round(random.uniform(165, 210), 2)
            qty = random.choice([10, 20, 50])
        elif symbol == "NVDA.US":
            price = round(random.uniform(600, 900), 2)
            qty = random.choice([5, 10, 30])
        else:
            price = round(random.uniform(200, 280), 2)
            qty = random.choice([10, 20, 50])

        submit_time = base_time + timedelta(days=random.randint(0, 90), hours=random.randint(9, 15))
        filled_qty = qty if status == "FilledStatus" else 0

        orders.append({
            "order_id": f"ORD{2000 + i}",
            "symbol": symbol,
            "side": side,
            "order_type": "LO",
            "status": status,
            "quantity": qty,
            "filled_qty": filled_qty,
            "avg_price": price if filled_qty > 0 else 0.0,
            "submitted_at": submit_time,
            "updated_at": submit_time + timedelta(minutes=random.randint(1, 30)),
        })

    orders.sort(key=lambda x: x["submitted_at"])
    return orders


MOCK_EXECUTIONS = _generate_mock_executions()
MOCK_ORDERS = _generate_mock_orders()

MOCK_QUOTES = {
    "700.HK": {
        "symbol": "700.HK",
        "symbol_name": "腾讯控股",
        "last_done": 368.80,
        "prev_close": 362.40,
        "open": 363.00,
        "high": 371.20,
        "low": 361.80,
        "volume": 12580000,
        "turnover": 4632800000.0,
        "timestamp": "2026-04-20T15:00:00",
    },
    "9988.HK": {
        "symbol": "9988.HK",
        "symbol_name": "阿里巴巴-SW",
        "last_done": 118.60,
        "prev_close": 115.20,
        "open": 115.80,
        "high": 119.40,
        "low": 114.60,
        "volume": 35620000,
        "turnover": 4198000000.0,
        "timestamp": "2026-04-20T15:00:00",
    },
    "3690.HK": {
        "symbol": "3690.HK",
        "symbol_name": "美团-W",
        "last_done": 165.40,
        "prev_close": 162.80,
        "open": 163.20,
        "high": 167.00,
        "low": 162.00,
        "volume": 8920000,
        "turnover": 1472000000.0,
        "timestamp": "2026-04-20T15:00:00",
    },
    "AAPL.US": {
        "symbol": "AAPL.US",
        "symbol_name": "Apple Inc.",
        "last_done": 198.30,
        "prev_close": 195.60,
        "open": 196.00,
        "high": 199.80,
        "low": 195.20,
        "volume": 48500000,
        "turnover": 9620000000.0,
        "pre_market_price": 199.10,
        "post_market_price": 198.55,
        "timestamp": "2026-04-18T20:00:00",
    },
    "NVDA.US": {
        "symbol": "NVDA.US",
        "symbol_name": "NVIDIA Corp.",
        "last_done": 875.50,
        "prev_close": 860.20,
        "open": 862.00,
        "high": 882.30,
        "low": 858.60,
        "volume": 32100000,
        "turnover": 28050000000.0,
        "pre_market_price": 880.40,
        "post_market_price": 877.20,
        "timestamp": "2026-04-18T20:00:00",
    },
    "TSLA.US": {
        "symbol": "TSLA.US",
        "symbol_name": "Tesla Inc.",
        "last_done": 218.60,
        "prev_close": 222.40,
        "open": 221.80,
        "high": 224.50,
        "low": 216.30,
        "volume": 52800000,
        "turnover": 11560000000.0,
        "pre_market_price": 217.30,
        "post_market_price": 219.10,
        "timestamp": "2026-04-18T20:00:00",
    },
}
