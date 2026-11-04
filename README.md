# Agora

一个模型无关、可自托管的多 Agent 讨论与迭代平台。

输入主题和背景资料，调用模型生成讨论角色与独立评估者，启动实时讨论；
每个角色在独立 tmux 窗口中运行，页面可以查看每个角色的输出、讨论流、
提案、旁路对话和历史归档。

## 功能

- 自定义讨论主题
- 拖入 / 选择文件作为背景资料（`.md / .txt / .json / .jsonl`）
- 调用模型自动生成讨论角色和评估者
- 讨论角色 / 评估者分开配置数量
- 手动编辑每个角色的名称、立场、目标、颜色
- 实时查看每个角色的 tmux 输出
- 讨论流与角色面板
- 旁路对话：独立于主讨论的顾问聊天
- 提案、评估、投票记录
- 暂停 / 继续 / 停止讨论
- 归档当前讨论，并从左侧历史讨论只读回看
- 导出完整讨论为 Markdown
- 模型配置：OpenAI-compatible / Anthropic-compatible + 连接测试

## 环境要求

- Python 3.10+
- Windows 下通过 WSL 运行 tmux 与 Claude Code CLI
- WSL 中安装：
  - `tmux`
  - `claude`（Claude Code CLI）
- 一个可用的模型 API，用于：
  - 生成角色
  - 旁路对话
  - 讨论角色实际执行

> 注意：讨论角色当前通过 WSL 里的 `claude` CLI 执行，因此实际讨论需要
> Anthropic-compatible 模型入口。OpenAI-compatible 入口可以用于生成角色和
> 旁路对话，但不能直接驱动 Claude Code CLI。

## 启动

```bash
cd C:\Users\30209\Desktop\Agora
python server.py
```

打开：

```text
http://127.0.0.1:8780
```

另开一个终端启动 pane 采集（不启动也能讨论，但页面看不到角色实时输出）：

```bash
cd C:\Users\30209\Desktop\Agora
python capture.py
```

## 使用流程

1. 点顶部“新建 / 设置”
2. 填写模型配置，点“测试连接”
3. 填写讨论主题
4. 拖入或选择背景资料文件
5. 设置讨论角色数量和评估者数量
6. 点“调用模型生成”
7. 检查、编辑角色
8. 点“应用并启动”
9. 在讨论流看发言，在角色面板看每个角色的 tmux 输出
10. 结束后点“归档”，在左侧历史讨论中回看

## 目录

```text
Agora/
  server.py             HTTP 服务与 API
  provider.py           模型配置、角色生成、模型测试
  bus.py                共享消息总线
  voting.py             提案与投票
  role_loop.py          单个讨论角色的循环
  start_discussion.py   创建/停止 tmux 角色窗口
  capture.py            采集 tmux pane 输出
  config.json           角色与服务配置
  static/               前端
  data/                 本地设置、pane、缓存（不提交）
  discussions/          当前讨论记录（不提交）
  archive/              历史归档（不提交）
```

## 数据与安全

- `data/settings.json` 保存模型地址、模型名和 API Key，已加入 `.gitignore`，不会提交。
- `discussions/`、`archive/`、`proposals/`、`evaluation/`、`final/`、
  `subagents/`、`decisions/`、`topic.md`、`background.md` 都是本地运行数据，不提交。
- 平台默认只监听 `127.0.0.1`，没有登录与鉴权，不要直接暴露到公网。
- 归档是本地复制，不会上传到任何外部服务。

## API

```text
GET  /api/health
GET  /api/state?after=N
GET  /api/settings
POST /api/settings                 {provider, base_url, model, api_key}
POST /api/model/test               {provider, base_url, model, api_key}
POST /api/roles/generate           {topic, count, evaluators, background}
POST /api/config                   {topic, roles, background}
POST /api/discussion/start         {new, topic, roles, background}
POST /api/discussion/stop
POST /api/pause                    {paused}
POST /api/message                  {text, role}
GET  /api/side-chat
POST /api/side-chat                {message}
GET  /api/archives
GET  /api/archive/<id>
POST /api/archive
POST /api/background               {text}
GET  /api/export
```

## 已知限制

- 单实例、单“当前讨论”；历史讨论只读回看，不支持分叉恢复。
- 讨论角色依赖 WSL + tmux + Claude Code CLI。
- 没有鉴权，只适合本地使用。
- 评估者是讨论角色的一种，不替代最终的人工裁决。

## 仓库定位

- `Kirke-Strata`：长链路打题 Agent 的分层黑板运行时。
- `Agora`：通用的自讨论、迭代、归档平台，两者独立。
