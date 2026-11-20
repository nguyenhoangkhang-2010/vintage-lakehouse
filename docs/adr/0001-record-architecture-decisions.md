# ADR 0001: Record architecture decisions

- Status: Accepted

## Context

The project makes several choices (time model, storage stack, evaluation protocol) whose
reasons are easy to forget and hard to defend later, for example in an interview or review.

## Decision

Record each significant decision as a short Architecture Decision Record in `docs/adr`,
numbered sequentially, with status, context, decision and consequences. A decision that is
reversed gets a new ADR that supersedes the old one, instead of an edit.

## Consequences

- Every non-trivial dependency or design choice has a written reason.
- Writing the ADR is part of the pull request that introduces the decision.
