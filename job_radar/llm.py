"""Provider-agnostic LLM wrapper supporting Gemini, OpenAI, Anthropic, and Mock."""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type
import httpx
from pydantic import BaseModel

from job_radar.config import settings

logger = logging.getLogger(__name__)


def extract_json_from_text(raw_text: str) -> Dict[str, Any]:
    """Extract valid JSON dict from LLM response text, stripping markdown codeblocks if present."""
    text = raw_text.strip()
    # Strip markdown ```json ... ``` or ``` ... ```
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()

    # Find first { and last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1:
        text = text[first_brace : last_brace + 1]

    try:
        return json.loads(text)
    except Exception as e:
        logger.warning(f"Failed to parse JSON directly: {e}. Trying secondary repair.")
        # Attempt minimal repair if needed
        clean = text.replace("\n", " ").replace("\r", "")
        return json.loads(clean)


class BaseLLMClient(ABC):
    """Abstract provider-agnostic LLM interface."""

    @abstractmethod
    async def complete_text(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Return raw generated text."""
        pass

    @abstractmethod
    async def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
    ) -> Dict[str, Any]:
        """Return structured JSON dictionary matching prompt instructions or schema."""
        pass


class GeminiLLMClient(BaseLLMClient):
    """Google Gemini REST API implementation."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or getattr(settings, "gemini_model", "gemini-2.5-flash-lite")
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

    async def complete_text(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is missing.")

        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": f"System Instructions: {system_prompt}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        payload = {"contents": contents}
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        if self.api_key.startswith("AQ."):
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}?key={self.api_key}",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as err:
                raise ValueError(f"Malformed Gemini response: {data}") from err

    async def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is missing.")

        json_instruction = (
            "\n\nCRITICAL: Respond ONLY with a valid, raw JSON object. Do not include markdown code blocks, "
            "do not include preamble or explanations."
        )
        full_prompt = prompt + json_instruction

        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": f"System Instructions: {system_prompt}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": full_prompt}]})

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"response_mime_type": "application/json"},
        }

        # Try user-requested Gemini models in order
        candidate_models = list(dict.fromkeys([
            self.model,
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-flash-latest",
            "gemini-1.5-flash",
        ]))

        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        if self.api_key.startswith("AQ."):
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=35.0) as client:
            last_err = None
            for m in candidate_models:
                for api_ver in ("v1beta", "v1"):
                    url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{m}:generateContent"
                    try:
                        resp = await client.post(
                            f"{url}?key={self.api_key}",
                            json=payload,
                            headers=headers,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                            return extract_json_from_text(raw_text)
                        last_err = f"{m} ({resp.status_code}): {resp.text}"
                    except Exception as err:
                        last_err = f"{m}: {err}"

            logger.error(f"All Gemini model endpoints failed. Last error: {last_err}")
            raise ValueError(f"Gemini API Error: {last_err}")


class OpenAILLMClient(BaseLLMClient):
    """OpenAI API implementation."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        self.api_key = api_key or settings.openai_api_key
        self.model = model
        self.base_url = "https://api.openai.com/v1/chat/completions"

    async def complete_text(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is missing.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "messages": messages},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is missing.")

        messages = []
        sys = (system_prompt or "") + "\nYou must output ONLY valid JSON."
        messages.append({"role": "system", "content": sys.strip()})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            raw_text = resp.json()["choices"][0]["message"]["content"]
            return extract_json_from_text(raw_text)


class AnthropicLLMClient(BaseLLMClient):
    """Anthropic Claude API implementation."""

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-3-5-haiku-20241022"):
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model
        self.base_url = "https://api.anthropic.com/v1/messages"

    async def complete_text(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is missing.")

        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.base_url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]

    async def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
    ) -> Dict[str, Any]:
        json_sys = (system_prompt or "") + "\nRespond with raw JSON only. No markdown formatting."
        raw_text = await self.complete_text(prompt, system_prompt=json_sys.strip())
        return extract_json_from_text(raw_text)


class MockLLMClient(BaseLLMClient):
    """Mock LLM for testing, CI, and local dry runs without API keys."""

    async def complete_text(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        return "Mock response generated for test."

    async def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
    ) -> Dict[str, Any]:
        p_lower = prompt.lower()
        if "resume" in p_lower:
            return {
                "skills": ["Python", "Machine Learning", "SQL", "Pandas", "Scikit-Learn"],
                "experience_level": "fresher",
                "years_of_experience": 1.0,
                "education": ["B.Tech in Computer Science"],
                "projects": ["Sentiment Analysis with BERT", "Sales Forecasting Pipeline"],
                "preferred_roles": ["ML Engineer", "Data Scientist", "Data Analyst"],
                "summary": "Fresher machine learning engineer skilled in Python, SQL, and data analytics.",
            }
        elif "scorer" in p_lower or "score" in p_lower:
            return {
                "score": 88,
                "why_matches": "Strong alignment with required Python & data modeling skills. Matches fresher experience level.",
                "missing_skills": ["Docker", "Kubernetes"],
            }
        elif "interview" in p_lower or "prep" in p_lower:
            return {
                "technical_questions": [
                    "How do you handle class imbalance in machine learning datasets?",
                    "Explain the difference between gradient boosting and random forest.",
                ],
                "dsa_topics": ["Binary Search", "Hash Maps & Two Pointers", "Breadth-First Search"],
                "resume_based_questions": [
                    "Tell us about your BERT sentiment analysis project and how you evaluated it.",
                ],
                "company_specific_points": [
                    "Focus on scalable production pipelines and data reliability.",
                    "Review their recent open-source engineering contributions.",
                ],
            }
        return {"status": "success", "mock": True}


def get_llm_client() -> BaseLLMClient:
    """Factory returning the configured LLM client."""
    provider = settings.llm_provider.lower().strip()
    if provider == "gemini":
        if settings.gemini_api_key:
            return GeminiLLMClient()
        logger.warning("GEMINI_API_KEY not set; falling back to MockLLMClient.")
        return MockLLMClient()
    elif provider == "openai":
        if settings.openai_api_key:
            return OpenAILLMClient()
        logger.warning("OPENAI_API_KEY not set; falling back to MockLLMClient.")
        return MockLLMClient()
    elif provider == "anthropic":
        if settings.anthropic_api_key:
            return AnthropicLLMClient()
        logger.warning("ANTHROPIC_API_KEY not set; falling back to MockLLMClient.")
        return MockLLMClient()
    elif provider == "mock":
        return MockLLMClient()

    logger.warning(f"Unknown LLM provider '{provider}', defaulting to MockLLMClient.")
    return MockLLMClient()
