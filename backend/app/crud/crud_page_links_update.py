import re
from sqlalchemy.future import select
from bs4 import BeautifulSoup

from app.database import async_session_maker
from app.crud.crud_page import get_page
from app.crud.crud_user_note import get_user_note

from app.models.model_page import Page, PageCharacteristicValue
from app.models.model_user_note import UserNote
from app.models.model_characteristic import Characteristic, ConceptCharacteristicLink


CROSSLINK_RE = re.compile(r'/worlds/\d+/concept/\d+/page/(\d+)')
async def remove_page_refs_from_characteristics(deleted_page: Page | int):
    """Remove references to a deleted page from all page reference characteristics
    within the same game world."""
    async with async_session_maker() as session:
        if isinstance(deleted_page, Page):
            deleted_page = await get_page(session, deleted_page.id)
        else:
            deleted_page = await get_page(session, deleted_page)

        if not deleted_page:
            return

        result = await session.execute(
            select(Characteristic).where(
                (Characteristic.type == "page_ref") &
                (Characteristic.gameworld_id == deleted_page.gameworld_id)
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

async def remove_crosslinks_to_page(deleted_page_id: int):    
    async with async_session_maker() as session:
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


async def auto_crosslink_page_content(page):

    async with async_session_maker() as session:
        if isinstance(page, Page):
            page = await get_page(session, page.id)
        else:
            page = await get_page(session, page)
        
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


        
        # Parse HTML for Content
        soup = BeautifulSoup(page.content or "", "html.parser")
        content_changed = False

        for name, target_page in page_name_map.items():
            # Check if already linked (ignore case, href and innerText both)


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
            

            for element in soup.find_all(string=True):
                if element.find_parent("a"):
                    continue  # skip if already inside any <a>
            

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

        if content_changed:
            try:
                new_content = str(soup)                
                page.content = new_content        
                await session.commit()
                await session.flush()
            except Exception as e:
                        print("CROSSLINK BACKGROUND TASK ERROR:", repr(e))
                        import traceback; traceback.print_exc()    

        #Parse HTML for autogenerated content
        soup = BeautifulSoup(page.autogenerated_content or "", "html.parser")
        auto_content_changed = False

        for name, target_page in page_name_map.items():
            # Check if already linked (ignore case, href and innerText both)


            already_linked = False
            for a in soup.find_all("a", href=True):
                if (
                    a.get("href") == f"/worlds/{target_page.gameworld_id}/concept/{target_page.concept_id}/page/{target_page.id}"
                    or a.get_text(strip=True).lower() == name
                ):
                    already_linked = True
                    break
            if already_linked:                
                continue
            
            # Search for unlinked text nodes and add links
            pattern = re.compile(rf"\b({re.escape(target_page.name)})\b", re.IGNORECASE)
            

            for element in soup.find_all(string=True):
                if element.find_parent("a"):
                    continue  # skip if already inside any <a>
            

                if pattern.search(element):
                    # Replace only the first occurrence in this node
                    def repl(m):
                        url = f"/worlds/{target_page.gameworld_id}/concept/{target_page.concept_id}/page/{target_page.id}"
                        return f'<a href="{url}" class="wiki-link" title="{target_page.name}">{m.group(0)}</a>'
                    new_html = pattern.sub(repl, element, count=1)
                    new_nodes = BeautifulSoup(new_html, "html.parser")
                    
                    element.replace_with(new_nodes)
                    auto_content_changed = True
                    break  # Only link the first occurrence per page name


        if auto_content_changed:
            try:
                new_content = str(soup)                
                page.autogenerated_content = new_content        
                await session.commit()
                await session.flush()
            except Exception as e:
                        print("CROSSLINK BACKGROUND TASK ERROR:", repr(e))
                        import traceback; traceback.print_exc()   
        


async def auto_crosslink_batch(new_page_id: int):
    """Update existing pages to link to the newly created page."""
    async with async_session_maker() as session:
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
        await auto_crosslink_page_content(page)

async def sync_page_ref_attributes(page: Page | int):
    """Ensure page reference characteristics are mirrored on the referenced page."""
    async with async_session_maker() as session:
        if isinstance(page, Page):
            page = await get_page(session, page.id)
        else:
            page = await get_page(session, page)

        if not page:
            return

        result = await session.execute(
            select(PageCharacteristicValue, Characteristic)
            .join(Characteristic, PageCharacteristicValue.characteristic_id == Characteristic.id)
            .where(PageCharacteristicValue.page_id == page.id)
            .where(Characteristic.type == "page_ref")
        )

        for pcv, char in result.all():
            # value can be stored as either a list or a single string depending
            # on the characteristic configuration. Normalise it to a list of
            # strings so the rest of the logic can assume a list.
            if pcv.value is None:
                continue
            ref_ids = pcv.value if isinstance(pcv.value, list) else [pcv.value]
            ref_ids = [str(r) for r in ref_ids if r is not None]
            if not ref_ids:
                continue
            for ref_id in ref_ids:
                ref_page = await get_page(session, int(ref_id))
                if not ref_page:
                    continue

                rev_chars_result = await session.execute(
                    select(Characteristic)
                    .join(ConceptCharacteristicLink, ConceptCharacteristicLink.characteristic_id == Characteristic.id)
                    .where(ConceptCharacteristicLink.concept_id == ref_page.concept_id)
                    .where(Characteristic.type == "page_ref")
                    .where(Characteristic.ref_concept_id == page.concept_id)
                )
                rev_chars = rev_chars_result.scalars().all()
                if not rev_chars:
                    continue

                for rev_char in rev_chars:
                    rev_val = await session.get(PageCharacteristicValue, (ref_page.id, rev_char.id))
                    if rev_val:
                        vals = rev_val.value
                        if vals is None:
                            vals = []
                        elif not isinstance(vals, list):
                            vals = [str(vals)]
                        if str(page.id) not in vals:
                            vals.append(str(page.id))
                            rev_val.value = vals
                    else:
                        session.add(
                            PageCharacteristicValue(
                                page_id=ref_page.id,
                                characteristic_id=rev_char.id,
                                value=[str(page.id)],
                            )
                        )

        await session.commit()
        await session.flush()


async def auto_crosslink_note_content(note: UserNote | int):
    """Add wiki cross-reference links inside a user note."""
    async with async_session_maker() as session:
        if isinstance(note, UserNote):
            note = await get_user_note(session, note.id)
        else:
            note = await get_user_note(session, note)

        if not note:
            return

        if note.gameworld_id:
            candidate_pages = await session.execute(
                select(Page)
                .where(Page.gameworld_id == note.gameworld_id)
                .where(Page.ignore_crosslink == False)
            )
        else:
            candidate_pages = await session.execute(
                select(Page).where(Page.ignore_crosslink == False)
            )
        candidate_pages = candidate_pages.scalars().all()

        page_name_map = {}
        for cp in candidate_pages:
            if cp.name.lower() not in page_name_map:
                page_name_map[cp.name.lower()] = cp

        soup = BeautifulSoup(note.content or "", "html.parser")
        content_changed = False
        for name, target_page in page_name_map.items():
            already_linked = False
            for a in soup.find_all("a", href=True):
                if (
                    a.get("href")
                    == f"/worlds/{target_page.gameworld_id}/concept/{target_page.concept_id}/page/{target_page.id}"
                    or a.get_text(strip=True).lower() == name
                ):
                    already_linked = True
                    break
            if already_linked:
                continue
            pattern = re.compile(rf"\b({re.escape(target_page.name)})\b", re.IGNORECASE)
            for element in soup.find_all(string=True):
                if element.find_parent("a"):
                    continue
                if pattern.search(element):
                    def repl(m):
                        url = f"/worlds/{target_page.gameworld_id}/concept/{target_page.concept_id}/page/{target_page.id}"
                        return f'<a href="{url}" class="wiki-link" title="{target_page.name}">{m.group(0)}</a>'
                    new_html = pattern.sub(repl, element, count=1)
                    new_nodes = BeautifulSoup(new_html, "html.parser")
                    element.replace_with(new_nodes)
                    content_changed = True
                    break

        if content_changed:
            try:
                note.content = str(soup)
                await session.commit()
                await session.flush()
            except Exception as e:
                print("NOTE CROSSLINK ERROR:", repr(e))
