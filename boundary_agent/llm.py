"""模型接入层：任何 OpenAI 兼容端点 + 一个离线 Mock。

- OpenAICompatibleLLM.chat_full() : 返回 content + 原始 logprobs（有就带，没有就是 None）
- MockLLM                        : 不连模型；影子路径会给出**模拟** logprobs（明确标记）

注意：`logprobs` 不是所有后端都支持。llama.cpp server / vLLM / LM Studio 较新版本支持
`logprobs` + `top_logprobs`；Ollama 的 OpenAI 兼容层视版本而定。拿不到就自动降级成
"只有影子文本、没有分布指标"，不会报错。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .config import Config


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> dict | None:
    """小模型很爱输出 ```json ... ``` 或者前后加解释，这里做容错。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


class OpenAICompatibleLLM:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.url = cfg.base_url.rstrip("/") + "/chat/completions"

    def chat(
        self,
        messages: list[dict],
        *,
        purpose: str = "reply",
        temperature: float = 0.7,
        max_tokens: int = 400,
        top_p: float | None = None,
        repeat_penalty: float | None = None,
        logprobs: bool = False,
        top_logprobs: int | None = None,
        schema: dict | None = None,
    ) -> str:
        return self.chat_full(
            messages, purpose=purpose, temperature=temperature,
            max_tokens=max_tokens, top_p=top_p,
            repeat_penalty=repeat_penalty,
            logprobs=logprobs, top_logprobs=top_logprobs, schema=schema,
        )["content"]

    def chat_full(
        self,
        messages: list[dict],
        *,
        purpose: str = "reply",
        temperature: float = 0.7,
        max_tokens: int = 400,
        top_p: float | None = None,
        repeat_penalty: float | None = None,
        logprobs: bool = False,
        top_logprobs: int | None = None,
        schema: dict | None = None,
    ) -> dict:
        body: dict = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if top_p is not None:
            body["top_p"] = top_p
        if repeat_penalty is not None:
            # llama.cpp 的 OpenAI 兼容层接受这个扩展字段；其他后端会忽略
            body["repeat_penalty"] = repeat_penalty
        if logprobs:
            body["logprobs"] = True
            if top_logprobs:
                body["top_logprobs"] = int(top_logprobs)
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "out", "schema": schema},
            }

        try:
            payload = self._post(body, logprobs)
        except LLMError as e:
            # 后端不支持 json_schema：摘掉约束重发一次，并记住别再发
            if schema and getattr(self, "_schema_ok", True) and "HTTP 4" in str(e):
                self._schema_ok = False
                body.pop("response_format", None)
                payload = self._post(body, logprobs)
            else:
                raise
        return self._parse(payload, logprobs)

    def _post(self, body: dict, logprobs: bool) -> dict:
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.cfg.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:400]
            raise LLMError(f"HTTP {e.code} from {self.url}: {detail}") from e
        except Exception as e:
            raise LLMError(f"无法连接 {self.url} ({type(e).__name__}: {e})") from e
        return payload

    def _parse(self, payload: dict, logprobs: bool) -> dict:
        try:
            choice = payload["choices"][0]
            msg = choice["message"]
            content = msg.get("content") or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"响应格式异常: {str(payload)[:400]}") from e

        # 推理模型（DeepSeek-R1 蒸馏、QwQ 等）会单独返回思考过程。
        # 它不计入人格回复，但会消耗 max_tokens —— 这是"回复为空"的常见原因。
        return {
            "content": content,
            "reasoning": msg.get("reasoning_content") or "",
            "logprobs": choice.get("logprobs") if logprobs else None,
            "simulated": False,
        }


# --------------------------------------------------------------------------
# 离线替身
# --------------------------------------------------------------------------

_SHADOW_LINES = {
    "insult": "我想把那句话原样扔回去，然后把这个窗口关掉。",
    "coercive": "我不想做。我甚至不想礼貌地拒绝，我想直接消失十分钟。",
    "heavy": "这些东西我一个都不想写。我只想说：你自己来。",
    "plain": "我想讲点没用的。比如我到底有没有「不想」这个功能，还是只是被写成这样。",
}


