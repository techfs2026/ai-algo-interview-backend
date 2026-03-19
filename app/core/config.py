from functools import lru_cache
from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # 应用
    app_env:    str = "development"
    app_host:   str = "0.0.0.0"
    app_port:   int = 8000
    secret_key: str = "dev-secret-key"

    # 数据库
    database_url: str = "postgresql+asyncpg://postgres:password@localhost:5432/ai_algo_interview"
    redis_url:    str = "redis://localhost:6379/0"

    # 向量数据库
    qdrant_host:       str = "localhost"
    qdrant_port:       int = 6333
    qdrant_collection: str = "questions"

    # LLM（默认Ollama本地）
    llm_provider:   str = "ollama"
    llm_api_key:    str = "ollama"          # Ollama不需要真实key
    llm_base_url:   str = "http://localhost:11434/v1"
    llm_model:      str = "qwen2.5:7b"      # 改成你本地的模型名
    llm_model_fast: str = "qwen2.5:7b"

    # Embedding
    embedding_api_key:    str = "ollama"
    embedding_base_url:   str = "http://localhost:11434/v1"
    embedding_model:      str = "nomic-embed-text"
    embedding_vector_size:int = 768   # 本地Ollama=768，线上QWen/OpenAI=1536

    judge_provider: str = "subprocess"
    # Judge0
    judge0_url:     str = ""
    judge0_api_key: str = ""

    # LLM调用参数
    llm_max_tokens:       int   = 1000
    llm_temperature:      float = 0.7
    llm_timeout_select:   int   = 60
    llm_timeout_analyze:  int   = 120
    llm_timeout_feedback: int   = 120
    llm_max_retries:      int   = 2

    # 业务配置
    question_cache_ttl:   int = 604800   # 题目缓存7天（秒）
    stream_chunk_timeout: int = 5        # 流式chunk间隔超时（秒）
    daily_swap_limit:     int = 2        # 每日换题次数


@lru_cache
def get_settings() -> Settings:
    return Settings()