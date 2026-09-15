import sqlite3
con = sqlite3.connect('social_media_agent.db')
try:
    con.execute("INSERT INTO brand_assets (key, label, filename, created_at) VALUES ('ida', 'Ida — Brand Mascot', 'Ida.jpeg', datetime('now'))")
    con.commit()
    print('Inserted ida')
except Exception as e:
    print('Error inserting ida:', e)
