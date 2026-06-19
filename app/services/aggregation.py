"""
Background aggregation job for click events.

Instead of updating urls.click_count on every redirect (which causes
row-level lock contention under load), clicks are written to a separate
click_events table and periodically aggregated here.

Pattern from Milestone 4: Hybrid Write Pattern
- Redirect path: INSERT into click_events (fast, no lock)
- This job: UPDATE urls.click_count (batched, off the hot path)
"""

import asyncio
import logging
from sqlalchemy import text
from app.db.database import SessionLocal

logger = logging.getLogger("url_shortener.aggregation")

AGGREGATION_INTERVAL = 60  # seconds


async def run_aggregation_loop():
    """
    Background task that aggregates click_events every 60 seconds.

    Steps:
    1. SELECT unprocessed click_events grouped by short_code
    2. UPDATE urls.click_count with the new counts
    3. Mark processed click_events as processed = TRUE
    """
    logger.info("🔄 Click aggregation background job started (interval=%ds)", AGGREGATION_INTERVAL)

    while True:
        try:
            await asyncio.sleep(AGGREGATION_INTERVAL)
            aggregate_clicks()
        except asyncio.CancelledError:
            logger.info("Aggregation job cancelled — shutting down")
            break
        except Exception as e:
            logger.error("Aggregation job error: %s", e, exc_info=True)
            # Don't crash the loop — wait and retry
            await asyncio.sleep(AGGREGATION_INTERVAL)


def aggregate_clicks():
    """
    Synchronous aggregation function.
    Groups unprocessed click_events by short_code and batch-updates urls.click_count.
    """
    db = SessionLocal()
    try:
        # Step 1: Get unprocessed click counts per short_code
        result = db.execute(text("""
            SELECT short_code, COUNT(*) as new_clicks
            FROM click_events
            WHERE processed = FALSE
            GROUP BY short_code
        """))

        rows = result.fetchall()

        if not rows:
            logger.debug("No unprocessed click events to aggregate")
            return

        total_aggregated = 0

        for row in rows:
            short_code = row[0]
            new_clicks = row[1]

            # Step 2: Update pre-computed click_count on urls table
            db.execute(text("""
                UPDATE urls
                SET click_count = click_count + :new_clicks
                WHERE short_code = :code
            """), {"new_clicks": new_clicks, "code": short_code})

            # Step 3: Mark events as processed
            db.execute(text("""
                UPDATE click_events
                SET processed = TRUE
                WHERE short_code = :code AND processed = FALSE
            """), {"code": short_code})

            total_aggregated += new_clicks

        db.commit()
        logger.info(
            "✅ Aggregated %d clicks across %d short codes",
            total_aggregated,
            len(rows)
        )

    except Exception as e:
        db.rollback()
        logger.error("Aggregation failed: %s", e, exc_info=True)
    finally:
        db.close()
