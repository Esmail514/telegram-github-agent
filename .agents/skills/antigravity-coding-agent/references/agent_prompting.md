# Antigravity Task Prompting Reference

## 1. Structure of an Agent Prompt

A high-performing agent task prompt should contain:

1. **Repository Metadata**: Full repository name, default branch, target working branch.
2. **Issue Details**: Issue number, title, labels, and original description.
3. **Repository Structure**: Condensed directory tree (max depth 3-4, excluding `.git`, `node_modules`, `__pycache__`).
4. **Key Configuration Files**: Content snippets of `package.json`, `pyproject.toml`, `Cargo.toml`, etc.
5. **Strict Constraints**:
   - Do NOT push directly to `main` or `master`.
   - Do NOT commit secrets or credentials.
   - Run tests and linters before reporting completion.
   - Limit fix iterations to `settings.MAX_FIX_ITERATIONS`.

## 2. Issue Polisher Prompt Schema

When prompting an LLM or Antigravity to polish a GitHub issue:

```json
{
  "title": "<improved, concise title (≤72 chars)>",
  "body": "<improved Markdown body — clear problem statement, steps to reproduce, acceptance criteria>",
  "labels": ["label1", "label2"],
  "explanation": "<one or two sentences explaining what was improved>"
}
```
