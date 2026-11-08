"""Resumable media-only PDF backfill; run from shreckworld with PYTHONPATH=. .

Example: python scripts/backfill_document_media.py --item-id ITEM --max-pages 50
Existing manifests are reused. No embedding, document activation or vectors change.
"""
import argparse
from sqlalchemy import select
from app.db.session import session_factory
from app.models import ShreckWorldDocument, ShreckWorldLibraryItem
from app.services.document_media import catalog


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--item-id',required=True)
    parser.add_argument('--max-pages',type=int,default=50)
    args=parser.parse_args()
    if not 1<=args.max_pages<=100000: parser.error('max-pages must be 1..100000')
    with session_factory()() as session:
        item=session.get(ShreckWorldLibraryItem,args.item_id)
        doc=session.scalar(select(ShreckWorldDocument).where(ShreckWorldDocument.library_item_id==args.item_id,ShreckWorldDocument.is_active.is_(True)))
        if not item or not doc: parser.error('active item/document required')
        import fitz
        with fitz.open(item.storage_path or item.pdf_path) as pdf: count=min(len(pdf),args.max_pages)
        for page in range(1,count+1):
            rows=catalog(item,doc,page)
            print(f'{page}: {len(rows)} media assets')


if __name__=='__main__': main()
