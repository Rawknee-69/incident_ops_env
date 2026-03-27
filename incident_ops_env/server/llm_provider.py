from __future__ import annotations

import os
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], system_prompt: str) -> str:
        raise NotImplementedError


class OpenAIProvider(LLMProvider):
    def __init__(self, model: str = "gpt-4o-mini"):
        from openai import OpenAI

        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = model

    def chat(self, messages: list[dict], system_prompt: str) -> str:
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        response = self.client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            temperature=0.0,
            max_tokens=1000,
        )
        return response.choices[0].message.content or "{}"


class GeminiProvider(LLMProvider):
    def __init__(self, model: str = "gemini-2.0-flash"):
        import google.generativeai as genai

        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        self.model_name = model

    def chat(self, messages: list[dict], system_prompt: str) -> str:
        import google.generativeai as genai

        model = genai.GenerativeModel(model_name=self.model_name, system_instruction=system_prompt)
        gemini_messages = []
        for message in messages:
            role = "user" if message["role"] == "user" else "model"
            gemini_messages.append({"role": role, "parts": [message["content"]]})
        response = model.generate_content(
            gemini_messages,
            generation_config={"temperature": 0.0, "max_output_tokens": 1000},
        )
        return response.text or "{}"


def get_provider() -> LLMProvider:
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIProvider(model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiProvider(model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"))
    raise EnvironmentError("No LLM API key found. Set OPENAI_API_KEY or GEMINI_API_KEY.")
