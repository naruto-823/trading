# AI Trading

本地 Web 界面查看长桥账户数据（账户概览、持仓、历史成交、盈亏、实时报价），并通过对话式 AI 助手用自然语言提问。

## 快速开始

### 1. 准备凭证

- **长桥 OpenAPI**：前往 [长桥开放平台](https://open.longportapp.com/docs) 申请 App Key / Secret / Access Token
- **Anthropic API Key**：前往 [Anthropic Console](https://console.anthropic.com/) 获取

### 2. 安装与配置

```bash
make setup
# 编辑 .env 填入你的凭证
```

### 3. 启动

```bash
make dev
```

- 后端：http://localhost:8000
- 前端：http://localhost:5173

### 其他命令

```bash
make sync    # 命令行手动同步一次长桥数据
make test    # 运行测试
make lint    # 代码检查
```

## 技术栈

- **后端**：FastAPI + SQLAlchemy + SQLite + longport SDK + Anthropic SDK
- **前端**：React + Vite + TypeScript + TanStack Query + Tailwind CSS + shadcn/ui + Recharts
