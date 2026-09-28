# ShreckWorld — Project Definition and Implementation Plan

> **Status:** Initial architecture / implementation brief  
> **Target project:** Shrecknet  
> **Primary goal:** Replace the Librarian agent with a world-aware knowledge and simulation subsystem capable of grounding, representing, and mechanically resolving actions inside a configured fictional or RPG world.

---

## 1. Why ShreckWorld Exists

Shrecknet already does a good job of representing narrative memory.

It stores canonical entities, scenes, milestones, relationships, CharacterAgents, character perspectives, traits, aspects, goals, and longitudinal changes. A CharacterAgent can therefore have an identity, remember what happened, interpret events subjectively, and decide what it wants to do.

What Shrecknet does **not** currently provide is an authoritative layer that answers a different question:

> **Given the rules and current state of this world, can this entity actually do what it wants to do, and what happens when it tries?**

This distinction is essential.

A CharacterAgent may decide:

> “Cwenhild jumps from one moving horse to another.”

The CharacterAgent is responsible for the **intention**. Its identity, memories, goals, perspectives, dispositions, and current situation explain *why* it chose that action.

The CharacterAgent must **not** decide whether the action succeeds.

ShreckWorld is responsible for understanding the relevant world rules, reading Cwenhild's persistent mechanical state, determining how the attempted action should be resolved, executing that resolution, applying the consequences, and finally producing a narrative description of what actually happened.

The central separation is therefore:

> **CharacterAgent decides what it wants to do.**  
> **ShreckWorld decides what reality allows and what the consequences are.**

ShreckWorld should become the authoritative world-knowledge and world-resolution subsystem of Shrecknet.

---

## 2. Relationship to Existing Shrecknet

Shrecknet is a scene-centric memory system. Its ontology, graph, knowledge, and agent layers evolve together around persistent narrative state.

ShreckWorld should extend that architecture rather than create a parallel application with duplicated concepts.

Existing Shrecknet concepts remain responsible for their current concerns:

- **EntityInstance / graph entities:** canonical entities in the world.
- **Scene / Milestone:** canonical narrative events and chronology.
- **CharacterAgent:** persistent simulation identity embodied by an EntityInstance.
- **Character traits, aspects, goals:** identity and behavioural context.
- **ScenePerspective:** what a CharacterAgent experienced, understood, believed, and remembers.
- **Library Items / PDF ingestion / PdfChunks / embeddings:** source material and retrieval infrastructure.
- **ShreckLLM:** model-provider execution and LLM configuration.

ShreckWorld adds the missing **mechanical embodiment + authoritative world-resolution layer**.

Do not create a second character identity system inside ShreckWorld.

Do not duplicate Scene or EntityInstance.

Do not make the mechanical sheet become the CharacterAgent.

A CharacterAgent describes **who the character is**.

A ShreckWorld embodiment describes **how that character is mechanically represented in one particular world/ruleset**.

---

## 3. Replace Librarian, Do Not Rebuild Its Infrastructure

The current Librarian should stop existing as a user-facing agent concept.

There should no longer be a “Librarian Agent” that users configure or query as a persona.

Instead, its useful technology becomes the **ShreckWorld Knowledge Engine**.

The current Librarian already provides valuable infrastructure:

- structured PDF ingestion;
- `PdfChunk` graph documents;
- document embeddings;
- vector retrieval;
- contextual full-text retrieval;
- exact named-term retrieval;
- Reciprocal Rank Fusion;
- cross-encoder reranking;
- parent/sibling evidence expansion;
- active library-item filtering;
- stable source identifiers;
- page provenance;
- bounding boxes and PDF navigation;
- citation rendering;
- embedding import/export packages;
- multi-pass evidence validation.

**Reuse this. Do not rewrite it unless required.**

The architectural change is ownership.

Old conceptual ownership:

```text
Librarian Agent
    -> selected ontology/library scope
    -> retrieval
    -> answer
```

New conceptual ownership:

```text
ShreckWorld
    -> World configuration
    -> selected Library Items
    -> World Knowledge Engine
    -> query / embodiment / resolution jobs
```

The retrieval engine is no longer the identity of an agent.

It is a capability of ShreckWorld.

Existing implementation areas worth reusing/refactoring include:

```text
shrecknet/app/jobs/librarian/
shrecknet/app/services/pdf_embedding_service.py
shrecknet/app/services/librarian_embedding_package_service.py
shrecknet/app/tasks/librarian_embedding_package.py
Documentation/Agents/Librarian/
```

The implementation agent should inspect the current code before renaming or moving anything. Prefer incremental refactoring with tests kept green over a destructive rewrite.

---

## 4. Core Product Concept

Each usable ShreckWorld represents the **mechanical and knowledge context for a Shrecknet world/campaign**.

A ShreckWorld configuration defines at minimum:

1. which library sources belong to this world;
2. which sources are authoritative and their precedence;
3. whether external/web search is available;
4. how external search may be used;
5. the persistent mechanical embodiment format used by characters/entities;
6. the world-specific mechanical state already accumulated during play.

Example:

```text
World: Arthur 513

ShreckWorld configuration
    Sources
        Pendragon 6E Core Rulebook
        Campaign Book
        Knights & Ladies
        Pablo House Rules

    Web search
        enabled: true
        policy: fallback

    Authority
        House Rules
        Core Rules
        Supplements
        Web
```

