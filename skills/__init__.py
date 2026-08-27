"""项目运行时 Skill：声明方法、能力需求与输出协议，不直接调用 Agent/Tool。"""

from skills.registry import SkillRegistry, skill_registry

__all__ = ["SkillRegistry", "skill_registry"]
