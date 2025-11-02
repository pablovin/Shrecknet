"""Background task for Architect step 2: entity generation from validated proposals."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from app.celery_app import celery_app
from app.core.config import get_settings
from app.db.session import AsyncSessionMaker
from app.graph.neo4j import get_driver
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.architect.entity_generator import EntityGenerator
from app.models.architect import ArchitectProposalStatus, ArchitectProposalType
from app.models.background_job import AuthorType, JobType
from app.repositories.architect_repository import ArchitectRepository
from app.repositories.ontology_repository import OntologyRepository
from app.schemas.ontology_instance import (
    OntologyInstanceEntityCreate,
    OntologyInstancePropertyValue,
    OntologyInstanceRelationshipCreate,
    OntologyInstanceUpdate,
)
from app.services.ontology_instance_service import OntologyInstanceService
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(name="architect.generate_entities")
def generate_entities(
    run_id: str,
    validated_proposals: list[dict[str, Any]],
    *,
    author_type: str = "user",
    author_id: str = "system",
) -> dict[str, Any]:
    """Background job for generating/updating entities from validated proposals."""

    description = f"Architect entity generation for run {run_id}"
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.ARCHITECT_GENERATION,
            description=description,
            celery_task_id=generate_entities.request.id,
            details={
                "run_id": run_id,
                "proposal_count": len(validated_proposals),
            },
        )
    )

    try:
        run_async(mark_job_running(job_id))
        run_async(_attach_generation_job_to_run(run_id, job_id))
        run_async(update_job_progress(job_id, 0.05, {"status": "Processing validated proposals"}))

        result = run_async(
            _execute_entity_generation(
                run_id=run_id,
                validated_proposals=validated_proposals,
                job_id=job_id,
                author_type=author_type,
                author_id=author_id,
            )
        )

        run_async(
            mark_job_done(
                job_id,
                {
                    "run_id": run_id,
                    "created_entities": len(result.get("created_entity_ids", [])),
                    "updated_entities": len(result.get("updated_entity_ids", [])),
                    "status": "completed",
                },
            )
        )
        return {"job_id": job_id, "status": "success", "run_id": run_id, **result}

    except Exception as exc:
        logger.error(
            "Entity generation failed for run %s: %s", run_id, exc, exc_info=True
        )
        run_async(mark_job_failed(job_id, str(exc)))
        raise


async def _attach_generation_job_to_run(run_id: str, job_id: int) -> None:
    """Attach the generation job to the architect run."""
    async with AsyncSessionMaker() as session:
        repo = ArchitectRepository(session)
        await repo.attach_generation_job(run_id, job_id)
        await session.commit()


async def _execute_entity_generation(
    *,
    run_id: str,
    validated_proposals: list[dict[str, Any]],
    job_id: int,
    author_type: str,
    author_id: str,
) -> dict[str, Any]:
    """Execute the entity generation workflow."""
    async with AsyncSessionMaker() as session:
        repo = ArchitectRepository(session)
        run = await repo.get_run(run_id, with_proposals=False)
        if not run:
            raise ValueError("Architect run not found")

        # Update proposals with validation data
        await update_job_progress(job_id, 0.10, {"status": "Updating proposal statuses"})
        for validated in validated_proposals:
            # Convert corrected_proposal_type from string to enum if provided
            corrected_proposal_type = None
            if validated.get("corrected_proposal_type") is not None:
                corrected_proposal_type = ArchitectProposalType(validated["corrected_proposal_type"])
            
            await repo.update_proposal_validation(
                proposal_id=validated["proposal_id"],
                status=ArchitectProposalStatus(validated["status"]),
                corrected_alias=validated.get("corrected_alias"),
                corrected_entity_definition_id=validated.get("corrected_entity_definition_id"),
                corrected_proposal_type=corrected_proposal_type,
                corrected_entity_instance_id=validated.get("corrected_entity_instance_id"),
                merged_into_proposal_id=validated.get("merged_into_proposal_id"),
            )
        await session.commit()

        # Get all proposals for this run
        all_proposals = await repo.get_proposals_by_run(run_id)
        proposals_dict = {p.id: p for p in all_proposals}

        # Filter to approved/merged proposals
        await update_job_progress(job_id, 0.15, {"status": "Filtering approved proposals"})
        approved_proposals = []
        for p in all_proposals:
            if p.status == ArchitectProposalStatus.APPROVED:
                approved_proposals.append(p)
            elif p.status == ArchitectProposalStatus.MERGED:
                # For merged proposals, only process the main one
                if not p.merged_into_proposal_id:
                    approved_proposals.append(p)

        if not approved_proposals:
            logger.info("No approved proposals to process for run %s", run_id)
            return {"created_entity_ids": [], "updated_entity_ids": []}

        # Load ontology data
        await update_job_progress(job_id, 0.20, {"status": "Loading ontology data"})
        ontology_id = run.ontology_id
        if not ontology_id:
            raise ValueError("Run does not have an associated ontology")

        onto_repo = OntologyRepository(session)
        entity_defs = await onto_repo.list_entities(ontology_id)
        
        # Build entity definitions map with properties and relationships
        entity_definitions_map: dict[int, dict[str, Any]] = {}
        for entity_def in entity_defs:
            properties = [
                {
                    "id": prop.id,
                    "name": prop.name,
                    "description": prop.description,
                    "data_type": prop.data_type.value,
                    "cardinality": prop.cardinality.value,
                }
                for prop in entity_def.properties
                if prop.auto_generatable
            ]
            relationships = [
                {
                    "id": rel.id,
                    "name": rel.name,
                    "description": rel.description,
                    "destiny_entity_id": rel.destiny_entity_id,
                    "destiny_entity_name": rel.destiny_entity.name if rel.destiny_entity else "any",
                }
                for rel in entity_def.relationships
                if rel.auto_generatable
            ]
            entity_definitions_map[entity_def.id] = {
                "id": entity_def.id,
                "name": entity_def.name,
                "description": entity_def.description,
                "properties": properties,
                "relationships": relationships,
            }

        # Get original text from ontology instance
        await update_job_progress(job_id, 0.25, {"status": "Loading instance text"})
        driver = get_driver()
        async with driver.session(database=settings.neo4j_database) as graph_session:
            instance_service = OntologyInstanceService(session, graph_session)
            ontology_instance = await instance_service.get_instance(run.ontology_instance_id)
            
            # Combine all entity texts for context
            original_text_parts = []
            for entity in ontology_instance.entities:
                if entity.text:
                    original_text_parts.append(entity.text)
                if entity.autogenerated_text:
                    original_text_parts.append(entity.autogenerated_text)
            original_text = "\n\n".join(original_text_parts)

            # Separate new and update proposals
            new_proposals = []
            update_proposals = []
            for p in approved_proposals:
                # Use corrected_proposal_type if provided, otherwise use original
                effective_proposal_type = p.corrected_proposal_type if p.corrected_proposal_type is not None else p.proposal_type
                
                # Use corrected_entity_instance_id if explicitly set (even if None), otherwise use original
                # This handles the case where user converts UPDATE_INSTANCE to NEW_INSTANCE
                if p.corrected_entity_instance_id is not None or p.corrected_proposal_type == ArchitectProposalType.NEW_INSTANCE:
                    effective_entity_instance_id = p.corrected_entity_instance_id
                else:
                    effective_entity_instance_id = p.entity_instance_id
                
                proposal_dict = {
                    "id": p.id,
                    "proposal_type": effective_proposal_type.value,
                    "entity_definition_id": p.entity_definition_id,
                    "entity_instance_id": effective_entity_instance_id,
                    "alias": p.alias,
                    "chunks": p.chunks or [],
                    "corrected_alias": p.corrected_alias,
                    "corrected_entity_definition_id": p.corrected_entity_definition_id,
                }
                if effective_proposal_type == ArchitectProposalType.NEW_INSTANCE:
                    new_proposals.append(proposal_dict)
                else:
                    update_proposals.append(proposal_dict)

            # Initialize LLM client and generator
            model_policy = ModelPolicy(
                decompose_model=settings.model_decompose,
                subanswer_model=settings.model_subanswer,
                synthesis_model=settings.model_synthesis,
                validation_model=settings.model_validation,
                style_model=settings.model_style,
                architect_extract_model=settings.model_architect_extract,
            )
            llm_client = OpenAIClient(
                api_key=settings.openai_api_key,
                timeout=60,
                max_retries=3,
            )
            generator = EntityGenerator(llm_client, model_policy)

            # Phase 1: Generate new entities (without relationships)
            created_entity_ids = []
            if new_proposals:
                await update_job_progress(
                    job_id, 0.35, {"status": f"Generating {len(new_proposals)} new entities"}
                )
                new_entities = await generator.generate_new_entities(
                    proposals=new_proposals,
                    entity_definitions_map=entity_definitions_map,
                    original_text=original_text,
                    author_type=author_type,
                    author_id=author_id,
                )

                # Store relationship data for phase 2
                relationship_data: dict[str, list[dict[str, Any]]] = {}
                for i, proposal in enumerate(new_proposals):
                    entity_def_id = (
                        proposal.get("corrected_entity_definition_id")
                        or proposal["entity_definition_id"]
                    )
                    entity_def = entity_definitions_map.get(entity_def_id)
                    if not entity_def:
                        continue

                    # Extract relationships from the proposal (would come from generator)
                    # For now, we'll extract them separately
                    alias = proposal.get("corrected_alias") or proposal["alias"]
                    alias_key = alias.lower().strip()
                    relationship_data[alias_key] = []

                # Phase 1.5: Add entities to instance
                await update_job_progress(
                    job_id, 0.50, {"status": f"Creating {len(new_entities)} entities in graph"}
                )
                
                # Create entities one by one and track their IDs
                from app.schemas.ontology_instance import OntologyInstanceEntityCreate
                for i, entity_create in enumerate(new_entities):
                    # Create a temporary instance with just this entity to get the ID
                    # We'll use update_instance to add entities one at a time
                    # Actually, we need to use the ontology_instance_service's internal methods
                    # Let's add entities to the existing instance
                    
                    # For now, collect all entities and add them in one update
                    pass

                # Batch create all new entities by updating the instance
                current_entities = list(ontology_instance.entities)
                
                # Convert existing entities to create format
                existing_entity_creates = []
                for e in current_entities:
                    existing_entity_creates.append(
                        OntologyInstanceEntityCreate(
                            definition_id=e.definition_id,
                            alias=e.alias or "unknown",
                            text=e.text or "",
                            autogenerated_text=e.autogenerated_text,
                            author_type=e.author_type,
                            author_id=e.author_id,
                            properties=[
                                OntologyInstancePropertyValue(
                                    definition_id=p.definition_id, value=p.value
                                )
                                for p in e.properties
                            ],
                            relationships=[
                                OntologyInstanceRelationshipCreate(
                                    definition_id=r.definition_id,
                                    target_entity_instance_id=r.target_entity_id,
                                    data=r.data,
                                )
                                for r in e.relationships
                            ],
                        )
                    )

                # Add new entities
                all_entities = existing_entity_creates + new_entities
                update_payload = OntologyInstanceUpdate(entities=all_entities)
                
                # Update the instance
                updated_instance = await instance_service.update_instance(
                    run.ontology_instance_id, update_payload
                )

                # Map new entity aliases to their generated IDs
                entity_alias_map = {}
                new_entity_start_idx = len(existing_entity_creates)
                for i, entity in enumerate(updated_instance.entities[new_entity_start_idx:]):
                    created_entity_ids.append(entity.entity_instance_id)
                    entity_alias_map[entity.alias.lower().strip()] = entity.entity_instance_id
                    
                    # Update proposal with generated entity ID
                    if i < len(new_proposals):
                        await repo.update_proposal_generated_entity(
                            new_proposals[i]["id"], entity.entity_instance_id
                        )

            # Phase 2: Update existing entities
            updated_entity_ids = []
            if update_proposals:
                await update_job_progress(
                    job_id, 0.70, {"status": f"Updating {len(update_proposals)} existing entities"}
                )
                
                # Build map of existing entities
                existing_entities_map = {}
                for entity in ontology_instance.entities:
                    existing_entities_map[entity.entity_instance_id] = {
                        "entity_instance_id": entity.entity_instance_id,
                        "definition_id": entity.definition_id,
                        "alias": entity.alias,
                        "text": entity.text,
                        "autogenerated_text": entity.autogenerated_text,
                        "properties": [
                            {"definition_id": p.definition_id, "value": p.value}
                            for p in entity.properties
                        ],
                        "relationships": [
                            {
                                "definition_id": r.definition_id,
                                "target_alias": None,  # We don't have this readily available
                                "target_entity_instance_id": r.target_entity_id,
                            }
                            for r in entity.relationships
                        ],
                    }

                updates = await generator.update_existing_entities(
                    proposals=update_proposals,
                    entity_definitions_map=entity_definitions_map,
                    existing_entities_map=existing_entities_map,
                    original_text=original_text,
                    author_type=author_type,
                    author_id=author_id,
                )

                # Apply updates to graph
                await update_job_progress(
                    job_id, 0.85, {"status": f"Applying updates to graph"}
                )
                for update_data in updates:
                    entity_id = update_data["entity_instance_id"]
                    updated_entity_ids.append(entity_id)
                    
                    # Update autogenerated_text if provided
                    if update_data.get("updated_autogenerated_summary"):
                        await graph_session.run(
                            """
                            MATCH (e:EntityInstance {entity_instance_id: $entity_id})
                            SET e.autogenerated_text = $text,
                                e.autogenerated_text_linked = $text,
                                e.last_updated_date = datetime(),
                                e.author_type = $author_type,
                                e.author_id = $author_id
                            """,
                            entity_id=entity_id,
                            text=update_data["updated_autogenerated_summary"],
                            author_type=author_type,
                            author_id=author_id,
                        )

                    # Add new properties
                    for prop in update_data.get("new_properties", []):
                        # Properties are stored in JSON, need to read, update, write
                        result = await graph_session.run(
                            """
                            MATCH (e:EntityInstance {entity_instance_id: $entity_id})
                            RETURN e.properties as props
                            """,
                            entity_id=entity_id,
                        )
                        record = await result.single()
                        if record:
                            import json
                            props = json.loads(record["props"] or "{}")
                            props[str(prop.definition_id)] = prop.value
                            await graph_session.run(
                                """
                                MATCH (e:EntityInstance {entity_instance_id: $entity_id})
                                SET e.properties = $props
                                """,
                                entity_id=entity_id,
                                props=json.dumps(props),
                            )

                    # Add new relationships
                    for rel in update_data.get("new_relationships", []):
                        target_id = rel.target_entity_instance_id
                        if not target_id and rel.target_alias:
                            # Look up target by alias
                            target_result = await graph_session.run(
                                """
                                MATCH (e:EntityInstance {instance_id: $instance_id})
                                WHERE toLower(e.alias) = toLower($alias)
                                RETURN e.entity_instance_id as eid
                                LIMIT 1
                                """,
                                instance_id=run.ontology_instance_id,
                                alias=rel.target_alias,
                            )
                            target_rec = await target_result.single()
                            if target_rec:
                                target_id = target_rec["eid"]

                        if target_id:
                            rel_id = str(uuid4())
                            import json
                            rel_data = json.dumps({"justification": rel.justification or ""})
                            
                            # Get relationship definition for destiny_entity_id
                            entity_def = existing_entities_map[entity_id]
                            rel_defs = entity_definitions_map[entity_def["definition_id"]]["relationships"]
                            destiny_entity_id = None
                            for rd in rel_defs:
                                if rd["id"] == rel.definition_id:
                                    destiny_entity_id = rd.get("destiny_entity_id")
                                    break

                            await graph_session.run(
                                """
                                MATCH (source:EntityInstance {entity_instance_id: $source_id})
                                MATCH (target:EntityInstance {entity_instance_id: $target_id})
                                CREATE (source)-[:RELATES_TO {
                                    relationship_instance_id: $rel_id,
                                    relationship_definition_id: $rel_def_id,
                                    destiny_entity_definition_id: $destiny_id,
                                    data: $data,
                                    created_at: datetime(),
                                    updated_at: datetime()
                                }]->(target)
                                """,
                                source_id=entity_id,
                                target_id=target_id,
                                rel_id=rel_id,
                                rel_def_id=rel.definition_id,
                                destiny_id=destiny_entity_id,
                                data=rel_data,
                            )

        await session.commit()
        await update_job_progress(job_id, 0.95, {"status": "Entity generation completed"})

        return {
            "created_entity_ids": created_entity_ids,
            "updated_entity_ids": updated_entity_ids,
        }
