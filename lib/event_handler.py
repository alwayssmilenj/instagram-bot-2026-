"""
Knight Bot Instagram - Event Handler
Handles Instagram events (follows, likes, etc.)
"""

import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings


class EventHandler:
    """Handles Instagram events"""
    
    def __init__(self, client):
        self.cl = client
        self.welcome_enabled = getattr(settings, "WELCOME_ENABLED", False)
        self.goodbye_enabled = getattr(settings, "GOODBYE_ENABLED", False)
        
    async def handle_follow(self, user_id: str):
        """Handle new follower event"""
        try:
            user_info = self.cl.user_info(user_id)
            print(f"👥 New follower: @{user_info.username}")
            
            # Auto-follow back (optional)
            # self.cl.user_follow(user_id)
            
        except Exception as e:
            print(f"Error handling follow: {e}")
            
    async def handle_like(self, media_id: str, user_id: str):
        """Handle like event"""
        try:
            user_info = self.cl.user_info(user_id)
            print(f"❤️ @{user_info.username} liked post {media_id}")
        except Exception as e:
            print(f"Error handling like: {e}")
            
    async def handle_comment(self, media_id: str, user_id: str, text: str):
        """Handle comment event"""
        try:
            user_info = self.cl.user_info(user_id)
            print(f"💬 @{user_info.username} commented: {text}")
        except Exception as e:
            print(f"Error handling comment: {e}")
            
    async def handle_mention(self, text: str, user_id: str, media_id: str = None):
        """Handle mention event"""
        try:
            user_info = self.cl.user_info(user_id)
            print(f"📢 @{user_info.username} mentioned you: {text}")
            
            # Auto-reply to mentions (optional)
            
        except Exception as e:
            print(f"Error handling mention: {e}")