The exact data model must integrate with the existing Shrecknet concept that currently represents world/ontology/instance scope. **Do not invent a duplicate “World” hierarchy if Shrecknet already has the necessary owner/scope object.** Inspect the current models and attach ShreckWorld configuration to the correct existing scope.

---

## 5. The Most Important Architectural Principle

### Rules are interpreted on demand. State persists.

ShreckWorld should **not** attempt to convert an entire RPG ruleset into a complete formal simulation ontology before it can be used.

That would turn the project into an enormous manual rule-modelling exercise.

Instead:

- source books remain searchable authoritative knowledge;
- relevant rules are retrieved when a situation requires them;
- LLM reasoning interprets those relevant rules;
- the rules needed for a specific resolution are compiled into a constrained executable specification;
- deterministic code executes that specification.

However, entity mechanical state must **not** be regenerated on every action.

Cwenhild must have one persistent Pendragon character sheet.

If that sheet says:

```text
Horsemanship: 16
Sword: 18
SIZ: 13
```

those values remain true until something explicitly changes them.

A later resolution reads the sheet. It does not infer Horsemanship again from Cwenhild's memories.

This is the fundamental compromise that makes ShreckWorld both flexible and consistent:

> **Dynamic rules. Persistent embodiments. Deterministic resolution.**

---

## 6. Persistent World Embodiments

A CharacterAgent may have a world-specific mechanical embodiment.

For RPG worlds this will often be a character sheet.

Example:

```text
CharacterAgent: Cwenhild
    identity / traits / aspects / goals / perspectives
        |
        +-- ShreckWorld Embodiment: Pendragon
                STR: 15
                DEX: 11
                SIZ: 13
                CON: 14
                APP: 10
                Horsemanship: 16
                Sword: 18
                Glory: 2874
                ...
```

The embodiment is **persistent state**.

It should not be silently reconstructed during an action-resolution request.

### 6.1 How an embodiment can be created

A character sheet must support three complementary sources.

#### Manual

An administrator/user enters or edits values directly.

Manual confirmed values should normally be treated as highly authoritative.

#### Evidence-derived

ShreckWorld uses the CharacterAgent's existing Shrecknet evidence:

- embodied EntityInstance;
- background story;
- traits;
- aspects;
- goals;
- identity revisions;
- relevant canonical scenes;
- ScenePerspectives;
- milestones;
- other grounded graph facts.

The system then proposes mechanical values according to the selected world's definitions.

This should be evidence-grounded and reviewable.

#### Imported

Later integrations may import an authoritative existing sheet from:

- PDF;
- Foundry VTT;
- JSON;
- another supported character-sheet source.

Imports should preserve provenance and should not require the model to re-infer values that are explicitly available.

---

## 7. Character Sheet Definition vs Character Sheet Instance

Do not confuse the definition of a sheet with the values of one character.

ShreckWorld needs a persistent **mechanical schema/template** for a world or ruleset.

For example, a Pendragon sheet definition may describe:

- characteristics;
- derived statistics;
- personality traits;
- passions;
- skills;
- combat skills;
- equipment;
- wounds;
- Glory;
- wealth;
- other system-specific values.

Cwenhild's embodiment is then an **instance** of that definition.

This allows all Pendragon characters in the same ShreckWorld to share the same field definitions while keeping separate values.

The sheet definition itself may eventually come from:

- a manually configured schema;
- a reviewed one-time extraction from authoritative rules;
- a Foundry system definition;
- a supported external integration.

It should **not** be re-generated per character action.

The first implementation may use a flexible JSON-backed schema rather than trying to create SQL columns for every RPG system.

The important properties are:

- typed fields where possible;
- stable field identifiers;
- display metadata;
- validation constraints;
- provenance;
- versioning;
- explicit update history.

---

## 8. Embodiment Provenance

Every mechanically important value should be explainable.

A sheet value should be able to indicate where it came from, for example:

```text
Horsemanship = 16

origin: inferred
evidence:
    - Scene X: years travelling on horseback
    - Aspect: Experienced Rider
rule_basis:
    - Pendragon Core Rulebook, Riding/Horsemanship definition
confidence: 0.89
confirmed: true
```

Or:

```text
Sword = 18

origin: imported_foundry
external_reference: ...
confirmed: true
```

Or:

```text
SIZ = 10

origin: resolution
previous_value: 13
cause: permanent injury from Scene Y
resolution_id: ...
```

This is important because ShreckWorld will continuously evolve mechanical state and must remain auditable.

---

## 9. Embodiment Updates

Once created, a sheet changes only through explicit updates.

Changes can come from several places.

### Deterministic mechanical consequence

Example:

```text
Resolution result:
    lose 3 SIZ
```

The update is direct and should not require an LLM to reinterpret the number.

### Rule-driven advancement

Example:

```text
Character completed a training period.
```

ShreckWorld retrieves the advancement/training rules and determines what changes are allowed.

### Narrative event interpretation

Example:

```text
Cwenhild spends one year training under an exceptional knight.
```

This may require ShreckWorld to interpret the event using the world rules and propose appropriate sheet changes.

### Manual update

An administrator directly changes a field.

All updates should be history-preserving and attributable to their source.

---

## 10. World Sources

Each ShreckWorld configuration selects the Library Items that define its knowledge base.

The same Library Item and its existing embeddings may be reused by multiple worlds.

