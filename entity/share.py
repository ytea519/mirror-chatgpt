from dataclasses import dataclass, field


@dataclass
class Share:
    user_name: str = field(default=None)

    access_token: str = field(default=None)

    gpt_4_limit: int = -1

    gpt_4o_limit: int = -1

    gpt_4o_mini_limit: int = -1

    gpt_o1_mini_limit: int = -1

    gpt_o1__limit: int = -1

    expire_at: int = -1

    # 对话隔离
    conversation_isolation: int = 1

    gpt_limit_enable: int = 0

    gpt_reset_every_day: int = 1

    temp_conversation_enable: int = 0
