import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv


@dataclass
class AgentModelConfig:
    model: str
    thinking: bool
    temperature: float = 0.7


# Agent → 模型配置映射
AGENT_MODEL_MAP = {
    # 架构层
    "world_builder":      AgentModelConfig(model="pro", thinking=True, temperature=0.5),
    "plot_designer":      AgentModelConfig(model="pro", thinking=True, temperature=0.5),

    # 创作层
    "chapter_planner":    AgentModelConfig(model="flash", thinking=True, temperature=0.5),
    "deepseek_writer":    AgentModelConfig(model="pro", thinking=False, temperature=0.9),

    # 质量层
    "plan_reviewer":      AgentModelConfig(model="flash", thinking=True, temperature=0.3),
    "state_manager":      AgentModelConfig(model="flash", thinking=True, temperature=0.3),
}


class Settings:
    """全局配置，从 .env 加载"""

    def __init__(self, env_file: Optional[str] = None):
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")

        # 项目根目录
        self.project_root = Path(__file__).parent.parent.parent
        self.data_dir = self.project_root / "data"
        self.prompts_dir = Path(__file__).parent / "prompts"

        # ── E04 RAG 配置 ──────────────────────────────────
        self.rag_chunk_size: int = 800
        self.rag_chunk_overlap: int = 100
        self.rag_top_k: int = 5

        # 模型名称映射
        self.model_names = {
            "pro": "deepseek-v4-pro",
            "flash": "deepseek-v4-flash",
        }

    def get_agent_config(self, agent_name: str) -> AgentModelConfig:
        """获取指定 Agent 的模型配置"""
        if agent_name not in AGENT_MODEL_MAP:
            raise ValueError(f"Unknown agent: {agent_name}. Available: {list(AGENT_MODEL_MAP.keys())}")
        return AGENT_MODEL_MAP[agent_name]

    def resolve_model_name(self, agent_name: str) -> str:
        """将逻辑模型名 (pro/flash) 解析为 API 模型名"""
        config = self.get_agent_config(agent_name)
        return self.model_names[config.model]


# 全局单例
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
