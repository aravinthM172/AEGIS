import asyncio

from app.services.monitoring import MonitoringService


async def main():

    urls = [
        "https://this-domain-does-not-exist-12345.com"
    ]

    monitoring_service = MonitoringService()

    for i in range(5):

        print(f"\nCheck #{i + 1}")

        results = await monitoring_service.check_multiple(urls)

        for result in results:
            print(result)

        await asyncio.sleep(1)

    await monitoring_service.close()


if __name__ == "__main__":
    asyncio.run(main())