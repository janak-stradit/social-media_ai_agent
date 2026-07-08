import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
    UPLOAD_FOLDER = 'static/uploads'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    
    # API Keys
    HF_API_TOKEN = os.getenv('HF_API_TOKEN')
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    
    # AgentScope Config
    AGENTSCOPE_MODEL = os.getenv('AGENTSCOPE_MODEL', 'gpt-4o')
    
    # ChromaDB
    CHROMA_PERSIST_DIR = './chroma_db'
    
    # Redis (for Celery)
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig
}