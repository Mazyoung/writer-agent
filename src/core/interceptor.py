import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from src.config.settings import get_settings


class InterceptorPolicy:
    AUTO_PASS = "auto_pass"
    NOTIFY = "notify"
    REQUIRE_APPROVAL = "require_approval"


class HumanInterceptor:
    """人工介入拦截器

    三种策略:
    - auto_pass: 直接放行，记录日志
    - notify: 保存输出到通知队列（_pending 目录），不阻塞
    - require_approval: 保存输出，阻塞等待人工编辑 _edited 文件或超时放行
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.pending_dir = data_dir / "pending_review"
        self.pending_dir.mkdir(parents=True, exist_ok=True)

        # 默认策略：全部自动放行，仅在关键节点通知
        # 需要人工确认时，用户可编辑文件后保存 _edited.md 来覆盖
        self.policies = {
            "plot_designer":     InterceptorPolicy.NOTIFY,             # 大纲生成后通知（可人工编辑）
            "quality_reviewer":  InterceptorPolicy.NOTIFY,             # 审阅结果通知
        }

    def set_policy(self, agent_name: str, policy: str):
        self.policies[agent_name] = policy

    def get_policy(self, agent_name: str) -> str:
        return self.policies.get(agent_name, InterceptorPolicy.AUTO_PASS)

    def intercept(self, agent_name: str, output: str, wait_timeout: int = 60) -> str:
        """拦截 Agent 输出，根据策略决定是否等待人工"""
        policy = self.get_policy(agent_name)

        if policy == InterceptorPolicy.AUTO_PASS:
            self._log(agent_name, "AUTO_PASS")
            return output

        elif policy == InterceptorPolicy.NOTIFY:
            self._save_pending(agent_name, output)
            self._log(agent_name, "NOTIFY — saved to pending")
            return output

        elif policy == InterceptorPolicy.REQUIRE_APPROVAL:
            self._save_pending(agent_name, output)
            self._log(agent_name, f"REQUIRE_APPROVAL — waiting for human (timeout={wait_timeout}s)")
            print(f"\n[拦截器] {agent_name} 产出需要人工确认")
            print(f"  待审文件: {self.pending_dir / f'{agent_name}_pending.md'}")
            print(f"  请编辑后保存为同目录下的 {agent_name}_edited.md")
            print(f"  超时 {wait_timeout}s 后自动放行...")

            # 轮询等待 _edited 文件出现
            import time
            edited_path = self.pending_dir / f"{agent_name}_edited.md"
            start = time.time()
            while time.time() - start < wait_timeout:
                if edited_path.exists():
                    edited_content = edited_path.read_text(encoding="utf-8")
                    self._log(agent_name, "APPROVED — human edit accepted")
                    return edited_content
                time.sleep(2)

            self._log(agent_name, "TIMEOUT — auto-approved")
            return output

        return output

    def _save_pending(self, agent_name: str, content: str):
        filepath = self.pending_dir / f"{agent_name}_pending.md"
        filepath.write_text(content, encoding="utf-8")

    def _log(self, agent_name: str, message: str):
        ts = datetime.now().isoformat()
        log_entry = f"[{ts}] {agent_name}: {message}\n"
        log_path = self.data_dir / "interceptor.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)


# 全局单例
_interceptor: Optional[HumanInterceptor] = None


def get_interceptor() -> HumanInterceptor:
    global _interceptor
    if _interceptor is None:
        settings = get_settings()
        _interceptor = HumanInterceptor(settings.data_dir)
    return _interceptor
