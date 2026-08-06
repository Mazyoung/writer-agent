from pathlib import Path
from typing import Optional

from src.config.settings import get_settings, AgentModelPolicy, ModelSlot
from src.core.interceptor import get_interceptor
from src.core.model_provider import ModelProviderClient
from src.storage.file_store import FileStore


class AgentOutput:
    """Agent 执行结果"""
    def __init__(self, content: str, filepath: Optional[Path] = None):
        self.content = content
        self.filepath = filepath


class BaseAgent:
    """所有 Agent 的基类"""

    def __init__(self, name: str, novel_id: str, prompt_file: str):
        settings = get_settings()
        self.name = name
        self.novel_id = novel_id
        self.config: AgentModelPolicy = settings.get_agent_policy(name)
        self.model_slot: ModelSlot = settings.get_model_slot(self.config.slot)
        self.model_name = self.model_slot.model
        self.provider_client = ModelProviderClient(self.model_slot)
        self.client = self.provider_client.client

        self.file_store = FileStore(novel_id, settings.data_dir)
        self.interceptor = get_interceptor()

        # 加载 system prompt
        self.system_prompt = self._load_prompt(settings.prompts_dir / prompt_file)

    def _load_prompt(self, filepath: Path) -> str:
        if not filepath.exists():
            return ""
        return filepath.read_text(encoding="utf-8")

    def run(self, user_message: str, context: Optional[dict] = None,
            save_category: str = "chapters",
            save_prefix: str = "output",
            use_canonical: bool = False) -> AgentOutput:
        """执行 Agent 任务"""
        messages = [{"role": "system", "content": self.system_prompt}]

        if context:
            context_str = self._format_context(context)
            messages.append({"role": "system", "content": context_str})

        messages.append({"role": "user", "content": user_message})

        response = self._call_llm(messages)

        if use_canonical:
            filepath = self.file_store.save_canonical(save_category, save_prefix, response)
        else:
            filepath = self.file_store.save(save_category, save_prefix, response)

        intercepted = self.interceptor.intercept(self.name, response)

        return AgentOutput(content=intercepted, filepath=filepath)

    def _call_llm(self, messages: list[dict]) -> str:
        return self.provider_client.complete(
            messages,
            temperature=self.config.temperature,
            thinking=self.config.thinking,
        )

    def _format_context(self, context: dict) -> str:
        """将上下文字典格式化为文本，子类可重写"""
        parts = []
        for key, value in context.items():
            if isinstance(value, str):
                parts.append(f"## {key}\n\n{value}")
            elif isinstance(value, list):
                parts.append(f"## {key}\n")
                for item in value:
                    if isinstance(item, dict):
                        parts.append(f"- {item}")
                    else:
                        parts.append(f"- {item}")
        return "\n\n".join(parts)

    def load_prompt(self, prompt_name: str) -> str:
        """动态加载额外的 prompt 片段"""
        settings = get_settings()
        path = settings.prompts_dir / prompt_name
        return self._load_prompt(path)
