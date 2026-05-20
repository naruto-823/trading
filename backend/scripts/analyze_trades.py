from app.db import SessionLocal
from app.models.execution import Execution
from app.models.position import Position
from app.models.account import AccountSnapshot
from datetime import datetime, timedelta

db = SessionLocal()

# 获取最近2天的成交记录
two_days_ago = datetime.now() - timedelta(days=2)
executions = db.query(Execution).filter(Execution.trade_done_at >= two_days_ago).order_by(Execution.trade_done_at.desc()).all()

print('=== 最近2天成交记录 ===')
if executions:
    for e in executions:
        print(f'{e.trade_done_at} | {e.symbol} | {e.side} | {e.quantity}股 @ ${e.price} | 金额: ${e.quantity * e.price:.2f}')
else:
    print('无成交记录')

print('\n=== 当前持仓 ===')
positions = db.query(Position).all()
if positions:
    for p in positions:
        cost_base = p.cost_price * p.quantity
        print(f'{p.symbol} ({p.name}) | {p.quantity}股 | 成本价: ${p.cost_price:.2f} | 现价: ${p.current_price:.2f} | 市值: ${p.market_value:.2f} | 未实现盈亏: ${p.unrealized_pnl:.2f} ({p.unrealized_pnl_ratio:.2f}%)')
else:
    print('无持仓')

print('\n=== 账户概览 ===')
account = db.query(AccountSnapshot).order_by(AccountSnapshot.synced_at.desc()).first()
if account:
    print(f'总资产: ${account.net_assets:.2f}')
    print(f'现金: ${account.total_cash:.2f}')
    print(f'持仓市值: ${account.market_value:.2f}')
    print(f'总盈亏: ${account.total_pnl:.2f}')
    print(f'当日盈亏: ${account.day_pnl:.2f}')
else:
    print('无账户数据')

db.close()
