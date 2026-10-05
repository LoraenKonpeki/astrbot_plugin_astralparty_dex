"""AstrBot entry point. All commands stop propagation, including rejected credentials."""

import asyncio

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .astralparty.app import PartyApp
from .astralparty.cards import CARD_TEMPLATE, REVIEW_TEMPLATE, text_card
from .astralparty.errors import UserError
from .astralparty.formatting import split_text
from .astralparty.service import PartyService
from .astralparty.store import owner_key


@register(
    "astrbot_plugin_astralparty_dex",
    "Loraen_Konpeki",
    "星趴登录、战绩与逐轮复盘助手",
    "0.3.3",
    "https://github.com/LoraenKonpeki/astrbot_plugin_astralparty_dex",
)
class AstralPartyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.service = None
        self.app = None
        self._start_lock = asyncio.Lock()

    async def initialize(self):
        async with self._start_lock:
            if self.service is None:
                service = PartyService(
                    StarTools.get_data_dir("astrbot_plugin_astralparty_dex"),
                    self.config,
                )
                await service.start()
                self.service, self.app = service, PartyApp(service)

    @filter.command("星趴", alias={"astralparty"})
    async def party_command(self, event: AstrMessageEvent):
        """星趴档案助手：~星趴 帮助；~星趴 帮助 指令名 查看详细说明。"""
        # Suppress subsequent default LLM handlers for both private and rejected group input.
        event.stop_event()
        try:
            await self.initialize()
            owner = owner_key(
                event.get_platform_name(),
                event.get_platform_id(),
                event.get_self_id(),
                event.get_sender_id(),
            )
            reply = await self.app.execute(
                owner,
                str(event.unified_msg_origin),
                event.is_private_chat(),
                event.get_message_str(),
            )
            if reply.card and self.config.get("image_cards", True):
                try:
                    visual = reply.visual or text_card(reply.text)
                    scale = max(1, min(3, int(self.config.get("image_scale", 2))))
                    image = await asyncio.wait_for(
                        self.html_render(
                            REVIEW_TEMPLATE
                            if visual.get("kind") == "review"
                            else CARD_TEMPLATE,
                            {"card": visual, "render_scale": scale},
                            return_url=False,
                            options={
                                "full_page": True,
                                "type": "jpeg",
                                "quality": 95,
                                "viewport": {
                                    "width": visual.get("width", 860) * scale,
                                    "height": 600,
                                },
                            },
                        ),
                        timeout=45,
                    )
                    yield event.image_result(image).stop_event()
                    if reply.copy_text:
                        for part in split_text(reply.copy_text):
                            yield event.plain_result(part).stop_event()
                    return
                except Exception as exc:
                    # Exception values or tracebacks may contain URLs/input. Log only the type.
                    logger.warning(
                        "星趴图片渲染失败，回退文字 (%s)", type(exc).__name__
                    )
            for part in split_text(reply.text):
                yield event.plain_result(part).stop_event()
        except UserError as exc:
            for part in split_text(str(exc)):
                yield event.plain_result(part).stop_event()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            logger.warning("星趴连接或存储失败 (%s)", type(exc).__name__)
            yield event.plain_result(
                "连接或保存数据失败，请稍后重试；若持续失败，请联系管理员查看网络与数据目录。"
            ).stop_event()
        except Exception as exc:
            logger.error("星趴插件处理失败 (%s)", type(exc).__name__)
            yield event.plain_result(
                "处理失败，请联系管理员检查插件版本。不会在聊天中输出原始登录响应。"
            ).stop_event()

    async def terminate(self):
        if self.service:
            await self.service.close()
            self.service = self.app = None
