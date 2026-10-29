import asyncio
from datetime import datetime, timezone, timedelta
import random
from src.bot_engine import Mingle2BotEngine
from src.config import settings
import src.database as database

async def main():
    await database.init_db()
    print("Clearing any leftover simulated likes...")
    cleared = await database.clear_simulated_likes()
    print(f"Cleared {cleared} simulated likes.")

    print("Initializing bot engine and scanning likers across all pages...")
    engine = Mingle2BotEngine(log_callback=lambda l, m: print(f"[{l}] {m}", flush=True))
    users = await engine.scan_interested_users(max_pages=10)
    print(f"Engine scanned {len(users)} users.", flush=True)

    added = 0
    now = datetime.now(timezone.utc)
    for u in users:
        user_id = u["user_id"]
        username = u["username"]
        profile_url = u["profile_url"]
        avatar_url = u.get("avatar_img_url")
        age_loc = u.get("age_location")
        detected_at = u.get("detected_at", now)

        delay_secs = settings.like_delay_minutes * 60
        jitter_range = max(5, settings.human_jitter_seconds)
        jitter = random.uniform(-jitter_range, jitter_range)
        scheduled_secs = max(20, delay_secs + jitter)
        scheduled_like_at = detected_at + timedelta(seconds=scheduled_secs)

        record, is_new = await database.add_or_get_liker(
            user_id=user_id,
            username=username,
            profile_url=profile_url,
            avatar_img_url=avatar_url,
            age_location=age_loc,
            detected_at=detected_at,
            scheduled_like_at=scheduled_like_at,
        )
        if is_new:
            added += 1

    stats = await database.get_stats()
    print(f"Ingestion complete: {added} new likers added. Total in queue: {stats.get('total_detected')}", flush=True)
    await engine.stop_active_browser()

if __name__ == "__main__":
    asyncio.run(main())
