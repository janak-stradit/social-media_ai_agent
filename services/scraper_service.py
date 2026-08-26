import os
import requests
import json
import urllib3
from config import Config

# Disable SSL warnings for self-signed certificates (Not Secure HTTPS)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class ScraperService:
    """Service to integrate with the external Scraper Explorer (Social Intelligence) tool."""
    
    def __init__(self):
        # Default to the IP provided by user
        self.base_url = os.getenv('SCRAPER_API_URL', 'https://3.239.217.112/api')
        self.api_key = os.getenv('SCRAPER_API_KEY', '')
        self.headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"

    def get_company_talking_points(self, company_name: str) -> str:
        """
        Fetches the latest talking points and storylines for a specific company.
        If the API fails (or isn't configured), returns a fallback string.
        """
        if not company_name or company_name.lower() == "none":
            return ""
            
        try:
            # Map frontend dropdown names to scraper keys
            company_map = {
                "BlackRock": "blackrock",
                "BNY Mellon": "bny",
                "Northern Trust": "northern_trust",
                "The Vanguard Group": "vanguard",
                "Vanguard": "vanguard"
            }
            company_key = company_map.get(company_name, company_name.lower().replace(" ", "_"))
            
            # Attempt to fetch from the scraper's REST API
            response = requests.get(
                f"{self.base_url}/accounts/{company_key}",
                headers=self.headers,
                timeout=8,
                verify=False
            )
            response.raise_for_status()
            data = response.json()
            
            # Format the output into a readable context string
            context = f"--- SCRAPED INTELLIGENCE FOR {company_name.upper()} ---\n"
            
            digest = data.get("digest")
            if digest:
                if isinstance(digest, dict):
                    for k, v in digest.items():
                        if isinstance(v, list):
                            context += f"**{k.replace('_', ' ').title()}**:\n"
                            for item in v:
                                context += f"- {item}\n"
                            context += "\n"
                        elif isinstance(v, str):
                            context += f"**{k.replace('_', ' ').title()}**: {v}\n\n"
                else:
                    context += str(digest) + "\n"
            else:
                context += "No recent digest generated for this company.\n"
                
            return context + "----------------------------------------\n"
            
        except Exception as e:
            print(f"[ScraperService] Failed to fetch data for {company_name}: {e}")
            # Mock fallback data so the feature still works/demonstrates functionality
            return (
                f"--- SCRAPED INTELLIGENCE FOR {company_name.upper()} ---\n"
                f"- [DRY RUN - API OFFLINE] In an always-on market, #CollateralManagement will continue to play a central role.\n"
                f"- [DRY RUN - API OFFLINE] Highlight resilience and liquidity in recent SEC filings.\n"
                f"- [DRY RUN - API OFFLINE] Weak presence on LinkedIn regarding ESG initiatives - opportunity to capitalize.\n"
                f"----------------------------------------\n"
            )

    def get_company_store(self, company_name: str) -> list:
        """
        Fetches the raw store object for a company and returns a list of posts.
        """
        if not company_name or company_name.lower() == "none":
            return []
            
        try:
            company_map = {
                "BlackRock": "blackrock",
                "BNY Mellon": "bny",
                "Northern Trust": "northern_trust",
                "The Vanguard Group": "vanguard",
                "Vanguard": "vanguard"
            }
            company_key = company_map.get(company_name, company_name.lower().replace(" ", "_"))
            
            response = requests.get(
                f"{self.base_url}/accounts/{company_key}",
                headers=self.headers,
                timeout=8,
                verify=False
            )
            response.raise_for_status()
            data = response.json()
            
            store = data.get("store", {})
            store_data = store.get("data", {})
            
            all_posts = []
            for platform, platform_data in store_data.items():
                if isinstance(platform_data, dict):
                    posts = platform_data.get("posts", [])
                    all_posts.extend(posts)
                    
            # Sort by rank or published_at if desired, but returning the raw list is fine
            return all_posts
            
        except Exception as e:
            print(f"[ScraperService] Failed to fetch store for {company_name}: {e}")
            # Mock fallback data for the dashboard
            return [
                {
                    "title": "[Mock] We are proud to announce our new ESG initiative for Q3.",
                    "text": "At " + company_name + ", we believe in a sustainable future. Today we are launching our massive Q3 initiative...",
                    "platform": "linkedin",
                    "post_url": "https://linkedin.com",
                    "scraped_at": "2026-08-25T00:00:00Z"
                },
                {
                    "title": "[Mock] Managing Collateral in a 24/7 Market",
                    "text": "The market never sleeps. See how our new collateral management systems are helping clients globally.",
                    "platform": "blog",
                    "post_url": "https://blog.com",
                    "scraped_at": "2026-08-24T00:00:00Z"
                }
            ]

    def get_platform_posts(self, platform_name: str) -> list:
        """
        Iterates over the core competitors and fetches their posts
        filtered specifically for the given platform (e.g. 'linkedin').
        """
        competitors = ['BlackRock', 'BNY Mellon', 'Northern Trust', 'The Vanguard Group']
        combined_posts = []
        
        for comp in competitors:
            # Reusing the underlying fetch logic. A bit inefficient for multiple calls,
            # but works since we only have 4 competitors.
            comp_store = self.get_company_store(comp)
            # Filter posts for the specific platform
            # Note: platform_name should match the key in the JSON, e.g., 'linkedin', 'blog'
            platform_lower = platform_name.lower()
            for post in comp_store:
                if post.get('platform', '').lower() == platform_lower:
                    # Tag the post with its source competitor
                    post['_source_competitor'] = comp
                    combined_posts.append(post)
                    
        return combined_posts
