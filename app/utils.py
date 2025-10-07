from fuzzywuzzy import fuzz

def find_match_in_db(customer_name, db_connection):
    cursor = db_connection.cursor()
    cursor.execute("SELECT id, customer_name FROM receipts WHERE forwarded = FALSE")
    receipts = cursor.fetchall()
    best_match = None
    best_score = 0
    for receipt in receipts:
        score = fuzz.token_sort_ratio(customer_name, receipt['customer_name'])
        if score > best_score:
            best_score = score
            best_match = receipt['id']
    return best_match, best_score