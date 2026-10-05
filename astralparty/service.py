"""Multi-user orchestration: credentials, sessions, cooldowns and bounded replay cache."""

import asyncio
import hashlib
import json
import re
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import aiohttp

from .client import GameClient
from .errors import AuthExpired, UserError
from .profile import profile_from_player, public_profile
from .review import build_review
from .sdk import SDK
from .store import CredentialStore, atomic_write

REPLAY_BASE = "https://sereplaycn.feimogames.com/prod/"


def digits(value, label="UID", minimum=4, maximum=11):
    if not re.fullmatch(rf"[0-9]{{{minimum},{maximum}}}", str(value)):
        raise UserError(f"{label}格式不正确，请查看 /星趴 帮助。")
    return str(value)


@dataclass
class UserState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    client: GameClient | None = None
    profile: dict | None = None
    pending: dict | None = field(default=None, repr=False)
    blocked: bool = False
    refresh_after: float = 0
    query_after: float = 0
    last_used: float = field(default_factory=time.monotonic)


class PartyService:
    def __init__(self, root, config=None):
        self.config = config or {}
        self.store = CredentialStore(root)
        self.states = {}
        self.records = OrderedDict()
        self.reviews = OrderedDict()
        self.http = self.sdk = self._maintenance = None
        self._login_lock = asyncio.Lock()
        self._replay_lock = asyncio.Lock()
        self._query_gate = asyncio.Semaphore(4)
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="astralparty-replay"
        )
        self._closed = False
        self.cache = self.store.root / "replays"
        self.cache.mkdir(exist_ok=True)
        self.max_replay = self._number("max_replay_mb", 16, 1, 64) * 1024 * 1024
        self.max_cache = self._number("cache_mb", 200, 32, 2048) * 1024 * 1024
        self.cache_days = self._number("cache_days", 7, 1, 30)
        self.timeout = self._number("request_timeout", 20, 5, 60)
        self.idle = self._number("session_idle_seconds", 180, 30, 1800)
        self.max_sessions = self._number("max_sessions", 20, 1, 100)
        self.sms_file = self.store.root / "sms_limits.json"
        try:
            self.sms_limits = (
                json.loads(self.sms_file.read_text()) if self.sms_file.exists() else {}
            )
        except (ValueError, OSError):
            self.sms_limits = {}

    def _number(self, key, default, low, high):
        return max(low, min(high, int(self.config.get(key, default))))

    async def start(self):
        if self.http is None:
            self.http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                connector=aiohttp.TCPConnector(limit=8),
            )
            self.sdk = SDK(
                self.http, self.config.get("client_version", "3.2.0"), self.timeout
            )
            self._maintenance = asyncio.create_task(self._maintain())

    def state(self, owner):
        if owner not in self.states:
            if len(self.states) >= 1024:
                raise UserError("使用人数达到当前容量，请稍后再试。")
            self.states[owner] = UserState()
        state = self.states[owner]
        state.last_used = time.monotonic()
        return state

    async def _maintain(self):
        try:
            while True:
                await asyncio.sleep(30)
                now = time.monotonic()
                for owner, state in list(self.states.items()):
                    if state.lock.locked():
                        continue
                    async with state.lock:
                        if state.client and now - state.client.last_used > self.idle:
                            await state.client.close()
                            state.client = None
                        if state.pending and state.pending["expires"] < time.time():
                            state.pending = None
                        if (
                            not state.client
                            and not state.pending
                            and now - state.last_used > 3600
                        ):
                            self.states.pop(owner, None)
                self._prune_cache()
        except asyncio.CancelledError:
            pass

    async def send_code(self, owner, phone):
        if not re.fullmatch(r"1[3-9][0-9]{9}", phone):
            raise UserError("请输入正确的中国大陆手机号。用法：/星趴 登录 手机号")
        state = self.state(owner)
        async with state.lock:
            now = time.time()
            # Serial reservation prevents simultaneous requests by separate chat users.
            async with self._login_lock:
                phone_key = hashlib.sha256(phone.encode()).hexdigest()
                keys = ["user:" + owner, "phone:" + phone_key]
                until = max((self.sms_limits.get(k, 0) for k in keys), default=0)
                if until > now:
                    raise UserError(
                        f"验证码发送有冷却，请 {int(until - now) + 1} 秒后再试。"
                    )
                # Preserve cooldown across reloads; a network timeout may still have sent SMS.
                self.sms_limits = {k: v for k, v in self.sms_limits.items() if v > now}
                if len(self.sms_limits) > 4096:
                    raise UserError("验证码服务繁忙，请稍后再试。")
                for k in keys:
                    self.sms_limits[k] = now + 60
                atomic_write(self.sms_file, json.dumps(self.sms_limits).encode())
            state.pending = None
            await self.sdk.send_code(phone)
            state.pending = {"phone": phone, "expires": now + 300, "attempts": 0}
        return (
            "验证码已发送，5 分钟内私聊 /星趴 验证 验证码。\n"
            "登录查询可能使游戏客户端下线，请先退出游戏。手机号和验证码不会由插件长期保存。"
        )

    async def verify(self, owner, code):
        digits(code, "验证码", 4, 8)
        state = self.state(owner)
        async with state.lock:
            pending = state.pending
            if not pending or pending["expires"] < time.time():
                state.pending = None
                raise UserError(
                    "没有待验证的登录，或验证码流程已过期。请私聊 /星趴 登录 手机号。"
                )
            pending["attempts"] += 1
            if pending["attempts"] > 5:
                state.pending = None
                raise UserError("验证次数过多，请重新发送验证码。")
            async with self._login_lock:
                ticket = await self.sdk.authorize(
                    owner, phone=pending["phone"], code=code
                )
                state.pending = None
                if state.client:
                    await state.client.close()
                    state.client = None
                state.profile = None
                state.blocked = False
                # Persist the rotated token even if the subsequent game handshake fails.
                self.store.save(
                    owner,
                    {
                        "token": ticket.token,
                        "uid": 0,
                        "nick": "",
                        "saved_at": time.time(),
                    },
                )
                await self._connect(owner, state, ticket.sid)
            return f"登录成功，已绑定 {state.profile['nick']}（UID {state.profile['uid']}）。\n使用 /星趴 我的 或 /星趴 战绩 查看。"

    async def _connect(self, owner, state, sid):
        # Caller owns state.lock. Login is globally serialized to avoid handshake bursts.
        active = [
            (k, s)
            for k, s in self.states.items()
            if s.client and s.client.alive and k != owner
        ]
        if len(active) >= self.max_sessions:
            # Only evict an idle/unlocked session; never close another user's active request.
            candidates = [(k, s) for k, s in active if not s.lock.locked()]
            if not candidates:
                raise UserError("当前查询会话已满，请稍后重试。登录凭据已经保存。")
            _, old = min(candidates, key=lambda item: item[1].client.last_used)
            async with old.lock:
                await old.client.close()
                old.client = None
        client = GameClient(
            self.config.get("game_host", "101.132.186.71"),
            self._number("game_port", 8800, 1, 65535),
            self.config.get("client_version", "3.2.0"),
            self.timeout,
        )
        try:
            await client.connect()
            player = await client.login(sid, owner)
            profile = profile_from_player(player)
            if not profile["uid"]:
                raise UserError("游戏服务器未返回玩家资料，请稍后私聊 /星趴 刷新。")
            record = self.store.load(owner)
            record.update(uid=profile["uid"], nick=profile["nick"])
            self.store.save(owner, record)
            state.profile, state.client = profile, client
            state.blocked = False
        except BaseException:
            state.blocked = True
            await client.close()
            raise

    async def _session(self, owner, state, force=False):
        record = self.store.load(owner)
        if not record:
            raise UserError("尚未绑定账号，请先私聊 /星趴 登录 手机号。")
        if not force and state.client and state.client.alive:
            state.client.last_used = time.monotonic()
            return state.client
        if state.blocked and not force:
            raise UserError(
                "上次连接未成功，请退出游戏后私聊 /星趴 刷新；插件不会自动接管在线账号。"
            )
        if state.client:
            await state.client.close()
            state.client = None
        async with self._login_lock:
            try:
                ticket = await self.sdk.authorize(owner, token=record["token"])
                record["token"], record["saved_at"] = ticket.token, time.time()
                self.store.save(owner, record)
                await self._connect(owner, state, ticket.sid)
            except AuthExpired:
                state.blocked = True
                raise
        return state.client

    async def profile(self, owner, refresh=False):
        state = self.state(owner)
        async with state.lock:
            if refresh:
                now = time.monotonic()
                if now < state.refresh_after:
                    raise UserError(
                        f"刷新有冷却，请 {int(state.refresh_after - now) + 1} 秒后再试。"
                    )
                state.refresh_after = now + 30
                await self._session(owner, state, force=True)
            elif state.profile is None:
                await self._session(owner, state)
            return state.profile

    async def query(self, owner, uid):
        uid = int(digits(uid))
        state = self.state(owner)
        async with self._query_gate, state.lock:
            record = self.store.load(owner)
            if record and int(record.get("uid", 0)) == uid:
                if state.profile is None:
                    await self._session(owner, state)
                return state.profile
            now = time.monotonic()
            if now < state.query_after:
                raise UserError("UID 查询有冷却，请稍等 3 秒再试。")
            state.query_after = now + 3
            client = await self._session(owner, state)
            if state.profile and state.profile["uid"] == uid:
                return state.profile
            try:
                show = await client.show(uid)
                try:
                    simple = await client.simple(uid)
                except UserError:
                    simple = None
                return public_profile(show, simple, uid)
            except UserError:
                await client.close()
                state.client = None
                raise

    async def unbind(self, owner):
        state = self.state(owner)
        async with state.lock:
            if state.client:
                await state.client.close()
            state.client = state.profile = state.pending = None
            state.blocked = False
            self.store.delete(owner)
            for key in list(self.records):
                if key[0] == owner:
                    del self.records[key]
        return "已解绑并删除插件保存的登录凭据，游戏账号本身不会被删除。"

    def status(self, owner):
        record = self.store.load(owner)
        if not record:
            return "尚未绑定账号。请私聊 /星趴 登录 手机号。"
        state = self.states.get(owner)
        connection = (
            "已连接" if state and state.client and state.client.alive else "未连接"
        )
        return (
            f"绑定账号：{record.get('nick') or '待读取资料'}\nUID：{record.get('uid') or '待读取'}\n"
            f"游戏会话：{connection}\n登录凭据：已保存（是否有效需查询时验证）\n"
            "可私聊 /星趴 刷新，或 /星趴 解绑。"
        )

    def remember_records(self, owner, conversation, records):
        key = (owner, conversation)
        self.records[key] = (time.monotonic() + 600, list(records))
        self.records.move_to_end(key)
        while len(self.records) > 1024:
            self.records.popitem(last=False)

    def resolve_replay(self, owner, conversation, value):
        if re.fullmatch(r"[0-9]{1,2}", value):
            cached = self.records.get((owner, conversation))
            if not cached or cached[0] < time.monotonic():
                raise UserError("战绩序号已过期，请先在当前会话使用 /星趴 战绩。")
            index = int(value)
            if not 1 <= index <= len(cached[1]):
                raise UserError("战绩序号超出范围，请查看最近一次战绩列表。")
            value = str(cached[1][index - 1]["replayId"])
        return digits(value, "回放号", 12, 24)

    def _prune_cache(self):
        now, kept = time.time(), []
        for path in self.cache.glob("*.bin"):
            stat = path.stat()
            if now - stat.st_mtime > self.cache_days * 86400:
                path.unlink(missing_ok=True)
            else:
                kept.append((stat.st_mtime, stat.st_size, path))
        size = sum(item[1] for item in kept)
        for _, n, path in sorted(kept):
            if size <= self.max_cache:
                break
            path.unlink(missing_ok=True)
            size -= n

    async def replay(self, replay_id):
        replay_id = digits(replay_id, "回放号", 12, 24)
        async with self._replay_lock:
            cached = self.reviews.get(replay_id)
            if cached and time.monotonic() - cached[0] < 600:
                self.reviews.move_to_end(replay_id)
                return cached[1]
            self._prune_cache()
            path = self.cache / f"{replay_id}.bin"
            if path.exists() and path.stat().st_size <= self.max_replay:
                data = await asyncio.to_thread(path.read_bytes)
            else:
                path.unlink(missing_ok=True)
                async with self.http.get(
                    REPLAY_BASE + replay_id, allow_redirects=False
                ) as res:
                    if res.status != 200:
                        raise UserError(
                            "取不到这个回放，回放号可能有误、已过期或服务暂不可用。"
                        )
                    if res.content_length and res.content_length > self.max_replay:
                        raise UserError("回放文件超过插件大小限制，请联系管理员调整。")
                    buf = bytearray()
                    async for chunk in res.content.iter_chunked(64 * 1024):
                        buf.extend(chunk)
                        if len(buf) > self.max_replay:
                            raise UserError("回放文件超过插件大小限制。")
                    data = bytes(buf)
            try:
                review = await asyncio.get_running_loop().run_in_executor(
                    self._executor, build_review, data, replay_id
                )
            except Exception:
                path.unlink(missing_ok=True)
                raise UserError("该回放无法解析，游戏协议可能已经更新。") from None
            if not review.get("players"):
                path.unlink(missing_ok=True)
                raise UserError(
                    "回放中没有可识别的玩家数据，回放可能无效或协议已更新。"
                )
            if not path.exists():
                await asyncio.to_thread(atomic_write, path, data)
            self._prune_cache()
            self.reviews[replay_id] = (time.monotonic(), review)
            self.reviews.move_to_end(replay_id)
            while len(self.reviews) > 16:
                self.reviews.popitem(last=False)
            return review

    async def close(self):
        self._closed = True
        if self._maintenance:
            self._maintenance.cancel()
            await asyncio.gather(self._maintenance, return_exceptions=True)
        await asyncio.gather(
            *(s.client.close() for s in self.states.values() if s.client),
            return_exceptions=True,
        )
        for state in self.states.values():
            state.pending = None
        if self.http:
            await self.http.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
