"""Platform-independent command dispatcher; private checks precede validation and IO."""

from dataclasses import dataclass

from . import cards
from . import formatting as fmt
from .errors import UserError
from .help import DETAILS, help_text

PRIVATE_COMMANDS = {"登录", "验证", "刷新", "解绑"}
PRIVATE_MESSAGE = "群聊中不执行登录、验证码或账号变更操作。请私聊机器人，使用 ~星趴 登录 手机号；详细说明见 ~星趴 帮助 登录。"
HELP_WORDS = {"帮助", "help", "-h", "--help"}


@dataclass
class Reply:
    text: str
    card: bool = False
    visual: dict | None = None


class PartyApp:
    def __init__(self, service):
        self.service = service

    async def execute(self, owner, conversation, private, message):
        parts = message.strip().split()
        if parts and parts[0].lstrip("~") in {"星趴", "astralparty"}:
            parts = parts[1:]
        if not parts:
            return Reply(help_text())
        cmd, args = parts[0], parts[1:]
        if cmd in HELP_WORDS:
            return Reply(help_text(args[0] if args else ""))
        if cmd not in DETAILS:
            return Reply("未知指令，请使用 ~星趴 帮助 查看指令列表。")
        if args and args[0] in HELP_WORDS:
            return Reply(help_text(cmd))
        # Must happen before parsing even missing/malformed credentials, or creating a state.
        if cmd in PRIVATE_COMMANDS and not private:
            return Reply(PRIVATE_MESSAGE)
        if cmd == "登录":
            self._arity(cmd, args, 1, 1)
            return Reply(await self.service.send_code(owner, args[0]))
        if cmd == "验证":
            self._arity(cmd, args, 1, 1)
            return Reply(await self.service.verify(owner, args[0]))
        if cmd == "解绑":
            self._arity(cmd, args, 0, 0)
            return Reply(await self.service.unbind(owner))
        if cmd == "状态":
            self._arity(cmd, args, 0, 0)
            return Reply(self.service.status(owner))
        if cmd in {"我的", "刷新"}:
            self._arity(cmd, args, 0, 0)
            p = await self.service.profile(owner, refresh=(cmd == "刷新"))
            return Reply(fmt.profile_text(p), True, cards.profile_card(p))
        if cmd == "战绩":
            self._arity(cmd, args, 0, 1)
            target = args[0] if args else "我的"
            p = (
                await self.service.profile(owner)
                if target == "我的"
                else await self.service.query(owner, target)
            )
            text = fmt.records_text(p)
            self.service.remember_records(owner, conversation, p["recent"])
            return Reply(text, True, cards.records_card(p))
        if cmd in {"对局", "复盘"}:
            self._arity(cmd, args, 1, 1)
            replay_id = self.service.resolve_replay(owner, conversation, args[0])
            review = await self.service.replay(replay_id)
            if cmd == "对局":
                cache = self.service.records.get((owner, conversation))
                metadata = (
                    next((r for r in cache[1] if str(r["replayId"]) == replay_id), {})
                    if cache
                    else {}
                )
                return Reply(
                    fmt.match_text(review, metadata),
                    True,
                    cards.match_card(review, metadata),
                )
            return Reply(
                fmt.review_text(review),
                True,
                cards.review_card(review),
            )
        if cmd == "角色":
            self._arity(cmd, args, 0, 0)
            p = await self.service.profile(owner)
            return Reply(fmt.heroes_text(p), True, cards.heroes_card(p))
        if cmd == "皮肤":
            self._arity(cmd, args, 0, 1)
            query = args[0] if args else ""
            p = await self.service.profile(owner)
            return Reply(fmt.skins_text(p, query), True, cards.skins_card(p, query))
        return Reply(help_text())

    @staticmethod
    def _arity(cmd, args, minimum, maximum):
        if not minimum <= len(args) <= maximum:
            raise UserError(help_text(cmd))
