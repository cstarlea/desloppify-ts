## VS Code Copilot Overlay

VS Code Copilot supports native subagents via `.github/agents/` definitions.
Use them for context-isolated subjective reviews.

### Review workflow

Define a reviewer in `.github/agents/desloppify-ts-reviewer.md`:

```yaml
---
name: desloppify-ts-reviewer
tools: ['read', 'search']
---
```

Use the prompt from the "Reviewer agent prompt" section above.

Define an orchestrator in `.github/agents/desloppify-ts-review-orchestrator.md`:

```yaml
---
name: desloppify-ts-review-orchestrator
tools: ['agent', 'read', 'search']
agents: ['desloppify-ts-reviewer']
---
```

Split dimensions across `desloppify-ts-reviewer` calls (Copilot runs them concurrently), merge assessments and findings, then import.

<!-- desloppify-ts-overlay: copilot -->
<!-- desloppify-ts-end -->