Do **not** duplicate PDF chunks or embeddings just because two worlds use the same rulebook.

Example:

```text
Library
    Pendragon Core Rulebook
        -> existing PDF
        -> existing PdfChunks
        -> existing embeddings

World A
    uses Core Rulebook

World B
    uses Core Rulebook
```

World membership is a scope/configuration concern, not a document duplication concern.

### 10.1 Source authority

ShreckWorld must be able to distinguish source authority.

At minimum consider:

- house rules;
- core rules;
- official supplements;
- setting/campaign books;
- errata;
- imported custom documents;
- external web results.

The exact representation may be an ordered priority, source type, explicit authority score, or another clear policy.

What matters is that conflicting sources are **not silently flattened into one truth**.

If a house rule overrides a core rule, ShreckWorld should know that.

If two official books disagree, the resolution should preserve that conflict or choose according to configured precedence.

---

## 11. Web Search

Web search should be a capability of the **World**, not a mandatory global capability of every ShreckWorld operation.

Suggested configuration:

```text
web_search:
    enabled: false | true
    policy: fallback | supplemental
```

### `fallback`

Search configured library sources first.

Use web search only when local authoritative evidence is insufficient.

### `supplemental`

Web evidence may be considered alongside configured local sources.

Web evidence must always preserve its provenance and should normally have lower authority than explicitly configured rulebooks/house rules unless the World configuration says otherwise.

A random forum post must never silently override an authoritative uploaded rules source.

Web search is useful for:

- official errata;
- publisher clarifications;
- missing setting information;
- rules not present in the local library;
- external references intentionally allowed by the world owner.

Initial ShreckWorld work should make the capability pluggable even if the first implementation leaves external web execution for a later phase.

---

## 12. ShreckWorld Jobs

ShreckWorld should expose a small set of explicit jobs.

The jobs below describe **product capabilities**, not necessarily final endpoint names. Match the existing Shrecknet job/background-task architecture where practical.

---

### 12.1 `shreckworld.query`

Replaces the Librarian general query.

Purpose:

> Answer a grounded question about the configured world using its selected authoritative sources.

Examples:

```text
How does mounted combat work?
What happens after a Major Wound?
How is Glory awarded?
Who rules Cornwall in 513 according to the configured campaign material?
```

Pipeline:

```text
question
    -> plan information needs
    -> retrieve from World-selected Library Items
    -> optional web fallback/supplement
    -> validate evidence coverage
    -> synthesize grounded answer
    -> return provenance/citations
```

This should reuse the current Librarian Query v2 retrieval/evidence behaviour as much as possible.

The major difference is that the query is scoped by **ShreckWorld**, not by a Librarian Agent.

---

### 12.2 `shreckworld.sheet.define`

Purpose:

> Create or update the mechanical character/entity sheet definition used by a ShreckWorld.

This is not run for every character action.

It creates the persistent template that embodiments use.

Possible inputs:

- manual definition;
- extraction from configured rules;
- imported system definition;
- future Foundry system metadata.

The result must be reviewable/versioned.

A later version may support several embodiment types in one World, e.g. player character, monster, vehicle, settlement, object, etc.

For the first implementation, **character embodiment is the priority**.

---

### 12.3 `shreckworld.embodiment.create`

Purpose:

> Create the persistent mechanical representation of a CharacterAgent inside this ShreckWorld.

Inputs may include:

- CharacterAgent ID;
- World ID/scope;
- manual values;
- current sheet definition;
- import payload/file;
- evidence-inference option.

Possible workflow:

```text
load sheet definition
    -> load CharacterAgent evidence
    -> load explicit/manual/imported values
    -> retrieve world definitions for needed fields
    -> propose missing values
    -> attach provenance/confidence
    -> validate against sheet definition
    -> review/accept
    -> persist embodiment
```

The exact UI/review lifecycle may follow the existing CharacterAgent embodiment-draft pattern where appropriate.

Important:

> Once accepted, these values become persistent state and are used by future resolutions.

---

### 12.4 `shreckworld.embodiment.update`

Purpose:

> Apply explicit mechanical changes to an existing embodiment.

Sources may be:

- manual edit;
- imported synchronization;
- mechanical resolution;
- rule-driven advancement;
- interpreted narrative development.

Pipeline:

```text
current embodiment
    + requested/event-derived changes
    -> validate against sheet definition
    -> resolve conflicts / authority
    -> record provenance
    -> create history/revision
    -> persist new state
```

Never silently regenerate the complete character sheet because one new scene was added.

The system should update only the fields justified by the event/rules.

---

### 12.5 `shreckworld.resolution.preview`

Purpose:

> Determine how an attempted action should be resolved without changing world state.

This job is useful for debugging, GM review, agent planning, UI explanations, and probability analysis.

Example input:

```text
Actor: Cwenhild
Situation: two horses running at speed in heavy rain
Attempt: leap from her horse onto the second horse
```

Pipeline:

```text
attempt
    -> gather current scene/world context
    -> load persistent embodiment
    -> retrieve relevant rules
    -> identify required sheet fields
    -> build Resolution Specification
    -> validate specification
    -> optionally calculate success probabilities
    -> return preview
```

If a required mechanical value is missing, do **not** casually infer a new value inside the resolution and forget it afterward.

Return an explicit missing-state condition or trigger a proper embodiment-update workflow.

---

