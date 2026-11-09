"""Compile one assessed Web recovery batch; never recover from tool failure alone."""
from __future__ import annotations

import re
import unicodedata

from app.core.config_store import COMPANION_WEB_QUERY_LIMIT
from app.jobs.companion.query_contracts import PlannedCall


def normalized(value):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', value).casefold()))


def web_calls(assessment, needs, *, limit, private_terms=()):
    """Reject known private identifiers; semantic privacy remains a prompt duty.

    Query/need associations are backend retrieval intent, never evidence support.
    No raw-question fallback or public-query rendering model is used.
    """
    if assessment is None or assessment.sufficient or not needs:
        return [], []
    blocked = []
    calls = []
    private = {normalized(term) for term in private_terms if isinstance(term, str) and normalized(term)}
    for query in assessment.web_queries:
        candidate = ' ' + normalized(query) + ' '
        if any(' ' + term + ' ' in candidate for term in private):
            blocked.append({'reason': 'private_identifier'})
            continue
        if len(calls) >= min(COMPANION_WEB_QUERY_LIMIT, limit):
            break
        calls.append(PlannedCall(id=f'web-recovery-{len(calls)+1}', tool='web', query=query,
                                 need_ids=[need.id for need in needs]))
    return calls, blocked
