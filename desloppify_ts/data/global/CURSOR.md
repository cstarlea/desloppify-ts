## Cursor Overlay

Cursor supports native subagents via `.cursor/agents/` definitions. Use them
for context-isolated subjective reviews.

### Review workflow

Define a reviewer agent in `.cursor/agents/desloppify-ts-reviewer.md`:

```markdown
---
name: desloppify-ts-reviewer
description: Scores subjective codebase quality dimensions for desloppify-ts
tools:
  - read
  - search
---
```

Use the prompt from the "Reviewer agent prompt" section above.

Launch multiple reviewer subagents, each with a subset of dimensions.
Each agent writes its output to `results/batch-N.raw.txt` (matching the batch index).
Merge assessments (average where dimensions overlap) and findings, then import.

<!-- desloppify-ts-overlay: cursor -->
<!-- desloppify-ts-end -->