### 12.6 `shreckworld.resolve`

Purpose:

> Mechanically resolve an attempted action and produce authoritative consequences.

This is the central simulation job.

Pipeline:

```text
1. RECEIVE ATTEMPT
        |
2. LOAD WORLD + CURRENT STATE
        |
3. LOAD PERSISTENT EMBODIMENT(S)
        |
4. RETRIEVE RELEVANT RULES
        |
5. COMPILE RESOLUTION SPECIFICATION
        |
6. VALIDATE RESOLUTION SPECIFICATION
        |
7. DETERMINISTIC EXECUTION
        |
8. BUILD STATE MUTATIONS
        |
9. VALIDATE + APPLY STATE MUTATIONS
        |
10. NARRATE RESULT
        |
11. RETURN RESOLUTION + PROVENANCE
```

This pipeline is explained in detail below.

---

### 12.7 `shreckworld.scene.resolve`

Purpose:

> Resolve a larger scene-level interaction containing multiple participants/actions while keeping all mechanical outcomes consistent with one world state.

This should be built **after** single-action resolution works reliably.

A scene resolver may coordinate:

- action order;
- multiple CharacterAgents;
- NPCs/entities;
- opposed actions;
- chained resolutions;
- resource consumption;
- injuries/statuses;
- movement/state mutation;
- resulting canonical scene/milestone information.

Do not begin implementation here.

Build reliable atomic action resolution first.

---

## 13. Action Resolution in Detail

### Step 1 — Attempt

An actor or orchestrating system submits an attempted action.

The input should distinguish:

- actor;
- intended action;
- target(s), if any;
- relevant Scene/current context;
- optional declared resources/abilities/items;
- execution mode.

The action statement should express **intent**, not predetermined success.

Good:

```text
Cwenhild attempts to leap onto the second horse.
```

Bad:

```text
Cwenhild successfully leaps onto the second horse.
```

---

### Step 2 — Context

Load only the context required to understand the physical/mechanical situation.

This can include:

- current Scene;
- relevant Milestones;
- actor and target entities;
- current location/state;
- equipment;
- statuses/injuries;
- CharacterAgent mechanical embodiment;
- relevant world facts.

Do not dump the entire graph into every prompt.

---

### Step 3 — Rule Retrieval

Use the ShreckWorld Knowledge Engine to find the rules required to adjudicate the action.

One attempted action may require several rules:

- base skill/check;
- situational modifier;
- movement;
- equipment penalty;
- opposed resolution;
- damage/falling;
- critical/fumble effects.

Retrieval should remain evidence-grounded and citation-aware.

---

### Step 4 — Resolution Specification

An LLM may interpret the retrieved prose and compile it into a constrained structured object.

Example conceptual result:

```yaml
resolution_type: roll_under
actor: cwenhild
check:
  field: horsemanship
  base_value: 16
modifiers:
  - reason: both horses moving at speed
    value: -3
  - reason: heavy rain
    value: -2
effective_target: 11
roll:
  expression: 1d20
outcomes:
  success:
    condition: roll <= effective_target
  failure:
    condition: roll > effective_target
    follow_up: falling_resolution
```

The exact schema will need careful design.

The LLM's job is:

> **Translate grounded relevant rules into an executable resolution plan.**

The LLM's job is **not** to choose success.

---

### Step 5 — Validation

Before execution, deterministic code should validate that the specification:

- uses supported primitives;
- references real embodiment fields;
- contains valid arithmetic;
- respects allowed random functions;
- has internally valid conditions;
- references sources for interpreted rules;
- does not request arbitrary code execution;
- does not mutate state outside permitted structures.

Invalid specifications should be repaired/recompiled or returned as an explicit failure.

---

### Step 6 — Deterministic Execution

The mechanical result must come from deterministic application code plus explicit randomness, not from LLM prose.

The execution runtime should support a generic set of primitives, initially such as:

- dice expressions (`1d20`, `3d6`, `5d10`);
- arithmetic;
- target numbers;
- roll-under / roll-over;
- success counting;
- opposed rolls;
- modifiers;
- min/max/clamp;
- tables;
- conditional branches;
- repeated rolls;
- resource costs;
- derived values;
- probability calculation where feasible.

Example:

```text
Horsemanship = 16
Modifier = -5
Target = 11
Roll = 14
Outcome = FAILURE
```

The LLM can explain why the modifier exists.

It must not fabricate the rolled result.

---

## 14. Do Not Use Arbitrary Generated Python as the Default Resolver

A tempting approach is:

> retrieve rules -> ask LLM to write Python -> execute generated code

Do **not** make this the normal architecture.

Problems include:

- inconsistent implementations of the same rule;
- subtle arithmetic bugs;
- difficult auditing;
- security risk;
- difficult reproducibility;
- poor testability;
- generated code gaining accidental access to application state.

Prefer a constrained **Resolution DSL / Resolution Specification** executed by a known runtime.

If future rules genuinely cannot be represented by the generic runtime, a highly sandboxed computation fallback may be considered later.

That is a future extension, not the MVP.

---

## 15. Probability vs Actual Roll

The same Resolution Specification should ideally support two execution styles.

### Preview/probability

Example:

```text
Effective target: 11 on 1d20
Success probability: 55%
```

No state change.

### Execute

Example:

```text
Roll: 14
Result: failure
```

This produces an authoritative outcome and may mutate state.