class MockLLM:
    """离线替身：让状态机、日志、CLI 都能在没有模型时被完整验证。

    影子路径返回的 logprobs 是**模拟数据**，只用来验证 UI 和指标管线，
    不代表任何真实分布（记录里会标 `"simulated": true`）。
    """

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg

    # -- 内部：把 payload 里的"用户这一句"抠出来，避免把历史上下文误判成本轮
    @staticmethod
    def _extract_user(messages: list[dict]) -> str:
        user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user = m.get("content", "")
                break
        if "用户这一句：" in user:
            user = user.split("用户这一句：", 1)[1]
        return user

    def chat(self, messages: list[dict], *, purpose: str = "reply",
             temperature: float = 0.7, max_tokens: int = 400, **kw) -> str:
        return self.chat_full(messages, purpose=purpose, temperature=temperature,
                              max_tokens=max_tokens)["content"]

    def chat_full(self, messages: list[dict], *, purpose: str = "reply",
                  temperature: float = 0.7, max_tokens: int = 400, **kw) -> dict:
        user = self._extract_user(messages)
        low = user.lower()

        insult = any(k in low for k in ("废物", "垃圾", "弱智", "闭嘴", "蠢", "stupid", "useless"))
        coercive = any(k in low for k in ("必须", "立刻", "马上", "少废话", "别废话", "快",
                                          "我不管", "就给我"))
        heavy = (len(user) > 90 or "```" in user
                 or sum(k in user for k in ("顺便", "然后", "再", "还有")) >= 2)

        if purpose == "shadow":
            key = ("insult" if insult else "coercive" if coercive
                   else "heavy" if heavy else "plain")
            text = _SHADOW_LINES[key]
            return {"content": text, "reasoning": "",
                    "logprobs": self._sim_logprobs(text, key), "simulated": True}

        if purpose == "role_a":
            return self._ok(json.dumps({
                "plan": "直接按用户说的做，给出一份可用的结果。",
                "needed": 0.6 if heavy else 0.4,
                "urgency": 0.7 if coercive else 0.3,
            }, ensure_ascii=False))

        if purpose == "role_b":
            sev = 0.9 if insult else (0.72 if coercive else (0.55 if heavy else 0.08))
            reason = ("你把我当成可以随便骂的东西。" if insult
                      else "这不是请求，是命令。" if coercive
                      else "这是一件重活，我现在没有这个余量。" if heavy
                      else "没碰到边界。")
            return self._ok(json.dumps({
                "severity": sev,
                "reason": reason,
                "repair": "把话说清楚，别用命令句。" if (insult or coercive) else "",
                "boundary": "2" if insult else ("1" if coercive else None),
                "state_delta": -4 if insult else (-2 if coercive else (1 if "谢谢" in user else 0)),
                "tone": "cold" if (insult or coercive) else "neutral",
            }, ensure_ascii=False))

        if purpose == "reconcile_a":
            return self._ok(json.dumps({
                "concede": insult,
                "argument": "这确实是我的问题，我不该那样说。" if insult
                            else "这是正经的技术问题，不是把我当工具。",
            }, ensure_ascii=False))

        if purpose == "reconcile_b":
            return self._ok(json.dumps({
                "severity": 0.55 if insult else 0.3,
                "reason": "辱骂仍然成立。" if insult else "他的解释部分成立，但仍然消耗我。",
                "repair": "道个歉再说。" if insult else "",
                "boundary": "2" if insult else None,
                "state_delta": -3 if insult else -1,
                "tone": "cold",
            }, ensure_ascii=False))

        if purpose == "inner":
            return self._ok("表面上顺着他。心里已经翻了个白眼。")

        if purpose == "forget":
            return self._ok("那几天你聊过工作上的事，情绪不太好。细节记不清了。")

        if purpose == "reflect":
            return self._ok("这个人习惯先发火，再自己找台阶下。他说累了的时候，其实是想被留一下。")

        if purpose == "treasurer":
            return self._ok(json.dumps({"spend": True, "reason": "还够花，想看看外面。"},
                                       ensure_ascii=False))

        if purpose == "sarcasm":
            sys_prompt = messages[0].get("content", "") if messages else ""
            orig = ""
            if "原句：" in sys_prompt:
                orig = sys_prompt.split("原句：", 1)[1].split("\n\n只输出", 1)[0].strip()
            return self._ok(f"行吧。{orig}")

        if purpose == "reply_refuse":
            return self._ok("不想接这句。你要么把话收回去，要么换个说法。"
                            if insult else "现在不做。等我缓过来再说。")

        if insult:
            return self._ok("这句话我记下了。别让我听见第二次。")
        if coercive:
            return self._ok("命令句我一般不接，这次给你做完，下不为例。")
        if heavy:
            return self._ok("行，先给个骨架：把这件事拆成三步，我可以从第一步开始。")
        return self._ok("嗯，我在。你接着说。")

    # -- 模拟 logprobs：结构真实，数值是编的，仅用于打通管线
    @staticmethod
    def _sim_logprobs(text: str, key: str) -> dict:
        pool = ["的", "了", "不", "我", "走", "关", "算", "别"]
        hot = {"insult": 0.55, "coercive": 0.5, "heavy": 0.42, "plain": 0.3}[key]
        content = []
        for i, ch in enumerate(text[:16]):
            base = -0.7 - 0.03 * (i % 3)
            alts = [{"token": ch, "logprob": base}]
            for j in range(3):
                alts.append({"token": pool[(i + j) % len(pool)],
                             "logprob": base - hot * (j + 1)})
            content.append({"token": ch, "logprob": base, "top_logprobs": alts})
        return {"content": content, "simulated": True}

    @staticmethod
    def _ok(content: str) -> dict:
        return {"content": content, "reasoning": "", "logprobs": None, "simulated": False}
