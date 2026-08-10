import time
import json
import os
from datetime import datetime
from threading import Thread
from sqlalchemy.orm import Session
from db import engine, ScheduledPost, update_scheduled_post_status
from services.social_publisher_service import SocialPublisherService

def process_due_posts(app_root_path):
    """Query the DB for pending posts that are due, and publish them."""
    with Session(engine) as session:
        now = datetime.now()
        due_posts = session.query(ScheduledPost).filter(
            ScheduledPost.status == 'pending',
            ScheduledPost.scheduled_at <= now
        ).all()

        if not due_posts:
            return

        publisher = SocialPublisherService()

        for post in due_posts:
            print(f"[Scheduler] Processing Scheduled Post #{post.id} for user {post.user_id}...")
            
            try:
                platforms = json.loads(post.platforms)
            except Exception:
                platforms = [post.platforms]
                
            try:
                content = json.loads(post.content_json)
            except Exception:
                content = {}

            caption = content.get('caption') or content.get('story') or ''
            image_url = content.get('image_url')
            
            abs_image_path = None
            if image_url:
                rel_path = image_url.lstrip('/').replace('/', os.sep)
                candidate = os.path.join(app_root_path, rel_path)
                if os.path.exists(candidate):
                    abs_image_path = candidate
            
            # Publish!
            results = publisher.publish_post_to_connected_accounts(
                user_id=post.user_id,
                platforms=platforms,
                caption=caption,
                image_path=abs_image_path
            )
            
            # Update status based on success
            any_success = any(res.get('success') for res in results.values())
            
            new_status = 'published' if any_success else 'failed'
            print(f"[Scheduler] Post #{post.id} completed with status: {new_status}. Results: {results}")
            
            # Update DB
            post.status = new_status
            session.commit()

def run_scheduler(app_root_path):
    print("[Scheduler] Background scheduling thread started...")
    while True:
        try:
            process_due_posts(app_root_path)
        except Exception as e:
            print(f"[Scheduler] Error processing posts: {e}")
        
        # Check every 20 seconds
        time.sleep(20)

def start_background_scheduler(app_root_path):
    t = Thread(target=run_scheduler, args=(app_root_path,), daemon=True)
    t.start()