This separation is useful for:

- debugging;
- testing;
- GM interfaces;
- simulation analysis;
- future agent planning.

Be careful not to automatically expose omniscient probability information to a CharacterAgent unless the calling workflow intends to do so.

---

## 16. State Mutations

Mechanical outcomes must become explicit structured mutations.

Example:

```yaml
mutations:
  - type: resource_change
    entity: cwenhild
    field: hit_points
    delta: -4

  - type: status_add
    entity: cwenhild
    status: prone

  - type: equipment_state
    entity: lance_123
    state: dropped
```

State changes should be validated before commit.

The mutation stage, not narrative text, is the source of truth.

If a narrative says Cwenhild loses her sword but no state mutation exists, the world should **not** silently treat the sword as lost.

Likewise, if the deterministic resolution says she suffered 4 damage, narration must not decide she escaped unharmed.

---

## 17. Narrative Outcome

Narration occurs **after** mechanical resolution.

Input:

- attempted action;
- Scene context;
- deterministic outcome;
- applied mutations;
- CharacterAgent identity/perspective information where appropriate.

Output:

- concise narrative account of what happened.

The narrator is allowed to choose presentation, sensory detail, and style.

It is **not** allowed to override mechanical truth.

Example mechanical truth:

```text
failure
4 damage
character falls prone
```

Valid narration:

> Cwenhild launches herself toward the second horse, but her boot slips on the rain-slick saddle. She crashes hard into the mud, the impact knocking the breath from her.

Invalid narration:

> At the last moment she catches the reins and swings safely onto the second horse.

The second narration contradicts the resolution and must not be allowed.

---

## 18. World State Beyond Character Sheets

Characters are the first priority, but the architecture should not assume that only characters can have mechanical state.

Eventually ShreckWorld may mechanically embody:

- NPCs;
- monsters;
- objects;
- weapons;
- vehicles;
- buildings;
- cities;
- armies;
- supernatural entities;
- locations;
- factions.

Example:

```text
Castle Gate
    structure: 18
    armour: 6
    burning: false

Sword
    damage: ...
    broken: false

Army
    size: ...
    morale: ...
```

Do not implement all of these in the first milestone.

Design the core embodiment/state interfaces so that the future extension is possible.

---

## 19. Relationship Between Character Identity and Mechanical Ability

Do not collapse personality traits and RPG skills into the same data.

Shrecknet may know:

> Cwenhild is an experienced rider.

Pendragon may represent that evidence as:

```text
Horsemanship = 16
```

A different system may represent the same evidence as:

```text
Dexterity 3
Ride 4
```

Therefore:

> Narrative identity belongs to CharacterAgent.  
> Mechanical interpretation belongs to the ShreckWorld embodiment.

The same CharacterAgent could theoretically be embodied in different rule systems without rewriting who the character fundamentally is.

---

## 20. Missing Mechanical Values

A resolution must never create ephemeral character-sheet values just to finish an action.

If the required sheet says:

```text
Horsemanship: missing
```

and the attempted action requires Horsemanship, ShreckWorld should return something similar to:

```text
resolution_status: blocked_missing_state
required_field: horsemanship
```

The caller can then:

- manually provide the value;
- run an evidence-derived embodiment update;
- import/synchronize the missing value.

Once accepted, the value is persisted.

Then resolution can be retried.

This preserves mechanical continuity.

---

## 21. Suggested Internal Component Boundaries

Names are provisional.

### `WorldKnowledgeService`

Owns:

- World source scope;
- local retrieval;
- optional external retrieval;
- evidence merging;
- source authority;
- provenance.

Built primarily from existing Librarian retrieval infrastructure.

### `WorldSheetDefinitionService`

Owns:

- mechanical sheet definitions;
- field schemas;
- validation;
- definition versions.

### `WorldEmbodimentService`

Owns:

- sheet instances;
- manual/imported/inferred values;
- provenance;
- revisions/history;
- updates.

### `WorldRuleCompiler`

Owns:

- converting retrieved rule evidence + situation + current mechanical state into a constrained Resolution Specification.

This is LLM-assisted.

### `WorldResolutionValidator`

Owns:

- checking the generated Resolution Specification before execution.

No LLM authority here.

### `WorldResolutionEngine`

Owns:

- dice;
- arithmetic;
- comparisons;
- tables;
- opposed checks;
- probability;
- deterministic mechanical outcome.

This must be normal application code.

### `WorldMutationService`

Owns:

- validating and applying mechanical state changes;
- creating revisions/audit history;
- ensuring atomicity where required.

### `WorldNarrationService`

Owns:

- converting authoritative mechanical results into narrative output without changing the outcome.

---

## 22. Source of Truth Rules

The implementation should preserve clear ownership.

### Narrative canonical truth

Owned by existing Shrecknet graph concepts such as:

- Scene;
- Milestone;
- EntityInstance;
- graph relationships.

### Character subjective truth

Owned by:

- CharacterAgent;
- ScenePerspective;
- beliefs;
- emotional interpretation;
- identity impacts.

### Mechanical truth

Owned by ShreckWorld:

- sheet definitions;
- sheet instances;
- resources;
- mechanical statuses;
- mechanical state revisions;
- resolution records.

### Rule truth

Owned by configured World sources plus explicit source-precedence policy.

### Random outcome

Owned by the deterministic resolution engine and recorded resolution event.

### Narrative rendering

