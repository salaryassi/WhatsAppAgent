# app/test_flow.py

import logging
from app.database import get_db_connection, setup_database, log_query
from app.utils import find_match_in_db

# Configure logging to see output in terminal
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# First, ensure database is set up
setup_database()

# Simulated incoming messages
incoming_messages = [
    {"customer_name": "John Doe", "group": "group1@g.us"},
    {"customer_name": "Jane Smith", "group": "group2@g.us"},
    {"customer_name": "John Doe", "group": "group2@g.us"},
    {"customer_name": "Alice", "group": "group3@g.us"},
]

for msg in incoming_messages:
    customer_name = msg["customer_name"]
    query_group = msg["group"]

    # Log the query (simulating webhook received)
    try:
        log_query(
            customer_name=customer_name,
            query_group=query_group,
            matched_receipt_id=None,
            status="received"
        )
        logging.info(f"Logged message from {customer_name} in {query_group}")
    except Exception as e:
        logging.error(f"Error logging message {customer_name} from {query_group}: {e}")

    # Check for matches in receipts table
    try:
        conn = get_db_connection()
        matched_receipt_id, best_score = find_match_in_db(customer_name, conn)
        if matched_receipt_id:
            logging.info(f"Found match for {customer_name}: {matched_receipt_id} (score: {best_score})")
        else:
            logging.info(f"No match found for {customer_name}")
        conn.close()
    except Exception as e:
        logging.error(f"Error checking match for {customer_name}: {e}")

