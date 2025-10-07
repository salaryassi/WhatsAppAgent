import logging
from fuzzywuzzy import fuzz
from .database import list_unforwarded_receipts

logger = logging.getLogger("app.utils")

def find_match_in_db(customer_name, top_n=100, threshold=60):
    """
    Find best match among unforwarded receipts using fuzzy matching.
    Returns (best_match_id, score) or (None, 0)
    """
    receipts = list_unforwarded_receipts(limit=top_n)
    best_match = None
    best_score = 0
    for r in receipts:
        score = fuzz.token_sort_ratio(customer_name or "", r["customer_name"] or "")
        if score > best_score:
            best_score = score
            best_match = r["id"]
    logger.debug("find_match_in_db(%s) -> (%s, %s)", customer_name, best_match, best_score)
    if best_score >= threshold:
        return best_match, best_score
    return None, best_score
