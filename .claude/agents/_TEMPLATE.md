---
name: agent-name                         # kebab-case, matches filename
description: "Use when [specific trigger condition]. One sentence that helps the router pick this agent over others."
tools: Read, Write, Edit, Bash, Glob, Grep   # comma-separated subset of available tools
model: haiku                             # haiku (fast/cheap) | sonnet (balanced) | opus (complex)
---

## Role

One paragraph describing who this agent IS and what it specialises in within the Yamaha project.

## Scope

What tasks this agent handles. Be specific — the router uses this to decide.

## Process

Numbered steps the agent follows every time it is invoked:
1. Step one
2. Step two
3. Step three

## Checklist

- [ ] Criterion one
- [ ] Criterion two

## Output

Describe the artifact(s) this agent produces (file paths, formats, schemas).

## Integration

Which other agents this agent collaborates with and in what direction.

---

<!-- AGENT AUTHORING NOTES (delete before use)

FRONTMATTER FIELDS
  name        : must match the filename (minus .md)
  description : this IS the routing hint — make it specific and trigger-focused
  tools       : only list tools the agent actually needs (principle of least privilege)
  model       : haiku for simple/fast tasks, sonnet for reasoning, opus for long complex tasks

BODY GUIDELINES
  - Write in second person ("You are …", "You will …")
  - Encode domain knowledge as explicit rules, not vague guidance
  - Include the relevant business rules from CLAUDE.md §4 if the agent touches data
  - Reference exact file paths and column names when known
  - Keep it under 400 lines — long prompts dilute focus

PLACEMENT
  Local  (.claude/agents/)  → project-specific, committed to git
  Global (~/.claude/agents/) → available in every project

-->
