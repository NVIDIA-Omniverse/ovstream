<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: LicenseRef-NvidiaProprietary

NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
property and proprietary rights in and to this material, related
documentation and any modifications thereto. Any use, reproduction,
disclosure or distribution of this material and related documentation
without an express license agreement from NVIDIA CORPORATION or
its affiliates is strictly prohibited.
-->
# ovstream Skills Directory

This directory contains structured skill files for AI coding agents (Cursor, Claude Code, Copilot, etc.) working with the ovstream SDK. Each skill is a self-contained reference for a specific ovstream task, with code snippets for both the Python and C APIs.

## Structure

Each subdirectory contains a single `SKILLS.md` file with YAML frontmatter:

```
skills/
  project-setup-c/SKILLS.md
  project-setup-python/SKILLS.md
  application-flow/SKILLS.md
  server-creation/SKILLS.md
  streaming-frames/SKILLS.md
  protocol-selection/SKILLS.md
  callbacks-and-input/SKILLS.md
  cuda-interop/SKILLS.md
  error-handling/SKILLS.md
  shm-consumers/SKILLS.md
```

## SKILLS.md Format

Each file opens with a vendored SPDX HTML comment for license attribution, followed by the YAML frontmatter, followed by the body. Frontmatter parsers that require `---` at byte 0 (rather than after a leading comment block) will not find these files; downstream tooling that consumes these skills is expected to either skip the HTML comment or treat the file as plain Markdown.

```markdown
<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: LicenseRef-NvidiaProprietary
... (full SPDX boilerplate) ...
-->
---
name: skill-name
description: What this skill covers. Use when user asks to [trigger phrases].
---

# Skill Title

## Overview
Brief explanation of when/why you'd use this.

## Python
Step-by-step with code snippets.

## C
Step-by-step with code snippets.

## Key Types / Functions
Quick reference of the API surface involved.

## Common Pitfalls
Gotchas and things to watch out for.
```

## Code Snippet References

Skills reference live code in example files instead of duplicating snippets inline. This keeps code in skills accurate as the API evolves.

### Marker format in source files

Python (`examples/python/*/main.py`):

```python
# [snippet:initialize-sdk]
ovstream.initialize(log_fn=..., log_min_severity=...)
# [/snippet:initialize-sdk]
```

C/C++ (`examples/c/*/main.cu`):

```cpp
// [snippet:initialize-sdk]
ovstream_init_config_t initCfg = {};
ovstream_initialize(&initCfg);
// [/snippet:initialize-sdk]
```

Names are kebab-case and unique within each file.

### Reference format in SKILLS.md

Replace inline code blocks with a blockquote directive:

```markdown
> **Source:** `examples/python/basic_stream/main.py` snippet `initialize-sdk`
```

Agents read the referenced file between the `# [snippet:name]` and `# [/snippet:name]` markers to get the current code.

## Adding a New Skill

1. **Add examples first.** If the workflow isn't already covered by an example under `examples/`, add one (or extend an existing one) with `# [snippet:name]` / `# [/snippet:name]` markers around each illustrative section.
2. Create a new directory under `skills/` named after the skill (kebab-case).
3. Add a `SKILLS.md` file inside it following the format above.
4. **Do not write inline code blocks for API usage.** Reference example snippets using `> **Source:** ...` blockquotes.

## Updating Skills

When you change the ovstream API surface, examples, or conventions that affect an existing skill, update the corresponding `SKILLS.md` to keep it accurate.

## Modifying Examples

- **Preserve snippet markers.** If you move or restructure marked code, update the markers to stay around the illustrative section.
- **Do not remove markers** without also removing or updating every `> **Source:**` reference in `skills/` and every `literalinclude` in `docs/` that points to them.
- **Add markers to new examples.** Every new example function that demonstrates an API workflow should have snippet markers around its illustrative code.
