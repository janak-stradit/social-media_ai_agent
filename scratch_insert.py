import sqlite3, datetime, json
from app import create_app

conn = sqlite3.connect('social_media_agent.db')
conn.execute('DELETE FROM content_collections')

# Sep 18 clusters (Recruitment)
clusters = [
    (1, 'BNY Mellon Digital Assets & AI Hiring', 'Recruitment for senior engineering roles focused on Digital Assets, Post-Trade Technology, and AI Engineering.', 'high', 'BNY Mellon', 'linkedin_jobs', '["https://www.linkedin.com/jobs/view/4419554706/"]', 11, '2026-09-18 09:11:41.300701'),
    (2, 'BlackRock AI Labs Recruitment', 'Job postings for BlackRock AI Labs, focusing on asset management and risk technology.', 'high', 'BlackRock', 'linkedin_jobs', '["https://www.linkedin.com/jobs/view/4457311830/"]', 3, '2026-09-18 09:11:41.300701'),
    (3, 'BNY Mellon Enterprise Risk Engineering Hiring', 'Recruitment for senior application development managers in enterprise risk engineering.', 'high', 'BNY Mellon', 'linkedin_jobs', '["https://www.linkedin.com/jobs/view/4447565569/"]', 2, '2026-09-18 09:11:41.300701'),
    
    # Sep 15 clusters
    (4, 'Singapore Unit Trust Expansion', 'Competitors expanding their unit trust offerings in Singapore.', 'high', 'Vanguard', 'linkedin', '[]', 5, '2026-09-15 09:11:41.300701'),
    (5, 'Pension Fund Asset Servicing Wins', 'New mandates and wins in the pension fund asset servicing space.', 'high', 'State Street', 'linkedin', '[]', 4, '2026-09-15 09:11:41.300701'),
    (6, 'BlackRock Private Credit Fund Trends', 'Growing focus on private credit fund structures.', 'high', 'BlackRock', 'linkedin', '[]', 6, '2026-09-15 09:11:41.300701'),
    (7, 'Vanguard ETF Product Expansion', 'Launch of new ETF products in emerging markets.', 'high', 'Vanguard', 'linkedin', '[]', 7, '2026-09-15 09:11:41.300701'),
    (8, 'Northern Trust ESG Thought Leadership', 'Publishing whitepapers on sustainable investing and ESG.', 'high', 'Northern Trust', 'linkedin', '[]', 3, '2026-09-15 09:11:41.300701')
]

for idx, label, desc, rel, comp, plat, urls, count, date in clusters:
    import hashlib
    h = hashlib.sha256(label.encode()).hexdigest()
    conn.execute('INSERT INTO content_collections (post_urls_hash, label, description, relevance, competitors, platforms, post_urls, post_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (h, label, desc, rel, comp, plat, urls, count, date))

conn.commit()
print('Inserted 8 clusters manually!')
