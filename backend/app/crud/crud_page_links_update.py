import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.model_page import Page, PageCharacteristicValue
from app.models.model_characteristic import Characteristic

from app.database import async_session_maker
from app.crud.crud_page import get_page

CROSSLINK_RE = re.compile(r'/worlds/\d+/concept/\d+/page/(\d+)')

from bs4 import BeautifulSoup


async def remove_page_refs_from_characteristics(
    deleted_page: Page | int, *, session: AsyncSession | None = None
):
    """
    Remove references to the deleted page in any PageCharacteristicValue
    for characteristics of type 'page_ref' that point to the same concept as the deleted page.
    """    
    own_session = session is None
    if own_session:
        async with async_session_maker() as session:
            await _remove_page_refs_from_characteristics(session, deleted_page)
    else:
        await _remove_page_refs_from_characteristics(session, deleted_page)


async def _remove_page_refs_from_characteristics(session: AsyncSession, deleted_page: Page | int):
    if isinstance(deleted_page, Page):
        deleted_page = await get_page(session, deleted_page.id)
    else:
        deleted_page = await get_page(session, deleted_page)

        if not deleted_page:
            return

        result = await session.execute(
            select(Characteristic).where(
                (Characteristic.type == "page_ref") &
                (Characteristic.ref_concept_id == deleted_page.concept_id)
            )
        )
        page_ref_characteristics = result.scalars().all()

        if not page_ref_characteristics:
            return

        for char in page_ref_characteristics:
            # 2. For each such characteristic, find all PageCharacteristicValue with this characteristic_id
            res_vals = await session.execute(
                select(PageCharacteristicValue).where(
                    PageCharacteristicValue.characteristic_id == char.id
                )
            )
            vals = res_vals.scalars().all()

            for pcv in vals:
                if not pcv.value:
                    continue
                # 3. Remove the deleted page id from the value list (always as string)
                value_list = [str(v) for v in pcv.value]
                if str(deleted_page.id) in value_list:
                    value_list = [v for v in value_list if v != str(deleted_page.id)]
                    pcv.value = value_list
                    await session.commit()
                    await session.flush()

async def remove_crosslinks_to_page(
    deleted_page_id: int, *, session: AsyncSession | None = None
):
    own_session = session is None
    if own_session:
        async with async_session_maker() as session:
            await _remove_crosslinks_to_page(session, deleted_page_id)
    else:
        await _remove_crosslinks_to_page(session, deleted_page_id)


async def _remove_crosslinks_to_page(session: AsyncSession, deleted_page_id: int):
    # Get all pages except the one being deleted
    result = await session.execute(
        select(Page).where(Page.id != deleted_page_id)
    )
    all_pages = result.scalars().all()

    link_pattern = re.compile(rf'/worlds/\d+/concept/\d+/page/{deleted_page_id}\b')

    for page in all_pages:
        soup = BeautifulSoup(page.content or "", "html.parser")
        content_changed = False

        # Find all links that match the deleted page
        for a in soup.find_all("a", href=True):
            if link_pattern.search(a["href"]):
                # Remove the <a> tag, but keep the text inside
                a.replace_with(a.get_text())
                content_changed = True

        if content_changed:
            page.content = str(soup)
            await session.commit()
            await session.flush()


async def auto_crosslink_page_content(
    page: Page | int, *, session: AsyncSession | None = None
):
    own_session = session is None
    if own_session:
        async with async_session_maker() as session:
            await _auto_crosslink_page_content(session, page)
    else:
        await _auto_crosslink_page_content(session, page)


async def _auto_crosslink_page_content(session: AsyncSession, page: Page | int):
    if isinstance(page, Page):
        page = await get_page(session, page.id)
    else:
        page = await get_page(session, page)
        print (f"Page allows autocrosslinl: {page.allow_crosslinks} ")
        print (f"Page allows crossworld: {page.allow_crossworld} ")
        if not page.allow_crosslinks:
            return

        # Fetch candidate pages as before
        if not page.allow_crossworld:
            candidate_pages = await session.execute(
                select(Page)
                .where(Page.gameworld_id == page.gameworld_id)
                .where(Page.ignore_crosslink == False)
                .where(Page.id != page.id)
            )
            candidate_pages = candidate_pages.scalars().all()
        else:
            candidate_pages = await session.execute(
                select(Page)
                .where(Page.ignore_crosslink == False)
                .where(Page.id != page.id)                
            )
            candidate_pages = candidate_pages.scalars().all()

        # print (f"Candidate Pages: {candidate_pages} ")
        page_name_map = {}
        for cp in candidate_pages:
            if cp.name.lower() not in page_name_map:
                page_name_map[cp.name.lower()] = cp


        print (f"Candidate pages: {page_name_map.keys()} ")
        # Parse HTML
        soup = BeautifulSoup(page.content or "", "html.parser")
        content_changed = False

        for name, target_page in page_name_map.items():
            # Check if already linked (ignore case, href and innerText both)

            print (f"-- Checking for: {name}")

            already_linked = False
            for a in soup.find_all("a", href=True):
                if (
                    a.get("href") == f"/worlds/{target_page.gameworld_id}/concept/{target_page.concept_id}/page/{target_page.id}"
                    or a.get_text(strip=True).lower() == name
                ):
                    already_linked = True
                    break
            if already_linked:
                print (f" Name found!")
                continue
            
            # Search for unlinked text nodes and add links
            pattern = re.compile(rf"\b({re.escape(target_page.name)})\b", re.IGNORECASE)
            print (f" -- Name not found, looking for this pattern: {pattern}")

            for element in soup.find_all(string=True):
                if element.find_parent("a"):
                    continue  # skip if already inside any <a>

                print (f" --- Element: {element} - Pattern search: {pattern.search(element)}")

                if pattern.search(element):
                    # Replace only the first occurrence in this node
                    def repl(m):
                        url = f"/worlds/{target_page.gameworld_id}/concept/{target_page.concept_id}/page/{target_page.id}"
                        return f'<a href="{url}" class="wiki-link" title="{target_page.name}">{m.group(0)}</a>'
                    new_html = pattern.sub(repl, element, count=1)
                    new_nodes = BeautifulSoup(new_html, "html.parser")
                    print (f" ---- New Node: {new_nodes}")
                    element.replace_with(new_nodes)
                    content_changed = True
                    break  # Only link the first occurrence per page name


        print (f"Content changed: {content_changed}")
        if content_changed:
            try:
                new_content = str(soup)
                print (f"New content: {new_content}")
                page.content = new_content        
                await session.commit()
                await session.flush()
            except Exception as e:
                        print("CROSSLINK BACKGROUND TASK ERROR:", repr(e))
                        import traceback; traceback.print_exc()            


async def auto_crosslink_batch(
    new_page_id: int, *, session: AsyncSession | None = None
):
    """Update existing pages to link to the newly created page."""
    own_session = session is None
    if own_session:
        async with async_session_maker() as session:
            await _auto_crosslink_batch(session, new_page_id)
    else:
        await _auto_crosslink_batch(session, new_page_id)


async def _auto_crosslink_batch(session: AsyncSession, new_page_id: int):
    new_page = await get_page(session, new_page_id)
    if not new_page:
        return

    candidate_pages = await session.execute(
        select(Page)
        .where(Page.ignore_crosslink == False)
        .where(Page.id != new_page.id)
    )
    candidate_pages = candidate_pages.scalars().all()

    for page in candidate_pages:
        await _auto_crosslink_page_content(session, page)

