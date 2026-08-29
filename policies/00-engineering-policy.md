# Engineering Policy

This policy defines general engineering rules for this repository.

Language-specific rules are defined in separate policy files. When rules conflict, the more specific policy wins.

## 1. Normative Language

The keywords `MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and `MAY` are normative only when written in uppercase.

* `MUST` means required.
* `MUST NOT` means forbidden.
* `SHOULD` means required unless there is a clear, documented reason not to.
* `SHOULD NOT` means forbidden unless there is a clear, documented reason to.
* `MAY` means allowed but optional.

## 2. Agent Operating Rules

AI agents MUST optimize for correctness, maintainability, and small reviewable changes.

Before changing code, agents MUST:

1. Read the relevant existing code.
2. Identify the smallest safe change.
3. Preserve existing public behavior unless the task explicitly requires changing it.
4. Follow existing repository style unless it conflicts with policy.

Agents MUST NOT:

* Rewrite unrelated code.
* Introduce speculative abstractions.
* Add compatibility layers without a concrete caller.
* Leave dead code, unused exports, unused dependencies, or obsolete comments.
* Hide uncertainty by inventing behavior, APIs, files, or test results.
* Claim checks passed unless they were actually run.

If a task is ambiguous, agents SHOULD make the safest local assumption and document it in the final response. Agents SHOULD ask only when proceeding would likely produce wrong or destructive work.

## 3. Change Discipline

Changes MUST be minimal, coherent, and reviewable.

Each change SHOULD have one clear purpose.

Large changes SHOULD be split into independent steps when possible.

Code movement MUST NOT be mixed with behavior changes unless necessary.

Public APIs MUST NOT be changed unless required by the task or justified by a clear design improvement.

Backward compatibility SHOULD be preserved for public APIs unless the project explicitly allows breaking changes.

## 4. Design Priorities

Prefer designs in this order:

1. Correct.
2. Simple.
3. Explicit.
4. Testable.
5. Maintainable.
6. Efficient.
7. Extensible.

Extensibility MUST NOT be added before there is a real use case.

A simple duplicated expression MAY be better than a premature abstraction.

An abstraction SHOULD exist only when it reduces real duplication, clarifies intent, or isolates volatility.

## 5. Architecture

Code SHOULD be organized around domain concepts, not technical layers alone.

Business logic MUST be separated from I/O, UI, CLI, network, filesystem, database, and environment access where practical.

Side effects SHOULD be isolated behind narrow interfaces.

Core logic SHOULD be deterministic and directly testable.

Modules SHOULD expose a small public surface.

Implementation details MUST remain private unless external callers need them.

Dependencies SHOULD point inward toward stable domain logic.

High-level policy MUST NOT depend on low-level implementation details.

## 6. Dependency Injection

Dependencies SHOULD be passed explicitly.

Use constructor parameters, function parameters, interfaces, traits, callbacks, or configuration objects instead of hidden global access.

Code MUST NOT directly access global state, environment variables, clocks, randomness, filesystem, network, or process state from core logic unless explicitly required.

External effects SHOULD be injectable or mockable in tests.

## 7. DRY and Duplication

Meaningful duplication SHOULD be removed.

Accidental duplication MAY remain when abstraction would reduce clarity.

Do not abstract merely because two code blocks look similar.

Abstract only when the duplicated code has the same reason to change.

## 8. SOLID Principles

Apply SOLID pragmatically.

Single Responsibility:
A unit SHOULD have one primary reason to change.

Open/Closed:
Stable code SHOULD be extendable without modifying unrelated behavior.

Liskov Substitution:
Implementations MUST satisfy the contract expected by their callers.

Interface Segregation:
Interfaces SHOULD be small and specific.

Dependency Inversion:
High-level logic SHOULD depend on abstractions, not concrete infrastructure.

## 9. Naming

Names MUST be precise, honest, and domain-oriented.

Names SHOULD describe purpose, not implementation mechanics.

Avoid vague names such as `manager`, `handler`, `processor`, `helper`, `util`, or `data` unless they are the clearest domain term.

Boolean names SHOULD read clearly at call sites.

Error names SHOULD describe the failed condition.

Tests SHOULD be named after the behavior being verified.

## 10. Functions and Modules

Functions SHOULD be small enough to understand without scrolling through unrelated logic.

A function SHOULD do one thing at one level of abstraction.

Function parameters SHOULD be limited to the data required for the operation.

Long parameter lists SHOULD be replaced by a named input type when that improves clarity.

Modules SHOULD group cohesive behavior.

Modules MUST NOT become dumping grounds for unrelated helpers.

## 11. Error Handling

Expected failures MUST be represented as explicit errors.

Unexpected invariant violations MAY fail fast.

Errors SHOULD preserve useful context.

Errors MUST NOT discard the root cause unless intentionally hiding sensitive details.

Error messages SHOULD help the caller understand what failed and what input or state caused it.

Code MUST NOT silently ignore errors.

Retries MUST be bounded and justified.

## 12. Logging and Diagnostics

Logs SHOULD describe important state transitions, failures, and external interactions.

Logs MUST NOT expose secrets, credentials, tokens, private keys, or sensitive user data.

Debug logs SHOULD be useful for diagnosis without requiring code inspection.

Production code MUST NOT use ad-hoc console output unless the application interface explicitly requires it.

## 13. Configuration

Configuration MUST be explicit.

Defaults SHOULD be safe.

Invalid configuration MUST fail early with a clear error.

Runtime configuration MUST NOT be hidden in unrelated code paths.

Environment-specific behavior SHOULD be isolated.

## 14. Security

Security-sensitive code MUST favor explicitness over cleverness.

Inputs from users, files, networks, processes, and external systems MUST be treated as untrusted.

Untrusted input MUST be validated before use.

Secrets MUST NOT be hardcoded.

Secrets MUST NOT be logged.

Authentication, authorization, cryptography, parsing, and sandbox boundaries MUST NOT be changed casually.

Security checks MUST fail closed.

## 15. Performance

Correctness comes before performance.

Performance optimizations SHOULD target measured or obvious bottlenecks.

Hot paths SHOULD avoid unnecessary allocation, copying, parsing, synchronization, and I/O.

Code MUST NOT trade clarity for performance without evidence or a clear local reason.

Caching MUST define invalidation, lifetime, and memory growth behavior.

## 16. Concurrency

This project is single-threaded apart from the metrics HTTP server started by `prometheus_client`.

Shared mutable state SHOULD be minimized.

Background work MUST have clear shutdown behavior.

## 17. Testing

Behavior changes MUST include tests unless testing is impractical.

Tests MUST verify observable behavior, not implementation trivia.

Tests SHOULD cover success, failure, edge cases, and regression cases.

Tests MUST be deterministic.

Tests MUST NOT depend on real network services, wall-clock timing, local machine state, or test order unless explicitly marked as integration/system tests.

Fixtures SHOULD be small, focused, and documented.

Mocks and fakes SHOULD model behavior, not mirror implementation.

A test that only repeats the implementation logic is not useful.

## 18. Documentation and Comments

Public behavior SHOULD be documented.

Complex or surprising decisions SHOULD be explained close to the code.

Comments MUST explain why, not restate obvious code.

Obsolete comments MUST be updated or removed.

Documentation MUST NOT promise behavior that tests or code do not provide.

Agent-facing documentation SHOULD be concise, current, and normative.

## 19. Dependencies

Dependencies MUST have a clear purpose.

Prefer standard library and existing dependencies over adding new ones.

New dependencies SHOULD be small, maintained, widely used, and compatible with the project license.

Dependencies MUST NOT be added for trivial functionality.

Unused dependencies MUST be removed.

Dependency features SHOULD be minimal.

## 20. Generated Code and Tooling

Generated files MUST be clearly marked.

Generated files SHOULD NOT be manually edited.

Tool configuration SHOULD live in standard project files.

Formatting and linting SHOULD be automated.

Agents SHOULD run the narrowest relevant checks after changes.

Before finalizing, agents SHOULD report:

* What changed.
* Which checks were run.
* Which checks were not run.
* Any remaining risks or follow-up work.