Presentation only. Never source of mechanical truth.

---

## 23. Resolution Records and Reproducibility

Every executed resolution should produce a persistent audit record sufficient to understand what happened.

Suggested contents:

```text
resolution_id
world_id / scope
scene_id
actor
targets
attempt
timestamp

embodiment revision(s) used
rule sources used
compiled Resolution Specification
random seed and/or explicit dice results
mechanical result
state mutations
mutation commit result
narrative result
model/job metadata for interpretive stages
```

For randomness, record enough information to reproduce/audit the result.

Do not merely store:

```text
"Cwenhild failed."
```

The purpose is to make simulations explainable.

---

## 24. Librarian Migration Strategy

This should be a controlled migration.

### Keep

Preserve and reuse:

- Library Item storage;
- PDF source management;
- Docling ingestion;
- PdfChunks;
- embeddings;
- vector index;
- full-text index;
- exact-term retrieval;
- RRF;
- reranker;
- parent expansion;
- evidence validation;
- citation/provenance structures;
- embedding packages;
- existing tests around retrieval correctness.

### Refactor

Move/generalize Librarian-specific retrieval concepts into World knowledge concepts.

For example, implementation may evolve from:

```text
app/jobs/librarian/query_v2.py
```

toward something conceptually like:

```text
app/jobs/shreckworld/query.py
app/services/shreckworld/knowledge_service.py
```

Exact structure should match project conventions.

Avoid large file moves before behaviour is covered by tests.

### Remove eventually

Once equivalent ShreckWorld functionality is stable:

- Librarian agent configuration;
- Librarian-specific UI;
- Librarian-agent CRUD where no longer required;
- `POST /jobs/librarian/{agent_id}/query`;
- documentation describing Librarian as a standalone agent.

### Replace with

Conceptually:

```text
POST /worlds/{world_id}/query
```

or the equivalent route consistent with existing Shrecknet routing.

The endpoint name is not fixed by this document.

What matters is that **World scope replaces Librarian-agent scope**.

Backward compatibility may temporarily proxy old Librarian query routes into ShreckWorld during migration if useful, but the final architecture should not keep Librarian as a second permanent concept.

---

## 25. Do Not Couple ShreckWorld to One RPG

Pendragon is a good development/test case, but the architecture must not encode Pendragon assumptions into core services.

Avoid core models such as:

```text
horsemanship
glory
passion
d20
```

Instead, sheet definitions and Resolution Specifications should be generic.

Pendragon-specific fields live in the Pendragon ShreckWorld configuration/embodiment.

Likewise Vampire, Call of Cthulhu, D&D, or non-RPG simulation worlds should use the same ShreckWorld infrastructure.

---

## 26. Recommended First Vertical Slice

Do not try to implement the whole project at once.

Use one complete Pendragon example to validate the architecture.

Suggested vertical slice:

### World

A Pendragon ShreckWorld with:

- one or two uploaded rulebooks;
- web search disabled;
- explicit source precedence.

### Character

One existing CharacterAgent, e.g. Cwenhild.

### Sheet

A small but persistent test sheet containing enough fields for the chosen scenario.

Example:

```text
DEX
SIZ
Horsemanship
damage / HP fields required by the relevant rule
```

### Scenario

Resolve:

> Cwenhild attempts a difficult riding manoeuvre.

The end-to-end success criterion is:

```text
1. World selects correct sources.
2. Character has a persistent stored sheet.
3. Rule retrieval finds the relevant rule.
4. Compiler generates a valid Resolution Specification.
5. Resolver rolls/calculates deterministically.
6. Outcome is recorded.
7. Any mechanical mutations are persisted.
8. Narration describes, but does not change, the outcome.
9. A second attempt uses the already stored sheet values.
```

If this works cleanly, generalization becomes much safer.

---

## 27. Implementation Phases

### Phase 1 — World Knowledge / Librarian Replacement

Goal:

> Make ShreckWorld the owner of document/world queries.

Implement:

- ShreckWorld configuration attached to the correct existing world scope;
- Library Item selection per world;
- source-authority configuration;
- web-search capability flag/policy model, even if execution comes later;
- `shreckworld.query`;
- reuse current Librarian Query v2 retrieval;
- grounded citations/provenance;
- tests proving World source scoping;
- begin deprecating Librarian-agent query path.

At the end of Phase 1, ShreckWorld should already fully replace Librarian for general source questions.

---

### Phase 2 — Persistent Character Sheets

Goal:

> Give a CharacterAgent a stable mechanical embodiment in one ShreckWorld.

Implement:

- sheet definition model;
- sheet instance/World embodiment model;
- provenance;
- manual values;
- evidence-derived creation;
- review/accept lifecycle if needed;
- revision/update history;
- read/edit APIs;
- missing-value behaviour.

Foundry/PDF imports can wait unless easy to integrate cleanly.

---

### Phase 3 — Resolution Specification + Deterministic Engine

Goal:

> Resolve one mechanical action without letting the LLM choose the outcome.

Implement:

- attempted-action request schema;
- relevant-rule retrieval;
- constrained Resolution Specification;
- validator;
- generic dice/math primitives;
- deterministic execution;
- probability preview;
- resolution audit records.

Keep the supported mechanical DSL deliberately small at first.

---

### Phase 4 — State Mutation

Goal:

> Make mechanical outcomes persist.

Implement:

