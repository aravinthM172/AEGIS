import asyncio
from datetime import datetime

from app.database import SessionLocal
from app.models import MonitorResult, Target
from app.services.monitor import check_url


MONITOR_INTERVAL = 60


async def monitor_loop():

    print("Aegis background monitor started")

    while True:

        db = SessionLocal()

        try:
            targets = (
                db.query(Target)
                .filter(Target.enabled == True)
                .all()
            )

            for target in targets:

                try:
                    result = check_url(target.url)

                    record = MonitorResult(
                        url=target.url,
                        is_up=result["is_up"],
                        status_code=result["status_code"],
                        response_time_ms=result["response_time_ms"],
                        error=result["error"]
                    )

                    db.add(record)

                    status = (
                        "UP"
                        if result["is_up"]
                        else "DOWN"
                    )

                    print(
                        f"[{datetime.now()}] "
                        f"{target.url} -> "
                        f"{status} | "
                        f"{result['response_time_ms']} ms"
                    )

                except Exception as error:

                    print(
                        f"Monitoring error: "
                        f"{target.url} -> {error}"
                    )

            db.commit()

        finally:
            db.close()

        await asyncio.sleep(MONITOR_INTERVAL)