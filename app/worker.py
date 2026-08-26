import asyncio
import logging
from datetime import datetime, timezone

from app.services.monitor import check_url
from app.redis_client import save_monitor_result
from app.targets import get_active_targets


logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("aegis-worker")


async def monitor_loop():
    logger.info("Aegis background monitoring worker started")

    while True:

        try:
            targets = get_active_targets()

            if not targets:
                logger.info("No active monitoring targets")

            for target in targets:

                url = target["url"]

                try:
                    result = check_url(url)

                    monitor_result = {
                        "target_id": target["id"],
                        "target_name": target["name"],
                        "url": url,
                        "timestamp": datetime.now(
                            timezone.utc
                        ).isoformat(),
                        **result,
                    }

                    save_monitor_result(monitor_result)

                    logger.info(
                        "MONITOR | target=%s | url=%s | "
                        "up=%s | status=%s | response_time=%sms",
                        target["name"],
                        url,
                        result["is_up"],
                        result["status_code"],
                        result["response_time_ms"],
                    )

                except Exception as e:

                    logger.error(
                        "Monitoring failed for %s: %s",
                        url,
                        e,
                    )

        except Exception as e:

            logger.error(
                "Worker cycle failed: %s",
                e,
            )

        await asyncio.sleep(30)


def start_worker():
    asyncio.create_task(monitor_loop())