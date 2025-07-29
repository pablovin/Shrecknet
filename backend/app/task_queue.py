import os
import asyncio
from celery import Celery
import multiprocessing
multiprocessing.set_start_method("spawn", force=True)


celery_app = Celery(
    'shrecknet',
    broker=os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
    backend=os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0'),
)

# celery_app = Celery(
#     'shrecknet',
#     broker=os.getenv('CELERY_BROKER_URL', 'memory://'),
#     backend=os.getenv('CELERY_RESULT_BACKEND', 'cache+memory://'),
# )


celery_app.conf.task_serializer = 'json'
celery_app.conf.result_serializer = 'json'
celery_app.conf.accept_content = ['json']
celery_app.conf.broker_connection_retry_on_startup = True

from app.crud.crud_page_links_update import (
    auto_crosslink_page_content,
    auto_crosslink_batch,
    remove_crosslinks_to_page,
    remove_page_refs_from_characteristics,
    sync_page_ref_attributes,
    auto_crosslink_note_content,
)

# Ensure all models are imported so SQLModel can resolve relationships
# Import all models so SQLModel is aware of every table when running in the
# Celery worker context. Without these imports SQLModel may not register the
# tables referenced by relationships, leading to NoReferencedTableError during
# flush/commit.
import app.models.model_concept  # noqa: F401
import app.models.model_gameworld  # noqa: F401
import app.models.model_user  # noqa: F401
import app.models.model_page  # noqa: F401
import app.models.model_characteristic  # noqa: F401
import app.models.model_specialist_source  # noqa: F401

@celery_app.task
def task_auto_crosslink_page_content(page_id: int):
    asyncio.run(auto_crosslink_page_content(page_id))

@celery_app.task
def task_auto_crosslink_batch(page_id: int):
    asyncio.run(auto_crosslink_batch(page_id))

@celery_app.task
def task_remove_crosslinks_to_page(page_id: int):
    asyncio.run(remove_crosslinks_to_page(page_id))

@celery_app.task
def task_remove_page_refs_from_characteristics(page_id: int):
    asyncio.run(remove_page_refs_from_characteristics(page_id))

@celery_app.task
def task_sync_page_ref_attributes(page_id: int):
    asyncio.run(sync_page_ref_attributes(page_id))

@celery_app.task
def task_auto_crosslink_note_content(note_id: int):
    asyncio.run(auto_crosslink_note_content(note_id))

from app.crud.crud_page_analysis import analyze_pages_bulk
from app.crud.crud_agent import get_agent
from app.crud.crud_page import get_page, update_page, get_pages
from app.database import async_session_maker
from app.config import settings
from app.crud import crud_vectordb, crud_specialist_vectordb, crud_novel
from app.crud import crud_library_vectordb
from datetime import datetime, timezone
import json
from pathlib import Path
from app.crud.crud_page_analysis import analyze_page, generate_pages


@celery_app.task
def task_analyze_pages_job(agent_id: int, page_ids: list[int], job_id: str):
    async def run():
        job_dir = Path(settings.writer_job_dir) / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        job_path = job_dir / "job.json"
        analysis_path = job_dir / "analysis.json"
        start_time = datetime.now(timezone.utc).isoformat()

        with open(job_path, "w") as f:
            json.dump(
                {
                    "status": "processing",
                    "agent_id": agent_id,
                    "job_type": "analyze_pages",
                    "page_ids": page_ids,
                    "pages_total": len(page_ids),
                    "pages_processed": 0,
                    "start_time": start_time,
                    "action_needed": None,
                },
                f,
                default=str,
            )

        async with async_session_maker() as session:
            agent = await get_agent(session, agent_id)
            if not agent:
                with open(job_path, "w") as ff:
                    json.dump({"status": "error", "error": "Agent not found", "start_time": start_time}, ff, default=str)
                return

            pages = []
            page_names = []
            processed = 0
            for pid in page_ids:
                p = await get_page(session, pid)
                if p and p.gameworld_id == agent.world_id:
                    pages.append(p)
                    page_names.append(p.name)
                processed += 1
                with open(job_path, "w") as f:
                    json.dump(
                        {
                            "status": "processing",
                            "agent_id": agent_id,
                            "job_type": "analyze_pages",
                            "page_ids": page_ids,
                            "page_names": page_names,
                            "pages_total": len(page_ids),
                            "pages_processed": processed,
                            "start_time": start_time,
                            "action_needed": None,
                        },
                        f,
                        default=str,
                    )

            if len(pages) == 1:
                result = await analyze_page(session, agent, pages[0])
                suggestions = result.get("suggestions", [])
            else:
                suggestions = await analyze_pages_bulk(session, agent, pages)

        end_time = datetime.now(timezone.utc).isoformat()
        with open(job_path, "w") as f:
            json.dump(
                {
                    "status": "done",
                    "agent_id": agent_id,
                    "job_type": "analyze_pages",
                    "page_ids": page_ids,
                    "page_names": page_names,
                    "pages_total": len(page_ids),
                    "pages_processed": len(page_ids),
                    "suggestions": suggestions,
                    "start_time": start_time,
                    "end_time": end_time,
                    "action_needed": "review",
                },
                f,
                default=str,
            )
        with open(analysis_path, "w") as af:
            json.dump({"suggestions": suggestions}, af, default=str)

    asyncio.run(run())


