"""
Standalone script to synchronize Scraper Explorer intelligence into the AI Agent's RAG memory.
Run this script periodically (e.g., via cron) to keep the AI aware of the latest company storylines.
"""

import os
import sys

# Ensure we can import from the main app structure
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import init_db  # noqa: E402  pylint: disable=wrong-import-position
from services.memory_service import MemoryService  # noqa: E402  pylint: disable=wrong-import-position
from services.scraper_service import ScraperService  # noqa: E402  pylint: disable=wrong-import-position


def sync_companies(companies):
    print("Initializing Database and RAG Memory...")
    init_db()

    # We will use a dummy user_id for global/system memory
    system_user_id = 999999

    scraper = ScraperService()
    memory = MemoryService()

    for company in companies:
        print(f"\nFetching live intelligence for {company}...")
        intelligence = scraper.get_company_talking_points(company)

        if intelligence and "API OFFLINE" not in intelligence:
            print(f"Syncing {len(intelligence)} chars of data to RAG memory...")
            memory.store_content(
                content_id=f"intelligence_{company.replace(' ', '_').lower()}",
                text=intelligence,
                metadata={"topic": f"{company} Social Intelligence", "user_id": str(system_user_id)}
            )
            print("Successfully stored in ChromaDB.")
        else:
            print(f"Skipping {company} - No valid live data retrieved.")


if __name__ == "__main__":
    target_companies = ["BlackRock", "BNY Mellon", "Northern Trust", "Vanguard"]
    sync_companies(target_companies)
    print("\nSync Complete!")