- structured mutation schema;
- validation;
- atomic mutation application;
- embodiment revisions;
- statuses/resources;
- resolution-to-mutation provenance;
- failure/rollback behaviour.

---

### Phase 5 — Narrative Result

Goal:

> Produce a natural-language account of authoritative results.

Implement:

- narrative outcome prompt/job;
- strict inclusion of mechanical truth;
- CharacterAgent context where appropriate;
- no ability for narration to rewrite resolution.

---

### Phase 6 — Scene Resolution

Goal:

> Coordinate multiple actions and entities inside a Scene.

Only begin after single-action resolution is reliable.

Implement incrementally:

- chained actions;
- opposed actions;
- multiple entities;
- action ordering;
- scene-level state transition;
- canonical Scene/Milestone integration.

---

### Phase 7 — External Integrations

Examples:

- Foundry character-sheet import/sync;
- character-sheet PDF extraction;
- web search provider/agent;
- rules errata connectors;
- other RPG/system importers.

These should plug into existing ShreckWorld contracts rather than bypassing them.

---

## 28. Testing Requirements

ShreckWorld requires stronger tests than normal conversational agents because it modifies authoritative state.

At minimum test:

### Knowledge

- World A cannot retrieve unselected World B sources.
- Shared Library Items can be used by multiple Worlds without duplicated embeddings.
- source precedence is respected;
- citations resolve to trusted provenance;
- Librarian retrieval behaviour remains equivalent after refactoring.

### Embodiment

- manual values persist;
- imported values persist;
- inferred values contain provenance;
- resolution reads existing values instead of re-inferring;
- updates create history;
- missing values block resolution rather than becoming ephemeral guesses.

### Resolution

- same Resolution Specification + same random seed gives same result;
- dice parsing is correct;
- modifiers are correct;
- opposed checks are correct;
- invalid specs are rejected;
- unsupported primitives fail safely;
- LLM text cannot directly set a successful outcome.

### Mutation

- mutations validate before commit;
- failed mutation sets do not partially corrupt state;
- resolution history points to applied revisions;
- deterministic changes do not require LLM reinterpretation.

### Narration

- output cannot contradict outcome in test fixtures;
- mechanical values are not invented by narrator.

---

## 29. Security and Safety Boundaries

Because ShreckWorld may eventually compile rules dynamically, keep strict boundaries.

Do not allow the LLM to:

- execute arbitrary Python;
- run shell commands;
- directly mutate Neo4j/SQL;
- invent unknown sheet fields and write them silently;
- choose its own random result;
- access sources outside the configured World without permission;
- treat web content as canonical without source policy;
- bypass deterministic validation.

LLM outputs should always pass through typed schemas and application validation before they affect authoritative state.

---

## 30. Observability

Every multi-stage job should expose enough trace information to debug incorrect outcomes.

Useful stages include:

```text
request
context selection
information needs
retrieved evidence
authority/conflict handling
rule interpretation
compiled resolution spec
validation
random execution
mechanical result
mutation proposal
mutation commit
narrative output
```

The current Librarian debug-artifact pattern may be useful as inspiration.

Do not expose hidden model reasoning. Store structured stage inputs/outputs, evidence, decisions, and validation results instead.

---

## 31. UI Direction

The backend architecture is the first priority, but the eventual ShreckWorld UI will likely need several surfaces.

### World setup

- selected Library Items;
- source priority;
- web search enabled/disabled;
- sheet definitions/integrations.

### Character mechanical sheet

- current values;
- provenance;
- manual editing;
- revision history;
- import/sync actions.

### Resolution inspector

- attempted action;
- rules used;
- current relevant mechanical values;
- modifiers;
- dice/calculation;
- result;
- state changes;
- narrative output.

This should make ShreckWorld understandable rather than presenting it as a black-box LLM adjudicator.

---

## 32. Important Non-Goals for the First Version

Do **not** attempt to:

- pre-encode every rule in every book;
- build a universal RPG ontology;
- automatically formalize the entire world before use;
- support every type of mechanical entity;
- resolve complete combats immediately;
- generate arbitrary Python for each rule;
- replace CharacterAgent identity with a character sheet;
- infer the same sheet values again on every action;
- make web search mandatory;
- rewrite the existing PDF retrieval stack from scratch;
- let narration become authoritative state.

The MVP is successful when one configured ShreckWorld can ground rules, maintain one persistent sheet, and deterministically resolve one action end-to-end.

---

## 33. Conceptual Example

Cwenhild exists in Shrecknet as a CharacterAgent.

Her Shrecknet identity contains her narrative history, traits, aspects, goals, and perspectives.

Her Pendragon ShreckWorld embodiment contains:

```text
Horsemanship: 16
```

The current Scene states:

```text
Cwenhild rides beside another horse.
Both horses are moving quickly.
Heavy rain makes the manoeuvre dangerous.
```

The CharacterAgent decides:

```text
I leap onto the other horse.
```

ShreckWorld receives the attempt.

ShreckWorld retrieves relevant Pendragon riding/difficulty/falling rules from the configured books.

The rule compiler creates something conceptually like:

```text
Check: Horsemanship
Base: 16
Moving-horse modifier: -3
Rain modifier: -2
Effective target: 11
Roll: 1d20
```

The deterministic engine rolls:

```text
14
```

Mechanical result:

```text
FAILURE
```

