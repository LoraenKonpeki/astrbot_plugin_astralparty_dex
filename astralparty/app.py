"""Platform-independent command dispatcher; private checks precede validation and IO."""

from dataclasses import dataclass

from . import formatting as fmt
from .errors import UserError
from .help import DETAILS, help_text
from .service import digits

PRIVATE_COMMANDS = {"登录", "验证", "刷新", "解绑"}
PRIVATE_MESSAGE = "群聊中不执行登录、验证码或账号变更操作。请私聊机器人，使用 /星趴 登录 手机号；详细说明见 /星趴 帮助 登录。"
HELP_WORDS = {"帮助", "help", "-h", "--help"}


@dataclass
class Reply:
    text: str
    card: bool = False


class PartyApp:
    def __init__(self, service):
        self.service = service

    async def execute(self, owner, conversation, private, message):
        parts = message.strip().split()
        if parts and parts[0].lstrip("/") in {"星趴", "astralparty"}:
            parts = parts[1:]
        if not parts:
            return Reply(help_text())
        cmd, args = parts[0], parts[1:]
        if cmd in HELP_WORDS:
            return Reply(help_text(args[0] if args else ""))
        if cmd not in DETAILS:
            return Reply("未知指令，请使用 /星趴 帮助 查看指令列表。")
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
            return Reply(fmt.profile_text(p), True)
        if cmd == "战绩":
            self._arity(cmd, args, 0, 2)
            target = args[0] if args else "我的"
            number = args[1] if len(args) > 1 else 1
            p = (
                await self.service.profile(owner)
                if target == "我的"
                else await self.service.query(owner, target)
            )
            text = fmt.records_text(p, number)
            self.service.remember_records(owner, conversation, p["recent"])
            return Reply(text, True)
        if cmd in {"对局", "复盘"}:
            self._arity(cmd, args, 1 if cmd == "对局" else 2, 1 if cmd == "对局" else 3)
            replay_id = self.service.resolve_replay(owner, conversation, args[0])
            if cmd == "复盘":
                uid = int(digits(args[1]))
                number = args[2] if len(args) > 2 else 1
                # Reject invalid page before downloading.
                if not str(number).isdigit() or int(number) < 1:
                    raise UserError("页码必须是正整数。")
            review = await self.service.replay(replay_id)
            if cmd == "对局":
                cache = self.service.records.get((owner, conversation))
                metadata = (
                    next((r for r in cache[1] if str(r["replayId"]) == replay_id), {})
                    if cache
                    else {}
                )
                return Reply(fmt.match_text(review, metadata), True)
            return Reply(fmt.review_text(review, uid, number))
        if cmd == "角色":
            self._arity(cmd, args, 0, 1)
            return Reply(
                fmt.heroes_text(
                    await self.service.profile(owner), args[0] if args else 1
                ),
                True,
            )
        if cmd == "皮肤":
            self._arity(cmd, args, 0, 2)
            query, number = "", 1
            if args:
                if args[0].isdigit() and len(args[0]) <= 2:
                    if len(args) > 1:
                        raise UserError(
                            "纯页码后不需要其他参数，详细说明见 /星趴 帮助 皮肤。"
                        )
                    number = args[0]
                else:
                    query = args[0]
                    number = args[1] if len(args) > 1 else 1
            return Reply(
                fmt.skins_text(await self.service.profile(owner), query, number), True
            )
        return Reply(help_text())

    @staticmethod
    def _arity(cmd, args, minimum, maximum):
        if not minimum <= len(args) <= maximum:
            raise UserError(help_text(cmd))