@celery_app.task
def task_rebuild_vectordb(agent_id: int, job_id: str):
    async def run():
        job_dir = Path(settings.vectordb_job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        job_path = job_dir / f"{job_id}.json"
        start_time = datetime.now(timezone.utc).isoformat()
        with open(job_path, "w") as f:
            json.dump({
                "status": "processing",
                "agent_id": agent_id,
                "job_type": "update_vector_db",
                "start_time": start_time,
            }, f, default=str)

        print (f" --- CALCUALTING SUGGESTIONS --- ")
        print (f" --- CALCUALTING SUGGESTIONS --- ")
        print (f" --- CALCUALTING SUGGESTIONS --- ")
        print (f" --- CALCUALTING SUGGESTIONS --- ")
        async with async_session_maker() as session:
            agent = await get_agent(session, agent_id)
            if not agent:
                with open(job_path, "w") as ff:
                    json.dump({
                        "status": "error",
                        "error": "Agent not found",
                        "start_time": start_time,
                    }, ff, default=str)
                return

            print (f" --- CALCUALTING SUGGESTIONS2 --- ")
            print (f" --- CALCUALTING SUGGESTIONS2 --- ")
            print (f" --- CALCUALTING SUGGESTIONS2 --- ")
            print (f" --- CALCUALTING SUGGESTIONS2 --- ")
            
            try:
                print (f" --- CALCUALTING SUGGESTIONS3 --- ")
                print (f" --- CALCUALTING SUGGESTIONS3 --- ")
                print (f" --- CALCUALTING SUGGESTIONS3 --- ")
                print (f" --- CALCUALTING SUGGESTIONS3 --- ")
                count = await crud_vectordb.rebuild_world(session, agent.world_id)
                print (f" --- CALCUALTING SUGGESTIONS4 --- ")
                print (f" --- CALCUALTING SUGGESTIONS4 --- ")
                print (f" --- CALCUALTING SUGGESTIONS4 --- ")
                print (f" --- CALCUALTING SUGGESTIONS4 --- ")
            except Exception as exc:  # pragma: no cover - defensive
                print (f" --- CALCUALTING SUGGESTIONS5  ERROR {str(exc)} --- ")
                print (f" --- CALCUALTING SUGGESTIONS5  ERROR {str(exc)} --- ")
                print (f" --- CALCUALTING SUGGESTIONS5  ERROR {str(exc)} --- ")
                print (f" --- CALCUALTING SUGGESTIONS5  ERROR {str(exc)} --- ")
            
                with open(job_path, "w") as ff:
                    json.dump(
                        {
                            "status": "error",
                            "error": str(exc),
                            "start_time": start_time,
                        },
                        ff,
                        default=str,
                    )
                raise

        end_time = datetime.now(timezone.utc).isoformat()
        with open(job_path, "w") as f:
            json.dump({
                "status": "done",
                "agent_id": agent_id,
                "job_type": "update_vector_db",
                "pages_indexed": count,
                "start_time": start_time,
                "end_time": end_time,
            }, f, default=str)

    asyncio.run(run())


@celery_app.task
def task_rebuild_specialist_vectors(agent_id: int, job_id: str):
    async def run():
        job_dir = Path(settings.specialist_job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        job_path = job_dir / f"{job_id}.json"
        start_time = datetime.now(timezone.utc).isoformat()
        def write_status(status: str, progress: str | None = None, count: int | None = None, end: str | None = None):
            data = {
                "status": status,
                "agent_id": agent_id,
                "job_type": "rebuild_specialist_vectors",
                "start_time": start_time,
            }
            if progress:
                data["progress"] = progress
            if count is not None:
                data["documents_indexed"] = count
            if end:
                data["end_time"] = end
            with open(job_path, "w") as f:
                json.dump(data, f, default=str)

        write_status("processing")

        async with async_session_maker() as session:
            agent = await get_agent(session, agent_id)
            if not agent:
                write_status("error")
                return
            try:
                def progress_cb(msg: str):
                    write_status("processing", msg)

                count = await crud_specialist_vectordb.rebuild_agent_with_progress(
                    session, agent_id, progress_cb
                )
            except Exception as exc:  # pragma: no cover - defensive
                write_status("error")
                raise

        end_time = datetime.now(timezone.utc).isoformat()
        write_status("done", count=count, end=end_time)

    asyncio.run(run())


@celery_app.task
def task_rebuild_library_vectors(item_id: int, job_id: str):
    async def run():
        job_dir = Path(settings.library_job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        job_path = job_dir / f"{job_id}.json"
        start_time = datetime.now(timezone.utc).isoformat()

        def write_status(status: str, progress: float | None = None, total: int | None = None, processed: int | None = None, end: str | None = None):
            data = {
                "status": status,
                "item_id": item_id,
                "job_type": "rebuild_library_vectors",
                "start_time": start_time,
            }
            if progress is not None:
                data["progress"] = progress
            if total is not None:
                data["chunks_total"] = total
            if processed is not None:
                data["chunks_processed"] = processed
            if end:
                data["end_time"] = end
            with open(job_path, "w") as f:
                json.dump(data, f, default=str)

        write_status("processing", progress=0)

        async with async_session_maker() as session:
            try:
                def cb(idx: int, total: int):
                    pct = int(idx / total * 100)
                    write_status("processing", progress=pct, total=total, processed=idx)

                count = await crud_library_vectordb.rebuild_item_with_progress(session, item_id, cb)
            except Exception:
                write_status("error")
                raise

        end_time = datetime.now(timezone.utc).isoformat()
        write_status("done", progress=100, total=count, processed=count, end=end_time)

    asyncio.run(run())


@celery_app.task
def task_rebuild_world_embedding(embedding_id: int, job_id: str):
    async def run():
        job_dir = Path(settings.world_embedding_job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        job_path = job_dir / f"{job_id}.json"
        start_time = datetime.now(timezone.utc).isoformat()
        with open(job_path, "w") as f:
            json.dump({
                "status": "processing",
                "embedding_id": embedding_id,
                "job_type": "rebuild_world_embedding",
                "start_time": start_time,
            }, f, default=str)

        async with async_session_maker() as session:
            from app.crud import crud_world_embedding
            embedding = await crud_world_embedding.get_embedding(session, embedding_id)
            if not embedding:
                with open(job_path, "w") as ff:
                    json.dump({"status": "error", "error": "Embedding not found", "start_time": start_time}, ff, default=str)
                return
            try:
                t0 = datetime.now(timezone.utc)
                count = await crud_vectordb.rebuild_embedding(session, embedding.world_id, embedding.id)
                t1 = datetime.now(timezone.utc)
                embedding.last_index_time = t1
                embedding.page_count = count
                embedding.build_seconds = (t1 - t0).total_seconds()
                session.add(embedding)
                await session.commit()
                await session.refresh(embedding)
            except Exception as exc:
                with open(job_path, "w") as ff:
                    json.dump({"status": "error", "error": str(exc), "start_time": start_time}, ff, default=str)
                raise

        end_time = datetime.now(timezone.utc).isoformat()
        with open(job_path, "w") as f:
            json.dump({
                "status": "done",
                "embedding_id": embedding_id,
                "job_type": "rebuild_world_embedding",
                "pages_indexed": count,
                "start_time": start_time,
                "end_time": end_time,
            }, f, default=str)

    asyncio.run(run())




@celery_app.task
def task_generate_pages_job(
    agent_id: int,
    page_id: int,
    pages: list[dict],
    job_id: str,
    merge_groups: list[list[str]] | None = None,
    suggestions: list[dict] | None = None,
    bulk_accept_updates: bool = False,
    request_id: str | None = None,
):
    async def run():
        req_dir = Path(settings.writer_job_dir) / (request_id or job_id)
        req_dir.mkdir(parents=True, exist_ok=True)
        job_path = req_dir / "job.json"
        generated_path = req_dir / "generated.json"
        start_time = datetime.now(timezone.utc).isoformat()
        with open(job_path, "w") as f:
            json.dump(
                {
                    "status": "processing",
                    "agent_id": agent_id,
                    "page_id": page_id,
                    "job_type": "generate_pages",
                    "start_time": start_time,
                    "merge_groups": merge_groups or [],
                    "suggestions": suggestions or [],
                    "bulk_accept_updates": bulk_accept_updates,
                    "action_needed": None,
                },
                f,
                default=str,
            )

        async with async_session_maker() as session:
            agent = await get_agent(session, agent_id)
            page = await get_page(session, page_id)
            if not agent or not page or agent.world_id != page.gameworld_id:
                with open(job_path, "w") as ff:
                    json.dump({
                        "status": "error",
                        "error": "Agent or page not found",
                        "start_time": start_time,
                    }, ff, default=str)
                return
            result = await generate_pages(session, agent, page, pages)

            pages_map = {p["name"]: p for p in result}
            final_pages = []
            auto_updated = []

            groups = merge_groups or [ [s["name"]] for s in (suggestions or []) ]
            for names in groups:
                group = [s for s in (suggestions or []) if s["name"] in names]
                if not group:
                    continue
                base = next((s for s in group if s.get("merge_targets")), group[0])
                merged_items = [s for s in group if s is not base]

                merged_contents = [
                    pages_map.get(base["name"], {}).get("autogenerated_content")
                    or base.get("autogenerated_content", "")
                ]
                for item in merged_items:
                    content = (
                        pages_map.get(item["name"], {}).get("autogenerated_content")
                        or item.get("autogenerated_content")
                    )
                    if content:
                        merged_contents.append(content)

                combined_autogen = "\n\n---\n\n".join(
                    [c for c in merged_contents if c]
                )

                if base.get("exists") or base.get("mode") == "update":
                    backend_page = None
                    if base.get("target_page_id"):
                        backend_page = await get_page(session, base["target_page_id"])
                    else:
                        pages_list = await get_pages(
                            session,
                            gameworld_id=page.gameworld_id,
                            concept_id=base["concept_id"],
                        )
                        for p in pages_list:
                            if p.name.lower() == base["name"].lower():
                                backend_page = p
                                break
                    if backend_page:
                        prev = backend_page.autogenerated_content or ""
                        full_autogen = (
                            (prev + "\n\n---\n\n") if prev else ""
                        ) + combined_autogen

                        if bulk_accept_updates:
                            await update_page(
                                session,
                                backend_page.id,
                                {
                                    "autogenerated_content": full_autogen,
                                    "updated_by_agent_id": agent_id,
                                },
                            )
                            auto_updated.append(
                                {"id": backend_page.id, "name": backend_page.name}
                            )
                            continue
                        else:
                            final_pages.append(
                                {
                                    **backend_page.model_dump(),
                                    "autogenerated_content": full_autogen,
                                }
                            )
                else:
                    created = pages_map.get(base["name"])
                    if created:
                        final_pages.append(
                            {**created, "autogenerated_content": combined_autogen}
                        )

            result_pages = final_pages

        end_time = datetime.now(timezone.utc).isoformat()
        with open(job_path, "w") as f:
            json.dump(
                {
                    "status": "done",
                    "agent_id": agent_id,
                    "page_id": page_id,
                    "job_type": "generate_pages",
                    "pages": result_pages,
                    "auto_updated": auto_updated,
                    "start_time": start_time,
                    "end_time": end_time,
                    "merge_groups": merge_groups or [],
                    "suggestions": suggestions or [],
                    "bulk_accept_updates": bulk_accept_updates,
                    "action_needed": "review",
                },
                f,
                default=str,
            )
        with open(generated_path, "w") as gf:
            json.dump({"pages": result_pages, "auto_updated": auto_updated}, gf, default=str)

    asyncio.run(run())

@celery_app.task
def task_create_novel_job(agent_id: int, text: str, instructions: str, previous_page_id: int | None, helper_agents: list[int], job_id: str):
    async def run():
        job_dir = Path(settings.novelist_job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        job_path = job_dir / f"{job_id}.json"
        start_time = datetime.now(timezone.utc).isoformat()
        with open(job_path, "w") as f:
            json.dump({
                "status": "processing",
                "agent_id": agent_id,
                "job_type": "create_novel",
                "progress": 0,
                "start_time": start_time,
            }, f, default=str)

        async with async_session_maker() as session:
            agent = await get_agent(session, agent_id)
            if not agent:
                with open(job_path, "w") as ff:
                    json.dump({"status": "error", "error": "Agent not found", "start_time": start_time}, ff, default=str)
                return

            async def progress_cb(idx: int, total: int):
                with open(job_path, "w") as f:
                    json.dump({
                        "status": "processing",
                        "agent_id": agent_id,
                        "job_type": "create_novel",
                        "progress": idx + 1,
                        "chunks_total": total,
                        "start_time": start_time,
                    }, f, default=str)

            novel = await crud_novel.create_novel(
                session,
                agent,
                text,
                instructions,
                previous_page_id,
                helper_agents,
                progress_cb,
            )

        end_time = datetime.now(timezone.utc).isoformat()
        with open(job_path, "w") as f:
            json.dump({
                "status": "done",
                "agent_id": agent_id,
                "job_type": "create_novel",
                "novel": novel,
                "start_time": start_time,
                "end_time": end_time,
                "action_needed": "review",
            }, f, default=str)

    asyncio.run(run())