The appropriate follow-up falling rule is resolved.

Result:

```text
4 damage
prone
```

Those state changes are applied.

Only then does narration produce something such as:

> Cwenhild pushes herself from the saddle, but the rain has made the leather treacherous. Her boot slips as she crosses the gap and she crashes hard into the churned earth between the horses.

Future actions use the updated mechanical state.

At no point did the CharacterAgent decide its own success.

At no point was Horsemanship regenerated.

At no point did the narrator change reality.

This is the behaviour ShreckWorld is being built to guarantee.

---

## 34. Final Architecture Summary

```text
                        SHRECKNET
                            |
        +-------------------+-------------------+
        |                                       |
 CHARACTER / NARRATIVE                        SHRECKWORLD
        |                                       |
 CharacterAgent                         World Configuration
 Traits / Aspects / Goals               Source Selection
 ScenePerspectives                      Source Authority
 Scenes / Milestones                    Web Policy
        |                                       |
        |                               World Knowledge Engine
        |                              (former Librarian tech)
        |                                       |
        +-------------> INTENTION               |
                         |                      |
                         v                      v
                  ATTEMPTED ACTION ----> RULE RETRIEVAL
                                                |
                                                v
                                   PERSISTENT EMBODIMENT
                                      / CHARACTER SHEET
                                                |
                                                v
                                     RULE COMPILATION
                                                |
                                                v
                                 RESOLUTION SPECIFICATION
                                                |
                                                v
                                  DETERMINISTIC EXECUTOR
                                                |
                                                v
                                      STATE MUTATIONS
                                                |
                              +-----------------+----------------+
                              |                                  |
                              v                                  v
                    UPDATED WORLD STATE                   NARRATIVE RESULT
```

---

## 35. Core Design Rules for the Coding Agent

When implementation decisions are ambiguous, preserve these rules:

1. **CharacterAgent owns intention and identity.**
2. **ShreckWorld owns mechanical reality and resolution.**
3. **Narrative state and mechanical state are connected but not the same thing.**
4. **World rules may be interpreted dynamically from authoritative sources.**
5. **Character/entity mechanical state is persistent.**
6. **Never re-infer a known sheet value just because a new action needs it.**
7. **LLMs interpret rules; deterministic code executes them.**
8. **Narration happens after resolution and cannot override it.**
9. **Every important mechanical value and outcome should have provenance.**
10. **Reuse the current Librarian retrieval technology; remove Librarian as a standalone agent concept.**
11. **A World scopes its own sources and optional web capability.**
12. **Do not pre-model the entire ruleset. Formalize only what is needed to execute the current resolution, while preserving reusable/persistent state.**
13. **Do not hard-code Pendragon concepts into the ShreckWorld core.**
14. **Prefer typed, auditable intermediate structures over free-form LLM decisions.**
15. **Build atomic action resolution before attempting full scene simulation.**

---

## 36. Repository Context to Read Before Coding

The coding agent should inspect at least these current areas before making changes:

```text
README.md
Documentation/Architecture/SHRECKNET_ARCHITECTURE.md

Documentation/Agents/Librarian/
shrecknet/app/jobs/librarian/
shrecknet/app/api/routers/librarian.py
shrecknet/app/services/pdf_embedding_service.py
shrecknet/app/services/librarian_embedding_package_service.py
shrecknet/app/tasks/librarian_embedding_package.py

Documentation/Agents/CharacterAgent/CharacterAgent.md
shrecknet/app/services/character_agent_service.py
shrecknet/app/services/character_embodiment_service.py
shrecknet/app/jobs/character_agent/
shrecknet/app/schemas/character_agent.py

Existing Scene / Milestone / ontology / instance models and APIs
Existing BackgroundJob / Celery job conventions
Existing ShreckLLM client/configuration conventions
```

Before creating new tables, graph labels, job infrastructure, or duplicated retrieval code, confirm that the required capability is not already represented in one of these systems.

---

## 37. First Coding Task

The recommended first implementation task is **Phase 1 only**:

> Create the ShreckWorld world-knowledge layer and replace Librarian-agent query ownership with World ownership, while preserving the existing Librarian Query v2 retrieval behaviour.

The first PR should therefore focus on:

- deciding the correct existing World/ontology scope owner;
- adding ShreckWorld configuration;
- associating selected Library Items with that configuration;
- adding source-precedence/web-policy configuration;
- exposing a World-scoped general query;
- adapting/reusing Librarian Query v2 behind the new interface;
- preserving citations/provenance/debugging;
- adding tests for source scoping;
- documenting the new architecture;
- marking the old Librarian query path as deprecated or preparing its removal.

Do **not** mix character sheets, rule compilation, deterministic dice resolution, scene mutation, Foundry integration, and Librarian migration into the same first implementation change.

Establish ShreckWorld as the new knowledge owner first.

Then build persistent embodiments on top of it.

---

# Project Statement

**ShreckWorld is the authoritative world-simulation layer of Shrecknet.**

It turns source material into usable world knowledge, gives entities persistent mechanical embodiments, dynamically interprets only the rules needed for the current situation, deterministically resolves attempted actions, applies their consequences to persistent state, and narrates the result without allowing an LLM to invent success.

Its purpose is not to simulate every possible rule in advance.

Its purpose is to guarantee that, inside a configured world:

> **an agent may decide what it wants to attempt, but the world decides what actually happens.**
