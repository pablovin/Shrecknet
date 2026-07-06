# Bounded State Updates

## Purpose

Applies validated lifecycle state updates after reflection.

## State Targets

1. `CompanionChatState`
2. `CompanionUserRapport`
3. `TurnReflection` record

## Patch Model

Tools propose patches. Backend applies patches only if valid.

Validation checks include:

1. Allowed trait names
2. Confidence threshold
3. Max delta per turn
4. Trait value clamp to `[min, max]`

## Determinism

1. Same input patch + same prior state -> same applied result.
2. Rejected patches are ignored, not partially applied.

## Persistence

1. Chat state and rapport are stored in SQLite lifecycle tables.
2. Turn reflection is stored for audit/debug.
3. Chat JSON memory remains compatibility-focused.
