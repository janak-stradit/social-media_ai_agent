import os, json
from dotenv import load_dotenv
load_dotenv()
import db
u = db.get_user_by_email('vaishnavi@stradit.com')
bp = db.get_user_brand_profile(u.id)
if 'typography' not in bp: bp['typography'] = {}
bp['typography']['fonts'] = ['Lato', 'Helvetica Neue', 'Arial']
db.update_user_brand_profile_fields(u.id, typography=bp['typography'])
print('done')
