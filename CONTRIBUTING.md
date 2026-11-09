# Contributing to HomeKit One

## Commit messages

- Do not list an AI agent or assistant as a co-author.
- Do not add `Co-Authored-By: Claude Code <noreply@anthropic.com>` or any equivalent `Co-Authored-By` trailer for an agent.
- Attribute commits to the repository author only.

## Before committing

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```

Keep changes focused. New providers should follow [docs/plugins.md](docs/plugins.md).

## 中文

### Git 提交

- 提交信息中不要将 AI agent / 助手写为协作者。
- 不要添加 `Co-Authored-By: Claude Code <noreply@anthropic.com>` 或任何其他 agent 的 `Co-Authored-By` 尾注。
- 提交只归属仓库作者本人。

### 提交前

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```

保持改动聚焦。新增 provider 请参考 [docs/plugins.md](docs/plugins.md)。
