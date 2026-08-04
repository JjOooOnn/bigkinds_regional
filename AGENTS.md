# Development Rules

## Before coding

- Inspect the current repository before proposing changes.
- Do not rely on previous conversation claims when they conflict with the repository.
- State assumptions and unresolved questions.
- Prefer the simplest approach that satisfies the requirement.

## Scope

- Modify only files directly required by the task.
- Do not refactor unrelated code.
- Do not add features that were not requested.
- Match the existing code style and architecture.

## Planning

For non-trivial work:

1. Inspect relevant files.
2. Explain the current flow.
3. Identify affected files.
4. Write a staged implementation plan.
5. Define a verification method for each stage.

Do not edit files during a planning-only request.

## Implementation

- Follow the approved plan.
- Implement one verified stage at a time.
- If the repository conflicts with the plan, report the difference instead of silently changing direction.
- Do not add dependencies unless justified.

## Verification

- Run applicable tests, type checks, lint, and builds.
- Do not claim success without executing the relevant verification.
- Report commands and results.
- Distinguish pre-existing failures from failures caused by the change.

## Git safety

- Do not commit, push, merge, reset, or delete branches unless explicitly requested.
- Do not modify unrelated uncommitted changes.