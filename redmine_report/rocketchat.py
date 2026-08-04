"""RocketChat 客户端 — 封装 REST API 调用。

认证方式：账号密码登录（POST /api/v1/login 获取会话令牌），
后续请求携带 X-Auth-Token / X-User-Id 请求头。
"""

from typing import Any

import requests as _requests


class RocketChatClientError(Exception):
    """RocketChat 客户端错误。"""

    pass


class RocketChatClient:
    """封装 RocketChat REST API 调用。"""

    # 房间类型 → 中文标签
    _TYPE_LABELS = {
        "c": "频道",
        "p": "群组",
        "d": "私聊",
    }

    def __init__(self, url: str, username: str, password: str, timeout: int = 30):
        self.url = url.rstrip("/")
        self._username = username
        self._password = password
        self._timeout = timeout
        self._session = _requests.Session()
        self._auth_token: str | None = None
        self._user_id: str | None = None

    # ── 认证 ──────────────────────────────────────────────

    def login(self) -> dict[str, Any]:
        """登录获取会话令牌，返回当前用户信息。"""
        resp = self._post(
            "/api/v1/login",
            json={"user": self._username, "password": self._password},
            authenticated=False,
        )
        data = resp.get("data") or {}
        token = data.get("authToken")
        uid = data.get("userId")
        if not token or not uid:
            raise RocketChatClientError("登录返回缺少会话信息，请检查服务器版本")

        self._auth_token = token
        self._user_id = uid
        # 更新 session 头，供后续请求使用
        self._session.headers["X-Auth-Token"] = token
        self._session.headers["X-User-Id"] = uid

        user = data.get("me") or {}
        return {
            "id": uid,
            "username": user.get("username", self._username),
            "name": user.get("name", ""),
            "email": user.get("email", ""),
        }

    def _ensure_login(self) -> None:
        """确保已登录。"""
        if not self._auth_token or not self._user_id:
            self.login()

    # ── 房间 ──────────────────────────────────────────────

    def list_rooms(self) -> list[dict[str, Any]]:
        """列出当前用户加入的频道/群组。

        subscriptions.get 返回用户订阅的所有房间（含私聊）。
        这里只保留公开频道(c)和私有群组(p)。
        """
        self._ensure_login()
        resp = self._get("/api/v1/subscriptions.get")
        items = resp.get("update") or resp.get("subscriptions") or []

        rooms = []
        for item in items:
            rtype = item.get("t", "")
            if rtype not in ("c", "p"):
                continue
            rid = item.get("rid") or item.get("_id")
            if not rid:
                continue
            # fname 为显示名（含本地化名），name 为系统名
            name = item.get("fname") or item.get("name") or ""
            rooms.append({
                "rid": rid,
                "name": name,
                "type": rtype,
                "type_label": self._TYPE_LABELS.get(rtype, rtype),
            })
        rooms.sort(key=lambda r: (r["type"], r["name"]))
        return rooms

    # ── 发送消息 ──────────────────────────────────────────

    def post_message(self, room_id: str, text: str) -> str:
        """发送消息到指定房间，返回消息 ID。"""
        self._ensure_login()
        resp = self._post(
            "/api/v1/chat.postMessage",
            json={"roomId": room_id, "text": text},
        )
        msg = resp.get("message") or {}
        return str(msg.get("_id", ""))

    # ── 底层请求 ──────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.url}{path}"
        try:
            resp = self._session.request(
                method, url, json=json, timeout=self._timeout
            )
        except _requests.exceptions.Timeout:
            raise RocketChatClientError(
                f"连接超时({self._timeout}s): {self.url}\n请检查地址和网络"
            ) from None
        except _requests.exceptions.ConnectionError:
            raise RocketChatClientError(
                f"无法连接到 RocketChat 服务器: {self.url}\n"
                "请检查地址、端口和网络是否可达"
            ) from None
        except _requests.exceptions.RequestException as e:
            raise RocketChatClientError(f"请求失败: {str(e)[:200]}") from None

        # 401 → 认证失败
        if resp.status_code == 401:
            if authenticated:
                raise RocketChatClientError("会话已失效，请重新测试连接")
            raise RocketChatClientError("用户名或密码错误")

        # 尝试解析 JSON（登录失败等错误也返回 JSON）
        try:
            data = resp.json()
        except ValueError:
            if resp.status_code >= 400:
                raise RocketChatClientError(
                    f"服务器返回错误 ({resp.status_code}): {resp.text[:200]}"
                ) from None
            data = {}

        if resp.status_code >= 400 or data.get("success") is False:
            err = data.get("error") or data.get("message") or resp.text[:200]
            raise RocketChatClientError(f"{err}")

        return data

    def _get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def _post(
        self, path: str, json: dict | None = None, authenticated: bool = True
    ) -> dict[str, Any]:
        return self._request("POST", path, json=json, authenticated=authenticated)
